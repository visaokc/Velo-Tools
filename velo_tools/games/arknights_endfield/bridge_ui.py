"""Velo bridge UI layered on top of the embedded EFMI core panels."""

from __future__ import annotations

from velo_tools.i18n import iface_

import bpy

from . import import_textures


_ORIGINAL_MENU_METHODS = {}
_ORIGINAL_IMPORT_EXECUTE = None


def _zh(text: str) -> str:
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return text


def velo_controls_for_mode(mode: str) -> tuple[str, ...]:
    """Return Velo-only controls that should be drawn inline for a tool mode."""
    if mode == "EXTRACT_FRAME_DATA":
        return ("generate_crossib_json", "extract_components_filter")
    if mode == "IMPORT_OBJECT":
        return ("import_as_component_collections",)
    if mode == "EXPORT_MOD":
        return ("auto_split_by_material", "slot_style_textures")
    return ()


def _draw_velo_inline_controls(layout, cfg, mode: str, context=None) -> None:
    controls = velo_controls_for_mode(mode)
    if not controls:
        return
    box = layout.box()
    box.label(text=iface_('Velo Compatibility Options'), icon="TOOL_SETTINGS")
    if "generate_crossib_json" in controls and hasattr(cfg, "generate_crossib_json"):
        box.prop(cfg, "generate_crossib_json")
    if "extract_components_filter" in controls and hasattr(cfg, "extract_components_filter"):
        box.prop(cfg, "extract_components_filter")
    if "import_as_component_collections" in controls and hasattr(cfg, "import_as_component_collections"):
        box.prop(cfg, "import_as_component_collections")
    if "auto_split_by_material" in controls and hasattr(cfg, "velo_auto_split_by_material"):
        box.prop(cfg, "velo_auto_split_by_material")
    if "slot_style_textures" in controls and hasattr(cfg, "slot_style_textures"):
        box.prop(cfg, "slot_style_textures")
        if cfg.slot_style_textures and context is not None:
            from . import slot_component_ui
            slot_component_ui.draw_component_selector(box, context)
    layout.row()


def draw_export_mode_selector(layout, cfg) -> bool:
    if not hasattr(cfg, "mod_skeleton_type"):
        return False
    layout.row().prop(cfg, "mod_skeleton_type", text=iface_('Export skeleton mode'))
    return True


class VTEF_PT_VeloBridgeOptions(bpy.types.Panel):
    bl_idname = "VTEF_PT_VELO_BRIDGE_OPTIONS"
    bl_label = _zh('Velo Compatibility Options')
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Velo Tools"
    bl_parent_id = "VTEF_PT_SIDEBAR"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return False

    def draw(self, context):
        return


def _add_row_with_error_handler():
    from ._efmi_core.addon.ui import add_row_with_error_handler

    return add_row_with_error_handler


def _patch_sidebar_menus() -> None:
    from ._efmi_core.addon import ui as _vui

    cls = _vui.VTEF_PT_SIDEBAR
    if _ORIGINAL_MENU_METHODS:
        return

    for name in (
        "draw_menu_export_mod",
        "draw_menu_import_object",
        "draw_menu_extract_frame_data",
        "draw_menu_EXTRACT_LOD_DATA",
    ):
        _ORIGINAL_MENU_METHODS[name] = getattr(cls, name)

    def draw_menu_export_mod(self, context):
        cfg = context.scene.VTEF_settings
        layout = self.layout
        add_row = _add_row_with_error_handler()

        layout.row()

        row = add_row(layout, cfg, "component_collection")
        row.prop(cfg, "component_collection", text=iface_('Component Set'))

        row = add_row(layout, cfg, "object_source_folder")
        row.prop(cfg, "object_source_folder", text=iface_('Object source directory'))

        row = add_row(layout, cfg, "mod_output_folder")
        row.prop(cfg, "mod_output_folder", text=iface_('Mod Output Directory'))

        draw_export_mode_selector(layout, cfg)
        _draw_velo_inline_controls(layout, cfg, "EXPORT_MOD", context)

        if not cfg.partial_export:
            layout.row()

            layout.row().prop(cfg, "mirror_mesh", text=iface_('Mirror Mesh'))
            layout.row().prop(cfg, "apply_all_modifiers", text=iface_('Apply all modifiers'))
            layout.row().prop(cfg, "copy_textures", text=iface_('Copy the sticker'))

            col = layout.column(align=True)
            grid = col.grid_flow(columns=2, align=True)
            grid.alignment = "LEFT"
            grid.prop(cfg, "write_ini", text=iface_('Write out mod.ini'))
            if cfg.write_ini:
                grid.prop(cfg, "comment_ini", text=iface_('Write comment'))

            layout.row()
            layout.row()

            if bpy.app.version >= (3, 5):
                row = layout.row()
                row.prop(cfg, "ignore_nested_collections", text=iface_('Ignore Nested Sets'))
                if not cfg.ignore_nested_collections:
                    row.prop(cfg, "ignore_hidden_collections", text=iface_('Ignore hidden collection'))

            layout.row().prop(cfg, "ignore_hidden_objects", text=iface_('Ignore hidden object'))
            layout.row().prop(cfg, "ignore_muted_shape_keys", text=iface_('Ignore disabling ShapeKey'))

    def draw_menu_import_object(self, context):
        cfg = context.scene.VTEF_settings
        layout = self.layout
        add_row = _add_row_with_error_handler()

        layout.row()

        row = add_row(layout, cfg, "object_source_folder")
        row.prop(cfg, "object_source_folder", text=iface_('Object source directory'))

        layout.row().prop(cfg, "color_storage", text=iface_('Vertex Color'))
        layout.row().prop(cfg, "import_skeleton_type", text=iface_('Import skeleton mode'))
        _draw_velo_inline_controls(layout, cfg, "IMPORT_OBJECT", context)
        if cfg.import_skeleton_type == "MERGED":
            layout.row().prop(cfg, "skip_empty_vertex_groups", text=iface_('Skip empty vertex group'))
        layout.row().prop(cfg, "mirror_mesh", text=iface_('Mirror Mesh'))
        if hasattr(cfg, "import_texture"):
            layout.row().prop(cfg, "import_texture", text=iface_('Import Texture'))

        layout.row()

        layout.row().operator(_vui.VTEF_Import.bl_idname)

    def draw_menu_extract_frame_data(self, context):
        cfg = context.scene.VTEF_settings
        layout = self.layout
        add_row = _add_row_with_error_handler()

        layout.row()

        row = add_row(layout, cfg, "frame_dump_folder")
        row.prop(cfg, "frame_dump_folder", text=iface_('Frame Dump Directory'))

        layout.row().prop(cfg, "extract_output_folder", text=iface_('Output directory'))

        layout.row()
        _draw_velo_inline_controls(layout, cfg, "EXTRACT_FRAME_DATA", context)

        layout.row().prop(cfg, "import_extracted_objects", text=iface_('Import Blender after extraction'))
        layout.row().prop(cfg, "tolerate_extraction_errors", text=iface_('Tolerate extraction errors'))
        layout.row().prop(cfg, "verbose_logging", text=iface_('Detailed log'))

        layout.row()

        layout.row().prop(cfg, "skip_static_objects", text=iface_('Object Filtering: Skip Static Objects'))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_min_component_count_enabled", text=iface_('Object filtering: Minimum component count'))
        sub = row.row()
        sub.enabled = cfg.skip_object_min_component_count_enabled
        sub.prop(cfg, "skip_object_min_component_count", text=iface_('Minimum number of components'))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_min_texture_count_enabled", text=iface_('Object filtering: Minimum texture count'))
        sub = row.row()
        sub.enabled = cfg.skip_object_min_texture_count_enabled
        sub.prop(cfg, "skip_object_min_texture_count", text=iface_('Minimum number of textures'))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_resource_hashes_enabled", text=iface_('Object filtering: Resource Hash'))
        sub = row.row()
        sub.enabled = cfg.skip_object_resource_hashes_enabled
        sub.prop(cfg, "skip_object_resource_hashes")

        layout.row()

        row = layout.row(align=True)
        row.prop(cfg, "skip_draw_resource_hashes_enabled", text=iface_('Component Filter: Blacklist Hash'))
        sub = row.row()
        sub.enabled = cfg.skip_draw_resource_hashes_enabled
        sub.prop(cfg, "skip_draw_resource_hashes")

        layout.row()

        row = layout.row(align=True)
        row.prop(cfg, "skip_small_textures", text=iface_('Texture filtering: skip small textures'))
        sub = row.row()
        sub.enabled = cfg.skip_small_textures
        sub.prop(cfg, "skip_small_textures_size", text=iface_('Minimum size KB'))

        layout.row().prop(cfg, "skip_jpg_textures", text=iface_('Texture filtering: skip .jpg'))
        layout.row().prop(
            cfg,
            "skip_slot_residual_textures",
            text=iface_('Texture filtering: skip Dirty Slot'),
        )

        layout.row()

        layout.row().operator(_vui.VTEF_ExtractFrameData.bl_idname)

    def draw_menu_EXTRACT_LOD_DATA(self, context):
        cfg = context.scene.VTEF_settings
        layout = self.layout
        add_row = _add_row_with_error_handler()

        layout.row()

        row = add_row(layout, cfg, "lod_frame_dump_folder")
        row.prop(cfg, "lod_frame_dump_folder", text=iface_('LOD Frame Dump Table of Contents'))

        row = add_row(layout, cfg, "object_source_folder")
        row.prop(cfg, "object_source_folder", text=iface_('Object source directory'))

        layout.row()
        _draw_velo_inline_controls(layout, cfg, "EXTRACT_LOD_DATA", context)

        layout.row().prop(cfg, "tolerate_extraction_errors", text=iface_('Tolerate extraction errors'))
        layout.row().prop(cfg, "verbose_logging", text=iface_('Detailed log'))

        layout.row()

        layout.row().prop(cfg, "skip_static_objects", text=iface_('Object Filtering: Skip Static Objects'))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_min_component_count_enabled", text=iface_('Object filtering: Minimum component count'))
        sub = row.row()
        sub.enabled = cfg.skip_object_min_component_count_enabled
        sub.prop(cfg, "skip_object_min_component_count", text=iface_('Minimum number of components'))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_min_texture_count_enabled", text=iface_('Object filtering: Minimum texture count'))
        sub = row.row()
        sub.enabled = cfg.skip_object_min_texture_count_enabled
        sub.prop(cfg, "skip_object_min_texture_count", text=iface_('Minimum number of textures'))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_resource_hashes_enabled", text=iface_('Object filtering: Resource Hash'))
        sub = row.row()
        sub.enabled = cfg.skip_object_resource_hashes_enabled
        sub.prop(cfg, "skip_object_resource_hashes")

        layout.row()

        row = layout.row(align=True)
        row.prop(cfg, "skip_component_below_vertex_count_enabled", text=iface_('Component Filter: Minimum Number of Vertices'))
        sub = row.row()
        sub.enabled = cfg.skip_component_below_vertex_count_enabled
        sub.prop(cfg, "skip_component_below_vertex_count", text=iface_('Minimum number of vertices'))

        row = layout.row(align=True)
        row.prop(cfg, "skip_component_hashes_enabled", text=iface_('Component Filter: Blacklist Hash'))
        sub = row.row()
        sub.enabled = cfg.skip_component_hashes_enabled
        sub.prop(cfg, "skip_component_hashes")

        layout.row()

        row = add_row(layout, cfg, "geo_matcher_error_threshold")

        if cfg.geo_matcher_method == "VOXEL":
            row.prop(cfg, "geo_matcher_voxel_error_threshold", text=iface_('Geometric Matching Error Threshold'))
        elif cfg.geo_matcher_method == "POINT_CLOUD":
            row.prop(cfg, "geo_matcher_error_threshold", text=iface_('Geometric Matching Error Threshold'))

        layout.row()

    cls.draw_menu_export_mod = draw_menu_export_mod
    cls.draw_menu_import_object = draw_menu_import_object
    cls.draw_menu_extract_frame_data = draw_menu_extract_frame_data
    cls.draw_menu_EXTRACT_LOD_DATA = draw_menu_EXTRACT_LOD_DATA


