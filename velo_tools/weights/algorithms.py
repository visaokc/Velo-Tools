from __future__ import annotations

from contextlib import contextmanager
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
    protected_over_limit_vertices: int = 0
    authority_limited_vertices: int = 0
    authority_suppressed_vertices: int = 0
    locked_boundary_vertices: int = 0
    no_yieldable_vertices: int = 0
    under_normalized_vertices: int = 0
    source_weight_max: float = 0.0
    raw_weight_max: float = 0.0
    raw_weight_nonzero: int = 0
    normalized: bool = False
    limit_skipped_same_object: bool = False
    normalize_skipped_same_object: bool = False
    donors: list[str] = field(default_factory=list)
    skipped_locked_donor_pairs: list[str] = field(default_factory=list)


@dataclass
class ScopedLimitReport:
    changed: bool = False
    protected_over_limit_vertices: int = 0
    authority_limited_vertices: int = 0


@dataclass
class NormalizationReport:
    attempted: bool = False
    changed: bool = False
    normalized_vertices: int = 0
    limited_vertices: int = 0
    locked_limit_vertices: int = 0
    no_yieldable_vertices: int = 0
    under_normalized_vertices: int = 0
    over_capacity_vertices: int = 0
    problem_vertices: list[int] = field(default_factory=list)

    def __bool__(self):
        return bool(self.attempted)


@dataclass
class DonorPairEligibility:
    donors: list = field(default_factory=list)
    mirror_donors: list = field(default_factory=list)
    skipped_locked_pairs: list[str] = field(default_factory=list)
    skipped_unavailable_pairs: list[str] = field(default_factory=list)


@dataclass
class SourceCompetitionInfo:
    selected_weights: object
    competitor_weights: object
    outcompeted: object


@dataclass
class RobustMatrixTransferResult:
    weights: object
    matched: object
    matched_count: int
    info: dict


@dataclass
class TargetGroupResolution:
    name: str
    should_create: bool
    reason: str = ""
    candidates: list[str] = field(default_factory=list)
    claimed: bool = False


@dataclass
class MirrorGroupResolution:
    source_name: str = ""
    mirror_name: str = ""
    reason: str = ""
    confidence: float = 0.0
    automatic: bool = False


_NATIVE_MIRROR_FLAGS = (
    ("use_mirror_vertex_groups", "Mirror Vertex Groups"),
    ("use_mirror_topology", "Topology Mirror"),
    ("use_mirror_x", "X Mirror"),
    ("use_mirror_y", "Y Mirror"),
    ("use_mirror_z", "Z Mirror"),
)
_DONOR_DOMINANCE_RATIO = 0.12
_DONOR_SHARED_VERTEX_RATIO = 0.35
_DONOR_SHARED_VERTEX_MIN = 3
_DONOR_SHARED_FOCUS_RATIO = 0.35
_AUTHORITY_TRUSTED_SOURCE_RATIO = 0.25
_AUTHORITY_COMPETITION_RATIO = 0.25
_AUTHORITY_LOW_SOURCE_RATIO = 0.08


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


def _collection_is_descendant_or_same(root, collection):
    if root is None or collection is None:
        return False
    if root == collection:
        return True
    try:
        return collection in getattr(root, "children_recursive", ())
    except Exception:
        return False


def active_component_collection(scene):
    if scene is None:
        return None
    try:
        from ..games import registry as _registry
        desc = _registry.get_active_descriptor(scene)
    except Exception:
        desc = None
    if desc is None:
        return None
    return desc.component_collection(scene)


def active_game_display_name(scene):
    try:
        from ..games import registry as _registry
        desc = _registry.get_active_descriptor(scene)
    except Exception:
        desc = None
    return getattr(desc, "display_name", "") or "当前游戏"


def is_component_export_object(scene, obj):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return False, "当前对象不是 Mesh"
    if not getattr(obj, "name", "").startswith("Component"):
        return False, "当前对象不是 Component 前缀"
    root = active_component_collection(scene)
    if root is None:
        return False, f"请先在{active_game_display_name(scene)} Export Mod 中指定部件集合"
    for collection in getattr(obj, "users_collection", ()):
        if _collection_is_descendant_or_same(root, collection):
            return True, f"接管范围: {root.name}"
    return False, f"当前对象不在 {root.name} 导出集合内"


def validate_velo_mirror_scope(context, obj):
    scene = getattr(context, "scene", None)
    ok, reason = is_component_export_object(scene, obj)
    if not ok:
        raise ValueError(f"Velo 镜像只接管当前游戏导出集合内的 Component 物体；{reason}")
    return True


def validate_native_mirror_disabled(obj):
    mesh = getattr(obj, "data", None)
    enabled = []
    for attr, label in _NATIVE_MIRROR_FLAGS:
        if bool(getattr(mesh, attr, False)):
            enabled.append(label)
    if enabled:
        raise ValueError(
            "当前对象已进入 Velo 镜像接管范围，请先关闭 Blender 自带镜像选项: "
            + ", ".join(enabled)
        )


@contextmanager
def suppress_native_mirror_flags(obj):
    mesh = getattr(obj, "data", None)
    saved = []
    if mesh is not None:
        for attr, _label in _NATIVE_MIRROR_FLAGS:
            try:
                original = getattr(mesh, attr)
            except Exception:
                continue
            saved.append((attr, original))
            if bool(original):
                try:
                    setattr(mesh, attr, False)
                except Exception:
                    pass
    try:
        yield
    finally:
        for attr, original in saved:
            try:
                setattr(mesh, attr, original)
            except Exception:
                pass


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


def validate_source_group(source, group_name, *, require_unlocked=True):
    if source is None or source.type != 'MESH':
        raise ValueError(f"来源网格无效: {_object_label(source)}")
    group_name = _clean_name(group_name)
    if not group_name:
        raise ValueError(f"来源网格 '{source.name}' 未选择来源顶点组")
    group = source.vertex_groups.get(group_name)
    if group is None:
        raise ValueError(f"来源网格 '{source.name}' 不存在顶点组 '{group_name}'")
    if require_unlocked and group.lock_weight:
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


def lock_transfer_target_groups(groups):
    locked = []
    seen = set()
    for group in groups or ():
        if group is None or getattr(group, "index", None) in seen:
            continue
        try:
            group.lock_weight = True
        except Exception:
            continue
        locked.append(group.name)
        seen.add(getattr(group, "index", None))
    return locked


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
    weights = _plain_group_weights(obj, group)
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


def _source_all_group_competition(
    source_eval,
    target,
    source_verts,
    source_faces,
    source_normals,
    target_verts,
    target_faces,
    target_normals,
    settings,
    source_group_name,
    *,
    threshold=0.00001,
):
    np = _rwt.np_module()
    candidate_groups = []
    selected_column = None
    for group in getattr(source_eval, "vertex_groups", ()):
        name = _clean_name(getattr(group, "name", ""))
        if not name or is_special_vg_name(name):
            continue
        if name == source_group_name:
            selected_column = len(candidate_groups)
        candidate_groups.append(group)
    if selected_column is None or len(candidate_groups) <= 1:
        return None
    group_indices = [group.index for group in candidate_groups]
    source_matrix = _rwt.get_groups_arr(source_eval, group_indices)
    if source_matrix.shape[1] <= 1:
        return None
    active_columns = set(int(column) for column in np.flatnonzero(np.any(source_matrix > threshold, axis=0)))
    active_columns.add(int(selected_column))
    ordered_columns = sorted(active_columns)
    if len(ordered_columns) <= 1:
        return None
    selected_column = ordered_columns.index(int(selected_column))
    source_matrix = source_matrix[:, ordered_columns]
    matched_all, all_weights = _rwt.find_matches_closest_surface(
        source_verts,
        source_faces,
        source_normals,
        target_verts,
        target_normals,
        source_matrix,
        settings.robust_max_distance ** 2,
        _rwt.degrees(settings.robust_normal_angle),
        settings.robust_flip_normals,
    )
    result, all_weights = _rwt.inpaint(
        target_verts,
        target_faces,
        all_weights,
        matched_all,
        settings.robust_point_cloud_inpaint,
    )
    if not result and bool(settings.robust_point_cloud_inpaint):
        result, all_weights = _rwt.inpaint(
            target_verts,
            target_faces,
            all_weights,
            matched_all,
            False,
        )
    if not result:
        return None
    selected_weights = np.asarray(all_weights[:, selected_column], dtype=float)
    competitor_matrix = np.delete(all_weights, selected_column, axis=1)
    if competitor_matrix.shape[1] == 0:
        return None
    competitor_weights = np.max(competitor_matrix, axis=1)
    outcompeted = (selected_weights > threshold) & (
        competitor_weights > (selected_weights / max(_AUTHORITY_COMPETITION_RATIO, threshold))
    )
    max_groups = int(getattr(settings, "max_groups_per_vertex", 0))
    if bool(getattr(settings, "limit_groups_enable", False)) and max_groups > 0 and all_weights.shape[1] > max_groups:
        adjacency = _rwt.get_mesh_adjacency_matrix_sparse(target.data, include_self=True)
        limit_mask = _rwt.limit_mask(
            all_weights,
            adjacency,
            dilation_repeat=int(getattr(settings, "limit_dilation_repeat", 4)),
            limit_num=max_groups,
        )
        limited_selected = (1 - limit_mask[:, selected_column]) * selected_weights
        outcompeted |= (selected_weights > threshold) & (limited_selected <= threshold)
    return SourceCompetitionInfo(
        selected_weights=selected_weights,
        competitor_weights=np.asarray(competitor_weights, dtype=float),
        outcompeted=np.asarray(outcompeted, dtype=bool),
    )


