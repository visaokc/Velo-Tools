from __future__ import annotations

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector


_handle_3d = None
_handle_2d = None
_centroids_cache = {}
_CLAIM_EPS = 1e-6


def invalidate_cache():
    _centroids_cache.clear()


def _compute_centroids_world(obj):
    out = {}
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return out
    me = obj.data
    mw = obj.matrix_world
    sums = {}
    weights = {}
    for vertex in me.vertices:
        co = vertex.co
        cox, coy, coz = co.x, co.y, co.z
        for group in vertex.groups:
            weight = group.weight
            if weight <= 0.0:
                continue
            group_index = group.group
            acc = sums.get(group_index)
            if acc is None:
                sums[group_index] = Vector((cox * weight, coy * weight, coz * weight))
                weights[group_index] = weight
            else:
                acc.x += cox * weight
                acc.y += coy * weight
                acc.z += coz * weight
                weights[group_index] += weight
    for group_index, total in weights.items():
        if total > 0.0:
            out[group_index] = mw @ (sums[group_index] / total)
    return out


def _obj_cache_key(obj):
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return None
    mw = obj.matrix_world
    return (
        obj.as_pointer(),
        obj.data.as_pointer(),
        obj.name,
        len(obj.vertex_groups),
        len(obj.data.vertices),
        tuple(mw[row][col] for row in range(4) for col in range(4)),
    )


def centroids_cached(obj):
    if obj is None:
        return {}
    key = _obj_cache_key(obj)
    if key is None:
        return {}
    cache_id = obj.as_pointer()
    cached = _centroids_cache.get(cache_id)
    if cached and cached[0] == key:
        return cached[1]
    data = _compute_centroids_world(obj)
    _centroids_cache[cache_id] = (key, data)
    return data


def world_matches_any(world, claimed_points, eps=_CLAIM_EPS):
    eps2 = float(eps) * float(eps)
    for claimed in claimed_points:
        if (world - claimed).length_squared <= eps2:
            return True
    return False


def claimed_world_positions(settings, side):
    obj = settings.source_object if side == 'src' else settings.target_object
    profile = settings.profile
    if obj is None or profile is None:
        return []
    mw = obj.matrix_world
    claimed = []
    for row in profile.rows:
        if not (row.target_name or "").strip():
            continue
        if side == 'src' and getattr(row, "has_source_centroid", False):
            claimed.append(mw @ Vector(row.source_centroid_local))
        if side == 'tgt' and getattr(row, "has_target_centroid", False):
            claimed.append(mw @ Vector(row.target_centroid_local))
    return claimed


def iter_pairs(settings):
    src = settings.source_object
    tgt = settings.target_object
    profile = settings.profile
    if src is None or tgt is None or profile is None:
        return
    smw = src.matrix_world
    tmw = tgt.matrix_world
    for row in profile.rows:
        source_name = (row.source_name or "").strip()
        target_name = (row.target_name or "").strip()
        if not source_name or not target_name:
            continue
        if not (row.has_source_centroid and row.has_target_centroid):
            continue
        sw = smw @ Vector(row.source_centroid_local)
        tw = tmw @ Vector(row.target_centroid_local)
        if source_name != target_name:
            label = f"{source_name} ({target_name})"
        else:
            label = source_name
        yield sw, tw, label, target_name


def iter_unmatched_targets(settings):
    tgt = settings.target_object
    if tgt is None:
        return
    try:
        from ..core.mapping.filters import is_special_vg_name
    except Exception:
        is_special_vg_name = lambda name: False
    claimed = claimed_world_positions(settings, 'tgt')
    centroids = centroids_cached(tgt)
    for vg in tgt.vertex_groups:
        if is_special_vg_name(vg.name):
            continue
        world = centroids.get(vg.index)
        if world is None:
            continue
        if world_matches_any(world, claimed):
            continue
        yield world, vg.name


