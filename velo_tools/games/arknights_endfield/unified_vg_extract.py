"""Extraction patches for matrix-signature unified vertex-group maps."""

from __future__ import annotations

import json


_PATCHED = False
_ORIGINAL_BUILD_VG_MAP = None
_ORIGINAL_EXPORT_METADATA = None
_ORIGINAL_FROM_DICT = None


def _clear_maps(migoto_object):
    if migoto_object.metadata is None:
        return
    for component in migoto_object.metadata.components:
        component.vg_map = {}
        component.vg_offset = 0
        component.vg_count = 0
        component.runtime_vg_map = {}
        component.runtime_source_valid = False
        component.runtime_source_weights = {}


def _build_matrix_signature_vg_map(self, migoto_object):
    from ._efmi_core.migoto_io.migoto_model.migoto_mesh import WeightingType
    from .unified_vg_builder import apply_to_metadata

    if migoto_object.metadata.weigthing_type != WeightingType.Explicit:
        print(
            f"[{migoto_object.id}]: Skipped building unified VG map "
            f"({migoto_object.metadata.weigthing_type.value} object)"
        )
        return
    try:
        checker = getattr(self.merged_skeleton_filter, "is_valid_bone_source", None)
        result = apply_to_metadata(migoto_object, checker)
        runtime_count = sum(result.vg_counts)
        print(
            f"[{migoto_object.id}]: Built matrix-signature VG map: "
            f"{runtime_count} local slots -> {result.authoring_group_count} compact groups"
        )
    except Exception as exc:
        _clear_maps(migoto_object)
        print(
            f"[{migoto_object.id}]: Failed to build matrix-signature VG map: {exc} "
            "(this object cannot use unified or merged skeleton export)"
        )


def _from_dict_with_runtime_map(cls, data):
    from ._efmi_core.migoto_io.object_extractor.migoto_object import metadata_format

    result = _ORIGINAL_FROM_DICT(cls, data)
    if cls is metadata_format.ExtractedObjectComponent and isinstance(data, dict):
        runtime_map = data.get("runtime_vg_map") or {}
        result.runtime_vg_map = {int(local): int(runtime) for local, runtime in runtime_map.items()}
        result.runtime_source_valid = bool(data.get("runtime_source_valid", True))
        source_weights = data.get("runtime_source_weights") or {}
        result.runtime_source_weights = {
            int(local): int(weight) for local, weight in source_weights.items()
        }
    return result


def _export_metadata_with_runtime_map(self, folder_path):
    _ORIGINAL_EXPORT_METADATA(self, folder_path)
    metadata_path = folder_path / "Metadata.json"
    with open(metadata_path, encoding="utf-8") as fh:
        data = json.load(fh)
    for component_data, component in zip(data.get("components", []), self.metadata.components):
        runtime_map = getattr(component, "runtime_vg_map", None)
        if runtime_map is not None:
            component_data["runtime_vg_map"] = {
                str(local): int(runtime) for local, runtime in sorted(runtime_map.items())
            }
        component_data["runtime_source_valid"] = bool(
            getattr(component, "runtime_source_valid", True)
        )
        source_weights = getattr(component, "runtime_source_weights", None) or {}
        component_data["runtime_source_weights"] = {
            str(local): int(weight) for local, weight in sorted(source_weights.items())
        }
    with open(metadata_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=4)
        fh.write("\n")


def install_patches():
    global _PATCHED, _ORIGINAL_BUILD_VG_MAP, _ORIGINAL_EXPORT_METADATA, _ORIGINAL_FROM_DICT
    if _PATCHED:
        return

    from ._efmi_core.migoto_io.object_extractor.migoto_object import metadata_format
    from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object import MigotoObject
    from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object_builder import MigotoObjectBuilder

    _ORIGINAL_BUILD_VG_MAP = MigotoObjectBuilder.build_vg_map
    _ORIGINAL_EXPORT_METADATA = MigotoObject.export_metadata
    _ORIGINAL_FROM_DICT = metadata_format.from_dict
    MigotoObjectBuilder.build_vg_map = _build_matrix_signature_vg_map
    MigotoObject.export_metadata = _export_metadata_with_runtime_map
    metadata_format.from_dict = _from_dict_with_runtime_map
    _PATCHED = True


def remove_patches():
    global _PATCHED, _ORIGINAL_BUILD_VG_MAP, _ORIGINAL_EXPORT_METADATA, _ORIGINAL_FROM_DICT
    if not _PATCHED:
        return

    from ._efmi_core.migoto_io.object_extractor.migoto_object import metadata_format
    from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object import MigotoObject
    from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object_builder import MigotoObjectBuilder

    if MigotoObjectBuilder.build_vg_map is _build_matrix_signature_vg_map:
        MigotoObjectBuilder.build_vg_map = _ORIGINAL_BUILD_VG_MAP
    if MigotoObject.export_metadata is _export_metadata_with_runtime_map:
        MigotoObject.export_metadata = _ORIGINAL_EXPORT_METADATA
    if metadata_format.from_dict is _from_dict_with_runtime_map:
        metadata_format.from_dict = _ORIGINAL_FROM_DICT
    _ORIGINAL_BUILD_VG_MAP = None
    _ORIGINAL_EXPORT_METADATA = None
    _ORIGINAL_FROM_DICT = None
    _PATCHED = False
