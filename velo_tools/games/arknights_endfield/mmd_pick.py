"""MMD 映射 - 端点拾取/拖拽交互。"""
from __future__ import annotations

import bpy
from bpy.app.handlers import persistent
from bpy_extras.view3d_utils import location_3d_to_region_2d


# ============================================================
# 公共状态（overlay.py 直接读取）
# ============================================================
_pick_state = {
    'active': False,
    'hover': None,
    'drag_origin': None,
    'drag_mouse': None,
}
_endpoint_cache = {}


def get_state():
    return _pick_state


def _tag_redraw(context=None):
    ctx = context or bpy.context
    try:
        screen = getattr(ctx, 'screen', None)
        if screen is None:
            return
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    except Exception:
        pass


def invalidate_cached_endpoints():
    _endpoint_cache.clear()


def reset_state(context=None, *, restore_brush=True):
    invalidate_cached_endpoints()
    _pick_state['active'] = False
    _pick_state['hover'] = None
    _pick_state['drag_origin'] = None
    _pick_state['drag_mouse'] = None
    VELO_OT_mmd_pick_modal._running = False
    if restore_brush and VELO_OT_mmd_pick_modal._weight_session and VELO_OT_mmd_pick_modal._brush_stash and context is not None:
        try:
            _restore_brush_state(context, VELO_OT_mmd_pick_modal._brush_stash)
        except Exception:
            pass
    if restore_brush:
        VELO_OT_mmd_pick_modal._brush_stash = None
        VELO_OT_mmd_pick_modal._weight_session = False
    _tag_redraw(context)


def _safe_collect_cache_key(ef, threshold):
    src = ef.mmd_source_object
    tgt = ef.mmd_target_object
    profile = ef.mmd_profile
    if src is None or tgt is None or profile is None:
        return None
    from ... import overlay as _ov

    row_signature = tuple(
        (
            (getattr(row, 'mmd_name', '') or '').strip(),
            (getattr(row, 'current_source_name', '') or '').strip(),
            (getattr(row, 'unified_name', '') or '').strip(),
        )
        for row in profile.rows
    )
    return (
        _ov._mmd_obj_cache_key(src, allow_live=True),
        _ov._mmd_obj_cache_key(tgt, allow_live=True),
        row_signature,
        round(float(threshold), 6),
    )


def _safe_collect(ef):
    src = ef.mmd_source_object
    tgt = ef.mmd_target_object
    profile = ef.mmd_profile
    if src is None or tgt is None or profile is None:
        return []
    try:
        from ...core.mapping.filters import is_special_vg_name
    except Exception:
        is_special_vg_name = lambda n: False  # noqa: E731

    s = getattr(bpy.context.scene, "velo_tools", None)
    threshold = max(getattr(s, "overlay_max_distance", 0.1), 1e-6) if s else 0.1

    from ... import overlay as _ov
    from mathutils import Vector

    cache_key = _safe_collect_cache_key(ef, threshold)
    if cache_key is not None:
        cached = _endpoint_cache.get(cache_key)
        if cached is not None:
            return cached

    smw = src.matrix_world
    tmw = tgt.matrix_world

    endpoints = []
    claimed_src = _ov._mmd_claimed_world_positions(ef, 'src')
    claimed_tgt = _ov._mmd_claimed_world_positions(ef, 'tgt')

    for ri, r in enumerate(profile.rows):
        if not r.mmd_name or not r.unified_name:
            continue
        sw, tw = _ov._mmd_row_world_positions(ef, r)
        if sw is None or tw is None:
            continue
        d = (sw - tw).length
        status = 'good' if d <= threshold else 'bad'
        endpoints.append({
            'kind': 'src', 'vg_name': r.mmd_name,
            'pick_vg_name': (getattr(r, 'current_source_name', '') or r.mmd_name or r.unified_name),
            'world': sw,
            'row_idx': ri, 'status': status, 'matched': True,
        })
        endpoints.append({
            'kind': 'tgt', 'vg_name': r.unified_name, 'world': tw,
            'row_idx': ri, 'status': status, 'matched': True,
        })

    tgt_c = _ov._mmd_centroids_cached(tgt, allow_live=True)
    src_c = _ov._mmd_centroids_cached(src, allow_live=True)

    # 未匹配的目标顶点组
    for vg in tgt.vertex_groups:
        if is_special_vg_name(vg.name):
            continue
        w = tgt_c.get(vg.index)
        if w is None:
            continue
        if _ov._world_matches_any(w, claimed_tgt):
            continue
        endpoints.append({
            'kind': 'tgt', 'vg_name': vg.name, 'world': w,
            'row_idx': -1, 'status': 'unmatched', 'matched': False,
        })

    # 未匹配的源顶点组
    rows_by_mmd = {}
    for i, r in enumerate(profile.rows):
        logical_name = (r.mmd_name or "").strip()
        current_name = (getattr(r, 'current_source_name', '') or "").strip()
        if logical_name and logical_name not in rows_by_mmd:
            rows_by_mmd[logical_name] = (i, r)
        if current_name and current_name not in rows_by_mmd:
            rows_by_mmd[current_name] = (i, r)
    for vg in src.vertex_groups:
        if is_special_vg_name(vg.name):
            continue
        w = src_c.get(vg.index)
        if w is None:
            continue
        if _ov._world_matches_any(w, claimed_src):
            continue
        ent = rows_by_mmd.get(vg.name)
        ri = ent[0] if ent else -1
        endpoints.append({
            'kind': 'src', 'vg_name': vg.name, 'world': w,
            'row_idx': ri, 'status': 'unmatched', 'matched': False,
        })
    if cache_key is not None:
        _endpoint_cache[cache_key] = endpoints
    return endpoints


