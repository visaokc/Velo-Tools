"""Wuthering Waves (鸣潮) integration driver for Velo Tools.

把修复版 WWMITools（已含 COLOR1→TEXCOORD1 修复）以「核心原样 vendored 在 _wwmi_core/
+ 本驱动层做接入适配」的方式挂进 Velo Tools，结构与 arknights_endfield(EFMI) 一致。

本驱动只负责把 WWMI 接进 Velo 的 UI / 注册框架（面板归类、收纳到“游戏” tab 下、
隐藏与终末地重复的 Toolbox、中和上游更新器），不改 _wwmi_core 内任何一行。
"""

import sys
from pathlib import Path

import bpy

# 上游 WWMI 依赖 libs（jinja2 / markupsafe）在 sys.path 中；在导入任何 _wwmi_core
# 子模块之前先注入，复刻上游 __init__.py 的做法。
_LIBS_DIR = str(Path(__file__).parent / "_wwmi_core" / "libs")
if _LIBS_DIR not in sys.path:
    sys.path.insert(0, _LIBS_DIR)

from ._wwmi_core import auto_load as _al
from ._wwmi_core import addon_updater_ops as _updater_ops
from ._wwmi_core.addon import settings as _wsettings
from ._wwmi_core.addon import ui as _wui
from .. import registry as _registry
from .. import _a2_panels as _a2


_TAB_VALUE = "GAME"
_GAME_VALUE = "WUTHERING"

# 鸣潮（WWMI）游戏描述符：导出算子/设置均指向 Velo 内置 fork（vtww / VTWW_settings），
# 不依赖外部独立 WWMI-Tools。
_DESCRIPTOR = _registry.GameDescriptor(
    key="WUTHERING",
    game_value="WUTHERING",
    display_name="鸣潮",
    settings_attr="VTWW_settings",
    adapter_key="WWMI",
    export_op="vtww.export_mod",
    export_op_class="VTWW_Export",
)

# 与 EFMI 同法剔除的 vendored 侧栏面板（更新器 / 调试，独立顶层面板，无 bl_parent_id）。
# 注意：_wwmi_core 已做命名空间 fork（WWMI_TOOLS_*→VTWW_*），故此处用 fork 后的 id。
_REMOVED_VENDOR_PANEL_IDS = {
    "VTWW_PT_UpdaterPanel",
    "VTWW_PT_DebugPanel",
}

# tool_mode 去掉 Toolbox 后保留的项（标识符 / 英文标签 / 描述 / 顺序与上游一致）。
_TOOL_MODE_ITEMS = [
    ('EXPORT_MOD', 'Export Mod', 'Export selected collection as WWMI mod'),
    ('IMPORT_OBJECT', 'Import Object', 'Import .ib ad .vb files from selected directory'),
    ('EXTRACT_FRAME_DATA', 'Extract Objects From Dump',
     'Extract components of all WWMI-compatible objects from the selected frame dump directory'),
]

# 还原用的原始引用快照（unregister 时复原）。
_orig_tool_mode_annotation = None
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
    # addon_updater_ops 里的 AddonUpdater* 算子 bl_idname = updater.addon + ".updater_*"
    # （上游写死 "wwmi_tools"），与独立 WWMI-Tools 同名。我们已中和更新器、且剥掉
    # UpdaterPanel/Preferences，这些算子无任何引用，整体剔除以避免与独立插件冲突。
    return getattr(cls, "__module__", "").endswith("addon_updater_ops")


def _strip_unwanted_vendor_panels():
    """从 auto_load 的注册列表中移除更新器/调试面板及 addon_updater_ops 算子，
    使其既不注册也不显示，避免与独立 WWMI-Tools 的同名全局类冲突。"""
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
        # 结构性 patch（须在注册前）；poll/draw 的 A2 门控改由 register() 末尾的 _a2.gate() 处理。
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


def _patch_tool_mode():
    """重定义 WWMI_Settings.tool_mode，删除 Toolbox 项以隐藏顶点组/多物体雕刻/Utility 段。"""
    global _orig_tool_mode_annotation
    _orig_tool_mode_annotation = _wsettings.VTWW_Settings.__annotations__.get("tool_mode", _MISSING)
    _wsettings.VTWW_Settings.__annotations__["tool_mode"] = bpy.props.EnumProperty(
        name="Mode",
        description="Defines list of available actions",
        items=list(_TOOL_MODE_ITEMS),
        update=lambda self, context: _wsettings.clear_error(self),
        default='EXTRACT_FRAME_DATA',
    )


