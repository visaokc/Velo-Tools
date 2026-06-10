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
        name="LOD Frame Dump",
        description="Frame dump files directory containing LOD draws. To properly dump character LODs, "
                    "dump the frame while the model is rendered at LOD distance (e.g. walk a few steps away)",
        default='',
        subtype="DIR_PATH",
    )

    allow_lod_overwrite: bpy.props.BoolProperty(
        name="Allow LoD Data Overwrite",
        description="Allow replacement of existing LOD data in Metadata.json on LoD object name collision",
        default=False,
    )

    skip_lods_below_error_threshold: bpy.props.BoolProperty(
        name="Skip LoDs Below Error Threshold",
        description="Skip LoD with similarity percentage below Geometry Matcher Error Threshold "
                    "instead of aborting LoDs extraction process",
        default=False,
    )

    geo_matcher_method: bpy.props.EnumProperty(
        name="Method",
        description="Controls which method is used for geometry-based LoDs search",
        items=[
            ('VOXEL', 'Voxel Matching (Determenistic)', 'Samples mesh as voxel grid of given size'),
            ('POINT_CLOUD', 'Point Cloud Matching (Random)',
             'Uniformly samples random points on a mesh surface using triangle areas.'),
        ],
        default='VOXEL',
    )

    geo_matcher_sensivity: bpy.props.FloatProperty(
        name="Geometry Matcher Sensivity",
        description="LoD mesh matching sensivity. Controls how raw distance values affect resulting percent value",
        default=0.5,
        min=0.25,
        max=1,
        precision=2,
    )

    geo_matcher_voxel_size: bpy.props.FloatProperty(
        name="Geometry Matcher Voxel Size",
        description="Controls into how many voxels meshes will be split. The less the size, the more the voxels, "
                    "the higher precision and the longer calculation time",
        default=0.01,
        min=0.005,
        max=0.1,
        precision=2,
    )

    geo_matcher_voxel_error_threshold: bpy.props.FloatProperty(
        name="Geometry Matcher Error Threshold",
        description="Similarity percentage required for LoD object to pass the check",
        default=55,
        min=25,
        max=100,
        precision=0,
        subtype='PERCENTAGE',
    )

    geo_matcher_sample_size: bpy.props.IntProperty(
        name="Geometry Matcher Sample Size",
        description="Number of uniform geometry samples used for LoD mesh matching",
        default=1000,
        min=500,
        max=5000,
    )

    geo_matcher_error_threshold: bpy.props.FloatProperty(
        name="Geometry Matcher Error Threshold",
        description="Similarity percentage required for LoD object to pass the check",
        default=85,
        min=50,
        max=100,
        precision=0,
        subtype='PERCENTAGE',
    )

    geo_matcher_prefilter_voxel_size: bpy.props.FloatProperty(
        name="Geometry Matcher Prefilter Voxel Size",
        description="Controls into how many voxels meshes will be split during prefilter pass",
        default=0.05,
        min=0.01,
        max=0.2,
        precision=2,
    )

    geo_matcher_prefilter_sample_size: bpy.props.IntProperty(
        name="Geometry Matcher Prefilter Sample Size",
        description="Number of uniform geometry samples used for LoD mesh matching during prefilter pass",
        default=250,
        min=100,
        max=2500,
    )

    geo_matcher_prefilter_candidates_count: bpy.props.IntProperty(
        name="Geometry Matcher Prefilter Candidates Count",
        description="Number of pre-filtered candidates satisfying specified prefilter parameters",
        default=5,
        min=1,
        max=10,
    )

    vg_matcher_candidates_count: bpy.props.IntProperty(
        name="Vertex Groups Matcher Candidates Count",
        description="Number of pre-filtered candidates based on centroid distance used for Vertex Groups matching",
        default=3,
        min=1,
        max=10,
    )

    skip_component_below_vertex_count_enabled: bpy.props.BoolProperty(
        name="Components Filtering: Min Vertex Count",
        description="Exclude LoD candidate components below specified vertex count from LoD matching",
        default=False,
    )

    skip_component_below_vertex_count: bpy.props.IntProperty(
        name="Min Vertex Count",
        description="Exclude LoD candidate components below specified vertex count from LoD matching",
        default=0,
        min=0,
        max=100000,
    )

    skip_object_hashes_enabled: bpy.props.BoolProperty(
        name="Objects Filtering: Blacklisted Hashes",
        description="Exclude candidate objects with specified vb0 hashes from LoD matching (separators: `,` `;` ` `)",
        default=False,
    )

    skip_object_hashes: bpy.props.StringProperty(
        name="",
        description="Exclude candidate objects with specified vb0 hashes from LoD matching (separators: `,` `;` ` `)",
        default="",
    )

    show_advanced: bpy.props.BoolProperty(
        name="Advanced",
        description="Show advanced LOD matching settings",
        default=False,
    )


class VTWW_OT_extract_lod_data(bpy.types.Operator):
    bl_idname = "vtww.extract_lod_data"
    bl_label = "Extract LOD Data"
    bl_description = "提取 LOD 数据：从 LOD 帧 dump 中匹配当前模型的 LOD 对象，并把 LOD 数据写入 Metadata.json"

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
    bl_label = "LOD 数据提取 (Extract LOD Data)"
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
