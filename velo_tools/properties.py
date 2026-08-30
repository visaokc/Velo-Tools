"""Properties and scene data."""

import json
import re

import bpy
from bpy.props import (
    StringProperty,
    FloatProperty,
    FloatVectorProperty,
    BoolProperty,
    IntProperty,
    EnumProperty,
    PointerProperty,
    CollectionProperty,
)


_suspend_updates = False
_suspend_history = False
_suspend_base_reload = False
_SUFFIX_RE = re.compile(r"\.\d{3}$")


# ============================================================
# Per-object mapping-table persistence (since v0.3.0_R3fix)
#
# Stores a JSON blob on base_object as the custom property "velo_local_rename_map":
#   {
#     "rows": [
#       {"src_orig": str, "current": str, "tgt_idx": int, "distance": float}
#     ],
#     "target_object": str | "",
#   }
# When base_object is switched / eyedropper-picked, s.mappings is automatically
# rebuilt from that object; on rename / revert it is written back in sync.
# ============================================================

PER_OBJ_KEY = "velo_local_rename_map"


def load_per_object_map(obj):
    if obj is None:
        return None
    raw = obj.get(PER_OBJ_KEY)
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return None


def save_per_object_map(obj, data: dict):
    if obj is None:
        return
    try:
        obj[PER_OBJ_KEY] = json.dumps(data or {}, ensure_ascii=False)
    except Exception:
        pass


def clear_per_object_map(obj):
    if obj is None:
        return
    if PER_OBJ_KEY in obj.keys():
        try:
            del obj[PER_OBJ_KEY]
        except Exception:
            pass


def _refresh_available_base_vgs(settings):
    coll = getattr(settings, "available_base_vgs", None)
    if coll is None:
        return
    coll.clear()
    names = set()
    obj = getattr(settings, "base_object", None)
    if obj is not None and obj.type == 'MESH':
        try:
            names.update(vg.name for vg in obj.vertex_groups)
        except Exception:
            pass
    for row in getattr(settings, "mappings", ()):
        for name in (
            (getattr(row, "original_name", "") or "").strip(),
            (getattr(row, "current_name", "") or "").strip(),
        ):
            if name:
                names.add(name)
    for name in sorted(names):
        item = coll.add()
        item.name = name


def _serialize_mapping_row(it) -> dict:
    return {
        "src_orig": it.original_name,
        "current_src": it.current_name,
        "target_name": it.target_name,
        "matched": bool(it.matched),
        "enabled": bool(getattr(it, "enabled", True)),
        "tgt_idx": int(it.target_vg_index),
        "distance": float(it.distance),
        # Cache centroids to avoid recomputing on object switch (performance-critical)
        "bc": list(it.base_centroid_local) if it.has_base_centroid else None,
        "tc": list(it.target_centroid_local) if it.has_target_centroid else None,
    }


def _legacy_target_name_from_row(row: dict, target):
    target_name = (row.get("target_name") or "").strip()
    if target_name:
        return target_name

    legacy_current = (row.get("current") or "").strip()
    tgt_idx = int(row.get("tgt_idx", -1))
    if target is not None and target.type == 'MESH':
        if legacy_current and target.vertex_groups.get(legacy_current) is not None:
            return legacy_current
        if 0 <= tgt_idx < len(target.vertex_groups):
            try:
                return target.vertex_groups[tgt_idx].name
            except Exception:
                pass
    return ""


def serialize_mappings_to_dict(settings) -> dict:
    rows = []
    for it in settings.mappings:
        rows.append(_serialize_mapping_row(it))
    target = settings.target_object
    base = settings.base_object
    # state: defaults to RENAMED (renamed state right after matching); set to ORIGINAL on revert
    existing = load_per_object_map(base)
    state = (existing or {}).get("state") or "RENAMED"
    return {
        "rows": rows,
        "target_object": target.name if target else "",
        "state": state,
    }


def sync_mappings_to_base_object(settings):
    """Write the current s.mappings back to the custom property on base_object."""
    base = settings.base_object
    if base is None:
        return
    save_per_object_map(base, serialize_mappings_to_dict(settings))


def set_per_object_state(obj, state: str):
    if obj is None:
        return
    data = load_per_object_map(obj) or {}
    data["state"] = state
    save_per_object_map(obj, data)


def get_per_object_state(obj):
    data = load_per_object_map(obj)
    if not data:
        return None
    return data.get("state")