def _neutralize_updater():
    """中和上游 addon_updater：auto_load 模块循环会调用 addon_updater_ops.register()，
    置空即不接 GitHub 更新器、不弹 reload popup；同时让 Preferences 不画在线更新 UI。"""
    global _orig_updater_register, _orig_pref_draw, _orig_pref_auto_check

    _orig_updater_register = getattr(_updater_ops, "register", None)
    _updater_ops.register = lambda: None

    pref = getattr(_wsettings, "Preferences", None)
    if pref is not None:
        _orig_pref_draw = getattr(pref, "draw", None)
        _orig_pref_auto_check = pref.__annotations__.get("auto_check_update", _MISSING)
        pref.__annotations__["auto_check_update"] = bpy.props.BoolProperty(
            name="Auto-check for Update",
            description="Disabled: Velo Tools 内置 WWMI core 不使用在线更新器。",
            default=False,
        )
        pref.draw = lambda self, context: self.layout.label(
            text="WWMI updater is disabled inside Velo Tools."
        )


def register():
    _al.init()
    _neutralize_updater()
    _patch_tool_mode()
    _strip_unwanted_vendor_panels()
    _patch_panels_for_velo_tools()
    _al.register()
    # 自行注册场景属性（不调用上游顶层 register()，从而不接 trigger_mod_export 计时器）。
    # 场景属性名也走 fork 命名空间，避免与独立 WWMI-Tools 的 Scene.wwmi_tools_settings 冲突。
    bpy.types.Scene.VTWW_settings = bpy.props.PointerProperty(type=_wsettings.VTWW_Settings)
    # 单容器（VELO_PT_game）用：折叠头文本 + 主体绘制（经 Shim 代理 vendored 根面板 draw）。
    _DESCRIPTOR.header_label = "鸣潮 WWMI"
    _DESCRIPTOR.draw_body = _a2.make_draw_body(_wui.VTWW_PT_SIDEBAR)
    _registry.register_descriptor(_DESCRIPTOR)
    # WWMI 在 EFMI 之后注册，此处再装一次导出 hook，使 MMD 预处理/材质路由 hook
    # 也命中 vtww 导出算子（_PATCHED 幂等，EFMI 那次已 patch 的不会重复）。
    try:
        from ...core.export import hook as _hook
        _hook.install_export_hook()
    except Exception:
        import traceback
        traceback.print_exc()
    # 跨场景导出 hook：导出源含 CrossSceneRouting.json → 走 orchestrator 折叠合并出多场景 mod（否则零影响）。
    try:
        from .embedded.crossscene import patch as _xspatch
        _xspatch.install()
    except Exception:
        import traceback
        traceback.print_exc()
    # 跨场景合并 UI（面板 + 合并算子 + Scene 属性）。
    try:
        from .embedded.crossscene import ui as _xsui
        _xsui.register()
    except Exception:
        import traceback
        traceback.print_exc()
    # 抽取时额外产出 ShaderTextureUsage.json（挂载 build_components 捕获 + write_objects 产出，幂等可逆）。
    try:
        from . import _shader_texture_usage as _stu
        _stu.install_patches()
    except Exception:
        import traceback
        traceback.print_exc()
    # 问题 A 修复（A1 单容器）：隐藏 vendored 根面板 + 把其子面板重挂到 VELO_PT_game 并按
    # active_game 门控。须在本游戏所有子面板注册完之后调用（此时 vtww 子面板已由 _al.register 注册）。
    _a2.gate("VTWW_PT_SIDEBAR", _GAME_VALUE, _wui.VTWW_PT_SIDEBAR)


def unregister():
    try:
        _a2.ungate("VTWW_PT_SIDEBAR")
    except Exception:
        pass
    # 复原 ShaderTextureUsage 挂载（WWMI unregister 不接 export hook 的遗漏不照抄，本补丁显式卸载）。
    try:
        from . import _shader_texture_usage as _stu
        _stu.uninstall_patches()
    except Exception:
        pass
    try:
        from .embedded.crossscene import patch as _xspatch
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

    # 复原被改面板属性。
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

    # 复原 tool_mode 注解。
    global _orig_tool_mode_annotation
    if _orig_tool_mode_annotation is not _MISSING and _orig_tool_mode_annotation is not None:
        _wsettings.WWMI_Settings.__annotations__["tool_mode"] = _orig_tool_mode_annotation
    _orig_tool_mode_annotation = None

    # 复原 updater / Preferences。
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
