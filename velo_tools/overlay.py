"""MMD 映射可视化 overlay。"""

import blf
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from typing import Sequence


_handle_mmd_3d = None
_handle_mmd_2d = None
_mmd_centroids_cache = {}
_CLAIM_EPS = 1e-6


def _world_matches_any(world, claimed_points, eps=_CLAIM_EPS):
    eps2 = float(eps) * float(eps)
    for claimed in claimed_points:
        if (world - claimed).length_squared <= eps2:
            return True
    return False


def _mmd_compute_centroids_world(obj):
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


def _mmd_obj_cache_key(obj, *, allow_live=False):
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return None
    mw = obj.matrix_world
    key = (
        obj.as_pointer(),
        obj.data.as_pointer(),
        obj.name,
        len(obj.vertex_groups),
        len(obj.data.vertices),
        tuple(mw[row][col] for row in range(4) for col in range(4)),
    )
    context_object = getattr(bpy.context, "object", None)
    context_mode = getattr(bpy.context, "mode", "")
    if allow_live and context_object is obj and context_mode in {'PAINT_WEIGHT', 'EDIT_MESH'}:
        # 进入权重/编辑模式后，挂起 target live 重心刷新，避免 draw handler
        # 在鼠标移动时反复全量扫描大网格而卡死；退出这些模式后 key 自然变化，
        # 会触发一次新的重心计算。
        return key + (f'{context_mode}_SUSPENDED',)
    return key


def _mmd_centroids_cached(obj, *, allow_live=False):
    if obj is None:
        return {}
    key = _mmd_obj_cache_key(obj, allow_live=allow_live)
    if key is None:
        return {}
    cached = _mmd_centroids_cache.get(key)
    if cached is not None:
        return cached
    if allow_live:
        base_key = _mmd_obj_cache_key(obj, allow_live=False)
        if base_key is not None and base_key != key:
            cached = _mmd_centroids_cache.get(base_key)
            if cached is not None:
                _mmd_centroids_cache[key] = cached
                return cached
    data = _mmd_compute_centroids_world(obj)
    _mmd_centroids_cache[key] = data
    return data


def invalidate_mmd_cache():
    _mmd_centroids_cache.clear()
    try:
        from .games.arknights_endfield import mmd_pick as _mmd_pick
        if hasattr(_mmd_pick, "invalidate_cached_endpoints"):
            _mmd_pick.invalidate_cached_endpoints()
    except Exception:
        pass


def _mmd_group_world_centroid(obj, preferred_names: Sequence[str], fallback_local=None, has_fallback=False, *, allow_live=False):
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return None
    centroids = _mmd_centroids_cached(obj, allow_live=allow_live)
    for name in preferred_names:
        name = (name or "").strip()
        if not name:
            continue
        group = obj.vertex_groups.get(name)
        if group is None:
            continue
        world = centroids.get(group.index)
        if world is not None:
            return world
    if has_fallback and fallback_local is not None:
        return obj.matrix_world @ Vector(fallback_local)
    return None


def _mmd_row_world_positions(settings, row):
    src = settings.mmd_source_object
    tgt = settings.mmd_target_object
    if src is None or tgt is None:
        return None, None
    source_world = _mmd_group_world_centroid(
        src,
        [getattr(row, "current_source_name", ""), row.mmd_name, row.unified_name],
        fallback_local=getattr(row, "mmd_centroid_local", None),
        has_fallback=getattr(row, "has_mmd_centroid", False),
        allow_live=True,
    )
    target_world = _mmd_group_world_centroid(
        tgt,
        [row.unified_name, row.mmd_name],
        fallback_local=getattr(row, "unified_centroid_local", None),
        has_fallback=getattr(row, "has_unified_centroid", False),
        allow_live=True,
    )
    return source_world, target_world


def _mmd_claimed_world_positions(settings, side):
    profile = settings.mmd_profile
    if profile is None:
        return []
    claimed = []
    for row in profile.rows:
        if not (row.unified_name or "").strip():
            continue
        source_world, target_world = _mmd_row_world_positions(settings, row)
        if side == 'src' and source_world is not None:
            claimed.append(source_world)
        if side == 'tgt' and target_world is not None:
            claimed.append(target_world)
    return claimed


