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
        child_heads = [heads[bones.index(child)] for child in children]
        if len(child_heads) == 1:
            return child_heads[0]
        parent = bones[index].parent
        incoming = head - heads[parent] if 0 <= parent < len(bones) else Vector()
        if incoming.length > 1e-5:
            incoming.normalize()
            aligned = [
                (child_head, (child_head - head).normalized().dot(incoming))
                for child_head in child_heads
                if (child_head - head).length > 1e-5
            ]
            if aligned:
                return max(aligned, key=lambda item: item[1])[0]
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
    return head + direction.normalized() * max(segment_length * 0.5, 0.01)


def import_skeleton(folder: Path, *, armature_name="WWMI Skeleton", mirror_mesh=False, bones=None):
    bones = tuple(bones) if bones is not None else _find_skeleton(Path(folder))
    heads = _world_heads(bones, mirror_mesh=mirror_mesh)
    arm_data = bpy.data.armatures.new(armature_name)
    arm_obj = bpy.data.objects.new(armature_name, arm_data)
    skeleton_collection = bpy.context.scene.collection.children.get("Skeleton")
    if skeleton_collection is None:
        skeleton_collection = bpy.data.collections.new("Skeleton")
        bpy.context.scene.collection.children.link(skeleton_collection)
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
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    for edit_bone in list(arm_data.edit_bones):
        arm_data.edit_bones.remove(edit_bone)
    created = []
    for index, bone in enumerate(bones):
        edit = arm_data.edit_bones.new(bone.name)
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
        if source_roots and _object_root_collection(bpy.context.scene, obj) not in source_roots:
            continue
        if not source_roots:
            continue
        modifier = next((item for item in obj.modifiers if item.type == 'ARMATURE' and item.name == 'WWMI Skeleton'), None)
        if modifier is None:
            modifier = obj.modifiers.new('WWMI Skeleton', 'ARMATURE')
        modifier.object = arm_obj
        bound += 1
    return arm_obj, len(bones), bound
