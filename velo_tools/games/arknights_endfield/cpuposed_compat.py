"""CPU-posed compatibility shims for Velo's Endfield Merged workflow."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterable


_PATCHES_INSTALLED = False
_ORIGINAL_APPLY_MERGED_VERTEX_GROUP_NAMES = None
_ORIGINAL_APPLY_TO_METADATA = None
_ORIGINAL_BUILD_MERGED_OBJECT = None
_ORIGINAL_BUILD_DATA_BUFFERS = None
_ORIGINAL_BUILD_MOD_INI = None

_RECORDED_CPU_POSED_IDS_ATTR = "_velo_cpu_posed_component_ids"
_VELO_EXPORT_SUFFIX_RE = re.compile(r"__velo_export(?:\.\d{3})?$")


def _component_is_cpu_posed(metadata: Any, component_id: int) -> bool:
    components = list(getattr(metadata, "components", []) or [])
    if component_id < 0 or component_id >= len(components):
        return False
    return bool(getattr(components[component_id], "cpu_posed", False))


def _dict_component_is_cpu_posed(components: list[Any], component_id: int) -> bool:
    if component_id < 0 or component_id >= len(components):
        return False
    component = components[component_id] or {}
    if isinstance(component, dict):
        return bool(component.get("cpu_posed", False))
    return bool(getattr(component, "cpu_posed", False))


def _entry_vg_map(entry: Any) -> dict:
    if entry is None:
        return {}
    if isinstance(entry, dict):
        value = entry.get("vg_map")
    else:
        value = getattr(entry, "vg_map", None)
    return value if isinstance(value, dict) else {}


def empty_vgmap_components_requiring_geometry(
    metadata_components: Iterable[Any],
    vgmap_components: Iterable[Any],
    component_ids: Iterable[int],
) -> list[int]:
    metadata_list = list(metadata_components or [])
    vgmap_list = list(vgmap_components or [])
    invalid: list[int] = []
    for component_id in component_ids:
        if component_id < 0 or component_id >= len(vgmap_list):
            continue
        if _entry_vg_map(vgmap_list[component_id]):
            continue
        if _dict_component_is_cpu_posed(metadata_list, component_id):
            continue
        invalid.append(component_id)
    return invalid


def _load_metadata_for_cfg(cfg: Any) -> Any:
    from ._efmi_core.migoto_io.blender_interface.utility import resolve_path
    from ._efmi_core.migoto_io.object_extractor.migoto_object.metadata_format import read_metadata

    return read_metadata(Path(resolve_path(getattr(cfg, "object_source_folder", ""))) / "Metadata.json")


def wrap_apply_merged_vertex_group_names(
    original: Callable[..., Any],
    metadata_loader: Callable[[Any], Any] = _load_metadata_for_cfg,
) -> Callable[..., Any]:
    def wrapped(obj: Any, component_id: int, vertex_group_map: Any, cfg: Any) -> Any:
        try:
            entry = vertex_group_map.components[component_id]
        except Exception:
            return original(obj, component_id, vertex_group_map, cfg)

        if _entry_vg_map(entry):
            return original(obj, component_id, vertex_group_map, cfg)

        try:
            metadata = metadata_loader(cfg)
        except Exception:
            return original(obj, component_id, vertex_group_map, cfg)

        if _component_is_cpu_posed(metadata, component_id):
            return None
        return original(obj, component_id, vertex_group_map, cfg)

    return wrapped


def prepare_metadata_for_merged_cpu_posed_export(
    metadata: Any,
    vertex_group_map: Any,
    apply_to_metadata: Callable[[Any, Any], Any],
) -> tuple[int, ...]:
    apply_to_metadata(metadata, vertex_group_map)

    cpu_component_ids: list[int] = []
    for component_id, component in enumerate(getattr(metadata, "components", []) or []):
        if bool(getattr(component, "cpu_posed", False)):
            cpu_component_ids.append(component_id)
            setattr(component, "cpu_posed", False)

    setattr(metadata, _RECORDED_CPU_POSED_IDS_ATTR, tuple(cpu_component_ids))
    return tuple(cpu_component_ids)


def is_recorded_cpu_posed_component(metadata: Any, component_id: int) -> bool:
    return component_id in set(getattr(metadata, _RECORDED_CPU_POSED_IDS_ATTR, ()) or ())


def should_skip_cpu_posed_geometry_buffers(metadata: Any, component_id: int) -> bool:
    return is_recorded_cpu_posed_component(metadata, component_id)


def restore_recorded_cpu_posed_components(metadata: Any) -> None:
    cpu_component_ids = set(getattr(metadata, _RECORDED_CPU_POSED_IDS_ATTR, ()) or ())
    for component_id, component in enumerate(getattr(metadata, "components", []) or []):
        if component_id in cpu_component_ids:
            setattr(component, "cpu_posed", True)


def _sanitize_export_object_name(name: str) -> str:
    return _VELO_EXPORT_SUFFIX_RE.sub("", (name or "").strip())


def build_cpu_posed_placeholder_merged_object(exporter: Any, component_id: int) -> Any:
    from ._efmi_core.blender_export.object_merger import (
        MergedObject,
        MergedObjectComponent,
        MergedObjectShapeKeys,
        SkeletonType,
        TempObject,
    )
    from ._efmi_core.migoto_io.blender_interface.collections import get_collection_objects
    from ._efmi_core.migoto_io.blender_interface.objects import object_is_hidden

    cfg = exporter.cfg
    pattern = re.compile(r".*component[_ -]*({})(?!\d).*".format(int(component_id)))
    objects = []
    for obj in get_collection_objects(
        cfg.component_collection,
        recursive=not bool(getattr(cfg, "ignore_nested_collections", False)),
        skip_hidden_collections=bool(getattr(cfg, "ignore_hidden_collections", False)),
    ):
        if getattr(obj, "type", None) != "MESH":
            continue
        if bool(getattr(cfg, "ignore_hidden_objects", False)) and object_is_hidden(obj):
            continue
        if (getattr(obj, "name", "") or "").startswith("TEMP_"):
            continue
        if not pattern.findall((obj.name or "").lower()):
            continue
        objects.append(
            TempObject(
                name=_sanitize_export_object_name(obj.name),
                object=None,
                vertex_count=0,
                index_count=0,
                index_offset=0,
            )
        )

    objects.sort(key=lambda item: item.name)
    component = MergedObjectComponent(id=component_id, objects=objects, vertex_count=0, index_count=0)
    return MergedObject(
        object=None,
        components=[component],
        shapekeys=MergedObjectShapeKeys(),
        skeleton_type=SkeletonType.PerComponent,
        vertex_count=0,
        index_count=0,
    )


def install_patches() -> None:
    global _PATCHES_INSTALLED
    global _ORIGINAL_APPLY_MERGED_VERTEX_GROUP_NAMES
    global _ORIGINAL_APPLY_TO_METADATA
    global _ORIGINAL_BUILD_MERGED_OBJECT
    global _ORIGINAL_BUILD_DATA_BUFFERS
    global _ORIGINAL_BUILD_MOD_INI

    if _PATCHES_INSTALLED:
        return

    from . import vgmap as velo_vgmap
    from ._efmi_core.blender_import import blender_import as import_module
    from ._efmi_core.blender_export import blender_export as export_module

    _ORIGINAL_APPLY_MERGED_VERTEX_GROUP_NAMES = import_module._apply_merged_vertex_group_names
    import_module._apply_merged_vertex_group_names = wrap_apply_merged_vertex_group_names(
        _ORIGINAL_APPLY_MERGED_VERTEX_GROUP_NAMES
    )

    _ORIGINAL_APPLY_TO_METADATA = velo_vgmap.apply_to_metadata

    def apply_to_metadata_with_cpu_posed_export_metadata(metadata: Any, vertex_group_map: Any) -> Any:
        return prepare_metadata_for_merged_cpu_posed_export(
            metadata,
            vertex_group_map,
            _ORIGINAL_APPLY_TO_METADATA,
        )

    velo_vgmap.apply_to_metadata = apply_to_metadata_with_cpu_posed_export_metadata

    _ORIGINAL_BUILD_MERGED_OBJECT = export_module.ModExporter.build_merged_object
    _ORIGINAL_BUILD_DATA_BUFFERS = export_module.ModExporter.build_data_buffers
    _ORIGINAL_BUILD_MOD_INI = export_module.ModExporter.build_mod_ini

    def build_merged_object_with_cpu_posed_skip(self: Any, component_id: int = -1) -> Any:
        if component_id >= 0 and should_skip_cpu_posed_geometry_buffers(self.extracted_object, component_id):
            return build_cpu_posed_placeholder_merged_object(self, component_id)
        return _ORIGINAL_BUILD_MERGED_OBJECT(self, component_id)

    def build_data_buffers_with_cpu_posed_skip(self: Any, merged_object: Any, component_id: int = -1) -> Any:
        if component_id >= 0 and should_skip_cpu_posed_geometry_buffers(self.extracted_object, component_id):
            return None
        return _ORIGINAL_BUILD_DATA_BUFFERS(self, merged_object, component_id)

    def build_mod_ini_with_cpu_posed_flags(self: Any) -> Any:
        restore_recorded_cpu_posed_components(self.extracted_object)
        return _ORIGINAL_BUILD_MOD_INI(self)

    export_module.ModExporter.build_merged_object = build_merged_object_with_cpu_posed_skip
    export_module.ModExporter.build_data_buffers = build_data_buffers_with_cpu_posed_skip
    export_module.ModExporter.build_mod_ini = build_mod_ini_with_cpu_posed_flags

    _PATCHES_INSTALLED = True


def uninstall_patches() -> None:
    global _PATCHES_INSTALLED
    global _ORIGINAL_APPLY_MERGED_VERTEX_GROUP_NAMES
    global _ORIGINAL_APPLY_TO_METADATA
    global _ORIGINAL_BUILD_MERGED_OBJECT
    global _ORIGINAL_BUILD_DATA_BUFFERS
    global _ORIGINAL_BUILD_MOD_INI

    if not _PATCHES_INSTALLED:
        return

    try:
        from . import vgmap as velo_vgmap
        from ._efmi_core.blender_import import blender_import as import_module
        from ._efmi_core.blender_export import blender_export as export_module

        if _ORIGINAL_APPLY_MERGED_VERTEX_GROUP_NAMES is not None:
            import_module._apply_merged_vertex_group_names = _ORIGINAL_APPLY_MERGED_VERTEX_GROUP_NAMES
        if _ORIGINAL_APPLY_TO_METADATA is not None:
            velo_vgmap.apply_to_metadata = _ORIGINAL_APPLY_TO_METADATA
        if _ORIGINAL_BUILD_MERGED_OBJECT is not None:
            export_module.ModExporter.build_merged_object = _ORIGINAL_BUILD_MERGED_OBJECT
        if _ORIGINAL_BUILD_DATA_BUFFERS is not None:
            export_module.ModExporter.build_data_buffers = _ORIGINAL_BUILD_DATA_BUFFERS
        if _ORIGINAL_BUILD_MOD_INI is not None:
            export_module.ModExporter.build_mod_ini = _ORIGINAL_BUILD_MOD_INI
    finally:
        _ORIGINAL_APPLY_MERGED_VERTEX_GROUP_NAMES = None
        _ORIGINAL_APPLY_TO_METADATA = None
        _ORIGINAL_BUILD_MERGED_OBJECT = None
        _ORIGINAL_BUILD_DATA_BUFFERS = None
        _ORIGINAL_BUILD_MOD_INI = None
        _PATCHES_INSTALLED = False
