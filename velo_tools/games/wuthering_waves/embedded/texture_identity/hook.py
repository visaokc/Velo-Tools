"""Reversible driver mounts for extraction and exporter preview generation."""

from __future__ import annotations

from pathlib import Path

from .exporter import PREVIEW_FILENAME, apply_manifest_to_ini
from .manifest import MANIFEST_FILENAME, write_manifest


_INSTALLED = False
_ORIGINAL_BUILD_COMPONENTS = None
_ORIGINAL_EXTRACT_FRAME_DATA = None
_ORIGINAL_UI_EXTRACT_FRAME_DATA = None
_ORIGINAL_WRITE_OBJECTS = None
_ORIGINAL_WRITE_FILES = None
_COMPONENT_BUILDER_MODULE = None
_EXTRACT_MODULE = None
_EXPORT_MODULE = None
_UI_MODULE = None
_CAPTURE = {}
_ACTIVE_SOURCE_PROFILE = None
_ACTIVE_MANIFEST_ENABLED = False


def _capture_components(mesh_object) -> dict:
    components = []
    for component_data in getattr(mesh_object, "components_data", ()):
        draw_data = getattr(component_data, "draw_data", None)
        if draw_data is None:
            continue
        components.append(
            {
                "call_id": getattr(draw_data, "call_id", None),
                "ib_hash": str(getattr(draw_data, "ib_hash", "") or ""),
                "vb0_hash": str(getattr(draw_data, "vb0_hash", "") or ""),
            }
        )
    return {"components": components}


def _wrapped_build_components(self, vb_layout, shapekeys):
    result = _ORIGINAL_BUILD_COMPONENTS(self, vb_layout, shapekeys)
    object_hash = str(getattr(self, "vb0_hash", "") or "")
    if object_hash:
        _CAPTURE[object_hash] = _capture_components(self)
    return result


def _wrapped_extract_frame_data(cfg):
    global _ACTIVE_SOURCE_PROFILE, _ACTIVE_MANIFEST_ENABLED
    _ACTIVE_SOURCE_PROFILE = "extracted-object"
    _ACTIVE_MANIFEST_ENABLED = bool(
        getattr(cfg, "extract_texture_identity_manifest", True))
    try:
        return _ORIGINAL_EXTRACT_FRAME_DATA(cfg)
    finally:
        _ACTIVE_SOURCE_PROFILE = None
        _ACTIVE_MANIFEST_ENABLED = False


def _wrapped_write_objects(output_directory, objects, allow_missing_shapekeys=False):
    try:
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
                write_manifest(
                    object_directory,
                    object_hash,
                    source_profile=_ACTIVE_SOURCE_PROFILE,
                    capture=_CAPTURE.get(object_hash),
                )
            elif not _ACTIVE_MANIFEST_ENABLED:
                (object_directory / MANIFEST_FILENAME).unlink(missing_ok=True)
    finally:
        _CAPTURE.clear()
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
    global _ORIGINAL_BUILD_COMPONENTS, _ORIGINAL_EXTRACT_FRAME_DATA
    global _ORIGINAL_UI_EXTRACT_FRAME_DATA
    global _ORIGINAL_WRITE_OBJECTS, _ORIGINAL_WRITE_FILES
    global _COMPONENT_BUILDER_MODULE, _EXTRACT_MODULE, _EXPORT_MODULE, _UI_MODULE
    if _INSTALLED:
        return
    from ..._wwmi_core.blender_export import blender_export as export_module
    from ..._wwmi_core.addon import ui as ui_module
    from ..._wwmi_core.extract_frame_data import component_builder as component_builder_module
    from ..._wwmi_core.extract_frame_data import extract_frame_data as extract_module

    _COMPONENT_BUILDER_MODULE = component_builder_module
    _EXTRACT_MODULE = extract_module
    _EXPORT_MODULE = export_module
    _UI_MODULE = ui_module
    _ORIGINAL_BUILD_COMPONENTS = component_builder_module.MeshObject.build_components
    _ORIGINAL_EXTRACT_FRAME_DATA = extract_module.extract_frame_data
    _ORIGINAL_UI_EXTRACT_FRAME_DATA = ui_module.extract_frame_data
    _ORIGINAL_WRITE_OBJECTS = extract_module.write_objects
    _ORIGINAL_WRITE_FILES = export_module.ModExporter.write_files
    component_builder_module.MeshObject.build_components = _wrapped_build_components
    extract_module.extract_frame_data = _wrapped_extract_frame_data
    ui_module.extract_frame_data = _wrapped_extract_frame_data
    extract_module.write_objects = _wrapped_write_objects
    export_module.ModExporter.write_files = _wrapped_write_files
    _INSTALLED = True


def remove() -> None:
    global _INSTALLED
    global _ORIGINAL_BUILD_COMPONENTS, _ORIGINAL_EXTRACT_FRAME_DATA
    global _ORIGINAL_UI_EXTRACT_FRAME_DATA
    global _ORIGINAL_WRITE_OBJECTS, _ORIGINAL_WRITE_FILES
    global _COMPONENT_BUILDER_MODULE, _EXTRACT_MODULE, _EXPORT_MODULE, _UI_MODULE
    global _ACTIVE_SOURCE_PROFILE
    if not _INSTALLED:
        return
    try:
        if _ORIGINAL_BUILD_COMPONENTS is not None:
            _COMPONENT_BUILDER_MODULE.MeshObject.build_components = _ORIGINAL_BUILD_COMPONENTS
        if _ORIGINAL_EXTRACT_FRAME_DATA is not None:
            _EXTRACT_MODULE.extract_frame_data = _ORIGINAL_EXTRACT_FRAME_DATA
        if _ORIGINAL_UI_EXTRACT_FRAME_DATA is not None:
            _UI_MODULE.extract_frame_data = _ORIGINAL_UI_EXTRACT_FRAME_DATA
        if _ORIGINAL_WRITE_OBJECTS is not None:
            _EXTRACT_MODULE.write_objects = _ORIGINAL_WRITE_OBJECTS
        if _ORIGINAL_WRITE_FILES is not None:
            _EXPORT_MODULE.ModExporter.write_files = _ORIGINAL_WRITE_FILES
    finally:
        _ORIGINAL_BUILD_COMPONENTS = None
        _ORIGINAL_EXTRACT_FRAME_DATA = None
        _ORIGINAL_UI_EXTRACT_FRAME_DATA = None
        _ORIGINAL_WRITE_OBJECTS = None
        _ORIGINAL_WRITE_FILES = None
        _COMPONENT_BUILDER_MODULE = None
        _EXTRACT_MODULE = None
        _EXPORT_MODULE = None
        _UI_MODULE = None
        _CAPTURE.clear()
        _ACTIVE_SOURCE_PROFILE = None
        _INSTALLED = False
