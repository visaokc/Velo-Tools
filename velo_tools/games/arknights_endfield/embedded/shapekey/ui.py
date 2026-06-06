"""Inject the "export custom shape keys" toggle into vendored EFMI's "Advanced"
panel, positioned between `fill_missing_mesh_data` and `allow_export_without_lods`.

Done by monkey-patching `VTEF_PT_SidePanelAdvancedExport.draw`: the wrapper
manually rebuilds the panel content and inserts our rows at the right position;
the original draw is restored on unregister.

The panel also has a live feedback area showing the count of Deform shape keys
detected in the current component collection along with any naming conflicts.
"""
import bpy
import sys
import traceback
from collections import Counter

from . import detector, props as _props


_PATCHED = {}  # id(cls) -> (cls, orig_draw)

# Blender's template_list caches the user's drag-resized row count in the
# region UI state, keyed by (listtype_name, list_id). Once the user drags the
# box bigger, lowering `maxrows` does NOT shrink that cache. To make collapse
# arrows snap the box back to the computed row count we permanently bump
# `_LIST_ID_NONCE` every time a collapse/expand happens -- a new list_id
# starts with no drag cache, so Blender is forced to render exactly `rows`
# rows. Drag is still available afterwards (against the new id) and the next
# toggle will allocate yet another fresh id, so snap is reliable on every
# click. Switching id only on toggle (not every frame) avoids the ghost /
# residual artifacts we saw when bouncing the id back and forth per draw.
_LIST_ID_NONCE = 0


def request_detector_snap():
    global _LIST_ID_NONCE
    _LIST_ID_NONCE += 1
    # Force the area containing our panel to redraw immediately so the new
    # list_id is picked up before the user moves the mouse.
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception:
        pass


def _find_advanced_export_panel():
    for name, mod in list(sys.modules.items()):
        cls = getattr(mod, "VTEF_PT_SidePanelAdvancedExport", None)
        if cls is None:
            continue
        if not isinstance(cls, type):
            continue
        if not hasattr(cls, "draw"):
            continue
        return cls
    return None


# ----------------------------------------------------- detector list synchronization
def _build_rows(coll):
    """Walk the component collection and return a sorted list of leaf tuples:
        (deform_num, component_id, object_name, sk_full_name, name_part)
    """
    rows = []
    if coll is None:
        return rows
    for obj in coll.all_objects:
        if obj.type != 'MESH':
            continue
        cid = _props.extract_component_id(obj.name)
        for k in detector.collect_deform_keys(obj):
            rows.append((
                int(k["slot"]),
                cid,
                obj.name,
                k["key_block"].name,
                k["name"],
            ))
    # Sort: by Deform number, then by Component id (unknown -> last), then obj_name.
    rows.sort(key=lambda r: (r[0], r[1] if r[1] >= 0 else 10**9, r[2]))
    return rows


def _sync_detected_items(settings, rows):
    """Rebuild settings.detected_items only when contents changed."""
    counts = Counter(r[0] for r in rows)
    plan = []
    seen_dn = set()
    for dn, cid, obj_name, sk_name, name_part in rows:
        if dn not in seen_dn:
            plan.append(("header", dn, -1, "", "", name_part, counts[dn]))
            seen_dn.add(dn)
        plan.append(("leaf", dn, cid, obj_name, sk_name, name_part, 0))

    fp = "|".join(f"{k}/{dn}/{cid}/{obj}/{sk}/{nm}/{gc}"
                  for (k, dn, cid, obj, sk, nm, gc) in plan)
    if fp == settings.detected_fingerprint:
        return False
    settings.detected_fingerprint = fp
    settings.detected_items.clear()
    for kind, dn, cid, obj_name, sk_name, name_part, gc in plan:
        it = settings.detected_items.add()
        it.item_kind = kind
        it.deform_num = dn
        it.component_id = cid
        it.object_name = obj_name
        it.sk_name = sk_name
        it.name_part = name_part
        it.group_size = gc
        if kind == "header":
            it.display = f"Deform {dn}   x{gc}"
        else:
            cid_lbl = f"C{cid}" if cid >= 0 else "C?"
            it.display = f"        {cid_lbl}  {name_part}   ({obj_name})"

    # Drop stale active index.
    if settings.detected_active_index >= len(settings.detected_items):
        # Avoid triggering jump callback for an out-of-range click.
        settings["detected_active_index"] = -1

    # Keep group_states in sync (only keep those still present).
    present = set(r[0] for r in rows)
    to_remove = [i for i, st in enumerate(settings.group_states) if st.deform_num not in present]
    for i in reversed(to_remove):
        settings.group_states.remove(i)
    return True