def _positive_weight_components(obj, positive_mask):
    total = len(getattr(obj.data, "vertices", ()))
    adjacency = [[] for _ in range(total)]
    for edge in getattr(obj.data, "edges", ()):
        a, b = edge.vertices
        if 0 <= a < total and 0 <= b < total:
            adjacency[a].append(b)
            adjacency[b].append(a)
    seen = [False] * total
    components = []
    for start in range(total):
        if seen[start] or not bool(positive_mask[start]):
            continue
        stack = [start]
        seen[start] = True
        component = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in adjacency[current]:
                if seen[neighbor] or not bool(positive_mask[neighbor]):
                    continue
                seen[neighbor] = True
                stack.append(neighbor)
        components.append(component)
    return components


def build_pre_write_authority_mask(
    obj,
    weights,
    direct_matched,
    direct_weights,
    source_weight_max,
    source_competition=None,
    *,
    threshold=0.00001,
):
    np = _rwt.np_module()
    flat_weights = np.asarray(_weight_values(weights), dtype=float).reshape(-1)
    row_count = len(flat_weights)
    authority = flat_weights > threshold
    if row_count != len(getattr(obj.data, "vertices", ())):
        return authority, {
            "authority_suppressed_vertices": 0,
            "authority_suppressed_components": 0,
        }
    direct = np.asarray(_weight_values(direct_weights), dtype=float).reshape(-1)
    matched = np.asarray(direct_matched, dtype=bool).reshape(-1)
    if direct.shape[0] != row_count or matched.shape[0] != row_count:
        return authority, {
            "authority_suppressed_vertices": 0,
            "authority_suppressed_components": 0,
        }
    seed_threshold = max(float(threshold), float(source_weight_max) * _AUTHORITY_TRUSTED_SOURCE_RATIO)
    direct_positive = matched & (direct > threshold)
    trusted_seed = matched & (direct >= seed_threshold)
    if source_competition is None:
        outcompeted = np.zeros(row_count, dtype=bool)
    else:
        outcompeted = np.asarray(source_competition.outcompeted, dtype=bool).reshape(-1)
        if outcompeted.shape[0] != row_count:
            outcompeted = np.zeros(row_count, dtype=bool)
    suppressed = np.zeros(row_count, dtype=bool)
    suppressed_components = 0
    for component in _positive_weight_components(obj, authority):
        indices = np.asarray(component, dtype=int)
        if bool(np.any(trusted_seed[indices])):
            continue
        has_direct_positive = bool(np.any(direct_positive[indices]))
        if has_direct_positive:
            continue
        suppressed[indices] = True
        suppressed_components += 1
    low_authority_limit = max(float(threshold), float(source_weight_max) * _AUTHORITY_LOW_SOURCE_RATIO)
    weak_outcompeted_rows = authority & outcompeted & (flat_weights <= low_authority_limit) & ~trusted_seed
    suppressed |= weak_outcompeted_rows
    if bool(np.any(suppressed)):
        authority = authority.copy()
        authority[suppressed] = False
    return authority, {
        "authority_suppressed_vertices": int(np.count_nonzero(suppressed)),
        "authority_suppressed_components": int(suppressed_components),
        "authority_suppressed_rows": suppressed,
    }


def _robust_matrix_source_groups(source_eval, source_group_name, *, threshold=0.00001):
    np = _rwt.np_module()
    candidate_groups = []
    selected_column = None
    for group in getattr(source_eval, "vertex_groups", ()):
        name = _clean_name(getattr(group, "name", ""))
        if not name or is_special_vg_name(name):
            continue
        if name == source_group_name:
            selected_column = len(candidate_groups)
        candidate_groups.append(group)
    if selected_column is None or not candidate_groups:
        return [], None, None
    group_indices = [group.index for group in candidate_groups]
    source_matrix = _rwt.get_groups_arr(source_eval, group_indices)
    if source_matrix.shape[1] == 0:
        return [], None, None
    active_columns = set(int(column) for column in np.flatnonzero(np.any(source_matrix > threshold, axis=0)))
    active_columns.add(int(selected_column))
    ordered_columns = sorted(active_columns)
    selected_column = ordered_columns.index(int(selected_column))
    groups = [candidate_groups[int(column)] for column in ordered_columns]
    return groups, selected_column, source_matrix[:, ordered_columns]


def _smooth_robust_matrix_weights(target, target_verts, weights, matched, settings):
    try:
        cache = build_weight_topology_cache(target)
        adjacency_matrix = cache["adjacency_matrix"]
        adjacency_list = cache["adjacency_list"]
    except Exception:
        return weights, False
    smoothed = _rwt.smooth_weigths(
        target_verts,
        weights,
        matched,
        adjacency_matrix,
        adjacency_list,
        settings.smoothing_repeat,
        settings.smoothing_factor,
        settings.robust_max_distance,
    )
    return smoothed, True


def _robust_no_match_error(match_stats, settings):
    return RuntimeError(
        "Robust 没有找到满足距离/法线角阈值的有效匹配；"
        f"目标顶点={match_stats.get('target_vertices', 0)}, "
        f"距离通过={match_stats.get('distance_pass', 0)}, "
        f"法线通过={match_stats.get('angle_pass', 0)}, "
        f"最终匹配=0, 最大距离={settings.robust_max_distance:g}, "
        f"法线角={_rwt.degrees(settings.robust_normal_angle):g}°, "
        f"允许翻转={bool(settings.robust_flip_normals)}。请增大 Robust 最大距离或法线角。"
    )


def _transfer_with_robust_matrix_context(
    source,
    target,
    source_eval,
    target_verts,
    target_faces,
    target_normals,
    source_verts,
    source_faces,
    source_normals,
    settings,
    source_group_name,
    *,
    threshold=0.00001,
):
    np = _rwt.np_module()
    _groups, selected_column, source_matrix = _robust_matrix_source_groups(
        source_eval,
        source_group_name,
        threshold=threshold,
    )
    if selected_column is None or source_matrix is None:
        return None
    selected_source = source_matrix[:, selected_column]
    source_weight_count = int(np.count_nonzero(selected_source > threshold))
    if source_weight_count <= 0:
        raise ValueError(
            f"来源顶点组 '{source.name}/{source_group_name}' 在当前来源结果上没有任何非零权重，无法传递。"
            "请确认该组不是空组，并检查是否因为修改器/姿态导致 evaluated 结果里权重已不再对应当前网格。"
        )
    matched, matrix_weights, match_stats = _rwt.find_matches_closest_surface(
        source_verts,
        source_faces,
        source_normals,
        target_verts,
        target_normals,
        source_matrix,
        settings.robust_max_distance ** 2,
        _rwt.degrees(settings.robust_normal_angle),
        settings.robust_flip_normals,
        return_stats=True,
    )
    matched_count = int(np.count_nonzero(matched))
    if matched_count <= 0:
        raise _robust_no_match_error(match_stats, settings)
    rescue_info = _empty_component_rescue_info()
    component_stats = _matched_component_stats(target, matched)
    direct_matrix_weights = matrix_weights.copy()
    result, painted_matrix_weights = _rwt.inpaint(
        target_verts,
        target_faces,
        direct_matrix_weights.copy(),
        matched,
        settings.robust_point_cloud_inpaint,
    )
    if result:
        matrix_weights = painted_matrix_weights
    if not result and bool(settings.robust_point_cloud_inpaint):
        fallback_result, fallback_weights = _rwt.inpaint(
            target_verts,
            target_faces,
            direct_matrix_weights.copy(),
            matched,
            False,
        )
        if fallback_result:
            rescue_info = dict(rescue_info)
            rescue_info["inpaint_fallback"] = "MESH"
            matrix_weights = fallback_weights
            result = True
        else:
            rescue_info = dict(rescue_info)
            rescue_info["inpaint_fallback_failed"] = "MESH"
    if not result:
        raise RuntimeError(describe_robust_inpaint_failure(target, matched, settings, component_stats, rescue_info))
    smoothing_requested = (
        settings.smoothing_enable
        and settings.smoothing_repeat > 0
        and settings.smoothing_factor > 0.0
    )
    if smoothing_requested:
        matrix_weights, smoothed = _smooth_robust_matrix_weights(target, target_verts, matrix_weights, matched, settings)
        rescue_info = dict(rescue_info)
        rescue_info["smoothing_handled"] = True
        rescue_info["smoothed"] = bool(smoothed)
    rescue_info["matrix_context"] = True
    return RobustMatrixTransferResult(
        weights=_weight_values(matrix_weights[:, selected_column]),
        matched=matched,
        matched_count=matched_count,
        info=rescue_info,
    )