def iter_unmatched_sources(settings):
    src = settings.source_object
    if src is None:
        return
    try:
        from ..core.mapping.filters import is_special_vg_name
    except Exception:
        is_special_vg_name = lambda name: False
    claimed = claimed_world_positions(settings, 'src')
    centroids = centroids_cached(src)
    for vg in src.vertex_groups:
        if is_special_vg_name(vg.name):
            continue
        world = centroids.get(vg.index)
        if world is None:
            continue
        if world_matches_any(world, claimed):
            continue
        yield world, vg.name


def _draw_3d():
    ctx = bpy.context
    settings = getattr(ctx.scene, "velo_general_mapping", None)
    if settings is None or not getattr(settings, "show_overlay", False):
        return
    profile = settings.profile
    if profile is None or len(profile.rows) == 0:
        return
    shared = getattr(ctx.scene, "velo_tools", None)
    threshold = max(getattr(shared, "overlay_max_distance", 0.1), 1e-6) if shared else 0.1

    good_lines, bad_lines = [], []
    good_pts_src, bad_pts_src = [], []
    good_pts_tgt, bad_pts_tgt = [], []
    for sw, tw, _label, _target_name in iter_pairs(settings):
        distance = (sw - tw).length
        src_tuple = (sw.x, sw.y, sw.z)
        tgt_tuple = (tw.x, tw.y, tw.z)
        if distance <= threshold:
            good_lines += [src_tuple, tgt_tuple]
            good_pts_src.append(src_tuple)
            good_pts_tgt.append(tgt_tuple)
        else:
            bad_lines += [src_tuple, tgt_tuple]
            bad_pts_src.append(src_tuple)
            bad_pts_tgt.append(tgt_tuple)

    unmatched_targets = []
    if shared and getattr(shared, "show_unmatched_targets", False):
        unmatched_targets = [(w.x, w.y, w.z) for w, _name in iter_unmatched_targets(settings)]
    unmatched_sources = [(w.x, w.y, w.z) for w, _name in iter_unmatched_sources(settings)]

    if not (good_lines or bad_lines or unmatched_targets or unmatched_sources):
        return

    try:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    except Exception:
        shader = gpu.shader.from_builtin('3D_UNIFORM_COLOR')

    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(2.5)
    shader.bind()

    def _draw(primitive, coords, color, point_size=10.0):
        if not coords:
            return
        gpu.state.point_size_set(point_size)
        batch = batch_for_shader(shader, primitive, {"pos": coords})
        shader.uniform_float("color", color)
        batch.draw(shader)

    good = (0.3, 0.7, 1.0, 0.95)
    bad = (1.0, 0.45, 0.85, 0.95)
    good_fade = (0.18, 0.42, 0.6, 0.6)
    bad_fade = (0.6, 0.27, 0.51, 0.6)
    unmatched_tgt = (1.0, 0.15, 0.15, 1.0)
    unmatched_src = (1.0, 0.6, 0.15, 1.0)

    _draw('LINES', good_lines, good)
    _draw('LINES', bad_lines, bad)
    _draw('POINTS', good_pts_src, good, 10.0)
    _draw('POINTS', bad_pts_src, bad, 10.0)
    _draw('POINTS', good_pts_tgt, good_fade, 10.0)
    _draw('POINTS', bad_pts_tgt, bad_fade, 10.0)
    _draw('POINTS', unmatched_targets, unmatched_tgt, 14.0)
    _draw('POINTS', unmatched_sources, unmatched_src, 14.0)

    try:
        from .pick import get_state as _get_state
        state = _get_state()
        hover = state.get('hover')
        if hover is not None:
            world = hover['world']
            color_map = {
                'good': (0.2, 1.0, 0.4, 1.0),
                'bad': (0.3, 0.7, 1.0, 1.0),
                'unmatched': (1.0, 0.2, 0.2, 1.0),
            }
            inner = color_map.get(hover.get('status', 'unmatched'), (1.0, 1.0, 1.0, 1.0))
            if hover.get('status') == 'unmatched' and hover.get('kind') == 'src':
                inner = unmatched_src
            _draw('POINTS', [(world.x, world.y, world.z)], (1.0, 1.0, 1.0, 0.95), 24.0)
            _draw('POINTS', [(world.x, world.y, world.z)], inner, 14.0)
    except Exception:
        pass

    gpu.state.point_size_set(1.0)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def _draw_2d():
    ctx = bpy.context
    settings = getattr(ctx.scene, "velo_general_mapping", None)
    shared = getattr(ctx.scene, "velo_tools", None)
    if settings is None or not getattr(settings, "show_overlay", False):
        return
    if shared is None or not getattr(shared, "show_labels", False):
        return
    profile = settings.profile
    if profile is None or len(profile.rows) == 0:
        return

    region = ctx.region
    rv3d = ctx.region_data
    if not (region and rv3d):
        return

    from bpy_extras.view3d_utils import location_3d_to_region_2d

    font_id = 0
    try:
        blf.size(font_id, 12)
    except TypeError:
        blf.size(font_id, 12, 72)

    threshold = max(shared.overlay_max_distance, 1e-6)
    for sw, tw, label, _target_name in iter_pairs(settings):
        distance = (sw - tw).length
        color = (0.7, 0.9, 1.0, 1.0) if distance <= threshold else (1.0, 0.7, 0.95, 1.0)
        co2d = location_3d_to_region_2d(region, rv3d, sw)
        if not co2d:
            continue
        blf.color(font_id, *color)
        blf.position(font_id, co2d.x + 8, co2d.y + 8, 0)
        blf.draw(font_id, label)

    if getattr(shared, "show_unmatched_targets", False):
        for world, name in iter_unmatched_targets(settings):
            co2d = location_3d_to_region_2d(region, rv3d, world)
            if not co2d:
                continue
            blf.color(font_id, 1.0, 0.4, 0.4, 1.0)
            blf.position(font_id, co2d.x + 10, co2d.y - 14, 0)
            blf.draw(font_id, name)

    for world, name in iter_unmatched_sources(settings):
        co2d = location_3d_to_region_2d(region, rv3d, world)
        if not co2d:
            continue
        blf.color(font_id, 1.0, 0.65, 0.2, 1.0)
        blf.position(font_id, co2d.x + 10, co2d.y - 14, 0)
        blf.draw(font_id, name)

    try:
        from .pick import get_state as _get_state
        state = _get_state()
        drag = state.get('drag_origin')
        mouse = state.get('drag_mouse')
        if drag is not None and mouse is not None:
            origin_2d = location_3d_to_region_2d(region, rv3d, drag['world'])
            if origin_2d is not None:
                try:
                    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
                except Exception:
                    shader = gpu.shader.from_builtin('2D_UNIFORM_COLOR')
                gpu.state.blend_set('ALPHA')
                gpu.state.line_width_set(2.5)
                shader.bind()
                coords = [
                    (origin_2d.x, origin_2d.y),
                    (float(mouse[0]), float(mouse[1])),
                ]
                batch = batch_for_shader(shader, 'LINES', {"pos": coords})
                shader.uniform_float("color", (1.0, 0.55, 0.1, 0.95))
                batch.draw(shader)
                gpu.state.line_width_set(1.0)
                gpu.state.blend_set('NONE')
    except Exception:
        pass


def register():
    global _handle_3d, _handle_2d
    if _handle_3d is None:
        _handle_3d = bpy.types.SpaceView3D.draw_handler_add(
            _draw_3d,
            (),
            'WINDOW',
            'POST_VIEW',
        )
    if _handle_2d is None:
        _handle_2d = bpy.types.SpaceView3D.draw_handler_add(
            _draw_2d,
            (),
            'WINDOW',
            'POST_PIXEL',
        )


def unregister():
    global _handle_3d, _handle_2d
    for handle_name in ("_handle_3d", "_handle_2d"):
        handle = globals().get(handle_name)
        if handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
            except Exception:
                pass
            globals()[handle_name] = None