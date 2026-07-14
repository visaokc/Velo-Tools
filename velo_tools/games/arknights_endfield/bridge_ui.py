"""Velo bridge UI layered on top of the embedded EFMI core panels."""

from __future__ import annotations

import bpy

from . import import_textures
from . import vgmap
from ._efmi_core.migoto_io.blender_interface.utility import resolve_path
from ._efmi_core.migoto_io.object_extractor.migoto_object.metadata_format import read_metadata


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
        return ("generate_vertex_group_map", "generate_crossib_json", "extract_components_filter")
    if mode == "IMPORT_OBJECT":
        return ("import_as_component_collections",)
    if mode == "EXPORT_MOD":
        return ("auto_split_by_material", "convert_legacy_vertex_group_map")
    return ()


def _draw_velo_inline_controls(layout, cfg, mode: str) -> None:
    controls = velo_controls_for_mode(mode)
    if not controls:
        return
    box = layout.box()
    box.label(text=_zh("Velo 兼容选项"), icon="TOOL_SETTINGS")
    if "generate_vertex_group_map" in controls and hasattr(cfg, "generate_vertex_group_map"):
        box.prop(cfg, "generate_vertex_group_map")
    if "generate_crossib_json" in controls and hasattr(cfg, "generate_crossib_json"):
        box.prop(cfg, "generate_crossib_json")
    if "extract_components_filter" in controls and hasattr(cfg, "extract_components_filter"):
        box.prop(cfg, "extract_components_filter")
    if "import_as_component_collections" in controls and hasattr(cfg, "import_as_component_collections"):
        box.prop(cfg, "import_as_component_collections")
    if "auto_split_by_material" in controls and hasattr(cfg, "velo_auto_split_by_material"):
        box.prop(cfg, "velo_auto_split_by_material")
    if "convert_legacy_vertex_group_map" in controls:
        box.operator(VTEF_OT_convert_legacy_vertex_group_map.bl_idname, icon="FILE_REFRESH")
    layout.row()


def draw_export_mode_selector(layout, cfg) -> bool:
    if not hasattr(cfg, "mod_skeleton_type"):
        return False
    layout.row().prop(cfg, "mod_skeleton_type", text=_zh("导出骨架模式"))
    return True


class VTEF_OT_convert_legacy_vertex_group_map(bpy.types.Operator):
    bl_idname = "vtef.convert_legacy_vertex_group_map"
    bl_label = _zh("从旧 Metadata 转换 VertexGroupMap")
    bl_description = _zh("从旧版 Velo 写在 Metadata.json 内的 component.vg_map 显式生成 VertexGroupMap.json")

    def execute(self, context):
        cfg = context.scene.VTEF_settings
        folder = resolve_path(cfg.object_source_folder)
        try:
            metadata = read_metadata(folder / "Metadata.json")
            out_path = vgmap.convert_legacy_metadata(folder, metadata)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, _zh(f"已生成 {out_path}"))
        return {"FINISHED"}


