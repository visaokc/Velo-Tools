"""WWMI numeric vertex-group <-> original bone-name mapping UI."""

from __future__ import annotations

from pathlib import Path

import bpy


class VELO_WWMI_BoneMapRow(bpy.types.PropertyGroup):
    numeric_id: bpy.props.StringProperty(name="数字编号", default="")
    original_name: bpy.props.StringProperty(name="原始骨骼名", default="")


class VELO_WWMI_BoneMapSettings(bpy.types.PropertyGroup):
    unpack_folder: bpy.props.StringProperty(name="解包路径", subtype="DIR_PATH", default="")
    rows: bpy.props.CollectionProperty(type=VELO_WWMI_BoneMapRow)
    active_row: bpy.props.IntProperty(default=0)
    show_original: bpy.props.BoolProperty(name="当前显示原始名", default=False)
    voxel_size: bpy.props.FloatProperty(
        name="体素大小", default=0.05, min=0.005, max=0.2, precision=3,
        description="几何匹配使用的归一化体素大小",
    )
    similarity_threshold: bpy.props.FloatProperty(
        name="最低相似度", default=55.0, min=1.0, max=100.0, subtype='PERCENTAGE',
        description="每个 WWMI Component 必须达到的最低体素相似度",
    )


def _selected_meshes(context):
    return [o for o in context.selected_objects if o.type == "MESH"]


class VELO_OT_wwmi_bone_map_generate(bpy.types.Operator):
    bl_idname = "velo.wwmi_bone_map_generate"
    bl_label = "自动生成映射表"
    bl_description = "体素匹配解包目录内全部 .uemodel section 与 WWMI Component，再生成全局编号到原始骨骼名的映射"

    def execute(self, context):
        settings = context.scene.velo_wwmi_bone_map
        cfg = getattr(context.scene, "VTWW_settings", None)
        source_folder = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
        if not settings.unpack_folder.strip():
            self.report({'ERROR'}, "请先填写解包路径")
            return {'CANCELLED'}
        if not source_folder.strip():
            self.report({'ERROR'}, "请先填写 WWMI 对象源目录")
            return {'CANCELLED'}
        from .games.wuthering_waves.bone_mapping import BoneMappingError, generate_mapping
        try:
            mapping, evidence = generate_mapping(
                Path(bpy.path.abspath(settings.unpack_folder)),
                Path(bpy.path.abspath(source_folder)),
                voxel_size=settings.voxel_size,
                similarity_threshold=settings.similarity_threshold,
            )
        except BoneMappingError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        settings.rows.clear()
        for number, bone_name in mapping.items():
            row = settings.rows.add()
            row.numeric_id = str(number)
            row.original_name = bone_name
        settings.show_original = False
        self.report({'INFO'}, f"已由 {len(evidence)} 个 Component 的体素匹配生成 {len(mapping)} 行")
        return {'FINISHED'}


class VELO_OT_wwmi_bone_map_toggle(bpy.types.Operator):
    bl_idname = "velo.wwmi_bone_map_toggle"
    bl_label = "切换编号显示"
    target: bpy.props.EnumProperty(items=[("ORIGINAL", "原始名字", ""), ("NUMERIC", "数字编号", "")])

    def execute(self, context):
        settings = context.scene.velo_wwmi_bone_map
        mapping = {r.numeric_id: r.original_name for r in settings.rows if r.numeric_id and r.original_name}
        if not mapping:
            self.report({'ERROR'}, "映射表没有完整的数字编号与原始骨骼名")
            return {'CANCELLED'}
        pairs = mapping if self.target == "ORIGINAL" else {v: k for k, v in mapping.items()}
        for obj in _selected_meshes(context):
            renames = [(vg.name, pairs[vg.name]) for vg in obj.vertex_groups if vg.name in pairs and pairs[vg.name] != vg.name]
            used = {vg.name for vg in obj.vertex_groups}
            for old, new in renames:
                if new in used and new != old:
                    continue
                vg = obj.vertex_groups.get(old)
                if vg:
                    vg.name = f"__velo_tmp__{old}"
            for old, new in renames:
                vg = obj.vertex_groups.get(f"__velo_tmp__{old}")
                if vg:
                    vg.name = new
        settings.show_original = self.target == "ORIGINAL"
        return {'FINISHED'}


