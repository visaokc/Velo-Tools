"""Canonical English UI msgids for the Velo WWMI bridge.

Consumed by the driver (__init__.py) which patches class bl_label /
bl_description and rebuilds property annotations BEFORE registration.
tool_mode / mod_skeleton_type texts live in the driver itself (they are
velo-owned re-definitions, not core patches) and are NOT listed here.
Simplified Chinese translations live only in ``velo_tools.i18n.zh``.
"""

# VTWW_Settings property name/description, keyed by property identifier.
WWMI_PROPERTY_TEXTS = {
    # Extract Frame Data
    "frame_dump_folder": ('Frame Dump Directory', 'Directory containing Frame Dump files and log.txt.'),
    "skip_small_textures": ('Texture filtering: skip small textures', 'Skip texture files smaller than the specified size.'),
    "skip_small_textures_size": ('Minimum size KB', 'Texture files smaller than this KB will be skipped; default is 256KB.'),
    "skip_jpg_textures": ('Texture filtering: skip .jpg', 'Skip .jpg textures; these files are usually gradients or masks.'),
    "skip_same_slot_hash_textures": (
        'Texture filtering: skip same slot same Hash',
        "If a certain texture's Hash appears in the same slot of all components, it will be skipped. Useful textures may be filtered out!",
    ),
    "skip_known_cubemap_textures": (
        'Texture filtering: skip known Cubemap',
        'Skip Hash textures that are in the known cubemap list; these textures often fail to load correctly.',
    ),
    "extract_output_folder": ('Output directory', 'The write directory of the extracted WWMI objects.'),
    # Object Import
    "object_source_folder": ('Object source directory', 'Directory containing WWMI object components and textures.'),
    "color_storage": ('Vertex Color', 'Control how vertex color data is saved and displayed during import.'),
    "import_skeleton_type": ('Skeleton', 'The processing method of vertex groups.'),
    "skip_empty_vertex_groups": (
        'Skip empty vertex group',
        "Automatically remove vertex groups without weights from imported components, so that each component's vertex group list only contains items actually used.",
    ),
    "mirror_mesh": (
        'Mirror Mesh',
        "Automatically mirror the mesh to match the in-game left-right direction; directly modifies mesh data without changing the object's Transform Scale X.",
    ),
    # Mod Export
    "component_collection": ('Component Set', 'Collection of Blender containing Component 0, Component_1, and other WWMI component objects.'),
    "mod_output_folder": ('Mod Output Directory', 'Write to Mod directory of mod.ini, Meshes, and Textures.'),
    "apply_all_modifiers": ('Apply all modifiers', "Apply all visible modifiers on each object's temporary copy during export."),
    "copy_textures": ('Copy the sticker', 'Copy referenced texture files to the Mod output directory during export.'),
    "write_ini": ('Write out mod.ini', 'Write a new mod.ini to the output directory during export.'),
    "comment_ini": ('Write comment', 'Write comments in the generated INI to make the structure easier to read.'),
    "ignore_nested_collections": ('Ignore Nested Sets', 'After enabling, objects in sub-collections within the component collection will not be exported.'),
    "ignore_hidden_collections": ('Ignore hidden collection', 'After enabling, objects in hidden sub-collections within the component collection will not be exported.'),
    "ignore_hidden_objects": ('Ignore hidden object', 'After enabling, hidden objects in the component collection will not be exported.'),
    "ignore_muted_shape_keys": ('Ignore disabling ShapeKey', 'After enabling, unchecked ShapeKey will not be exported.'),
    # Advanced
    "add_missing_vertex_groups": (
        'Supplement missing vertex groups',
        'Fill in missing items in the middle according to vertex group numbers, for example, if 0 and 2 exist, fill in 1.',
    ),
    "fill_missing_mesh_data": (
        'Fill Missing Mesh Data',
        'Automatically generate missing COLOR (filled with [0, 0.25, 0, 1.0]), TEXCOORD.xy (empty UV), TEXCOORD1.xy (empty UV), TEXCOORD2.xy (copy of TEXCOORD.xy), and TEXCOORD3.xy (front projection).',
    ),
    "unrestricted_custom_shape_keys": (
        'Export custom ShapeKey',
        "Enabled by default. When enabled, ShapeKey with valid offsets that exceed the native Metadata numbering range will be exported through a separate shader; when disabled, only the game's native numbering range will be exported. No additional resources are generated if there are no valid custom ShapeKey.",
    ),
    "skeleton_scale": ('Skeleton Scaling', 'Scale the model in the game (default 1.0); Per-Component skeleton is not supported.'),
    "partial_export": (
        'Partial export',
        'Advanced Usage: Only export the selected buffer; confirming that certain data have not changed since the last export can speed up the export. INI generation and resource copying will be disabled.',
    ),
    # Partial Export
    "export_index": ("Index Buffer", 'Export index buffer, saving vertex and face associations.'),
    "export_positions": ("Position Buffer", 'Export location buffer, saving each vertex coordinate.'),
    "export_blends": ("Blend Buffer", 'Export vertex group numbers and weights buffer.'),
    "export_vectors": ("Vector Buffer", 'Export normals and tangents buffer.'),
    "export_colors": ("Color Buffer", 'Export vertex color buffer named COLOR.'),
    "export_texcoords": ("TexCoord Buffer", 'Export TEXCOORD UV layer buffer.'),
    "export_shapekeys": ("ShapeKey Buffer", 'Export ShapeKey related buffer.'),
    # Mod Info
    "mod_name": ('Mod Name', 'The name displayed in notifications and the Mod manager.'),
    "mod_author": ('Author', 'The author name displayed in notifications and the Mod manager.'),
    "mod_desc": ('Mod Description', 'The short description displayed in notifications and the Mod manager.'),
    "mod_link": ('Mod Link', 'The web link displayed in notifications and the Mod manager.'),
    "mod_logo": (
        'Mod Icon',
        '512x512 .dds icon texture (BC7 SRGB), export to Textures/Logo.dds, displayed in notifications and Mod manager.',
    ),
    # Ini Template
    "use_custom_template": ('Use a custom template', 'Generate a complete mod.ini using the specified jinja2 template.'),
    "custom_template_live_update": ('Template real-time update', 'Control whether the INI template real-time generation thread is running.'),
    "custom_template_source": ('Template storage', 'Select storage location for custom INI template.'),
    "custom_template_path": (
        'Template file',
        'mod.ini Template File Path.\nWhen creating a new template, you can first copy the default content from the built-in editor.',
    ),
    # Ini Toggles
    "use_ini_toggles": ('Use INI switch', 'Write the configured INI switch logic into mod.ini.'),
}

