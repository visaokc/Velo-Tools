"""CrossIB UI panel and operators for the vendored EFMI integration."""

from velo_tools.i18n import iface_
import bpy


def _vtef_settings_present():
    return hasattr(bpy.types.Scene, "VTEF_settings")


class CROSSIB_PT_Panel(bpy.types.Panel):
    bl_label = 'Cross Index Buffer (Cross IB)'
    bl_idname = "CROSSIB_PT_PANEL"
    bl_parent_id = "VTEF_PT_SIDEBAR"
    bl_options = {'DEFAULT_CLOSED'}
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Velo Tools Endfield"
    bl_order = 50

    @classmethod
    def poll(cls, context):
        if not _vtef_settings_present():
            return False
        cfg = getattr(context.scene, "VTEF_settings", None)
        if cfg is None:
            return False
        return getattr(cfg, "tool_mode", "") == 'EXPORT_MOD' and not getattr(cfg, "partial_export", False)

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "crossib_settings", None)
        if settings is None:
            layout.label(text='CrossIB Data not registered', icon='ERROR')
            return

        layout.prop(settings, 'enabled', text='Enable cross-IB (Cross Index Buffer).')
        if not settings.enabled:
            return

        layout.label(text='The left (source) borrows the rendering pipeline of the right (target)', icon='INFO')

        from .sidecar import sidecar_status

        cfg = getattr(context.scene, "VTEF_settings", None)
        source = getattr(cfg, "object_source_folder", "") if cfg else ""
        status, detail = sidecar_status(source)
        if status == "ready":
            layout.label(text='CrossIB.json v2 is ready', icon='CHECKMARK')
            layout.label(text=detail)
            button_text = "重新生成 CrossIB.json（选择一份帧转储）"
        elif status == "outdated":
            layout.label(text='CrossIB.json v1 has expired and must be regenerated', icon='ERROR')
            button_text = "生成 CrossIB.json v2（选择一份帧转储）"
        elif status == "invalid":
            layout.label(text='CrossIB.json Invalid', icon='ERROR')
            layout.label(text=detail)
            button_text = "重新生成 CrossIB.json（选择一份帧转储）"
        else:
            layout.label(text='CrossIB.json v2 not found', icon='ERROR')
            button_text = "生成 CrossIB.json v2（选择一份帧转储）"
        layout.operator("crossib.generate_sidecars", text=button_text, icon='FILE_FOLDER')

        add_row = layout.row(align=True)
        op_obj = add_row.operator("crossib.add_mapping", text='Add object mapping', icon='ADD')
        op_obj.source_kind = 'OBJECT'
        op_col = add_row.operator("crossib.add_mapping", text='Add Collection Mapping', icon='OUTLINER_COLLECTION')
        op_col.source_kind = 'COLLECTION'

        for index, mapping in enumerate(settings.mappings):
            box = layout.box()
            row = box.row(align=True)
            if mapping.source_kind == 'COLLECTION':
                row.prop(mapping, "source_collection", text="", icon='OUTLINER_COLLECTION')
            else:
                row.prop(mapping, "source_object", text="")
            row.label(text="", icon='FORWARD')
            row.prop(mapping, "target_component", text='Part')
            op = row.operator("crossib.remove_mapping", text="", icon='X')
            op.mapping_index = index


class CROSSIB_OT_AddMapping(bpy.types.Operator):
    bl_idname = "crossib.add_mapping"
    bl_label = 'Add CrossIB Mapping'
    bl_options = {'REGISTER', 'UNDO'}

    source_kind: bpy.props.EnumProperty(
        items=[('OBJECT', 'Object', ""), ('COLLECTION', 'Collection', "")],
        default='OBJECT',
    )  # type: ignore

    def execute(self, context):
        mapping = context.scene.crossib_settings.mappings.add()
        mapping.source_kind = self.source_kind
        return {'FINISHED'}


class CROSSIB_OT_RemoveMapping(bpy.types.Operator):
    bl_idname = "crossib.remove_mapping"
    bl_label = 'Delete CrossIB Mapping'
    bl_options = {'REGISTER', 'UNDO'}

    mapping_index: bpy.props.IntProperty()  # type: ignore

    def execute(self, context):
        context.scene.crossib_settings.mappings.remove(self.mapping_index)
        return {'FINISHED'}


class CROSSIB_OT_GenerateSidecars(bpy.types.Operator):
    """Generate CrossIB.json v2 from exactly one FrameAnalysis folder."""

    bl_idname = "crossib.generate_sidecars"
    bl_label = 'Generate / Regenerate CrossIB.json v2'
    bl_options = {'REGISTER'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH')  # type: ignore
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN'})  # type: ignore

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        cfg = getattr(context.scene, "VTEF_settings", None)
        source = getattr(cfg, "object_source_folder", "") if cfg else ""
        source = bpy.path.abspath(source) if source else ""
        dump = bpy.path.abspath(self.directory) if self.directory else ""
        if not source:
            self.report({'ERROR'}, iface_('Object source folder (object_source_folder) not set'))
            return {'CANCELLED'}
        if not dump:
            self.report({'ERROR'}, iface_('No frame dump folder selected'))
            return {'CANCELLED'}

        from .sidecar import regenerate_crossib_json

        ok, message = regenerate_crossib_json(source, dump)
        if not ok:
            self.report({'WARNING'}, iface_(str(message)))
            return {'CANCELLED'}
        settings = getattr(context.scene, "crossib_settings", None)
        if settings is not None:
            settings.frame_dump_folder = self.directory
        self.report({'INFO'}, iface_(str(message)))
        return {'FINISHED'}
