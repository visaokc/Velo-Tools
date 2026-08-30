
from velo_tools.i18n import iface_
# "Extract LOD Data" UI for WWMI (velo-owned, registered like crossscene/ui.py;
# nothing added to _wwmi_core's auto_load or tool_mode enum).
#
# The panel is shown whenever the Velo Game tab is active for WWMI, matching
# the other velo-owned WWMI helper panels. Settings mirror EFMI's Extract LOD
# Data panel.

import traceback

import bpy


class VTWW_LodSettings(bpy.types.PropertyGroup):

    lod_frame_dump_folder: bpy.props.StringProperty(
        name='LOD Frame Dump Table of Contents',
        description='The Frame Dump directory where LOD draw is stored. To properly dump character LOD, capture frames when the model is rendering at a LOD distance (for example, by taking a few steps away)',
        default='',
        subtype="DIR_PATH",
    )

    allow_lod_overwrite: bpy.props.BoolProperty(
        name='Allow overwriting LOD data',
        description='Allow replacing existing LOD data in Metadata.json when LOD object names conflict',
        default=False,
    )

    skip_lods_below_error_threshold: bpy.props.BoolProperty(
        name='Skip LOD below the threshold',
        description='Skip this LOD when similarity is below the geometric matching error threshold, instead of interrupting the entire extraction process',
        default=False,
    )

    geo_matcher_method: bpy.props.EnumProperty(
        name='Matching Method',
        description='Select geometric matching algorithm for automatically locating LOD mesh',
        items=[
            ('VOXEL', 'Voxel matching (deterministic)', 'Sample the mesh into a voxel grid of specified size for matching'),
            ('POINT_CLOUD', 'Point Cloud Matching (Random)',
             'Match points by uniformly randomly sampling points on the mesh surface according to triangle area.'),
        ],
        default='VOXEL',
    )

    geo_matcher_sensivity: bpy.props.FloatProperty(
        name='Geometry matching sensitivity',
        description='Control how the raw distance values are mapped to the final similarity percentage.',
        default=0.5,
        min=0.25,
        max=1,
        precision=2,
    )

    geo_matcher_voxel_size: bpy.props.FloatProperty(
        name='Voxel size',
        description='Grid size for voxel matching; the smaller the value, the higher the precision and the slower the computation',
        default=0.01,
        min=0.005,
        max=0.1,
        precision=2,
    )

    geo_matcher_voxel_error_threshold: bpy.props.FloatProperty(
        name='Geometric Matching Error Threshold',
        description='LOD Similarity percentage required for an object to pass voxel matching',
        default=55,
        min=25,
        max=100,
        precision=0,
        subtype='PERCENTAGE',
    )

    geo_matcher_sample_size: bpy.props.IntProperty(
        name='Point Cloud Sampling Number',
        description='Number of points sampled from the mesh surface during point cloud matching',
        default=1000,
        min=500,
        max=5000,
    )

    geo_matcher_error_threshold: bpy.props.FloatProperty(
        name='Geometric Matching Error Threshold',
        description='LOD Similarity percentage required for an object to pass point cloud matching',
        default=85,
        min=50,
        max=100,
        precision=0,
        subtype='PERCENTAGE',
    )

    geo_matcher_prefilter_voxel_size: bpy.props.FloatProperty(
        name='Pre-filter voxel size',
        description='LOD Voxel size used in the candidate pre-filtering stage',
        default=0.05,
        min=0.01,
        max=0.2,
        precision=2,
    )

    geo_matcher_prefilter_sample_size: bpy.props.IntProperty(
        name='Pre-filter sample count',
        description='LOD Number of points sampled in the candidate pre-filtering stage',
        default=250,
        min=100,
        max=2500,
    )

    geo_matcher_prefilter_candidates_count: bpy.props.IntProperty(
        name='Pre-filter candidate count',
        description='Enter the number of candidates for precise geometric matching; the more candidates, the slower it is',
        default=5,
        min=1,
        max=10,
    )

    vg_matcher_candidates_count: bpy.props.IntProperty(
        name='Number of vertex group match candidates',
        description='Preselect candidate numbers based on the centroid distance of vertex groups',
        default=3,
        min=1,
        max=10,
    )

    skip_component_below_vertex_count_enabled: bpy.props.BoolProperty(
        name='Component Filter: Minimum Number of Vertices',
        description='After enabling, exclude components with too few vertices from candidates matched in LOD',
        default=False,
    )

    skip_component_below_vertex_count: bpy.props.IntProperty(
        name='Minimum number of vertices',
        description='LOD Candidate components will be excluded if they have fewer than this number of vertices',
        default=0,
        min=0,
        max=100000,
    )

    skip_object_hashes_enabled: bpy.props.BoolProperty(
        name='Object Filtering: Blacklist Hash',
        description='After enabling, exclude objects of specified vb0 Hash from candidates matched in LOD (separated by commas, semicolons, or spaces)',
        default=False,
    )

    skip_object_hashes: bpy.props.StringProperty(
        name="",
        description='Object vb0 Hash to be excluded from candidates matched from LOD, supporting separation by commas, semicolons, or spaces.',
        default="",
    )

    show_advanced: bpy.props.BoolProperty(
        name='Advanced',
        description='Show advanced LOD matching settings',
        default=False,
    )


class VTWW_OT_extract_lod_data(bpy.types.Operator):
    bl_idname = "vtww.extract_lod_data"
    bl_label = 'Extract LOD Data'
    bl_description = 'Match the LOD object of the current model from the LOD frame dump and write LOD data into Metadata.json.'

    def execute(self, context):
        try:
            from . import extract
            from . import matcher
            summary = extract.run_extract_lod_data(context)
        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, iface_('LOD Data extraction failed: {0}').format(exc))
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            iface_('Imported LOD data for {0}/{1} components from LOD object {2} ({3} use full mesh as LOD).').format(summary['matched_components'], summary['total_components'], summary['lod_object_name'], summary['full_mesh_components'])
        )
        return {'FINISHED'}


class VELO_PT_wwmi_lod(bpy.types.Panel):
    bl_idname = "VELO_PT_wwmi_lod"
    bl_label = 'LOD Data Extraction'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Velo Tools"
    bl_parent_id = "VELO_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        # Shown on the Game tab with WWMI active; independent of the stock WWMI
        # tool mode so helper panels stay available across workflows.
        vt = getattr(context.scene, "velo_tools", None)
        if (vt is None
                or getattr(vt, "active_tab", "") != 'GAME'
                or getattr(vt, "active_game", "") != 'WUTHERING'):
            return False
        wwmi_cfg = getattr(context.scene, "VTWW_settings", None)
        return wwmi_cfg is not None

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
