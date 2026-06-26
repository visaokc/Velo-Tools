from __future__ import annotations

from dataclasses import dataclass, field

import bpy
from mathutils import Vector, kdtree

from ..core.mapping.filters import is_special_vg_name
from . import rwt_bridge as _rwt


@dataclass
class WeightTransferReport:
    engine: str = ""
    target_group: str = ""
    created_group: bool = False
    created_bone: bool = False
    matched_count: int = 0
    rescued_components: int = 0
    rescued_vertices: int = 0
    zero_anchor_components: int = 0
    zero_anchor_vertices: int = 0
    evidence_blocked_components: int = 0
    evidence_blocked_vertices: int = 0
    inpaint_fallback: str = ""
    smoothed: bool = False
    smoothing_skipped: bool = False
    limited: bool = False
    normalized: bool = False
    limit_skipped_same_object: bool = False
    normalize_skipped_same_object: bool = False
    donors: list[str] = field(default_factory=list)


@dataclass
class TargetGroupResolution:
    name: str
    should_create: bool
    reason: str = ""
    candidates: list[str] = field(default_factory=list)
    claimed: bool = False


def _object_label(obj):
    if obj is None:
        return "<未选择>"
    return f"{obj.name} ({getattr(obj, 'type', 'UNKNOWN')})"


def _clean_name(value):
    return (value or "").strip()


def _unique_names(*names):
    result = []
    seen = set()
    for name in names:
        cleaned = _clean_name(name)
        if not cleaned or cleaned in seen:
            continue
        result.append(cleaned)
        seen.add(cleaned)
    return result


def _mmd_profile(context):
    scene = getattr(context, "scene", None)
    ef = getattr(scene, "velo_endfield", None) if scene is not None else None
    profile = getattr(ef, "mmd_profile", None) if ef is not None else None
    return ef, profile


def _row_names(row):
    return {
        "mmd": _clean_name(getattr(row, "mmd_name", "")),
        "current": _clean_name(getattr(row, "current_source_name", "")),
        "unified": _clean_name(getattr(row, "unified_name", "")),
    }


def _mmd_direction(ef, source, target):
    if ef is None:
        return "unknown"
    mmd_source = getattr(ef, "mmd_source_object", None)
    mmd_target = getattr(ef, "mmd_target_object", None)
    if source is mmd_source and target is mmd_target:
        return "mmd_to_unified"
    if source is mmd_target and target is mmd_source:
        return "unified_to_mmd"
    return "unknown"


def _mmd_candidate_names(names, source_name, direction):
    if direction == "mmd_to_unified":
        return _unique_names(names["unified"], names["current"], names["mmd"])
    if direction == "unified_to_mmd":
        return _unique_names(names["current"], names["mmd"], names["unified"])
    if source_name == names["unified"]:
        return _unique_names(names["current"], names["mmd"], names["unified"])
    return _unique_names(names["unified"], names["current"], names["mmd"])


def _resolve_mmd_claimed_group(context, settings, source_name):
    target = settings.target_object
    ef, profile = _mmd_profile(context)
    if target is None or profile is None:
        return None
    direction = _mmd_direction(ef, settings.source_object, target)
    for row in getattr(profile, "rows", ()):
        names = _row_names(row)
        if source_name not in {names["mmd"], names["current"], names["unified"]}:
            continue
        candidates = _mmd_candidate_names(names, source_name, direction)
        if not candidates:
            continue
        for name in candidates:
            if target.vertex_groups.get(name) is not None:
                return TargetGroupResolution(
                    name=name,
                    should_create=False,
                    reason=(
                        "MMD 映射表认领: "
                        f"source='{source_name}', mmd='{names['mmd']}', "
                        f"current='{names['current']}', unified='{names['unified']}', "
                        f"direction={direction}; 命中目标已有组 '{name}'"
                    ),
                    candidates=candidates,
                    claimed=True,
                )
        return TargetGroupResolution(
            name=candidates[0],
            should_create=True,
            reason=(
                "MMD 映射表认领: "
                f"source='{source_name}', mmd='{names['mmd']}', "
                f"current='{names['current']}', unified='{names['unified']}', "
                f"direction={direction}; 目标缺少候选组，按映射候选创建 '{candidates[0]}'"
            ),
            candidates=candidates,
            claimed=True,
        )
    return None


def validate_source_group(source, group_name):
    if source is None or source.type != 'MESH':
        raise ValueError(f"来源网格无效: {_object_label(source)}")
    group_name = _clean_name(group_name)
    if not group_name:
        raise ValueError(f"来源网格 '{source.name}' 未选择来源顶点组")
    group = source.vertex_groups.get(group_name)
    if group is None:
        raise ValueError(f"来源网格 '{source.name}' 不存在顶点组 '{group_name}'")
    if group.lock_weight:
        raise ValueError(f"来源顶点组 '{source.name}/{group.name}' 已锁定，不能作为供体")
    if is_special_vg_name(group.name):
        raise ValueError(f"来源顶点组 '{source.name}/{group.name}' 是 Velo 特殊组，不能作为供体")
    return group


def suggest_target_group_name(context, settings):
    source_name = (settings.source_group or "").strip()
    if not source_name:
        return ""
    mapped = _resolve_mmd_claimed_group(context, settings, source_name)
    if mapped is not None:
        return mapped.name
    return source_name


def resolve_target_group(context, settings):
    source_name = _clean_name(settings.source_group)
    if not source_name:
        raise ValueError("未选择来源顶点组，无法解析承接组")
    target = settings.target_object
    if target is None or target.type != 'MESH':
        raise ValueError(f"目标网格无效: {_object_label(target)}")

    mapped = _resolve_mmd_claimed_group(context, settings, source_name)
    if mapped:
        return mapped

    requested = _clean_name(settings.target_group_name)
    if getattr(settings, "manual_target_group_name", False) and requested:
        return TargetGroupResolution(
            name=requested,
            should_create=target.vertex_groups.get(requested) is None,
            reason=f"手动承接组名: '{requested}'",
            candidates=[requested],
            claimed=False,
        )

    if target.vertex_groups.get(source_name) is not None:
        return TargetGroupResolution(
            name=source_name,
            should_create=False,
            reason=f"未被 MMD 映射表认领；目标网格 '{target.name}' 已有同名组 '{source_name}'",
            candidates=[source_name],
            claimed=False,
        )

    return TargetGroupResolution(
        name=source_name,
        should_create=True,
        reason=f"未被 MMD 映射表认领；按来源组名创建新承接组 '{source_name}'",
        candidates=[source_name],
        claimed=False,
    )


