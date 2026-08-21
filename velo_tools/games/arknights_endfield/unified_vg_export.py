"""Compatibility export mode for unified authoring and local runtime palettes."""

from pathlib import Path


_PATCHED = False
_ORIGINAL_EXPORT_MOD = None
_ORIGINAL_FINALIZE_DATA = None


def _component_map(merger, component_id):
    component = merger.extracted_object.components[component_id]
    mapping = getattr(component, "vg_map", None) or {}
    if mapping:
        return {int(local): int(global_id) for local, global_id in mapping.items()}

    from . import vgmap
    from ._efmi_core.migoto_io.blender_interface.utility import resolve_path

    folder = Path(resolve_path(merger.context.scene.VTEF_settings.object_source_folder))
    sidecar = vgmap.load_for_metadata(folder, merger.extracted_object)
    return {
        int(local): int(global_id)
        for local, global_id in sidecar.components[component_id].vg_map.items()
    }


def _has_weight(obj, group_index):
    for vertex in obj.data.vertices:
        for assignment in vertex.groups:
            if assignment.group == group_index and assignment.weight > 1e-6:
                return True
    return False


def _translate_object_to_local(merger, obj, component_id):
    from ._efmi_core.addon.exceptions import ConfigError
    from ._efmi_core.migoto_io.blender_tools.vertex_groups import remove_unused_vertex_groups
    from ...core.mapping.algorithms import reorder_numeric_vertex_groups_first

    local_to_global = _component_map(merger, component_id)
    global_to_local = {}
    for local_id, global_id in local_to_global.items():
        current = global_to_local.get(global_id)
        if current is None or local_id < current:
            global_to_local[global_id] = local_id

    remove_unused_vertex_groups(merger.context, obj)
    unmatched = []
    to_remove = []
    prefix = "__unified_to_local_"
    for group in list(obj.vertex_groups):
        name = (group.name or "").strip()
        if not name.isdigit():
            continue
        local_id = global_to_local.get(int(name))
        if local_id is None:
            if _has_weight(obj, group.index):
                unmatched.append(name)
            else:
                to_remove.append(group)
            continue
        group.name = f"{prefix}{local_id}"

    if unmatched:
        preview = ", ".join(unmatched[:12])
        if len(unmatched) > 12:
            preview += f", ... (+{len(unmatched) - 12})"
        raise ConfigError(
            "component_collection",
            f"物体 `{obj.name}` (Component {component_id}) 的统一顶点组不属于该部件骨表：{preview}。",
        )

    for group in to_remove:
        obj.vertex_groups.remove(group)
    for group in obj.vertex_groups:
        if group.name.startswith(prefix):
            group.name = group.name[len(prefix):]

    local_count = max(local_to_global, default=-1) + 1
    names = {group.name for group in obj.vertex_groups}
    for local_id in range(local_count):
        name = str(local_id)
        if name not in names:
            obj.vertex_groups.new(name=name)
            names.add(name)
    reorder_numeric_vertex_groups_first(obj)


def _finalize_unified_as_component(self):
    cfg = getattr(self.context.scene, "VTEF_settings", None)
    if cfg is None or not bool(cfg.get("_unified_vg_component_export", False)):
        return _ORIGINAL_FINALIZE_DATA(self)

    for component in self.components:
        for temp_object in component.objects:
            _translate_object_to_local(self, temp_object.object, component.id)

    previous = self.add_missing_vertex_groups
    self.add_missing_vertex_groups = False
    try:
        return _ORIGINAL_FINALIZE_DATA(self)
    finally:
        self.add_missing_vertex_groups = previous


def _export_with_three_modes(self, *args, **kwargs):
    cfg = self.cfg
    requested_mode = cfg.mod_skeleton_type
    if requested_mode not in {"MERGED", "MERGED_SKELETON"}:
        return _ORIGINAL_EXPORT_MOD(self, *args, **kwargs)

    intermediate = requested_mode == "MERGED"
    cfg["_unified_vg_component_export"] = intermediate
    cfg.mod_skeleton_type = "COMPONENT" if intermediate else "MERGED"
    try:
        return _ORIGINAL_EXPORT_MOD(self, *args, **kwargs)
    finally:
        cfg.mod_skeleton_type = requested_mode
        try:
            del cfg["_unified_vg_component_export"]
        except Exception:
            pass


def install_patches():
    global _PATCHED, _ORIGINAL_EXPORT_MOD, _ORIGINAL_FINALIZE_DATA
    if _PATCHED:
        return

    from ._efmi_core.blender_export.blender_export import ModExporter, ObjectMergerEFMI

    _ORIGINAL_EXPORT_MOD = ModExporter.export_mod
    _ORIGINAL_FINALIZE_DATA = ObjectMergerEFMI.finalize_temp_objects_data
    ModExporter.export_mod = _export_with_three_modes
    ObjectMergerEFMI.finalize_temp_objects_data = _finalize_unified_as_component
    _PATCHED = True


def remove_patches():
    global _PATCHED, _ORIGINAL_EXPORT_MOD, _ORIGINAL_FINALIZE_DATA
    if not _PATCHED:
        return

    from ._efmi_core.blender_export.blender_export import ModExporter, ObjectMergerEFMI

    ModExporter.export_mod = _ORIGINAL_EXPORT_MOD
    ObjectMergerEFMI.finalize_temp_objects_data = _ORIGINAL_FINALIZE_DATA
    _ORIGINAL_EXPORT_MOD = None
    _ORIGINAL_FINALIZE_DATA = None
    _PATCHED = False