# Enum item label/description, keyed by (class name, property identifier) then
# item identifier. Identifiers / icons / numbers are never touched, so upstream
# reordering or behavioral changes survive the patch.
WWMI_ENUM_TEXTS = {
    ("VTWW_Settings", "color_storage"): {
        "LINEAR": ("Linear", 'Display vertex colors in true linear color, and store in color_attributes with full float precision.'),
        "LEGACY": ('sRGB (Old Version)', 'Display vertex colors with sRGB offset and store in old vertex_colors with 8-bit float precision.'),
    },
    ("VTWW_Settings", "import_skeleton_type"): {
        "MERGED": (
            "Merged",
            'The imported mesh uses a unified vertex group list. Any vertex of any component can have weights painted to any bone. Advantages of Mod: easy to paint weights, supports custom skeleton scaling, supports advanced weights (for example, painting long hair onto a cape). Disadvantages of Mod: model updates have a 1-frame delay; when multiple identical modified objects appear on the screen, the mod will pause. Suggested use: beginners, characters with complex weights, or Echo mods.',
        ),
        "COMPONENT": (
            "Per-Component",
            'The imported mesh splits the vertex group list by components, and each vertex can only be painted with weights to its own component. Advantages of Mod: model updates have no 1-frame delay, slightly better performance. Disadvantages of Mod: difficult to paint weights, very limited weight options, does not support custom skeleton scaling. Suggested use: weapon mods and simple texture modifications.',
        ),
    },
    ("VTWW_Settings", "custom_template_source"): {
        "INTERNAL": ('Built-in editor', 'Use text in Blender text editor as a custom template.'),
        "EXTERNAL": ('External files', 'Use a specified external file as a custom template.'),
    },
    ("ToggleVarStateCondition", "logic"): {
        "&&": ("AND", 'The object is displayed only when both the current condition and the previous condition are TRUE; AND is calculated before OR.'),
        "||": ("OR", 'As long as the current condition is TRUE, the object can be displayed; AND is calculated before OR.'),
    },
    ("ToggleVarStateCondition", "type"): {
        "TOGGLE": ('Toggle variable', 'Bind to an existing INI switch variable.'),
        "EXTERNAL": ('Custom variable', 'Bind to a custom INI variable.'),
    },
    ("ToggleVarStateCondition", "operator"): {
        "==": ("==", 'Variable must be equal to the specified value.'),
        "!=": ("!=", 'Variable must not equal the specified value.'),
        ">": (">", 'Variable must be greater than the specified value.'),
        "<": ("<", 'Variable must be less than the specified value.'),
        ">=": (">=", 'Variable must be greater than or equal to the specified value.'),
        "<=": ("<=", 'Variable must be less than or equal to the specified value.'),
    },
}