def resolve_target_group_name(context, settings):
    resolution = resolve_target_group(context, settings)
    return resolution.name, resolution.should_create


def ensure_target_group(context, settings, resolution, should_create=None):
    target = settings.target_object
    if isinstance(resolution, TargetGroupResolution):
        group_name = resolution.name
        should_create = resolution.should_create
        reason = resolution.reason
        candidates = resolution.candidates
    else:
        group_name = _clean_name(resolution)
        reason = "旧式承接组解析"
        candidates = [group_name]
    if should_create is None:
        should_create = False
    if target is None or target.type != 'MESH':
        raise ValueError(f"目标网格无效: {_object_label(target)}")
    if not group_name:
        raise ValueError(f"承接组名为空；解析来源: {reason}")
    group = target.vertex_groups.get(group_name)
    if group is not None:
        if group.lock_weight:
            extra = f"；候选: {', '.join(candidates)}" if candidates else ""
            raise ValueError(
                f"承接组 '{target.name}/{group.name}' 已存在且已锁定，不能修改；"
                f"解析来源: {reason}{extra}。请解锁该顶点组、关闭手动承接组覆盖，或改用未锁定的承接组。"
            )
        return group, False, False
    if not should_create:
        raise ValueError(f"承接组 '{group_name}' 不存在且当前解析不允许创建；解析来源: {reason}")
    group = target.vertex_groups.new(name=group_name)
    created_group = target.vertex_groups.get(group_name)
    if created_group is None:
        raise RuntimeError(f"已请求创建承接组 '{group_name}'，但目标网格 '{target.name}' 创建后仍无法找到该组")
    return created_group, True, False


def ensure_group_bone(context, settings, obj, group_name):
    armature = resolve_armature_object(settings)
    if armature is None or not getattr(settings, "create_bone_if_missing", False):
        return False
    head, tail = group_bone_placement(obj, group_name, armature)
    return create_armature_bone(context, armature, group_name, head=head, tail=tail)


def resolve_armature_object(settings):
    armature = getattr(settings, "armature_object", None)
    if armature is not None and getattr(armature, "type", None) == 'ARMATURE':
        return armature
    target = getattr(settings, "target_object", None)
    if target is None:
        return None
    try:
        found = target.find_armature()
        if found is not None and found.type == 'ARMATURE':
            return found
    except Exception:
        pass
    parent = getattr(target, "parent", None)
    if parent is not None and getattr(parent, "type", None) == 'ARMATURE':
        return parent
    for modifier in getattr(target, "modifiers", ()):
        if getattr(modifier, "type", None) != 'ARMATURE':
            continue
        obj = getattr(modifier, "object", None)
        if obj is not None and getattr(obj, "type", None) == 'ARMATURE':
            return obj
    return None


def group_weighted_centroid_world(obj, group_name, *, threshold=0.00001):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return None, None
    group = obj.vertex_groups.get(group_name)
    if group is None:
        return obj.matrix_world.translation.copy(), None
    weights = read_group_weights(obj, group)
    if len(weights) != len(obj.data.vertices):
        return obj.matrix_world.translation.copy(), None

    total = 0.0
    position_sum = Vector((0.0, 0.0, 0.0))
    normal_sum = Vector((0.0, 0.0, 0.0))
    normal_matrix = obj.matrix_world.to_3x3()
    for vertex, raw_weight in zip(obj.data.vertices, weights):
        weight = float(raw_weight)
        if weight <= threshold:
            continue
        position_sum += (obj.matrix_world @ vertex.co) * weight
        normal = normal_matrix @ vertex.normal
        if normal.length_squared > 1e-12:
            normal_sum += normal.normalized() * weight
        total += weight

    if total <= threshold:
        return obj.matrix_world.translation.copy(), None

    centroid = position_sum / total
    direction = None
    if normal_sum.length_squared > 1e-12:
        direction = normal_sum.normalized()
    return centroid, direction


def group_bone_placement(obj, group_name, armature, *, bone_length=0.05):
    centroid_world, direction_world = group_weighted_centroid_world(obj, group_name)
    head = armature.matrix_world.inverted() @ centroid_world
    if direction_world is None:
        direction_local = Vector((0.0, 1.0, 0.0))
    else:
        direction_local = armature.matrix_world.inverted().to_3x3() @ direction_world
        if direction_local.length_squared <= 1e-12:
            direction_local = Vector((0.0, 1.0, 0.0))
        else:
            direction_local.normalize()
    tail = head + (direction_local * bone_length)
    if (tail - head).length_squared <= 1e-12:
        tail = head + Vector((0.0, bone_length, 0.0))
    return head, tail


def create_armature_bone(context, armature, bone_name, *, head=None, tail=None):
    if armature is None or armature.type != 'ARMATURE' or not bone_name:
        return False
    if armature.data.bones.get(bone_name) is not None:
        return False
    old_active = context.view_layer.objects.active
    old_mode = getattr(context, "mode", 'OBJECT')
    old_selection = list(context.selected_objects)
    try:
        if old_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in context.selected_objects:
            obj.select_set(False)
        armature.select_set(True)
        context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode='EDIT')
        bone = armature.data.edit_bones.new(bone_name)
        bone.head = head if head is not None else Vector((0.0, 0.0, 0.0))
        bone.tail = tail if tail is not None else (bone.head + Vector((0.0, 0.05, 0.0)))
        bone.use_deform = True
        return True
    finally:
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in old_selection:
            try:
                obj.select_set(True)
            except Exception:
                pass
        if old_active is not None:
            context.view_layer.objects.active = old_active
        if old_mode != 'OBJECT' and old_active is not None:
            try:
                bpy.ops.object.mode_set(mode=old_mode)
            except Exception:
                pass


