"""UI: N 面板 (View3D > Sidebar > Velo)。"""

import sys

import bpy


def _version_text():
    package = sys.modules.get(__package__ or "velo_tools")
    version = getattr(package, "bl_info", {}).get("version", (0, 0, 0))
    text = "v" + ".".join(str(part) for part in version)
    # Append the development pre-release / build marker (see velo_tools/_version.py).
    # On a tagged release PRERELEASE is None, so the text stays e.g. "v1.2.7".
    try:
        from ._version import PRERELEASE, BUILD
        if PRERELEASE:
            text += "-" + PRERELEASE
        if BUILD:
            text += " (" + BUILD + ")"
    except Exception:
        pass
    return text


class VELO_PT_main(bpy.types.Panel):
    bl_label = "Velo Tools"
    bl_idname = "VELO_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'

    def draw(self, context):
        layout = self.layout
        s = context.scene.velo_tools
        col = layout.column(align=True)
        col.label(text="Mod 制作辅助工具集", icon='TOOL_SETTINGS')
        col.label(text=_version_text(), icon='BLANK1')
        # 顶部功能区切换
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.prop(s, "active_tab", expand=True)
        # 「游戏」tab：游戏选择下拉（终末地 EFMI / 鸣潮 WWMI）。
        # 必须画在本父面板里——改变父面板上的属性才会触发整块 N 面板区域重建，
        # 让新合格的兄弟子面板（VTWW_PT_SIDEBAR 等）立即被 poll 重新求值并显示。
        # 若放在子面板里，改属性只重绘该子面板，兄弟面板不会被重新 poll（问题 A 根因）。
        if s.active_tab == 'GAME':
            layout.prop(s, "active_game", text="游戏")


def _is_match_tab(context):
    return context.scene.velo_tools.active_tab == 'MATCH'


class VELO_PT_vg_tools(bpy.types.Panel):
    """顶点组工具 Tab 顶部 — 来自 VTEF toolbox 的顶点组批量操作。"""
    bl_label = "顶点组操作"
    bl_idname = "VELO_PT_vg_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return _is_match_tab(context)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 1.1
        col.operator("vtef.merge_vertex_groups", icon='GROUP_VERTEX')
        col.operator("vtef.fill_gaps_in_vertex_groups", icon='ADD')
        col.operator("vtef.remove_unused_vertex_groups", icon='X')
        col.operator("vtef.remove_all_vertex_groups", icon='TRASH')


class VELO_PT_game(bpy.types.Panel):
    """「游戏」tab 下的单一游戏容器面板（问题 A 修复 + 单折叠头，1.1.3）。

    poll 只判 `active_tab=='GAME'` → 进 GAME tab 即恒实例化，切 active_game 只换内容（容器在场、
    子面板随 plain redraw 增减，复刻已验证可用的 tool_mode 机制）。折叠头按 active_game 显示当前
    游戏名；主体经各游戏描述符的 `draw_body`（驱动层用 Shim 代理 vendored 根面板 draw）绘制。
    各游戏子面板已由驱动层重挂到本容器并按 active_game 门控，故 GAME tab 内只显示选中游戏、无多余折叠头。
    """
    bl_label = ""  # 头部文本由 draw_header 动态显示当前游戏
    bl_idname = "VELO_PT_game"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        s = getattr(context.scene, "velo_tools", None)
        return s is not None and getattr(s, "active_tab", None) == 'GAME'

    @staticmethod
    def _active_desc(context):
        from .games import registry as _registry
        return _registry.get_active_descriptor(context.scene)

    def draw_header(self, context):
        desc = self._active_desc(context)
        self.layout.label(text=(getattr(desc, "header_label", None) or "游戏"))

    def draw(self, context):
        desc = self._active_desc(context)
        fn = getattr(desc, "draw_body", None) if desc is not None else None
        if callable(fn):
            fn(self, context)


_classes = (
    VELO_PT_main,
    VELO_PT_vg_tools,
    VELO_PT_game,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
