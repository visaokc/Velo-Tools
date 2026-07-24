"""Wuthering Waves (鸣潮) integration driver for Velo Tools.

Mounts the fixed WWMITools (already includes the COLOR1->TEXCOORD1 fix) into Velo Tools
using the "core vendored as-is under _wwmi_core/ + this driver layer does the integration
adaptation" approach, with the same structure as arknights_endfield(EFMI).

This driver is only responsible for wiring WWMI into Velo's UI / registration framework
(panel categorization, tucking it under the "game" tab, hiding the Toolbox that duplicates
arknights_endfield, neutralizing the upstream updater); it does not change a single line
inside _wwmi_core.
"""

import sys
from pathlib import Path

import bpy

# Upstream WWMI depends on libs (jinja2 / markupsafe) being on sys.path; inject before
# importing any _wwmi_core submodule, replicating the upstream __init__.py approach.
_LIBS_DIR = str(Path(__file__).parent / "_wwmi_core" / "libs")
if _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

from ._wwmi_core import auto_load as _al
from ._wwmi_core import addon_updater_ops as _updater_ops
from ._wwmi_core.addon import settings as _wsettings
from ._wwmi_core.addon import ui as _wui
from . import ui_l10n as _ui_l10n
from .. import registry as _registry
from .. import _a2_panels as _a2


_TAB_VALUE = "GAME"
_GAME_VALUE = "WUTHERING"


def _zh(text: str) -> str:
    # Self-heal strings that were decoded with the wrong codec (cp1252 vs
    # utf-8), same guard as the EFMI bridge.
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return text

# Wuthering Waves (WWMI) game descriptor: export operator/settings both point to Velo's
# built-in fork (vtww / VTWW_settings), no dependency on a standalone external WWMI-Tools.
_DESCRIPTOR = _registry.GameDescriptor(
    key="WUTHERING",
    game_value="WUTHERING",
    display_name="鸣潮",
    settings_attr="VTWW_settings",
    adapter_key="WWMI",
    export_op="vtww.export_mod",
    export_op_class="VTWW_Export",
)

# Vendored sidebar panels removed the same way as EFMI (updater / debug, standalone
# top-level panels, no bl_parent_id).
# Note: _wwmi_core already did a namespace fork (WWMI_TOOLS_*->VTWW_*), so use the forked ids here.
_REMOVED_VENDOR_PANEL_IDS = {
    "VTWW_PT_UpdaterPanel",
    "VTWW_PT_DebugPanel",
}

# Items retained in tool_mode after dropping Toolbox (identifier / order kept
# consistent with upstream; labels and descriptions localized like EFMI).
_TOOL_MODE_ITEMS = [
    ('EXPORT_MOD', '导出 Mod', '将所选集合导出为 WWMI mod'),
    ('IMPORT_OBJECT', '导入对象', '从所选目录导入 .ib 和 .vb 模型'),
    ('EXTRACT_FRAME_DATA', '提取帧数据',
     '从所选 Frame Dump 目录提取所有 WWMI 兼容对象的组件'),
]

# Original reference snapshots used for restoration (restored on unregister).
_orig_tool_mode_annotation = None
_orig_mod_skeleton_type_annotation = None
_orig_updater_register = None
_orig_pref_draw = None
_orig_pref_auto_check = None
_patched_panels = []  # [(cls, {attr: old_value_or_MISSING})]

_MISSING = object()


def enabled() -> bool:
    return True


def _is_removed_vendor_panel(cls) -> bool:
    return getattr(cls, "bl_idname", "") in _REMOVED_VENDOR_PANEL_IDS


def _is_updater_class(cls) -> bool:
    # The AddonUpdater* operators in addon_updater_ops have bl_idname = updater.addon + ".updater_*"
    # (upstream hardcodes "wwmi_tools"), sharing the name with standalone WWMI-Tools. We've already
    # neutralized the updater and stripped UpdaterPanel/Preferences, so these operators have no
    # references; remove them entirely to avoid conflicting with the standalone addon.
    return getattr(cls, "__module__", "").endswith("addon_updater_ops")


