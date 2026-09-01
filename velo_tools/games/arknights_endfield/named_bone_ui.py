"""Blender UI for Endfield Component-local bone-name mapping generation."""

from pathlib import Path

import bpy
from mathutils import Matrix, Vector

from velo_tools.i18n import iface_

from .named_bone_mapping import (
    SKELETON_FILE_NAME,
    NamedBoneMappingError,
    generate_mapping,
    uses_asset_input,
    write_mapping,
)


class _OrientationNode:
    __slots__ = ("name", "parent", "head")

    def __init__(self, name, parent, head):
        self.name = name
        self.parent = parent
        self.head = head


def _stabilize_bip001_import_length(armature):
    bip = armature.data.bones.get("Bip001")
    if bip is None or bip.parent is not None:
        return
    desired_head = Vector((0.0, 0.0, 0.1))
    offset = bip.head_local - desired_head
    if offset.length <= 1.0e-6:
        return
    bpy.ops.object.mode_set(mode="EDIT")
    for edit_bone in armature.data.edit_bones:
        edit_bone.head -= offset
        edit_bone.tail -= offset
    bpy.ops.object.mode_set(mode="OBJECT")
    armature.matrix_world = armature.matrix_world @ Matrix.Translation(offset)


def _orient_armature_from_structure(armature):
    from .armature_orientation import derive_tails

    nodes_by_name = {}
    ordered = []

    def add_bone(bone, parent_node):
        node = _OrientationNode(bone.name, parent_node, bone.head_local.copy())
        nodes_by_name[bone.name] = node
        ordered.append(node)
        for child in bone.children:
            add_bone(child, node)

    source_bones = list(armature.data.bones)
    for root in (bone for bone in source_bones if bone.parent is None):
        add_bone(root, None)
    for bone in source_bones:
        if bone.name not in nodes_by_name:
            add_bone(bone, nodes_by_name.get(bone.parent.name) if bone.parent else None)

    tails = derive_tails(ordered, lambda node: node.head)
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    for node in ordered:
        edit_bone = armature.data.edit_bones.get(node.name)
        if edit_bone is None:
            continue
        edit_bone.head = node.head
        edit_bone.tail = tails[id(node)]
        edit_bone.roll = 0.0
    bpy.ops.object.mode_set(mode="OBJECT")
    _stabilize_bip001_import_length(armature)


class ComponentBoneMappingSettings(bpy.types.PropertyGroup):
    unpack_path: bpy.props.StringProperty(
        name="LOD0 GLB or unpacked asset path",
        description="Direct GLB keeps its bone orientation; asset input uses only LOD0 and rebuilds bone orientation from the hierarchy",
        subtype="FILE_PATH",
        default="",
    )
    voxel_size: bpy.props.FloatProperty(
        name="Voxel size",
        description="Normalized voxel size used to match LOD0 GLB meshes to EFMI Components",
        default=0.01,
        min=0.005,
        max=0.1,
        precision=3,
    )
    similarity_threshold: bpy.props.FloatProperty(
        name="Minimum similarity",
        description="Minimum voxel similarity required for every EFMI Component",
        default=55.0,
        min=25.0,
        max=100.0,
        subtype="PERCENTAGE",
    )


