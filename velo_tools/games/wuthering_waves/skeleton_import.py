"""Blender-side WWMI UEMODEL skeleton import."""

from __future__ import annotations

import re
import json
from pathlib import Path

import bpy
from mathutils import Quaternion, Vector

from .bone_mapping import BoneMappingError, UEBone, load_uemodel_skeleton


_COMPONENT_RE = re.compile(r"(?:^|[ _])Component (\d+(?:\.\d+)?)")
_MESH_SCALE = 0.01
_MIN_TAIL_LENGTH = 0.025


def _side_name_candidates(name: str):
    if name.endswith((".L", ".R")):
        return ()
    match = re.match(r"^(.*?)([_\- ]?)([LR])$", name)
    if match and match.group(2):
        prefix, separator, side = match.groups()
        counterpart = prefix + separator + ("R" if side == "L" else "L")
        return ((counterpart, prefix, side),)
    if re.match(r"^[LR](?=[A-Z_])", name):
        return ((("R" if name[0] == "L" else "L") + name[1:]), name[1:], name[0]),
    match = re.match(r"^(.*?)([LR])(?=[A-Z_])(.+)$", name)
    if match:
        prefix, side, suffix = match.groups()
        counterpart = prefix + ("R" if side == "L" else "L") + suffix
        prefix_core = prefix.rstrip("_- ")
        suffix_core = suffix.lstrip("_- ")
        separator = "_" if prefix != prefix_core or suffix != suffix_core else ""
        base = prefix_core + separator + suffix_core
        return ((counterpart, base, side),)
    return ()


def side_suffix_names(names):
    """Return Blender .L/.R names only when a matching opposite-side bone exists."""
    name_set = set(names)
    renamed = {}
    for name in names:
        candidates = _side_name_candidates(name)
        if not candidates:
            continue
        counterpart, base, side = candidates[0]
        if counterpart in name_set:
            renamed[name] = f"{base}.{side}"
    return renamed


def _find_skeleton(folder: Path) -> tuple[UEBone, ...]:
    candidates = []
    for path in sorted(folder.rglob("*.uemodel")):
        try:
            bones = load_uemodel_skeleton(path)
        except BoneMappingError:
            continue
        if bones:
            candidates.append((len(bones), path.name, bones))
    if not candidates:
        raise BoneMappingError("模型文件夹中没有可读取的 .uemodel 骨架")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return candidates[0][2]


def _collection_contains(collection, obj):
    if collection.objects.get(obj.name) is not None:
        return True
    return any(_collection_contains(child, obj) for child in collection.children)


def _object_root_collection(scene, obj):
    return next(
        (collection for collection in scene.collection.children
         if _collection_contains(collection, obj)),
        None,
    )


def _collection_has_content(collection):
    return bool(collection.objects) or any(_collection_has_content(child) for child in collection.children)


def _move_child_first(parent, child):
    children = list(parent.children)
    if not children or children[0] == child:
        return
    if child not in children:
        parent.children.link(child)
        children.append(child)
    for collection in children:
        parent.children.unlink(collection)
    parent.children.link(child)
    for collection in children:
        if collection != child:
            parent.children.link(collection)


def _skeleton_collection(scene, preferred=None):
    target = preferred if preferred is not None and _collection_has_content(preferred) else scene.collection
    skeleton = target.children.get("Skeleton")
    if skeleton is None:
        skeleton = bpy.data.collections.new("Skeleton")
        target.children.link(skeleton)
    _move_child_first(target, skeleton)
    return skeleton