def rebuild_mappings_from_per_object(settings, base):
    """Restore the s.mappings display from base's custom property (no algorithm rerun, no vertex scan)."""
    settings.mappings.clear()
    settings.unmatched_sources.clear()
    settings.unmatched_targets.clear()
    settings.rename_history.clear()
    settings.source_baselines.clear()
    settings.baseline_object_name = base.name if base else ""

    data = load_per_object_map(base) if base else None
    if not data or not isinstance(data, dict):
        return 0

    rows = data.get("rows") or []
    if not isinstance(rows, list):
        return 0

    target = settings.target_object

    global _suspend_updates
    for src_idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        orig = row.get("src_orig") or ""
        curr = (row.get("current_src") or row.get("current") or orig)
        target_name = _legacy_target_name_from_row(row, target)
        enabled = bool(row.get("enabled", True))
        tgt_idx = int(row.get("tgt_idx", -1))
        matched = bool(row.get("matched", tgt_idx >= 0))
        dist = float(row.get("distance", -1.0))
        bc = row.get("bc")
        tc = row.get("tc")
        if not orig:
            continue

        bl = settings.source_baselines.add()
        bl.source_index = src_idx
        bl.baseline_name = orig

        it = settings.mappings.add()
        it.enabled = enabled
        it.source_index = src_idx
        it.original_name = orig
        it.current_name = curr
        it.distance = dist
        it.matched = matched
        it.target_vg_index = tgt_idx
        _suspend_updates = True
        try:
            it.target_name = target_name
        finally:
            _suspend_updates = False

        if bc and len(bc) == 3:
            it.base_centroid_local = bc
            it.has_base_centroid = True
        if tc and len(tc) == 3:
            it.target_centroid_local = tc
            it.has_target_centroid = True

    return len(settings.mappings)


def _on_base_object_update(self, context):
    """Automatically rebuild the match-result panel when the source object changes."""
    global _suspend_base_reload
    if _suspend_base_reload:
        return
    s = context.scene.velo_tools
    base = s.base_object

    # Before switching away, save the current unsaved edits to the previous base
    prev_name = s.baseline_object_name
    if prev_name and (base is None or base.name != prev_name):
        prev = bpy.data.objects.get(prev_name)
        if prev and len(s.mappings) > 0:
            try:
                # Temporarily point base back to serialize
                rows = []
                for it in s.mappings:
                    rows.append(_serialize_mapping_row(it))
                old = load_per_object_map(prev) or {}
                state = old.get("state") or "RENAMED"
                save_per_object_map(prev, {"rows": rows, "state": state})
            except Exception:
                pass

    if base is None:
        s.mappings.clear()
        s.unmatched_sources.clear()
        s.unmatched_targets.clear()
        s.baseline_object_name = ""
        return
    rebuild_mappings_from_per_object(s, base)
    # Task 4: switch the mapping-table Text based on object binding (each part keeps its own mapping after material separate/join)
    try:
        bound = base.get("velo_general_text", "")
        if bound:
            tb = bpy.data.texts.get(bound)
            if tb is not None and s.active_general_text != tb:
                s.active_general_text = tb  # the update callback rebuilds mappings
    except Exception:
        pass
    try:
        from . import overlay as _ov
        _ov.invalidate_cache()
    except Exception:
        pass
    try:
        _refresh_available_base_vgs(s)
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _on_target_object_update(self, context):
    """On target-object switch, only trigger a redraw; do not clear s.mappings (keep the user's unsaved edits)."""
    try:
        from . import overlay as _ov
        _ov.invalidate_cache()
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _on_match_show_overlay_update(self, context):
    """The old general-overlay hook is disabled; the general section has moved to general_mapping.props."""
    return


