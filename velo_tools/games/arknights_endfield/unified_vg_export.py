"""Export adapters for compact authoring and three skeleton modes."""


import json
from pathlib import Path


_PATCHED = False
_ORIGINAL_EXPORT_MOD = None
_ORIGINAL_FINALIZE_DATA = None
_ORIGINAL_READ_METADATA = None
_ORIGINAL_IMPORT_LODS = None


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


def _lod_is_present(component, lod):
    explicit = getattr(lod, "present", None)
    if explicit is not None:
        return bool(explicit)
    return True


def _write_lod_presence(metadata_path, lod_object_name, matched_component_ids):
    metadata_path = Path(metadata_path)
    with metadata_path.open("r", encoding="utf-8") as handle:
        metadata = json.load(handle)

    matched_component_ids = set(matched_component_ids)
    changed = False
    for component_id, component in enumerate(metadata.get("components", [])):
        for lod in component.get("lods", []) or []:
            if str(lod.get("lod_object_name", "")) != str(lod_object_name):
                continue
            present = component_id in matched_component_ids
            if lod.get("present") != present:
                lod["present"] = present
                changed = True

    if not changed:
        return
    temp_path = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(metadata_path)


def _remove_lod_overwrite_collisions(full_object, lod_object, matched_components):
    incoming_name = str(lod_object.id)
    replacement_names = {incoming_name}

    for full_component in full_object.components:
        lod_component, _vg_map = matched_components.get(full_component, (None, None))
        if lod_component is None:
            lod_component = full_component
        incoming_vertex_count = int(lod_component.metadata.vertex_count)
        for old_lod in full_component.metadata.lods or []:
            if (
                str(old_lod.lod_object_name) == incoming_name
                or int(old_lod.vertex_count) == incoming_vertex_count
            ):
                replacement_names.add(str(old_lod.lod_object_name))

    for full_component in full_object.components:
        full_component.metadata.lods = [
            old_lod
            for old_lod in full_component.metadata.lods or []
            if str(old_lod.lod_object_name) not in replacement_names
        ]


def _import_lods_with_presence(context, cfg, full_object, lod_object, matched_components):
    if getattr(cfg, "allow_lod_overwrite", False):
        _remove_lod_overwrite_collisions(full_object, lod_object, matched_components)
    result = _ORIGINAL_IMPORT_LODS(context, cfg, full_object, lod_object, matched_components)
    from ._efmi_core.migoto_io.blender_interface.utility import resolve_path

    matched_component_ids = {
        component_id
        for component_id, component in enumerate(full_object.components)
        if component in matched_components
    }
    metadata_path = resolve_path(cfg.object_source_folder) / "Metadata.json"
    _write_lod_presence(metadata_path, lod_object.id, matched_component_ids)
    return result


def _apply_explicit_lod_presence(extracted_object, metadata_path):
    from ._efmi_core.addon.exceptions import ConfigError

    metadata_path = Path(metadata_path)
    try:
        with metadata_path.open("r", encoding="utf-8") as handle:
            raw_components = json.load(handle).get("components", [])
    except (OSError, ValueError, AttributeError) as exc:
        raise ConfigError(
            "object_source_folder",
            f"无法读取 Metadata.json 的 LOD presence 标记：{exc}",
        ) from exc

    components = extracted_object.components
    for component_id, raw_component in enumerate(raw_components):
        if component_id >= len(components):
            break
        loaded_lods = getattr(components[component_id], "lods", None) or []
        for lod_id, raw_lod in enumerate(raw_component.get("lods", []) or []):
            if "present" not in raw_lod:
                continue
            present = raw_lod["present"]
            if not isinstance(present, bool):
                raise ConfigError(
                    "object_source_folder",
                    f"Metadata.json Component {component_id} LOD {lod_id} 的 present 必须是 true 或 false。",
                )
            if lod_id >= len(loaded_lods):
                raise ConfigError(
                    "object_source_folder",
                    f"Metadata.json Component {component_id} 的 LOD presence 标记无法对应已加载数据。",
                )
            raw_name = str(raw_lod.get("lod_object_name", ""))
            loaded_name = str(loaded_lods[lod_id].lod_object_name)
            if raw_name != loaded_name:
                raise ConfigError(
                    "object_source_folder",
                    f"Metadata.json Component {component_id} 的 LOD presence 标记名称不匹配。",
                )
            loaded_lods[lod_id].present = present