def _restore_sidebar_menus() -> None:
    if not _ORIGINAL_MENU_METHODS:
        return
    from ._efmi_core.addon import ui as _vui

    cls = _vui.VTEF_PT_SIDEBAR
    for name, method in _ORIGINAL_MENU_METHODS.items():
        setattr(cls, name, method)
    _ORIGINAL_MENU_METHODS.clear()


def _patch_import_texture_operator() -> None:
    global _ORIGINAL_IMPORT_EXECUTE
    if _ORIGINAL_IMPORT_EXECUTE is not None:
        return
    from ._efmi_core.addon import ui as _vui

    _ORIGINAL_IMPORT_EXECUTE = _vui.VTEF_Import.execute

    def execute_with_texture_import(self, context):
        cfg = context.scene.VTEF_settings
        existing_names = {obj.name for obj in bpy.data.objects}
        result = _ORIGINAL_IMPORT_EXECUTE(self, context)
        if not getattr(cfg, "import_texture", False):
            return result
        new_objects = [
            obj for obj in bpy.data.objects
            if obj.name not in existing_names and getattr(obj, "type", None) == "MESH"
        ]
        try:
            summary = import_textures.assign_textures(cfg.object_source_folder, objects=new_objects)
        except Exception as exc:
            self.report({"WARNING"}, iface_('Failed to import texture: {0}').format(exc))
            return result
        if summary.assigned:
            self.report({"INFO"}, iface_('Assigned textures for {0} imported meshes').format(summary.assigned))
        return result

    _vui.VTEF_Import.execute = execute_with_texture_import


def _restore_import_texture_operator() -> None:
    global _ORIGINAL_IMPORT_EXECUTE
    if _ORIGINAL_IMPORT_EXECUTE is None:
        return
    from ._efmi_core.addon import ui as _vui

    _vui.VTEF_Import.execute = _ORIGINAL_IMPORT_EXECUTE
    _ORIGINAL_IMPORT_EXECUTE = None


def install_patches() -> None:
    _patch_sidebar_menus()
    _patch_import_texture_operator()


def uninstall_patches() -> None:
    _restore_import_texture_operator()
    _restore_sidebar_menus()


classes = (
    VTEF_PT_VeloBridgeOptions,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    install_patches()


def unregister():
    uninstall_patches()
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
