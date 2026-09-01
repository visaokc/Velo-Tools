"""Canonical English UI msgids for the Velo Endfield bridge."""

VTEF_TOOL_MODE_ITEMS = [
    ("EXPORT_MOD", 'Export Mod', 'Export the selected set as EFMI mod'),
    ("IMPORT_OBJECT", 'Import Object', 'Import .fmt/.ib/.vb models from the extraction directory.'),
    ("EXTRACT_LOD_DATA", 'Extract LOD Data', 'Extract LOD from the open-world Frame Dump and write into Metadata.json.'),
    ("EXTRACT_FRAME_DATA", 'Extract frame data', 'Extract EFMI objects from Frame Dump.'),
]

IMPORT_SKELETON_ITEMS = [
    (
        "MERGED",
        'Merged (Unified Vertex Groups)',
        'Import with unified IDs generated from the skeleton matrices in Metadata v4; optional merging of duplicate bones.',
    ),
    (
        "COMPONENT",
        'Per-Component (Component Standalone)',
        'Each component keeps its own local vertex group number.',
    ),
]

MOD_SKELETON_ITEMS = [
    (
        "MERGED",
        'Merged (Unified Vertex Groups)',
        'Use unified vertex group editing in Blender, export converts back to local numbering according to each Component’s vg_map and uses Per-Component runtime.',
    ),
    (
        "COMPONENT",
        'Per-Component (Component Standalone)',
        'Export by local vertex group number per component.',
    ),
    (
        "MERGED_SKELETON",
        'Merged (Merged Skeleton)',
        'Use EFMI v1.4.1 Merged Skeleton runtime, export with unified IDs, supporting cross-component weights, skeleton scaling, and multiple instances.',
    ),
]