class VTEF_PT_VeloBridgeOptions(bpy.types.Panel):
    bl_idname = "VTEF_PT_VELO_BRIDGE_OPTIONS"
    bl_label = _zh("Velo 兼容选项")
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
        row.prop(cfg, "component_collection", text=_zh("组件集合"))

        row = add_row(layout, cfg, "object_source_folder")
        row.prop(cfg, "object_source_folder", text=_zh("对象源目录"))

        row = add_row(layout, cfg, "mod_output_folder")
        row.prop(cfg, "mod_output_folder", text=_zh("Mod 输出目录"))

        draw_export_mode_selector(layout, cfg)
        _draw_velo_inline_controls(layout, cfg, "EXPORT_MOD")

        if not cfg.partial_export:
            layout.row()

            layout.row().prop(cfg, "mirror_mesh", text=_zh("镜像网格"))
            layout.row().prop(cfg, "apply_all_modifiers", text=_zh("应用所有修改器"))
            layout.row().prop(cfg, "copy_textures", text=_zh("复制贴图"))

            col = layout.column(align=True)
            grid = col.grid_flow(columns=2, align=True)
            grid.alignment = "LEFT"
            grid.prop(cfg, "write_ini", text=_zh("写出 mod.ini"))
            if cfg.write_ini:
                grid.prop(cfg, "comment_ini", text=_zh("写入注释"))

            layout.row()
            layout.row()

            if bpy.app.version >= (3, 5):
                row = layout.row()
                row.prop(cfg, "ignore_nested_collections", text=_zh("忽略嵌套集合"))
                if not cfg.ignore_nested_collections:
                    row.prop(cfg, "ignore_hidden_collections", text=_zh("忽略隐藏集合"))

            layout.row().prop(cfg, "ignore_hidden_objects", text=_zh("忽略隐藏对象"))
            layout.row().prop(cfg, "ignore_muted_shape_keys", text=_zh("忽略禁用形态键"))

    def draw_menu_import_object(self, context):
        cfg = context.scene.VTEF_settings
        layout = self.layout
        add_row = _add_row_with_error_handler()

        layout.row()

        row = add_row(layout, cfg, "object_source_folder")
        row.prop(cfg, "object_source_folder", text=_zh("对象源目录"))

        layout.row().prop(cfg, "color_storage", text=_zh("顶点色"))
        layout.row().prop(cfg, "import_skeleton_type", text=_zh("导入骨架模式"))
        _draw_velo_inline_controls(layout, cfg, "IMPORT_OBJECT")
        if cfg.import_skeleton_type == "MERGED":
            layout.row().prop(cfg, "skip_empty_vertex_groups", text=_zh("跳过空顶点组"))
        layout.row().prop(cfg, "mirror_mesh", text=_zh("镜像网格"))
        if hasattr(cfg, "import_texture"):
            layout.row().prop(cfg, "import_texture", text=_zh("导入贴图"))

        layout.row()

        layout.row().operator(_vui.VTEF_Import.bl_idname)

    def draw_menu_extract_frame_data(self, context):
        cfg = context.scene.VTEF_settings
        layout = self.layout
        add_row = _add_row_with_error_handler()

        layout.row()

        row = add_row(layout, cfg, "frame_dump_folder")
        row.prop(cfg, "frame_dump_folder", text=_zh("Frame Dump 目录"))

        layout.row().prop(cfg, "extract_output_folder", text=_zh("输出目录"))

        layout.row()
        _draw_velo_inline_controls(layout, cfg, "EXTRACT_FRAME_DATA")

        layout.row().prop(cfg, "import_extracted_objects", text=_zh("提取后导入 Blender"))
        layout.row().prop(cfg, "tolerate_extraction_errors", text=_zh("容忍提取错误"))
        layout.row().prop(cfg, "verbose_logging", text=_zh("详细日志"))

        layout.row()

        layout.row().prop(cfg, "skip_static_objects", text=_zh("对象过滤：跳过静态对象"))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_min_component_count_enabled", text=_zh("对象过滤：最少组件数"))
        sub = row.row()
        sub.enabled = cfg.skip_object_min_component_count_enabled
        sub.prop(cfg, "skip_object_min_component_count", text=_zh("最少组件数"))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_min_texture_count_enabled", text=_zh("对象过滤：最少贴图数"))
        sub = row.row()
        sub.enabled = cfg.skip_object_min_texture_count_enabled
        sub.prop(cfg, "skip_object_min_texture_count", text=_zh("最少贴图数"))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_resource_hashes_enabled", text=_zh("对象过滤：资源 Hash"))
        sub = row.row()
        sub.enabled = cfg.skip_object_resource_hashes_enabled
        sub.prop(cfg, "skip_object_resource_hashes")

        layout.row()

        row = layout.row(align=True)
        row.prop(cfg, "skip_draw_resource_hashes_enabled", text=_zh("组件过滤：黑名单 Hash"))
        sub = row.row()
        sub.enabled = cfg.skip_draw_resource_hashes_enabled
        sub.prop(cfg, "skip_draw_resource_hashes")

        layout.row()

        row = layout.row(align=True)
        row.prop(cfg, "skip_small_textures", text=_zh("贴图过滤：跳过小贴图"))
        sub = row.row()
        sub.enabled = cfg.skip_small_textures
        sub.prop(cfg, "skip_small_textures_size", text=_zh("最小大小 KB"))

        layout.row().prop(cfg, "skip_jpg_textures", text=_zh("贴图过滤：跳过 .jpg"))

        layout.row()

        layout.row().operator(_vui.VTEF_ExtractFrameData.bl_idname)

    def draw_menu_EXTRACT_LOD_DATA(self, context):
        cfg = context.scene.VTEF_settings
        layout = self.layout
        add_row = _add_row_with_error_handler()

        layout.row()

        row = add_row(layout, cfg, "lod_frame_dump_folder")
        row.prop(cfg, "lod_frame_dump_folder", text=_zh("LOD Frame Dump 目录"))

        row = add_row(layout, cfg, "object_source_folder")
        row.prop(cfg, "object_source_folder", text=_zh("对象源目录"))

        layout.row()
        _draw_velo_inline_controls(layout, cfg, "EXTRACT_LOD_DATA")

        layout.row().prop(cfg, "tolerate_extraction_errors", text=_zh("容忍提取错误"))
        layout.row().prop(cfg, "verbose_logging", text=_zh("详细日志"))

        layout.row()

        layout.row().prop(cfg, "skip_static_objects", text=_zh("对象过滤：跳过静态对象"))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_min_component_count_enabled", text=_zh("对象过滤：最少组件数"))
        sub = row.row()
        sub.enabled = cfg.skip_object_min_component_count_enabled
        sub.prop(cfg, "skip_object_min_component_count", text=_zh("最少组件数"))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_min_texture_count_enabled", text=_zh("对象过滤：最少贴图数"))
        sub = row.row()
        sub.enabled = cfg.skip_object_min_texture_count_enabled
        sub.prop(cfg, "skip_object_min_texture_count", text=_zh("最少贴图数"))

        row = layout.row(align=True)
        row.prop(cfg, "skip_object_resource_hashes_enabled", text=_zh("对象过滤：资源 Hash"))
        sub = row.row()
        sub.enabled = cfg.skip_object_resource_hashes_enabled
        sub.prop(cfg, "skip_object_resource_hashes")

        layout.row()

        row = layout.row(align=True)
        row.prop(cfg, "skip_component_below_vertex_count_enabled", text=_zh("组件过滤：最少顶点数"))
        sub = row.row()
        sub.enabled = cfg.skip_component_below_vertex_count_enabled
        sub.prop(cfg, "skip_component_below_vertex_count", text=_zh("最少顶点数"))

        row = layout.row(align=True)
        row.prop(cfg, "skip_component_hashes_enabled", text=_zh("组件过滤：黑名单 Hash"))
        sub = row.row()
        sub.enabled = cfg.skip_component_hashes_enabled
        sub.prop(cfg, "skip_component_hashes")

        layout.row()

        row = add_row(layout, cfg, "geo_matcher_error_threshold")

        if cfg.geo_matcher_method == "VOXEL":
            row.prop(cfg, "geo_matcher_voxel_error_threshold", text=_zh("几何匹配误差阈值"))
        elif cfg.geo_matcher_method == "POINT_CLOUD":
            row.prop(cfg, "geo_matcher_error_threshold", text=_zh("几何匹配误差阈值"))

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
            self.report({"WARNING"}, _zh(f"导入贴图失败：{exc}"))
            return result
        if summary.assigned:
            self.report({"INFO"}, _zh(f"已为 {summary.assigned} 个导入网格指定贴图"))
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
    VTEF_OT_convert_legacy_vertex_group_map,
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
