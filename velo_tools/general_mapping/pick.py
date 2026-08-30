from __future__ import annotations

import bpy
from bpy.app.handlers import persistent
from bpy_extras.view3d_utils import location_3d_to_region_2d

from . import overlay as _overlay


_pick_state = {
    'active': False,
    'hover': None,
    'drag_origin': None,
    'drag_mouse': None,
}


def get_state():
    return _pick_state


def _tag_redraw(context=None):
    ctx = context or bpy.context
    try:
        screen = getattr(ctx, "screen", None)
        if screen is None:
            return
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    except Exception:
        pass


def reset_state(context=None, *, restore_brush=True):
    _pick_state['active'] = False
    _pick_state['hover'] = None
    _pick_state['drag_origin'] = None
    _pick_state['drag_mouse'] = None
    VELO_OT_general_pick_modal._running = False
    if restore_brush and VELO_OT_general_pick_modal._weight_session and VELO_OT_general_pick_modal._brush_stash and context is not None:
        try:
            _restore_brush_state(context, VELO_OT_general_pick_modal._brush_stash)
        except Exception:
            pass
    if restore_brush:
        VELO_OT_general_pick_modal._brush_stash = None
        VELO_OT_general_pick_modal._weight_session = False
    _tag_redraw(context)


def _safe_collect(settings):
    src = settings.source_object
    tgt = settings.target_object
    profile = settings.profile
    if src is None or tgt is None or profile is None:
        return []
    try:
        from ..core.mapping.filters import is_special_vg_name
    except Exception:
        is_special_vg_name = lambda name: False

    from mathutils import Vector

    shared = getattr(bpy.context.scene, "velo_tools", None)
    threshold = max(getattr(shared, 'overlay_max_distance', 0.1), 1e-6) if shared else 0.1
    smw = src.matrix_world
    tmw = tgt.matrix_world

    endpoints = []
    claimed_src = _overlay.claimed_world_positions(settings, 'src')
    claimed_tgt = _overlay.claimed_world_positions(settings, 'tgt')

    for row_idx, row in enumerate(profile.rows):
        source_name = (getattr(row, 'source_name', '') or '').strip()
        target_name = (getattr(row, 'target_name', '') or '').strip()
        if not source_name or not target_name:
            continue
        if not (row.has_source_centroid and row.has_target_centroid):
            continue
        sw = smw @ Vector(row.source_centroid_local)
        tw = tmw @ Vector(row.target_centroid_local)
        distance = (sw - tw).length
        status = 'good' if distance <= threshold else 'bad'
        endpoints.append({
            'kind': 'src',
            'vg_name': source_name,
            'pick_vg_name': (getattr(row, 'current_source_name', '') or source_name or target_name),
            'world': sw,
            'row_idx': row_idx,
            'status': status,
        })
        endpoints.append({
            'kind': 'tgt',
            'vg_name': target_name,
            'world': tw,
            'row_idx': row_idx,
            'status': status,
        })

    tgt_centroids = _overlay.centroids_cached(tgt)
    for vg in tgt.vertex_groups:
        if is_special_vg_name(vg.name):
            continue
        world = tgt_centroids.get(vg.index)
        if world is None:
            continue
        if _overlay.world_matches_any(world, claimed_tgt):
            continue
        endpoints.append({
            'kind': 'tgt',
            'vg_name': vg.name,
            'world': world,
            'row_idx': -1,
            'status': 'unmatched',
        })

    rows_by_source = {}
    for row_idx, row in enumerate(profile.rows):
        source_name = (getattr(row, 'source_name', '') or '').strip()
        current_name = (getattr(row, 'current_source_name', '') or '').strip()
        if source_name and source_name not in rows_by_source:
            rows_by_source[source_name] = (row_idx, row)
        if current_name and current_name not in rows_by_source:
            rows_by_source[current_name] = (row_idx, row)

    src_centroids = _overlay.centroids_cached(src)
    for vg in src.vertex_groups:
        if is_special_vg_name(vg.name):
            continue
        world = src_centroids.get(vg.index)
        if world is None:
            continue
        if _overlay.world_matches_any(world, claimed_src):
            continue
        entry = rows_by_source.get(vg.name)
        row_idx = entry[0] if entry else -1
        endpoints.append({
            'kind': 'src',
            'vg_name': vg.name,
            'world': world,
            'row_idx': row_idx,
            'status': 'unmatched',
        })
    return endpoints


def _hit_test(context, mouse, endpoints, radius_px=14.0):
    region = context.region
    rv3d = context.region_data
    if not (region and rv3d) or not endpoints:
        return None
    best = None
    best_d2 = radius_px * radius_px
    for endpoint in endpoints:
        co2d = location_3d_to_region_2d(region, rv3d, endpoint['world'])
        if not co2d:
            continue
        dx = co2d.x - mouse[0]
        dy = co2d.y - mouse[1]
        d2 = dx * dx + dy * dy
        if d2 < best_d2:
            best_d2 = d2
            best = endpoint
    return best