VTEF_PROPERTY_TEXTS = {
    "tool_mode": ('Mode', 'Toggle current Arknights: Endfield EFMI/Velo tool functionality.'),
    "frame_dump_folder": ('Frame Dump Directory', 'Directory containing Frame Dump files and log.txt.'),
    "extract_output_folder": ('Output directory', 'The extracted EFMI objects, textures, and the write directory of Metadata.json.'),
    "import_extracted_objects": ('Import Blender after extraction', 'After extraction is completed, automatically import the object into Blender for quick browsing of the Dump.'),
    "tolerate_extraction_errors": ('Tolerate extraction errors', 'If a single object error occurs during extraction, processing will continue, and the erroneous object will be skipped.'),
    "verbose_logging": ('Detailed log', 'Output more detailed debugging information to the console.'),
    "skip_static_objects": ('Object Filtering: Skip Static Objects', 'Skip static objects without weight data, such as scene props.'),
    "skip_object_min_component_count_enabled": ('Object filtering: Minimum component count', 'After enabling, skip objects with fewer components than the specified value.'),
    "skip_object_min_component_count": ('Minimum number of components', 'Minimum number of components an object must contain to be extracted.'),
    "skip_object_min_texture_count_enabled": ('Object filtering: Minimum texture count', 'After enabling, skip objects with fewer textures than the specified value.'),
    "skip_object_min_texture_count": ('Minimum number of textures', 'Minimum number of textures an object must reference to be extracted.'),
    "skip_object_resource_hashes_enabled": ('Object filtering: Resource Hash', 'After enabling, only keep objects containing the specified resources IB/VB/textures Hash.'),
    "skip_object_resource_hashes": ('Resource Hash', 'Used to filter target resources Hash, supporting separation by commas, semicolons, or spaces.'),
    "skip_draw_resource_hashes_enabled": ('Component Filter: Blacklist Hash', 'After enabling, skip components containing the specified resources Hash when extracting objects.'),
    "skip_draw_resource_hashes": ('Blacklist Hash', 'Component resources Hash to be excluded from normal frame extraction, supporting separation by commas, semicolons, or spaces.'),
    "skip_small_textures": ('Texture filtering: skip small textures', 'Skip texture files smaller than the specified size.'),
    "skip_small_textures_size": ('Minimum size KB', 'Texture files smaller than this KB will be skipped.'),
    "skip_jpg_textures": ('Texture filtering: skip .jpg', 'Skip .jpg textures; these files are usually gradients or masks.'),
    "skip_slot_residual_textures": (
        'Texture filtering: skip Dirty Slot',
        'Retain slots explicitly bound by PSSetShaderResources in the log.txt; stale inherited bindings are removed from STU, TextureUsage.json, texture ownership, and extracted files. If there is no usable log evidence, legacy output is retained and deletion is not guessed.',
    ),
    "auto_skip_lod_components": (
        'Automatically skip LOD components',
        'Before analysis and JSON output, remove components whose raw draw data contains no PS texture bindings, then renumber the remainder continuously. An explicit Object filtering: Resource Hash selection bypasses this automatic filter and preserves native EFMI extraction.',
    ),
    "slot_style_textures": (
        'Slot-style textures',
        'Replace texture Hash overrides with Component-local ps-t slot bindings built from fresh ShaderTextureUsage.json evidence. Export stops if the slot layouts cannot safely distinguish all assignments.',
    ),
    "object_source_folder": ('Object source directory', 'Directory of EFMI objects containing components .fmt/.ib/.vb, Metadata.json, TextureUsage.json.'),
    "color_storage": ('Vertex Color', 'Control how vertex color data of COLOR is saved and displayed during import.'),
    "import_skeleton_type": ('Skeleton', 'Vertex group naming method. Merged uses Metadata v4 built-in vg_map; Per-Component uses local numbering of each component.'),
    "skip_empty_vertex_groups": ('Skip empty vertex group', 'Automatically remove vertex groups with no weight when importing Merged objects.'),
    "dedupe_bones": ('Merge duplicate bones', 'Remap bones with identical matrices to the same unified vertex group. LOD should be turned off when geometry is not equivalent.'),
    "mirror_mesh": ('Mirror Mesh', "Automatically mirror the mesh to match the in-game left-right direction; directly modifies mesh data without changing the object's Transform Scale X."),
    "lod_frame_dump_folder": ('LOD Frame Dump Table of Contents', 'A directory for storing open-world LOD Frame Dump; It must include log.txt.'),
    "allow_lod_overwrite": ('Allow overwriting LOD data', 'Allow replacing existing component LOD data in Metadata.json when there is a conflict in LOD object names or vertex counts.'),
    "geo_matcher_method": ('Matching Method', 'Select geometric matching algorithm for automatically locating LOD mesh.'),
    "geo_matcher_voxel_error_threshold": ('Geometric Matching Error Threshold', 'LOD Similarity percentage required for an object to pass voxel matching.'),
    "geo_matcher_error_threshold": ('Geometric Matching Error Threshold', 'LOD Similarity percentage required for an object to pass point cloud matching.'),
    "import_matched_lod_objects": ('Import matched LOD', 'Import the automatically matched LOD mesh into Blender for convenient manual inspection.'),
    "skip_lods_below_error_threshold": ('Skip LOD below the threshold', 'Skip this LOD when similarity is below the geometric matching error threshold, instead of interrupting the entire extraction process.'),
    "geo_matcher_sensivity": ('Geometry matching sensitivity', 'Control how the raw distance values are mapped to the final similarity percentage.'),
    "skip_component_below_vertex_count_enabled": ('Component Filter: Minimum Number of Vertices', 'After enabling, exclude components with too few vertices from candidates matched in LOD.'),
    "skip_component_below_vertex_count": ('Minimum number of vertices', 'LOD Candidate components will be excluded if they have fewer than this number of vertices.'),
    "skip_component_hashes_enabled": ('Component Filter: Blacklist Hash', 'After enabling, exclude specified IB Hash components from candidates matched in LOD.'),
    "skip_component_hashes": ('Blacklist Hash', 'IB Hash to be excluded from candidates matched from LOD, supporting separation by commas, semicolons, or spaces.'),
    "geo_matcher_voxel_size": ('Voxel size', 'Grid size for voxel matching; the smaller the value, the higher the precision and the slower the computation.'),
    "geo_matcher_sample_size": ('Point Cloud Sampling Number', 'Number of points sampled from the mesh surface during point cloud matching.'),
    "geo_matcher_prefilter_voxel_size": ('Pre-filter voxel size', 'LOD Voxel size used in the candidate pre-filtering stage.'),
    "geo_matcher_prefilter_sample_size": ('Pre-filter sample count', 'LOD Number of points sampled in the candidate pre-filtering stage.'),
    "geo_matcher_prefilter_candidates_count": ('Pre-filter candidate count', 'Enter the number of candidates for precise geometric matching; the more candidates, the slower it is.'),
    "vg_matcher_candidates_count": ('Number of vertex group match candidates', 'Preselect candidate numbers based on the centroid distance of vertex groups.'),
    "component_collection": ('Component Set', 'Collection of Blender containing Component 0, Component_1, and other EFMI component objects.'),
    "mod_output_folder": ('Mod Output Directory', 'Write to Mod directory of mod.ini, Meshes, and Textures.'),
    "mod_skeleton_type": ('Skeleton', 'Select Merged (unified vertex group), Per-Component, or Merged (merged skeleton).'),
    "apply_all_modifiers": ('Apply all modifiers', 'Apply all visible modifiers on the temporary copy during export.'),
    "copy_textures": ('Copy the sticker', 'Copy referenced texture files to the Mod output directory during export.'),
    "write_ini": ('Write out mod.ini', 'Write a new mod.ini to the output directory during export.'),
    "comment_ini": ('Write comment', 'Write comments in the generated INI to make the structure easier to read.'),
    "ignore_nested_collections": ('Ignore Nested Sets', 'After enabling, objects in sub-collections within the component collection will not be exported.'),
    "ignore_hidden_collections": ('Ignore hidden collection', 'After enabling, objects in hidden sub-collections within the component collection will not be exported.'),
    "ignore_hidden_objects": ('Ignore hidden object', 'After enabling, hidden objects in the component collection will not be exported.'),
    "ignore_muted_shape_keys": ('Ignore disabling ShapeKey', 'After enabling, unchecked ShapeKey will not be exported.'),
    "allow_export_without_lods": ('Allow export without LOD', 'Allow exporting Metadata.json even when there is no LOD data; may not load correctly in the open world.'),
    "add_missing_vertex_groups": ('Supplement missing vertex groups', 'Fill in missing items in the middle according to vertex group numbers, for example, if 0 and 2 exist, fill in 1.'),
    "fill_missing_mesh_data": ('Fill Missing Mesh Data', 'Automatically generate missing COLOR black vertex color and empty TEXCOORD.xy.'),
    "max_instance_count": ('Maximum number of instances', 'Merged Skeleton Supported Maximum Instances on the Same Object; Each Instance Reserves VRAM According to the Unified Number of Bones.'),
    "use_spatial_identification": ('Spatial identity recognition', 'Used to verify component ownership by position, preventing accidental modification of components shared by multiple objects such as shadows; only supports weighted objects.'),
    "spatial_identification_threshold": ('Spatial recognition component threshold', 'Number of components that must appear before forming a new spatial identity; should be more than the number of non-unique meshes and not exceed the number of LOD0 meshes used in LOD1+.'),
    "skeleton_scale": ('Skeleton Scaling', 'Scale the model in the game; Per-Component skeleton is not supported.'),
    "partial_export": ('Partial export', 'Advanced Usage: Only export the selected buffer, skipping INI generation and resource copying.'),
    "export_index": ("Index Buffer", 'Export index buffer, saving vertex and face associations.'),
    "export_positions": ("Position Buffer", 'Export location buffer, saving each vertex coordinate.'),
    "export_blends": ("Blend Buffer", 'Export bone numbers and weights buffer.'),
    "export_vectors": ("Vector Buffer", 'Export normals and tangents buffer.'),
    "export_colors": ("Color Buffer", 'Export COLOR vertex color buffer.'),
    "export_texcoords": ("TexCoord Buffer", 'Export UV and COLOR1 vertex color buffer.'),
    "export_shapekeys": ("ShapeKey Buffer", 'Export ShapeKey related buffer.'),
    "mod_name": ('Mod Name', 'The name displayed in notifications and the Mod manager.'),
    "mod_author": ('Author', 'The author name displayed in notifications and the Mod manager.'),
    "mod_desc": ('Mod Description', 'The short description displayed in notifications and the Mod manager.'),
    "mod_link": ('Mod Link', 'The web link displayed in notifications and the Mod manager.'),
    "mod_logo": ('Mod Icon', '512x512 .dds icon texture (BC7 SRGB), export to Textures/Logo.dds.'),
    "use_custom_template": ('Use a custom template', 'Generate a complete mod.ini using the specified jinja2 template.'),
    "custom_template_live_update": ('Template real-time update', 'Control whether the INI template real-time generation thread is running.'),
    "custom_template_source": ('Template storage', 'Select storage location for custom INI template.'),
    "custom_template_path": ('Template file', 'External mod.ini template file path. When creating a new template, you can first copy the default content from the built-in editor.'),
    "use_ini_toggles": ('Use INI switch', 'Write the configured INI switch logic into mod.ini.'),
    "import_as_component_collections": ('Create sub-collection by component', "When importing the model, create C0/C1/... sub-collections under the object's parent collection; after turning it off, continue to use the single-collection import from upstream EFMI."),
    "import_named_skeleton": ('Import bone-name mapping and skeleton', 'Use BoneNameMapping.json and BoneNameSkeleton.glb to replace numeric vertex groups with original bone names and bind the complete skeleton. When disabled, both sidecars are ignored.'),
    "rename_mirror_pairs": ('Rename mirrored bones to .L/.R suffixes', 'For unambiguous mirror pairs whose names differ only by one L/R character, rename both bones and vertex groups to Blender-compatible .L/.R suffixes.'),
    "extract_components_filter": ('Component Filter (Keep)', 'Optional Component indices or ranges to retain for downstream analysis and output, for example 0-8 or 0,1,5-7. Indices are evaluated after automatic LOD filtering.'),
    "extract_components_skip_filter": ('Component Filter (Skip)', 'Optional Component indices or ranges to exclude from downstream analysis and output, for example 4,6 or 4-6. Indices are evaluated after automatic LOD filtering, and Skip takes precedence over Keep.'),
    "import_texture": ('Import Texture', 'After importing the model, assign the .dds texture in the source directory to the mesh according to TextureUsage.json.'),
}