def _transfer_with_robust_single_group_fallback(context, settings, source_group_name):
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
    source_weight_max = float(_rwt.np_module().max(source_weights)) if source_weight_count > 0 else 0.0
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
    direct_matched = matched.copy()
    direct_weights = weights.copy()
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
            source_competition = None
            try:
                source_competition = _source_all_group_competition(
                    source_eval,
                    target,
                    source_verts,
                    source_faces,
                    source_normals,
                    target_verts,
                    target_faces,
                    target_normals,
                    settings,
                    source_group_name,
                )
            except Exception:
                source_competition = None
            authority_mask, authority_info = build_pre_write_authority_mask(
                target,
                fallback_weights,
                direct_matched,
                direct_weights,
                source_weight_max,
                source_competition,
            )
            fallback_weights = fallback_weights.copy()
            fallback_weights[~authority_mask] = 0.0
            rescue_info.update(authority_info)
            rescue_info["authority_mask"] = authority_mask
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
    source_competition = None
    try:
        source_competition = _source_all_group_competition(
            source_eval,
            target,
            source_verts,
            source_faces,
            source_normals,
            target_verts,
            target_faces,
            target_normals,
            settings,
            source_group_name,
        )
    except Exception:
        source_competition = None
    authority_mask, authority_info = build_pre_write_authority_mask(
        target,
        weights,
        direct_matched,
        direct_weights,
        source_weight_max,
        source_competition,
    )
    weights = weights.copy()
    weights[~authority_mask] = 0.0
    rescue_info.update(authority_info)
    rescue_info["authority_mask"] = authority_mask
    return _weight_values(weights), matched_for_inpaint, matched_count, rescue_info


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
    matrix_result = _transfer_with_robust_matrix_context(
        source,
        target,
        source_eval,
        target_verts,
        target_faces,
        target_normals,
        source_verts,
        source_faces,
        source_normals,
        settings,
        source_group_name,
    )
    if matrix_result is not None:
        return (
            matrix_result.weights,
            matrix_result.matched,
            matrix_result.matched_count,
            matrix_result.info,
        )
    return _transfer_with_robust_single_group_fallback(context, settings, source_group_name)


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


def weight_evidence_stats(weights, *, threshold=0.00001):
    np = _rwt.np_module()
    arr = np.asarray(weights, dtype=float).reshape(-1)
    if arr.size == 0:
        return 0, 0.0
    return int(np.count_nonzero(arr > threshold)), float(arr.max())


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


def _row_mask(np, rows, row_count):
    mask = np.zeros(row_count, dtype=bool)
    if rows is None:
        return mask
    arr = np.asarray(rows)
    if arr.dtype == bool:
        if arr.shape[0] != row_count:
            raise ValueError("顶点行掩码长度与网格顶点数不一致")
        return arr.astype(bool, copy=True)
    for value in arr.reshape(-1):
        index = int(value)
        if 0 <= index < row_count:
            mask[index] = True
    return mask


def combine_row_masks(row_count, *rows):
    np = _rwt.np_module()
    combined = np.zeros(row_count, dtype=bool)
    for row_set in rows:
        combined |= _row_mask(np, row_set, row_count)
    return combined


def _preserve_weight_rows(obj, group, proposed_weights, preserve_rows=None, preserve_weights=None):
    if preserve_rows is None:
        return proposed_weights
    np = _rwt.np_module()
    result = np.asarray(proposed_weights, dtype=float).reshape(-1).copy()
    preserve_mask = _row_mask(np, preserve_rows, len(obj.data.vertices))
    if not bool(np.any(preserve_mask)):
        return result
    if preserve_weights is None:
        original = read_group_weights(obj, group)
    else:
        original = np.asarray(preserve_weights, dtype=float).reshape(-1)
    if original.shape[0] != result.shape[0]:
        raise ValueError("保留权重长度与写入权重长度不一致")
    result[preserve_mask] = original[preserve_mask]
    return result


def locked_boundary_preserve_mask(
    obj,
    authority_groups,
    *,
    proposed_weights=None,
    original_weights=None,
    threshold=0.0001,
):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"锁定边界检测不可用: {error}")
    np = _rwt.np_module()
    row_count = len(obj.data.vertices)
    authority_indices = set()
    for group in authority_groups or ():
        if group is None:
            continue
        if is_special_vg_name(group.name):
            continue
        authority_indices.add(group.index)
    locked_indices = _ordinary_locked_group_indices(obj, exclude_indices=authority_indices)
    if not locked_indices:
        return np.zeros(row_count, dtype=bool)
    locked_weights = _rwt.get_groups_arr(obj, locked_indices)
    locked_active = np.any(locked_weights > threshold, axis=1)
    peer_indices = [
        group.index
        for group in obj.vertex_groups
        if group.index not in authority_indices
        and not group.lock_weight
        and not is_special_vg_name(group.name)
    ]
    if peer_indices:
        peer_weights = _rwt.get_groups_arr(obj, peer_indices)
        peer_active = np.any(peer_weights > threshold, axis=1)
    else:
        peer_active = np.zeros(row_count, dtype=bool)
    mask = locked_active & ~peer_active
    if proposed_weights is None:
        return mask
    proposed = np.asarray(proposed_weights, dtype=float).reshape(-1)
    if proposed.shape[0] != row_count:
        raise ValueError("候选权重长度与网格顶点数不一致")
    if original_weights is None:
        changed = proposed > threshold
    else:
        original = np.asarray(original_weights, dtype=float).reshape(-1)
        if original.shape[0] != row_count:
            raise ValueError("原始权重长度与网格顶点数不一致")
        changed = np.abs(proposed - original) > threshold
    return mask & changed


def apply_locked_boundary_preserve(obj, authority_groups, group, proposed_weights, *, original_weights=None, threshold=0.0001):
    original = original_weights
    if original is None:
        original = read_group_weights(obj, group)
    preserve_mask = locked_boundary_preserve_mask(
        obj,
        authority_groups,
        proposed_weights=proposed_weights,
        original_weights=original,
        threshold=threshold,
    )
    preserved = _preserve_weight_rows(
        obj,
        group,
        proposed_weights,
        preserve_mask,
        preserve_weights=original,
    )
    return preserved, preserve_mask


def apply_seam_safe_smoothing(obj, group, settings, matched=None, *, topology_cache=None, preserve_rows=None):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"Robust 平滑不可用: {error}")
    np = _rwt.np_module()
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
    preserve_mask = _row_mask(np, preserve_rows, len(obj.data.vertices))
    if bool(np.any(preserve_mask)):
        matched = np.asarray(matched, dtype=bool).copy()
        matched[preserve_mask] = False
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
    if bool(np.any(preserve_mask)):
        smoothed[preserve_mask, 0] = weights[preserve_mask, 0]
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


def _group_label(obj, group):
    obj_name = getattr(obj, "name", "") or "Mesh"
    group_name = getattr(group, "name", "") or "<unnamed>"
    return f"{obj_name}/{group_name}"


def _unique_editable_groups(groups, *, obj=None, action="写入"):
    result = []
    seen = set()
    for group in groups or ():
        if group is None:
            continue
        if getattr(group, "index", None) in seen:
            continue
        if getattr(group, "lock_weight", False):
            raise ValueError(f"{action}顶点组 '{_group_label(obj, group)}' 已锁定，请先解锁后再执行")
        if is_special_vg_name(group.name):
            continue
        result.append(group)
        seen.add(group.index)
    return result


def _blocked_by_protected_limit_error(max_groups):
    return (
        f"集合外权重已占满或超出每顶点 {max_groups} 组限制，"
        "无法安全保留本次权重；请解锁并纳入相关镜像组/供体，或暂时关闭限制后再执行。"
    )


def _ordinary_locked_group_indices(obj, exclude_indices=None):
    exclude = set(exclude_indices or ())
    return [
        group.index
        for group in obj.vertex_groups
        if group.index not in exclude
        and group.lock_weight
        and not is_special_vg_name(group.name)
    ]


def _yieldable_groups(obj, prefix_groups, *, exclude_indices=None):
    result = []
    seen = set(exclude_indices or ())
    for group in prefix_groups or ():
        if group is None or group.index in seen:
            continue
        if group.lock_weight:
            raise ValueError(f"顶点组 '{_group_label(obj, group)}' 已锁定，不能写入")
        if is_special_vg_name(group.name):
            continue
        result.append(group)
        seen.add(group.index)
    for group in obj.vertex_groups:
        if group.index in seen:
            continue
        if group.lock_weight or is_special_vg_name(group.name):
            continue
        result.append(group)
        seen.add(group.index)
    return result