def refresh_detected(scene):
    """Rebuild the detector list from the active component collection.
    Safe to call from operators / depsgraph handlers (NOT from panel.draw)."""
    settings = getattr(scene, "shapekey_settings", None)
    if settings is None:
        return False
    cfg = getattr(scene, "VTEF_settings", None)
    coll = getattr(cfg, "component_collection", None) if cfg is not None else None
    rows = _build_rows(coll)
    return _sync_detected_items(settings, rows)


# ----------------------------------------------------- depsgraph handler
_DEPSGRAPH_INSTALLED = False
_TIMER_INSTALLED = False
_TIMER_INTERVAL = 0.3  # seconds; fingerprint short-circuit makes this cheap


def _on_depsgraph_update(scene, depsgraph=None):
    try:
        s = getattr(scene, "shapekey_settings", None)
        if s is None or not s.enabled:
            return
        refresh_detected(scene)
    except Exception:
        traceback.print_exc()


def _detector_timer():
    """Polled fallback for shape-key add / rename / delete which often does NOT
    fire depsgraph_update_post. Iterate every scene because bpy.context.scene
    may be None inside timer callbacks. Cheap: fingerprint guard means the
    timer only writes to RNA when contents actually changed."""
    try:
        for scene in bpy.data.scenes:
            s = getattr(scene, "shapekey_settings", None)
            if s is not None and s.enabled:
                refresh_detected(scene)
    except Exception:
        traceback.print_exc()
    return _TIMER_INTERVAL


def install_depsgraph_handler():
    global _DEPSGRAPH_INSTALLED, _TIMER_INSTALLED
    if not _DEPSGRAPH_INSTALLED:
        handlers = bpy.app.handlers.depsgraph_update_post
        if _on_depsgraph_update not in handlers:
            handlers.append(_on_depsgraph_update)
        _DEPSGRAPH_INSTALLED = True
    if not _TIMER_INSTALLED:
        try:
            if not bpy.app.timers.is_registered(_detector_timer):
                bpy.app.timers.register(
                    _detector_timer,
                    first_interval=_TIMER_INTERVAL,
                    persistent=True,
                )
        except Exception:
            traceback.print_exc()
        _TIMER_INSTALLED = True


def remove_depsgraph_handler():
    global _DEPSGRAPH_INSTALLED, _TIMER_INSTALLED
    handlers = bpy.app.handlers.depsgraph_update_post
    if _on_depsgraph_update in handlers:
        try:
            handlers.remove(_on_depsgraph_update)
        except ValueError:
            pass
    _DEPSGRAPH_INSTALLED = False
    try:
        if bpy.app.timers.is_registered(_detector_timer):
            bpy.app.timers.unregister(_detector_timer)
    except Exception:
        pass
    _TIMER_INSTALLED = False


# ----------------------------------------------------- operators
class SHAPEKEY_OT_ToggleGroup(bpy.types.Operator):
    """Toggle the expand state of a Deform-N group inside the detector list."""
    bl_idname = "shapekey.toggle_detector_group"
    bl_label = "Toggle Group"
    bl_options = {'INTERNAL'}

    deform_num: bpy.props.IntProperty(default=0)  # type: ignore

    def execute(self, context):
        s = getattr(context.scene, "shapekey_settings", None)
        if s is None:
            return {'CANCELLED'}
        st = _props.get_or_create_group_state(s, self.deform_num)
        st.expanded = not st.expanded
        request_detector_snap()
        return {'FINISHED'}


class SHAPEKEY_OT_RefreshDetected(bpy.types.Operator):
    """Rescan the active component collection for Deform shape keys."""
    bl_idname = "shapekey.refresh_detected"
    bl_label = "Refresh Detected Shape Keys"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        s = getattr(context.scene, "shapekey_settings", None)
        if s is not None:
            s.detected_fingerprint = ""
        refresh_detected(context.scene)
        return {'FINISHED'}


