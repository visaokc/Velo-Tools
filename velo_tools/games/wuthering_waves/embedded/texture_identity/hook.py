"""Reversible driver mounts for extraction and exporter preview generation."""

from __future__ import annotations

from pathlib import Path

from .exporter import PREVIEW_FILENAME, apply_manifest_to_ini
from .manifest import MANIFEST_FILENAME, write_manifest


_INSTALLED = False
_ORIGINAL_EXTRACT_FRAME_DATA = None
_ORIGINAL_UI_EXTRACT_FRAME_DATA = None
_ORIGINAL_WRITE_OBJECTS = None
_ORIGINAL_WRITE_FILES = None
_EXTRACT_MODULE = None
_EXPORT_MODULE = None
_UI_MODULE = None
_ACTIVE_MANIFEST_ENABLED = False


def _wrapped_extract_frame_data(cfg):
    global _ACTIVE_MANIFEST_ENABLED
    _ACTIVE_MANIFEST_ENABLED = bool(
        getattr(cfg, "extract_texture_identity_manifest", True))
    try:
        return _ORIGINAL_EXTRACT_FRAME_DATA(cfg)
    finally:
        _ACTIVE_MANIFEST_ENABLED = False


def _wrapped_write_objects(output_directory, objects, allow_missing_shapekeys=False):
    result = _ORIGINAL_WRITE_OBJECTS(output_directory, objects, allow_missing_shapekeys)
    output_directory = Path(output_directory)
    for object_hash, object_data in objects.items():
        object_name = object_hash
        shapekeys = object_data.shapekeys
        if shapekeys.offsets_hash and not shapekeys.shapekey_offsets:
            if allow_missing_shapekeys:
                object_name += "_MISSING_SHAPEKEYS"
            else:
                continue
        object_directory = output_directory / object_name
        if _ACTIVE_MANIFEST_ENABLED:
            write_manifest(object_directory)
        else:
            (object_directory / MANIFEST_FILENAME).unlink(missing_ok=True)
    return result


def _wrapped_write_files(self):
    result = _ORIGINAL_WRITE_FILES(self)
    source_folder = getattr(self, "object_source_folder", None)
    output_folder = getattr(self, "mod_output_folder", None)
    if source_folder is not None and output_folder is not None:
        output_folder = Path(output_folder)
        (output_folder / PREVIEW_FILENAME).unlink(missing_ok=True)
        if (
            bool(getattr(self.cfg, "use_texture_identity_matching", False))
            and not bool(getattr(self.cfg, "partial_export", False))
            and bool(getattr(self.cfg, "write_ini", True))
        ):
            apply_manifest_to_ini(
                source_folder,
                output_folder / "mod.ini",
            )
    return result


def install() -> None:
    global _INSTALLED
    global _ORIGINAL_EXTRACT_FRAME_DATA
    global _ORIGINAL_UI_EXTRACT_FRAME_DATA
    global _ORIGINAL_WRITE_OBJECTS, _ORIGINAL_WRITE_FILES
    global _EXTRACT_MODULE, _EXPORT_MODULE, _UI_MODULE
    if _INSTALLED:
        return
    from ..._wwmi_core.blender_export import blender_export as export_module
    from ..._wwmi_core.addon import ui as ui_module
    from ..._wwmi_core.extract_frame_data import extract_frame_data as extract_module

    _EXTRACT_MODULE = extract_module
    _EXPORT_MODULE = export_module
    _UI_MODULE = ui_module
    _ORIGINAL_EXTRACT_FRAME_DATA = extract_module.extract_frame_data
    _ORIGINAL_UI_EXTRACT_FRAME_DATA = ui_module.extract_frame_data
    _ORIGINAL_WRITE_OBJECTS = extract_module.write_objects
    _ORIGINAL_WRITE_FILES = export_module.ModExporter.write_files
    extract_module.extract_frame_data = _wrapped_extract_frame_data
    ui_module.extract_frame_data = _wrapped_extract_frame_data
    extract_module.write_objects = _wrapped_write_objects
    export_module.ModExporter.write_files = _wrapped_write_files
    _INSTALLED = True


def remove() -> None:
    global _INSTALLED
    global _ORIGINAL_EXTRACT_FRAME_DATA
    global _ORIGINAL_UI_EXTRACT_FRAME_DATA
    global _ORIGINAL_WRITE_OBJECTS, _ORIGINAL_WRITE_FILES
    global _EXTRACT_MODULE, _EXPORT_MODULE, _UI_MODULE
    if not _INSTALLED:
        return
    try:
        if _ORIGINAL_EXTRACT_FRAME_DATA is not None:
            _EXTRACT_MODULE.extract_frame_data = _ORIGINAL_EXTRACT_FRAME_DATA
        if _ORIGINAL_UI_EXTRACT_FRAME_DATA is not None:
            _UI_MODULE.extract_frame_data = _ORIGINAL_UI_EXTRACT_FRAME_DATA
        if _ORIGINAL_WRITE_OBJECTS is not None:
            _EXTRACT_MODULE.write_objects = _ORIGINAL_WRITE_OBJECTS
        if _ORIGINAL_WRITE_FILES is not None:
            _EXPORT_MODULE.ModExporter.write_files = _ORIGINAL_WRITE_FILES
    finally:
        _ORIGINAL_EXTRACT_FRAME_DATA = None
        _ORIGINAL_UI_EXTRACT_FRAME_DATA = None
        _ORIGINAL_WRITE_OBJECTS = None
        _ORIGINAL_WRITE_FILES = None
        _EXTRACT_MODULE = None
        _EXPORT_MODULE = None
        _UI_MODULE = None
        _INSTALLED = False