def remove_armature_bone(context, armature, bone_name):
    if armature is None or armature.type != 'ARMATURE' or not bone_name:
        return False
    if armature.data.bones.get(bone_name) is None:
        return False
    old_active = context.view_layer.objects.active
    old_mode = getattr(context, "mode", 'OBJECT')
    old_selection = list(context.selected_objects)
    try:
        if old_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        for obj in context.selected_objects:
            obj.select_set(False)
        armature.select_set(True)
        context.view_layer.objects.active = armature
        bpy.ops.object.mode_set(mode='EDIT')
        bone = armature.data.edit_bones.get(bone_name)
        if bone is None:
            return False
        armature.data.edit_bones.remove(bone)
        return True
    finally:
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except Exception:
            pass
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in old_selection:
            try:
                obj.select_set(True)
            except Exception:
                pass
        if old_active is not None:
            context.view_layer.objects.active = old_active
        if old_mode != 'OBJECT' and old_active is not None:
            try:
                bpy.ops.object.mode_set(mode=old_mode)
            except Exception:
                pass


def clear_vertex_group(obj, group):
    if group.lock_weight:
        raise ValueError(f"承接组 '{obj.name}/{group.name}' 已锁定，不能清空")
    indices = list(range(len(obj.data.vertices)))
    for start in range(0, len(indices), 10000):
        group.remove(indices[start:start + 10000])


def write_group_weights(obj, group, weights, *, threshold=0.00001):
    if group.lock_weight:
        raise ValueError(f"承接组 '{obj.name}/{group.name}' 已锁定，不能写入")
    clear_vertex_group(obj, group)
    for vertex_index, weight in enumerate(weights):
        value = max(0.0, min(float(weight), 1.0))
        if value >= threshold:
            group.add([vertex_index], value, 'REPLACE')


def _weight_values(weights):
    if getattr(weights, "ndim", 1) > 1:
        return weights[:, 0]
    return weights


def transfer_with_robust(context, settings, source_group_name):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"Robust 引擎不可用: {error}")
    source = settings.source_object
    target = settings.target_object
    depsgraph = context.evaluated_depsgraph_get()
    source_eval = source.evaluated_get(depsgraph) if settings.use_deformed_source else source
    target_eval = target.evaluated_get(depsgraph) if settings.use_deformed_target else target
    source_verts, source_faces, source_normals = _rwt.get_obj_arrs_world(source_eval)
    target_verts, target_faces, target_normals = _rwt.get_obj_arrs_world(target_eval)
    if len(target_verts) != len(target.data.vertices):
        raise ValueError("目标变形结果拓扑与原网格不一致")
    if len(source_faces) == 0 or len(target_faces) == 0:
        raise ValueError("来源或目标没有可用三角面")
    source_weights = _rwt.get_group_arr(source_eval, source_group_name)
    source_weight_count = int(_rwt.np_module().count_nonzero(source_weights > 0.00001))
    if source_weight_count <= 0:
        raise ValueError(
            f"来源顶点组 '{source.name}/{source_group_name}' 在当前来源结果上没有任何非零权重，无法传递。"
            "请确认该组不是空组，并检查是否因为修改器/姿态导致 evaluated 结果里权重已不再对应当前网格。"
        )
    matched, weights, match_stats = _rwt.find_matches_closest_surface(
        source_verts,
        source_faces,
        source_normals,
        target_verts,
        target_normals,
        source_weights,
        settings.robust_max_distance ** 2,
        _rwt.degrees(settings.robust_normal_angle),
        settings.robust_flip_normals,
        return_stats=True,
    )
    matched_count = int(_rwt.np_module().count_nonzero(matched))
    if matched_count <= 0:
        raise RuntimeError(
            "Robust 没有找到满足距离/法线角阈值的有效匹配；"
            f"目标顶点={match_stats.get('target_vertices', 0)}, "
            f"距离通过={match_stats.get('distance_pass', 0)}, "
            f"法线通过={match_stats.get('angle_pass', 0)}, "
            f"最终匹配=0, 最大距离={settings.robust_max_distance:g}, "
            f"法线角={_rwt.degrees(settings.robust_normal_angle):g}°, "
            f"允许翻转={bool(settings.robust_flip_normals)}。请增大 Robust 最大距离或法线角。"
        )
    component_stats = _matched_component_stats(target, matched)
    weights_for_inpaint = weights
    matched_for_inpaint = matched
    rescue_info = {
        "rescued_components": 0,
        "rescued_vertices": 0,
        "filled_unmatched_vertices": 0,
        "zero_anchor_components": 0,
        "zero_anchor_vertices": 0,
        "evidence_blocked_components": 0,
        "evidence_blocked_vertices": 0,
        "inpaint_fallback": "",
    }
    if component_stats is not None:
        weights_for_inpaint, matched_for_inpaint, rescue_info = promote_unseeded_component_matches(
            target_verts,
            matched,
            weights,
            component_stats,
        )
    result, weights = _rwt.inpaint(
        target_verts,
        target_faces,
        weights_for_inpaint,
        matched_for_inpaint,
        settings.robust_point_cloud_inpaint,
    )
    if not result and bool(settings.robust_point_cloud_inpaint):
        fallback_result, fallback_weights = _rwt.inpaint(
            target_verts,
            target_faces,
            weights_for_inpaint,
            matched_for_inpaint,
            False,
        )
        if fallback_result:
            rescue_info = dict(rescue_info)
            rescue_info["inpaint_fallback"] = "MESH"
            fallback_weights, gate_info = _apply_source_positive_component_gate(
                fallback_weights,
                matched,
                weights_for_inpaint,
                component_stats,
            )
            rescue_info.update(gate_info)
            return _weight_values(fallback_weights), matched_for_inpaint, matched_count, rescue_info
        rescue_info = dict(rescue_info)
        rescue_info["inpaint_fallback_failed"] = "MESH"
    if not result:
        raise RuntimeError(describe_robust_inpaint_failure(target, matched, settings, component_stats, rescue_info))
    weights, gate_info = _apply_source_positive_component_gate(
        weights,
        matched,
        weights_for_inpaint,
        component_stats,
    )
    rescue_info.update(gate_info)
    return _weight_values(weights), matched_for_inpaint, matched_count, rescue_info