# ----------------------------------------------------- UIList
class SHAPEKEY_UL_Detected(bpy.types.UIList):
    """Mixed list of group headers (Deform N) and leaf rows (per component)."""

    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        # Hide the built-in filter / sort UI (toggle arrow + Az + reverse).
        # Setting it on the instance every redraw is the documented way.
        self.use_filter_show = False
        if item.item_kind == "header":
            st = _props.find_group_state(data, item.deform_num)
            expanded = bool(st.expanded) if st is not None else True
            tri_icon = 'TRIA_DOWN' if expanded else 'TRIA_RIGHT'
            row = layout.row(align=True)
            op = row.operator(
                SHAPEKEY_OT_ToggleGroup.bl_idname,
                text="", icon=tri_icon, emboss=False,
            )
            op.deform_num = item.deform_num
            row.label(text=item.display, icon='SHAPEKEY_DATA')
        else:
            row = layout.row(align=True)
            row.label(text="", icon='BLANK1')   # indent column 1
            row.label(text="", icon='BLANK1')   # indent column 2
            row.label(text=item.display, icon='OBJECT_DATAMODE')

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flt_flags = [self.bitflag_filter_item] * len(items)
        # Hide leaves whose owning Deform-N group is collapsed.
        for i, it in enumerate(items):
            if it.item_kind == "leaf":
                st = _props.find_group_state(data, it.deform_num)
                if st is not None and not st.expanded:
                    flt_flags[i] &= ~self.bitflag_filter_item
        return flt_flags, []

    def draw_filter(self, context, layout):
        """Suppress the built-in filter / sort / reverse bar entirely.
        The list is already sorted in Python and the default 'Az' button
        would scramble our header/leaf grouping."""
        return