VTEF_ENUM_ITEMS = {
    "color_storage": [
        ("LINEAR", "Linear", 'Display vertex colors in true linear color, and store in color_attributes with full float precision.'),
        ("LEGACY", 'sRGB (Old Version)', 'Display vertex colors with sRGB offset and store in old vertex_colors with 8-bit float precision.'),
    ],
    "geo_matcher_method": [
        ("VOXEL", 'Voxel matching (deterministic)', 'Sample the mesh into a voxel grid of specified size for matching.'),
        ("POINT_CLOUD", 'Point Cloud Matching (Random)', 'Match points by uniformly randomly sampling points on the mesh surface according to triangle area.'),
    ],
    "custom_template_source": [
        ("INTERNAL", 'Built-in editor', 'Use text in Blender text editor as a custom template.'),
        ("EXTERNAL", 'External files', 'Use a specified external file as a custom template.'),
    ],
}

VTEF_CLASS_TEXTS = {
    "VTEF_PT_SidePanelAdvancedLodsExtraction": ('Advanced', None),
    "VTEF_PT_SidePanelLodsExtractionFooter": ('Extract', None),
    "VTEF_PT_SidePanelPartialExport": ('Partial export', None),
    "VTEF_PT_SidePanelAdvancedExport": ('Advanced', None),
    "VTEF_PT_SidePanelModInfo": ('Mod Information', None),
    "VTEF_PT_SidePanelIniTemplate": ('INI Template', None),
    "VTEF_PT_SidePanelExportFooter": ('Export', None),
    "VTEF_Import": ('Import Model', 'Import EFMI object from the extraction directory.'),
    "VTEF_Export": ('Export Mod', 'Export the current component set as EFMI Mod.'),
    "VTEF_ExtractFrameData": ('Extract the model from Dump.', 'Extract available EFMI objects from the current Frame Dump.'),
    "VTEF_ImportLODData": ('Extract LOD from Dump.', 'Match and write LOD data from LOD to Frame Dump.'),
    "VTEF_OpenIniTemplateEditor": ('Edit template', 'Open the current INI template for viewing or editing.'),
    "VTEF_IniTemplateEditor_ToggleLiveUpdates": ('Start INI update', 'Toggle real-time updates for the external INI template.'),
    "VTEF_IniTemplateEditor_Reset": ('Reset template', 'Reset the built-in INI template to default content.'),
    "VTEF_PT_TEXT_EDITOR_IniTemplate": ('INI Template - Velo Tools Terminal', None),
}