def _matched_component_stats(obj, matched):
    mesh = getattr(obj, "data", None)
    if mesh is None:
        return None
    total = len(mesh.vertices)
    if total == 0 or len(matched) != total:
        return None
    adjacency = [[] for _ in range(total)]
    for edge in mesh.edges:
        a, b = edge.vertices
        adjacency[a].append(b)
        adjacency[b].append(a)
    seen = [False] * total
    components = 0
    unseeded_components = 0
    largest_unseeded = 0
    isolated_unseeded = 0
    unseeded_vertex_count = 0
    component_lists = []
    unseeded_lists = []
    for start in range(total):
        if seen[start]:
            continue
        components += 1
        stack = [start]
        seen[start] = True
        size = 0
        seeded = False
        component_vertices = []
        while stack:
            current = stack.pop()
            size += 1
            component_vertices.append(current)
            if matched[current]:
                seeded = True
            for neighbor in adjacency[current]:
                if seen[neighbor]:
                    continue
                seen[neighbor] = True
                stack.append(neighbor)
        component_lists.append(component_vertices)
        if seeded:
            continue
        unseeded_components += 1
        unseeded_vertex_count += size
        largest_unseeded = max(largest_unseeded, size)
        unseeded_lists.append(component_vertices)
        if size == 1:
            isolated_unseeded += 1
    return {
        "components": components,
        "unseeded_components": unseeded_components,
        "largest_unseeded": largest_unseeded,
        "isolated_unseeded": isolated_unseeded,
        "unseeded_vertex_count": unseeded_vertex_count,
        "component_lists": component_lists,
        "unseeded_lists": unseeded_lists,
    }


def promote_unseeded_component_matches(target_verts, matched, weights, component_stats, *, threshold=0.00001):
    if component_stats is None:
        return weights, matched, _empty_component_rescue_info()
    component_lists = component_stats.get("component_lists") or []
    if not component_lists:
        return weights, matched, _empty_component_rescue_info()
    flat_weights = _weight_values(weights)
    prepared_weights = weights.copy()
    prepared_matched = matched.copy()
    zero_anchor_components = 0
    zero_anchor_vertices = 0
    evidence_blocked_components = 0
    evidence_blocked_vertices = 0
    for component in component_lists:
        if not component:
            continue
        has_match = any(bool(matched[index]) for index in component)
        has_positive_seed = any(
            bool(matched[index]) and float(flat_weights[index]) > threshold
            for index in component
        )
        if not has_match:
            anchor_index = component[0]
            prepared_weights[anchor_index] = 0.0
            prepared_matched[anchor_index] = True
            zero_anchor_components += 1
            zero_anchor_vertices += 1
        if not has_positive_seed:
            evidence_blocked_components += 1
            evidence_blocked_vertices += len(component)
    return prepared_weights, prepared_matched, {
        "rescued_components": 0,
        "rescued_vertices": 0,
        "filled_unmatched_vertices": 0,
        "zero_anchor_components": zero_anchor_components,
        "zero_anchor_vertices": zero_anchor_vertices,
        "evidence_blocked_components": evidence_blocked_components,
        "evidence_blocked_vertices": evidence_blocked_vertices,
        "inpaint_fallback": "",
    }


def _empty_component_rescue_info():
    return {
        "rescued_components": 0,
        "rescued_vertices": 0,
        "filled_unmatched_vertices": 0,
        "zero_anchor_components": 0,
        "zero_anchor_vertices": 0,
        "evidence_blocked_components": 0,
        "evidence_blocked_vertices": 0,
        "inpaint_fallback": "",
    }


def _apply_source_positive_component_gate(weights, matched, seed_weights, component_stats, *, threshold=0.00001):
    if component_stats is None:
        return weights, {
            "evidence_blocked_components": 0,
            "evidence_blocked_vertices": 0,
        }
    component_lists = component_stats.get("component_lists") or []
    if not component_lists:
        return weights, {
            "evidence_blocked_components": 0,
            "evidence_blocked_vertices": 0,
        }
    flat_seed_weights = _weight_values(seed_weights)
    gated_weights = weights.copy()
    evidence_blocked_components = 0
    evidence_blocked_vertices = 0
    for component in component_lists:
        has_positive_seed = any(
            bool(matched[index]) and float(flat_seed_weights[index]) > threshold
            for index in component
        )
        if has_positive_seed:
            continue
        evidence_blocked_components += 1
        evidence_blocked_vertices += len(component)
        for vertex_index in component:
            gated_weights[vertex_index] = 0.0
    return gated_weights, {
        "evidence_blocked_components": evidence_blocked_components,
        "evidence_blocked_vertices": evidence_blocked_vertices,
    }


def describe_robust_inpaint_failure(target, matched, settings, component_stats=None, rescue_info=None):
    total = len(getattr(target.data, "vertices", ()))
    matched_count = int(_rwt.np_module().count_nonzero(matched))
    parts = [
        "Robust inpaint 失败",
        f"direct match={matched_count}/{total}",
        f"Point inpaint={bool(settings.robust_point_cloud_inpaint)}",
    ]
    component_stats = component_stats or _matched_component_stats(target, matched)
    if component_stats is not None:
        parts.append(f"目标连通域={component_stats['components']}")
        parts.append(f"无命中连通域={component_stats['unseeded_components']}")
        if component_stats["largest_unseeded"] > 0:
            parts.append(f"最大无命中连通域顶点数={component_stats['largest_unseeded']}")
        if component_stats["isolated_unseeded"] > 0:
            parts.append(f"孤立无命中顶点={component_stats['isolated_unseeded']}")
    rescue_info = rescue_info or _empty_component_rescue_info()
    if rescue_info.get("zero_anchor_components", 0) > 0:
        parts.append(f"零权重锚点连通域={rescue_info['zero_anchor_components']}")
        parts.append(f"零权重锚点={rescue_info['zero_anchor_vertices']}")
    if rescue_info.get("evidence_blocked_components", 0) > 0:
        parts.append(f"无来源正权重证据连通域={rescue_info['evidence_blocked_components']}")
        parts.append(f"无来源正权重证据顶点={rescue_info['evidence_blocked_vertices']}")
    if rescue_info.get("inpaint_fallback_failed"):
        parts.append(f"自动回退={rescue_info['inpaint_fallback_failed']}(失败)")
    detail = "；".join(parts)
    if component_stats is not None and component_stats["unseeded_components"] > 0:
        return (
            f"{detail}。目标网格至少有一块连通域完全没有拿到 direct match 种子，"
            "inpaint 无法把权重传播进去。把法线角调到 180° 只能放宽角度过滤，"
            "不会取消最大距离过滤；如果仍有整块连通域离所有可匹配来源面都太远，就还是会零种子。"
            "请优先检查 Robust 最大距离、目标断岛位置，或改用 Data Transfer 表面插值。"
        )
    if rescue_info.get("inpaint_fallback_failed"):
        return (
            f"{detail}。当前 Point inpaint 与自动回退的 mesh inpaint 都失败了。"
            "请优先检查目标几何是否存在异常、非流形或极薄断片，再视情况放宽 direct match 阈值，"
            "或改用 Data Transfer 表面插值。"
        )
    return (
        f"{detail}。求解器未能为当前目标网格建立有效的 inpaint 结果。"
        "请尝试切换 Point inpaint、检查目标几何是否异常，或放宽 direct match 阈值后重试。"
    )


