"""MMD <-> unified-name mapping UI (V0.1.1)."""

from __future__ import annotations

from velo_tools.i18n import iface_

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
    bl_label = 'MMD ↔ Unified ID Mapping'
    bl_idname = "VELO_EF_PT_mmd_mapping"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        s = getattr(context.scene, "velo_tools", None)
        return s is not None and s.active_tab == 'MATCH'

    def draw(self, context):
        layout = self.layout
        ef = getattr(context.scene, "velo_endfield", None)
        if ef is None:
            layout.label(text='Uninitialized Arknights: Endfield data', icon='ERROR')
            return
        profile = ef.mmd_profile
        if profile is None:
            layout.label(text='Uninitialized mapping data', icon='ERROR')
            return

        # Top: MMD-specific source/target objects
        box_obj = layout.box()
        box_obj.label(text='MMD Working Objects (completely isolated from the vertex group renaming tab below)', icon='OBJECT_DATA')
        box_obj.prop(ef, "mmd_source_object", text='MMD Source')
        box_obj.prop(ef, "mmd_target_object", text='Target Component')
        box_obj.prop(ef, "mmd_armature_object", text='MMD Skeleton')

        # 1.0.8: the export adapter is decided solely by active_game on the "Game" tab; here we only show a read-only hint of the current game
        try:
            from ...games import registry as _registry
            desc = _registry.get_active_descriptor(context.scene)
            display = desc.display_name if desc is not None else "终末地"
        except Exception:
            display = "终末地"
        box_obj.label(
            text=iface_("Current game: {0} (switch in the 'Game' tab above)").format(display),
            icon='SETTINGS',
        )
        box_obj.label(
            text="Click the original 'Export Mod' button of the corresponding exporter, and it will automatically export using a cloned copy after preprocessing, leaving the source object unchanged",
            icon='INFO',
        )

        # Task 4: mapping-table selector (template_ID, same as Blender's text datablock selector)
        box_p = layout.box()
        box_p.label(text='Mapping Table', icon='TEXT')
        box_p.template_ID(ef, "active_mmd_text", new="velo.mmd_text_new")

        layout.template_list(
            "VELO_EF_UL_mmd_rows", "",
            profile, "rows",
            profile, "active_row_index",
            rows=8,
        )

        # Row operations
        row = layout.row(align=True)
        row.operator("velo.vg_row_add", icon='ADD', text='Add a new blank row').after_index = -1
        row.operator("velo.vg_row_remove", icon='REMOVE', text='Delete current row').index = -1
        row.operator("velo.vg_table_clear", icon='TRASH', text='Clear Mapping Table')

        row = layout.row(align=True)
        row.operator("velo.vg_stage_unified", icon='IMPORT', text='Fill Rows from Source Object')
        row.operator("velo.vg_match_to_mmd_table", icon='AUTOMERGE_ON', text='Match by position → write to this table.')

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
        box_t.label(text='.blend built-in text synchronization (can be opened in the Text Editor to edit velo_mmd_mapping.txt)', icon='TEXT')
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
        box.label(text='Workflow', icon='INFO')
        box.label(text='① At the top, pick the MMD source object and the target Component separately')
        box.label(text="② Click 'Fill from Source Object' to automatically fill the MMD column (filtered for unweighted / mmd_edge_scale / UV_*)")
        box.label(text="③ Click 'Match by Position → Write to This Table' to automatically fill the unified column (KDTree accelerated)")
        box.label(text="④ Click 'Actual Rename' to switch to unified numbering; click 'Restore' to switch back to MMD names (only applies to MMD source objects)")


class VELO_EF_PT_mmd_overlay(bpy.types.Panel):
    """Visualize MMD mapping results (a sub-panel of the MMD mapping panel)."""
    bl_label = 'MMD Mapping - Visual Proofreading (Centroid Connection)'
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
            layout.label(text='Uninitialized', icon='ERROR')
            return

        layout.label(text='Data from MMD source ↔ target center of gravity connection', icon='INFO')
        layout.prop(ef, "show_overlay", toggle=True,
                    icon='HIDE_OFF' if ef.show_overlay else 'HIDE_ON')

        col = layout.column(align=True)
        col.enabled = ef.show_overlay
        col.prop(s, "show_labels")
        col.prop(s, "show_unmatched_targets", text='Show unmatched target vertex groups')
        col.prop(s, "overlay_max_distance")

        gm = getattr(context.scene, "velo_general_mapping", None)
        if ef.show_overlay and gm is not None and getattr(gm, "show_overlay", False):
            layout.label(text='Overlay of vertex group renaming tab automatically closed to avoid overlap', icon='INFO')


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