def apply_limit_groups_scoped(obj, settings, authority_groups, *, priority_groups=None, preserve_rows=None):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"Robust limit 不可用: {error}")
    np = _rwt.np_module()
    authorities = _unique_editable_groups(authority_groups, obj=obj, action="限制")
    if not authorities:
        return ScopedLimitReport()
    priority_source = authorities[:1] if priority_groups is None else list(priority_groups or ())
    priorities = _unique_editable_groups(priority_source, obj=obj, action="限制")
    if not priorities:
        priorities = authorities[:1]
    priority_indices = [group.index for group in priorities]
    write_groups = _yieldable_groups(obj, authorities, exclude_indices=priority_indices)
    write_groups = priorities + write_groups
    write_indices = [group.index for group in write_groups]
    priority_count = len(priorities)
    row_count = len(obj.data.vertices)
    preserve_mask = _row_mask(np, preserve_rows, row_count)
    max_groups = max(0, int(getattr(settings, "max_groups_per_vertex", 0)))
    if max_groups <= 0:
        current = _rwt.get_groups_arr(obj, write_indices)
        weights = current.copy()
        weights[~preserve_mask, :] = 0.0
        changed_rows = int(np.count_nonzero(np.any(np.abs(weights - current) > 1e-8, axis=1)))
        if changed_rows:
            write_groups_by_indices(obj, write_indices, weights)
        return ScopedLimitReport(changed=bool(changed_rows), authority_limited_vertices=changed_rows)
    locked_indices = _ordinary_locked_group_indices(obj, exclude_indices=write_indices)
    write_weights = _rwt.get_groups_arr(obj, write_indices)
    write_weights[write_weights <= 0.0001] = 0.0
    if locked_indices:
        locked_weights = _rwt.get_groups_arr(obj, locked_indices)
        locked_weights[locked_weights <= 0.0001] = 0.0
        locked_counts = np.count_nonzero(locked_weights, axis=1)
    else:
        locked_counts = np.zeros(len(obj.data.vertices), dtype=np.int64)
    priority_weights = write_weights[:, :priority_count]
    focus_rows = np.any(priority_weights > 0.0001, axis=1)
    focus_rows &= ~preserve_mask
    slots = np.maximum(0, max_groups - locked_counts).astype(int)
    limited = write_weights.copy()
    row_count = write_weights.shape[0]
    changed_rows = 0
    for row in range(row_count):
        if not bool(focus_rows[row]):
            continue
        keep = int(slots[row])
        row_weights = write_weights[row]
        limited[row, :] = 0.0
        priority_nonzero = np.flatnonzero(row_weights[:priority_count] > 0.0001)
        secondary_nonzero = priority_count + np.flatnonzero(row_weights[priority_count:] > 0.0001)
        nonzero = list(priority_nonzero) + list(secondary_nonzero)
        if keep <= 0:
            if len(nonzero):
                changed_rows += 1
            continue
        priority_order = sorted(priority_nonzero, key=lambda column: (-float(row_weights[column]), column))
        keep_columns = priority_order[:keep]
        remaining = keep - len(keep_columns)
        if remaining > 0:
            secondary_order = sorted(secondary_nonzero, key=lambda column: (-float(row_weights[column]), column))
            keep_columns.extend(secondary_order[:remaining])
        limited[row, keep_columns] = row_weights[keep_columns]
        if len(keep_columns) < len(nonzero):
            changed_rows += 1
    changed_columns = np.flatnonzero(np.any(np.abs(limited - write_weights) > 1e-8, axis=0))
    if len(changed_columns) == 0:
        return ScopedLimitReport(
            protected_over_limit_vertices=int(np.count_nonzero((locked_counts > max_groups) & focus_rows)),
        )
    changed_indices = [write_indices[int(column)] for column in changed_columns]
    write_groups_by_indices(obj, changed_indices, limited[:, changed_columns])
    return ScopedLimitReport(
        changed=True,
        protected_over_limit_vertices=int(np.count_nonzero((locked_counts > max_groups) & focus_rows)),
        authority_limited_vertices=int(changed_rows),
    )


def write_groups_by_indices(obj, group_indices, weights):
    for column, group_index in enumerate(group_indices):
        group = obj.vertex_groups[group_index]
        if group.lock_weight or is_special_vg_name(group.name):
            continue
        write_group_weights(obj, group, weights[:, column])


def _plain_group_weights(obj, group):
    weights = [0.0] * len(obj.data.vertices)
    group_index = group.index
    for vertex in obj.data.vertices:
        for item in vertex.groups:
            if item.group == group_index:
                weights[vertex.index] = float(item.weight)
                break
    return weights


def _coord_bucket_key(co, tolerance):
    return (
        int(round(float(co.x) / tolerance)),
        int(round(float(co.y) / tolerance)),
        int(round(float(co.z) / tolerance)),
    )


def _coord_tuple(co):
    return (float(co.x), float(co.y), float(co.z))


def _bucket_vertices_by_coordinate(obj, tolerance):
    buckets = {}
    for vertex in obj.data.vertices:
        key = _coord_bucket_key(vertex.co, tolerance)
        buckets.setdefault(key, []).append(vertex.index)
    return buckets


def mirror_group_weights(
    obj,
    source_group,
    mirror_group,
    *,
    tolerance=None,
    threshold=0.00001,
    preserve_rows=None,
    preserve_weights=None,
):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        raise ValueError("镜像权重需要 Mesh 对象")
    if source_group is None or mirror_group is None:
        raise ValueError("镜像权重缺少来源组或镜像组")
    if mirror_group.lock_weight:
        raise ValueError(f"镜像顶点组 '{obj.name}/{mirror_group.name}' 已锁定，不能写入")
    diagonal = _bbox_diagonal_local(obj)
    if diagonal <= 1e-12:
        raise ValueError(f"当前对象 '{obj.name}' 没有有效包围盒，无法镜像权重")
    if tolerance is None:
        tolerance = max(diagonal * 0.0001, 0.000001)
    source_weights = _plain_group_weights(obj, source_group)
    source_buckets = _bucket_vertices_by_coordinate(obj, tolerance)
    bucket_centers = []
    bucket_weights = []
    for indices in source_buckets.values():
        if not indices:
            continue
        x = y = z = weight_sum = 0.0
        for index in indices:
            co = obj.data.vertices[index].co
            x += float(co.x)
            y += float(co.y)
            z += float(co.z)
            weight_sum += float(source_weights[index])
        scale = 1.0 / len(indices)
        bucket_centers.append((x * scale, y * scale, z * scale))
        bucket_weights.append(weight_sum * scale)
    if not bucket_centers:
        mirrored_weights = _preserve_weight_rows(
            obj,
            mirror_group,
            [0.0] * len(obj.data.vertices),
            preserve_rows,
            preserve_weights=preserve_weights,
        )
        write_group_weights(obj, mirror_group, mirrored_weights)
        return {"matched_buckets": 0, "matched_vertices": 0, "coincident_buckets": 0, "tolerance": tolerance}

    tree = kdtree.KDTree(len(bucket_centers))
    for index, center in enumerate(bucket_centers):
        tree.insert(Vector(center), index)
    tree.balance()

    target_buckets = _bucket_vertices_by_coordinate(obj, tolerance)
    mirrored_weights = [0.0] * len(obj.data.vertices)
    matched_buckets = 0
    matched_vertices = 0
    coincident_buckets = 0
    for indices in target_buckets.values():
        if not indices:
            continue
        x = y = z = 0.0
        for index in indices:
            co = obj.data.vertices[index].co
            x += float(co.x)
            y += float(co.y)
            z += float(co.z)
        scale = 1.0 / len(indices)
        center = (x * scale, y * scale, z * scale)
        mirror_center = Vector((-center[0], center[1], center[2]))
        _co, source_bucket_index, distance = tree.find(mirror_center)
        if source_bucket_index is None or distance is None or float(distance) > tolerance:
            continue
        value = float(bucket_weights[int(source_bucket_index)])
        if value <= threshold:
            continue
        matched_buckets += 1
        if len(indices) > 1:
            coincident_buckets += 1
        for index in indices:
            mirrored_weights[index] = value
            matched_vertices += 1
    mirrored_weights = _preserve_weight_rows(
        obj,
        mirror_group,
        mirrored_weights,
        preserve_rows,
        preserve_weights=preserve_weights,
    )
    write_group_weights(obj, mirror_group, mirrored_weights, threshold=threshold)
    return {
        "matched_buckets": matched_buckets,
        "matched_vertices": matched_vertices,
        "coincident_buckets": coincident_buckets,
        "tolerance": tolerance,
    }


def mirrored_donor_groups(context, settings, obj, donor_groups, *, mirror_names=None, exclude_names=None):
    exclude = {_clean_name(name) for name in (exclude_names or ()) if _clean_name(name)}
    result = []
    seen = set()
    donor_groups = list(donor_groups or ())
    override_names = list(mirror_names or ())
    use_override_names = mirror_names is not None
    for slot_index, donor in enumerate(donor_groups):
        if donor is None:
            continue
        if use_override_names:
            mirror_name = _clean_name(override_names[slot_index] if slot_index < len(override_names) else "")
            if not mirror_name:
                raise ValueError(f"供体 {slot_index + 1} '{donor.name}' 未指定镜像供体，无法执行镜像规格化")
        else:
            resolution = resolve_mirror_group(context, settings, obj, donor.name)
            mirror_name = resolution.mirror_name
            if not mirror_name:
                raise ValueError(f"供体 '{donor.name}' 未找到镜像供体，无法执行镜像规格化")
        if mirror_name in exclude:
            raise ValueError(f"供体 '{donor.name}' 的镜像组 '{mirror_name}' 是本次权威组，不能作为供体")
        mirror_group = obj.vertex_groups.get(mirror_name)
        if mirror_group is None:
            raise ValueError(f"供体 '{donor.name}' 的镜像组 '{mirror_name}' 不存在")
        if mirror_group.lock_weight:
            raise ValueError(f"镜像供体 '{obj.name}/{mirror_group.name}' 已锁定，请先解锁后再执行")
        if is_special_vg_name(mirror_group.name):
            raise ValueError(f"镜像供体 '{obj.name}/{mirror_group.name}' 是 Velo 特殊组，不能参与规格化")
        if count_group_weights(obj, mirror_group) <= 0:
            raise ValueError(f"镜像供体 '{obj.name}/{mirror_group.name}' 没有非零权重，不能参与规格化")
        if mirror_group.index in seen:
            continue
        seen.add(mirror_group.index)
        result.append(mirror_group)
    return result


def auto_donor_pair_eligibility(context, settings, obj, donor_groups, *, exclude_names=None):
    exclude = {_clean_name(name) for name in (exclude_names or ()) if _clean_name(name)}
    result = DonorPairEligibility()
    seen_donors = set()
    seen_mirrors = set()
    for donor in donor_groups or ():
        if donor is None or getattr(donor, "index", None) in seen_donors:
            continue
        donor_name = _clean_name(getattr(donor, "name", ""))
        if not donor_name:
            continue
        resolution = resolve_mirror_group(context, settings, obj, donor_name)
        mirror_name = _clean_name(resolution.mirror_name)
        pair_label = f"{donor_name} ↔ {mirror_name or '?'}"
        if getattr(donor, "lock_weight", False):
            result.skipped_locked_pairs.append(pair_label)
            continue
        if is_special_vg_name(donor_name) or donor_name in exclude:
            result.skipped_unavailable_pairs.append(pair_label)
            continue
        if count_group_weights(obj, donor) <= 0:
            result.skipped_unavailable_pairs.append(pair_label)
            continue

        if not mirror_name or mirror_name in exclude:
            result.skipped_unavailable_pairs.append(pair_label)
            continue
        mirror_group = obj.vertex_groups.get(mirror_name)
        if mirror_group is None:
            result.skipped_unavailable_pairs.append(pair_label)
            continue
        if getattr(mirror_group, "lock_weight", False):
            result.skipped_locked_pairs.append(pair_label)
            continue
        if is_special_vg_name(mirror_group.name):
            result.skipped_unavailable_pairs.append(pair_label)
            continue
        if count_group_weights(obj, mirror_group) <= 0:
            result.skipped_unavailable_pairs.append(pair_label)
            continue
        if getattr(mirror_group, "index", None) in seen_mirrors:
            result.skipped_unavailable_pairs.append(pair_label)
            continue

        seen_donors.add(donor.index)
        seen_mirrors.add(mirror_group.index)
        result.donors.append(donor)
        result.mirror_donors.append(mirror_group)
    return result


def _unique_normalize_groups(obj, groups, *, label):
    result = []
    seen = set()
    for group in groups or ():
        if group is None or is_special_vg_name(group.name) or group.index in seen:
            continue
        if group.lock_weight:
            raise ValueError(f"{label}顶点组 '{_group_label(obj, group)}' 已锁定，请先解锁后再执行")
        result.append(group)
        seen.add(group.index)
    return result


def _normalize_authority_priority_groups(obj, authority_groups, secondary_groups, *, preserve_rows=None, tolerance=0.0001):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"规格化不可用: {error}")
    np = _rwt.np_module()
    authorities = _unique_normalize_groups(obj, authority_groups, label="规格化")
    if not authorities:
        return NormalizationReport()
    authority_count = len(authorities)
    secondary = _yieldable_groups(
        obj,
        _unique_normalize_groups(obj, secondary_groups, label="规格化"),
        exclude_indices=[group.index for group in authorities],
    )
    participant = authorities + secondary
    participant_indices = [group.index for group in participant]
    participant_weights = _rwt.get_groups_arr(obj, participant_indices)
    row_count = participant_weights.shape[0]
    preserve_mask = _row_mask(np, preserve_rows, row_count)
    locked_indices = _ordinary_locked_group_indices(obj, exclude_indices=participant_indices)
    if locked_indices:
        locked_sum = _rwt.get_groups_arr(obj, locked_indices).sum(axis=1)
    else:
        locked_sum = np.zeros(row_count, dtype=np.float32)
    available = np.maximum(0.0, 1.0 - locked_sum)
    authority_weights = participant_weights[:, :authority_count]
    authority_totals = authority_weights.sum(axis=1)
    secondary_start = authority_count
    if participant_weights.shape[1] > secondary_start:
        secondary_weights = participant_weights[:, secondary_start:]
        secondary_totals = secondary_weights.sum(axis=1)
    else:
        secondary_weights = np.zeros((row_count, 0), dtype=participant_weights.dtype)
        secondary_totals = np.zeros(row_count, dtype=participant_weights.dtype)
    focus_rows = (authority_totals > tolerance) & ~preserve_mask
    normalized = participant_weights.copy()
    if bool(np.any(focus_rows)):
        over_authority = focus_rows & (authority_totals > available + tolerance)
        if bool(np.any(over_authority)):
            authority_scale = np.divide(
                available,
                authority_totals,
                out=np.zeros_like(available),
                where=(over_authority & (authority_totals > tolerance)),
            )
            normalized[over_authority, :authority_count] = (
                authority_weights[over_authority] * authority_scale[over_authority].reshape(-1, 1)
            )
            if participant_weights.shape[1] > secondary_start:
                normalized[over_authority, secondary_start:] = 0.0
        fit_authority = focus_rows & ~over_authority
        normalized[fit_authority, :authority_count] = authority_weights[fit_authority]
        if participant_weights.shape[1] > secondary_start:
            secondary_available = np.maximum(0.0, available - authority_totals)
            secondary_scale = np.divide(
                secondary_available,
                secondary_totals,
                out=np.zeros_like(secondary_available),
                where=(fit_authority & (secondary_totals > tolerance)),
            )
            normalized[fit_authority, secondary_start:] = (
                secondary_weights[fit_authority] * secondary_scale[fit_authority].reshape(-1, 1)
            )
    changed_columns = np.flatnonzero(np.any(np.abs(normalized - participant_weights) > 1e-8, axis=0))
    if len(changed_columns):
        changed_indices = [participant_indices[int(column)] for column in changed_columns]
        write_groups_by_indices(obj, changed_indices, normalized[:, changed_columns])
    totals = locked_sum + normalized.sum(axis=1)
    under_rows = focus_rows & (totals < (1.0 - tolerance))
    over_rows = focus_rows & (locked_sum > (1.0 + tolerance))
    no_yieldable_rows = under_rows & (secondary_totals <= tolerance)
    remaining_under_rows = under_rows & ~no_yieldable_rows
    problem_rows = remaining_under_rows | over_rows
    resolved_rows = focus_rows & ~(problem_rows | no_yieldable_rows)
    return NormalizationReport(
        attempted=True,
        changed=bool(len(changed_columns)),
        normalized_vertices=int(np.count_nonzero(resolved_rows)),
        no_yieldable_vertices=int(np.count_nonzero(no_yieldable_rows)),
        under_normalized_vertices=int(np.count_nonzero(remaining_under_rows)),
        over_capacity_vertices=int(np.count_nonzero(over_rows)),
        problem_vertices=[int(index) for index in np.flatnonzero(problem_rows)],
    )


def normalize_authority_groups_with_donors(obj, authority_groups, donor_groups, *, preserve_rows=None):
    return _normalize_authority_priority_groups(obj, authority_groups, donor_groups, preserve_rows=preserve_rows)


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


def mirror_name_candidates(name):
    cleaned = _clean_name(name)
    if not cleaned:
        return []
    candidates = []
    lowered = cleaned.lower()
    suffix_pairs = (
        (".l", ".R"),
        (".r", ".L"),
        ("_l", "_R"),
        ("_r", "_L"),
        ("-l", "-R"),
        ("-r", "-L"),
    )
    for suffix, replacement in suffix_pairs:
        if lowered.endswith(suffix):
            candidates.append(cleaned[:-len(suffix)] + replacement)
            candidates.append(cleaned[:-len(suffix)] + replacement.lower())
            break
    token_pairs = (
        ("左", "右"),
        ("右", "左"),
        ("Left", "Right"),
        ("Right", "Left"),
        ("left", "right"),
        ("right", "left"),
        ("LEFT", "RIGHT"),
        ("RIGHT", "LEFT"),
    )
    for left, right in token_pairs:
        if left in cleaned:
            candidates.append(cleaned.replace(left, right, 1))
            break
    return _unique_names(*candidates)