# Core panel / operator bl_label + bl_description, keyed by class name.
# The root panel is NOT listed (the driver already sets it to 鸣潮 WWMI).
WWMI_CLASS_TEXTS = {
    "VTWW_PT_SidePanelPartialExport": ('Partial export', None),
    "VTWW_PT_SidePanelAdvancedExport": ('Advanced', None),
    "VTWW_PT_SidePanelModInfo": ('Mod Information', None),
    "VTWW_PT_SidePanelIniTemplate": ('INI Template', None),
    "VTWW_PT_SidePanelExportFooter": ('Export', None),
    "VTWW_PT_TEXT_EDITOR_IniTemplate": ('INI Template - Velo Tools Mingchao', None),
    "VTWW_Import": ('Import Model', 'Import WWMI object from the extraction directory.'),
    "VTWW_Export": ('Export Mod', 'Export the current component set as WWMI Mod.'),
    "VTWW_ExtractFrameData": ('Extract the model from Dump.', 'Extract available WWMI objects from the current Frame Dump.'),
    "VTWW_OpenIniTemplateEditor": ('Edit template', 'Open the current INI template for viewing or editing.'),
    "VTWW_IniTemplateEditor_ToggleLiveUpdates": (
        'Start INI update',
        'Toggle real-time updates for the INI template; when enabled, editing the template will export and write to mod.ini based on current settings each time.',
    ),
    "VTWW_IniTemplateEditor_Reset": ('Reset template', 'Warning: This operation will reset the custom template to default content!'),
}

INI_TOGGLE_CLASS_TEXTS = {
    "VTWW_PT_SidePanelIniToggles": ('INI Switch', None),
    "VTWW_PT_TEXT_EDITOR_IniToggles": ('INI Switch - Velo Tools Mingchao', None),
    "VTWW_OT_CollapseToggleVars": ('Foldable Variables', 'Collapse all switch variables in the list and exit edit mode.'),
    "VTWW_OT_ExpandToggleVars": ('Expand variables', 'Expand all switch variables in the list and enter edit mode.'),
    "VTWW_OT_AddToggleVar": ('Add switch variable', 'Add a INI switch variable to control object visibility.'),
    "VTWW_OT_RemoveToggleVar": ('Delete switch variable', 'Delete this variable from the INI switch list.'),
    "VTWW_OT_MoveToggleVar": ('Move switch variable', 'Adjust the order of this switch variable in the list.'),
    "VTWW_OT_EditToggleVar": ('Edit variable', 'Configure variable hotkeys and default states.'),
    "VTWW_OT_AddToggleVarState": ('Add state', 'Add a new state for this variable; each state can control multiple objects.'),
    "VTWW_OT_RemoveToggleVarState": ('Delete state', 'Delete the current state from this INI switch variable.'),
    "VTWW_OT_MoveToggleVarState": ('Move state', 'Adjust the order of the current state in the variable state list.'),
    "VTWW_OT_AddToggleVarStateObject": ('Add state object', 'Add the object to the current state so that it displays according to the conditions of that state.'),
    "VTWW_OT_RemoveToggleVarStateObject": ('Delete state object', 'Remove this object from the current state.'),
    "VTWW_OT_EditVarStateObject": ('Edit conditions', 'Open the custom display conditions window for the current object.'),
    "VTWW_OT_AddToggleVarStateObjectCondition": ('Add condition', 'Add a new custom display condition.'),
    "VTWW_OT_RemoveToggleVarStateObjectCondition": ('Delete condition', 'Delete current custom display condition.'),
    "VTWW_OpenIniTogglesImportExportEditor": (
        'Open INI switch import/export',
        'Open the text editor window for importing or exporting INI switch variables.',
    ),
    "VTWW_ExportIniToggles": ('Export INI switch', 'Export the current INI switch variables as JSON text that can be re-imported.'),
    "VTWW_ImportIniToggles": ('Import INI Switch', 'Import INI switch variables from the JSON in the current text file.'),
}

# Ini Toggles PropertyGroup texts, keyed by class name then property identifier.
INI_TOGGLE_PROPERTY_TEXTS = {
    "IniToggles": {
        "replace_vars_on_import": (
            'Replace variables with the same name on import',
            'When importing INI Switch, replace existing variables with the same name with the imported content; skip duplicates when off.',
        ),
        "clear_vars_on_import": ('Clear variables before import', 'Delete all existing variables before importing INI Switch.'),
        "hide_empty_states": ('Hide -1 states with no objects', 'Hide -1 empty states with no objects to reduce UI space usage.'),
        "hide_default_conditions": ('Hide default conditions', 'Hide automatically generated default conditions to reduce UI space usage.'),
    },
    "ToggleVar": {
        "default_state": ('Default state', 'The initial state value used by this switch variable.'),
        "hotkeys": (
            'Hotkeys',
            'Keys used to switch between multiple states; key combinations are separated by spaces, and multiple hotkeys are separated by semicolons.',
        ),
        "ui_expanded": ('Expand variables', 'Enter or exit the edit state of this variable.'),
    },
    "ToggleVarStateCondition": {
        "logic": ('Logic', 'Control how multiple conditions collectively affect object display.'),
        "type": ('Type', 'Select to bind existing INI switch variable, or bind custom INI variable.'),
        "var": (
            'Variable',
            'Variable to compare with the status value. Prefix custom variable names with $ to prevent automatic formatting as $swapvar_name.',
        ),
        "operator": ('Compare', 'Control how variables and values are compared to determine if the condition is met.'),
        "state": ('Status Value', 'Status value used for comparison with variables.'),
    },
    "ToggleVarStateObject": {
        "object": ('Objects', 'Under default conditions, selected objects will only be displayed when the variable equals the current state value.'),
    },
}
