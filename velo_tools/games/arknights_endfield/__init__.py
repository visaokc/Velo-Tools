"""Arknights: Endfield integration driver for Velo Tools."""

import bpy

from . import props as _props
from . import embedded as _embedded
from . import mmd_pick as _mmd_pick
from . import bridge_ui as _bridge_ui
from . import ui_l10n as _ui_l10n
from . import lod_debug_import as _lod_debug_import
from . import unified_vg_export as _unified_vg_export
from ._efmi_core import auto_load as _al
from ._efmi_core.addon import settings as _vsettings
from .. import registry as _registry
from .. import _a2_panels as _a2


_TAB_VALUE = "GAME"

# Arknights: Endfield (EFMI) game descriptor: registered into the multi-game registry,
# queried by active_game for shared tools such as the export adapter / export hook /
# split-by-material.
_DESCRIPTOR = _registry.GameDescriptor(
    key="ENDFIELD",
    game_value="ENDFIELD",
    display_name="终末地",
    settings_attr="VTEF_settings",
    adapter_key="EFMI",
    export_op="vtef.export_mod",
    export_op_class="VTEF_Export",
)
_REMOVED_VENDOR_PANEL_IDS = {
    "VTEF_PT_UpdaterPanel",
    "VTEF_PT_DebugPanel",
}


def _zh(text: str) -> str:
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return text


def _is_removed_vendor_panel(cls) -> bool:
    return getattr(cls, "bl_idname", "") in _REMOVED_VENDOR_PANEL_IDS


def _iter_velo_sidebar_panels(root_cls):
    seen = {id(root_cls)}
    yield root_cls

    for cls in list(_al.ordered_classes or ()):
        if not isinstance(cls, type) or id(cls) in seen or _is_removed_vendor_panel(cls):
            continue
        seen.add(id(cls))
        yield cls

    extra_modules = []
    try:
        from ._efmi_core.addon.modules.ini_toggles import ui as _toggles_ui
        extra_modules.append(_toggles_ui)
    except Exception:
        pass
    try:
        from .embedded.crossib import ui as _crossib_ui
        extra_modules.append(_crossib_ui)
    except Exception:
        pass

    for module in extra_modules:
        for value in vars(module).values():
            if not isinstance(value, type) or id(value) in seen or _is_removed_vendor_panel(value):
                continue
            seen.add(id(value))
            yield value


def _strip_unwanted_vendor_panels():
    if not _al.ordered_classes:
        return
    _al.ordered_classes = [
        cls
        for cls in _al.ordered_classes
        if not _is_removed_vendor_panel(cls)
    ]


def _patch_single_panel(cls, root_cls):
    if not issubclass(cls, bpy.types.Panel):
        return
    if getattr(cls, "bl_space_type", None) != "VIEW_3D":
        return
    if getattr(cls, "bl_region_type", None) != "UI":
        return
    if getattr(cls, "bl_category", None) == "Text":
        return

    cls.bl_category = "Velo Tools"

    if cls is root_cls:
        # Structural patch (must run before registration); A2 gating of poll/draw is handled by _a2.gate() at the end of register().
        cls.bl_label = _zh("终末地 EFMI")
        cls.bl_parent_id = "VELO_PT_main"
        cls.bl_options = set(getattr(cls, "bl_options", set())) | {"DEFAULT_CLOSED"}

        def _no_header(self, context):
            return

        cls.draw_header = _no_header


def _patch_panels_for_velo_tools():
    from ._efmi_core.addon import ui as _vui

    root = _vui.VTEF_PT_SIDEBAR
    for cls in _iter_velo_sidebar_panels(root):
        _patch_single_panel(cls, root)
    _patch_velo_ui_labels()


def _patch_velo_ui_labels():
    from ._efmi_core.addon import ui as _vui

    for name, (label, description) in _ui_l10n.VTEF_CLASS_TEXTS.items():
        cls = getattr(_vui, name, None)
        if cls is not None:
            cls.bl_label = _zh(label)
            if description:
                cls.bl_description = _zh(description)

    try:
        from ._efmi_core.addon.modules.ini_toggles import ui as _toggles_ui
    except Exception:
        return
    for name, (label, description) in _ui_l10n.INI_TOGGLE_CLASS_TEXTS.items():
        cls = getattr(_toggles_ui, name, None)
        if cls is not None:
            cls.bl_label = _zh(label)
            if description:
                cls.bl_description = _zh(description)


def _patch_preferences():
    from bpy.props import BoolProperty

    _vsettings.Preferences.__annotations__["auto_check_update"] = BoolProperty(
        name=_zh("自动检查更新"),
        description=_zh("Velo 内置 EFMI core 禁用在线更新检查。"),
        default=False,
    )
    _vsettings.Preferences.draw = lambda self, context: self.layout.label(text=_zh("Velo 内置 EFMI core 不使用在线更新器。"))


def _translated_items(items):
    return [(identifier, _zh(name), _zh(description)) for identifier, name, description in items]


def _patch_vtef_property(prop_type, name: str, default=None, items=None, **kwargs) -> None:
    if default is not None:
        kwargs["default"] = default
    if items is not None:
        kwargs["items"] = _translated_items(items)
    text = _ui_l10n.VTEF_PROPERTY_TEXTS[name]
    _vsettings.VTEF_Settings.__annotations__[name] = prop_type(
        name=_zh(text[0]),
        description=_zh(text[1]),
        **kwargs,
    )


def _patch_ini_toggle_property(cls, prop_type, prop_name: str, default=None, items=None, **kwargs) -> None:
    if default is not None:
        kwargs["default"] = default
    if items is not None:
        kwargs["items"] = _translated_items(items)
    text = _ui_l10n.INI_TOGGLE_PROPERTY_TEXTS[cls.__name__][prop_name]
    cls.__annotations__[prop_name] = prop_type(
        name=_zh(text[0]),
        description=_zh(text[1]),
        **kwargs,
    )


def _patch_crossib_property(cls, prop_type, prop_name: str, default=None, items=None, **kwargs) -> None:
    if default is not None:
        kwargs["default"] = default
    if items is not None:
        kwargs["items"] = _translated_items(items)
    text = _ui_l10n.CROSSIB_PROPERTY_TEXTS[cls.__name__][prop_name]
    cls.__annotations__[prop_name] = prop_type(
        name=_zh(text[0]),
        description=_zh(text[1]),
        **kwargs,
    )


def _patch_shapekey_property(cls, prop_type, prop_name: str, default=None, **kwargs) -> None:
    if default is not None:
        kwargs["default"] = default
    text = _ui_l10n.SHAPEKEY_PROPERTY_TEXTS[cls.__name__][prop_name]
    cls.__annotations__[prop_name] = prop_type(
        name=_zh(text[0]),
        description=_zh(text[1]),
        **kwargs,
    )


def _patch_vtef_visible_property_texts():
    from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty

    _vsettings.VTEF_Settings.__annotations__["tool_mode"] = EnumProperty(
        name=_zh(_ui_l10n.VTEF_PROPERTY_TEXTS["tool_mode"][0]),
        description=_zh(_ui_l10n.VTEF_PROPERTY_TEXTS["tool_mode"][1]),
        items=_translated_items(_ui_l10n.VTEF_TOOL_MODE_ITEMS),
        default="EXTRACT_FRAME_DATA",
    )
    _patch_vtef_property(
        StringProperty,
        "frame_dump_folder",
        default="",
        subtype="DIR_PATH",
        update=lambda self, context: self.on_update_clear_error("frame_dump_folder"),
    )
    _patch_vtef_property(StringProperty, "extract_output_folder", default="", subtype="DIR_PATH")
    _patch_vtef_property(BoolProperty, "import_extracted_objects", default=False)
    _patch_vtef_property(BoolProperty, "tolerate_extraction_errors", default=True)
    _patch_vtef_property(BoolProperty, "verbose_logging", default=False)
    _patch_vtef_property(BoolProperty, "skip_static_objects", default=True)
    _patch_vtef_property(BoolProperty, "skip_object_min_component_count_enabled", default=False)
    _patch_vtef_property(IntProperty, "skip_object_min_component_count", default=0, min=0, max=20)
    _patch_vtef_property(BoolProperty, "skip_object_min_texture_count_enabled", default=False)
    _patch_vtef_property(IntProperty, "skip_object_min_texture_count", default=0, min=0, max=32)
    _patch_vtef_property(BoolProperty, "skip_object_resource_hashes_enabled", default=False)
    _patch_vtef_property(StringProperty, "skip_object_resource_hashes", default="")
    _patch_vtef_property(BoolProperty, "skip_draw_resource_hashes_enabled", default=False)
    _patch_vtef_property(StringProperty, "skip_draw_resource_hashes", default="")
    _patch_vtef_property(BoolProperty, "skip_small_textures", default=True)
    _patch_vtef_property(IntProperty, "skip_small_textures_size", default=256, min=0, max=16384)
    _patch_vtef_property(BoolProperty, "skip_jpg_textures", default=True)

    _patch_vtef_property(
        StringProperty,
        "object_source_folder",
        default="",
        subtype="DIR_PATH",
        update=lambda self, context: self.on_update_clear_error("object_source_folder"),
    )
    _patch_vtef_property(EnumProperty, "color_storage", items=_ui_l10n.VTEF_ENUM_ITEMS["color_storage"], default="LINEAR")
    _patch_vtef_property(
        EnumProperty,
        "import_skeleton_type",
        items=_ui_l10n.IMPORT_SKELETON_ITEMS,
        default="MERGED",
    )
    _patch_vtef_property(BoolProperty, "skip_empty_vertex_groups", default=True)
    _patch_vtef_property(BoolProperty, "dedupe_bones", default=True)
    _patch_vtef_property(BoolProperty, "mirror_mesh", default=False)

    _patch_vtef_property(BoolProperty, "allow_lod_overwrite", default=False)
    _patch_vtef_property(
        EnumProperty,
        "geo_matcher_method",
        items=_ui_l10n.VTEF_ENUM_ITEMS["geo_matcher_method"],
        default="VOXEL",
    )
    _patch_vtef_property(BoolProperty, "import_matched_lod_objects", default=False)
    _patch_vtef_property(StringProperty, "lod_frame_dump_folder", default="", subtype="DIR_PATH")
    _patch_vtef_property(BoolProperty, "skip_lods_below_error_threshold", default=False)
    _patch_vtef_property(FloatProperty, "geo_matcher_sensivity", default=0.5, min=0.25, max=1, precision=2)
    _patch_vtef_property(BoolProperty, "skip_component_below_vertex_count_enabled", default=False)
    _patch_vtef_property(IntProperty, "skip_component_below_vertex_count", default=0, min=0, max=100000)
    _patch_vtef_property(BoolProperty, "skip_component_hashes_enabled", default=False)
    _patch_vtef_property(StringProperty, "skip_component_hashes", default="")
    _patch_vtef_property(FloatProperty, "geo_matcher_voxel_size", default=0.01, min=0.005, max=0.1, precision=2)
    _patch_vtef_property(FloatProperty, "geo_matcher_voxel_error_threshold", default=55, min=25, max=100, precision=0, subtype="PERCENTAGE")
    _patch_vtef_property(IntProperty, "geo_matcher_sample_size", default=1000, min=500, max=5000)
    _patch_vtef_property(FloatProperty, "geo_matcher_error_threshold", default=85, min=50, max=100, precision=0, subtype="PERCENTAGE")
    _patch_vtef_property(FloatProperty, "geo_matcher_prefilter_voxel_size", default=0.05, min=0.01, max=0.2, precision=2)
    _patch_vtef_property(IntProperty, "geo_matcher_prefilter_sample_size", default=250, min=100, max=2500)
    _patch_vtef_property(IntProperty, "geo_matcher_prefilter_candidates_count", default=5, min=1, max=10)
    _patch_vtef_property(IntProperty, "vg_matcher_candidates_count", default=3, min=1, max=10)

    _patch_vtef_property(
        PointerProperty,
        "component_collection",
        type=bpy.types.Collection,
        update=lambda self, context: self.on_update_clear_error("component_collection"),
    )
    _patch_vtef_property(
        StringProperty,
        "mod_output_folder",
        default="",
        subtype="DIR_PATH",
        update=lambda self, context: self.on_update_clear_error("mod_output_folder"),
    )
    _patch_vtef_property(EnumProperty, "mod_skeleton_type", items=_ui_l10n.MOD_SKELETON_ITEMS, default="MERGED")
    _patch_vtef_property(BoolProperty, "apply_all_modifiers", default=False)
    _patch_vtef_property(BoolProperty, "copy_textures", default=True)
    _patch_vtef_property(BoolProperty, "write_ini", default=True)
    _patch_vtef_property(BoolProperty, "comment_ini", default=False)
    _patch_vtef_property(BoolProperty, "ignore_nested_collections", default=True)
    _patch_vtef_property(BoolProperty, "ignore_hidden_collections", default=True)
    _patch_vtef_property(BoolProperty, "ignore_hidden_objects", default=False)
    _patch_vtef_property(BoolProperty, "ignore_muted_shape_keys", default=True)
    _patch_vtef_property(BoolProperty, "allow_export_without_lods", default=False)
    _patch_vtef_property(BoolProperty, "add_missing_vertex_groups", default=True)
    _patch_vtef_property(BoolProperty, "fill_missing_mesh_data", default=True)
    _patch_vtef_property(IntProperty, "max_instance_count", default=8, min=2, max=32)
    _patch_vtef_property(BoolProperty, "use_spatial_identification", default=True)
    _patch_vtef_property(IntProperty, "spatial_identification_threshold", default=3, min=1, max=8)
    _patch_vtef_property(FloatProperty, "skeleton_scale", default=1.0)
    _patch_vtef_property(BoolProperty, "partial_export", default=False)
    _patch_vtef_property(BoolProperty, "export_index", default=True)
    _patch_vtef_property(BoolProperty, "export_positions", default=True)
    _patch_vtef_property(BoolProperty, "export_blends", default=True)
    _patch_vtef_property(BoolProperty, "export_vectors", default=True)
    _patch_vtef_property(BoolProperty, "export_colors", default=True)
    _patch_vtef_property(BoolProperty, "export_texcoords", default=True)
    _patch_vtef_property(BoolProperty, "export_shapekeys", default=True)
    _patch_vtef_property(StringProperty, "mod_name", default="Unnamed Mod")
    _patch_vtef_property(StringProperty, "mod_author", default="Unknown Author")
    _patch_vtef_property(StringProperty, "mod_desc", default="")
    _patch_vtef_property(StringProperty, "mod_link", default="")
    _patch_vtef_property(StringProperty, "mod_logo", default="", subtype="FILE_PATH")
    _patch_vtef_property(
        BoolProperty,
        "use_custom_template",
        default=False,
        update=lambda self, context: self.on_update_clear_error("use_custom_template"),
    )
    _patch_vtef_property(BoolProperty, "custom_template_live_update", default=False)
    _patch_vtef_property(
        EnumProperty,
        "custom_template_source",
        items=_ui_l10n.VTEF_ENUM_ITEMS["custom_template_source"],
        default="INTERNAL",
        update=lambda self, context: self.on_update_clear_error("use_custom_template"),
    )
    _patch_vtef_property(
        StringProperty,
        "custom_template_path",
        default="",
        subtype="FILE_PATH",
        update=lambda self, context: self.on_update_clear_error("custom_template_path"),
    )
    _patch_vtef_property(BoolProperty, "use_ini_toggles", default=False)


def _patch_ini_toggle_texts():
    from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty
    from ._efmi_core.addon.modules.ini_toggles import props as _toggle_props

    _patch_ini_toggle_property(_toggle_props.IniToggles, BoolProperty, "replace_vars_on_import", default=True)
    _patch_ini_toggle_property(_toggle_props.IniToggles, BoolProperty, "clear_vars_on_import", default=False)
    _patch_ini_toggle_property(_toggle_props.IniToggles, BoolProperty, "hide_empty_states", default=False)
    _patch_ini_toggle_property(_toggle_props.IniToggles, BoolProperty, "hide_default_conditions", default=False)
    _patch_ini_toggle_property(_toggle_props.ToggleVar, StringProperty, "default_state", default="")
    _patch_ini_toggle_property(_toggle_props.ToggleVar, StringProperty, "hotkeys", default="")
    _patch_ini_toggle_property(_toggle_props.ToggleVar, BoolProperty, "ui_expanded", default=True)
    _patch_ini_toggle_property(
        _toggle_props.ToggleVarStateCondition,
        EnumProperty,
        "logic",
        items=_ui_l10n.INI_TOGGLE_ENUM_ITEMS[("ToggleVarStateCondition", "logic")],
        default="&&",
    )
    _patch_ini_toggle_property(
        _toggle_props.ToggleVarStateCondition,
        EnumProperty,
        "type",
        items=_ui_l10n.INI_TOGGLE_ENUM_ITEMS[("ToggleVarStateCondition", "type")],
        default="TOGGLE",
    )
    _patch_ini_toggle_property(_toggle_props.ToggleVarStateCondition, StringProperty, "var", default="")
    _patch_ini_toggle_property(
        _toggle_props.ToggleVarStateCondition,
        EnumProperty,
        "operator",
        items=_ui_l10n.INI_TOGGLE_ENUM_ITEMS[("ToggleVarStateCondition", "operator")],
        default="==",
    )
    _patch_ini_toggle_property(_toggle_props.ToggleVarStateCondition, StringProperty, "state", default="")
    _patch_ini_toggle_property(_toggle_props.ToggleVarStateObject, PointerProperty, "object", type=bpy.types.Object)


