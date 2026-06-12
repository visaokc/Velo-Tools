# "Extract LOD Data" UI for WWMI (velo-owned, registered like crossscene/ui.py;
# nothing added to _wwmi_core's auto_load or tool_mode enum).
#
# The panel shows up only while the WWMI section is in the
# "Extract Objects From Dump" mode, so it reads as the follow-up step of the
# extraction workflow (per user decision: standalone sub-panel, no extra
# tool_mode switch). Settings mirror EFMI's Extract LOD Data panel.

import traceback

import bpy


class VTWW_LodSettings(bpy.types.PropertyGroup):

    lod_frame_dump_folder: bpy.props.StringProperty(
        name="LOD Frame Dump 目录",
        description="存放 LOD draw 的 Frame Dump 目录。要正确 dump 角色 LOD，"
                    "请在模型以 LOD 距离渲染时抓帧（例如走远几步）",
        default='',
        subtype="DIR_PATH",
    )

    allow_lod_overwrite: bpy.props.BoolProperty(
        name="允许覆盖 LOD 数据",
        description="允许在 LOD 对象名冲突时替换 Metadata.json 中已有的 LOD 数据",
        default=False,
    )

    skip_lods_below_error_threshold: bpy.props.BoolProperty(
        name="低于阈值的 LOD 跳过",
        description="相似度低于几何匹配误差阈值时跳过该 LOD，而不是中断整个提取流程",
        default=False,
    )

    geo_matcher_method: bpy.props.EnumProperty(
        name="匹配方法",
        description="选择用于自动查找 LOD 网格的几何匹配算法",
        items=[
            ('VOXEL', '体素匹配（确定性）', '把网格采样为指定大小的体素网格进行匹配'),
            ('POINT_CLOUD', '点云匹配（随机）',
             '按三角面面积在网格表面均匀随机采样点进行匹配'),
        ],
        default='VOXEL',
    )

    geo_matcher_sensivity: bpy.props.FloatProperty(
        name="几何匹配灵敏度",
        description="控制原始距离值如何映射到最终相似度百分比",
        default=0.5,
        min=0.25,
        max=1,
        precision=2,
    )

    geo_matcher_voxel_size: bpy.props.FloatProperty(
        name="体素大小",
        description="体素匹配时的网格划分大小；数值越小精度越高、计算越慢",
        default=0.01,
        min=0.005,
        max=0.1,
        precision=2,
    )

    geo_matcher_voxel_error_threshold: bpy.props.FloatProperty(
        name="几何匹配误差阈值",
        description="LOD 对象通过体素匹配所需的相似度百分比",
        default=55,
        min=25,
        max=100,
        precision=0,
        subtype='PERCENTAGE',
    )

    geo_matcher_sample_size: bpy.props.IntProperty(
        name="点云采样数",
        description="点云匹配时从网格表面采样的点数",
        default=1000,
        min=500,
        max=5000,
    )

    geo_matcher_error_threshold: bpy.props.FloatProperty(
        name="几何匹配误差阈值",
        description="LOD 对象通过点云匹配所需的相似度百分比",
        default=85,
        min=50,
        max=100,
        precision=0,
        subtype='PERCENTAGE',
    )

    geo_matcher_prefilter_voxel_size: bpy.props.FloatProperty(
        name="预过滤体素大小",
        description="LOD 候选预过滤阶段使用的体素大小",
        default=0.05,
        min=0.01,
        max=0.2,
        precision=2,
    )

    geo_matcher_prefilter_sample_size: bpy.props.IntProperty(
        name="预过滤采样数",
        description="LOD 候选预过滤阶段使用的点云采样数",
        default=250,
        min=100,
        max=2500,
    )

    geo_matcher_prefilter_candidates_count: bpy.props.IntProperty(
        name="预过滤候选数",
        description="进入精确几何匹配的候选数量；数量越多越慢",
        default=5,
        min=1,
        max=10,
    )

    vg_matcher_candidates_count: bpy.props.IntProperty(
        name="顶点组匹配候选数",
        description="按顶点组重心距离预选的候选数量",
        default=3,
        min=1,
        max=10,
    )

    skip_component_below_vertex_count_enabled: bpy.props.BoolProperty(
        name="组件过滤：最少顶点数",
        description="启用后从 LOD 匹配候选中排除顶点数过少的组件",
        default=False,
    )

    skip_component_below_vertex_count: bpy.props.IntProperty(
        name="最少顶点数",
        description="LOD 候选组件低于该顶点数时会被排除",
        default=0,
        min=0,
        max=100000,
    )

    skip_object_hashes_enabled: bpy.props.BoolProperty(
        name="对象过滤：黑名单 Hash",
        description="启用后从 LOD 匹配候选中排除指定 vb0 Hash 的对象（逗号、分号或空格分隔）",
        default=False,
    )

    skip_object_hashes: bpy.props.StringProperty(
        name="",
        description="要从 LOD 匹配候选中排除的对象 vb0 Hash，支持用逗号、分号或空格分隔",
        default="",
    )

    show_advanced: bpy.props.BoolProperty(
        name="高级",
        description="显示高级 LOD 匹配设置",
        default=False,
    )