def _strip_unwanted_vendor_panels():
    """Remove the updater/debug panels and addon_updater_ops operators from auto_load's
    registration list so they neither register nor display, avoiding conflicts with
    standalone WWMI-Tools' identically named global classes."""
    if not _al.ordered_classes:
        return
    _al.ordered_classes = [
        cls
        for cls in _al.ordered_classes
        if not _is_removed_vendor_panel(cls) and not _is_updater_class(cls)
    ]


def _record_panel_attr(cls, attr, store):
    store[attr] = getattr(cls, attr, _MISSING)


def _patch_single_panel(cls, root_cls):
    if not isinstance(cls, type) or not issubclass(cls, bpy.types.Panel):
        return
    if getattr(cls, "bl_space_type", None) != "VIEW_3D":
        return
    if getattr(cls, "bl_region_type", None) != "UI":
        return
    if getattr(cls, "bl_category", None) == "Text":
        return

    store = {}
    _record_panel_attr(cls, "bl_category", store)
    cls.bl_category = "Velo Tools"

    if cls is root_cls:
        # Structural patch (must happen before registration); the A2 gating of poll/draw is handled by _a2.gate() at the end of register().
        for attr in ("bl_label", "bl_parent_id", "bl_options", "draw_header"):
            _record_panel_attr(cls, attr, store)
        cls.bl_label = "鸣潮 WWMI"
        cls.bl_parent_id = "VELO_PT_main"
        cls.bl_options = set(getattr(cls, "bl_options", set())) | {"DEFAULT_CLOSED"}

        def _no_header(self, context):
            return

        cls.draw_header = _no_header

    _patched_panels.append((cls, store))


def _patch_panels_for_velo_tools():
    root = _wui.VTWW_PT_SIDEBAR
    seen = {id(root)}
    _patch_single_panel(root, root)
    for cls in list(_al.ordered_classes or ()):
        if not isinstance(cls, type) or id(cls) in seen or _is_removed_vendor_panel(cls):
            continue
        seen.add(id(cls))
        _patch_single_panel(cls, root)


# Localization patch state (snapshots restored on unregister).
_l10n_patched_classes = []  # [(cls, {attr: old_value_or_MISSING})]
_l10n_orig_annotations = []  # [(cls, prop_name, original_deferred)]


def _patch_l10n_class_texts():
    """Set Chinese bl_label / bl_description on vendored classes by class name
    (must run before _al.register(); Blender bakes these at register time)."""
    texts = {**_ui_l10n.WWMI_CLASS_TEXTS, **_ui_l10n.INI_TOGGLE_CLASS_TEXTS}
    for cls in list(_al.ordered_classes or ()):
        entry = texts.get(getattr(cls, "__name__", ""))
        if entry is None:
            continue
        label, description = entry
        store = {}
        _record_panel_attr(cls, "bl_label", store)
        cls.bl_label = _zh(label)
        if description:
            _record_panel_attr(cls, "bl_description", store)
            cls.bl_description = _zh(description)
        _l10n_patched_classes.append((cls, store))


def _translated_enum_items(items, enum_texts):
    """Replace only label/description of static enum items by identifier;
    identifier, icon and number fields pass through untouched."""
    out = []
    for item in items:
        item = tuple(item)
        entry = enum_texts.get(item[0])
        if entry is None or len(item) < 3:
            out.append(item)
            continue
        label, desc = entry
        new = list(item)
        new[1] = _zh(label)
        if desc is not None:
            new[2] = _zh(desc)
        out.append(tuple(new))
    return out


def _patch_l10n_properties():
    """Rebuild dictionary-listed property annotations with Chinese name /
    description / enum texts. All other keywords are copied from the live
    annotation, so upstream changes to defaults / limits / callbacks survive
    a core upgrade without touching this patch."""
    from ._wwmi_core.addon.modules.ini_toggles import props as _tprops

    prop_texts = {"VTWW_Settings": _ui_l10n.WWMI_PROPERTY_TEXTS,
                  **_ui_l10n.INI_TOGGLE_PROPERTY_TEXTS}
    owners = {"VTWW_Settings": _wsettings.VTWW_Settings}
    for cls_name in _ui_l10n.INI_TOGGLE_PROPERTY_TEXTS:
        owners[cls_name] = getattr(_tprops, cls_name, None)

    for cls_name, texts in prop_texts.items():
        cls = owners.get(cls_name)
        if cls is None:
            continue
        ann_map = cls.__annotations__
        for prop_name, (name_text, desc_text) in texts.items():
            ann = ann_map.get(prop_name)
            if ann is None or not hasattr(ann, "function") or not hasattr(ann, "keywords"):
                continue
            kwargs = dict(ann.keywords)
            if name_text is not None:
                kwargs["name"] = _zh(name_text)
            if desc_text is not None:
                kwargs["description"] = _zh(desc_text)
            if (cls_name == "VTWW_Settings"
                    and prop_name == "unrestricted_custom_shape_keys"):
                kwargs["default"] = True
            enum_texts = _ui_l10n.WWMI_ENUM_TEXTS.get((cls_name, prop_name))
            if enum_texts and isinstance(kwargs.get("items"), (list, tuple)):
                kwargs["items"] = _translated_enum_items(kwargs["items"], enum_texts)
            _l10n_orig_annotations.append((cls, prop_name, ann))
            ann_map[prop_name] = ann.function(**kwargs)


def _restore_l10n():
    global _l10n_patched_classes, _l10n_orig_annotations
    for cls, prop_name, ann in reversed(_l10n_orig_annotations):
        try:
            cls.__annotations__[prop_name] = ann
        except Exception:
            pass
    _l10n_orig_annotations = []
    for cls, store in reversed(_l10n_patched_classes):
        for attr, old in store.items():
            try:
                if old is _MISSING:
                    if attr in cls.__dict__:
                        delattr(cls, attr)
                else:
                    setattr(cls, attr, old)
            except Exception:
                pass
    _l10n_patched_classes = []


def _patch_tool_mode():
    """Redefine WWMI_Settings.tool_mode, deleting the Toolbox item to hide the vertex group / multi-object sculpt / Utility sections."""
    global _orig_tool_mode_annotation
    _orig_tool_mode_annotation = _wsettings.VTWW_Settings.__annotations__.get("tool_mode", _MISSING)
    _wsettings.VTWW_Settings.__annotations__["tool_mode"] = bpy.props.EnumProperty(
        name=_zh("模式"),
        description=_zh("切换当前鸣潮 WWMI/Velo 工具功能。"),
        items=[(i, _zh(label), _zh(desc)) for i, label, desc in _TOOL_MODE_ITEMS],
        update=lambda self, context: _wsettings.clear_error(self),
        default='EXTRACT_FRAME_DATA',
    )


def _patch_mod_skeleton_type():
    """Add a 3rd export option `Per-Component (from Merged)` to mod_skeleton_type by re-defining its
    EnumProperty annotation (driver-layer, same technique as _patch_tool_mode; restored on unregister;
    `_wwmi_core` source untouched). The velo `embedded/per_from_merged.py` export hook interprets this
    value: it translates unified vertex-group names back to per-component local numbering (+ stray-weight
    validation), then runs a stock COMPONENT export. The original 2 items keep their exact labels/descs."""
    global _orig_mod_skeleton_type_annotation
    _orig_mod_skeleton_type_annotation = _wsettings.VTWW_Settings.__annotations__.get("mod_skeleton_type", _MISSING)
    # Item labels stay in English on purpose (Merged / Per-Component are
    # technical terms by project convention); descriptions are localized.
    _wsettings.VTWW_Settings.__annotations__["mod_skeleton_type"] = bpy.props.EnumProperty(
        name=_zh("骨架"),
        description=_zh("请选择与导入时一致的骨架类型！决定导出 mod.ini 的逻辑。"),
        items=[
            ('MERGED', 'Merged', _zh('该骨架的网格使用统一顶点组列表。')),
            ('COMPONENT', 'Per-Component', _zh('该骨架的网格按组件拆分顶点组列表。')),
            ('COMPONENT_FROM_MERGED', 'Per-Component (from Merged)',
             _zh('按 Merged（统一）骨架编辑、导出 Per-Component mod：统一顶点组编号自动回译为'
                 '各组件局部编号。避免 Merged 模式同屏多个相同对象时的运行期暂停。把权重刷到'
                 '顶点组所属组件之外的骨骼会报错并阻止导出。')),
        ],
        default=0,
    )