def _read_metadata_with_lod_presence(metadata_path):
    extracted_object = _ORIGINAL_READ_METADATA(metadata_path)
    _apply_explicit_lod_presence(extracted_object, metadata_path)
    return extracted_object


def _metadata_map(component, attribute):
    mapping = getattr(component, attribute, None) or {}
    return {int(local): int(value) for local, value in mapping.items()}


def _local_lod_coverage(component, local_id):
    coverage = set()
    for lod in (getattr(component, "lods", None) or []):
        if not _lod_is_present(component, lod):
            continue
        remap = _metadata_map(lod, "vg_map")
        if remap.get(local_id, local_id) < 0:
            continue
        coverage.add(str(lod.lod_object_name))
    return coverage


def _lod_aware_compact_to_runtime(components, required_lods_by_identity):
    candidates = {}
    preferred = {}
    for component in components:
        if bool(getattr(component, "cpu_posed", False)):
            continue
        local_to_compact = _metadata_map(component, "vg_map")
        local_to_runtime = _metadata_map(component, "runtime_vg_map")
        if set(local_to_compact) != set(local_to_runtime):
            continue
        source_valid = bool(getattr(component, "runtime_source_valid", True))
        source_weights = getattr(component, "runtime_source_weights", None) or {}
        if isinstance(source_weights, list):
            source_weights = {local: weight for local, weight in enumerate(source_weights)}
        source_weights = {int(local): int(weight) for local, weight in source_weights.items()}
        offset = int(component.vg_offset)
        for local_id, compact_id in local_to_compact.items():
            runtime_id = local_to_runtime[local_id]
            identity = (compact_id, runtime_id)
            coverage = _local_lod_coverage(component, local_id)
            preferred.setdefault(identity, runtime_id)
            candidates.setdefault(identity, []).append(
                (
                    source_valid,
                    coverage,
                    source_weights.get(local_id, 0),
                    offset + local_id,
                )
            )

    mapping = {}
    unsupported = {}
    for identity, compact_candidates in candidates.items():
        required_lods = required_lods_by_identity.get(identity, set())
        eligible = [
            candidate
            for candidate in compact_candidates
            if candidate[0] and required_lods.issubset(candidate[1])
        ]
        if not eligible:
            best_coverage = max((candidate[1] for candidate in compact_candidates), key=len, default=set())
            unsupported[identity] = tuple(sorted(required_lods - best_coverage))
            continue
        preferred_runtime = preferred.get(identity)
        selected = max(
            eligible,
            key=lambda candidate: (
                candidate[3] == preferred_runtime,
                candidate[2],
                -candidate[3],
            ),
        )
        mapping[identity] = selected[3]
    return mapping, unsupported


