"""UI: N panel (View3D > Sidebar > Velo)."""

import sys

import bpy

from .updater import notify


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
        # Top-of-panel "update available" banner (host-level auto-updater notice).
        notify.draw_update_banner(self, context)
        s = context.scene.velo_tools
        col = layout.column(align=True)
        title_row = col.row(align=True)
        title_row.label(text='Mod Production Auxiliary Toolset', icon='TOOL_SETTINGS')
        repository = title_row.operator("wm.url_open", text="GitHub", icon='URL')
        package = sys.modules.get(__package__ or "velo_tools")
        repository.url = getattr(package, "bl_info", {}).get("doc_url", "")
        col.label(text=_version_text(), icon='BLANK1')
        # Top function-area tab switch
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.prop(s, "active_tab", expand=True)
        # "Game" tab: game selection dropdown (Endfield EFMI / Wuthering Waves WWMI).
        # Must be drawn in this parent panel -- only changing a property on the parent panel
        # triggers a rebuild of the whole N-panel region,
        # so newly qualifying sibling sub-panels (VTWW_PT_SIDEBAR etc.) are immediately re-polled and shown.
        # If placed in a sub-panel, changing the property only redraws that sub-panel; sibling panels are not re-polled (root cause of issue A).
        if s.active_tab == 'GAME':
            layout.prop(s, "active_game", text='Game')


def _is_match_tab(context):
    return context.scene.velo_tools.active_tab == 'MATCH'


class VELO_PT_vg_tools(bpy.types.Panel):
    """Top of the Vertex Group tools Tab -- batch vertex-group operations from the VTEF toolbox."""
    bl_label = 'Vertex Group Operations'
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
    """Single game container panel under the "Game" tab (issue A fix + single collapse header, 1.1.3).

    poll only checks `active_tab=='GAME'` -> always instantiated once on the GAME tab; switching active_game only swaps content (container stays present,
    sub-panels added/removed via plain redraw, replicating the verified-working tool_mode mechanism). The collapse header shows the current
    game name per active_game; the body is drawn via each game descriptor's `draw_body` (driver layer uses a Shim to proxy the vendored root panel draw).
    Each game's sub-panels have been re-parented to this container by the driver layer and gated by active_game, so only the selected game shows inside the GAME tab, with no extra collapse headers.
    """
    bl_label = ""  # Header text is shown dynamically by draw_header for the current game
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
        self.layout.label(text=(getattr(desc, "header_label", None) or 'Game'))

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