def _existing_mirror_name_from_alias(obj, name):
    for candidate in mirror_name_candidates(name):
        group = obj.vertex_groups.get(candidate)
        if group is not None and group.name != name:
            return group.name
    return ""


def _manual_mirror_name(settings, group_name):
    cleaned = _clean_name(group_name)
    if not cleaned:
        return ""
    for row in getattr(settings, "mirror_mappings", ()):
        left = _clean_name(getattr(row, "left_group", ""))
        right = _clean_name(getattr(row, "right_group", ""))
        if not left or not right or left == right:
            continue
        if cleaned == left:
            return right
        if cleaned == right:
            return left
    return ""


def add_mirror_mapping(settings, left_name, right_name):
    left = _clean_name(left_name)
    right = _clean_name(right_name)
    if not left or not right or left == right:
        return False
    for row in getattr(settings, "mirror_mappings", ()):
        a = _clean_name(getattr(row, "left_group", ""))
        b = _clean_name(getattr(row, "right_group", ""))
        if {a, b} == {left, right}:
            row.left_group = left
            row.right_group = right
            return False
    row = settings.mirror_mappings.add()
    row.left_group = left
    row.right_group = right
    settings.active_mirror_mapping_index = len(settings.mirror_mappings) - 1
    return True


def should_persist_transfer_mirror_mapping(context, settings, obj, left_name, right_name):
    left = _clean_name(left_name)
    right = _clean_name(right_name)
    if not left or not right or left == right:
        return False
    resolution = resolve_mirror_group(context, settings, obj, left)
    if resolution.mirror_name == right and resolution.reason in {"命名镜像匹配", "MMD 映射镜像匹配"}:
        return False
    return True


def _mmd_profile_mirror_name(context, obj, name):
    ef, profile = _mmd_profile(context)
    if profile is None or obj is None:
        return ""
    rows = []
    for row in getattr(profile, "rows", ()):
        names = _row_names(row)
        values = _unique_names(names["current"], names["mmd"], names["unified"])
        if not values:
            continue
        rows.append(values)
    wanted = set(mirror_name_candidates(name))
    if not wanted:
        return ""
    for values in rows:
        if not wanted.intersection(values):
            continue
        existing = _first_existing_group_name(obj, values)
        if existing and existing != name:
            return existing
    return ""


def _bbox_diagonal_local(obj):
    vertices = getattr(getattr(obj, "data", None), "vertices", ())
    if not vertices:
        return 0.0
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    for vertex in vertices:
        co = vertex.co
        for axis, value in enumerate((co.x, co.y, co.z)):
            value = float(value)
            mins[axis] = min(mins[axis], value)
            maxs[axis] = max(maxs[axis], value)
    if mins[0] == float("inf"):
        return 0.0
    dx = maxs[0] - mins[0]
    dy = maxs[1] - mins[1]
    dz = maxs[2] - mins[2]
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def _group_centroid_local(obj, group, *, threshold=0.00001):
    total = 0.0
    x = 0.0
    y = 0.0
    z = 0.0
    group_index = group.index
    for vertex in obj.data.vertices:
        weight = 0.0
        for item in vertex.groups:
            if item.group == group_index:
                weight = float(item.weight)
                break
        if weight <= threshold:
            continue
        co = vertex.co
        x += float(co.x) * weight
        y += float(co.y) * weight
        z += float(co.z) * weight
        total += weight
    if total <= threshold:
        return None, 0.0
    return (x / total, y / total, z / total), total


def _mirror_centroid_error(source_center, candidate_center, diagonal):
    if diagonal <= 1e-12:
        diagonal = 1.0
    dx = -float(source_center[0]) - float(candidate_center[0])
    dy = float(source_center[1]) - float(candidate_center[1])
    dz = float(source_center[2]) - float(candidate_center[2])
    return ((dx * dx + dy * dy + dz * dz) ** 0.5) / diagonal


def _auto_numeric_mirror_name(obj, group_name, *, max_error=0.001, min_side_ratio=0.005):
    if obj is None or getattr(obj, "type", None) != 'MESH' or not _clean_name(group_name).isdigit():
        return "", 0.0
    source_group = obj.vertex_groups.get(_clean_name(group_name))
    if source_group is None:
        return "", 0.0
    source_center, source_total = _group_centroid_local(obj, source_group)
    if source_center is None:
        return "", 0.0
    diagonal = _bbox_diagonal_local(obj)
    if diagonal <= 1e-12:
        return "", 0.0
    min_side = diagonal * min_side_ratio
    if abs(float(source_center[0])) <= min_side:
        return "", 0.0
    best = None
    for group in obj.vertex_groups:
        if group.index == source_group.index:
            continue
        if not _clean_name(group.name).isdigit():
            continue
        if group.lock_weight or is_special_vg_name(group.name):
            continue
        candidate_center, candidate_total = _group_centroid_local(obj, group)
        if candidate_center is None:
            continue
        if abs(float(candidate_center[0])) <= min_side:
            continue
        if float(source_center[0]) * float(candidate_center[0]) >= 0.0:
            continue
        total_ratio = min(source_total, candidate_total) / max(source_total, candidate_total, 1e-12)
        if total_ratio < 0.25:
            continue
        error = _mirror_centroid_error(source_center, candidate_center, diagonal)
        if error > max_error:
            continue
        score = (error, -total_ratio, group.index)
        if best is None or score < best[0]:
            best = (score, group.name, error)
    if best is None:
        return "", 0.0
    return best[1], max(0.0, 1.0 - float(best[2]))


def resolve_mirror_group(context, settings, obj, group_name):
    cleaned = _clean_name(group_name)
    if obj is None or getattr(obj, "type", None) != 'MESH' or not cleaned:
        return MirrorGroupResolution(source_name=cleaned, reason="缺少可解析的网格或顶点组")
    group = obj.vertex_groups.get(cleaned)
    if group is None:
        return MirrorGroupResolution(source_name=cleaned, reason=f"当前网格不存在顶点组 '{cleaned}'")

    manual = _manual_mirror_name(settings, cleaned)
    if manual and obj.vertex_groups.get(manual) is not None:
        return MirrorGroupResolution(cleaned, manual, "手动镜像映射", 1.0, False)

    alias = _existing_mirror_name_from_alias(obj, cleaned)
    if alias:
        return MirrorGroupResolution(cleaned, alias, "命名镜像匹配", 0.95, True)

    mapped = _mmd_profile_mirror_name(context, obj, cleaned)
    if mapped:
        return MirrorGroupResolution(cleaned, mapped, "MMD 映射镜像匹配", 0.9, True)

    numeric, confidence = _auto_numeric_mirror_name(obj, cleaned)
    if numeric:
        return MirrorGroupResolution(cleaned, numeric, "数字组权重重心镜像匹配", confidence, True)

    return MirrorGroupResolution(source_name=cleaned, reason="未找到可信镜像顶点组")


def resolve_transfer_mirror_group_name(context, settings, target, target_group_name, fallback_name=""):
    override = ""
    if getattr(settings, "manual_mirror_target_group_name", False):
        override = _clean_name(getattr(settings, "mirror_target_group_name", ""))
    if override:
        return override
    mirror_resolution = resolve_mirror_group(context, settings, target, target_group_name)
    return mirror_resolution.mirror_name or _clean_name(fallback_name)


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


def _vector_length(value):
    length = getattr(value, "length", None)
    if length is not None:
        return float(length)
    try:
        return float(sum(float(part) * float(part) for part in value) ** 0.5)
    except Exception:
        return 0.0


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
            center_distance = _vector_length(target_center - source_center)
            side_score = _side_affinity(vg.name, target_group.name)
            candidates.append((round(mean_distance, 4), -side_score, mean_distance, center_distance, -total, vg.index))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4], item[5]))
    return [obj.vertex_groups[index] for _bucket, _side, _mean_distance, _center_distance, _neg_total, index in candidates[:count]]


