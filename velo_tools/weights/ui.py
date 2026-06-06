from __future__ import annotations

import bpy


def _report_summary(text, limit=84):
    cleaned = (text or "").replace("\n", " ").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _donor_slot_count(settings):
    try:
        return max(1, min(int(getattr(settings, "donor_count", 1)), 4))
    except Exception:
        return 1


def _is_weight_tab(context):
    settings = getattr(context.scene, "velo_tools", None)
    return settings is not None and settings.active_tab == 'WEIGHT'


class VELO_PT_weight_objects(bpy.types.Panel):
    bl_label = "工作对象"
    bl_idname = "VELO_PT_weight_objects"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return _is_weight_tab(context)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.velo_weight_tools
        col = layout.column(align=True)
        col.prop(settings, "source_object")
        row = col.row(align=True)
        row.prop_search(settings, "source_group", settings, "available_source_vgs", text="来源顶点组")
        row.operator("velo.weight_refresh_groups", text="", icon='FILE_REFRESH')
        col.prop(settings, "target_object")
        col.prop(settings, "armature_object")


class VELO_PT_weight_transfer(bpy.types.Panel):
    bl_label = "传递设置"
    bl_idname = "VELO_PT_weight_transfer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return _is_weight_tab(context)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.velo_weight_tools
        col = layout.column(align=True)
        col.prop(settings, "engine")
        col.prop(settings, "manual_target_group_name")
        row = col.row(align=True)
        row.enabled = settings.manual_target_group_name
        row.prop(settings, "target_group_name")
        row = col.row(align=True)
        row.enabled = False
        row.prop(settings, "reuse_existing_group")
        row = col.row(align=True)
        row.enabled = False
        row.prop(settings, "clear_before_transfer")
        row = col.row(align=True)
        row.enabled = settings.armature_object is not None
        row.prop(settings, "create_bone_if_missing")
        col.prop(settings, "donor_count")
        donor_box = col.box()
        donor_box.label(text="预计算供体（规格化时使用）")
        if len(settings.available_donor_vgs) <= 0:
            donor_box.label(text="当前没有可选供体组", icon='INFO')
        else:
            for slot_index, prop_name in enumerate(("donor_slot_1", "donor_slot_2", "donor_slot_3", "donor_slot_4")[:_donor_slot_count(settings)], start=1):
                donor_box.prop_search(settings, prop_name, settings, "available_donor_vgs", text=f"供体 {slot_index}")
        layout.operator("velo.weight_transfer", icon='MOD_DATA_TRANSFER')
        if settings.last_report:
            box = layout.box()
            icon = 'ERROR' if ("失败" in settings.last_report or "错误" in settings.last_report) else 'INFO'
            row = box.row(align=True)
            row.operator("velo.weight_show_last_report", text=_report_summary(settings.last_report), icon=icon)
            row.operator("velo.weight_copy_last_report", text="", icon='COPYDOWN')


class VELO_PT_weight_group_merge(bpy.types.Panel):
    bl_label = "权重组转移"
    bl_idname = "VELO_PT_weight_group_merge"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 1
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _is_weight_tab(context)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.velo_weight_tools
        active = getattr(context, "active_object", None)
        if active is None or active.type != 'MESH':
            layout.label(text="请先选中一个网格物体", icon='INFO')
            return
        layout.label(text=f"当前物体: {active.name}", icon='MESH_DATA')
        col = layout.column(align=True)
        col.prop_search(settings, "merge_source_group", active, "vertex_groups", text="来源组")
        col.prop_search(settings, "merge_target_group", active, "vertex_groups", text="目标组")
        col.operator("velo.weight_merge_groups", icon='SORT_DESC')
        layout.separator()
        col = layout.column(align=True)
        op = col.operator("velo.weight_merge_mapping_families", text="按 MMD 映射合并同目标", icon='AUTOMERGE_ON')
        op.mapping_mode = 'MMD'
        op = col.operator("velo.weight_merge_mapping_families", text="按通用映射合并同目标", icon='AUTOMERGE_ON')
        op.mapping_mode = 'GENERAL'


class VELO_PT_weight_postprocess(bpy.types.Panel):
    bl_label = "后处理"
    bl_idname = "VELO_PT_weight_postprocess"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return _is_weight_tab(context)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.velo_weight_tools
        col = layout.column(align=True)
        col.prop(settings, "smoothing_enable")
        sub = col.column(align=True)
        sub.enabled = settings.smoothing_enable
        sub.prop(settings, "smoothing_repeat")
        sub.prop(settings, "smoothing_factor")
        col.separator()
        col.prop(settings, "limit_groups_enable")
        row = col.row(align=True)
        row.enabled = settings.limit_groups_enable
        row.prop(settings, "max_groups_per_vertex")
        col.prop(settings, "normalize_after")


class VELO_PT_weight_advanced(bpy.types.Panel):
    bl_label = "高级"
    bl_idname = "VELO_PT_weight_advanced"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 3
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _is_weight_tab(context)

    def draw(self, context):
        layout = self.layout
        settings = context.scene.velo_weight_tools
        col = layout.column(align=True)
        col.prop(settings, "robust_max_distance")
        col.prop(settings, "robust_normal_angle")
        col.prop(settings, "robust_flip_normals")
        col.prop(settings, "robust_point_cloud_inpaint")
        col.prop(settings, "use_deformed_source")
        col.prop(settings, "use_deformed_target")
        col.prop(settings, "limit_dilation_repeat")


_classes = (
    VELO_PT_weight_objects,
    VELO_PT_weight_group_merge,
    VELO_PT_weight_transfer,
    VELO_PT_weight_postprocess,
    VELO_PT_weight_advanced,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