def _neutralize_updater():
    """Neutralize the upstream addon_updater: the auto_load module loop calls addon_updater_ops.register();
    nulling it out means no GitHub updater is wired up and no reload popup appears; also make Preferences
    not draw the online-update UI."""
    global _orig_updater_register, _orig_pref_draw, _orig_pref_auto_check

    _orig_updater_register = getattr(_updater_ops, "register", None)
    _updater_ops.register = lambda: None

    pref = getattr(_wsettings, "Preferences", None)
    if pref is not None:
        _orig_pref_draw = getattr(pref, "draw", None)
        _orig_pref_auto_check = pref.__annotations__.get("auto_check_update", _MISSING)
        pref.__annotations__["auto_check_update"] = bpy.props.BoolProperty(
            name=_zh("自动检查更新"),
            description=_zh("已禁用：Velo Tools 内置 WWMI core 不使用在线更新器。"),
            default=False,
        )
        pref.draw = lambda self, context: self.layout.label(
            text=_zh("Velo Tools 内置 WWMI core 已禁用在线更新器。")
        )


def register():
    _al.init()
    _neutralize_updater()
    _patch_tool_mode()
    _patch_mod_skeleton_type()
    # Slot-style texture export option: the VTWW_Settings annotation must be
    # injected before _al.register() registers the settings class.
    try:
        from .embedded import slot_textures as _slott
        _slott.inject_settings()
    except Exception:
        import traceback
        traceback.print_exc()
    # Velo import extras settings (component sub-collections + texture assign):
    # injected before _al.register() like the slot-style option above.
    try:
        from .embedded import velo_import as _vimport
        _vimport.inject_settings()
    except Exception:
        import traceback
        traceback.print_exc()
    # Localization runs after every annotation re-definition above and before
    # _al.register() (labels/annotations are baked at class registration).
    _patch_l10n_class_texts()
    _patch_l10n_properties()
    _strip_unwanted_vendor_panels()
    _patch_panels_for_velo_tools()
    _al.register()
    # Register scene properties ourselves (don't call the upstream top-level register(), so the trigger_mod_export timer isn't wired up).
    # The scene property name also uses the fork namespace, avoiding conflicts with standalone WWMI-Tools' Scene.wwmi_tools_settings.
    bpy.types.Scene.VTWW_settings = bpy.props.PointerProperty(type=_wsettings.VTWW_Settings)
    # One collection visibility policy serves stock single-IB, Per-Component
    # (from Merged), and Cross-Scene export. The stock route is connected by
    # replacing only ObjectMerger's imported provider binding.
    try:
        from .embedded import export_selection as _export_selection
        _export_selection.install()
    except Exception:
        import traceback
        traceback.print_exc()
    try:
        from ...core.export import material_partition as _material_partition
        from ._wwmi_core.blender_export.blender_export import ObjectMergerWWMI
        from .embedded.crossscene.export_units import postprocess_partitioned_fragment
        _material_partition.install(
            ObjectMergerWWMI,
            "VTWW_settings",
            after_split=postprocess_partitioned_fragment,
        )
    except Exception:
        import traceback
        traceback.print_exc()
    # For the single container (VELO_PT_game): collapse-header text + body draw (proxies the vendored root panel draw via a Shim).
    _DESCRIPTOR.header_label = "鸣潮 WWMI"
    _DESCRIPTOR.draw_body = _a2.make_draw_body(_wui.VTWW_PT_SIDEBAR)
    _registry.register_descriptor(_DESCRIPTOR)
    # WWMI registers after EFMI; install the export hook once more here so the MMD preprocessing /
    # material routing hooks also catch the vtww export operator (_PATCHED is idempotent, so what EFMI already patched won't be repeated).
    try:
        from ...core.export import hook as _hook
        _hook.install_export_hook()
    except Exception:
        import traceback
        traceback.print_exc()
    # Cross-scene hook: schema-v3 aggregate roots use the direct compiler; ordinary roots stay on the stock path.
    try:
        from .embedded.crossscene import patch as _xspatch
        _xspatch.install()
        _xspatch.install_import()
    except Exception:
        import traceback
        traceback.print_exc()
    # Velo import extras: execute wrap (component sub-collection reorg +
    # texture auto-assign) installed AFTER the cross-scene import fix so it
    # wraps outermost; also replaces draw_menu_import_object for the
    # EFMI-style layout (options above the import button).
    try:
        from .embedded import velo_import as _vimport
        _vimport.install()
    except Exception:
        import traceback
        traceback.print_exc()
    # Per-Component (from Merged) export hook: when mod_skeleton_type == COMPONENT_FROM_MERGED, remap
    # unified VG names -> per-component local + validate, then run a stock COMPONENT export. Installed
    # after the cross-scene patch so it wraps it (outermost); a no-op for the other two modes.
    try:
        from .embedded import per_from_merged as _pfm
        _pfm.install()
    except Exception:
        import traceback
        traceback.print_exc()
    # LOD export hook: patches ModExporter.export_mod (independent target from the operator wraps
    # above); appends per-LOD remapped blend buffers + ini override sections when the export source's
    # Metadata.json carries velo per-component "lods" entries (MERGED mode only; a no-op otherwise).
    try:
        from .embedded.lod import export_hook as _lodhook
        _lodhook.install()
    except Exception:
        import traceback
        traceback.print_exc()
    # Independent custom ShapeKey export wraps the final stock/LOD buffer and
    # INI lifecycle while leaving the vendored unrestricted path disabled.
    try:
        from .embedded.shapekey import hook as _shapehook
        _shapehook.install()
    except Exception:
        import traceback
        traceback.print_exc()
    # Extract LOD Data UI (panel + operator + Scene properties); registered before the cross-scene UI
    # so the LOD panel sits right under the WWMI section, above the cross-scene panel.
    try:
        from .embedded.lod import ui as _lodui
        _lodui.register()
    except Exception:
        import traceback
        traceback.print_exc()
    # Cross-scene merge UI (panel + merge operator + Scene properties).
    try:
        from .embedded.crossscene import ui as _xsui
        _xsui.register()
    except Exception:
        import traceback
        traceback.print_exc()
    # Slot-style texture layer: IniMaker hook (installed AFTER the LOD hook so
    # it post-processes the final rendered ini, whatever template produced it)
    # + the "Velo 兼容选项" export box and the form-merge sub-panel.
    try:
        from .embedded import slot_textures as _slott
        _slott.install()
        _slott.register_ui()
    except Exception:
        import traceback
        traceback.print_exc()
    # Final INI artifact sanitizer: installed after slot_textures so it wraps
    # outermost and cleans stock, LOD, cross-scene and custom-template renders.
    try:
        from .embedded import ini_sanitize as _inisanitize
        _inisanitize.install()
    except Exception:
        import traceback
        traceback.print_exc()
    # During extraction, additionally produce ShaderTextureUsage.json (hook build_components to capture + write_objects to output, idempotent and reversible).
    try:
        from . import _shader_texture_usage as _stu
        _stu.install_patches()
    except Exception:
        import traceback
        traceback.print_exc()
    # Texture identity prototype wraps the completed STU extraction path and
    # emits only a disabled exporter preview until the runtime ABI exists.
    try:
        from .embedded import texture_identity as _texture_identity
        _texture_identity.install()
    except Exception:
        import traceback
        traceback.print_exc()
    # Issue A fix (A1 single container): hide the vendored root panel + re-parent its sub-panels to
    # VELO_PT_game and gate by active_game. Must be called after all this game's sub-panels are registered
    # (by this point the vtww sub-panels have already been registered by _al.register).
    _a2.gate("VTWW_PT_SIDEBAR", _GAME_VALUE, _wui.VTWW_PT_SIDEBAR)
    # Velo raw-mesh tool (extract/import/export VFX & scene meshes by hash):
    # self-contained panel under VELO_PT_game, gated to WUTHERING. Registered
    # after the container exists; fully isolated from VTWW_* (gate() never sees it).
    try:
        from .embedded import raw_mesh as _rawmesh
        _rawmesh.register()
    except Exception:
        import traceback
        traceback.print_exc()


def unregister():
    try:
        from ...core.export import material_partition as _material_partition
        from ._wwmi_core.blender_export.blender_export import ObjectMergerWWMI
        _material_partition.remove(ObjectMergerWWMI)
    except Exception:
        pass
    try:
        from .embedded import export_selection as _export_selection
        _export_selection.remove()
    except Exception:
        pass
    try:
        from .embedded import raw_mesh as _rawmesh
        _rawmesh.unregister()
    except Exception:
        pass
    try:
        _a2.ungate("VTWW_PT_SIDEBAR")
    except Exception:
        pass
    # Final INI artifact sanitizer first (LIFO: installed after slot_textures).
    try:
        from .embedded import ini_sanitize as _inisanitize
        _inisanitize.remove()
    except Exception:
        pass
    # Slot-style texture layer after the final sanitizer (LIFO).
    try:
        from .embedded import slot_textures as _slott
        _slott.unregister_ui()
        _slott.remove()
    except Exception:
        pass
    try:
        from .embedded import texture_identity as _texture_identity
        _texture_identity.remove()
    except Exception:
        pass
    # Restore the ShaderTextureUsage hook (don't copy WWMI unregister's omission of unhooking the export hook; this patch unhooks explicitly).
    try:
        from . import _shader_texture_usage as _stu
        _stu.uninstall_patches()
    except Exception:
        pass
    # Unregister the LOD UI / hook first (LIFO: they were installed last).
    try:
        from .embedded.lod import ui as _lodui
        _lodui.unregister()
    except Exception:
        pass
    try:
        from .embedded.shapekey import hook as _shapehook
        _shapehook.remove()
    except Exception:
        pass
    try:
        from .embedded.lod import export_hook as _lodhook
        _lodhook.remove()
    except Exception:
        pass
    # Remove the Per-Component (from Merged) hook (LIFO: it wraps the operator outermost).
    try:
        from .embedded import per_from_merged as _pfm
        _pfm.remove()
    except Exception:
        pass
    # Velo import extras before the cross-scene import fix (LIFO: installed after it).
    try:
        from .embedded import velo_import as _vimport
        _vimport.remove()
    except Exception:
        pass
    try:
        from .embedded.crossscene import patch as _xspatch
        _xspatch.remove_import()
        _xspatch.remove()
    except Exception:
        pass
    try:
        from .embedded.crossscene import ui as _xsui
        _xsui.unregister()
    except Exception:
        pass
    try:
        _registry.unregister_descriptor("WUTHERING")
    except Exception:
        pass
    if hasattr(bpy.types.Scene, "VTWW_settings"):
        try:
            del bpy.types.Scene.VTWW_settings
        except Exception:
            pass

    try:
        _al.unregister()
    except Exception:
        import traceback
        traceback.print_exc()

    # Restore patched panel attributes.
    global _patched_panels
    for cls, store in reversed(_patched_panels):
        for attr, old in store.items():
            try:
                if old is _MISSING:
                    if attr in cls.__dict__:
                        delattr(cls, attr)
                else:
                    setattr(cls, attr, old)
            except Exception:
                pass
    _patched_panels = []

    # Restore localized class texts and property annotations.
    _restore_l10n()

    # Restore the tool_mode annotation.
    global _orig_tool_mode_annotation
    if _orig_tool_mode_annotation is not _MISSING and _orig_tool_mode_annotation is not None:
        _wsettings.VTWW_Settings.__annotations__["tool_mode"] = _orig_tool_mode_annotation
    _orig_tool_mode_annotation = None

    # Restore the mod_skeleton_type annotation.
    global _orig_mod_skeleton_type_annotation
    if _orig_mod_skeleton_type_annotation is not _MISSING and _orig_mod_skeleton_type_annotation is not None:
        _wsettings.VTWW_Settings.__annotations__["mod_skeleton_type"] = _orig_mod_skeleton_type_annotation
    _orig_mod_skeleton_type_annotation = None

    # Restore the slot-style settings annotation.
    try:
        from .embedded import slot_textures as _slott
        _slott.restore_settings()
    except Exception:
        pass

    # Restore the velo import extras annotations.
    try:
        from .embedded import velo_import as _vimport
        _vimport.restore_settings()
    except Exception:
        pass

    # Restore updater / Preferences.
    global _orig_updater_register, _orig_pref_draw, _orig_pref_auto_check
    if _orig_updater_register is not None:
        _updater_ops.register = _orig_updater_register
    _orig_updater_register = None

    pref = getattr(_wsettings, "Preferences", None)
    if pref is not None:
        if _orig_pref_draw is not None:
            pref.draw = _orig_pref_draw
        if _orig_pref_auto_check is not _MISSING and _orig_pref_auto_check is not None:
            pref.__annotations__["auto_check_update"] = _orig_pref_auto_check
    _orig_pref_draw = None
    _orig_pref_auto_check = None