def _prefilter_donor_candidates(obj, candidate_groups, target_weights, preferred_side, preferred_names, count, np):
    if not candidate_groups:
        return []
    target_weights = np.asarray(target_weights, dtype=float).reshape(-1)
    target_mask = target_weights > 0.00001
    if len(target_weights) != len(obj.data.vertices) or int(np.count_nonzero(target_mask)) <= 0:
        return []
    preferred_names = {_clean_name(name) for name in (preferred_names or ()) if _clean_name(name)}
    candidate_by_index = {group.index: group for group in candidate_groups}
    stats = {
        group.index: {
            "total": 0.0,
            "focus_sum": 0.0,
            "overlap": 0.0,
            "shared_vertices": 0,
            "weighted_center": Vector((0.0, 0.0, 0.0)),
        }
        for group in candidate_groups
    }
    coords = [obj.matrix_world @ vertex.co for vertex in obj.data.vertices] if hasattr(obj, "matrix_world") else None
    target_center = None
    if coords is not None:
        target_indices = np.flatnonzero(target_mask)
        target_center = _mean_vector(coords, target_indices)
    saw_memberships = False
    for vertex_index, vertex in enumerate(obj.data.vertices):
        groups = getattr(vertex, "groups", None)
        if groups is None:
            continue
        saw_memberships = True
        target_weight = float(target_weights[vertex_index])
        is_focus = target_weight > 0.00001
        for item in groups:
            group_index = getattr(item, "group", None)
            stat = stats.get(group_index)
            if stat is None:
                continue
            weight = float(getattr(item, "weight", 0.0))
            if weight <= 0.00001:
                continue
            stat["total"] += weight
            if is_focus:
                stat["focus_sum"] += weight
                stat["overlap"] += min(weight, target_weight)
                stat["shared_vertices"] += 1
            if coords is not None:
                stat["weighted_center"] = stat["weighted_center"] + (coords[vertex_index] * weight)
    if not saw_memberships:
        for group in candidate_groups:
            weights = np.asarray(read_group_weights(obj, group), dtype=float).reshape(-1)
            if len(weights) != len(target_weights):
                continue
            positive = weights > 0.00001
            stat = stats[group.index]
            stat["total"] = float(weights[positive].sum())
            focus = positive & target_mask
            stat["focus_sum"] = float(weights[target_mask].sum())
            stat["overlap"] = float(np.minimum(weights, target_weights).sum())
            stat["shared_vertices"] = int(np.count_nonzero(focus))
    scored = []
    for group in candidate_groups:
        stat = stats[group.index]
        total = float(stat["total"])
        if total <= 0.0:
            continue
        shared_vertices = int(stat["shared_vertices"])
        overlap = float(stat["overlap"])
        focus_sum = float(stat["focus_sum"])
        focus_ratio = focus_sum / total if total > 1e-8 else 0.0
        side_match = 1 if preferred_side in {"L", "R"} and _name_side_suffix(group.name) == preferred_side else 0
        preferred = 1 if group.name in preferred_names else 0
        center_distance = float("inf")
        if target_center is not None:
            center = stat["weighted_center"] / total
            center_distance = _vector_length(target_center - center)
        scored.append((
            preferred,
            side_match,
            overlap,
            focus_ratio,
            focus_sum,
            shared_vertices,
            -center_distance,
            -total,
            group.index,
        ))
    if not scored:
        return []
    local_multiplier = 16
    pool_size = max(count * local_multiplier, count + len(preferred_names), 32)
    scored.sort(key=lambda item: item[:-1], reverse=True)
    selected_indices = [item[-1] for item in scored[:pool_size]]
    selected = set(selected_indices)
    for item in scored:
        index = item[-1]
        if item[0] and index not in selected:
            selected_indices.append(index)
            selected.add(index)
    return [obj.vertex_groups[index] for index in selected_indices]


def _weights_center_world(obj, weights, np, *, threshold=0.00001):
    if weights is None or len(weights) != len(obj.data.vertices):
        return None
    indices = np.flatnonzero(weights > threshold)
    if len(indices) == 0:
        return None
    total = 0.0
    center = Vector((0.0, 0.0, 0.0))
    for index in indices:
        weight = float(weights[int(index)])
        center += (obj.matrix_world @ obj.data.vertices[int(index)].co) * weight
        total += weight
    if total <= threshold:
        return None
    return center / total


def infer_donor_side(obj, target_group=None, *, focus_weights=None, candidate_groups=None):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return ""
    side_from_name = _name_side_suffix(getattr(target_group, "name", ""))
    ok, _error = _rwt.ensure_available()
    np = _rwt.np_module() if ok else None
    reference_center = _weights_center_world(obj, focus_weights, np) if np is not None else None
    if reference_center is None and target_group is not None and getattr(target_group, "index", -1) >= 0:
        reference_center, _normal = group_weighted_centroid_world(obj, target_group.name)
    if reference_center is None:
        return side_from_name

    groups = list(candidate_groups) if candidate_groups is not None else list(getattr(obj, "vertex_groups", ()))
    side_centers = {"L": [], "R": []}
    for group in groups:
        side = _name_side_suffix(group.name)
        if side not in side_centers:
            continue
        center, _normal = group_weighted_centroid_world(obj, group.name)
        if center is not None:
            side_centers[side].append(center)
    if side_centers["L"] and side_centers["R"]:
        means = {}
        for side, centers in side_centers.items():
            total = Vector((0.0, 0.0, 0.0))
            for center in centers:
                total += center
            means[side] = total / len(centers)
        distances = {side: float((reference_center - center).length) for side, center in means.items()}
        if abs(distances["L"] - distances["R"]) > 1e-6:
            return "L" if distances["L"] < distances["R"] else "R"
    return side_from_name


def source_group_target_focus_weights(context, settings, source_group_name, *, threshold=0.00001):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"供体来源覆盖范围不可用: {error}")
    source = getattr(settings, "source_object", None)
    target = getattr(settings, "target_object", None)
    if source is None or target is None:
        return None
    depsgraph = context.evaluated_depsgraph_get()
    source_eval = source.evaluated_get(depsgraph) if getattr(settings, "use_deformed_source", True) else source
    target_eval = target.evaluated_get(depsgraph) if getattr(settings, "use_deformed_target", True) else target
    source_verts, source_faces, source_normals = _rwt.get_obj_arrs_world(source_eval)
    target_verts, target_faces, target_normals = _rwt.get_obj_arrs_world(target_eval)
    if len(target_verts) != len(target.data.vertices) or len(source_faces) == 0 or len(target_faces) == 0:
        return None
    source_weights = _rwt.get_group_arr(source_eval, source_group_name)
    matched, weights = _rwt.find_matches_closest_surface(
        source_verts,
        source_faces,
        source_normals,
        target_verts,
        target_normals,
        source_weights,
        float(getattr(settings, "robust_max_distance", 0.05)) ** 2,
        _rwt.degrees(float(getattr(settings, "robust_normal_angle", 1.0471975511965976))),
        bool(getattr(settings, "robust_flip_normals", True)),
    )
    np = _rwt.np_module()
    flat_weights = _weight_values(weights)
    focus_weights = np.zeros(len(target.data.vertices), dtype=float)
    mask = (matched == True) & (flat_weights > threshold)
    if int(np.count_nonzero(mask)) <= 0:
        return None
    focus_weights[mask] = flat_weights[mask]
    return focus_weights


def _side_filtered_groups(groups, preferred_side, count):
    if preferred_side not in {"L", "R"}:
        return list(groups)
    matching = [group for group in groups if _name_side_suffix(group.name) == preferred_side]
    allow_unsided = len(matching) < count
    result = []
    for group in groups:
        side = _name_side_suffix(group.name)
        if side and side != preferred_side:
            continue
        if not side and not allow_unsided:
            continue
        result.append(group)
    return result


def _apply_donor_dominance_gate(candidates):
    if len(candidates) <= 1:
        return candidates
    leader = candidates[0]
    leader_overlap = max(0.0, float(leader[2]))
    leader_focus_sum = max(0.0, float(leader[4]))
    leader_shared = max(0, int(leader[5]))
    if leader_overlap <= 0.0 and leader_focus_sum <= 0.0:
        return candidates[:1]
    filtered = [leader]
    for candidate in candidates[1:]:
        preferred = bool(candidate[0])
        overlap = max(0.0, float(candidate[2]))
        focus_ratio = max(0.0, float(candidate[3]))
        focus_sum = max(0.0, float(candidate[4]))
        shared = max(0, int(candidate[5]))
        if preferred and (overlap > 0.0 or focus_sum > 0.0 or shared > 0):
            filtered.append(candidate)
            continue
        if leader_overlap > 0.0 and overlap >= leader_overlap * _DONOR_DOMINANCE_RATIO:
            filtered.append(candidate)
            continue
        if leader_focus_sum > 0.0 and focus_sum >= leader_focus_sum * _DONOR_DOMINANCE_RATIO:
            filtered.append(candidate)
            continue
        shared_floor = max(_DONOR_SHARED_VERTEX_MIN, int(leader_shared * _DONOR_SHARED_VERTEX_RATIO))
        if (
            leader_focus_sum > 0.0
            and shared >= shared_floor
            and focus_ratio >= _DONOR_SHARED_FOCUS_RATIO
            and focus_sum >= leader_focus_sum * (_DONOR_DOMINANCE_RATIO * 0.75)
        ):
            filtered.append(candidate)
    return filtered


