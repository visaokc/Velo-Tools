"""Reversible driver mounts for extraction and exporter preview generation."""

from __future__ import annotations

from pathlib import Path

from .exporter import consume_manifest
from .manifest import write_manifest


_INSTALLED = False
_ORIGINAL_BUILD_COMPONENTS = None
_ORIGINAL_WRITE_OBJECTS = None
_ORIGINAL_WRITE_FILES = None
_COMPONENT_BUILDER_MODULE = None
_EXTRACT_MODULE = None
_EXPORT_MODULE = None
_CAPTURE = {}


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
            write_manifest(
                output_directory / object_name,
                object_hash,
                capture=_CAPTURE.get(object_hash),
            )
    finally:
        _CAPTURE.clear()
    return result


def _wrapped_write_files(self):
    result = _ORIGINAL_WRITE_FILES(self)
    source_folder = getattr(self, "object_source_folder", None)
    output_folder = getattr(self, "mod_output_folder", None)
    if source_folder is not None and output_folder is not None:
        try:
            consume_manifest(source_folder, output_folder)
        except Exception as exc:
            print(f"[texture-identity] preview generation skipped: {exc}")
    return result


def install() -> None:
    global _INSTALLED
    global _ORIGINAL_BUILD_COMPONENTS, _ORIGINAL_WRITE_OBJECTS, _ORIGINAL_WRITE_FILES
    global _COMPONENT_BUILDER_MODULE, _EXTRACT_MODULE, _EXPORT_MODULE
    if _INSTALLED:
        return
    from ..._wwmi_core.blender_export import blender_export as export_module
    from ..._wwmi_core.extract_frame_data import component_builder as component_builder_module
    from ..._wwmi_core.extract_frame_data import extract_frame_data as extract_module

    _COMPONENT_BUILDER_MODULE = component_builder_module
    _EXTRACT_MODULE = extract_module
    _EXPORT_MODULE = export_module
    _ORIGINAL_BUILD_COMPONENTS = component_builder_module.MeshObject.build_components
    _ORIGINAL_WRITE_OBJECTS = extract_module.write_objects
    _ORIGINAL_WRITE_FILES = export_module.ModExporter.write_files
    component_builder_module.MeshObject.build_components = _wrapped_build_components
    extract_module.write_objects = _wrapped_write_objects
    export_module.ModExporter.write_files = _wrapped_write_files
    _INSTALLED = True


def remove() -> None:
    global _INSTALLED
    global _ORIGINAL_BUILD_COMPONENTS, _ORIGINAL_WRITE_OBJECTS, _ORIGINAL_WRITE_FILES
    global _COMPONENT_BUILDER_MODULE, _EXTRACT_MODULE, _EXPORT_MODULE
    if not _INSTALLED:
        return
    try:
        if _ORIGINAL_BUILD_COMPONENTS is not None:
            _COMPONENT_BUILDER_MODULE.MeshObject.build_components = _ORIGINAL_BUILD_COMPONENTS
        if _ORIGINAL_WRITE_OBJECTS is not None:
            _EXTRACT_MODULE.write_objects = _ORIGINAL_WRITE_OBJECTS
        if _ORIGINAL_WRITE_FILES is not None:
            _EXPORT_MODULE.ModExporter.write_files = _ORIGINAL_WRITE_FILES
    finally:
        _ORIGINAL_BUILD_COMPONENTS = None
        _ORIGINAL_WRITE_OBJECTS = None
        _ORIGINAL_WRITE_FILES = None
        _COMPONENT_BUILDER_MODULE = None
        _EXTRACT_MODULE = None
        _EXPORT_MODULE = None
        _CAPTURE.clear()
        _INSTALLED = False
