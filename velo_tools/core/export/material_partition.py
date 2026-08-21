"""Shared, reversible export-time material partition for EFMI and WWMI."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import json
import re
from typing import Sequence


_COMPONENT_RE = re.compile(r"component[_ -]*(\d+)", re.IGNORECASE)
_PATCHES: dict[type, tuple[object, object, str, object]] = {}


@dataclass(frozen=True)
class UsedMaterial:
    slot: int
    name: str
    key: object


@dataclass(frozen=True)
class PartitionPlan:
    mode: str
    component_id: int | None
    expected_material_names: frozenset[str]


def component_id_from_name(name: str) -> int | None:
    match = _COMPONENT_RE.search(name or "")
    return int(match.group(1)) if match else None


def classify_materials(
    object_name: str,
    materials: Sequence[UsedMaterial],
    *,
    enforce_component_match: bool = True,
) -> PartitionPlan:
    """Classify one eligible export object using its actually used materials."""
    component_id = component_id_from_name(object_name)
    if component_id is None:
        return PartitionPlan("OBJECT_NAME", None, frozenset(material.name for material in materials))

    if not enforce_component_match:
        return PartitionPlan(
            "OBJECT_NAME",
            component_id,
            frozenset(material.name for material in materials),
        )

    prefixed: dict[object, UsedMaterial] = {}
    for material in materials:
        detected = component_id_from_name(material.name)
        if detected is not None:
            prefixed[material.key] = material

    if len(prefixed) < 2:
        for material in prefixed.values():
            detected = component_id_from_name(material.name)
            if detected != component_id:
                raise ValueError(_material_error(object_name, material, component_id, detected))
        return PartitionPlan(
            "OBJECT_NAME",
            component_id,
            frozenset(material.name for material in materials),
        )

    for material in materials:
        detected = component_id_from_name(material.name)
        if detected is None:
            raise ValueError(_material_error(object_name, material, component_id, None))

    return PartitionPlan(
        "MATERIAL_NAME",
        component_id,
        frozenset(material.name for material in prefixed.values()),
    )


def _material_error(
    object_name: str,
    material: UsedMaterial,
    expected: int,
    detected: int | None,
    collection_path: str = "",
) -> str:
    location = f"集合 `{collection_path}` / " if collection_path else ""
    detected_text = "无 Component 前缀" if detected is None else f"Component {detected}"
    return (
        f"导出已拒绝：{location}对象 `{object_name}` 的材质槽 {material.slot} "
        f"(`{material.name}`) 与对象 Component {expected} 不一致；检测到 {detected_text}。\n"
        "请使用网格工具中的“为选中物体添加Component前缀”“选中物体生成材质球”"
        "或“按材质拆分”处理后重试。"
    )


def _used_materials(obj) -> list[UsedMaterial]:
    mesh = obj.data
    polygon_count = len(mesh.polygons)
    if polygon_count == 0:
        return []
    indices = array("i", [0]) * polygon_count
    mesh.polygons.foreach_get("material_index", indices)
    result = []
    for slot in sorted(set(indices)):
        material = mesh.materials[slot] if 0 <= slot < len(mesh.materials) else None
        if material is None:
            name = "<empty>"
            key = ("empty", slot)
        else:
            name = material.name
            try:
                key = material.as_pointer()
            except AttributeError:
                key = id(material)
        result.append(UsedMaterial(slot, name, key))
    return result


def _collection_path(obj) -> str:
    names = sorted(collection.name for collection in getattr(obj, "users_collection", ()))
    return " / ".join(names) or "<unknown>"


def _source_name(temp_object) -> str:
    return str(temp_object.object.get("_velo_export_source_name", temp_object.name))


def _classify_with_location(
    object_name: str,
    obj,
    used: Sequence[UsedMaterial],
    *,
    enforce_component_match: bool = True,
) -> PartitionPlan:
    try:
        return classify_materials(
            object_name,
            used,
            enforce_component_match=enforce_component_match,
        )
    except ValueError as exc:
        message = str(exc).replace(
            "导出已拒绝：对象 `",
            f"导出已拒绝：集合 `{_collection_path(obj)}` / 对象 `",
            1,
        )
        raise ValueError(message) from None


def cache_plan(
    obj,
    plan: PartitionPlan,
    component_routes: dict[int, int] | None = None,
) -> None:
    obj["_velo_material_partition_mode"] = plan.mode
    obj["_velo_material_partition_component"] = -1 if plan.component_id is None else plan.component_id
    obj["_velo_material_partition_names"] = json.dumps(
        sorted(plan.expected_material_names), ensure_ascii=False
    )
    if component_routes is not None:
        obj["_velo_material_partition_routes"] = json.dumps(
            {str(key): int(value) for key, value in component_routes.items()}
        )


def _cached_plan(obj) -> PartitionPlan | None:
    mode = obj.get("_velo_material_partition_mode")
    if not mode:
        return None
    component_id = int(obj.get("_velo_material_partition_component", -1))
    names = json.loads(str(obj.get("_velo_material_partition_names", "[]")))
    return PartitionPlan(
        str(mode),
        None if component_id < 0 else component_id,
        frozenset(str(name) for name in names),
    )


def _cached_component_routes(obj) -> dict[int, int] | None:
    raw = obj.get("_velo_material_partition_routes")
    if raw is None:
        return None
    return {
        int(key): int(value)
        for key, value in json.loads(str(raw)).items()
    }


def _solidify_target_slot(source_slot: int, offset: int, slot_count: int) -> int:
    return max(0, min(source_slot + offset, slot_count - 1))


def _validate_solidify(temp_object, used: Sequence[UsedMaterial]) -> None:
    obj = temp_object.object
    slot_count = len(obj.data.materials)
    if slot_count < 2:
        return
    for modifier in obj.modifiers:
        if modifier.type != "SOLIDIFY" or not modifier.show_viewport:
            continue
        for attr in ("material_offset", "material_offset_rim"):
            offset = int(getattr(modifier, attr, 0))
            if offset == 0:
                continue
            for material in used:
                target = _solidify_target_slot(material.slot, offset, slot_count)
                target_material = obj.data.materials[target]
                source_material = obj.data.materials[material.slot]
                if target != material.slot and target_material is not None and target_material != source_material:
                    raise ValueError(
                        f"导出已拒绝：集合 `{_collection_path(obj)}` / 对象 `{_source_name(temp_object)}` "
                        f"的 Solidify 修改器 `{modifier.name}` 通过 `{attr}` 将生成面路由到材质槽 {target} "
                        f"(`{target_material.name}`)。请处理或删除该修改器后重试。"
                    )


def prepare_cross_scene_object(obj, source_name: str, apply_modifiers: bool) -> PartitionPlan:
    """Validate one selected cross-scene source and cache its plan on a later copy."""
    temp = type("CrossSceneSource", (), {"name": source_name, "object": obj})()
    used = _used_materials(obj)
    plan = _classify_with_location(source_name, obj, used)
    if apply_modifiers:
        _validate_solidify(temp, used)
    return plan


def _shape_key_threshold(context) -> float:
    settings = getattr(context.scene, "velo_tools", None)
    return float(getattr(settings, "shapekey_cleanup_threshold", 0.0001))


def _clean_fragment_shape_keys(context, obj) -> None:
    from ...mesh.operators import _clean_unused_shape_keys

    _clean_unused_shape_keys(obj, _shape_key_threshold(context))


def _split_object_by_material(context, obj) -> list:
    import bpy
    from ...mesh.split_normals import (
        capture_split_corner_normals,
        restore_split_corner_normals,
    )

    active = context.view_layer.objects.active
    selected = list(context.selected_objects)
    previous_mode = getattr(context.object, "mode", "OBJECT") if context.object else "OBJECT"
    before = {item.as_pointer() for item in bpy.data.objects}
    normal_attribute = capture_split_corner_normals(obj.data)
    try:
        try:
            if context.object and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.select_all(action="DESELECT")
            obj.hide_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.separate(type="MATERIAL")
            bpy.ops.object.mode_set(mode="OBJECT")
        finally:
            if context.object and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            split_results = [obj]
            split_results.extend(
                item for item in bpy.data.objects
                if item.as_pointer() not in before and item.type == "MESH"
            )
            restore_split_corner_normals(split_results, normal_attribute)
        return split_results
    except Exception:
        for item in [item for item in bpy.data.objects if item.as_pointer() not in before]:
            if item.type == "MESH":
                bpy.data.meshes.remove(item.data)
        raise
    finally:
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        for item in selected:
            if item.name in bpy.data.objects:
                item.select_set(True)
        if active and active.name in bpy.data.objects:
            context.view_layer.objects.active = active
            if previous_mode != "OBJECT":
                try:
                    bpy.ops.object.mode_set(mode=previous_mode)
                except RuntimeError:
                    pass


def _material_name_for_fragment(obj) -> str:
    used = _used_materials(obj)
    if len(used) != 1:
        raise ValueError(f"导出材质拆分失败：临时对象 `{obj.name}` 未能归属到唯一材质。")
    return used[0].name


def _fragment_component_index(
    plan: PartitionPlan,
    material_name: str,
    *,
    source_component_index: int,
    component_count: int,
    component_routes: dict[int, int] | None,
) -> int:
    detected = component_id_from_name(material_name)
    if detected is None:
        raise ValueError(
            f"导出材质拆分失败：材质 `{material_name}` 没有 Component 前缀。"
        )
    if detected == plan.component_id:
        return source_component_index
    if component_routes is None:
        target = detected
    else:
        target = component_routes.get(detected)
        if target is None:
            raise ValueError(
                f"导出已拒绝：材质 `{material_name}` 指向 Component {detected}，"
                "但该 Component 不属于当前导出单元。"
            )
    if target < 0 or target >= component_count:
        raise ValueError(
            f"导出已拒绝：材质 `{material_name}` 指向 Component {detected}，"
            "但 Metadata 中不存在可接收它的 Component。"
        )
    return target


def _postprocess_merger(merger, after_split=None) -> None:
    plans = getattr(merger, "_velo_material_partition_plans", {})
    enforce_component_match = not bool(
        getattr(merger, "_velo_allow_host_material_routes", False)
    )
    rebuilt = [[] for _component in merger.components]
    source_entries = []
    postprocess_errors = []
    for component_index, component in enumerate(merger.components):
        original_entries = list(plans.get(id(component), ()))
        cleanup_objects = [entry[0] for entry in original_entries]
        component.objects = cleanup_objects
        source_entries.append((component_index, original_entries, cleanup_objects))

    for component_index, original_entries, cleanup_objects in source_entries:
        for temp_object, plan in original_entries:
            realized = _used_materials(temp_object.object)
            realized_plan = _classify_with_location(
                _source_name(temp_object),
                temp_object.object,
                realized,
                enforce_component_match=enforce_component_match,
            )
            realized_names = frozenset(item.name for item in realized)
            mode_changed = realized_plan.mode != plan.mode
            if mode_changed or not realized_names.issubset(plan.expected_material_names):
                unexpected = sorted(realized_names - plan.expected_material_names)
                detail = ", ".join(unexpected) or "材质归属发生变化"
                raise ValueError(
                    f"导出已拒绝：集合 `{_collection_path(temp_object.object)}` / 对象 `{_source_name(temp_object)}` "
                    f"在应用修改器后产生无法归属的材质面：{detail}。"
                )
            if plan.mode == "OBJECT_NAME":
                rebuilt[component_index].append(temp_object)
                continue

            fragments = _split_object_by_material(merger.context, temp_object.object)
            temp_type = type(temp_object)
            cleanup_objects.extend(
                temp_type(name=fragment.name, object=fragment)
                for fragment in fragments[1:]
            )
            for fragment in fragments:
                logical_name = _material_name_for_fragment(fragment)
                exported_fragment = temp_type(name=logical_name, object=fragment)
                target_index = _fragment_component_index(
                    plan,
                    logical_name,
                    source_component_index=component_index,
                    component_count=len(merger.components),
                    component_routes=_cached_component_routes(temp_object.object),
                )
                rebuilt[target_index].append(exported_fragment)
                if after_split is not None:
                    error = after_split(merger, exported_fragment, target_index)
                    if error:
                        postprocess_errors.append(str(error))
                _clean_fragment_shape_keys(merger.context, fragment)

    for component_index, component in enumerate(merger.components):
        component.objects = rebuilt[component_index]
        component.objects.sort(key=lambda item: item.name)
    if postprocess_errors:
        details = "\n".join(
            f"{index}. {message}"
            for index, message in enumerate(postprocess_errors, 1)
        )
        raise ValueError(
            f"检测到 {len(postprocess_errors)} 个材质片段存在越界权重；"
            f"已一次性列出，未修改源权重：\n{details}"
        )


def install(merger_cls: type, settings_attr: str, after_split=None,
            after_finalize=None) -> None:
    """Install one reversible wrapper on a vendored ObjectMerger subclass."""
    if merger_cls in _PATCHES:
        return
    original_import = merger_cls.import_objects_from_collection
    original_finalize = merger_cls.finalize_temp_objects_geometry

    def import_objects_from_collection(self):
        original_import(self)
        cfg = getattr(self.context.scene, settings_attr, None)
        self._velo_allow_host_material_routes = (
            settings_attr == "VTEF_settings"
            and (
                getattr(cfg, "mod_skeleton_type", None) == "MERGED"
                or bool(cfg.get("_unified_vg_component_export", False))
            )
        )
        if not bool(getattr(cfg, "velo_auto_split_by_material", True)):
            self._velo_material_partition_plans = None
            return
        plans = {}
        for component in self.components:
            entries = []
            for temp_object in component.objects:
                plan = (
                    None
                    if self._velo_allow_host_material_routes
                    else _cached_plan(temp_object.object)
                )
                if plan is None:
                    used = _used_materials(temp_object.object)
                    plan = _classify_with_location(
                        _source_name(temp_object),
                        temp_object.object,
                        used,
                        enforce_component_match=not self._velo_allow_host_material_routes,
                    )
                    if self.apply_modifiers:
                        _validate_solidify(temp_object, used)
                entries.append((temp_object, plan))
            plans[id(component)] = entries
        self._velo_material_partition_plans = plans

    def finalize_temp_objects_geometry(self):
        original_finalize(self)
        if getattr(self, "_velo_material_partition_plans", None) is not None:
            _postprocess_merger(self, after_split=after_split)
        if after_finalize is not None:
            after_finalize(self)

    merger_cls.import_objects_from_collection = import_objects_from_collection
    merger_cls.finalize_temp_objects_geometry = finalize_temp_objects_geometry
    _PATCHES[merger_cls] = (
        original_import,
        original_finalize,
        settings_attr,
        after_split,
        after_finalize,
    )


def remove(merger_cls: type) -> None:
    patch = _PATCHES.pop(merger_cls, None)
    if patch is None:
        return
    merger_cls.import_objects_from_collection = patch[0]
    merger_cls.finalize_temp_objects_geometry = patch[1]