class VTWW_OT_extract_lod_data(bpy.types.Operator):
    bl_idname = "vtww.extract_lod_data"
    bl_label = "提取 LOD 数据"
    bl_description = "从 LOD 帧 dump 中匹配当前模型的 LOD 对象，并把 LOD 数据写入 Metadata.json"

    def execute(self, context):
        try:
            from . import extract
            from . import matcher
            summary = extract.run_extract_lod_data(context)
        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, f"LOD 数据提取失败：{exc}")
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"Imported LOD data for {summary['matched_components']}/{summary['total_components']} components "
            f"from LOD object {summary['lod_object_name']} "
            f"({summary['full_mesh_components']} use full mesh as LOD)."
        )
        return {'FINISHED'}


class VELO_PT_wwmi_lod(bpy.types.Panel):
    bl_idname = "VELO_PT_wwmi_lod"
    bl_label = "LOD 数据提取"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Velo Tools"
    bl_parent_id = "VELO_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        # Shown on the Game tab with WWMI active, only while the WWMI section is in
        # "Extract Objects From Dump" mode (reads as the next step of that workflow).
        vt = getattr(context.scene, "velo_tools", None)
        if (vt is None
                or getattr(vt, "active_tab", "") != 'GAME'
                or getattr(vt, "active_game", "") != 'WUTHERING'):
            return False
        wwmi_cfg = getattr(context.scene, "VTWW_settings", None)
        return wwmi_cfg is not None and wwmi_cfg.tool_mode == 'EXTRACT_FRAME_DATA'

    def draw(self, context):
        layout = self.layout
        cfg = context.scene.vtww_lod_settings
        wwmi_cfg = context.scene.VTWW_settings

        layout.row().prop(cfg, 'lod_frame_dump_folder')
        layout.row().prop(wwmi_cfg, 'object_source_folder')

        layout.separator()

        row = layout.row(align=True)
        row.prop(cfg, "skip_component_below_vertex_count_enabled")
        sub = row.row()
        sub.enabled = cfg.skip_component_below_vertex_count_enabled
        sub.prop(cfg, "skip_component_below_vertex_count")

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_hashes_enabled")
        sub = row.row()
        sub.enabled = cfg.skip_object_hashes_enabled
        sub.prop(cfg, "skip_object_hashes")

        if cfg.geo_matcher_method == 'VOXEL':
            layout.row().prop(cfg, 'geo_matcher_voxel_error_threshold')
        else:
            layout.row().prop(cfg, 'geo_matcher_error_threshold')

        box = layout.box()
        box.prop(cfg, 'show_advanced', icon='TRIA_DOWN' if cfg.show_advanced else 'TRIA_RIGHT', emboss=False)
        if cfg.show_advanced:
            box.row().prop(cfg, 'allow_lod_overwrite')
            box.row().prop(cfg, 'skip_lods_below_error_threshold')
            box.separator()
            box.row().prop(cfg, 'geo_matcher_method')
            box.row().prop(cfg, 'geo_matcher_sensivity')
            if cfg.geo_matcher_method == 'VOXEL':
                box.row().prop(cfg, 'geo_matcher_voxel_size')
                box.row().prop(cfg, 'geo_matcher_prefilter_voxel_size')
            else:
                box.row().prop(cfg, 'geo_matcher_sample_size')
                box.row().prop(cfg, 'geo_matcher_prefilter_sample_size')
            box.row().prop(cfg, 'geo_matcher_prefilter_candidates_count')
            box.row().prop(cfg, 'vg_matcher_candidates_count')

        layout.separator()
        layout.row().operator(VTWW_OT_extract_lod_data.bl_idname, icon='MOD_DECIM')


_CLASSES = (
    VTWW_LodSettings,
    VTWW_OT_extract_lod_data,
    VELO_PT_wwmi_lod,
)


def register():
    for cls in _CLASSES:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            traceback.print_exc()
    bpy.types.Scene.vtww_lod_settings = bpy.props.PointerProperty(type=VTWW_LodSettings)


def unregister():
    if hasattr(bpy.types.Scene, "vtww_lod_settings"):
        try:
            del bpy.types.Scene.vtww_lod_settings
        except Exception:
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