def _hit_test(context, mouse, endpoints, radius_px=14.0):
    region = context.region
    rv3d = context.region_data
    if not (region and rv3d) or not endpoints:
        return None
    best = None
    best_d2 = radius_px * radius_px
    for ep in endpoints:
        co2d = location_3d_to_region_2d(region, rv3d, ep['world'])
        if not co2d:
            continue
        dx = co2d.x - mouse[0]
        dy = co2d.y - mouse[1]
        d2 = dx * dx + dy * dy
        if d2 < best_d2:
            best_d2 = d2
            best = ep
    return best


def _target_claim_count(ef, target_name):
    profile = getattr(ef, "mmd_profile", None) if ef is not None else None
    target_name = (target_name or "").strip()
    if profile is None or not target_name:
        return 0
    return sum(1 for row in profile.rows if (row.unified_name or "").strip() == target_name)


def _retarget_claimed_target(ef, old_name, new_name):
    profile = getattr(ef, "mmd_profile", None) if ef is not None else None
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if profile is None or not old_name or not new_name or old_name == new_name:
        return 0
    touched = []
    from . import props as _props
    from ...core.mapping import operators as _ops
    prev = _props._suspend_mmd_row_update
    _props._suspend_mmd_row_update = True
    try:
        for row in profile.rows:
            if (row.unified_name or "").strip() != old_name:
                continue
            row.unified_name = new_name
            touched.append(row)
    finally:
        _props._suspend_mmd_row_update = prev
    if not touched:
        return 0
    try:
        if _ops._mmd_source_is_unified(ef):
            _ops._sync_mmd_source_from_table(ef, save_backup=False)
        for row in touched:
            _ops._capture_mmd_row_snapshot(ef, row)
        _ops._autosync_to_text(ef)
    except Exception:
        pass
    invalidate_cached_endpoints()
    return len(touched)


def _get_brush(context):
    ts = context.tool_settings
    wp = getattr(ts, "weight_paint", None)
    return wp.brush if wp else None


def _save_brush_state(context):
    """保存笔刷 + unified_paint_settings 的上下文。"""
    ts = context.tool_settings
    ups = getattr(ts, "unified_paint_settings", None)
    b = _get_brush(context)
    stash = {}
    if b is not None:
        try:
            stash['brush'] = {
                'weight': b.weight,
                'size': b.size,
                'strength': b.strength,
                'blend': b.blend,
            }
        except Exception:
            pass
    if ups is not None:
        try:
            stash['ups'] = {
                'use_unified_size': ups.use_unified_size,
                'use_unified_strength': ups.use_unified_strength,
                'use_unified_weight': ups.use_unified_weight,
                'size': ups.size,
                'strength': ups.strength,
                'weight': ups.weight,
            }
        except Exception:
            pass
    return stash or None


def _apply_zero_subtract_brush(context):
    """同时写 brush 和 unified_paint_settings，以防被 unified 覆盖。"""
    ts = context.tool_settings
    ups = getattr(ts, "unified_paint_settings", None)
    b = _get_brush(context)
    if b is not None:
        try:
            b.weight = 0.0
            b.size = 1
            b.strength = 0.0
            b.blend = 'SUB'
        except Exception:
            pass
    if ups is not None:
        try:
            ups.size = 1
            ups.weight = 0.0
            ups.strength = 0.0
        except Exception:
            pass


