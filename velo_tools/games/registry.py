"""Velo Tools multi-game registry (single source of truth).

`scene.velo_tools.active_game` (the dropdown at the top of the game tab) is the sole
switch for the "current game". Each game driver registers one GameDescriptor in its own
register(); shared tools (export adapters, export hook, separate-by-material, etc.) all
query this table. Adding a new game only requires adding one descriptor, not editing
each tool individually.

This module is a pure data layer: it does not import bpy nor any game module, to avoid
circular imports. The shared layer uses lazy imports inside functions to fetch this module.
"""
from __future__ import annotations


class GameDescriptor:
    """Export wiring information describing one game.

    settings_attr  : name of this game's settings PointerProperty on the Scene (e.g. 'VTEF_settings')
    adapter_key    : adapter key in core/export/adapters.py ('EFMI' / 'WWMI')
    export_op      : export operator bl_idname (e.g. 'vtef.export_mod')
    export_op_class: export operator class name, used by the hook's _find_class to locate it (e.g. 'VTEF_Export')
    header_label   : display text for the VELO_PT_game single-container panel's collapse header (e.g. '鸣潮 WWMI'); attached by each game driver in register().
    draw_body      : optional callable(container_panel, context) that draws this game's root panel body inside the VELO_PT_game container
                     (the driver layer uses a Shim to proxy the vendored root panel draw). Attached by each game driver in register().
    """

    def __init__(
        self,
        key: str,
        game_value: str,
        display_name: str,
        settings_attr: str,
        adapter_key: str,
        export_op: str,
        export_op_class: str,
    ):
        self.key = key
        self.game_value = game_value
        self.display_name = display_name
        self.settings_attr = settings_attr
        self.adapter_key = adapter_key
        self.export_op = export_op
        self.export_op_class = export_op_class
        # For the single-container panel (VELO_PT_game): collapse-header text + body draw callback, attached by each game driver in register().
        self.header_label = None
        self.draw_body = None

    def settings(self, scene):
        return getattr(scene, self.settings_attr, None)

    def component_collection(self, scene):
        cfg = self.settings(scene)
        return getattr(cfg, "component_collection", None) if cfg is not None else None

    def __repr__(self):
        return f"<GameDescriptor {self.game_value} settings={self.settings_attr}>"


# game_value -> GameDescriptor (_ORDER preserves registration order, used by all_descriptors)
_REGISTRY = {}
_ORDER = []

_DEFAULT_GAME = "ENDFIELD"


def register_descriptor(desc: GameDescriptor) -> None:
    _REGISTRY[desc.game_value] = desc
    if desc.game_value not in _ORDER:
        _ORDER.append(desc.game_value)


def unregister_descriptor(game_value: str) -> None:
    _REGISTRY.pop(game_value, None)
    if game_value in _ORDER:
        _ORDER.remove(game_value)


def get_descriptor(game_value: str):
    return _REGISTRY.get(game_value)


def all_descriptors():
    return [_REGISTRY[g] for g in _ORDER if g in _REGISTRY]


def get_active_descriptor(scene):
    """Get the current game descriptor by scene.velo_tools.active_game; fall back to ENDFIELD if missing/unknown."""
    s = getattr(scene, "velo_tools", None)
    game_value = getattr(s, "active_game", _DEFAULT_GAME) if s is not None else _DEFAULT_GAME
    return _REGISTRY.get(game_value) or _REGISTRY.get(_DEFAULT_GAME)
