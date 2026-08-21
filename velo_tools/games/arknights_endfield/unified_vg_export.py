"""Export adapters for compact authoring and three skeleton modes."""


_PATCHED = False
_ORIGINAL_EXPORT_MOD = None
_ORIGINAL_FINALIZE_DATA = None


def _component_map(merger, component_id, attribute="vg_map"):
    from ._efmi_core.addon.exceptions import ConfigError

    component = merger.extracted_object.components[component_id]
    mapping = getattr(component, attribute, None) or {}
    if mapping:
        return {int(local): int(global_id) for local, global_id in mapping.items()}
    field_name = "runtime_vg_map" if attribute == "runtime_vg_map" else "vg_map"
    raise ConfigError(
        "object_source_folder",
        f"Metadata.json Component {component_id} 缺少 {field_name}。请使用当前版本重新提取模型文件夹。",
    )


def _has_weight(obj, group_index):
    for vertex in obj.data.vertices:
        for assignment in vertex.groups:
            if assignment.group == group_index and assignment.weight > 1e-6:
                return True
    return False


def _translate_numeric_groups(merger, obj, component_id, source_to_target, prefix, target_count):
    from ._efmi_core.addon.exceptions import ConfigError
    from ._efmi_core.migoto_io.blender_tools.vertex_groups import remove_unused_vertex_groups
    from ...core.mapping.algorithms import reorder_numeric_vertex_groups_first

    remove_unused_vertex_groups(merger.context, obj)
    unmatched = []
    to_remove = []
    for group in list(obj.vertex_groups):
        name = (group.name or "").strip()
        if not name.isdigit():
            continue
        target_id = source_to_target.get(int(name))
        if target_id is None:
            if _has_weight(obj, group.index):
                unmatched.append(name)
            else:
                to_remove.append(group)
            continue
        group.name = f"{prefix}{target_id}"

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

    names = {group.name for group in obj.vertex_groups}
    for target_id in range(target_count):
        name = str(target_id)
        if name not in names:
            obj.vertex_groups.new(name=name)
            names.add(name)
    reorder_numeric_vertex_groups_first(obj)


def _translate_object_to_local(merger, obj, component_id):
    local_to_compact = _component_map(merger, component_id)
    compact_to_local = {}
    for local_id, compact_id in local_to_compact.items():
        current = compact_to_local.get(compact_id)
        if current is None or local_id < current:
            compact_to_local[compact_id] = local_id
    local_count = max(local_to_compact, default=-1) + 1
    _translate_numeric_groups(
        merger,
        obj,
        component_id,
        compact_to_local,
        "__compact_to_local_",
        local_count,
    )


def _compact_to_runtime_map(merger):
    from ._efmi_core.addon.exceptions import ConfigError

    compact_to_runtime = {}
    for component_id in range(len(merger.extracted_object.components)):
        component = merger.extracted_object.components[component_id]
        if bool(getattr(component, "cpu_posed", False)):
            continue
        local_to_compact = _component_map(merger, component_id)
        local_to_runtime = _component_map(merger, component_id, "runtime_vg_map")
        if set(local_to_compact) != set(local_to_runtime):
            raise ConfigError(
                "object_source_folder",
                f"Metadata.json Component {component_id} 的 vg_map 与 runtime_vg_map 骨骼集合不一致。请重新提取。",
            )
        for local_id, compact_id in local_to_compact.items():
            runtime_id = local_to_runtime[local_id]
            previous = compact_to_runtime.setdefault(compact_id, runtime_id)
            if previous != runtime_id:
                raise ConfigError(
                    "object_source_folder",
                    f"Metadata.json 的紧凑顶点组 {compact_id} 对应多个运行时编号。请重新提取。",
                )
    return compact_to_runtime


def _translate_object_to_runtime(merger, obj, component_id, compact_to_runtime):
    runtime_count = sum(int(component.vg_count) for component in merger.extracted_object.components)
    _translate_numeric_groups(
        merger,
        obj,
        component_id,
        compact_to_runtime,
        "__compact_to_runtime_",
        runtime_count,
    )


def _finalize_unified_vertex_groups(self):
    cfg = getattr(self.context.scene, "VTEF_settings", None)
    if cfg is None:
        return _ORIGINAL_FINALIZE_DATA(self)

    intermediate = bool(cfg.get("_compact_vg_component_export", False))
    full_merged = bool(cfg.get("_compact_vg_merged_skeleton_export", False))
    if not intermediate and not full_merged:
        return _ORIGINAL_FINALIZE_DATA(self)

    compact_to_runtime = _compact_to_runtime_map(self) if full_merged else None
    for component in self.components:
        for temp_object in component.objects:
            if intermediate:
                _translate_object_to_local(self, temp_object.object, component.id)
            else:
                _translate_object_to_runtime(self, temp_object.object, component.id, compact_to_runtime)

    if full_merged:
        return _ORIGINAL_FINALIZE_DATA(self)

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
    marker = "_compact_vg_component_export" if intermediate else "_compact_vg_merged_skeleton_export"
    cfg[marker] = True
    cfg.mod_skeleton_type = "COMPONENT" if intermediate else "MERGED"
    try:
        return _ORIGINAL_EXPORT_MOD(self, *args, **kwargs)
    finally:
        cfg.mod_skeleton_type = requested_mode
        try:
            del cfg[marker]
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
    ObjectMergerEFMI.finalize_temp_objects_data = _finalize_unified_vertex_groups
    _PATCHED = True


def remove_patches():
    global _PATCHED, _ORIGINAL_EXPORT_MOD, _ORIGINAL_FINALIZE_DATA
    if not _PATCHED:
        return

    from ._efmi_core.blender_export.blender_export import ModExporter, ObjectMergerEFMI

    if ModExporter.export_mod is _export_with_three_modes:
        ModExporter.export_mod = _ORIGINAL_EXPORT_MOD
    if ObjectMergerEFMI.finalize_temp_objects_data is _finalize_unified_vertex_groups:
        ObjectMergerEFMI.finalize_temp_objects_data = _ORIGINAL_FINALIZE_DATA
    _ORIGINAL_EXPORT_MOD = None
    _ORIGINAL_FINALIZE_DATA = None
    _PATCHED = False