def _weighted_compact_lod_requirements(metadata_components, temp_components):
    requirements = {}
    local_identities = {}
    identities_by_compact = {}
    for component_id, metadata in enumerate(metadata_components):
        local_to_compact = _metadata_map(metadata, "vg_map")
        local_to_runtime = _metadata_map(metadata, "runtime_vg_map")
        for local_id, compact_id in local_to_compact.items():
            identity = (compact_id, local_to_runtime[local_id])
            local_identities.setdefault(component_id, {})[compact_id] = identity
            identities_by_compact.setdefault(compact_id, set()).add(identity)
    for component in temp_components:
        metadata = metadata_components[component.id]
        required_lods = {
            str(lod.lod_object_name)
            for lod in (getattr(metadata, "lods", None) or [])
            if _lod_is_present(metadata, lod)
        }
        for temp_object in component.objects:
            obj = temp_object.object
            for group in obj.vertex_groups:
                name = (group.name or "").strip()
                if name.isdigit() and _has_weight(obj, group.index):
                    compact_id = int(name)
                    identity = local_identities.get(component.id, {}).get(compact_id)
                    if identity is None:
                        identities = identities_by_compact.get(compact_id, set())
                        if len(identities) == 1:
                            identity = next(iter(identities))
                    if identity is not None:
                        requirements.setdefault(identity, set()).update(required_lods)
    return requirements


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

    components = merger.extracted_object.components
    identities_by_compact = {}
    local_identities = {}
    for component_id, component in enumerate(components):
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
            identity = (compact_id, runtime_id)
            local_identities.setdefault(component_id, {})[compact_id] = identity
            identities_by_compact.setdefault(compact_id, set()).add(identity)

    required_lods_by_identity = _weighted_compact_lod_requirements(components, merger.components)
    identity_to_runtime, unsupported = _lod_aware_compact_to_runtime(
        components,
        required_lods_by_identity,
    )
    unique_compact_runtime = {
        compact_id: identity_to_runtime[next(iter(identities))]
        for compact_id, identities in identities_by_compact.items()
        if len(identities) == 1 and next(iter(identities)) in identity_to_runtime
    }
    component_maps = {}
    for component_id in range(len(components)):
        mapping = dict(unique_compact_runtime)
        for compact_id, identity in local_identities.get(component_id, {}).items():
            runtime_id = identity_to_runtime.get(identity)
            if runtime_id is not None:
                mapping[compact_id] = runtime_id
        component_maps[component_id] = mapping
    return component_maps, unsupported


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

    compact_to_runtime = None
    if full_merged:
        compact_to_runtime, unsupported = _compact_to_runtime_map(self)
        blockers = sorted(
            identity
            for identity, missing_lods in unsupported.items()
            if missing_lods
        )
        if blockers:
            preview = ", ".join(
                f"{identity[0]} (缺少 {', '.join(unsupported[identity])})"
                for identity in blockers[:12]
            )
            if len(blockers) > 12:
                preview += f", ... (+{len(blockers) - 12})"
            from ._efmi_core.addon.exceptions import ConfigError
            raise ConfigError(
                "object_source_folder",
                f"以下统一顶点组没有覆盖全部 LOD 的骨骼来源：{preview}。请重新提取 LOD 或调整权重。",
            )
    for component in self.components:
        for temp_object in component.objects:
            if intermediate:
                _translate_object_to_local(self, temp_object.object, component.id)
            else:
                _translate_object_to_runtime(
                    self,
                    temp_object.object,
                    component.id,
                    compact_to_runtime[component.id],
                )

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
    global _ORIGINAL_READ_METADATA, _ORIGINAL_IMPORT_LODS
    if _PATCHED:
        return

    from ._efmi_core.blender_export import blender_export
    from ._efmi_core.extract_frame_data import extract_frame_data

    ModExporter = blender_export.ModExporter
    ObjectMergerEFMI = blender_export.ObjectMergerEFMI

    _ORIGINAL_EXPORT_MOD = ModExporter.export_mod
    _ORIGINAL_FINALIZE_DATA = ObjectMergerEFMI.finalize_temp_objects_data
    _ORIGINAL_READ_METADATA = blender_export.read_metadata
    _ORIGINAL_IMPORT_LODS = extract_frame_data.import_lods
    ModExporter.export_mod = _export_with_three_modes
    ObjectMergerEFMI.finalize_temp_objects_data = _finalize_unified_vertex_groups
    blender_export.read_metadata = _read_metadata_with_lod_presence
    extract_frame_data.import_lods = _import_lods_with_presence
    _PATCHED = True


def remove_patches():
    global _PATCHED, _ORIGINAL_EXPORT_MOD, _ORIGINAL_FINALIZE_DATA
    global _ORIGINAL_READ_METADATA, _ORIGINAL_IMPORT_LODS
    if not _PATCHED:
        return

    from ._efmi_core.blender_export import blender_export
    from ._efmi_core.extract_frame_data import extract_frame_data

    ModExporter = blender_export.ModExporter
    ObjectMergerEFMI = blender_export.ObjectMergerEFMI

    if ModExporter.export_mod is _export_with_three_modes:
        ModExporter.export_mod = _ORIGINAL_EXPORT_MOD
    if ObjectMergerEFMI.finalize_temp_objects_data is _finalize_unified_vertex_groups:
        ObjectMergerEFMI.finalize_temp_objects_data = _ORIGINAL_FINALIZE_DATA
    if blender_export.read_metadata is _read_metadata_with_lod_presence:
        blender_export.read_metadata = _ORIGINAL_READ_METADATA
    if extract_frame_data.import_lods is _import_lods_with_presence:
        extract_frame_data.import_lods = _ORIGINAL_IMPORT_LODS
    _ORIGINAL_EXPORT_MOD = None
    _ORIGINAL_FINALIZE_DATA = None
    _ORIGINAL_READ_METADATA = None
    _ORIGINAL_IMPORT_LODS = None
    _PATCHED = False