def _redraw_view3d(context=None):
    """Redraw the VIEW_3D area (including the UI region) of all windows.

    Iterate via window_manager rather than context.screen -- in property update
    callbacks context.screen is sometimes None, which makes the redraw not happen
    at all. Returns the number of redrawn areas, for diagnostics.
    """
    n = 0
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return 0
    for win in wm.windows:
        screen = getattr(win, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
                n += 1
                for region in area.regions:
                    if region.type == 'UI':
                        region.tag_redraw()
    return n


def _on_active_game_update(self, context):
    """Redraw the N panel when the current game (Endfield/Wuthering) is switched.

    Issue A fix (A2, 2026-06-01, confirmed by real Blender 4.4 GUI testing): the poll of both
    game root panels was changed to only check active_tab=='GAME', so they are "always
    instantiated" when entering the GAME tab; switching active_game just swaps content on the
    already-instantiated panels per active_game (root.draw early-return + child-panel poll gating,
    see games/_a2_panels.py). So switching games only needs a plain redraw to take effect
    immediately, no longer needing a 0-delay timer to re-register the root panels -- 1.1.1's timer
    re-registration was shown to be ineffective for this issue (re-registering when the root panel
    was never instantiated still produces no instance), so it was removed along with
    _rebuild_active_game_panels."""
    _redraw_view3d()


def _on_active_tab_update(self, context):
    if getattr(self, "active_tab", None) != 'WEIGHT':
        return
    try:
        from .weights import runtime as _weight_runtime
        _weight_runtime.reset_overlay_pick_runtime(context)
    except Exception:
        pass


def strip_dup_suffix(name: str) -> str:
    if not name:
        return name
    return _SUFFIX_RE.sub("", name)


def lookup_vgroup(obj, name):
    """Try an exact match first; on failure, match once more using the suffix-stripped name."""
    if not (obj and obj.type == 'MESH' and name):
        return None
    vg = obj.vertex_groups.get(name)
    if vg:
        return vg
    base = strip_dup_suffix(name)
    if base != name:
        return obj.vertex_groups.get(base)
    return None


def _drop_by_name(coll, *names):
    """Remove all items with the given name from the collection."""
    targets = {n for n in names if n}
    for i in range(len(coll) - 1, -1, -1):
        if coll[i].name in targets:
            coll.remove(i)


def _add_unmatched_unique(coll, name, reason, obj):
    """If name is not in coll and the vertex group exists on obj, add it (and cache the centroid)."""
    if not name or coll is None:
        return
    base_name = strip_dup_suffix(name)
    for it in coll:
        if it.name == name or strip_dup_suffix(it.name) == base_name:
            return
    if not (obj and obj.type == 'MESH'):
        return
    vg = obj.vertex_groups.get(name) or obj.vertex_groups.get(base_name)
    if not vg:
        return
    # Deferred import to avoid a cycle
    from . import operators as _ops
    c = _ops.vgroup_centroid_local(obj, vg.index)
    item = coll.add()
    item.name = vg.name
    item.reason = reason
    if c is not None:
        item.centroid_local = c
        item.has_centroid = True


def _push_rename_history(settings, source_index, original_name, old_name, new_name):
    """Record the local rename history, used by "undo rename only"."""
    if not original_name or not old_name or not new_name:
        return
    if old_name == new_name:
        return
    item = settings.rename_history.add()
    item.source_index = source_index
    item.original_name = original_name
    item.old_name = old_name
    item.new_name = new_name
    # Prevent the history from growing without bound
    if len(settings.rename_history) > 300:
        settings.rename_history.remove(0)


def _on_target_name_update(self, context):
    """When the target name is edited a second time:
    - rename the vertex group and bone in sync
    - refresh the cached centroid (suffix fallback)
    - if the target centroid is located successfully -> mark as matched and clear the corresponding unmatched item
    """
    global _suspend_updates, _suspend_history
    if _suspend_updates:
        return

    new_name = (self.target_name or "").strip()

    s = context.scene.velo_tools
    base = s.base_object
    target = s.target_object
    arm = s.armature_object
    old = self.current_name or self.original_name

    from . import operators as _ops
    from . import overlay as _ov
    from .core.mapping import algorithms as _algo
    from mathutils import Vector

    if not new_name:
        old_target_vg_index = self.target_vg_index
        old_target_vg_name = ""
        if (
            old_target_vg_index >= 0
            and target
            and target.type == 'MESH'
            and old_target_vg_index < len(target.vertex_groups)
        ):
            old_target_vg_name = target.vertex_groups[old_target_vg_index].name

        if base and base.type == 'MESH' and _algo.has_vg_snapshot(base):
            final_name, _bone_n = _ops._sync_general_source_row_incremental(s, self, "")
            base_lookup_name = (final_name or self.current_name or self.original_name or "").strip()
            bg = None
            if base_lookup_name:
                bg = (base.vertex_groups.get(base_lookup_name)
                      or base.vertex_groups.get(self.current_name)
                      or base.vertex_groups.get(self.original_name))
            if bg:
                c = _ops.vgroup_centroid_local(base, bg.index)
                if c is not None:
                    self.base_centroid_local = c
                    self.has_base_centroid = True
        self.has_target_centroid = False
        self.target_vg_index = -1
        self.matched = False
        if self.original_name:
            _add_unmatched_unique(
                s.unmatched_sources,
                self.original_name,
                "手动断开映射",
                base,
            )
        if old_target_vg_index >= 0 and old_target_vg_name:
            still_claimed = False
            for it in s.mappings:
                if it == self:
                    continue
                if it.matched and it.target_vg_index == old_target_vg_index:
                    still_claimed = True
                    break
            if not still_claimed:
                _add_unmatched_unique(
                    s.unmatched_targets,
                    old_target_vg_name,
                    "原匹配被断开",
                    target,
                )
        _ops._general_autosync_to_text(s)
        _ov.invalidate_cache()
        _refresh_available_base_vgs(s)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return

    # If the source object is currently in unified-numbering state, subsequent mapping edits must:
    # only update the actual name corresponding to this single row; duplicate unified names (e.g. 36 / 36.001)
    # can be handled by non-merging suffix assignment, with no need to replay the whole table.
    if base and base.type == 'MESH' and _algo.has_vg_snapshot(base):
        final_name, _bone_n = _ops._sync_general_source_row_incremental(s, self, new_name)
        base_lookup_name = (final_name or self.current_name or self.original_name or new_name).strip()
        bg = None
        if base_lookup_name:
            bg = (base.vertex_groups.get(base_lookup_name)
                  or base.vertex_groups.get(self.current_name)
                  or base.vertex_groups.get(self.original_name)
                  or base.vertex_groups.get(new_name))
        if bg:
            c = _ops.vgroup_centroid_local(base, bg.index)
            if c is not None:
                self.base_centroid_local = c
                self.has_base_centroid = True

        found_target = False
        new_target_vg_index = -1
        new_target_vg_name = ""
        if target and target.type == 'MESH':
            tg = lookup_vgroup(target, new_name)
            if tg:
                c = _ops.vgroup_centroid_local(target, tg.index)
                if c is not None:
                    self.target_centroid_local = c
                    self.has_target_centroid = True
                    found_target = True
                    new_target_vg_index = tg.index
                    new_target_vg_name = tg.name
            if not found_target:
                self.has_target_centroid = False

        if self.has_base_centroid and self.has_target_centroid and base and target:
            bw = base.matrix_world @ Vector(self.base_centroid_local)
            tw = target.matrix_world @ Vector(self.target_centroid_local)
            self.distance = (bw - tw).length

        old_target_vg_index = self.target_vg_index
        old_target_vg_name = ""
        if (
            old_target_vg_index >= 0
            and target
            and target.type == 'MESH'
            and old_target_vg_index < len(target.vertex_groups)
        ):
            old_target_vg_name = target.vertex_groups[old_target_vg_index].name

        self.target_vg_index = new_target_vg_index
        self.matched = bool(found_target)
        if found_target:
            _drop_by_name(s.unmatched_sources, self.original_name)
            _drop_by_name(
                s.unmatched_targets,
                new_target_vg_name,
                strip_dup_suffix(new_target_vg_name),
            )
        elif self.original_name:
            _add_unmatched_unique(
                s.unmatched_sources,
                self.original_name,
                "手动改名后失配",
                base,
            )

        if (
            old_target_vg_index >= 0
            and old_target_vg_index != new_target_vg_index
            and old_target_vg_name
        ):
            still_claimed = False
            for it in s.mappings:
                if it == self:
                    continue
                if it.matched and it.target_vg_index == old_target_vg_index:
                    still_claimed = True
                    break
            if not still_claimed:
                _add_unmatched_unique(
                    s.unmatched_targets,
                    old_target_vg_name,
                    "原匹配被改走",
                    target,
                )

        _ops._general_autosync_to_text(s)
        _ov.invalidate_cache()
        _refresh_available_base_vgs(s)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return

    if new_name != old:
        old_name_for_history = old
        final_name = new_name
        if arm and arm.type == 'ARMATURE':
            bone = arm.data.bones.get(old)
            if bone:
                bone.name = new_name
                final_name = bone.name

        if base and base.type == 'MESH':
            vg = base.vertex_groups.get(old) or base.vertex_groups.get(final_name)
            if vg and vg.name != final_name:
                vg.name = final_name
                final_name = vg.name

        _suspend_updates = True
        try:
            self.current_name = final_name
            if self.target_name != final_name:
                self.target_name = final_name
        finally:
            _suspend_updates = False

        if (not _suspend_history) and final_name != old_name_for_history:
            _push_rename_history(
                s,
                self.source_index,
                self.original_name,
                old_name_for_history,
                final_name,
            )
    else:
        final_name = old

    # Base-object centroid
    if base and base.type == 'MESH':
        bg = base.vertex_groups.get(final_name)
        if bg:
            c = _ops.vgroup_centroid_local(base, bg.index)
            if c is not None:
                self.base_centroid_local = c
                self.has_base_centroid = True

    # Target centroid (suffix fallback)
    found_target = False
    new_target_vg_index = -1
    new_target_vg_name = ""
    if target and target.type == 'MESH':
        tg = lookup_vgroup(target, final_name)
        if tg:
            c = _ops.vgroup_centroid_local(target, tg.index)
            if c is not None:
                self.target_centroid_local = c
                self.has_target_centroid = True
                found_target = True
                new_target_vg_index = tg.index
                new_target_vg_name = tg.name
        if not found_target:
            self.has_target_centroid = False

    # Distance
    if self.has_base_centroid and self.has_target_centroid and base and target:
        bw = base.matrix_world @ Vector(self.base_centroid_local)
        tw = target.matrix_world @ Vector(self.target_centroid_local)
        self.distance = (bw - tw).length

    # Old target vgroup index (the real target before editing), used to detect whether it becomes orphaned
    old_target_vg_index = self.target_vg_index
    old_target_vg_name = ""
    if (
        old_target_vg_index >= 0
        and target
        and target.type == 'MESH'
        and old_target_vg_index < len(target.vertex_groups)
    ):
        old_target_vg_name = target.vertex_groups[old_target_vg_index].name

    # Write back the new target index
    self.target_vg_index = new_target_vg_index

    # Mark match state + sync the unmatched collections
    if found_target:
        self.matched = True
        _drop_by_name(s.unmatched_sources, self.original_name)
        _drop_by_name(
            s.unmatched_targets,
            new_target_vg_name,
            strip_dup_suffix(new_target_vg_name),
        )
    else:
        self.matched = False
        if self.original_name:
            _add_unmatched_unique(
                s.unmatched_sources,
                self.original_name,
                "手动改名后失配",
                base,
            )

    # If the old target becomes orphaned (no other matched row's target_vg_index equals it), add it back to unmatched targets
    if (
        old_target_vg_index >= 0
        and old_target_vg_index != new_target_vg_index
        and old_target_vg_name
    ):
        still_claimed = False
        for it in s.mappings:
            if it == self:
                continue
            if it.matched and it.target_vg_index == old_target_vg_index:
                still_claimed = True
                break
        if not still_claimed:
            _add_unmatched_unique(
                s.unmatched_targets,
                old_target_vg_name,
                "原匹配被改走",
                target,
            )

    _ov.invalidate_cache()
    _refresh_available_base_vgs(s)
    # Note: per-object persistence is synced only when matching finishes / on explicit revert / on object switch,
    # not full-serialized on every keystroke rename (performance-critical, avoids 200 rows x JSON per keystroke)
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


class VELO_CandidateItem(bpy.types.PropertyGroup):
    """A single match candidate (for the UI dropdown switch)."""
    target_vg_index: IntProperty(default=-1)
    target_name: StringProperty()
    score: FloatProperty(default=-1.0)


class VELO_AvailableVGName(bpy.types.PropertyGroup):
    pass


_suspend_shapekey_update = False


def _is_real_mesh_simple(obj):
    """Lightweight check equivalent to mesh_ops.is_real_mesh (avoids circular import)."""
    if obj is None or obj.type != 'MESH':
        return False
    if ".placeholder" in (obj.name or "").lower():
        return False
    if obj.data is not None and ".placeholder" in (obj.data.name or "").lower():
        return False
    return True


def _on_target_collection_update(self, context):
    """Scan once immediately when the target collection is set/switched (no manual refresh needed)."""
    self.shapekey_rename_unlock_from = -1
    self.shapekey_rename_unlock_end = -1
    # Deferred import to avoid a cycle
    try:
        from .mesh import shapekey_ops as _sk
    except Exception:
        return
    try:
        _sk.refresh_shapekey_list(context, force=True)
    except Exception:
        pass


def _on_shapekey_agg_name_update(self, context):
    """On rename in the aggregate panel, rename all same-named shape keys under the target collection together."""
    global _suspend_shapekey_update
    if _suspend_shapekey_update:
        return
    new_name = (self.name or "").strip()
    old_name = self.original_name
    if not new_name or not old_name or new_name == old_name:
        return

    s = context.scene.velo_tools
    coll = s.target_collection
    if coll is None:
        return

    from .mesh.shapekey_model import deform_number

    old_number = deform_number(old_name)
    new_number = deform_number(new_name)
    opened_repair_boundary = old_number is not None and new_number != old_number
    if opened_repair_boundary:
        existing_numbers = [
            number
            for item in s.shapekey_items
            if (number := deform_number(item.original_name or item.name)) is not None
        ]
        self.deform_rename_order = old_number
        current_boundary = int(s.shapekey_rename_unlock_from)
        s.shapekey_rename_unlock_from = (
            old_number
            if current_boundary < 0
            else min(current_boundary, old_number)
        )
        s.shapekey_rename_unlock_end = max(
            existing_numbers + [old_number, int(s.shapekey_rename_unlock_end)]
        )

    renamed = 0
    for obj in coll.all_objects:
        if not _is_real_mesh_simple(obj):
            continue
        sk = obj.data.shape_keys
        if not sk:
            continue
        kb = sk.key_blocks.get(old_name)
        if kb is None:
            continue
        kb.name = new_name
        renamed += 1

    _suspend_shapekey_update = True
    try:
        # When Blender auto-suffixes, sync the displayed real name (use the result of the last rename)
        self.original_name = new_name
        if self.name != new_name:
            self.name = new_name
    finally:
        _suspend_shapekey_update = False

    if opened_repair_boundary:
        from .mesh import shapekey_ops as _sk

        _sk.update_shapekey_number_locks(s)


def _on_shapekey_agg_value_update(self, context):
    """On value change in the aggregate panel, set the value of all same-named shape keys under the target collection together."""
    global _suspend_shapekey_update
    if _suspend_shapekey_update:
        return
    s = context.scene.velo_tools
    coll = s.target_collection
    if coll is None:
        return
    name = self.original_name or self.name
    if not name:
        return
    v = float(self.value)
    for obj in coll.all_objects:
        if not _is_real_mesh_simple(obj):
            continue
        sk = obj.data.shape_keys
        if not sk:
            continue
        kb = sk.key_blocks.get(name)
        if kb is None:
            continue
        if kb.value != v:
            kb.value = v


class VELO_ShapeKeyAggItem(bpy.types.PropertyGroup):
    """An aggregate entry for shape keys that appear in the target collection (only mesh.shape_keys, i.e. MMD vertex morphs)."""
    name: StringProperty(
        name='ShapeKey Name',
        description='Edit name will rename all items with the same name in the collection synchronously',
        update=_on_shapekey_agg_name_update,
    )
    original_name: StringProperty()
    selected: BoolProperty(
        name='Select auto rename',
        description='Only checked and currently unlocked ShapeKey will participate in automatic renaming.',
        default=False,
    )
    deform_rename_order: IntProperty(default=-1, options={'HIDDEN'})
    count: IntProperty(default=0, description='The number of meshes this name appears on')
    contributor_names: StringProperty(
        name='Contribution object',
        description='Contains all mesh object names of this ShapeKey.',
    )
    is_deform_numbered: BoolProperty(
        name='Deform number already exists',
        description='The prefix ShapeKey in serial number lock is protected and does not participate in automatic renaming',
        default=False,
    )
    value: FloatProperty(
        name='Value',
        default=0.0,
        min=-10.0,
        max=10.0,
        soft_min=0.0,
        soft_max=1.0,
        precision=3,
        description='Synchronize the value of all ShapeKey with the same name in the settings collection',
        update=_on_shapekey_agg_value_update,
    )


class VELO_MatchMappingItem(bpy.types.PropertyGroup):
    enabled: BoolProperty(name='Enable', default=True)
    source_index: IntProperty(name='Source Index', default=-1)
    target_vg_index: IntProperty(name='Target vertex group index', default=-1)
    original_name: StringProperty(name='Original Name')
    current_name: StringProperty(name='Current name')
    target_name: StringProperty(
        name='Target Name',
        description='Target vertex group name; direct editing can be corrected a second time, synchronize vertex groups with skeleton',
        update=_on_target_name_update,
    )
    distance: FloatProperty(name='Distance', default=-1.0)
    matched: BoolProperty(default=True)

    base_centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    target_centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    has_base_centroid: BoolProperty(default=False)
    has_target_centroid: BoolProperty(default=False)

    # Full candidate ranking (ascending by score, including the current selection); the UI dropdown filters out the current one in real time and takes the top N
    candidates: CollectionProperty(type=VELO_CandidateItem)


class VELO_UnmatchedItem(bpy.types.PropertyGroup):
    name: StringProperty()
    reason: StringProperty()
    centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    has_centroid: BoolProperty(default=False)


class VELO_RenameHistoryItem(bpy.types.PropertyGroup):
    source_index: IntProperty(default=-1)
    original_name: StringProperty()
    old_name: StringProperty()
    new_name: StringProperty()


class VELO_SourceBaselineItem(bpy.types.PropertyGroup):
    source_index: IntProperty(default=-1)
    baseline_name: StringProperty()


def _on_active_general_text_update(self, context):
    """When the mapping-table Text is switched: load that Text's content into mappings."""
    if _suspend_updates:
        return
    s = context.scene.velo_tools
    tb = s.active_general_text
    base = s.base_object
    if base is not None:
        try:
            base["velo_general_text"] = (tb.name if tb is not None else "")
        except Exception:
            pass
    if tb is None:
        return
    try:
        from . import operators as _ops
        _ops._load_general_text_into_mappings(s, tb.as_string())
        try:
            from .core.mapping import algorithms as _algo
            if base is not None and _algo.has_vg_snapshot(base):
                _ops._sync_general_source_from_table(s, save_backup=False)
        except Exception:
            pass
        _refresh_available_base_vgs(s)
    except Exception:
        pass


class VELO_ToolsSettings(bpy.types.PropertyGroup):
    base_object: PointerProperty(
        name='Source Object',
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
        update=lambda self, context: _on_base_object_update(self, context),
    )
    target_object: PointerProperty(
        name='Target Object',
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
        update=lambda self, context: _on_target_object_update(self, context),
    )
    armature_object: PointerProperty(
        name='Skeleton',
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE',
        description='Optional; if selected, renaming will be synchronized to the skeleton.',
    )

    mappings: CollectionProperty(type=VELO_MatchMappingItem)
    active_mapping_index: IntProperty(default=0)
    available_base_vgs: CollectionProperty(type=VELO_AvailableVGName)

    unmatched_sources: CollectionProperty(type=VELO_UnmatchedItem)
    unmatched_targets: CollectionProperty(type=VELO_UnmatchedItem)
    active_unmatched_source_index: IntProperty(default=0)
    active_unmatched_target_index: IntProperty(default=0)

    rename_history: CollectionProperty(type=VELO_RenameHistoryItem)
    source_baselines: CollectionProperty(type=VELO_SourceBaselineItem)
    baseline_object_name: StringProperty(default="")

    # Task 4: current mapping-table Text selector (template_ID style, consistent with the MMD section)
    active_general_text: PointerProperty(
        name='Mapping Table',
        type=bpy.types.Text,
        description='Current mapping table built-in text; click the small triangle icon to switch between multiple mapping tables',
        update=_on_active_general_text_update,
    )

    # Match options
    match_method: EnumProperty(
        name='Matching Algorithm',
        items=[
            ('SAMPLES', 'Top-K Weighted Samples', 'For each vertex group, take the top K vertices by weight for weighted point cloud distance (V0.0.5 algorithm)'),
            ('CENTROID', 'Weight Center of Gravity', 'Only compare weight centers (old algorithm, fastest speed, low distinction).'),
        ],
        default='SAMPLES',
    )
    sample_count: IntProperty(
        name='Number of samples K',
        default=8,
        min=1,
        max=64,
        description='For each vertex group, use the top K vertices by weight for matching',
    )
    use_max_distance: BoolProperty(
        name='Enable maximum distance filtering.',
        default=False,
        description='Matches with score (distance) exceeding the threshold are considered failed, original name retained',
    )
    max_match_distance: FloatProperty(
        name='Maximum match distance',
        default=0.5,
        min=0.0,
        soft_max=10.0,
    )

    # Visualization
    show_overlay: BoolProperty(
        name='Enable visualization proofreading',
        default=False,
        description='Old General Mapping Visualization Toggle; The current general mapping has been migrated to a separate panel, and this no longer automatically disables any other visualization features',
        update=lambda self, context: _on_match_show_overlay_update(self, context),
    )
    only_show_active: BoolProperty(
        name='Only display the currently selected row.',
        default=False,
        description='Only display visual connections and endpoints for the currently selected mapping row.',
    )
    overlay_max_distance: FloatProperty(
        name='Distance threshold (visualization)',
        default=0.1,
        min=0.0,
        soft_max=2.0,
        description='Threshold for determining whether the match distance is normal in visualization; exceeding the threshold will display as an abnormal color.',
    )
    show_labels: BoolProperty(
        name='Display name label',
        default=True,
        description='Display vertex group name labels next to the visual endpoint',
    )
    show_unmatched_targets: BoolProperty(
        name='Show unmatched target vertex groups',
        default=True,
        description='Show available vertex group endpoints on the target mesh that have not yet been claimed by the mapping table',
    )
    show_unmatched_sources: BoolProperty(
        name='Show unmatched source vertex groups',
        default=False,
        description='Show available vertex group endpoints on the source mesh that have not yet been claimed by the mapping table',
    )

    # ============================================================
    # Mesh / shape-key features (separate N-panel tab "Velo 网格")
    # ============================================================
    target_collection: PointerProperty(
        name='Target collection',
        type=bpy.types.Collection,
        description='ShapeKey Scan Range of Aggregate Panel',
        update=lambda self, context: _on_target_collection_update(self, context),
    )
    shapekey_items: CollectionProperty(type=VELO_ShapeKeyAggItem)
    active_shapekey_index: IntProperty(default=0)
    shapekey_rename_unlock_from: IntProperty(default=-1, options={'HIDDEN'})
    shapekey_rename_unlock_end: IntProperty(default=-1, options={'HIDDEN'})

    # Split by material: shape-key "near-zero displacement" cleanup threshold
    # Unit = object local coordinates (meters). Baking bones into shape keys often leaves residual displacement on the order of 1e-4 ~ 1e-3;
    # Blender's built-in "clean" only deletes exact zeros, so this provides an adjustable threshold as a fallback.
    shapekey_cleanup_threshold: FloatProperty(
        name='ShapeKey Cleanup Threshold',
        description=(
            'After separating by material, if the maximum displacement of all vertices of a certain ShapeKey on this submesh ≤ this threshold, consider it invalid and delete it (unit: meters, object local coordinates)'
        ),
        default=1e-4,
        min=0.0,
        soft_max=1e-2,
        precision=6,
        step=0.01,
    )
    mesh_component_prefix_id: IntProperty(
        name='Component numbering',
        description='Number used when adding Component prefix to selected objects in the grid tool',
        default=0,
        min=0,
        soft_max=999,
    )

    mesh_auto_material_on_rename: BoolProperty(
        name='Automatically synchronize material when renaming',
        description=(
            'When enabled, renaming single-material mesh objects in the current game Export Mod component set and its sub-collections will automatically sync the mesh and material names; multi-material objects are not processed'
        ),
        default=False,
    )

    # Top-of-main-panel tab switch
    active_tab: EnumProperty(
        name='Function Area',
        items=[
            ('MATCH', 'Vertex Group Tools', 'Vertex Group Name Matching / MMD Mapping / Vertex Group Operations'),
            ('MESH', 'Grid tool', 'Material / Split and Merge / ShapeKey Aggregation / Multi-Object Sculpting'),
            ('WEIGHT', 'Weight Tool', 'Weight transfer / Smooth / Limit group count'),
            ('GAME', 'Game', 'Game MOD Workflow: Arknights: Endfield(EFMI) / Wuthering Waves(WWMI)'),
        ],
        default='MATCH',
        update=_on_active_tab_update,
    )

    # Game selector (dropdown) inside the "游戏" tab: Endfield (EFMI) / Wuthering (WWMI)
    active_game: EnumProperty(
        name='Game',
        items=[
            ('ENDFIELD', 'Arknights: Endfield', 'Arknights: Endfield Mod Workflow (EFMI Tools)'),
            ('WUTHERING', 'Wuthering Waves', 'Wuthering Waves MOD Workflow (WWMITools)'),
        ],
        default='ENDFIELD',
        update=_on_active_game_update,
    )


_classes = (
    VELO_CandidateItem,
    VELO_AvailableVGName,
    VELO_ShapeKeyAggItem,
    VELO_MatchMappingItem,
    VELO_UnmatchedItem,
    VELO_RenameHistoryItem,
    VELO_SourceBaselineItem,
    VELO_ToolsSettings,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.velo_tools = PointerProperty(type=VELO_ToolsSettings)


def unregister():
    if hasattr(bpy.types.Scene, "velo_tools"):
        del bpy.types.Scene.velo_tools
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