def transfer_with_data_transfer(context, settings, source_group_name, target_group_name):
    source = settings.source_object
    target = settings.target_object
    source_group = source.vertex_groups.get(source_group_name)
    target_group = target.vertex_groups.get(target_group_name)
    if source_group is None or target_group is None:
        raise ValueError("Data Transfer 缺少来源组或承接组")
    depsgraph = context.evaluated_depsgraph_get()
    source_eval = source.evaluated_get(depsgraph) if settings.use_deformed_source else source
    target_eval = target.evaluated_get(depsgraph) if settings.use_deformed_target else target
    old_active = context.view_layer.objects.active
    old_mode = getattr(context, "mode", 'OBJECT')
    old_selection = list(context.selected_objects)
    modifier = None
    temp_source = None
    temp_target = None
    try:
        if old_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        temp_source = _create_temp_transfer_object(context, source_eval, "__velo_weight_dt_source")
        temp_target = _create_temp_transfer_object(context, target_eval, "__velo_weight_dt_target")
        if len(temp_target.data.vertices) != len(target.data.vertices):
            raise ValueError("目标变形结果拓扑与原网格不一致")
        _clear_vertex_groups(temp_source)
        _clear_vertex_groups(temp_target)
        source_weights = read_group_weights(source, source_group)
        if len(source_weights) != len(temp_source.data.vertices):
            raise ValueError("来源变形结果拓扑与原网格不一致，面插值传递暂不支持拓扑变化来源")
        temp_source_group = temp_source.vertex_groups.new(name=source_group_name)
        temp_target_group = temp_target.vertex_groups.new(name=target_group_name)
        write_group_weights(temp_source, temp_source_group, source_weights)
        temp_source.vertex_groups.active_index = temp_source_group.index
        temp_target.vertex_groups.active_index = temp_target_group.index
        for obj in context.selected_objects:
            obj.select_set(False)
        temp_target.select_set(True)
        context.view_layer.objects.active = temp_target
        modifier = temp_target.modifiers.new("__velo_weight_data_transfer", 'DATA_TRANSFER')
        modifier.object = temp_source
        modifier.use_vert_data = True
        modifier.data_types_verts = {'VGROUP_WEIGHTS'}
        modifier.vert_mapping = 'POLYINTERP_NEAREST'
        if hasattr(modifier, "layers_vgroup_select_src"):
            modifier.layers_vgroup_select_src = source_group_name
        if hasattr(modifier, "layers_vgroup_select_dst"):
            modifier.layers_vgroup_select_dst = 'INDEX'
        if hasattr(modifier, "mix_mode"):
            modifier.mix_mode = 'REPLACE'
        if hasattr(modifier, "mix_factor"):
            modifier.mix_factor = 1.0
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        modifier = None
        temp_target_group = temp_target.vertex_groups.get(target_group_name)
        if temp_target_group is None:
            raise RuntimeError("面插值传递完成后未能在临时目标网格中找到承接组")
        return read_group_weights(temp_target, temp_target_group)
    except UnicodeDecodeError as exc:
        raise RuntimeError(
            "面插值传递在 Blender Data Transfer 执行阶段遇到文本解码错误；"
            "当前实现已经改为在临时求值网格上运行 `POLYINTERP_NEAREST`，"
            "若仍触发该错误，说明场景里有其它插件或导入对象的更新逻辑在依赖图刷新时读取了非 UTF-8 文件。"
            f"原始错误: {exc}"
        ) from exc
    finally:
        if modifier is not None and temp_target is not None and modifier.name in temp_target.modifiers:
            try:
                temp_target.modifiers.remove(modifier)
            except Exception:
                pass
        _remove_temp_transfer_object(temp_target)
        _remove_temp_transfer_object(temp_source)
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in old_selection:
            try:
                obj.select_set(True)
            except Exception:
                pass
        if old_active is not None:
            context.view_layer.objects.active = old_active
        if old_mode != 'OBJECT' and old_active is not None:
            try:
                bpy.ops.object.mode_set(mode=old_mode)
            except Exception:
                pass


def _create_temp_transfer_object(context, obj, name):
    mesh_eval = obj.to_mesh()
    if mesh_eval is None:
        raise RuntimeError(f"无法为 '{obj.name}' 创建临时传递网格")
    try:
        mesh_copy = mesh_eval.copy()
    finally:
        obj.to_mesh_clear()
    temp_obj = bpy.data.objects.new(name, mesh_copy)
    context.scene.collection.objects.link(temp_obj)
    temp_obj.hide_viewport = True
    temp_obj.hide_render = True
    return temp_obj


def _remove_temp_transfer_object(obj):
    if obj is None:
        return
    mesh = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:
        pass
    if mesh is not None and getattr(mesh, "users", 0) == 0:
        try:
            bpy.data.meshes.remove(mesh)
        except Exception:
            pass


def _clear_vertex_groups(obj):
    groups = list(getattr(obj, "vertex_groups", ()))
    for group in groups:
        try:
            obj.vertex_groups.remove(group)
        except Exception:
            pass


def read_group_weights(obj, group):
    return _rwt.get_group_weights_by_index(obj, group.index)


def count_group_weights(obj, group, *, threshold=0.00001):
    weights = read_group_weights(obj, group)
    return int(_rwt.np_module().count_nonzero(weights > threshold))


def _uv_key(value):
    return (round(float(value.x), 6), round(float(value.y), 6))