INI_TOGGLE_CLASS_TEXTS = {
    "VTEF_PT_SidePanelIniToggles": ('INI Switch', None),
    "VTEF_CollapseToggleVars": ('Foldable Variables', 'Collapse all switch variables in the list and exit edit mode.'),
    "VTEF_ExpandToggleVars": ('Expand variables', 'Expand all switch variables in the list and enter edit mode.'),
    "VTEF_AddToggleVar": ('Add switch variable', 'Add a INI switch variable to control object visibility.'),
    "VTEF_RemoveToggleVar": ('Delete switch variable', 'Delete this variable from the INI switch list.'),
    "VTEF_MoveToggleVar": ('Move switch variable', 'Adjust the order of this switch variable in the list.'),
    "VTEF_EditToggleVar": ('Edit variable', 'Configure variable hotkeys and default states.'),
    "VTEF_AddVarState": ('Add state', 'Add a new state for this variable; each state can control multiple objects.'),
    "VTEF_RemoveVarState": ('Delete state', 'Delete the current state from this INI switch variable.'),
    "VTEF_MoveToggleVarState": ('Move state', 'Adjust the order of the current state in the variable state list.'),
    "VTEF_AddVarStateObject": ('Add state object', 'Add the object to the current state so that it displays according to the conditions of that state.'),
    "VTEF_RemoveVarStateObject": ('Delete state object', 'Remove this object from the current state.'),
    "VTEF_EditVarStateObject": ('Edit conditions', 'Open the custom display conditions window for the current object.'),
    "VTEF_AddCondition": ('Add condition', 'Add a new custom display condition.'),
    "VTEF_RemoveCondition": ('Delete condition', 'Delete current custom display condition.'),
    "VTEF_OpenIniTogglesImportExportEditor": ('Open INI switch import/export', 'Open the text editor window for importing or exporting INI switch variables.'),
    "VTEF_ExportIniToggles": ('Export INI switch', 'Export the current INI switch variables as JSON text that can be re-imported.'),
    "VTEF_ImportIniToggles": ('Import INI Switch', 'Import INI switch variables from the JSON in the current text file.'),
    "VTEF_PT_TEXT_EDITOR_IniToggles": ('INI Switch - Velo Tools', None),
}