def load_saved_skeleton(path: Path) -> tuple[UEBone, ...]:
    """Load the skeleton snapshot stored in a matching-result JSON file."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        records = payload["skeleton"]
        bones = tuple(
            UEBone(
                str(record["name"]),
                int(record["parent"]),
                tuple(float(value) for value in record["position"]),
                tuple(float(value) for value in record["rotation"]),
            )
            for record in records
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BoneMappingError(f"匹配结果文件中的骨架数据无效：{exc}") from exc
    if not bones:
        raise BoneMappingError("匹配结果文件不包含骨架")
    for index, bone in enumerate(bones):
        if bone.parent >= index or bone.parent < -1:
            raise BoneMappingError(f"匹配结果文件中的骨架父索引无效：{bone.name}")
    return bones


def _world_heads(bones: tuple[UEBone, ...], *, mirror_mesh=False) -> list[Vector]:
    heads = [None] * len(bones)
    rotations = [None] * len(bones)
    for index, bone in enumerate(bones):
        local = Vector(bone.position)
        local_rotation = Quaternion((bone.rotation[3], bone.rotation[0], bone.rotation[1], bone.rotation[2]))
        if 0 <= bone.parent < index:
            heads[index] = heads[bone.parent] + rotations[bone.parent] @ local
            rotations[index] = rotations[bone.parent] @ local_rotation
        else:
            heads[index] = local
            rotations[index] = local_rotation
    # Match the WWMI mesh import's mesh_scale=0.01, mirror_mesh, and Z rotation.
    x_sign = 1.0 if mirror_mesh else -1.0
    return [Vector((x_sign * head.x * _MESH_SCALE, -head.y * _MESH_SCALE, head.z * _MESH_SCALE)) for head in heads]


def _bone_tail(index: int, bones: tuple[UEBone, ...], heads: list[Vector]) -> Vector:
    children = [child for child in bones if child.parent == index]
    head = heads[index]
    if bones[index].name == "Root":
        return head + Vector((0.0, 0.0, 0.5))
    if bones[index].name == "Bip001":
        return head + Vector((0.0, 0.0, 0.1))
    if children:
        child_entries = [(child, heads[bones.index(child)]) for child in children]
        child_heads = [point for _child, point in child_entries]
        distances = sorted((point - head).length for point in child_heads)
        if len(distances) >= 3:
            median = distances[len(distances) // 2]
            deviations = sorted(abs(distance - median) for distance in distances)
            mad = deviations[len(deviations) // 2]
            cutoff = median + 3.0 * max(mad, median * 0.5)
            clustered = [
                (child, point) for child, point in child_entries
                if (point - head).length <= cutoff
            ]
            if len(clustered) >= 2 and len(clustered) < len(child_heads):
                continuations = []
                for child, child_head in clustered:
                    child_index = bones.index(child)
                    grandchildren = [
                        heads[other] for other, candidate in enumerate(bones)
                        if candidate.parent == child_index
                    ]
                    if grandchildren:
                        target = sum(grandchildren, Vector()) / len(grandchildren)
                        continuation = target - child_head
                        if continuation.length > 1e-5:
                            continuations.append(continuation.normalized())
                direction = Vector()
                if len(continuations) >= 2:
                    combined = sum(continuations, Vector())
                    coherence = combined.length / len(continuations)
                    if coherence >= 0.85:
                        direction = combined
                if direction.length <= 1e-5:
                    target = sum((point for _child, point in clustered), Vector()) / len(clustered)
                    direction = target - head
                if direction.length > 1e-5:
                    length = max(_MIN_TAIL_LENGTH, min(0.08, median * 2.5))
                    return head + direction.normalized() * length
        return sum(child_heads, Vector()) / len(child_heads)
    parent = bones[index].parent
    chain = [index]
    while len(chain) < 5:
        parent_index = bones[chain[-1]].parent
        if not 0 <= parent_index < len(bones):
            break
        chain.append(parent_index)
    segment = heads[chain[0]] - heads[chain[1]] if len(chain) > 1 else Vector()
    segment_length = segment.length
    direction = segment.copy()
    if segment_length > 1e-5:
        base_direction = segment.normalized()
        smooth_segments = [segment]
        for current, parent_index in zip(chain[1:], chain[2:]):
            candidate = heads[current] - heads[parent_index]
            if candidate.length < 1e-5:
                break
            # Stop at a branch-like turn; a distant auxiliary chain must not
            # pull a leaf tail sideways.
            if base_direction.dot(candidate.normalized()) < 0.5:
                break
            smooth_segments.append(candidate)
            base_direction = candidate.normalized()
        direction = sum(
            (segment.normalized() * (len(smooth_segments) - offset)
             for offset, segment in enumerate(smooth_segments)),
            Vector(),
        )
    if direction.length < 1e-5 and 0 <= parent < len(bones):
        direction = head - heads[parent]
    if direction.length < 1e-5:
        direction = Vector((0.0, 0.0, 1.0))
    length = _leaf_length(index, bones, heads)
    return head + direction.normalized() * length


def _leaf_length(index: int, bones: tuple[UEBone, ...], heads: list[Vector]) -> float:
    """Estimate leaf display length from nearby bones, not a distant ancestor."""
    bone = bones[index]
    parent = bone.parent
    parent_children = [
        candidate for candidate in bones if candidate.parent == parent
    ] if 0 <= parent < len(bones) else []
    facial = bone.name.startswith(("Facial_", "Face_", "Eye", "Brow", "Mouth", "Tongue"))
    if len(parent_children) == 1:
        parent_tail = _bone_tail(parent, bones, heads)
        parent_length = (parent_tail - heads[parent]).length
        if parent_length > 1e-5:
            local = parent_length
        else:
            local = _MIN_TAIL_LENGTH
    else:
        local = None
    if local is None:
        siblings = [
            (heads[other] - heads[index]).length
            for other, candidate in enumerate(bones)
            if other != index and candidate.parent == parent
            and (heads[other] - heads[index]).length > 1e-5
        ]
        if siblings:
            siblings.sort()
            local = siblings[len(siblings) // 2] * 0.5
        elif 0 <= parent < len(bones):
            local = (heads[index] - heads[parent]).length * 0.35
        else:
            local = _MIN_TAIL_LENGTH
    if facial:
        local = min(local, 0.035)
    elif len(parent_children) == 1:
        return max(local, 1e-5)
    return max(min(local, 0.08), _MIN_TAIL_LENGTH)


def import_skeleton(folder: Path, *, armature_name="WWMI Skeleton", mirror_mesh=False,
                    rename_side_suffix=True, bones=None, component_collection=None):
    bones = tuple(bones) if bones is not None else _find_skeleton(Path(folder))
    heads = _world_heads(bones, mirror_mesh=mirror_mesh)
    arm_data = bpy.data.armatures.new(armature_name)
    arm_obj = bpy.data.objects.new(armature_name, arm_data)
    skeleton_collection = _skeleton_collection(bpy.context.scene, component_collection)
    skeleton_collection.objects.link(arm_obj)
    arm_obj.show_in_front = True
    selected_component_meshes = [
        obj for obj in bpy.context.selected_objects
        if obj.type == "MESH" and _COMPONENT_RE.search(obj.name)
    ]
    source_roots = {
        root for root in (_object_root_collection(bpy.context.scene, obj)
                          for obj in selected_component_meshes)
        if root is not None and root.name != "Skeleton"
    }
    binding_root = component_collection if component_collection is not None and _collection_has_content(component_collection) else None
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for edit_bone in list(arm_data.edit_bones):
        arm_data.edit_bones.remove(edit_bone)
    created = []
    bone_names = side_suffix_names([bone.name for bone in bones]) if rename_side_suffix else {}
    for index, bone in enumerate(bones):
        edit = arm_data.edit_bones.new(bone_names.get(bone.name, bone.name))
        edit.head = heads[index]
        edit.tail = _bone_tail(index, bones, heads)
        created.append(edit)
    for index, bone in enumerate(bones):
        if 0 <= bone.parent < len(created):
            created[index].parent = created[bone.parent]
            created[index].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")
    arm_obj.data.display_type = 'OCTAHEDRAL'
    arm_obj.data.axes_position = 0

    bound = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or not _COMPONENT_RE.search(obj.name):
            continue
        if binding_root is not None:
            if not any(_collection_contains(collection, obj) for collection in (binding_root,)):
                continue
        elif source_roots:
            if _object_root_collection(bpy.context.scene, obj) not in source_roots:
                continue
        else:
            continue
        modifier = next((item for item in obj.modifiers if item.type == 'ARMATURE' and item.name == 'WWMI Skeleton'), None)
        if modifier is None:
            modifier = obj.modifiers.new('WWMI Skeleton', 'ARMATURE')
        modifier.object = arm_obj
        bound += 1
    return arm_obj, len(bones), bound
