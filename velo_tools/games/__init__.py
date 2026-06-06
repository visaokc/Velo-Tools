"""Velo Tools - multi-game partition root

Each game submodule must expose:
    register()     -> register all its own bpy classes
    unregister()   -> unregister
    enabled()      -> bool, whether to expose it in the current UI

The top-level __init__.py decides whether to call register() based on enabled().
"""

from . import arknights_endfield
from . import wuthering_waves

ALL_GAMES = (arknights_endfield, wuthering_waves)


def register():
    for g in ALL_GAMES:
        if g.enabled():
            g.register()


def unregister():
    for g in reversed(ALL_GAMES):
        if g.enabled():
            try:
                g.unregister()
            except Exception:
                pass