INI_TOGGLE_PROPERTY_TEXTS = {
    "IniToggles": {
        "replace_vars_on_import": ('Replace variables with the same name on import', 'When importing INI Switch, replace existing variables with the same name with the imported content; skip duplicates when off.'),
        "clear_vars_on_import": ('Clear variables before import', 'Delete all existing variables before importing INI Switch.'),
        "hide_empty_states": ('Hide -1 states with no objects', 'Hide -1 empty states with no objects to reduce UI space usage.'),
        "hide_default_conditions": ('Hide default conditions', 'Hide automatically generated default conditions to reduce UI space usage.'),
    },
    "ToggleVar": {
        "default_state": ('Default state', 'The initial state value used by this switch variable.'),
        "hotkeys": ('Hotkeys', 'Keys used to switch between multiple states; key combinations are separated by spaces, and multiple hotkeys are separated by semicolons.'),
        "ui_expanded": ('Expand variables', 'Enter or exit the edit state of this variable.'),
    },
    "ToggleVarStateCondition": {
        "logic": ('Logic', 'Control how multiple conditions collectively affect object display.'),
        "type": ('Type', 'Select to bind existing INI switch variable, or bind custom INI variable.'),
        "var": ('Variable', 'Variable to compare with the status value. Prefix custom variable names with $ to prevent automatic formatting as $swapvar_name.'),
        "operator": ('Compare', 'Control how variables and values are compared to determine if the condition is met.'),
        "state": ('Status Value', 'Status value used for comparison with variables.'),
    },
    "ToggleVarStateObject": {
        "object": ('Objects', 'Under default conditions, selected objects will only be displayed when the variable equals the current state value.'),
    },
}