def _target_claim_count(settings, target_name):
    profile = getattr(settings, "profile", None) if settings is not None else None
    target_name = (target_name or "").strip()
    if profile is None or not target_name:
        return 0
    return sum(1 for row in profile.rows if (row.target_name or "").strip() == target_name)


def _retarget_claimed_target(settings, old_name, new_name):
    profile = getattr(settings, "profile", None) if settings is not None else None
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()
    if profile is None or not old_name or not new_name or old_name == new_name:
        return 0
    touched = []
    from . import props as _props
    from . import operators as _ops
    prev = _props._suspend_general_row_update
    _props._suspend_general_row_update = True
    try:
        for row in profile.rows:
            if (row.target_name or "").strip() != old_name:
                continue
            row.target_name = new_name
            touched.append(row)
    finally:
        _props._suspend_general_row_update = prev
    if not touched:
        return 0
    try:
        if _ops.general_source_is_target_named(settings):
            _ops.sync_general_source_from_table(settings, save_backup=False)
        for row in touched:
            _ops.capture_general_row_snapshot(settings, row)
        _ops.autosync_general_to_text(settings)
        _ops.refresh_general_runtime(settings)
    except Exception:
        pass
    return len(touched)


def _get_brush(context):
    ts = context.tool_settings
    wp = getattr(ts, "weight_paint", None)
    return wp.brush if wp else None