def _patch_crossib_texts():
    from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
    from .embedded.crossib import props as _crossib_props
    from .embedded.crossib import ui as _crossib_ui

    _patch_crossib_property(
        _crossib_props.CrossIBMapping,
        EnumProperty,
        "source_kind",
        items=_ui_l10n.CROSSIB_ENUM_ITEMS[("CrossIBMapping", "source_kind")],
        default="OBJECT",
    )
    _patch_crossib_property(_crossib_props.CrossIBMapping, PointerProperty, "source_object", type=bpy.types.Object, poll=lambda self, obj: obj.type == "MESH")
    _patch_crossib_property(_crossib_props.CrossIBMapping, PointerProperty, "source_collection", type=bpy.types.Collection)
    _patch_crossib_property(_crossib_props.CrossIBMapping, IntProperty, "target_component", default=0, min=0, max=15)
    _patch_crossib_property(_crossib_props.CrossIBSettings, BoolProperty, "enabled", default=False)
    _patch_crossib_property(_crossib_props.CrossIBSettings, StringProperty, "frame_dump_folder", default="", subtype="DIR_PATH")

    for name, (label, description) in _ui_l10n.CROSSIB_CLASS_TEXTS.items():
        cls = getattr(_crossib_ui, name, None)
        if cls is not None:
            cls.bl_label = _zh(label)
            if description:
                cls.bl_description = _zh(description)


def _patch_shapekey_texts():
    from bpy.props import BoolProperty
    from .embedded.shapekey import props as _shapekey_props
    from .embedded.shapekey import ui as _shapekey_ui

    _patch_shapekey_property(_shapekey_props.ShapeKeySettings, BoolProperty, "enabled", default=False)
    _patch_shapekey_property(_shapekey_props.ShapeKeySettings, BoolProperty, "merge_buffers", default=True)
    _patch_shapekey_property(_shapekey_props.ShapeKeySettings, BoolProperty, "show_detector", default=True)

    for name, (label, description) in _ui_l10n.SHAPEKEY_CLASS_TEXTS.items():
        cls = getattr(_shapekey_ui, name, None)
        if cls is not None:
            cls.bl_label = _zh(label)
            if description:
                cls.bl_description = _zh(description)


