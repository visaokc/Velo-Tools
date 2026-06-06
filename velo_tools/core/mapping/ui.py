"""MMD <-> unified-name mapping UI (V0.1.1)."""

from __future__ import annotations

import bpy


class VELO_EF_UL_mmd_rows(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        ef = getattr(context.scene, "velo_endfield", None)
        row = layout.row(align=True)
        if not item.unified_name:
            row.alert = True
        # Left: mmd_name uses prop_search so typing filters the source object's vertex groups (Blender native 6-item list + scroll wheel)
        if ef is not None and len(ef.available_src_vgs) > 0:
            row.prop_search(
                item, "mmd_name",
                ef, "available_src_vgs",
                text="", icon='GROUP_VERTEX',
            )
        else:
            row.prop(item, "mmd_name", text="", emboss=True, icon='GROUP_VERTEX')
        arrow = row.row()
        arrow.ui_units_x = 1.6
        arrow.alignment = 'CENTER'
        arrow.label(text="→")
        row.prop(item, "unified_name", text="")
        # Right: per-row +/-
        op_add = row.operator("velo.vg_row_add", text="", icon='ADD')
        op_add.after_index = index
        op_del = row.operator("velo.vg_row_remove", text="", icon='REMOVE')
        op_del.index = index


class VELO_EF_PT_mmd_mapping(bpy.types.Panel):
    bl_label = "MMD ↔ 统一编号 映射"
    bl_idname = "VELO_EF_PT_mmd_mapping"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 1

    @classmethod
    def poll(cls, context):
        s = getattr(context.scene, "velo_tools", None)
        return s is not None and s.active_tab == 'MATCH'

    def draw(self, context):
        layout = self.layout
        ef = getattr(context.scene, "velo_endfield", None)
        if ef is None:
            layout.label(text="未初始化终末地数据", icon='ERROR')
            return
        profile = ef.mmd_profile
        if profile is None:
            layout.label(text="未初始化映射数据", icon='ERROR')
            return

        # Top: MMD-specific source/target objects
        box_obj = layout.box()
        box_obj.label(text="MMD 工作物体（与下方 顶点组改名 Tab 完全隔离）", icon='OBJECT_DATA')
        box_obj.prop(ef, "mmd_source_object", text="MMD 源")
        box_obj.prop(ef, "mmd_target_object", text="目标 Component")
        box_obj.prop(ef, "mmd_armature_object", text="MMD 骨架")

        # 1.0.8: the export adapter is decided solely by active_game on the "Game" tab; here we only show a read-only hint of the current game
        try:
            from ...games import registry as _registry
            desc = _registry.get_active_descriptor(context.scene)
            display = desc.display_name if desc is not None else "终末地"
        except Exception:
            display = "终末地"
        box_obj.label(
            text=f"当前游戏：{display}（在上方『游戏』tab 切换）",
            icon='SETTINGS',
        )
        box_obj.label(
            text="点击对应导出器原本的「导出 Mod」按钮，会自动用克隆副本完成预处理后导出，源物体不被改动",
            icon='INFO',
        )

        # Task 4: mapping-table selector (template_ID, same as Blender's text datablock selector)
        box_p = layout.box()
        box_p.label(text="映射表", icon='TEXT')
        box_p.template_ID(ef, "active_mmd_text", new="velo.mmd_text_new")

        layout.template_list(
            "VELO_EF_UL_mmd_rows", "",
            profile, "rows",
            profile, "active_row_index",
            rows=8,
        )

        # Row operations
        row = layout.row(align=True)
        row.operator("velo.vg_row_add", icon='ADD', text="新增空行").after_index = -1
        row.operator("velo.vg_row_remove", icon='REMOVE', text="删除当前行").index = -1
        row.operator("velo.vg_table_clear", icon='TRASH', text="清空映射表")

        row = layout.row(align=True)
        row.operator("velo.vg_stage_unified", icon='IMPORT', text="从源物体补行")
        row.operator("velo.vg_match_to_mmd_table", icon='AUTOMERGE_ON', text="按位置匹配→写入本表")

        col = layout.column(align=True)
        col.scale_y = 1.2
        r1 = col.row(align=True)
        r1.operator("velo.vg_apply_unified", icon='GREASEPENCIL')
        r1.operator("velo.vg_target_to_mmd", icon='GREASEPENCIL')
        r2 = col.row(align=True)
        r2.operator("velo.vg_revert_to_mmd", icon='LOOP_BACK')
        r2.operator("velo.vg_target_to_unified", icon='LOOP_BACK')

        # Built-in Text two-way sync
        layout.separator()
        box_t = layout.box()
        box_t.label(text=".blend 内置文本同步（可在 Text Editor 中打开 velo_mmd_mapping.txt 编辑）", icon='TEXT')
        rt = box_t.row(align=True)
        rt.operator("velo.vg_table_to_text", icon='EXPORT')
        rt.operator("velo.vg_table_from_text", icon='IMPORT')

        # External file import/export
        row = layout.row(align=True)
        row.operator("velo.vg_table_import", icon='FILE_FOLDER')
        row.operator("velo.vg_table_export", icon='FILE_TICK')

        # Hints
        box = layout.box()
        box.scale_y = 0.8
        box.label(text="工作流", icon='INFO')
        box.label(text="① 在顶部分别拾取 MMD 源物体 与 目标 Component")
        box.label(text="② 点『从源物体补行』自动补 mmd 列（已过滤无权重 / mmd_edge_scale / UV_*）")
        box.label(text="③ 点『按位置匹配→写入本表』自动填 unified 列（KDTree 加速）")
        box.label(text="④ 点『实际改名』切到统一编号；点『还原』切回 MMD 名（仅作用于 MMD 源物体）")


class VELO_EF_PT_mmd_overlay(bpy.types.Panel):
    """Visualize MMD mapping results (a sub-panel of the MMD mapping panel)."""
    bl_label = "MMD 映射 - 可视化校对（重心连线）"
    bl_idname = "VELO_EF_PT_mmd_overlay"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_EF_PT_mmd_mapping'
    bl_order = 2
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        s = getattr(context.scene, "velo_tools", None)
        return s is not None and s.active_tab == 'MATCH'

    def draw(self, context):
        layout = self.layout
        ef = getattr(context.scene, "velo_endfield", None)
        s = context.scene.velo_tools
        if ef is None:
            layout.label(text="未初始化", icon='ERROR')
            return

        layout.label(text="数据来自 MMD 源 ↔ 目标 重心连线", icon='INFO')
        layout.prop(ef, "show_overlay", toggle=True,
                    icon='HIDE_OFF' if ef.show_overlay else 'HIDE_ON')

        col = layout.column(align=True)
        col.enabled = ef.show_overlay
        col.prop(s, "show_labels")
        col.prop(s, "show_unmatched_targets", text="显示未匹配的目标顶点组")
        col.prop(s, "overlay_max_distance")

        gm = getattr(context.scene, "velo_general_mapping", None)
        if ef.show_overlay and gm is not None and getattr(gm, "show_overlay", False):
            layout.label(text="已自动关闭顶点组改名 Tab 的 overlay 以避免重叠", icon='INFO')


_classes = (
    VELO_EF_UL_mmd_rows,
    VELO_EF_PT_mmd_mapping,
    VELO_EF_PT_mmd_overlay,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass