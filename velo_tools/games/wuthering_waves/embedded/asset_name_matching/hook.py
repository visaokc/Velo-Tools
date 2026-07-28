"""Reversible exporter mount for Unreal asset-name texture matching."""

from __future__ import annotations

from pathlib import Path

from .exporter import apply_stu_to_ini


_INSTALLED = False
_ORIGINAL_WRITE_FILES = None
_EXPORT_MODULE = None


def _wrapped_write_files(self):
    result = _ORIGINAL_WRITE_FILES(self)
    source_folder = getattr(self, "object_source_folder", None)
    output_folder = getattr(self, "mod_output_folder", None)
    if (
        source_folder is not None
        and output_folder is not None
        and bool(getattr(self.cfg, "use_asset_name_matching", False))
        and not bool(getattr(self.cfg, "partial_export", False))
        and bool(getattr(self.cfg, "write_ini", True))
    ):
        apply_stu_to_ini(
            source_folder,
            Path(output_folder) / "mod.ini",
        )
    return result


def install() -> None:
    global _INSTALLED, _ORIGINAL_WRITE_FILES, _EXPORT_MODULE
    if _INSTALLED:
        return
    from ..._wwmi_core.blender_export import blender_export as export_module

    _EXPORT_MODULE = export_module
    _ORIGINAL_WRITE_FILES = export_module.ModExporter.write_files
    export_module.ModExporter.write_files = _wrapped_write_files
    _INSTALLED = True


def remove() -> None:
    global _INSTALLED, _ORIGINAL_WRITE_FILES, _EXPORT_MODULE
    if not _INSTALLED:
        return
    try:
        if _ORIGINAL_WRITE_FILES is not None:
            _EXPORT_MODULE.ModExporter.write_files = _ORIGINAL_WRITE_FILES
    finally:
        _ORIGINAL_WRITE_FILES = None
        _EXPORT_MODULE = None
        _INSTALLED = False