def _write_skeleton_glb(source_glb: Path, target: Path, *, correct_asset_orientation=False):
    before = set(bpy.data.objects)
    before_meshes = set(bpy.data.meshes)
    before_armatures = set(bpy.data.armatures)
    previous_active = bpy.context.view_layer.objects.active
    previous_selection = {obj: obj.select_get() for obj in bpy.context.scene.objects}
    try:
        bpy.ops.object.select_all(action="DESELECT")
        bpy.ops.import_scene.gltf(filepath=str(source_glb))
        imported = [obj for obj in bpy.data.objects if obj not in before]
        armatures = [obj for obj in imported if obj.type == "ARMATURE"]
        if not armatures:
            raise NamedBoneMappingError("The source GLB contains no Armature")
        bpy.ops.object.select_all(action="DESELECT")
        for armature in armatures:
            matrix_world = armature.matrix_world.copy()
            armature.parent = None
            armature.matrix_world = matrix_world
            armature.select_set(True)
        bpy.context.view_layer.objects.active = armatures[0]
        if len(armatures) > 1:
            bpy.ops.object.join()
        armature = bpy.context.view_layer.objects.active
        armature.name = "Named Skeleton"
        if correct_asset_orientation:
            _orient_armature_from_structure(armature)
        carrier_mesh = bpy.data.meshes.new("Skeleton Carrier")
        carrier_mesh.from_pydata(((0.0, 0.0, 0.0), (0.0001, 0.0, 0.0), (0.0, 0.0001, 0.0)), (), ((0, 1, 2),))
        carrier = bpy.data.objects.new("Skeleton Carrier", carrier_mesh)
        bpy.context.scene.collection.objects.link(carrier)
        carrier.parent = armature
        group = carrier.vertex_groups.new(name=armature.data.bones[0].name)
        group.add((0, 1, 2), 1.0, "REPLACE")
        modifier = carrier.modifiers.new(name="Armature", type="ARMATURE")
        modifier.object = armature
        bpy.ops.object.select_all(action="DESELECT")
        armature.select_set(True)
        carrier.select_set(True)
        bpy.context.view_layer.objects.active = armature
        bpy.ops.export_scene.gltf(
            filepath=str(target),
            export_format="GLB",
            use_selection=True,
            export_animations=False,
            export_materials="NONE",
        )
        if not target.is_file() or target.stat().st_size == 0:
            raise NamedBoneMappingError(f"Failed to create {target.name}")
    finally:
        bpy.ops.object.select_all(action="DESELECT")
        for obj in list(bpy.data.objects):
            if obj not in before:
                bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in list(bpy.data.meshes):
            if mesh not in before_meshes and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for armature in list(bpy.data.armatures):
            if armature not in before_armatures and armature.users == 0:
                bpy.data.armatures.remove(armature)
        for obj, selected in previous_selection.items():
            if obj.name in bpy.context.scene.objects:
                obj.select_set(selected)
        if previous_active is not None and previous_active.name in bpy.context.scene.objects:
            bpy.context.view_layer.objects.active = previous_active


class COMPONENTBONE_OT_generate_mapping(bpy.types.Operator):
    bl_idname = "component_bone_mapping.generate"
    bl_label = "Generate Bone Name Mapping"
    bl_description = "Match LOD0 meshes, write local-to-bone-name Metadata, and create an oriented skeleton GLB"

    def execute(self, context):
        settings = context.scene.component_bone_mapping
        cfg = getattr(context.scene, "VTEF_settings", None)
        source_folder = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
        if not settings.unpack_path.strip():
            self.report({"ERROR"}, iface_("Please select an unpacked model or asset path"))
            return {"CANCELLED"}
        if not source_folder.strip():
            self.report({"ERROR"}, iface_("Please select the EFMI object source directory"))
            return {"CANCELLED"}
        source_folder = Path(bpy.path.abspath(source_folder)).resolve()
        try:
            glb_path, metadata, component_maps, evidence = generate_mapping(
                Path(bpy.path.abspath(settings.unpack_path)).resolve(),
                source_folder,
                voxel_size=settings.voxel_size,
                similarity_threshold=settings.similarity_threshold,
            )
            mapping_path = write_mapping(source_folder, glb_path, metadata, component_maps)
            _write_skeleton_glb(
                glb_path,
                source_folder / SKELETON_FILE_NAME,
                correct_asset_orientation=uses_asset_input(
                    Path(bpy.path.abspath(settings.unpack_path)).resolve()
                ),
            )
        except (NamedBoneMappingError, OSError, ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, iface_("Bone name mapping failed: {0}").format(exc))
            return {"CANCELLED"}
        mapped = sum(len(mapping) for mapping in component_maps.values())
        self.report(
            {"INFO"},
            iface_("Generated {0} local bone-name mappings for {1} Components in {2}").format(
                mapped, len(evidence), mapping_path.name
            ),
        )
        return {"FINISHED"}


class COMPONENTBONE_PT_mapping(bpy.types.Panel):
    bl_idname = "COMPONENTBONE_PT_mapping"
    bl_label = "Named Bone Mapping"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Velo Tools"
    bl_parent_id = "VTEF_PT_SIDEBAR"
    bl_order = 900
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.component_bone_mapping
        cfg = getattr(context.scene, "VTEF_settings", None)
        layout.prop(settings, "unpack_path")
        if cfg is not None:
            layout.prop(cfg, "object_source_folder", text="EFMI object source directory")
        box = layout.box()
        box.prop(settings, "similarity_threshold")
        box.prop(settings, "voxel_size")
        layout.operator(COMPONENTBONE_OT_generate_mapping.bl_idname, icon="ARMATURE_DATA")
        layout.label(text="Merged import/export uses Component-local bone names when the sidecar exists.", icon="INFO")


_CLASSES = (
    ComponentBoneMappingSettings,
    COMPONENTBONE_OT_generate_mapping,
    COMPONENTBONE_PT_mapping,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.component_bone_mapping = bpy.props.PointerProperty(type=ComponentBoneMappingSettings)


def unregister():
    if hasattr(bpy.types.Scene, "component_bone_mapping"):
        del bpy.types.Scene.component_bone_mapping
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
