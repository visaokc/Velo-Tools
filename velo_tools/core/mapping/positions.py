"""Evaluated vertex-group positions shared by matching, drawing and picking."""

import bpy
from bpy.app.handlers import persistent

_pose_cache = {}


def pose_centroids_world(obj):
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return {}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    key = (depsgraph.as_pointer(), obj.as_pointer())
    cached = _pose_cache.get(key)
    if cached is not None:
        return cached
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        from ...operators import compute_all_centroids_local
        from types import SimpleNamespace
        local = compute_all_centroids_local(SimpleNamespace(type='MESH', data=mesh))
        result = {index: evaluated.matrix_world @ co for index, co in local.items()}
    finally:
        evaluated.to_mesh_clear()
    _pose_cache[key] = result
    return result


def pose_group_world(obj, names, fallback_local=None, has_fallback=False):
    points = pose_centroids_world(obj)
    if obj is None:
        return None
    for name in names:
        group = obj.vertex_groups.get((name or '').strip())
        if group is not None and group.index in points:
            return points[group.index]
    # Old tables retain rest-local snapshots across renames. Resolve identity in
    # rest space, then read that group's evaluated center; never draw a stale point.
    if has_fallback and fallback_local is not None:
        from ...operators import compute_all_centroids_local
        from mathutils import Vector
        rest = compute_all_centroids_local(obj)
        candidates = [index for index, co in rest.items()
                      if (co - Vector(fallback_local)).length_squared <= 1e-12]
        if len(candidates) == 1:
            return points.get(candidates[0])
    return None


@persistent
def invalidate_positions(*_args):
    if not _pose_cache:
        return
    _pose_cache.clear()
    from ...games.arknights_endfield import mmd_pick
    mmd_pick.invalidate_cached_endpoints()


def on_position_mode_update(self, context):
    invalidate_positions()
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def register():
    for handlers in (bpy.app.handlers.depsgraph_update_post,
                     bpy.app.handlers.frame_change_post,
                     bpy.app.handlers.load_post, bpy.app.handlers.undo_post,
                     bpy.app.handlers.redo_post):
        if invalidate_positions not in handlers:
            handlers.append(invalidate_positions)


def unregister():
    for handlers in (bpy.app.handlers.depsgraph_update_post,
                     bpy.app.handlers.frame_change_post,
                     bpy.app.handlers.load_post, bpy.app.handlers.undo_post,
                     bpy.app.handlers.redo_post):
        if invalidate_positions in handlers:
            handlers.remove(invalidate_positions)
    _pose_cache.clear()