# ----------------------------------------------------- main row drawer
def _draw_shapekey_row(layout, context):
    settings = getattr(context.scene, "shapekey_settings", None)
    if settings is None:
        return
    row = layout.row()
    row.prop(settings, "enabled")

    if not settings.enabled:
        return

    # Sub-option (indented under the main toggle).
    sub = layout.row()
    sub.separator()
    sub.prop(settings, "merge_buffers")

    # ----------- detector box (collapsible, modeled on the CrossIB sub-feature) -----------
    cfg = getattr(context.scene, "VTEF_settings", None)
    coll = getattr(cfg, "component_collection", None) if cfg is not None else None

    box = layout.box()
    head = box.row(align=True)
    head.prop(
        settings, "show_detector",
        icon=('TRIA_DOWN' if settings.show_detector else 'TRIA_RIGHT'),
        text="", emboss=False,
    )
    leaf_count = sum(1 for it in settings.detected_items if it.item_kind == "leaf")
    head.label(text=f"已识别形状键  ({leaf_count})", icon='SHAPEKEY_DATA')
    head.operator(
        SHAPEKEY_OT_RefreshDetected.bl_idname,
        text="", icon='FILE_REFRESH', emboss=False,
    )

    if not settings.show_detector:
        return

    if coll is None:
        box.label(text="未设置部件集合（Component Collection）", icon='INFO')
        return

    if not settings.detected_items:
        box.label(text="未发现命名为 'Deform <编号> <名称>' 的形状键", icon='INFO')
        box.label(text="如果你刚刚编辑了形状键，请点右侧刷新按钮",
                  icon='INFO')
        return

    # Auto-grow: rows defaults to 6 (the floor users asked for), and grows by
    # one for every additional VISIBLE entry so the whole list stays visible
    # when you expand the box. A "visible" entry is any header plus any leaf
    # whose Deform-N group header is currently expanded -- so collapsing a
    # single Deform-N group also shrinks the box. maxrows is left a notch
    # higher than rows so the built-in drag handle stays available for
    # temporary manual stretching; the next time the user toggles a
    # collapse/expand, draw() runs again and the box snaps back to the
    # computed (auto) height.
    visible_count = 0
    for it in settings.detected_items:
        if it.item_kind == "header":
            visible_count += 1
        else:
            st = _props.find_group_state(settings, it.deform_num)
            if st is None or st.expanded:
                visible_count += 1
    # V0.1.6: auto expand/shrink only applies within the 6~15 row range; beyond
    # 15 rows it no longer keeps auto-expanding -- the remaining content is
    # browsed via template_list's built-in scroll/wheel. maxrows stays at a
    # larger value so the user can still drag the divider to enlarge the view.
    AUTO_MIN = 6
    AUTO_MAX = 15
    n_rows = min(AUTO_MAX, max(AUTO_MIN, visible_count))
    n_max = max(n_rows + 4, AUTO_MAX + 5)
    list_id = f"shapekey_detected_{_LIST_ID_NONCE}"
    box.template_list(
        "SHAPEKEY_UL_Detected", list_id,
        settings, "detected_items",
        settings, "detected_active_index",
        rows=n_rows, maxrows=n_max,
        type='DEFAULT',
    )
    box.label(text="点击列表行可跳转到对应物体与形状键",
              icon='RESTRICT_SELECT_OFF')

    # Validate naming issues (across all detected leaves).
    leaf_keys = [
        {"slot": it.deform_num, "name": it.name_part,
         "raw": it.sk_name, "object": it.object_name}
        for it in settings.detected_items if it.item_kind == "leaf"
    ]

    # Per-object duplicate (slot, name): block export.
    by_obj = {}
    for k in leaf_keys:
        by_obj.setdefault(k["object"], []).append(k)
    duplicates = []
    for obj_name, ks in by_obj.items():
        for slot, name, raws in detector.validate_no_duplicate_keys(ks):
            duplicates.append((obj_name, slot, name, raws))
    if duplicates:
        warn = layout.column(align=True)
        warn.alert = True
        warn.label(text="形状键重复 — 导出将被阻止：", icon='ERROR')
        for obj_name, slot, name, raws in duplicates:
            raw_str = ", ".join(f"'{r}'" for r in raws)
            warn.label(text=f"  [{obj_name}] Deform {slot} '{name}' → {raw_str}")

    # Cross-slot name conflict: block export.
    conflicts = detector.validate_no_name_conflicts(leaf_keys)
    if conflicts:
        warn = layout.column(align=True)
        warn.alert = True
        warn.label(text="形状键命名冲突 — 导出将被阻止：", icon='ERROR')
        for name, slots in conflicts:
            warn.label(text=f"  '{name}' 被 Deform {slots} 占用")

    # Same-slot / different-name across components: block export.
    disagree = detector.validate_no_slot_name_disagreement(leaf_keys)
    if disagree:
        warn = layout.column(align=True)
        warn.alert = True
        warn.label(text="同一 Deform 编号在不同物体上对应不同名称 — 导出将被阻止：",
                   icon='ERROR')
        for slot, names in disagree:
            names_str = ", ".join(f"'{n}'" for n in names)
            warn.label(text=f"  Deform {slot} → {names_str}")


def _make_patched_draw(orig_draw):
    def patched_draw(self, context):
        cfg = context.scene.VTEF_settings
        layout = self.layout

        # Replicate vendored EFMI's advanced panel original draw logic, and
        # insert our rows between `fill_missing_mesh_data` and `allow_export_without_lods`.
        if not cfg.partial_export:
            layout.row().prop(cfg, 'add_missing_vertex_groups')
            layout.row().prop(cfg, 'fill_missing_mesh_data')
            # ─── injection start ───
            try:
                _draw_shapekey_row(layout, context)
            except Exception:
                row = layout.row()
                row.alert = True
                row.label(text="ShapeKey UI 渲染异常（详见控制台）", icon='ERROR')
                traceback.print_exc()
            # ─── injection end ───
            layout.row().prop(cfg, 'allow_export_without_lods')
            if cfg.mod_skeleton_type == 'MERGED':
                layout.row().prop(cfg, 'skeleton_scale')
    return patched_draw


def install_ui_patch():
    cls = _find_advanced_export_panel()
    if cls is None:
        print("[ShapeKey] VTEF_PT_SidePanelAdvancedExport not found — UI patch skipped.")
        return
    if id(cls) in _PATCHED:
        return
    _PATCHED[id(cls)] = (cls, cls.draw)
    cls.draw = _make_patched_draw(cls.draw)
    print("[ShapeKey] UI patch installed.")


def remove_ui_patch():
    for entry in list(_PATCHED.values()):
        cls, orig = entry
        try:
            cls.draw = orig
        except Exception:
            pass
    _PATCHED.clear()
