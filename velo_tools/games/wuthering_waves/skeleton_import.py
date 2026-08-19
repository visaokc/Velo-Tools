"""Blender-side WWMI UEMODEL skeleton import."""

from __future__ import annotations

import re
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
        return sum((heads[bones.index(child)] for child in children), Vector()) / len(children)
    parent = bones[index].parent
    chain = [index]
    while len(chain) < 4:
        parent_index = bones[chain[-1]].parent
        if not 0 <= parent_index < len(bones):
            break
        chain.append(parent_index)
    segment = heads[chain[0]] - heads[chain[1]] if len(chain) > 1 else Vector()
    segment_length = segment.length
    direction = segment.copy()
    if len(chain) >= 3:
        previous = heads[chain[1]] - heads[chain[2]]
        # The endpoint derivative of a quadratic fitted through the last three
        # heads. This continues a curved chain rather than averaging it back
        # toward an older direction.
        direction = 1.5 * segment - 0.5 * previous
    if len(chain) >= 4:
        older = heads[chain[2]] - heads[chain[3]]
        direction += 0.25 * (segment - 2.0 * previous + older)
    if direction.length < 1e-5 and 0 <= parent < len(bones):
        direction = head - heads[parent]
    if direction.length < 1e-5:
        direction = Vector((0.0, 0.0, 1.0))
    return head + direction.normalized() * max(segment_length * 0.5, 0.01)


def import_skeleton(folder: Path, *, armature_name="WWMI Skeleton", mirror_mesh=False):
    bones = _find_skeleton(Path(folder))
    heads = _world_heads(bones, mirror_mesh=mirror_mesh)
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
            created[index].use_connect = False
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