INI_TOGGLE_ENUM_ITEMS = {
    ("ToggleVarStateCondition", "logic"): [
        ("&&", "AND", 'The object is displayed only when both the current condition and the previous condition are TRUE; AND is calculated before OR.'),
        ("||", "OR", 'As long as the current condition is TRUE, the object can be displayed; AND is calculated before OR.'),
    ],
    ("ToggleVarStateCondition", "type"): [
        ("TOGGLE", 'Toggle variable', 'Bind to an existing INI switch variable.'),
        ("EXTERNAL", 'Custom variable', 'Bind to a custom INI variable.'),
    ],
    ("ToggleVarStateCondition", "operator"): [
        ("==", "==", 'Variable must be equal to the specified value.'),
        ("!=", "!=", 'Variable must not equal the specified value.'),
        (">", ">", 'Variable must be greater than the specified value.'),
        ("<", "<", 'Variable must be less than the specified value.'),
        (">=", ">=", 'Variable must be greater than or equal to the specified value.'),
        ("<=", "<=", 'Variable must be less than or equal to the specified value.'),
    ],
}

CROSSIB_PROPERTY_TEXTS = {
    "CrossIBMapping": {
        "source_kind": ('Source Type', 'Select a single source object or an entire source collection to provide cross-IB geometry.'),
        "source_object": ('Source Object', 'A single mesh object as the provider.'),
        "source_collection": ('Source Collection', 'Each mesh in the collection will act as a provider.'),
        "target_component": ('Target part', 'Redraw the source geometry onto which target Component.'),
    },
    "CrossIBSettings": {
        "enabled": ('Enable cross-IB (CrossIB).', "Enable CrossIB rendering during export, connecting the source geometry to the target component's rendering pipeline."),
        "frame_dump_folder": ('Frame Dump folder', 'Optional. Manually specify the original frame dump; if left blank, it will be automatically scanned from the model source directory and parent directories.'),
    },
}

CROSSIB_ENUM_ITEMS = {
    ("CrossIBMapping", "source_kind"): [
        ("OBJECT", 'Object', 'Use a single mesh object as the provider.'),
        ("COLLECTION", 'Collection', 'Use all meshes in the collection as the provider.'),
    ],
}

CROSSIB_CLASS_TEXTS = {
    "CROSSIB_PT_Panel": ('Cross Index Buffer (Cross IB)', None),
    "CROSSIB_OT_AddMapping": ('Add CrossIB Mapping', 'Add a mapping from a CrossIB source to the target component.'),
    "CROSSIB_OT_RemoveMapping": ('Delete CrossIB Mapping', 'Delete current CrossIB mapping.'),
}