def select_auto_donors(
    obj,
    target_group,
    count,
    *,
    exclude_group_names=None,
    preferred_names=None,
    strict_preferred=False,
    focus_weights=None,
    preferred_side=None,
    include_locked_candidates=False,
):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"供体选择不可用: {error}")
    np = _rwt.np_module()
    count = max(int(count), 0)
    if count == 0:
        return []
    exclude_group_names = {_clean_name(name) for name in (exclude_group_names or ()) if _clean_name(name)}
    preferred_names = [_clean_name(name) for name in (preferred_names or ()) if _clean_name(name)]
    preferred_name_set = set(preferred_names)
    raw_candidate_groups = []
    for vg in obj.vertex_groups:
        if vg.index == target_group.index:
            continue
        if vg.lock_weight and not include_locked_candidates:
            continue
        if _clean_name(vg.name) in exclude_group_names:
            continue
        if is_special_vg_name(vg.name):
            continue
        raw_candidate_groups.append(vg)
    if preferred_side not in {"L", "R"}:
        preferred_side = _name_side_suffix(getattr(target_group, "name", ""))
    candidate_groups = _side_filtered_groups(raw_candidate_groups, preferred_side, count)
    preferred = []
    seen_preferred = set()
    for cleaned in preferred_names:
        if cleaned in seen_preferred or cleaned in exclude_group_names:
            continue
        group = obj.vertex_groups.get(cleaned)
        if group is None or group.index == target_group.index:
            continue
        if group.lock_weight and not include_locked_candidates:
            continue
        if is_special_vg_name(group.name):
            continue
        if group not in candidate_groups:
            continue
        if count_group_weights(obj, group) <= 0:
            continue
        preferred.append(group)
        seen_preferred.add(cleaned)
        if len(preferred) >= count:
            break
    if preferred and strict_preferred:
        return preferred
    target_weights = focus_weights if focus_weights is not None else read_group_weights(obj, target_group)
    target_weights = np.asarray(target_weights, dtype=float).reshape(-1)
    if len(target_weights) != len(obj.data.vertices):
        return preferred
    target_mask = target_weights > 0.00001
    if int(np.count_nonzero(target_mask)) <= 0:
        return preferred
    if not candidate_groups:
        return preferred
    candidate_pool = _prefilter_donor_candidates(
        obj,
        candidate_groups,
        target_weights,
        preferred_side,
        preferred_names,
        count,
        np,
    )
    if candidate_pool:
        candidate_groups = candidate_pool
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
            preferred_score = 1 if vg.name in preferred_name_set else 0
            side_score = 1 if preferred_side in {"L", "R"} and _name_side_suffix(vg.name) == preferred_side else 0
            candidates.append((preferred_score, side_score, overlap, focus_ratio, focus_sum, shared_vertices, -total, vg.index))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4], item[5], item[6]), reverse=True)
    candidates = _apply_donor_dominance_gate(candidates)
    selected_indices = []
    selected_indices.extend(
        index
        for _preferred, _side, _overlap, _focus_ratio, _focus_sum, _shared, _neg_total, index in candidates
        if index not in selected_indices
    )
    selected_indices = selected_indices[:count]
    return [obj.vertex_groups[index] for index in selected_indices]


def _write_groups_preserving_locks(obj, groups, weights):
    locked = [group for group in groups if getattr(group, "lock_weight", False)]
    if locked:
        names = ", ".join(_group_label(obj, group) for group in locked)
        raise ValueError(f"顶点组已锁定，不能写入: {names}")
    write_groups_by_indices(obj, [group.index for group in groups], weights)


def normalize_with_donors(obj, target_group, donor_groups, *, preserve_rows=None):
    return _normalize_authority_priority_groups(obj, [target_group], donor_groups, preserve_rows=preserve_rows)


def _selected_limit_continuity_scores(obj, weights, selected_mask):
    np = _rwt.np_module()
    scores = np.zeros_like(weights, dtype=float)
    edges = getattr(getattr(obj, "data", None), "edges", ()) or ()
    for edge in edges:
        vertices = tuple(getattr(edge, "vertices", ()) or ())
        if len(vertices) != 2:
            continue
        left, right = int(vertices[0]), int(vertices[1])
        if left < 0 or right < 0 or left >= weights.shape[0] or right >= weights.shape[0]:
            continue
        if not (bool(selected_mask[left]) and bool(selected_mask[right])):
            continue
        scores[left] += weights[right]
        scores[right] += weights[left]
    return scores


def normalize_selected_vertices_proportional(
    obj,
    vertex_indices,
    *,
    limit_groups_enable=False,
    max_groups_per_vertex=None,
    tolerance=0.0001,
):
    ok, error = _rwt.ensure_available()
    if not ok:
        raise RuntimeError(f"按比例规格化不可用: {error}")
    np = _rwt.np_module()
    row_count = len(obj.data.vertices)
    selected_mask = _row_mask(np, vertex_indices, row_count)
    if not bool(np.any(selected_mask)):
        return NormalizationReport(attempted=True)
    writable_indices = editable_group_indices(obj)
    locked_indices = _ordinary_locked_group_indices(obj)
    if locked_indices:
        locked_weights = _rwt.get_groups_arr(obj, locked_indices)
        locked_sum = locked_weights.sum(axis=1)
    else:
        locked_weights = np.zeros((row_count, 0), dtype=np.float32)
        locked_sum = np.zeros(row_count, dtype=np.float32)
    if writable_indices:
        writable_weights = _rwt.get_groups_arr(obj, writable_indices)
    else:
        writable_weights = np.zeros((row_count, 0), dtype=np.float32)
    normalized = writable_weights.copy()
    limited_mask = np.zeros(row_count, dtype=bool)
    locked_limit_rows = np.zeros(row_count, dtype=bool)
    max_groups = int(max_groups_per_vertex or 0)
    limit_enabled = bool(limit_groups_enable) and max_groups > 0
    if limit_enabled:
        if locked_indices:
            locked_counts = np.count_nonzero(locked_weights > tolerance, axis=1)
        else:
            locked_counts = np.zeros(row_count, dtype=np.int64)
        if writable_weights.shape[1]:
            continuity_scores = _selected_limit_continuity_scores(obj, writable_weights, selected_mask)
            for row in np.flatnonzero(selected_mask):
                active_columns = np.flatnonzero(normalized[row] > tolerance)
                remaining_slots = max_groups - int(locked_counts[row])
                if remaining_slots <= 0:
                    locked_limit_rows[row] = True
                    if len(active_columns):
                        normalized[row, active_columns] = 0.0
                        limited_mask[row] = True
                    continue
                if len(active_columns) <= remaining_slots:
                    continue
                ordered = sorted(
                    (int(column) for column in active_columns),
                    key=lambda column: (
                        -float(normalized[row, column]),
                        -float(continuity_scores[row, column]),
                        column,
                    ),
                )
                keep = set(ordered[:remaining_slots])
                for column in active_columns:
                    if int(column) not in keep:
                        normalized[row, int(column)] = 0.0
                limited_mask[row] = True
        else:
            locked_limit_rows = selected_mask & (locked_counts >= max_groups)
    writable_totals = normalized.sum(axis=1) if normalized.shape[1] else np.zeros(row_count, dtype=np.float32)
    available = np.maximum(0.0, 1.0 - locked_sum)
    scalable_rows = selected_mask & (writable_totals > tolerance)
    if bool(np.any(scalable_rows)) and writable_weights.shape[1]:
        scale = np.divide(
            available,
            writable_totals,
            out=np.zeros_like(available),
            where=scalable_rows,
        )
        normalized[scalable_rows, :] = normalized[scalable_rows] * scale[scalable_rows].reshape(-1, 1)
    empty_problem_rows = selected_mask & (writable_totals <= tolerance) & (available > tolerance)
    over_capacity_rows = selected_mask & (locked_sum > (1.0 + tolerance))
    totals = locked_sum + normalized.sum(axis=1)
    under_rows = selected_mask & (totals < (1.0 - tolerance))
    problem_rows = empty_problem_rows | over_capacity_rows | under_rows | locked_limit_rows
    changed_columns = []
    if writable_indices:
        changed_columns = np.flatnonzero(np.any(np.abs(normalized - writable_weights) > 1e-8, axis=0))
        if len(changed_columns):
            changed_indices = [writable_indices[int(column)] for column in changed_columns]
            write_groups_by_indices(obj, changed_indices, normalized[:, changed_columns])
    return NormalizationReport(
        attempted=True,
        changed=bool(len(changed_columns)),
        normalized_vertices=int(np.count_nonzero(selected_mask & ~problem_rows)),
        limited_vertices=int(np.count_nonzero(limited_mask)),
        locked_limit_vertices=int(np.count_nonzero(locked_limit_rows)),
        under_normalized_vertices=int(np.count_nonzero(under_rows)),
        over_capacity_vertices=int(np.count_nonzero(over_capacity_rows)),
        problem_vertices=[int(index) for index in np.flatnonzero(problem_rows)],
    )


def ensure_numeric_export_compatible(obj, group_name):
    if not (group_name or "").isdigit():
        return
    try:
        from ..core.mapping import algorithms as _mapping_algorithms
        _mapping_algorithms.reorder_numeric_vertex_groups_first(obj)
        obj["velo_weight_numeric_export_ready"] = True
    except Exception:
        pass