def _restore_brush_state(context, stash):
    if not stash:
        return
    ts = context.tool_settings
    ups = getattr(ts, "unified_paint_settings", None)
    b = _get_brush(context)
    bs = stash.get('brush') if stash else None
    if b is not None and bs:
        try:
            b.weight = bs.get('weight', b.weight)
            b.size = bs.get('size', b.size)
            b.strength = bs.get('strength', b.strength)
            b.blend = bs.get('blend', b.blend)
        except Exception:
            pass
    us = stash.get('ups') if stash else None
    if ups is not None and us:
        try:
            ups.size = us.get('size', ups.size)
            ups.strength = us.get('strength', ups.strength)
            ups.weight = us.get('weight', ups.weight)
            ups.use_unified_size = us.get('use_unified_size', ups.use_unified_size)
            ups.use_unified_strength = us.get('use_unified_strength', ups.use_unified_strength)
            ups.use_unified_weight = us.get('use_unified_weight', ups.use_unified_weight)
        except Exception:
            pass


def _enter_weight_paint(context, obj, vg_name):
    if obj is None:
        return False
    try:
        if obj.hide_get():
            obj.hide_set(False)
        obj.hide_viewport = False
    except Exception:
        pass
    try:
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass
    try:
        for o in context.view_layer.objects:
            try:
                o.select_set(False)
            except Exception:
                pass
        obj.select_set(True)
        context.view_layer.objects.active = obj
    except Exception:
        return False
    vg = obj.vertex_groups.get(vg_name)
    if vg is not None:
        try:
            obj.vertex_groups.active_index = vg.index
        except Exception:
            pass
    try:
        bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
    except Exception:
        return False
    return True


