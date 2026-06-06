from __future__ import annotations

import bpy
from bpy.app.handlers import persistent


def _tag_redraw(context=None):
    ctx = context or bpy.context
    screen = getattr(ctx, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def reset_overlay_pick_runtime(context=None, *, disable_overlays=False):
    ctx = context or bpy.context
    scene = getattr(ctx, "scene", None)
    general_overlay_enabled = False
    mmd_overlay_enabled = False
    if scene is not None:
        gm = getattr(scene, "velo_general_mapping", None)
        general_overlay_enabled = bool(gm is not None and getattr(gm, "show_overlay", False))
        ef = getattr(scene, "velo_endfield", None)
        mmd_overlay_enabled = bool(ef is not None and getattr(ef, "show_overlay", False))

    # Overlay visibility is user-controlled. Keep the kwarg for compatibility,
    # but never mutate show_overlay here.
    _ = disable_overlays

    general_pick = None
    mmd_pick = None

    try:
        from ..general_mapping import pick as _general_pick
        general_pick = _general_pick
        _general_pick.reset_state(ctx, restore_brush=False)
    except Exception:
        pass
    try:
        from ..games.arknights_endfield import mmd_pick as _mmd_pick
        mmd_pick = _mmd_pick
        _mmd_pick.reset_state(ctx, restore_brush=False)
    except Exception:
        pass
    try:
        from .. import overlay as _overlay
        if hasattr(_overlay, "invalidate_cache"):
            _overlay.invalidate_cache()
        if hasattr(_overlay, "invalidate_mmd_cache"):
            _overlay.invalidate_mmd_cache()
    except Exception:
        pass
    try:
        from ..general_mapping import overlay as _general_overlay
        if hasattr(_general_overlay, "invalidate_cache"):
            _general_overlay.invalidate_cache()
    except Exception:
        pass

    if general_overlay_enabled and general_pick is not None:
        try:
            general_pick.start_modal_if_needed()
        except Exception:
            pass
    if mmd_overlay_enabled and mmd_pick is not None:
        try:
            mmd_pick.start_modal_if_needed()
        except Exception:
            pass

    _tag_redraw(ctx)


@persistent
def _velo_weight_load_post(_scene):
    reset_overlay_pick_runtime(None)


def register():
    if _velo_weight_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_velo_weight_load_post)
    reset_overlay_pick_runtime(None)


def unregister():
    if _velo_weight_load_post in bpy.app.handlers.load_post:
        try:
            bpy.app.handlers.load_post.remove(_velo_weight_load_post)
        except Exception:
            pass