def _patch_toolbox_texts():
    from bpy.props import BoolProperty
    from ._efmi_core.addon.modules.toolbox import ui as _toolbox_ui

    for name, (label, description) in _ui_l10n.TOOLBOX_CLASS_TEXTS.items():
        cls = getattr(_toolbox_ui, name, None)
        if cls is not None:
            cls.bl_label = _zh(label)
            if description:
                cls.bl_description = _zh(description)

    modifier_cls = getattr(_toolbox_ui, "VTEF_ApplyModifierForObjectWithShapeKeysOperator", None)
    if modifier_cls is None:
        return

    disable_text = _ui_l10n.TOOLBOX_PROPERTY_TEXTS[modifier_cls.__name__]["disable_armatures"]
    modifier_cls.__annotations__["disable_armatures"] = BoolProperty(
        name=_zh(disable_text[0]),
        description=_zh(disable_text[1]),
        default=True,
    )

    dialog_text = _ui_l10n.TOOLBOX_DIALOG_TEXTS[modifier_cls.__name__]

    def execute(self, context):
        ob = _toolbox_ui.bpy.context.object
        _toolbox_ui.bpy.ops.object.select_all(action="DESELECT")
        context.view_layer.objects.active = ob
        ob.select_set(True)

        selected_modifiers = [item.name for item in self.my_collection if item.checked]
        if not selected_modifiers:
            self.report({"ERROR"}, _zh(dialog_text["no_modifier_selected"]))
            return {"FINISHED"}

        success, error_info = _toolbox_ui.apply_modifiers_for_object_with_shape_keys(
            context,
            selected_modifiers,
            self.disable_armatures,
        )
        if not success:
            self.report({"ERROR"}, error_info)
        return {"FINISHED"}

    def draw(self, context):
        if context.object.data.shape_keys and context.object.data.shape_keys.animation_data:
            self.layout.separator()
            for line in dialog_text["animation_warning"]:
                self.layout.label(text=_zh(line))
            self.layout.separator()

        box = self.layout.box()
        for prop in self.my_collection:
            box.prop(prop, "checked", text=prop["name"])
        self.layout.prop(self, "disable_armatures")

    modifier_cls.execute = execute
    modifier_cls.draw = draw


def _patch_velo_settings():
    from bpy.props import BoolProperty, StringProperty

    _patch_preferences()
    _patch_vtef_visible_property_texts()
    _patch_ini_toggle_texts()
    _patch_crossib_texts()
    _patch_shapekey_texts()
    _patch_toolbox_texts()

    _vsettings.VTEF_Settings.__annotations__["velo_auto_split_by_material"] = BoolProperty(
        name="导出时自动按材质拆分",
        description="导出临时对象时按带 Component 前缀的实际材质自动拆分；不会修改场景对象",
        default=True,
    )

    _vsettings.VTEF_Settings.__annotations__["generate_crossib_json"] = BoolProperty(
        name=_zh("生成 CrossIB.json"),
        description=_zh("提取对象时随模型文件夹生成 CrossIB.json v2，只记录 Component 匹配与透明性证据。跨 IB 导出必须使用该文件，并生成规则式 CrossIBClassifier.ini；不再生成 ShaderOverride.ini 或累积多个场景的 VS Hash。"),
        default=True,
    )
    _vsettings.VTEF_Settings.__annotations__["import_as_component_collections"] = BoolProperty(
        name=_zh("按组件创建子集合"),
        description=_zh("导入模型时在对象父集合下创建 C0/C1/... 子集合；关闭后沿用上游 EFMI 的单集合导入。"),
        default=True,
    )
    _vsettings.VTEF_Settings.__annotations__["extract_components_filter"] = StringProperty(
        name=_zh("组件过滤"),
        description=_zh("可选组件范围，例如 0-8 或 0,1,5-7。过滤后输出仍连续编号为 Component 0..N。"),
        default="",
    )
    _vsettings.VTEF_Settings.__annotations__["import_texture"] = BoolProperty(
        name=_zh("导入贴图"),
        description=_zh("导入模型后依据 TextureUsage.json 给网格指定源目录内的 .dds 贴图。"),
        default=True,
    )