class VELO_OT_mmd_pick_modal(bpy.types.Operator):
    bl_idname = "velo.mmd_pick_modal"
    bl_label = "MMD 映射 - 端点拾取/拖拽"
    bl_options = {'INTERNAL'}

    _running = False
    _brush_stash = None
    _weight_session = False
    _CLICK_PIXEL_THRESHOLD = 5

    @classmethod
    def is_running(cls):
        return cls._running

    def invoke(self, context, event):
        if VELO_OT_mmd_pick_modal._running:
            return {'CANCELLED'}
        VELO_OT_mmd_pick_modal._running = True
        _pick_state['active'] = True
        _pick_state['hover'] = None
        _pick_state['drag_origin'] = None
        _pick_state['drag_mouse'] = None
        self._press_origin = None
        self._press_mouse = None
        self._drag_active = False
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _redraw(self, context):
        _tag_redraw(context)

    def _exit(self, context):
        VELO_OT_mmd_pick_modal._running = False
        _pick_state['active'] = False
        _pick_state['hover'] = None
        _pick_state['drag_origin'] = None
        _pick_state['drag_mouse'] = None
        if VELO_OT_mmd_pick_modal._weight_session and VELO_OT_mmd_pick_modal._brush_stash:
            try:
                _restore_brush_state(context, VELO_OT_mmd_pick_modal._brush_stash)
            except Exception:
                pass
        VELO_OT_mmd_pick_modal._brush_stash = None
        VELO_OT_mmd_pick_modal._weight_session = False
        self._redraw(context)

    def modal(self, context, event):
        ef = getattr(context.scene, "velo_endfield", None)
        if not VELO_OT_mmd_pick_modal._running:
            self._exit(context)
            return {'CANCELLED'}
        if ef is None or not getattr(ef, "show_overlay", False):
            self._exit(context)
            return {'CANCELLED'}

        # 自动还原笔刷：用户手动退出权重模式时
        if VELO_OT_mmd_pick_modal._weight_session and context.mode != 'PAINT_WEIGHT':
            try:
                _restore_brush_state(context, VELO_OT_mmd_pick_modal._brush_stash)
            except Exception:
                pass
            VELO_OT_mmd_pick_modal._brush_stash = None
            VELO_OT_mmd_pick_modal._weight_session = False

        area = context.area
        if area is None or area.type != 'VIEW_3D':
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            mouse = (event.mouse_region_x, event.mouse_region_y)
            endpoints = _safe_collect(ef)
            if self._drag_active:
                candidates = [endpoint for endpoint in endpoints if endpoint['kind'] == 'tgt']
                _pick_state['drag_mouse'] = mouse
            else:
                candidates = endpoints
            _pick_state['hover'] = _hit_test(context, mouse, candidates)
            self._redraw(context)
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            mouse = (event.mouse_region_x, event.mouse_region_y)
            endpoints = _safe_collect(ef)
            hit = _hit_test(context, mouse, endpoints)
            if hit is None:
                self._press_origin = None
                self._press_mouse = None
                self._drag_active = False
                return {'PASS_THROUGH'}
            self._press_origin = hit
            self._press_mouse = mouse
            self._drag_active = (hit['kind'] == 'src') or (
                hit['kind'] == 'tgt' and _target_claim_count(ef, hit.get('vg_name')) > 0
            )
            if self._drag_active:
                _pick_state['drag_origin'] = hit
                _pick_state['drag_mouse'] = mouse
            self._redraw(context)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            origin = self._press_origin
            press_mouse = self._press_mouse
            was_dragging = self._drag_active
            self._press_origin = None
            self._press_mouse = None
            self._drag_active = False
            _pick_state['drag_origin'] = None
            _pick_state['drag_mouse'] = None
            if origin is None:
                return {'PASS_THROUGH'}
            cur = (event.mouse_region_x, event.mouse_region_y)
            moved = (
                press_mouse is not None and
                (abs(press_mouse[0] - cur[0]) + abs(press_mouse[1] - cur[1])
                 > self._CLICK_PIXEL_THRESHOLD)
            )
            if was_dragging and moved:
                self._handle_drop(context, origin, cur)
            else:
                self._handle_click(context, origin)
            self._redraw(context)
            return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    def _handle_click(self, context, origin):
        ef = getattr(context.scene, 'velo_endfield', None)
        if ef is None:
            return
        obj = ef.mmd_source_object if origin['kind'] == 'src' else ef.mmd_target_object
        if obj is None:
            return
        pick_vg_name = (origin.get('pick_vg_name') or origin['vg_name'])
        if VELO_OT_mmd_pick_modal._weight_session and context.mode == 'PAINT_WEIGHT':
            _enter_weight_paint(context, obj, pick_vg_name)
            return
        ok = _enter_weight_paint(context, obj, pick_vg_name)
        if not ok:
            return
        VELO_OT_mmd_pick_modal._brush_stash = _save_brush_state(context)
        _apply_zero_subtract_brush(context)
        VELO_OT_mmd_pick_modal._weight_session = True

    def _handle_drop(self, context, origin, mouse):
        ef = getattr(context.scene, 'velo_endfield', None)
        if ef is None or ef.mmd_profile is None:
            return
        endpoints = _safe_collect(ef)
        drop_targets = [endpoint for endpoint in endpoints if endpoint['kind'] == 'tgt']
        hit = _hit_test(context, mouse, drop_targets)
        if origin['kind'] == 'tgt':
            if hit is None:
                return
            _retarget_claimed_target(ef, origin.get('vg_name'), hit.get('vg_name'))
            try:
                for area in context.screen.areas:
                    area.tag_redraw()
            except Exception:
                pass
            return
        if origin['kind'] != 'src':
            return
        new_name = hit['vg_name'] if hit else ""
        profile = ef.mmd_profile
        ri = origin['row_idx']
        if 0 <= ri < len(profile.rows):
            row = profile.rows[ri]
        else:
            row = profile.rows.add()
            row.mmd_name = origin['vg_name']
            row.current_source_name = origin['vg_name']
            row.enabled = True
        if row.unified_name != new_name:
            row.unified_name = new_name
        if VELO_OT_mmd_pick_modal._weight_session and context.mode == 'PAINT_WEIGHT':
            pick_name = (getattr(row, 'current_source_name', '') or row.mmd_name or '').strip()
            if ef.mmd_source_object is not None and pick_name:
                _enter_weight_paint(context, ef.mmd_source_object, pick_name)
        try:
            for area in context.screen.areas:
                area.tag_redraw()
        except Exception:
            pass


def start_modal_if_needed():
    if VELO_OT_mmd_pick_modal.is_running():
        return
    try:
        if getattr(bpy.app, "background", False):
            return
        # 必须在 VIEW_3D 上下文调用 INVOKE，否则 modal_handler_add 失败。
        win = bpy.context.window
        if win is None or win.screen is None:
            return
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        with bpy.context.temp_override(
                            window=win, area=area, region=region,
                        ):
                            bpy.ops.velo.mmd_pick_modal('INVOKE_DEFAULT')
                        return
    except Exception:
        pass


def _restart_modal_for_saved_overlay():
    reset_state()
    scene = getattr(bpy.context, 'scene', None)
    if scene is None:
        return 0.2
    ef = getattr(scene, 'velo_endfield', None)
    if ef is not None and getattr(ef, 'show_overlay', False):
        start_modal_if_needed()
    return None


@persistent
def _load_post_restart_modal(_dummy):
    try:
        bpy.app.timers.register(_restart_modal_for_saved_overlay, first_interval=0.1)
    except Exception:
        pass


_classes = (VELO_OT_mmd_pick_modal,)


def register():
    for c in _classes:
        try:
            bpy.utils.register_class(c)
        except Exception:
            pass
    if _load_post_restart_modal not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post_restart_modal)


def unregister():
    if _load_post_restart_modal in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post_restart_modal)
    reset_state()
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
