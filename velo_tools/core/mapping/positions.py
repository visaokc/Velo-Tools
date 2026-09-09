"""Evaluated vertex-group positions shared by matching, drawing and picking."""

import bpy
from bpy.app.handlers import persistent

_pose_cache = {}
_rest_lookup_cache = {}


def _mesh_centroids_local(mesh):
    if not mesh.is_editmode:
        from ...operators import compute_all_centroids_local
        from types import SimpleNamespace
        return compute_all_centroids_local(SimpleNamespace(type='MESH', data=mesh))
    # to_mesh() can expose pre-edit vertices while Blender displays the BMesh.
    # Read the live edit data without flushing it or mutating from a draw handler.
    import bmesh
    from mathutils import Vector
    edit_mesh = bmesh.from_edit_mesh(mesh)
    layer = edit_mesh.verts.layers.deform.active
    if layer is None:
        return {}
    sums, weights = {}, {}
    for vertex in edit_mesh.verts:
        for index, weight in vertex[layer].items():
            if weight <= 0.0:
                continue
            if index not in sums:
                sums[index] = Vector((0.0, 0.0, 0.0))
                weights[index] = 0.0
            sums[index] += vertex.co * weight
            weights[index] += weight
    return {index: co / weights[index] for index, co in sums.items()}


def pose_centroids_world(obj):
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return {}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    key = (depsgraph.as_pointer(), obj.as_pointer())
    cached = _pose_cache.get(key)
    if cached is not None:
        return cached
    evaluated = obj.evaluated_get(depsgraph)
    # Preserving every layer can rebuild from pre-edit object data in Blender.
    # The viewport mesh retains deform weights and current Edit Mode topology.
    mesh = evaluated.to_mesh(preserve_all_data_layers=not obj.data.is_editmode,
                             depsgraph=depsgraph)
    try:
        local = _mesh_centroids_local(mesh)
    finally:
        evaluated.to_mesh_clear()
    result = {index: evaluated.matrix_world @ co for index, co in local.items()}
    _pose_cache[key] = result
    return result


def pose_group_world(obj, names, fallback_local=None, has_fallback=False):
    points = pose_centroids_world(obj)
    if obj is None:
        return None
    for name in names:
        group = obj.vertex_groups.get((name or '').strip())
        if group is not None:
            # A surviving group without weighted vertices is empty, not renamed.
            return points.get(group.index)
    # Old tables retain rest-local snapshots across renames. Resolve identity in
    # rest space, then read that group's evaluated center; never draw a stale point.
    if has_fallback and fallback_local is not None:
        from mathutils import Vector
        depsgraph = bpy.context.evaluated_depsgraph_get()
        key = (depsgraph.as_pointer(), obj.as_pointer())
        cached = _rest_lookup_cache.get(key)
        if cached is None:
            cached = (_mesh_centroids_local(obj.data), {})
            _rest_lookup_cache[key] = cached
        rest, resolved = cached
        location = tuple(fallback_local)
        if location not in resolved:
            local = Vector(location)
            candidates = [index for index, co in rest.items()
                          if (co - local).length_squared <= 1e-12]
            # Cache misses too: deleted groups must not rescan on every draw.
            resolved[location] = candidates[0] if len(candidates) == 1 else None
        return points.get(resolved[location])
    return None


@persistent
def invalidate_positions(*_args):
    from ... import overlay as mmd_overlay
    from ...general_mapping import overlay as general_overlay
    if not (_pose_cache or _rest_lookup_cache or mmd_overlay._mmd_centroids_cache
            or general_overlay._centroids_cache):
        return
    mmd_overlay.invalidate_mmd_cache()
    general_overlay.invalidate_cache()
    _pose_cache.clear()
    _rest_lookup_cache.clear()
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
    _rest_lookup_cache.clear()
