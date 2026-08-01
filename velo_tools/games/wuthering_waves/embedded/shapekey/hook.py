"""Mount independent custom ShapeKey export on the stock WWMI exporter."""

from __future__ import annotations

from pathlib import Path

from ..._wwmi_core.blender_export import blender_export as _be_module

from .generator import (
    collect_shape_key_names,
    inject_single_ib_ini,
    write_hlsl_assets,
)
from .planner import ShapeKeyPlanError
from .runtime import prepare_exporter


_INSTALLED = False
_ORIG_BUILD_DATA_BUFFERS = None
_ORIG_BUILD_MOD_INI = None
_ORIG_WRITE_FILES = None


class _CfgProxy:
    def __init__(self, cfg, **overrides):
        object.__setattr__(self, "_cfg", cfg)
        object.__setattr__(self, "_overrides", overrides)

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._cfg, name)

    def __setattr__(self, name, value):
        if name in self._overrides:
            self._overrides[name] = value
        else:
            setattr(self._cfg, name, value)


def _full_export_error() -> ShapeKeyPlanError:
    return ShapeKeyPlanError(
        "自定义 ShapeKey 数据发生变化时必须执行完整导出："
        "请关闭 Partial Export、启用 Write INI 后重试。")


def install() -> None:
    global _INSTALLED, _ORIG_BUILD_DATA_BUFFERS, _ORIG_BUILD_MOD_INI, _ORIG_WRITE_FILES
    if _INSTALLED:
        return
    _ORIG_BUILD_DATA_BUFFERS = _be_module.ModExporter.build_data_buffers
    _ORIG_BUILD_MOD_INI = _be_module.ModExporter.build_mod_ini
    _ORIG_WRITE_FILES = _be_module.ModExporter.write_files

    def _wrapped_build_data_buffers(self):
        result = _ORIG_BUILD_DATA_BUFFERS(self)
        enabled = bool(getattr(self.cfg, "unrestricted_custom_shape_keys", True))
        plan = prepare_exporter(self, enabled=enabled)
        self.velo_shape_key_names = collect_shape_key_names(
            (self,), plan.channels if plan is not None else {})
        if (plan is not None and plan.parsed.custom_records
                and (bool(getattr(self.cfg, "partial_export", False))
                     or not bool(getattr(self.cfg, "write_ini", True)))):
            raise _full_export_error()
        return result

    def _wrapped_build_mod_ini(self):
        original_cfg = self.cfg
        self.cfg = _CfgProxy(
            original_cfg,
            unrestricted_custom_shape_keys=False,
        )
        try:
            result = _ORIG_BUILD_MOD_INI(self)
        finally:
            self.cfg = original_cfg
        if hasattr(self, "ini"):
            self.ini.cfg = original_cfg
        plan = getattr(self, "velo_shape_key_plan", None)
        if plan is None or not (plan.has_native or plan.has_external):
            return result
        text = inject_single_ib_ini(
            self.ini.ini_string,
            plan,
            plan.channels,
            mesh_vertex_count=int(self.merged_object.vertex_count),
            shape_names=getattr(self, "velo_shape_key_names", {}),
        )
        self.ini.ini_string = self.ini.with_checksum(text)
        return result

    def _wrapped_write_files(self):
        result = _ORIG_WRITE_FILES(self)
        if not bool(getattr(self.cfg, "partial_export", False)):
            plan = getattr(self, "velo_shape_key_plan", None)
            write_hlsl_assets(
                Path(self.mod_output_folder),
                bool(plan is not None and plan.has_external),
            )
        return result

    _wrapped_build_data_buffers._external_shape_key_hook = True
    _wrapped_build_mod_ini._external_shape_key_hook = True
    _wrapped_write_files._external_shape_key_hook = True
    _be_module.ModExporter.build_data_buffers = _wrapped_build_data_buffers
    _be_module.ModExporter.build_mod_ini = _wrapped_build_mod_ini
    _be_module.ModExporter.write_files = _wrapped_write_files
    _INSTALLED = True


def remove() -> None:
    global _INSTALLED, _ORIG_BUILD_DATA_BUFFERS, _ORIG_BUILD_MOD_INI, _ORIG_WRITE_FILES
    if not _INSTALLED:
        return
    _be_module.ModExporter.build_data_buffers = _ORIG_BUILD_DATA_BUFFERS
    _be_module.ModExporter.build_mod_ini = _ORIG_BUILD_MOD_INI
    _be_module.ModExporter.write_files = _ORIG_WRITE_FILES
    _ORIG_BUILD_DATA_BUFFERS = None
    _ORIG_BUILD_MOD_INI = None
    _ORIG_WRITE_FILES = None
    _INSTALLED = False
