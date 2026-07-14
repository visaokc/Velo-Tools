"""Build WWMI cross-scene geometry buffers without child exports."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .selection import ExportSelection, SelectedObject


@dataclass(frozen=True)
class ExportUnitPlan:
    identity: str
    kind: str
    ib_hash: str
    suffix: str
    resource_domain: str
    component_map: Tuple[Tuple[int, int], ...]
    selected: Tuple[SelectedObject, ...]
    empty_local_components: Tuple[int, ...]
    manifest_entry: Mapping[str, Any]

    @property
    def global_components(self) -> Tuple[int, ...]:
        return tuple(global_id for _local_id, global_id in self.component_map)


@dataclass
class ExportUnit:
    plan: ExportUnitPlan
    extracted_object: Any
    merged_object: Any
    buffers: Dict[str, Any]


class _CfgProxy:
    def __init__(self, cfg: Any, **overrides: Any):
        self._cfg = cfg
        self._overrides = overrides

    def __getattr__(self, name: str) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._cfg, name)


def _position_hash(x: float, y: float, z: float) -> int:
    value = 2166136261
    for coordinate in (x, y, z):
        encoded = (int(round(coordinate * 100)) + 1000000) & 0xffffffff
        value = ((value ^ encoded) * 16777619) & 0xffffffff
    return value


def _punch_position_hole(obj: Any, fraction: int) -> None:
    import bmesh
    import bpy

    try:
        bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    mesh = bmesh.from_edit_mesh(obj.data)
    faces = list(mesh.faces)
    if len(faces) >= 10:
        removed = [
            face for face in faces
            if _position_hash(*face.calc_center_median()) % 100 < fraction
        ]
        if len(removed) >= len(faces):
            removed = removed[:len(faces) // 2]
        bmesh.ops.delete(mesh, geom=removed, context="FACES")
        bmesh.update_edit_mesh(obj.data, destructive=True)
    bpy.ops.object.mode_set(mode="OBJECT")


def _bake_shapekey_mix(obj: Any) -> None:
    shape_keys = obj.data.shape_keys
    if not shape_keys:
        return
    mixed = obj.shape_key_add(name="xs_mix", from_mix=True)
    coordinates = [0.0] * (len(obj.data.vertices) * 3)
    mixed.data.foreach_get("co", coordinates)
    for key_block in list(shape_keys.key_blocks):
        obj.shape_key_remove(key_block)
    obj.data.vertices.foreach_set("co", coordinates)
    obj.data.update()


def _rename_and_compact_vertex_groups(obj: Any,
                                      renames: Mapping[str, str]) -> None:
    renames = dict(renames or {})
    if not renames:
        return
    try:
        from .....core.mapping import algorithms as mapping_algorithms
    except Exception:
        from . import vg_translate
        by_name = {group.name: group for group in obj.vertex_groups}
        for old, new in renames.items():
            group = by_name.get(old)
            if group is not None:
                group.name = vg_translate.TMP_PREFIX + new
        for group in obj.vertex_groups:
            if group.name.startswith(vg_translate.TMP_PREFIX):
                group.name = group.name[len(vg_translate.TMP_PREFIX):]
        return
    mapping_algorithms.rename_and_reorder(
        obj,
        renames,
        sort_renamed_numerically=False,
        untouched_to_end=False,
    )


def _weighted_vertex_groups(obj: Any) -> list[Tuple[str, bool]]:
    weighted = set()
    for vertex in obj.data.vertices:
        for membership in vertex.groups:
            if membership.weight > 1e-6:
                weighted.add(membership.group)
    return [(group.name, group.index in weighted) for group in obj.vertex_groups]


def _translate_host_vertex_groups(obj: Any, split: Mapping[str, Any],
                                  ib_hash: str) -> None:
    from . import vg_translate

    check = split.get("host_vg_selfcheck") or {}
    remap = split.get("host_vg_remap")
    renames, drops, strays = vg_translate.plan_host_vg_translation(
        _weighted_vertex_groups(obj), remap, check.get("host_vg_count"))
    if strays:
        usable = (sorted(remap.keys(), key=int) if remap
                  else ["0..%d" % (int(check.get("host_vg_count") or 1) - 1)])
        raise RuntimeError(
            "own-buffer 部件 %s（IB %s）存在带权重的顶点组 %s 不在 host 骨表内——"
            "该饰品的权重必须刷在 host 既有骨上（本部件可用顶点组：%s）。"
            "请把越界权重转移到可用顶点组或刷零后再导出。"
            % (split.get("split_object"), ib_hash, strays, usable))
    by_name = {group.name: group for group in obj.vertex_groups}
    for old in drops:
        obj.vertex_groups.remove(by_name[old])
    _rename_and_compact_vertex_groups(obj, renames)


def _prepare_own_buffer_vertex_groups(obj: Any, split: Mapping[str, Any],
                                      component_vg_map: Mapping[str, int],
                                      ib_hash: str) -> None:
    from . import vg_translate

    check = split.get("host_vg_selfcheck") or {}
    renames, skip_host, strays = vg_translate.plan_own_buffer_vg_normalization(
        _weighted_vertex_groups(obj),
        component_vg_map,
        split.get("host_vg_remap"),
        check.get("host_vg_count"),
    )
    if strays:
        raise RuntimeError(
            "own-buffer 部件 %s（IB %s）的顶点组 %s 权重越界——它们既不属于 host 骨表，"
            "也无法通过该部件的 vg_map 翻译成本部件骨。请把这些权重转回本部件的骨，"
            "或刷零后再导出。"
            % (split.get("split_object"), ib_hash, strays))
    _rename_and_compact_vertex_groups(obj, renames)
    if not skip_host:
        _translate_host_vertex_groups(obj, split, ib_hash)


def _prepare_editable_vertex_groups(
        obj: Any,
        merged_component_vg_map: Mapping[str, int],
        source_component_vg_map: Mapping[str, int],
        merged_component: int,
        ib_hash: str,
        export_skeleton_type: str,
) -> None:
    from . import vg_translate

    renames, strays = vg_translate.plan_editable_vg_normalization(
        _weighted_vertex_groups(obj),
        merged_component_vg_map,
        source_component_vg_map,
        target_scope=export_skeleton_type,
    )
    if strays:
        raise RuntimeError(
            "editable IB %s merged Component %s 的顶点组 %s 权重越界——它们既不属于 "
            "merged/root component palette，也无法翻译到 editable source palette。"
            "请把这些权重转回该 editable IB 的本部件骨，或刷零后再导出。"
            % (ib_hash, merged_component, strays))
    if str(export_skeleton_type).upper() == "COMPONENT":
        from ..per_from_merged import _apply_component_remap_preserving_vertex_order
        _apply_component_remap_preserving_vertex_order(
            obj, renames, source_component_vg_map)
    else:
        _rename_and_compact_vertex_groups(obj, renames)


def _normalized_component_map(entry: Mapping[str, Any]) -> Tuple[Tuple[int, int], ...]:
    return tuple(sorted(
        (int(local_id), int(global_id))
        for local_id, global_id in (entry.get("component_map") or {}).items()
    ))


def plan_export_units(
        selection: ExportSelection,
        manifest: Mapping[str, Any],
) -> Tuple[ExportUnitPlan, ...]:
    """Allocate stable final identities before any geometry is encoded."""
    base = manifest["base"]
    base_count = int(base["component_count"])
    runtime = tuple(manifest.get("runtime_ibs") or ())

    body_selected = tuple(
        item for item in selection.objects
        if item.component_id < base_count
    )
    body_map = tuple((component_id, component_id)
                     for component_id in range(base_count))
    body_present = {item.component_id for item in body_selected}
    plans = [ExportUnitPlan(
        identity="body",
        kind="body",
        ib_hash=str(base.get("vb0_hash") or ""),
        suffix="_ib0",
        resource_domain="ib0",
        component_map=body_map,
        selected=body_selected,
        empty_local_components=tuple(
            component_id for component_id in range(base_count)
            if component_id not in body_present),
        manifest_entry=base,
    )]

    for entry in runtime:
        if entry.get("kind") != "fold":
            continue
        component_map = _normalized_component_map(entry)
        selected_globals = selection.selected_component_ids
        plans.append(ExportUnitPlan(
            identity=f"fold:{entry['ib_hash']}",
            kind="fold",
            ib_hash=str(entry["ib_hash"]),
            suffix="_ib0",
            resource_domain="ib0",
            component_map=component_map,
            selected=tuple(
                item for item in body_selected
                if item.component_id in {value for _key, value in component_map}),
            empty_local_components=tuple(
                local_id for local_id, global_id in component_map
                if global_id not in selected_globals),
            manifest_entry=entry,
        ))

    scoped_entries = [entry for entry in runtime
                      if entry.get("kind") == "own_buffer"]
    scoped_entries.extend(entry for entry in runtime
                          if entry.get("kind") == "editable")
    for index, entry in enumerate(scoped_entries, start=1):
        component_map = _normalized_component_map(entry)
        if entry.get("kind") == "own_buffer":
            split_name = str((entry.get("split_route") or {}).get("split_object") or "")
            item = selection.by_name(split_name)
            selected = (item,) if item is not None else ()
        else:
            global_ids = {value for _key, value in component_map}
            selected = tuple(item for item in selection.objects
                             if item.component_id in global_ids)
        selected_globals = {item.component_id for item in selected}
        plans.append(ExportUnitPlan(
            identity=f"{entry['kind']}:{entry['ib_hash']}",
            kind=str(entry["kind"]),
            ib_hash=str(entry["ib_hash"]),
            suffix=f"_ib{index}",
            resource_domain=f"ib{index}",
            component_map=component_map,
            selected=selected,
            empty_local_components=tuple(
                local_id for local_id, global_id in component_map
                if global_id not in selected_globals),
            manifest_entry=entry,
        ))
    return tuple(plans)


def _empty_merged_object(extracted_object: Any, skeleton_type: Any) -> Any:
    from ..._wwmi_core.blender_export.object_merger import (
        MergedObject,
        MergedObjectComponent,
        MergedObjectShapeKeys,
    )
    return MergedObject(
        object=None,
        components=[MergedObjectComponent(objects=[], index_count=0)
                    for _component in extracted_object.components],
        shapekeys=MergedObjectShapeKeys(),
        skeleton_type=skeleton_type,
        vertex_count=0,
        index_count=0,
        vg_count=0,
        blend_remap_count=0,
    )


def _copy_input_object(source: Any, collection: Any, name: str) -> Any:
    obj = source.copy()
    obj.data = source.data.copy()
    obj.name = name
    collection.objects.link(obj)
    return obj


def _local_name(local_id: int, ordinal: int) -> str:
    return f"Component {local_id}" if ordinal == 0 else f"Component {local_id}.{ordinal:03d}"


def _prepare_inputs(context: Any, cfg: Any, plan: ExportUnitPlan,
                    collection: Any, metadata: Mapping[str, Any],
                    hole: bool, hole_frac: int) -> Tuple[list[Any], Dict[str, str]]:
    inputs = []
    labels = {}
    skeleton_mode = ("COMPONENT" if cfg.mod_skeleton_type == "COMPONENT_FROM_MERGED"
                     else cfg.mod_skeleton_type)
    per_local_count: Dict[int, int] = {}
    global_to_local = {global_id: local_id
                       for local_id, global_id in plan.component_map}
    for selected in plan.selected:
        local_id = global_to_local.get(selected.component_id)
        if local_id is None:
            continue
        ordinal = per_local_count.get(local_id, 0)
        per_local_count[local_id] = ordinal + 1
        name = _local_name(local_id, ordinal)
        obj = _copy_input_object(selected.object, collection, name)
        labels[obj.name] = selected.name

        if plan.kind == "body" and cfg.mod_skeleton_type == "COMPONENT_FROM_MERGED":
            from .. import per_from_merged
            component_meta = (metadata.get("components") or [])[local_id]
            settings = getattr(context.scene, "velo_endfield", None)
            profile = getattr(settings, "mmd_profile", None) if settings else None
            stray = per_from_merged._prepare_object_for_component_export(
                obj, component_meta.get("vg_map") or {}, profile)
            if stray:
                raise RuntimeError(
                    "跨场景 Per-Component(from Merged)：物体 %s 的顶点组 %s 权重越界。"
                    % (selected.name, stray))
        elif plan.kind == "own_buffer":
            split = plan.manifest_entry.get("split_route") or {}
            _bake_shapekey_mix(obj)
            base_component = int(split["base_component"])
            base_metadata = plan.manifest_entry.get("aggregate_metadata")
            if base_metadata is None:
                base_metadata = metadata
            component_meta = (base_metadata.get("components") or [])[base_component]
            _prepare_own_buffer_vertex_groups(
                obj, split, component_meta.get("vg_map") or {}, plan.ib_hash)
        elif plan.kind == "editable":
            base_metadata = plan.manifest_entry.get("aggregate_metadata")
            if base_metadata is None:
                raise RuntimeError("editable ExportUnit is missing aggregate metadata")
            merged_component = selected.component_id
            source_component = local_id
            source_meta = (metadata.get("components") or [])[source_component]
            base_components = base_metadata.get("components") or []
            if merged_component < len(base_components):
                merged_vg_map = base_components[merged_component].get("vg_map") or {}
            else:
                vg_offset = int(plan.manifest_entry.get("vg_base_offset") or 0)
                merged_vg_map = {
                    key: int(value) + vg_offset
                    for key, value in (source_meta.get("vg_map") or {}).items()
                }
            _prepare_editable_vertex_groups(
                obj,
                merged_vg_map,
                source_meta.get("vg_map") or {},
                merged_component, plan.ib_hash, skeleton_mode)

        if hole:
            _punch_position_hole(obj, hole_frac)
        inputs.append(obj)
    return inputs, labels


def _build_geometry_unit(context: Any, cfg: Any, plan: ExportUnitPlan,
                         metadata: Mapping[str, Any], excluded_buffers: Iterable[str],
                         hole: bool, hole_frac: int) -> ExportUnit:
    import bpy

    from ..._wwmi_core.blender_export import blender_export as blender_export_module
    from ..._wwmi_core.blender_export.object_merger import SkeletonType
    from ..._wwmi_core.extract_frame_data.metadata_format import ExtractedObject, from_dict
    from ..._wwmi_core.migoto_io.blender_interface.mesh import remove_mesh
    from ..lod import export_hook as lod_export_hook

    extracted = from_dict(ExtractedObject, dict(metadata))
    skeleton_mode = ("COMPONENT" if cfg.mod_skeleton_type == "COMPONENT_FROM_MERGED"
                     else cfg.mod_skeleton_type)
    skeleton_type = (SkeletonType.Merged if skeleton_mode == "MERGED"
                     else SkeletonType.PerComponent)
    setattr(extracted, "velo_lods", [])
    setattr(extracted, "velo_lod_excluded_objects", [])
    setattr(extracted, "velo_draws", [[] for _component in extracted.components])
    if not plan.selected:
        return ExportUnit(
            plan=plan,
            extracted_object=extracted,
            merged_object=_empty_merged_object(extracted, skeleton_type),
            buffers={},
        )

    collection = bpy.data.collections.new("xs_unit_" + plan.resource_domain)
    bpy.context.scene.collection.children.link(collection)
    inputs = []
    merged_object = None
    merged_mesh = None
    try:
        if (cfg.mod_skeleton_type == "COMPONENT_FROM_MERGED"
                and plan.kind in {"body", "editable"}):
            from ..per_from_merged import batch_vertex_group_sort_context
            sort_context = batch_vertex_group_sort_context()
        else:
            from contextlib import nullcontext
            sort_context = nullcontext()
        with sort_context:
            inputs, labels = _prepare_inputs(
                context, cfg, plan, collection, metadata, hole, hole_frac)
        from ..._wwmi_core.blender_export.blender_export import ObjectMergerWWMI
        merger = ObjectMergerWWMI(
            extracted_object=extracted,
            ignore_nested_collections=True,
            ignore_hidden_collections=False,
            ignore_hidden_objects=False,
            ignore_muted_shape_keys=plan.manifest_entry.get(
                "ignore_muted_shape_keys", False),
            apply_modifiers=plan.manifest_entry.get("apply_modifiers", False),
            context=context,
            collection=collection,
            skeleton_type=skeleton_type,
            fill_missing_mesh_data=bool(cfg.fill_missing_mesh_data),
            add_missing_vertex_groups=bool(cfg.add_missing_vertex_groups),
        )
        merged_object = merger.merged_object
        merged_mesh = merged_object.object.data
        for component in merged_object.components:
            for temp_object in component.objects:
                temp_object.name = labels.get(temp_object.name, temp_object.name)

        proxy_cfg = _CfgProxy(
            cfg,
            component_collection=collection,
            mod_skeleton_type=skeleton_mode,
        )
        adapter = SimpleNamespace(
            context=context,
            cfg=proxy_cfg,
            extracted_object=extracted,
            merged_object=merged_object,
            excluded_buffers=list(excluded_buffers),
        )
        core_build = (lod_export_hook._ORIG_BUILD_DATA_BUFFERS
                      or blender_export_module.ModExporter.build_data_buffers)
        core_build(adapter)
        lod_export_hook._snapshot_lod_export_state(adapter)
        lod_export_hook.prepare_lod_export_memory(
            adapter,
            metadata=dict(metadata),
            excluded_names=(
                [split.get("split_object")
                 for split in plan.manifest_entry.get("splits") or []
                 if split.get("split_object")]
                if plan.kind == "body" else ()),
        )
        buffers = dict(adapter.buffers)
        merged_object.object = None
        return ExportUnit(plan, extracted, merged_object, buffers)
    finally:
        if merged_mesh is not None:
            try:
                remove_mesh(merged_mesh)
            except Exception:
                pass
        for obj in inputs:
            try:
                if getattr(obj, "data", None) is not None:
                    remove_mesh(obj.data)
            except Exception:
                pass
        try:
            bpy.data.collections.remove(collection)
        except Exception:
            pass


def build_export_units(
        context: Any,
        cfg: Any,
        selection: ExportSelection,
        root: Any,
        *,
        excluded_buffers: Iterable[str] = (),
        hole: bool = False,
        hole_frac: int = 2,
) -> Tuple[ExportUnit, ...]:
    """Encode body/own/editable units; fold routes reuse the body resource domain."""
    manifest = root.manifest
    plans = plan_export_units(selection, manifest)
    aggregate_metadata = root.metadata
    units = []
    for plan in plans:
        if plan.kind == "fold":
            continue
        entry = dict(plan.manifest_entry)
        entry["aggregate_metadata"] = aggregate_metadata
        entry["ignore_muted_shape_keys"] = selection.ignore_muted_shape_keys
        entry["apply_modifiers"] = selection.apply_modifiers
        plan = ExportUnitPlan(
            identity=plan.identity,
            kind=plan.kind,
            ib_hash=plan.ib_hash,
            suffix=plan.suffix,
            resource_domain=plan.resource_domain,
            component_map=plan.component_map,
            selected=plan.selected,
            empty_local_components=plan.empty_local_components,
            manifest_entry=entry,
        )
        metadata = (aggregate_metadata if plan.kind == "body"
                    else entry["runtime_layout"])
        units.append(_build_geometry_unit(
            context, cfg, plan, metadata, excluded_buffers, hole, hole_frac))
    return tuple(units)