def collect_uv_seam_edges(mesh):
    blocked = set()
    for edge in mesh.edges:
        if getattr(edge, "use_seam", False):
            blocked.add(tuple(sorted(edge.vertices)))
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return blocked
    seen = {}
    uv_data = uv_layer.data
    for poly in mesh.polygons:
        verts = list(poly.vertices)
        loops = list(poly.loop_indices)
        count = len(verts)
        for i in range(count):
            a = verts[i]
            b = verts[(i + 1) % count]
            key = tuple(sorted((a, b)))
            uv_a = _uv_key(uv_data[loops[i]].uv)
            uv_b = _uv_key(uv_data[loops[(i + 1) % count]].uv)
            pair = (uv_a, uv_b) if key[0] == a else (uv_b, uv_a)
            old = seen.get(key)
            if old is None:
                seen[key] = pair
            elif old != pair:
                blocked.add(key)
    return blocked


def build_weight_topology_cache(obj):
    blocked = collect_uv_seam_edges(obj.data)
    return {
        "blocked_edges": blocked,
        "adjacency_matrix": _rwt.get_mesh_adjacency_matrix_sparse(obj.data, include_self=True, blocked_edges=blocked),
        "adjacency_list": _rwt.get_mesh_adjacency_list(obj.data, blocked_edges=blocked),
    }


def apply_seam_safe_smoothing(obj, group, settings, matched=None, *, topology_cache=None):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"Robust 平滑不可用: {error}")
    try:
        cache = topology_cache or build_weight_topology_cache(obj)
        adjacency_matrix = cache["adjacency_matrix"]
        adjacency_list = cache["adjacency_list"]
    except Exception:
        return False
    verts, _faces, _normals = _rwt.get_obj_arrs_world(obj)
    weights = read_group_weights(obj, group).reshape(-1, 1)
    if matched is None or len(matched) != len(obj.data.vertices):
        matched = weights[:, 0] > 0.00001
    smoothed = _rwt.smooth_weigths(
        verts,
        weights,
        matched,
        adjacency_matrix,
        adjacency_list,
        settings.smoothing_repeat,
        settings.smoothing_factor,
        settings.robust_max_distance,
    )
    write_group_weights(obj, group, smoothed[:, 0])
    return True


def editable_group_indices(obj):
    indices = []
    for vg in obj.vertex_groups:
        if vg.lock_weight:
            continue
        if is_special_vg_name(vg.name):
            continue
        indices.append(vg.index)
    return indices


def apply_limit_groups(obj, settings, *, topology_cache=None):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"Robust limit 不可用: {error}")
    np = _rwt.np_module()
    group_indices = editable_group_indices(obj)
    if len(group_indices) <= settings.max_groups_per_vertex:
        return False
    weights = _rwt.get_groups_arr(obj, group_indices)
    weights[weights <= 0.0001] = 0.0
    if topology_cache is not None:
        adjacency = topology_cache["adjacency_matrix"]
    else:
        blocked = collect_uv_seam_edges(obj.data)
        adjacency = _rwt.get_mesh_adjacency_matrix_sparse(obj.data, include_self=True, blocked_edges=blocked)
    mask = _rwt.limit_mask(
        weights,
        adjacency,
        dilation_repeat=settings.limit_dilation_repeat,
        limit_num=settings.max_groups_per_vertex,
    )
    limited = (1 - mask) * weights
    limited[limited <= 0.0001] = 0.0
    changed_columns = np.flatnonzero(np.any(np.abs(limited - weights) > 1e-8, axis=0))
    if len(changed_columns) == 0:
        return False
    changed_indices = [group_indices[int(column)] for column in changed_columns]
    write_groups_by_indices(obj, changed_indices, limited[:, changed_columns])
    return True


def write_groups_by_indices(obj, group_indices, weights):
    for column, group_index in enumerate(group_indices):
        group = obj.vertex_groups[group_index]
        if group.lock_weight or is_special_vg_name(group.name):
            continue
        write_group_weights(obj, group, weights[:, column])


def _name_side_suffix(name):
    lowered = _clean_name(name).lower()
    if lowered.endswith((".l", "_l", "-l")):
        return "L"
    if lowered.endswith((".r", "_r", "-r")):
        return "R"
    return ""


def _side_affinity(candidate_name, target_name):
    candidate_side = _name_side_suffix(candidate_name)
    target_side = _name_side_suffix(target_name)
    if not candidate_side or not target_side:
        return 0
    return 1 if candidate_side == target_side else -1


def _name_without_side(name):
    cleaned = _clean_name(name)
    lowered = cleaned.lower()
    for suffix in (".l", ".r", "_l", "_r", "-l", "-r"):
        if lowered.endswith(suffix):
            return cleaned[:-len(suffix)]
    return cleaned


def _is_control_bone_name(name):
    base = _name_without_side(name)
    if not base:
        return False
    if base.startswith(("_dummy_", "_shadow_")):
        return True
    if base.endswith(("C", "P")):
        return True
    return base in {"全ての親", "センター", "グルーブ"} or "キャンセル" in base


def _bone_chain(armature, bone_name):
    if armature is None or getattr(armature, "type", None) != 'ARMATURE':
        return []
    bone = armature.data.bones.get(bone_name)
    chain = []
    while bone is not None:
        chain.append(bone)
        bone = bone.parent
    return chain


def _is_usable_donor_group(obj, name, *, exclude_index=None):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return False
    group = obj.vertex_groups.get(_clean_name(name))
    if group is None:
        return False
    if exclude_index is not None and group.index == exclude_index:
        return False
    if group.lock_weight or is_special_vg_name(group.name):
        return False
    return count_group_weights(obj, group) > 0


def _hierarchy_anchor_donor_names(obj, armature, target_name, *, exclude_index=None, max_count=None):
    chain = _bone_chain(armature, target_name)
    if len(chain) <= 1:
        return []
    target_side = _name_side_suffix(target_name)
    ancestors = chain[1:]
    valid = []
    for ancestor in ancestors:
        name = ancestor.name
        if target_side and _name_side_suffix(name) not in {"", target_side}:
            continue
        if not _is_usable_donor_group(obj, name, exclude_index=exclude_index):
            continue
        valid.append(ancestor)
    if not valid:
        return []
    for ancestor in valid:
        parent = ancestor.parent
        if parent is not None and _is_control_bone_name(parent.name):
            return [ancestor.name]
    result = [ancestor.name for ancestor in valid]
    if max_count is not None and max_count > 0:
        return result[:max_count]
    return result