def _save_brush_state(context):
    ts = context.tool_settings
    ups = getattr(ts, "unified_paint_settings", None)
    brush = _get_brush(context)
    stash = {}
    if brush is not None:
        try:
            stash['brush'] = {
                'weight': brush.weight,
                'size': brush.size,
                'strength': brush.strength,
                'blend': brush.blend,
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
    ts = context.tool_settings
    ups = getattr(ts, "unified_paint_settings", None)
    brush = _get_brush(context)
    if brush is not None:
        try:
            brush.weight = 0.0
            brush.size = 1
            brush.strength = 0.0
            brush.blend = 'SUB'
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
    brush = _get_brush(context)
    brush_state = stash.get('brush') if stash else None
    if brush is not None and brush_state:
        try:
            brush.weight = brush_state.get('weight', brush.weight)
            brush.size = brush_state.get('size', brush.size)
            brush.strength = brush_state.get('strength', brush.strength)
            brush.blend = brush_state.get('blend', brush.blend)
        except Exception:
            pass
    unified_state = stash.get('ups') if stash else None
    if ups is not None and unified_state:
        try:
            ups.size = unified_state.get('size', ups.size)
            ups.strength = unified_state.get('strength', ups.strength)
            ups.weight = unified_state.get('weight', ups.weight)
            ups.use_unified_size = unified_state.get('use_unified_size', ups.use_unified_size)
            ups.use_unified_strength = unified_state.get('use_unified_strength', ups.use_unified_strength)
            ups.use_unified_weight = unified_state.get('use_unified_weight', ups.use_unified_weight)
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
        for other in context.view_layer.objects:
            try:
                other.select_set(False)
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


class VELO_OT_general_pick_modal(bpy.types.Operator):
    bl_idname = "velo.general_pick_modal"
    bl_label = 'General mapping - endpoint picking/dragging'
    bl_options = {'INTERNAL'}

    _running = False
    _brush_stash = None
    _weight_session = False
    _CLICK_PIXEL_THRESHOLD = 5

    @classmethod
    def is_running(cls):
        return cls._running

    def invoke(self, context, event):
        if VELO_OT_general_pick_modal._running:
            return {'CANCELLED'}
        VELO_OT_general_pick_modal._running = True
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
        VELO_OT_general_pick_modal._running = False
        _pick_state['active'] = False
        _pick_state['hover'] = None
        _pick_state['drag_origin'] = None
        _pick_state['drag_mouse'] = None
        if VELO_OT_general_pick_modal._weight_session and VELO_OT_general_pick_modal._brush_stash:
            try:
                _restore_brush_state(context, VELO_OT_general_pick_modal._brush_stash)
            except Exception:
                pass
        VELO_OT_general_pick_modal._brush_stash = None
        VELO_OT_general_pick_modal._weight_session = False
        self._redraw(context)

    def modal(self, context, event):
        settings = getattr(context.scene, "velo_general_mapping", None)
        if not VELO_OT_general_pick_modal._running:
            self._exit(context)
            return {'CANCELLED'}
        overlay_on = settings is not None and getattr(settings, "show_overlay", False)
        if not overlay_on:
            self._exit(context)
            return {'CANCELLED'}

        if VELO_OT_general_pick_modal._weight_session and context.mode != 'PAINT_WEIGHT':
            try:
                _restore_brush_state(context, VELO_OT_general_pick_modal._brush_stash)
            except Exception:
                pass
            VELO_OT_general_pick_modal._brush_stash = None
            VELO_OT_general_pick_modal._weight_session = False

        area = context.area
        if area is None or area.type != 'VIEW_3D':
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            mouse = (event.mouse_region_x, event.mouse_region_y)
            endpoints = _safe_collect(settings)
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
            endpoints = _safe_collect(settings)
            hit = _hit_test(context, mouse, endpoints)
            if hit is None:
                self._press_origin = None
                self._press_mouse = None
                self._drag_active = False
                return {'PASS_THROUGH'}
            self._press_origin = hit
            self._press_mouse = mouse
            self._drag_active = hit['kind'] == 'src' or (
                hit['kind'] == 'tgt' and _target_claim_count(settings, hit.get('vg_name')) > 0
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
            current_mouse = (event.mouse_region_x, event.mouse_region_y)
            moved = (
                press_mouse is not None and
                (abs(press_mouse[0] - current_mouse[0]) + abs(press_mouse[1] - current_mouse[1])
                 > self._CLICK_PIXEL_THRESHOLD)
            )
            if was_dragging and moved:
                self._handle_drop(context, settings, origin, current_mouse)
            else:
                self._handle_click(context, settings, origin)
            self._redraw(context)
            return {'RUNNING_MODAL'}

        return {'PASS_THROUGH'}

    def _handle_click(self, context, settings, origin):
        obj = settings.source_object if origin['kind'] == 'src' else settings.target_object
        if obj is None:
            return
        pick_name = (origin.get('pick_vg_name') or origin['vg_name'])
        if VELO_OT_general_pick_modal._weight_session and context.mode == 'PAINT_WEIGHT':
            _enter_weight_paint(context, obj, pick_name)
            return
        ok = _enter_weight_paint(context, obj, pick_name)
        if not ok:
            return
        VELO_OT_general_pick_modal._brush_stash = _save_brush_state(context)
        _apply_zero_subtract_brush(context)
        VELO_OT_general_pick_modal._weight_session = True

    def _handle_drop(self, context, settings, origin, mouse):
        endpoints = _safe_collect(settings)
        drop_targets = [endpoint for endpoint in endpoints if endpoint['kind'] == 'tgt']
        hit = _hit_test(context, mouse, drop_targets)
        if origin['kind'] == 'tgt':
            if hit is None:
                return
            _retarget_claimed_target(settings, origin.get('vg_name'), hit.get('vg_name'))
            try:
                for area in context.screen.areas:
                    area.tag_redraw()
            except Exception:
                pass
            return
        if origin['kind'] != 'src':
            return
        new_name = hit['vg_name'] if hit else ""
        profile = settings.profile
        if profile is None:
            return
        row_idx = origin['row_idx']
        if not (0 <= row_idx < len(profile.rows)):
            return
        row = profile.rows[row_idx]
        if row.target_name != new_name:
            row.target_name = new_name
        if VELO_OT_general_pick_modal._weight_session and context.mode == 'PAINT_WEIGHT':
            pick_name = (getattr(row, 'current_source_name', '') or row.source_name or '').strip()
            if settings.source_object is not None and pick_name:
                _enter_weight_paint(context, settings.source_object, pick_name)
        try:
            for area in context.screen.areas:
                area.tag_redraw()
        except Exception:
            pass


def start_modal_if_needed():
    if VELO_OT_general_pick_modal.is_running():
        return
    try:
        if getattr(bpy.app, "background", False):
            return
        window = bpy.context.window
        if window is None or window.screen is None:
            return
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            for region in area.regions:
                if region.type != 'WINDOW':
                    continue
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.velo.general_pick_modal('INVOKE_DEFAULT')
                return
    except Exception:
        pass


def _restart_modal_for_saved_overlay():
    reset_state()
    scene = getattr(bpy.context, 'scene', None)
    if scene is None:
        return 0.2
    settings = getattr(scene, 'velo_general_mapping', None)
    if settings is not None and getattr(settings, 'show_overlay', False):
        start_modal_if_needed()
    return None


@persistent
def _load_post_restart_modal(_dummy):
    try:
        bpy.app.timers.register(_restart_modal_for_saved_overlay, first_interval=0.1)
    except Exception:
        pass


_classes = (VELO_OT_general_pick_modal,)


def register():
    for cls in _classes:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            pass
    if _load_post_restart_modal not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_load_post_restart_modal)


def unregister():
    if _load_post_restart_modal in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_load_post_restart_modal)
    reset_state()
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass