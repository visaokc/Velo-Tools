"""Blender-side WWMI UEMODEL skeleton import."""

from __future__ import annotations

import re
from pathlib import Path

import bpy
from mathutils import Vector

from .bone_mapping import BoneMappingError, UEBone, load_uemodel_skeleton


_COMPONENT_RE = re.compile(r"(?:^|[ _])Component (\d+(?:\.\d+)?)")
_STANDARD_RE = re.compile(r"^(?:Bip001|Bone_(?:Chest|Spine|Neck|Head|Shoulder|Arm|Forearm|Hand|Thigh|Calf|Foot|Pelvis))")


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


def _world_heads(bones: tuple[UEBone, ...]) -> list[Vector]:
    heads = [None] * len(bones)
    for index, bone in enumerate(bones):
        local = Vector(bone.position)
        if 0 <= bone.parent < index:
            heads[index] = heads[bone.parent] + local
        else:
            heads[index] = local
    return heads


def _bone_tail(index: int, bones: tuple[UEBone, ...], heads: list[Vector]) -> Vector:
    children = [child for child in bones if child.parent == index]
    head = heads[index]
    if children:
        return sum((heads[bones.index(child)] for child in children), Vector()) / len(children)
    parent = bones[index].parent
    direction = Vector()
    segment = index
    weight = 1.0
    segment_length = 0.0
    # Estimate the endpoint tangent from several recent chain segments so a
    # curved chain continues along its local curvature instead of copying only
    # the immediate parent direction.
    while 0 <= bones[segment].parent < len(bones) and weight <= 3.0:
        ancestor = bones[segment].parent
        delta = heads[segment] - heads[ancestor]
        if delta.length > 1e-5:
            direction += delta.normalized() * weight
            if segment == index:
                segment_length = delta.length
            weight += 1.0
        segment = ancestor
    if direction.length < 1e-5 and 0 <= parent < len(bones):
        direction = head - heads[parent]
    if direction.length < 1e-5:
        direction = Vector((0.0, 0.0, 1.0))
    return head + direction.normalized() * max(segment_length * 0.5, 0.01)


def _is_standard(name: str) -> bool:
    return bool(_STANDARD_RE.match(name))


def import_skeleton(folder: Path, *, armature_name="WWMI Skeleton"):
    bones = _find_skeleton(Path(folder))
    heads = _world_heads(bones)
    arm_data = bpy.data.armatures.get(armature_name) or bpy.data.armatures.new(armature_name)
    arm_obj = bpy.data.objects.get(armature_name)
    if arm_obj is None or arm_obj.type != "ARMATURE":
        arm_obj = bpy.data.objects.new(armature_name, arm_data)
        bpy.context.collection.objects.link(arm_obj)
    arm_obj.show_in_front = True
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
            parent_children = [candidate for candidate in bones if candidate.parent == bone.parent]
            standard_children = [candidate for candidate in parent_children if _is_standard(candidate.name)]
            created[index].use_connect = (
                len(standard_children) == 1
                and _is_standard(bone.name)
                and _is_standard(bones[bone.parent].name)
            )
    bpy.ops.object.mode_set(mode="OBJECT")
    arm_obj.data.display_type = 'OCTAHEDRAL'
    arm_obj.data.axes_position = 0

    bound = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or not _COMPONENT_RE.search(obj.name):
            continue
        modifier = next((item for item in obj.modifiers if item.type == 'ARMATURE' and item.name == 'WWMI Skeleton'), None)
        if modifier is None:
            modifier = obj.modifiers.new('WWMI Skeleton', 'ARMATURE')
        modifier.object = arm_obj
        bound += 1
    return arm_obj, len(bones), bound