def _source_group_centroid_world(obj, group_name, *, threshold=0.00001):
    group = obj.vertex_groups.get(_clean_name(group_name)) if obj is not None else None
    if obj is None or getattr(obj, "type", None) != 'MESH' or group is None:
        return None
    total = 0.0
    center = Vector((0.0, 0.0, 0.0))
    for vertex in obj.data.vertices:
        weight = 0.0
        for item in vertex.groups:
            if item.group == group.index:
                weight = float(item.weight)
                break
        if weight <= threshold:
            continue
        center += (obj.matrix_world @ vertex.co) * weight
        total += weight
    if total <= threshold:
        return None
    return center / total


def _source_group_centroids_world(obj, group_names, *, threshold=0.00001):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return {}
    wanted = []
    seen = set()
    for name in group_names:
        cleaned = _clean_name(name)
        if not cleaned or cleaned in seen:
            continue
        group = obj.vertex_groups.get(cleaned)
        if group is None:
            continue
        wanted.append((cleaned, group.index))
        seen.add(cleaned)
    if not wanted:
        return {}
    wanted_by_index = {index: name for name, index in wanted}
    sums = {name: Vector((0.0, 0.0, 0.0)) for name, _index in wanted}
    totals = {name: 0.0 for name, _index in wanted}
    matrix_world = obj.matrix_world
    for vertex in obj.data.vertices:
        world = None
        for item in vertex.groups:
            name = wanted_by_index.get(item.group)
            if name is None:
                continue
            weight = float(item.weight)
            if weight <= threshold:
                continue
            if world is None:
                world = matrix_world @ vertex.co
            sums[name] += world * weight
            totals[name] += weight
    return {
        name: sums[name] / total
        for name, total in totals.items()
        if total > threshold
    }


def _first_existing_group_name(obj, names):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return ""
    for name in names:
        cleaned = _clean_name(name)
        if cleaned and obj.vertex_groups.get(cleaned) is not None:
            return cleaned
    return ""


def _mapped_neighbor_anchor_donor_names(context, settings, source_name, target_name, *, max_rows=12, max_count=None):
    source = getattr(settings, "source_object", None)
    target = getattr(settings, "target_object", None)
    armature = resolve_armature_object(settings)
    if source is None or target is None or armature is None:
        return []
    ef, profile = _mmd_profile(context)
    if profile is None:
        return []
    direction = _mmd_direction(ef, source, target)
    row_specs = []
    centroid_names = [source_name]
    for row in getattr(profile, "rows", ()):
        names = _row_names(row)
        row_source = _first_existing_group_name(
            source,
            _mmd_candidate_names(names, source_name, direction) + _unique_names(names["current"], names["mmd"], names["unified"]),
        )
        if not row_source or row_source == source_name:
            continue
        row_target = _first_existing_group_name(
            target,
            _mmd_candidate_names(names, row_source, direction) + _unique_names(names["unified"], names["current"], names["mmd"]),
        )
        if not row_target or row_target == target_name:
            continue
        row_specs.append((row_source, row_target))
        centroid_names.append(row_source)
    centroids = _source_group_centroids_world(source, centroid_names)
    source_center = centroids.get(_clean_name(source_name))
    if source_center is None:
        return []
    rows = []
    for row_source, row_target in row_specs:
        center = centroids.get(row_source)
        if center is None:
            continue
        distance = float((source_center - center).length)
        rows.append((distance, row_target))
    rows.sort(key=lambda item: item[0])
    result = []
    seen = set()
    for _distance, row_target in rows[:max_rows]:
        for donor_name in _hierarchy_anchor_donor_names(target, armature, row_target, max_count=max_count):
            if donor_name in seen:
                continue
            seen.add(donor_name)
            result.append(donor_name)
            if max_count is not None and max_count > 0 and len(result) >= max_count:
                return result
    return result


def semantic_auto_donor_names(context, settings, source_name, target_group, count=None):
    max_count = None
    if count is not None:
        max_count = max(int(count), 0)
        if max_count == 0:
            return []
    target = getattr(settings, "target_object", None)
    armature = resolve_armature_object(settings)
    names = _hierarchy_anchor_donor_names(
        target,
        armature,
        target_group.name,
        exclude_index=target_group.index,
        max_count=max_count,
    )
    if max_count is not None and len(names) >= max_count:
        return names[:max_count]
    mapped = _mapped_neighbor_anchor_donor_names(
        context,
        settings,
        source_name,
        target_group.name,
        max_count=max_count,
    )
    combined = _unique_names(*names, *mapped)
    if max_count is not None and max_count > 0:
        return combined[:max_count]
    return combined


def _mean_vector(coords, indices):
    center = Vector((0.0, 0.0, 0.0))
    count = 0
    for index in indices:
        center += coords[int(index)]
        count += 1
    if count:
        center /= count
    return center


def _mean_nearest_distance(coords, source_indices, target_indices):
    if len(source_indices) == 0 or len(target_indices) == 0:
        return float("inf")
    tree = kdtree.KDTree(len(source_indices))
    for index in source_indices:
        tree.insert(coords[int(index)], int(index))
    tree.balance()
    total = 0.0
    count = 0
    for index in target_indices:
        _co, _nearest_index, distance = tree.find(coords[int(index)])
        if distance is None:
            continue
        total += float(distance)
        count += 1
    if count == 0:
        return float("inf")
    return total / count


def _select_spatial_fallback_donors(obj, target_group, target_mask, candidate_groups, count, np):
    if count <= 0 or not candidate_groups:
        return []
    target_indices = np.flatnonzero(target_mask)
    if len(target_indices) == 0:
        return []
    coords = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
    target_center = _mean_vector(coords, target_indices)
    candidates = []
    batch_size = 64
    for start in range(0, len(candidate_groups), batch_size):
        group_batch = candidate_groups[start:start + batch_size]
        batch_indices = [vg.index for vg in group_batch]
        batch_weights = _rwt.get_groups_arr(obj, batch_indices)
        if batch_weights.size == 0:
            continue
        for column, vg in enumerate(group_batch):
            weights = batch_weights[:, column]
            total = float(weights.sum())
            if total <= 0.0:
                continue
            source_indices = np.flatnonzero(weights > 0.00001)
            if len(source_indices) == 0:
                continue
            mean_distance = _mean_nearest_distance(coords, source_indices, target_indices)
            if mean_distance == float("inf"):
                continue
            source_center = _mean_vector(coords, source_indices)
            center_distance = float((target_center - source_center).length)
            side_score = _side_affinity(vg.name, target_group.name)
            candidates.append((round(mean_distance, 4), -side_score, mean_distance, center_distance, -total, vg.index))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4], item[5]))
    return [obj.vertex_groups[index] for _bucket, _side, _mean_distance, _center_distance, _neg_total, index in candidates[:count]]