def enabled() -> bool:
    return True


def register():
    _props.register()
    _al.init()
    _lod_debug_import.install_patches()
    _strip_unwanted_vendor_panels()
    _patch_panels_for_velo_tools()
    _patch_velo_settings()
    _al.register()
    bpy.types.Scene.VTEF_settings = bpy.props.PointerProperty(type=_vsettings.VTEF_Settings)
    _unified_vg_export.install_patches()
    try:
        from ...core.export import material_partition as _material_partition
        from ._efmi_core.blender_export.blender_export import ObjectMergerEFMI
        from .embedded.shapekey.export_state import finalize_merger
        _material_partition.install(
            ObjectMergerEFMI,
            "VTEF_settings",
            after_finalize=finalize_merger,
        )
    except Exception:
        import traceback
        traceback.print_exc()
    # For the single container (VELO_PT_game): collapse-header text + body draw (proxies the vendored root panel draw via a Shim).
    from ._efmi_core.addon import ui as _vui_desc
    _DESCRIPTOR.header_label = _zh("终末地 EFMI")
    _DESCRIPTOR.draw_body = _a2.make_draw_body(_vui_desc.VTEF_PT_SIDEBAR)
    _registry.register_descriptor(_DESCRIPTOR)
    try:
        _bridge_ui.register()
    except Exception:
        import traceback
        traceback.print_exc()
    try:
        _embedded.register()
    except Exception:
        import traceback
        traceback.print_exc()
    try:
        from . import form_identity as _form_identity
        _form_identity.install()
    except Exception:
        import traceback
        traceback.print_exc()
    try:
        from ...core.export import hook as _hook
        _hook.install_export_hook()
    except Exception:
        import traceback
        traceback.print_exc()
    try:
        _mmd_pick.register()
    except Exception:
        import traceback
        traceback.print_exc()
    # Issue A fix (A1 single container): hide the vendored root panel + re-parent its subpanels onto VELO_PT_game and gate
    # them by active_game. Must be called after all EFMI subpanels (vtef main panel, bridge, embedded/crossib/shapekey, ini_toggles) are registered.
    try:
        from ._efmi_core.addon import ui as _vui
        _a2.gate("VTEF_PT_SIDEBAR", "ENDFIELD", _vui.VTEF_PT_SIDEBAR)
    except Exception:
        import traceback
        traceback.print_exc()


def register_embedded_late():
    return


def unregister_embedded_late():
    return


def unregister():
    try:
        from . import form_identity as _form_identity
        _form_identity.remove()
    except Exception:
        pass
    try:
        from ...core.export import material_partition as _material_partition
        from ._efmi_core.blender_export.blender_export import ObjectMergerEFMI
        _material_partition.remove(ObjectMergerEFMI)
    except Exception:
        pass
    try:
        _a2.ungate("VTEF_PT_SIDEBAR")
    except Exception:
        pass
    try:
        _registry.unregister_descriptor("ENDFIELD")
    except Exception:
        pass
    try:
        _mmd_pick.unregister()
    except Exception:
        pass
    try:
        from ...core.export import hook as _hook
        _hook.remove_export_hook()
    except Exception:
        pass
    try:
        _embedded.unregister()
    except Exception:
        pass
    try:
        _unified_vg_export.remove_patches()
    except Exception:
        pass
    try:
        _bridge_ui.unregister()
    except Exception:
        pass
    try:
        _lod_debug_import.uninstall_patches()
    except Exception:
        pass
    if hasattr(bpy.types.Scene, "VTEF_settings"):
        try:
            del bpy.types.Scene.VTEF_settings
        except Exception:
            pass
    try:
        _al.unregister()
    except Exception:
        pass
    try:
        _props.unregister()
    except Exception:
        pass