def _mmd_iter_pairs(settings):
    src = settings.mmd_source_object
    tgt = settings.mmd_target_object
    profile = settings.mmd_profile
    if src is None or tgt is None or profile is None:
        return
    for row in profile.rows:
        if not row.mmd_name or not row.unified_name:
            continue
        sw, tw = _mmd_row_world_positions(settings, row)
        if sw is None or tw is None:
            continue
        if row.mmd_name != row.unified_name:
            label = f"{row.mmd_name} ({row.unified_name})"
        else:
            label = row.mmd_name
        yield sw, tw, label, row.unified_name


def _mmd_iter_unmatched_targets(settings):
    tgt = settings.mmd_target_object
    profile = settings.mmd_profile
    if tgt is None or profile is None:
        return
    try:
        from .core.mapping.filters import is_special_vg_name
    except Exception:
        is_special_vg_name = lambda name: False
    claimed = _mmd_claimed_world_positions(settings, 'tgt')
    tgt_centroids = _mmd_centroids_cached(tgt, allow_live=True)
    for vg in tgt.vertex_groups:
        if is_special_vg_name(vg.name):
            continue
        world = tgt_centroids.get(vg.index)
        if world is None:
            continue
        if _world_matches_any(world, claimed):
            continue
        yield world, vg.name


def _mmd_iter_unmatched_sources(settings):
    src = settings.mmd_source_object
    if src is None:
        return
    try:
        from .core.mapping.filters import is_special_vg_name
    except Exception:
        is_special_vg_name = lambda name: False
    claimed = _mmd_claimed_world_positions(settings, 'src')
    src_centroids = _mmd_centroids_cached(src, allow_live=True)
    for vg in src.vertex_groups:
        if is_special_vg_name(vg.name):
            continue
        world = src_centroids.get(vg.index)
        if world is None:
            continue
        if _world_matches_any(world, claimed):
            continue
        yield world, vg.name


def _draw_mmd_3d():
    ctx = bpy.context
    ef = getattr(ctx.scene, "velo_endfield", None)
    if ef is None or not getattr(ef, "show_overlay", False):
        return
    # V0.1.6: 表为空时不画任何内容（避免源顶点组 / 未匹配点 / 标签残留）
    profile = ef.mmd_profile
    if profile is None or len(profile.rows) == 0:
        return
    s = getattr(ctx.scene, "velo_tools", None)
    threshold = max(getattr(s, "overlay_max_distance", 0.1), 1e-6) if s else 0.1

    good_lines, bad_lines = [], []
    good_pts_b, bad_pts_b = [], []
    good_pts_t, bad_pts_t = [], []
    for bw, tw, _n1, _n2 in _mmd_iter_pairs(ef):
        d = (bw - tw).length
        bt = (bw.x, bw.y, bw.z); tt = (tw.x, tw.y, tw.z)
        if d <= threshold:
            good_lines += [bt, tt]; good_pts_b.append(bt); good_pts_t.append(tt)
        else:
            bad_lines += [bt, tt]; bad_pts_b.append(bt); bad_pts_t.append(tt)

    unmatched_pts = []
    if s and getattr(s, "show_unmatched_targets", False):
        unmatched_pts = [
            (w.x, w.y, w.z) for w, _n in _mmd_iter_unmatched_targets(ef)
        ]

    UNMATCHED_S = (1.0, 0.6, 0.15, 1.0)
    unmatched_src_pts = [
        (w.x, w.y, w.z) for w, _n in _mmd_iter_unmatched_sources(ef)
    ]

    if not (good_lines or bad_lines or unmatched_pts or unmatched_src_pts):
        return

    try:
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    except Exception:
        shader = gpu.shader.from_builtin('3D_UNIFORM_COLOR')

    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(2.5)
    shader.bind()

    def _draw(prim, coords, color, point_size=10.0):
        if not coords:
            return
        gpu.state.point_size_set(point_size)
        batch = batch_for_shader(shader, prim, {"pos": coords})
        shader.uniform_float("color", color)
        batch.draw(shader)

    GOOD = (0.3, 0.7, 1.0, 0.95)   # MMD 用蓝色系区分
    BAD = (1.0, 0.45, 0.85, 0.95)
    GOOD_FADE = (0.18, 0.42, 0.6, 0.6)
    BAD_FADE = (0.6, 0.27, 0.51, 0.6)
    UNMATCHED_T = (1.0, 0.15, 0.15, 1.0)
    _draw('LINES', good_lines, GOOD)
    _draw('LINES', bad_lines, BAD)
    _draw('POINTS', good_pts_b, GOOD, 10.0)
    _draw('POINTS', bad_pts_b, BAD, 10.0)
    _draw('POINTS', good_pts_t, GOOD_FADE, 10.0)
    _draw('POINTS', bad_pts_t, BAD_FADE, 10.0)

    # 未匹配的目标 VG（红色大点）
    _draw('POINTS', unmatched_pts, UNMATCHED_T, 14.0)

    # V0.1.6: 未匹配的源 VG（橙色大点），缺省总是显示，避免用户找不到漏匹配的源骨
    _draw('POINTS', unmatched_src_pts, UNMATCHED_S, 14.0)

    # V0.1.6: hover 高亮——外层白色光环 + 内核原状态色
    try:
        from .games.arknights_endfield.mmd_pick import get_state as _ps
        st = _ps()
        hov = st.get('hover')
        if hov is not None:
            w = hov['world']
            color_map = {
                'good': (0.2, 1.0, 0.4, 1.0),
                'bad':  (0.3, 0.7, 1.0, 1.0),
                'unmatched': (1.0, 0.2, 0.2, 1.0),
            }
            inner_col = color_map.get(hov.get('status', 'unmatched'),
                                      (1.0, 1.0, 1.0, 1.0))
            # 先画外层白色光环（大点），再画内核色点覆盖中心，
            # 可视效果：中心色块 + 四周一圈白环。
            _draw('POINTS', [(w.x, w.y, w.z)], (1.0, 1.0, 1.0, 0.95), 24.0)
            _draw('POINTS', [(w.x, w.y, w.z)], inner_col, 14.0)
    except Exception:
        pass

    gpu.state.point_size_set(1.0)
    gpu.state.line_width_set(1.0)
    gpu.state.blend_set('NONE')


def _draw_mmd_2d():
    ctx = bpy.context
    ef = getattr(ctx.scene, "velo_endfield", None)
    if ef is None or not getattr(ef, "show_overlay", False):
        return
    # V0.1.6: 表为空时不画标签（避免数字编号残留）
    profile = ef.mmd_profile
    if profile is None or len(profile.rows) == 0:
        return
    s = getattr(ctx.scene, "velo_tools", None)
    if not s or not s.show_labels:
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
    threshold = max(s.overlay_max_distance, 1e-6)
    for bw, tw, label, _u in _mmd_iter_pairs(ef):
        d = (bw - tw).length
        col = (0.7, 0.9, 1.0, 1.0) if d <= threshold else (1.0, 0.7, 0.95, 1.0)
        co2d = location_3d_to_region_2d(region, rv3d, bw)
        if not co2d:
            continue
        blf.color(font_id, *col)
        blf.position(font_id, co2d.x + 8, co2d.y + 8, 0)
        blf.draw(font_id, label)

    if getattr(s, "show_unmatched_targets", False):
        for tw, name in _mmd_iter_unmatched_targets(ef):
            co2d = location_3d_to_region_2d(region, rv3d, tw)
            if not co2d:
                continue
            blf.color(font_id, 1.0, 0.4, 0.4, 1.0)
            blf.position(font_id, co2d.x + 10, co2d.y - 14, 0)
            blf.draw(font_id, name)

    # V0.1.5: 未匹配的源 VG 标签（橙色），用于显示「有权重但表里没行/无目标」的源骨
    for sw, name in _mmd_iter_unmatched_sources(ef):
        co2d = location_3d_to_region_2d(region, rv3d, sw)
        if not co2d:
            continue
        blf.color(font_id, 1.0, 0.65, 0.2, 1.0)
        blf.position(font_id, co2d.x + 10, co2d.y - 14, 0)
        blf.draw(font_id, name)

    # V0.1.6: 拖拽中：从拖起点（源端点）画到鼠标
    try:
        from .games.arknights_endfield.mmd_pick import get_state as _get_state
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
    global _handle_mmd_3d, _handle_mmd_2d
    if _handle_mmd_3d is None:
        _handle_mmd_3d = bpy.types.SpaceView3D.draw_handler_add(
            _draw_mmd_3d, (), 'WINDOW', 'POST_VIEW'
        )
    if _handle_mmd_2d is None:
        _handle_mmd_2d = bpy.types.SpaceView3D.draw_handler_add(
            _draw_mmd_2d, (), 'WINDOW', 'POST_PIXEL'
        )


def unregister():
    global _handle_mmd_3d, _handle_mmd_2d
    for var_name in ("_handle_mmd_3d", "_handle_mmd_2d"):
        h = globals().get(var_name)
        if h is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
            except Exception:
                pass
            globals()[var_name] = None
