"""Keep COMPONENT export on EFMI's native per-component path."""

from __future__ import annotations

from typing import Any, Callable


_PATCHES_INSTALLED = False
_ORIGINAL_PREPARE_NUMERIC_EXPORT = None
_ORIGINAL_VALIDATE_PER_COMPONENT_VG_IDS = None
_ORIGINAL_GET_COMPONENT_LOCAL_VG_COUNT = None


def is_native_per_export(merger: Any) -> bool:
    """Return True when the current export mode is EFMI-style COMPONENT."""
    context = getattr(merger, "context", None)
    scene = getattr(context, "scene", None) if context is not None else None
    cfg = getattr(scene, "VTEF_settings", None) if scene is not None else None
    if cfg is None:
        return False
    return getattr(cfg, "mod_skeleton_type", None) == "COMPONENT"


def wrap_prepare_temp_object_for_numeric_export(original: Callable[..., Any]) -> Callable[..., Any]:
    def prepare_temp_object_for_numeric_export_native_per(self: Any, temp_obj: Any) -> Any:
        if is_native_per_export(self):
            return None
        return original(self, temp_obj)

    return prepare_temp_object_for_numeric_export_native_per


def wrap_validate_per_component_vg_ids(original: Callable[..., Any]) -> Callable[..., Any]:
    def validate_per_component_vg_ids_native_per(
        self: Any,
        temp_obj: Any,
        component_id: int,
        local_vg_count: int | None,
    ) -> Any:
        if is_native_per_export(self):
            return None
        return original(self, temp_obj, component_id, local_vg_count)

    return validate_per_component_vg_ids_native_per


def wrap_get_component_local_vg_count(original: Callable[..., Any]) -> Callable[..., Any]:
    def get_component_local_vg_count_native_per(self: Any, component_id: int) -> Any:
        if is_native_per_export(self):
            return None
        return original(self, component_id)

    return get_component_local_vg_count_native_per


def install_patches(object_merger_module=None) -> None:
    global _PATCHES_INSTALLED
    global _ORIGINAL_PREPARE_NUMERIC_EXPORT
    global _ORIGINAL_VALIDATE_PER_COMPONENT_VG_IDS
    global _ORIGINAL_GET_COMPONENT_LOCAL_VG_COUNT

    if _PATCHES_INSTALLED:
        return
    if object_merger_module is None:
        from ._efmi_core.blender_export import object_merger as object_merger_module

    merger_cls = object_merger_module.ObjectMerger
    _ORIGINAL_PREPARE_NUMERIC_EXPORT = merger_cls._prepare_temp_object_for_numeric_export
    _ORIGINAL_VALIDATE_PER_COMPONENT_VG_IDS = merger_cls._validate_per_component_vg_ids
    _ORIGINAL_GET_COMPONENT_LOCAL_VG_COUNT = merger_cls._get_component_local_vg_count
    merger_cls._prepare_temp_object_for_numeric_export = wrap_prepare_temp_object_for_numeric_export(
        _ORIGINAL_PREPARE_NUMERIC_EXPORT
    )
    merger_cls._validate_per_component_vg_ids = wrap_validate_per_component_vg_ids(
        _ORIGINAL_VALIDATE_PER_COMPONENT_VG_IDS
    )
    merger_cls._get_component_local_vg_count = wrap_get_component_local_vg_count(
        _ORIGINAL_GET_COMPONENT_LOCAL_VG_COUNT
    )
    _PATCHES_INSTALLED = True


def uninstall_patches(object_merger_module=None) -> None:
    global _PATCHES_INSTALLED
    global _ORIGINAL_PREPARE_NUMERIC_EXPORT
    global _ORIGINAL_VALIDATE_PER_COMPONENT_VG_IDS
    global _ORIGINAL_GET_COMPONENT_LOCAL_VG_COUNT

    if not _PATCHES_INSTALLED:
        return
    try:
        if object_merger_module is None:
            from ._efmi_core.blender_export import object_merger as object_merger_module

        merger_cls = object_merger_module.ObjectMerger
        if _ORIGINAL_PREPARE_NUMERIC_EXPORT is not None:
            merger_cls._prepare_temp_object_for_numeric_export = _ORIGINAL_PREPARE_NUMERIC_EXPORT
        if _ORIGINAL_VALIDATE_PER_COMPONENT_VG_IDS is not None:
            merger_cls._validate_per_component_vg_ids = _ORIGINAL_VALIDATE_PER_COMPONENT_VG_IDS
        if _ORIGINAL_GET_COMPONENT_LOCAL_VG_COUNT is not None:
            merger_cls._get_component_local_vg_count = _ORIGINAL_GET_COMPONENT_LOCAL_VG_COUNT
    finally:
        _ORIGINAL_PREPARE_NUMERIC_EXPORT = None
        _ORIGINAL_VALIDATE_PER_COMPONENT_VG_IDS = None
        _ORIGINAL_GET_COMPONENT_LOCAL_VG_COUNT = None
        _PATCHES_INSTALLED = False
