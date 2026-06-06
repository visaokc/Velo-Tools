"""CrossIB UI panel + operators (V0.1.6, adapted to vendored EFMI / Chinese-localized UI).

Mount point: under vendored EFMI's sidebar `VTEF_PT_SIDEBAR`, sibling to the INI toggle panel.
Category: `Velo Tools Endfield` (matching vendored EFMI).
Depends on: `scene.VTEF_settings` (vendored EFMI) + `scene.crossib_settings`.
"""
import bpy


def _vtef_settings_present():
    return hasattr(bpy.types.Scene, "VTEF_settings")


class CROSSIB_PT_Panel(bpy.types.Panel):
    bl_label = "Cross Index Buffer（跨 IB）"
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
        s = getattr(context.scene, "crossib_settings", None)
        if s is None:
            layout.label(text="CrossIB 数据未注册", icon='ERROR')
            return

        layout.prop(s, 'enabled', text="启用跨 IB（Cross Index Buffer）")
        if not s.enabled:
            return

        layout.label(text="左侧（源）借用右侧（目标）的渲染管线", icon='INFO')

        # CrossIB sidecars (CrossIB.json + ShaderOverride.ini): when present, export consumes
        # them directly and skips frame-dump scanning. Old extractions or runs without the
        # generate option checked leave these two files incomplete; a button pops a folder
        # picker to specify a frame dump and auto-complete them (replaces the old persistent
        # frame-dump path field).
        from .sidecar import sidecars_present
        cfg = getattr(context.scene, "VTEF_settings", None)
        source = getattr(cfg, "object_source_folder", "") if cfg else ""
        if source and sidecars_present(source):
            layout.label(text="已检测到 CrossIB.json（导出将直接消费）", icon='CHECKMARK')
            # Show the button even when present: each new scene frame dump fed in accumulates its shaders.
            layout.operator("crossib.generate_sidecars", text="合并新场景 dump（累积）", icon='FILE_FOLDER')
        else:
            layout.label(text="未找到 CrossIB.json（旧提取或未勾选生成）", icon='ERROR')
            layout.operator("crossib.generate_sidecars", text="生成 CrossIB.json（选帧转储）", icon='FILE_FOLDER')

        add_row = layout.row(align=True)
        op_obj = add_row.operator("crossib.add_mapping", text="添加物体映射", icon='ADD')
        op_obj.source_kind = 'OBJECT'
        op_col = add_row.operator("crossib.add_mapping", text="添加集合映射", icon='OUTLINER_COLLECTION')
        op_col.source_kind = 'COLLECTION'

        for i, m in enumerate(s.mappings):
            box = layout.box()
            row = box.row(align=True)
            if m.source_kind == 'COLLECTION':
                row.prop(m, "source_collection", text="", icon='OUTLINER_COLLECTION')
            else:
                row.prop(m, "source_object", text="")
            row.label(text="", icon='FORWARD')
            row.prop(m, "target_component", text="部件")
            op = row.operator("crossib.remove_mapping", text="", icon='X')
            op.mapping_index = i


class CROSSIB_OT_AddMapping(bpy.types.Operator):
    bl_idname = "crossib.add_mapping"
    bl_label = "添加跨 IB 映射"
    bl_options = {'REGISTER', 'UNDO'}

    source_kind: bpy.props.EnumProperty(
        items=[('OBJECT', "物体", ""), ('COLLECTION', "集合", "")],
        default='OBJECT',
    )  # type: ignore

    def execute(self, context):
        s = context.scene.crossib_settings
        m = s.mappings.add()
        m.source_kind = self.source_kind
        return {'FINISHED'}


class CROSSIB_OT_RemoveMapping(bpy.types.Operator):
    bl_idname = "crossib.remove_mapping"
    bl_label = "删除跨 IB 映射"
    bl_options = {'REGISTER', 'UNDO'}

    mapping_index: bpy.props.IntProperty()  # type: ignore

    def execute(self, context):
        context.scene.crossib_settings.mappings.remove(self.mapping_index)
        return {'FINISHED'}


class CROSSIB_OT_GenerateSidecars(bpy.types.Operator):
    """Pop a folder picker to specify one frame dump and generate / accumulate-merge
    CrossIB.json + ShaderOverride.ini for the current object source. When CrossIB.json
    already exists, new-scene shaders are merged in by default (no overwrite) -- feeding
    a character's frame dumps from multiple scenes (normal/dodge/attack/skill) gradually
    completes the shader set. Checking "overwrite rebuild" recomputes from scratch with
    the current classifier."""
    bl_idname = "crossib.generate_sidecars"
    bl_label = "生成 / 合并（累积）CrossIB.json"
    bl_options = {'REGISTER'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH')  # type: ignore
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN'})  # type: ignore
    overwrite: bpy.props.BoolProperty(
        name="覆盖重建（不累积）",
        description="勾上则忽略已有 CrossIB.json、按当前分类器从头重算（velo 分类逻辑升级后刷新用）；默认不勾＝把新场景 shader 并入已有",
        default=False,
    )  # type: ignore

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def draw(self, context):
        self.layout.prop(self, "overwrite")

    def execute(self, context):
        cfg = getattr(context.scene, "VTEF_settings", None)
        source = getattr(cfg, "object_source_folder", "") if cfg else ""
        source = bpy.path.abspath(source) if source else ""
        dump = bpy.path.abspath(self.directory) if self.directory else ""
        if not source:
            self.report({'ERROR'}, "未设置对象源文件夹（object_source_folder）")
            return {'CANCELLED'}
        if not dump:
            self.report({'ERROR'}, "未选择帧转储文件夹")
            return {'CANCELLED'}
        from .sidecar import accumulate_crossib_sidecars
        ok, msg = accumulate_crossib_sidecars(source, dump, overwrite=self.overwrite)
        if ok:
            s = getattr(context.scene, "crossib_settings", None)
            if s is not None:
                s.frame_dump_folder = self.directory
            self.report({'INFO'}, msg)
            return {'FINISHED'}
        # Safe refusal (e.g. new dump doesn't contain this character): leave existing json untouched, emit WARNING instead of crashing.
        self.report({'WARNING'}, msg)
        return {'CANCELLED'}