def select_auto_donors(obj, target_group, count, *, exclude_group_names=None, preferred_names=None, strict_preferred=False):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"供体选择不可用: {error}")
    np = _rwt.np_module()
    count = max(int(count), 0)
    if count == 0:
        return []
    exclude_group_names = {_clean_name(name) for name in (exclude_group_names or ()) if _clean_name(name)}
    preferred = []
    seen_preferred = set()
    for name in preferred_names or ():
        cleaned = _clean_name(name)
        if not cleaned or cleaned in seen_preferred or cleaned in exclude_group_names:
            continue
        group = obj.vertex_groups.get(cleaned)
        if group is None or group.index == target_group.index:
            continue
        if group.lock_weight or is_special_vg_name(group.name):
            continue
        if count_group_weights(obj, group) <= 0:
            continue
        preferred.append(group)
        seen_preferred.add(cleaned)
        if len(preferred) >= count:
            break
    if preferred and strict_preferred:
        return preferred
    target_weights = read_group_weights(obj, target_group)
    target_mask = target_weights > 0.00001
    if int(np.count_nonzero(target_mask)) <= 0:
        return preferred
    candidate_groups = []
    for vg in obj.vertex_groups:
        if vg.index == target_group.index:
            continue
        if vg.lock_weight:
            continue
        if _clean_name(vg.name) in exclude_group_names:
            continue
        if is_special_vg_name(vg.name):
            continue
        candidate_groups.append(vg)
    if not candidate_groups:
        return preferred
    candidates = []
    target_mask_2d = target_mask.reshape(-1, 1)
    target_weights_2d = target_weights.reshape(-1, 1)
    batch_size = 64
    for start in range(0, len(candidate_groups), batch_size):
        group_batch = candidate_groups[start:start + batch_size]
        batch_indices = [vg.index for vg in group_batch]
        batch_weights = _rwt.get_groups_arr(obj, batch_indices)
        if batch_weights.size == 0:
            continue
        batch_mask = batch_weights > 0.00001
        shared_vertices_arr = np.count_nonzero(batch_mask & target_mask_2d, axis=0)
        overlap_arr = np.minimum(batch_weights, target_weights_2d).sum(axis=0)
        focus_sum_arr = batch_weights[target_mask].sum(axis=0)
        total_arr = batch_weights.sum(axis=0)
        for column, vg in enumerate(group_batch):
            total = float(total_arr[column])
            if total <= 0.0:
                continue
            shared_vertices = int(shared_vertices_arr[column])
            overlap = float(overlap_arr[column])
            focus_sum = float(focus_sum_arr[column])
            if shared_vertices <= 0 and overlap <= 0.0 and focus_sum <= 0.0:
                continue
            focus_ratio = focus_sum / total if total > 1e-8 else 0.0
            # Prefer donors that share real weighted mass with the target group.
            # Raw covered-vertex count can otherwise bias the result toward large,
            # unrelated rigid groups that merely blanket the same region.
            candidates.append((overlap, focus_ratio, focus_sum, shared_vertices, -total, vg.index))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4]), reverse=True)
    selected_indices = [group.index for group in preferred]
    remaining_count = max(0, count - len(selected_indices))
    selected_indices.extend(
        index
        for _overlap, _focus_ratio, _focus_sum, _shared, _neg_total, index in candidates
        if index not in selected_indices
    )
    selected_indices = selected_indices[:count]
    if len(selected_indices) < count:
        selected = set(selected_indices)
        remaining_groups = [vg for vg in candidate_groups if vg.index not in selected]
        fallback = _select_spatial_fallback_donors(obj, target_group, target_mask, remaining_groups, count - len(selected_indices), np)
        selected_indices.extend(vg.index for vg in fallback)
    return [obj.vertex_groups[index] for index in selected_indices]


def _write_groups_preserving_locks(obj, groups, weights):
    original_locks = []
    try:
        for group in groups:
            original_locks.append((group, bool(group.lock_weight)))
            if group.lock_weight:
                group.lock_weight = False
        write_groups_by_indices(obj, [group.index for group in groups], weights)
    finally:
        for group, lock_weight in original_locks:
            try:
                group.lock_weight = lock_weight
            except Exception:
                pass


def normalize_with_donors(obj, target_group, donor_groups):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"规格化不可用: {error}")
    np = _rwt.np_module()
    participant = []
    seen = set()
    for vg in [target_group] + list(donor_groups):
        if vg is None or is_special_vg_name(vg.name) or vg.index in seen:
            continue
        participant.append(vg)
        seen.add(vg.index)
    if not participant:
        return False
    participant_indices = [vg.index for vg in participant]
    participant_weights = _rwt.get_groups_arr(obj, participant_indices)
    protected_indices = [
        vg.index
        for vg in obj.vertex_groups
        if vg.index not in participant_indices and not is_special_vg_name(vg.name)
    ]
    if protected_indices:
        protected_sum = _rwt.get_groups_arr(obj, protected_indices).sum(axis=1)
    else:
        protected_sum = np.zeros(len(obj.data.vertices), dtype=np.float32)
    available = np.maximum(0.0, 1.0 - protected_sum)
    target_weights = participant_weights[:, 0]
    normalized = np.zeros_like(participant_weights)
    normalized[:, 0] = np.minimum(target_weights, available)
    if participant_weights.shape[1] > 1:
        donor_weights = participant_weights[:, 1:]
        donor_available = np.maximum(0.0, available - normalized[:, 0])
        donor_totals = donor_weights.sum(axis=1)
        donor_scale = np.divide(
            donor_available,
            donor_totals,
            out=np.zeros_like(donor_available),
            where=donor_totals > 1e-8,
        )
        normalized[:, 1:] = donor_weights * donor_scale.reshape(-1, 1)
    _write_groups_preserving_locks(obj, participant, normalized)
    return True


def ensure_numeric_export_compatible(obj, group_name):
    if not (group_name or "").isdigit():
        return
    try:
        from ..core.mapping import algorithms as _mapping_algorithms
        _mapping_algorithms.reorder_numeric_vertex_groups_first(obj)
        obj["velo_weight_numeric_export_ready"] = True
    except Exception:
        pass
