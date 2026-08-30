# Settings for the Velo raw-mesh tool (own PropertyGroup, isolated from
# VTWW_Settings). Labels are Simplified Chinese; technical terms (Hash/IB/VB/
# Component/MERGED...) stay English, per the WWMI-area l10n convention.

import bpy


class VELO_RawMesh_Settings(bpy.types.PropertyGroup):
    tool_mode: bpy.props.EnumProperty(
        name='Mode',
        items=[
            ('EXTRACT', 'Extract frame data', 'Extract special effects/scene meshes from Frame Dump to the integrated folder according to Hash.'),
            ('IMPORT', 'Import Object', 'Import the consolidated folder into Blender for editing, keeping all vertex attributes.'),
            ('EXPORT', 'Export Mod', 'Export the edited mesh as a usable mod (per-component independent override).'),
        ],
        default='EXTRACT',
    )

    # --- Extract ---
    frame_dump_folder: bpy.props.StringProperty(
        name='Frame Dump Directory',
        description='Directory containing Frame Dump files and log.txt.',
        default='', subtype='DIR_PATH',
    )
    output_folder: bpy.props.StringProperty(
        name='Output directory',
        description='The parent directory of the extracted integrated mesh folder',
        default='', subtype='DIR_PATH',
    )
    hashes: bpy.props.StringProperty(
        name='Hash List',
        description=('IB/VB Hash to extract, separated by commas (e.g., vb0=358cdfe4, ib=ce56ef1a). VB Hash takes the entire VB0 object (automatically split into components); IB Hash takes the component it locks.'),
        default='',
    )
    folder_name: bpy.props.StringProperty(
        name='Folder name',
        description='Name of the consolidated output folder (leave blank to automatically name based on the first VB0 Hash)',
        default='',
    )
    position_override: bpy.props.StringProperty(
        name='Position Element',
        description=('Optional: manually specify which vertex element as Position (e.g., ATTRIBUTE0). Leave blank for automatic determination (3rd/4th float components of slot0/offset0).'),
        default='',
    )
    skip_jpg: bpy.props.BoolProperty(
        name='Texture filtering: skip .jpg', description='Skip .jpg textures; these files are usually gradients or masks.', default=False,
    )
    skip_small: bpy.props.BoolProperty(
        name='Texture filtering: skip small textures', description='Skip texture files smaller than the specified size.', default=False,
    )
    skip_small_kb: bpy.props.IntProperty(
        name='Minimum size KB', description='Texture files smaller than this KB will be skipped; default is 256KB.', default=256, min=0,
    )

    # --- Import (Phase 2) ---
    import_folder: bpy.props.StringProperty(
        name='Object source directory',
        description='Folder of integrated mesh files extracted by this tool to import.',
        default='', subtype='DIR_PATH',
    )

    # --- Export (Phase 3) ---
    export_collection: bpy.props.PointerProperty(
        name='Component Set',
        description='Collection containing raw-mesh objects imported by this tool.',
        type=bpy.types.Collection,
    )
    mod_output_folder: bpy.props.StringProperty(
        name='Mod Output Directory',
        description='Export the generated mod folder',
        default='', subtype='DIR_PATH',
    )
    export_mode: bpy.props.EnumProperty(
        name='Export mode',
        items=[
            ('AUTO', 'Automatic', 'Topology unchanged, using Faithful (byte-accurate); topology changed, using Rebuild'),
            ('FAITHFUL', 'Guarantee the authenticity and direct passage', 'Original Bytes Pass-through; only rewrites edited Position (cannot change topology)'),
            ('REBUILD', 'Rebuild', 'Rebuild according to layout; take standard semantics from Blender, recalculate/fill defaults for the rest (topology can be changed, lossy).'),
        ],
        default='AUTO',
    )


def register():
    bpy.utils.register_class(VELO_RawMesh_Settings)
    bpy.types.Scene.velo_raw_mesh_settings = bpy.props.PointerProperty(type=VELO_RawMesh_Settings)


def unregister():
    if hasattr(bpy.types.Scene, 'velo_raw_mesh_settings'):
        try:
            del bpy.types.Scene.velo_raw_mesh_settings
        except Exception:
            pass
    try:
        bpy.utils.unregister_class(VELO_RawMesh_Settings)
    except Exception:
        pass
