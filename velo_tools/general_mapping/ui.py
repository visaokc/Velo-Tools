from __future__ import annotations

from velo_tools.i18n import iface_

import bpy


class VELO_GM_UL_rows(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        settings = getattr(context.scene, "velo_general_mapping", None)
        row = layout.row(align=True)
        if not item.target_name:
            row.alert = True
        if settings is not None and len(settings.available_source_vgs) > 0:
            row.prop_search(
                item,
                "source_name",
                settings,
                "available_source_vgs",
                text="",
                icon='GROUP_VERTEX',
            )
        else:
            row.prop(item, "source_name", text="", emboss=True, icon='GROUP_VERTEX')
        arrow = row.row()
        arrow.ui_units_x = 1.6
        arrow.alignment = 'CENTER'
        arrow.label(text="→")
        row.prop(item, "target_name", text="")
        op_add = row.operator("velo.match_row_add", text="", icon='ADD')
        op_add.after_index = index
        op_del = row.operator("velo.match_row_remove", text="", icon='REMOVE')
        op_del.index = index


class VELO_PT_general_mapping(bpy.types.Panel):
    bl_label = 'General Vertex Group Mapping'
    bl_idname = "VELO_PT_weight_match"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 3
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        shared = getattr(context.scene, "velo_tools", None)
        return shared is not None and shared.active_tab == 'MATCH'

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "velo_general_mapping", None)
        if settings is None:
            layout.label(text='Uninitialized general mapping data', icon='ERROR')
            return
        profile = settings.profile
        if profile is None:
            layout.label(text='Uninitialized mapping table', icon='ERROR')
            return

        box_obj = layout.box()
        box_obj.label(text='General work object', icon='OBJECT_DATA')
        box_obj.prop(settings, "source_object", text='Source Object')
        box_obj.prop(settings, "target_object", text='Target Object')
        box_obj.prop(settings, "armature_object", text='Skeleton')

        opt = layout.box()
        opt.label(text='Matching Options', icon='PREFERENCES')
        row = opt.row(align=True)
        row.prop(settings, "use_max_distance")
        sub = row.row(align=True)
        sub.enabled = settings.use_max_distance
        sub.prop(settings, "max_match_distance", text='Threshold')

        box_p = layout.box()
        box_p.label(text='Mapping Table', icon='TEXT')
        box_p.template_ID(settings, "active_general_text", new="velo.general_text_new")

        layout.template_list(
            "VELO_GM_UL_rows", "",
            profile, "rows",
            profile, "active_row_index",
            rows=8,
        )

        row = layout.row(align=True)
        row.operator("velo.match_row_add", icon='ADD', text='Add a new blank row').after_index = -1
        row.operator("velo.match_row_remove", icon='REMOVE', text='Delete current row').index = -1
        row.operator("velo.clear_mappings", icon='TRASH', text='Clear Mapping Table')

        row = layout.row(align=True)
        row.operator("velo.match_stage_from_source", icon='IMPORT', text='Fill Rows from Source Object')
        row.operator("velo.match_to_table_lite", icon='AUTOMERGE_ON', text='Match by position → write to this table.')

        col = layout.column(align=True)
        col.scale_y = 1.2
        r1 = col.row(align=True)
        r1.operator("velo.general_source_to_unified", icon='GREASEPENCIL')
        r1.operator("velo.general_target_to_mmd", icon='GREASEPENCIL')
        r2 = col.row(align=True)
        r2.operator("velo.general_source_to_original", icon='LOOP_BACK')
        r2.operator("velo.general_target_to_unified", icon='LOOP_BACK')

        layout.separator()
        box_t = layout.box()
        box_t.label(text='.blend built-in text synchronization', icon='TEXT')
        text_row = box_t.row(align=True)
        text_row.operator("velo.match_table_to_text", icon='EXPORT')
        text_row.operator("velo.match_table_from_text", icon='IMPORT')

        file_row = layout.row(align=True)
        file_row.operator("velo.match_table_import", icon='FILE_FOLDER')
        file_row.operator("velo.match_table_export", icon='FILE_TICK')

        box = layout.box()
        box.scale_y = 0.8
        box.label(text='Workflow', icon='INFO')
        box.label(text='① At the top, select the source object and the target object separately')
        box.label(text="② Click 'Fill from Source Object' to complete the source column")
        box.label(text="③ Click 'Match by Position → Write to This Table' to generate the target column")
        box.label(text='④ The 4 buttons respectively handle renaming and restoring on the source/target sides')


class VELO_PT_general_overlay(bpy.types.Panel):
    bl_label = 'General Mapping - Visual Review (Centroid Links)'
    bl_idname = "VELO_PT_overlay"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_weight_match'
    bl_order = 4
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        shared = getattr(context.scene, "velo_tools", None)
        return shared is not None and shared.active_tab == 'MATCH'

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "velo_general_mapping", None)
        shared = getattr(context.scene, "velo_tools", None)
        ef = getattr(context.scene, "velo_endfield", None)
        if settings is None or shared is None:
            layout.label(text='Uninitialized', icon='ERROR')
            return

        layout.label(text='Data comes from source object ↔ target object center of mass connection', icon='INFO')
        layout.prop(
            settings,
            "show_overlay",
            toggle=True,
            icon='HIDE_OFF' if settings.show_overlay else 'HIDE_ON',
        )

        col = layout.column(align=True)
        col.enabled = settings.show_overlay
        col.prop(shared, "show_labels")
        col.prop(shared, "show_unmatched_targets", text='Show unmatched target vertex groups')
        col.prop(shared, "overlay_max_distance")

        if settings.show_overlay and ef is not None and getattr(ef, "show_overlay", False):
            layout.label(text='Overlay of MMD mapping area automatically closed to avoid overlap', icon='INFO')


class VELO_PT_general_unmatched(bpy.types.Panel):
    bl_label = 'Unmatched list'
    bl_idname = "VELO_PT_unmatched"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_weight_match'
    bl_order = 5
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        shared = getattr(context.scene, "velo_tools", None)
        return shared is not None and shared.active_tab == 'MATCH'

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "velo_general_mapping", None)
        if settings is None:
            layout.label(text='Uninitialized', icon='ERROR')
            return
        from . import overlay as _overlay
        unmatched_sources = list(_overlay.iter_unmatched_sources(settings) or ())
        unmatched_targets = list(_overlay.iter_unmatched_targets(settings) or ())

        layout.label(text=iface_('Unmatched source vertex groups: {0}').format(len(unmatched_sources)), icon='OUTLINER_OB_MESH')
        if unmatched_sources:
            box = layout.box()
            for _world, name in unmatched_sources[:12]:
                box.label(text=name, icon='GROUP_VERTEX')

        layout.separator()
        layout.label(text=iface_('Unmatched target vertex groups: {0}').format(len(unmatched_targets)), icon='OUTLINER_OB_MESH')
        if unmatched_targets:
            box = layout.box()
            for _world, name in unmatched_targets[:12]:
                box.label(text=name, icon='GROUP_VERTEX')

        if not unmatched_sources and not unmatched_targets:
            layout.label(text='(None)', icon='CHECKMARK')


_classes = (
    VELO_GM_UL_rows,
    VELO_PT_general_mapping,
    VELO_PT_general_overlay,
    VELO_PT_general_unmatched,
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