class VELO_UL_wwmi_bone_map(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "numeric_id", text="")
        row.label(text="→")
        row.prop(item, "original_name", text="")
        op = row.operator("velo.wwmi_bone_map_remove", text="", icon='REMOVE')
        op.index = index


class VELO_OT_wwmi_bone_map_remove(bpy.types.Operator):
    bl_idname = "velo.wwmi_bone_map_remove"
    bl_label = "删除映射行"
    index: bpy.props.IntProperty()
    def execute(self, context):
        rows = context.scene.velo_wwmi_bone_map.rows
        if 0 <= self.index < len(rows): rows.remove(self.index)
        return {'FINISHED'}


class VELO_OT_wwmi_bone_map_add(bpy.types.Operator):
    bl_idname = "velo.wwmi_bone_map_add"
    bl_label = "新增映射行"
    def execute(self, context):
        context.scene.velo_wwmi_bone_map.rows.add()
        return {'FINISHED'}


class VELO_PT_wwmi_bone_map(bpy.types.Panel):
    bl_idname = "VELO_PT_wwmi_bone_map"
    bl_label = "WWMI 数字编号 ↔ 原始骨骼名"
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'; bl_order = 1
    @classmethod
    def poll(cls, context):
        s = getattr(context.scene, "velo_tools", None)
        return s is not None and s.active_tab == 'MATCH' and s.active_game == 'WUTHERING'
    def draw(self, context):
        layout = self.layout; settings = context.scene.velo_wwmi_bone_map
        layout.prop(settings, "unpack_folder")
        cfg = getattr(context.scene, "VTWW_settings", None)
        if cfg is not None:
            layout.prop(cfg, "object_source_folder", text="对象源目录")
        match_box = layout.box()
        match_box.prop(settings, "similarity_threshold")
        match_box.prop(settings, "voxel_size")
        layout.operator("velo.wwmi_bone_map_generate", icon='SORTBYEXT')
        layout.template_list("VELO_UL_wwmi_bone_map", "", settings, "rows", settings, "active_row", rows=8)
        layout.operator("velo.wwmi_bone_map_add", text="新增空行", icon='ADD')
        row = layout.row(align=True)
        op = row.operator("velo.wwmi_bone_map_toggle", text="切换至原始名字", icon='FONT_DATA'); op.target = 'ORIGINAL'
        op = row.operator("velo.wwmi_bone_map_toggle", text="切换至数字编号", icon='SORT_ASC'); op.target = 'NUMERIC'
        layout.label(text="改名只作用于当前选中的网格对象；未选对象不会修改。", icon='INFO')


_classes = (VELO_WWMI_BoneMapRow, VELO_WWMI_BoneMapSettings, VELO_OT_wwmi_bone_map_generate,
            VELO_OT_wwmi_bone_map_toggle, VELO_UL_wwmi_bone_map, VELO_OT_wwmi_bone_map_remove,
            VELO_OT_wwmi_bone_map_add,
            VELO_PT_wwmi_bone_map)

def register():
    for cls in _classes: bpy.utils.register_class(cls)
    bpy.types.Scene.velo_wwmi_bone_map = bpy.props.PointerProperty(type=VELO_WWMI_BoneMapSettings)

def unregister():
    if hasattr(bpy.types.Scene, "velo_wwmi_bone_map"): del bpy.types.Scene.velo_wwmi_bone_map
    for cls in reversed(_classes):
        try: bpy.utils.unregister_class(cls)
        except Exception: pass