SHAPEKEY_PROPERTY_TEXTS = {
    "ShapeKeySettings": {
        "enabled": (
            'Export custom ShapeKey',
            'After enabling, add persistent control variables for EFMI v0.6.4 official ShapeKey export; position interpolation and runtime processing are handled by the official core.',
        ),
        "merge_buffers": (
            'Merge Buffer file',
            'Merge the ShapeKey deltas/maps within the same Component into fewer buffer to reduce the number of Meshes directory files.',
        ),
        "show_detector": ('Show recognized ShapeKey', 'Expand or collapse the real-time recognition list below.'),
    },
}

SHAPEKEY_CLASS_TEXTS = {
    "SHAPEKEY_OT_ToggleGroup": ('Collapse/expand groups.', 'Fold or unfold the Deform group in the detection list.'),
    "SHAPEKEY_OT_RefreshDetected": ('Refresh recognized ShapeKey', 'Rescan the Deform ShapeKey in the current component set.'),
}

TOOLBOX_CLASS_TEXTS = {
    "VTEF_MergeVertexGroups": (
        'Merge vertex groups with the same name',
        'Merge vertices with the same name before the decimal suffix in the selected objects, for example, merge 7, 7.1, 7.3 into the same group.',
    ),
    "VTEF_FillGapsInVertexGroups": (
        'Fill in gaps in vertex group numbering',
        'Complete the missing numeric vertex groups for the selected object and sort them by number. For example, if there are 0, 4, 2, fill in 1 and 3.',
    ),
    "VTEF_RemoveUnusedVertexGroups": (
        'Remove unused vertex groups',
        'Remove vertex groups with no weights from the selected object.',
    ),
    "VTEF_RemoveAllVertexGroups": (
        'Remove all vertex groups',
        'Remove all vertex groups from the selected object.',
    ),
    "VTEF_ApplyModifierForObjectWithShapeKeysOperator": (
        'Apply modifier with ShapeKey',
        'Apply the selected modifier to objects with ShapeKey and remove it from the modifier stack, used to bypass the restriction that Blender does not allow directly applying this type of modifier',
    ),
    "VTEF_CreateMergedObject": (
        'Create merged sculpted object',
        'Temporarily merge selected objects into a single object for multi-object sculpting. Do not add or remove vertices on the original objects before completion.',
    ),
    "VTEF_ApplyMergedObjectSculpt": (
        'Write back merged sculpt.',
        'Write back the vertex position changes from the merged sculpt object to the original object.',
    ),
    "VTEF_ApplyMergedObjectSculptWithShapekeys": (
        'Write back sculpt to ShapeKey.',
        'Write back the vertex position changes of the merged sculpted objects to the original objects and apply the position differences to all ShapeKey.',
    ),
    "VTEF_ConvertVertexColors": (
        'Convert vertex color to Linear',
        'Migrate the COLOR and COLOR1 vertex color layers of the selected objects to the new Linear storage method.',
    ),
    "VTEF_FillMissingMeshData": (
        'Fill Missing Mesh Data',
        'Generate the missing COLOR black vertex colors and empty TEXCOORD.xy data for the selected object.',
    ),
}

TOOLBOX_PROPERTY_TEXTS = {
    "VTEF_ApplyModifierForObjectWithShapeKeysOperator": {
        "disable_armatures": (
            'Does not include skeletal deformation',
            'Skip Armature deformation when applying the modifier to avoid baking the skeleton pose into a mesh with ShapeKey.',
        ),
    },
}

TOOLBOX_DIALOG_TEXTS = {
    "VTEF_ApplyModifierForObjectWithShapeKeysOperator": {
        "no_modifier_selected": 'No modifier selected.',
        "animation_warning": (
            'Warning:',
            'The object’s ShapeKey contains animation data',
            '(for example, drivers, keyframes, etc.)',
            'These data will be lost when applying the modifier.',
        ),
    },
}
