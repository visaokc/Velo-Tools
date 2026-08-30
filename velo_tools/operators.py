"""Shared mesh utility functions.

The general mapping operators have been moved to the general_mapping module; this file only keeps pure functions reused by other modules.
"""

from __future__ import annotations

from velo_tools.i18n import iface_

import json

import bpy
from bpy.props import BoolProperty, IntProperty
from mathutils import Vector

from . import properties as _props


def is_real_mesh(obj) -> bool:
    if obj is None or obj.type != 'MESH':
        return False
    if ".placeholder" in (obj.name or "").lower():
        return False
    if obj.data is not None and ".placeholder" in (obj.data.name or "").lower():
        return False
    return True


def vgroup_centroid_local(obj, vg_index: int):
    if obj is None or obj.type != 'MESH' or vg_index < 0:
        return None
    me = obj.data
    if me is None or len(me.vertices) == 0:
        return None
    acc = Vector((0.0, 0.0, 0.0))
    total_weight = 0.0
    for vertex in me.vertices:
        for group in vertex.groups:
            if group.group == vg_index and group.weight > 0.0:
                acc += vertex.co * group.weight
                total_weight += group.weight
                break
    if total_weight <= 0.0:
        return None
    return acc / total_weight


def compute_all_centroids_local(obj, only_indices=None):
    out = {}
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return out
    sums = {}
    weights = {}
    for vertex in obj.data.vertices:
        co = vertex.co
        cox, coy, coz = co.x, co.y, co.z
        for group in vertex.groups:
            group_index = group.group
            if only_indices is not None and group_index not in only_indices:
                continue
            weight = group.weight
            if weight <= 0.0:
                continue
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
            out[group_index] = sums[group_index] / total
    return out


def compute_all_top_samples(obj, k: int, only_indices=None):
    out = {}
    if obj is None or obj.type != 'MESH' or obj.data is None or k <= 0:
        return out
    rows = {}
    for vertex in obj.data.vertices:
        co = vertex.co
        for group in vertex.groups:
            group_index = group.group
            if only_indices is not None and group_index not in only_indices:
                continue
            weight = group.weight
            if weight <= 0.0:
                continue
            rows.setdefault(group_index, []).append((weight, co.copy()))
    for group_index, samples in rows.items():
        samples.sort(key=lambda item: item[0], reverse=True)
        out[group_index] = [(co, weight) for weight, co in samples[:k]]
    return out


def register():
    return


def unregister():
    return


# ============================================================
# Row-level helper operators
# ============================================================

class VELO_OT_focus_mapping(bpy.types.Operator):
    bl_idname = "velo.focus_mapping"
    bl_label = 'Focus Row'
    bl_options = {'REGISTER'}

    index: IntProperty(default=-1)

    def execute(self, context):
        s = context.scene.velo_tools
        if 0 <= self.index < len(s.mappings):
            s.active_mapping_index = self.index
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return {'FINISHED'}


class VELO_OT_revert_item(bpy.types.Operator):
    bl_idname = "velo.revert_item"
    bl_label = 'Restore Target Name for This Row'
    bl_description = 'Restore the target name to the original name (synchronize vertex groups and bones)'
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1)

    def execute(self, context):
        s = context.scene.velo_tools
        if not (0 <= self.index < len(s.mappings)):
            return {'CANCELLED'}
        it = s.mappings[self.index]
        if it.original_name and it.target_name != it.original_name:
            it.target_name = it.original_name
        return {'FINISHED'}


class VELO_OT_undo_last_rename(bpy.types.Operator):
    bl_idname = "velo.undo_last_rename"
    bl_label = 'Undo last rename'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.velo_tools
        if len(s.rename_history) == 0:
            self.report({'INFO'}, iface_('Irreversible Rename'))
            return {'CANCELLED'}
        last = s.rename_history[-1]
        # Find the corresponding mapping row
        target_item = None
        for it in s.mappings:
            if it.source_index == last.source_index:
                target_item = it
                break
        if target_item is None:
            s.rename_history.remove(len(s.rename_history) - 1)
            return {'CANCELLED'}
        # Trigger the rename (no longer write to history, to avoid loops)
        _props._suspend_history = True
        try:
            target_item.target_name = last.old_name
        finally:
            _props._suspend_history = False
        s.rename_history.remove(len(s.rename_history) - 1)
        return {'FINISHED'}


class VELO_OT_clear_mappings(bpy.types.Operator):
    bl_idname = "velo.clear_mappings"
    bl_label = 'Clear Mapping Table'
    bl_description = 'Clear Current Match Results (Renaming Will Not Be Rolled Back)'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.velo_tools
        s.mappings.clear()
        s.unmatched_sources.clear()
        s.unmatched_targets.clear()
        s.rename_history.clear()
        s.source_baselines.clear()
        s.baseline_object_name = ""
        # Sync to the built-in Text (consistent with the MMD section)
        try:
            _general_autosync_to_text(s)
        except Exception:
            pass
        return {'FINISHED'}


class VELO_OT_show_candidates(bpy.types.Operator):
    bl_idname = "velo.show_candidates"
    bl_label = 'View candidates'
    bl_options = {'REGISTER'}

    index: IntProperty(default=-1)

    def invoke(self, context, event):
        s = context.scene.velo_tools
        if not (0 <= self.index < len(s.mappings)):
            return {'CANCELLED'}
        s.active_mapping_index = self.index
        return context.window_manager.invoke_popup(self, width=380)

    def execute(self, context):
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        s = context.scene.velo_tools
        if not (0 <= self.index < len(s.mappings)):
            layout.label(text='(Invalid item)')
            return
        it = s.mappings[self.index]
        layout.label(text=iface_('Source: {0}').format(it.original_name), icon='GROUP_VERTEX')
        layout.label(text=iface_('Current target: {0}').format(it.target_name))
        layout.separator()
        layout.label(text='Candidates (in ascending order of distance):')
        if len(it.candidates) == 0:
            layout.label(text='(None)')
            return
        for i, c in enumerate(it.candidates):
            row = layout.row(align=True)
            mark = '●' if c.target_name == it.target_name else '○'
            row.label(text=f"{mark} {c.target_name}  ({c.score:.4f})")
            op = row.operator("velo.apply_candidate", text='Apply')
            op.index = self.index
            op.candidate_index = i


class VELO_OT_apply_candidate(bpy.types.Operator):
    bl_idname = "velo.apply_candidate"
    bl_label = 'Apply Candidate'
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1)
    candidate_index: IntProperty(default=-1)

    def execute(self, context):
        s = context.scene.velo_tools
        if not (0 <= self.index < len(s.mappings)):
            return {'CANCELLED'}
        it = s.mappings[self.index]
        if not (0 <= self.candidate_index < len(it.candidates)):
            return {'CANCELLED'}
        c = it.candidates[self.candidate_index]
        if c.target_name and c.target_name != it.target_name:
            it.target_name = c.target_name  # trigger update
        return {'FINISHED'}


# ============================================================
# Reversible toggle: ORIGINAL <-> RENAMED (based on the persisted per-object mapping table)
# ============================================================

def _apply_rename_dir(base, armature, direction: str):
    """Switch the naming direction according to the persisted mapping on base.

    direction='TO_RENAMED': rename src_orig to current
    direction='TO_ORIGINAL': rename current back to src_orig
    Returns the renamed count. The mapping table itself is **not deleted**.
    """
    data = _props.load_per_object_map(base)
    if not data:
        return 0
    rows = data.get("rows") or []
    n = 0
    for row in rows:
        orig = row.get("src_orig") or ""
        curr = row.get("current") or ""
        if not orig or not curr or orig == curr:
            continue
        if direction == 'TO_RENAMED':
            from_name, to_name = orig, curr
        else:
            from_name, to_name = curr, orig
        if base and base.type == 'MESH':
            vg = base.vertex_groups.get(from_name)
            if vg:
                vg.name = to_name
                n += 1
        if armature and armature.type == 'ARMATURE':
            bone = armature.data.bones.get(from_name)
            if bone:
                bone.name = to_name
    # Update the state marker
    new_state = 'RENAMED' if direction == 'TO_RENAMED' else 'ORIGINAL'
    data["state"] = new_state
    _props.save_per_object_map(base, data)
    return n


class VELO_OT_toggle_naming_this_object(bpy.types.Operator):
    bl_idname = "velo.toggle_naming_this_object"
    bl_label = 'Toggle naming for this object (original name ↔ renamed)'
    bl_description = 'Toggle back and forth between the original name and the renamed one; the mapping table will not be cleared and can go back and forth infinitely'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.velo_tools
        base = s.base_object
        if base is None:
            self.report({'ERROR'}, iface_('Original object not specified'))
            return {'CANCELLED'}
        state = _props.get_per_object_state(base)
        if state is None:
            self.report({'ERROR'}, iface_('This object has no persistent mapping; please perform matching first'))
            return {'CANCELLED'}
        direction = 'TO_ORIGINAL' if state == 'RENAMED' else 'TO_RENAMED'
        n = _apply_rename_dir(base, s.armature_object, direction)
        # Rebuild the UI display
        _props._on_base_object_update(s, context)
        new_state = _props.get_per_object_state(base) or "?"
        self.report({'INFO'}, iface_('Switched {0} vertex groups → Status: {1}').format(n, new_state))
        return {'FINISHED'}


class VELO_OT_toggle_naming_all_objects(bpy.types.Operator):
    bl_idname = "velo.toggle_naming_all_objects"
    bl_label = 'Toggle all object naming'
    bl_description = 'For all objects with persistent mappings, switch to reverse naming based on their current state; the mapping table is fully retained'
    bl_options = {'REGISTER', 'UNDO'}

    target_state: bpy.props.EnumProperty(
        name='Target state',
        items=[
            ('AUTO', 'Invert each', 'Flip each object according to its current state'),
            ('ORIGINAL', 'Revert all to original names', 'Force all to ORIGINAL'),
            ('RENAMED', 'Rename all', 'Force all to RENAMED'),
        ],
        default='AUTO',
    )

    def execute(self, context):
        s = context.scene.velo_tools
        arm = s.armature_object
        n_objs = n_renamed = 0
        for obj in bpy.data.objects:
            if obj.type != 'MESH' or _props.PER_OBJ_KEY not in obj.keys():
                continue
            state = _props.get_per_object_state(obj) or 'RENAMED'
            if self.target_state == 'AUTO':
                direction = 'TO_ORIGINAL' if state == 'RENAMED' else 'TO_RENAMED'
            elif self.target_state == 'ORIGINAL':
                if state == 'ORIGINAL':
                    continue
                direction = 'TO_ORIGINAL'
            else:
                if state == 'RENAMED':
                    continue
                direction = 'TO_RENAMED'
            n_renamed += _apply_rename_dir(obj, arm, direction)
            n_objs += 1
        _props._on_base_object_update(s, context)
        self.report({'INFO'}, iface_('Processed {0} objects, {1} vertex groups').format(n_objs, n_renamed))
        return {'FINISHED'}


class VELO_OT_clear_per_object_map(bpy.types.Operator):
    bl_idname = "velo.clear_per_object_map"
    bl_label = "Clear This Object's Mapping Table"
    bl_description = 'Thoroughly clear the persistent map on this object (vertex groups will not be renamed; subsequent toggle buttons will be disabled)'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.velo_tools
        base = s.base_object
        if base is None:
            return {'CANCELLED'}
        _props.clear_per_object_map(base)
        _props._on_base_object_update(s, context)
        self.report({'INFO'}, iface_('Cleared'))
        return {'FINISHED'}


# ============================================================
# Mapping table import / export for the general vertex-group rename Tab (V0.1.2)
# ============================================================

GENERAL_INTERNAL_TEXT_NAME = "velo_general_mapping.txt"
GENERAL_TEXT_META_KEY = "velo_general_overlay_meta"


def _general_serialize_to_text(s) -> str:
    lines = ["# velo_tools 通用顶点组匹配表", "# 格式: <原名>\\t<目标名>"]
    for it in s.mappings:
        src = (it.original_name or "").strip()
        tgt = (it.target_name or it.current_name or "").strip()
        if not src or not tgt:
            continue
        lines.append(f"{src}\t{tgt}")
    return "\n".join(lines) + "\n"


def _general_row_meta_key(src: str, tgt: str) -> str:
    return f"{(src or '').strip()}\t{(tgt or '').strip()}"


def _general_capture_row_snapshot(s, it):
    base = s.base_object
    target = s.target_object
    src = (getattr(it, "original_name", "") or "").strip()
    cur = (getattr(it, "current_name", "") or "").strip()
    tgt = (it.target_name or "").strip()

    if base is not None and src:
        bg = base.vertex_groups.get(src) or base.vertex_groups.get(cur) or base.vertex_groups.get(tgt)
        if bg is not None:
            bc = vgroup_centroid_local(base, bg.index)
            if bc is not None:
                it.base_centroid_local = bc
                it.has_base_centroid = True

    if target is not None and tgt:
        tg = target.vertex_groups.get(tgt) or target.vertex_groups.get(src) or target.vertex_groups.get(cur)
        if tg is not None:
            tc = vgroup_centroid_local(target, tg.index)
            if tc is not None:
                it.target_centroid_local = tc
                it.has_target_centroid = True
                it.target_vg_index = tg.index

    if it.has_base_centroid and it.has_target_centroid and base is not None and target is not None:
        bw = base.matrix_world @ Vector(it.base_centroid_local)
        tw = target.matrix_world @ Vector(it.target_centroid_local)
        it.distance = (bw - tw).length
        it.matched = True
    elif not tgt:
        it.matched = False


def _general_write_text_meta(tb, s):
    if tb is None:
        return
    rows = []
    for it in s.mappings:
        rows.append({
            "src": (getattr(it, "original_name", "") or "").strip(),
            "tgt": (it.target_name or "").strip(),
            "bc": list(it.base_centroid_local) if getattr(it, "has_base_centroid", False) else None,
            "tc": list(it.target_centroid_local) if getattr(it, "has_target_centroid", False) else None,
        })
    try:
        tb[GENERAL_TEXT_META_KEY] = json.dumps({"rows": rows}, ensure_ascii=False)
    except Exception:
        pass


def _general_apply_text_meta(s, tb):
    meta_rows = {}
    if tb is not None:
        try:
            raw = tb.get(GENERAL_TEXT_META_KEY, "")
            payload = json.loads(str(raw)) if raw else {}
            for row in payload.get("rows", []):
                key = _general_row_meta_key(row.get("src", ""), row.get("tgt", ""))
                if key:
                    meta_rows[key] = row
        except Exception:
            meta_rows = {}

    base = s.base_object
    target = s.target_object
    for it in s.mappings:
        it.has_base_centroid = False
        it.has_target_centroid = False
        row = meta_rows.get(_general_row_meta_key(it.original_name, it.target_name))
        if row is not None:
            bc = row.get("bc")
            tc = row.get("tc")
            if bc and len(bc) == 3:
                it.base_centroid_local = bc
                it.has_base_centroid = True
            if tc and len(tc) == 3:
                it.target_centroid_local = tc
                it.has_target_centroid = True
        if not (it.has_base_centroid and it.has_target_centroid):
            _general_capture_row_snapshot(s, it)
        if it.has_base_centroid and it.has_target_centroid and base is not None and target is not None:
            bw = base.matrix_world @ Vector(it.base_centroid_local)
            tw = target.matrix_world @ Vector(it.target_centroid_local)
            it.distance = (bw - tw).length
            it.matched = True


def _ensure_general_text(s):
    """Ensure s.active_general_text is valid; if not, create and bind one named after the current base_object."""
    tb = s.active_general_text
    if tb is not None:
        return tb
    base = s.base_object
    base_part = (base.name if base is not None else "default")
    name = f"velo_general_{base_part}.txt"
    tb = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    from . import properties as _props
    prev = _props._suspend_updates
    _props._suspend_updates = True
    try:
        s.active_general_text = tb
    finally:
        _props._suspend_updates = prev
    if base is not None:
        try:
            base["velo_general_text"] = tb.name
        except Exception:
            pass
    return tb


def _general_autosync_to_text(s):
    """After a match / row operation, auto-sync to the current mapping table Text; if not bound, create one automatically."""
    try:
        tb = _ensure_general_text(s)
        if tb is None:
            return
        tb.from_string(_general_serialize_to_text(s))
        _general_write_text_meta(tb, s)
    except Exception:
        pass


def _general_parse_text(raw: str):
    pairs = []
    for ln in (raw or "").splitlines():
        line = ln.split('#', 1)[0].strip()
        if not line:
            continue
        if line.startswith('@'):  # skip @mod / @component / @profile and other header lines
            continue
        if '\t' in line:
            parts = line.split('\t', 1)
        else:
            parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        a, b = parts[0].strip(), parts[1].strip()
        if a and b:
            pairs.append((a, b))
    return pairs


def _load_general_text_into_mappings(s, raw: str):
    """Called when switching the mapping table Text: overwrite mappings with the Text content."""
    pairs = _general_parse_text(raw)
    from . import properties as _props
    prev = _props._suspend_updates
    _props._suspend_updates = True
    try:
        s.mappings.clear()
        for src, tgt in pairs:
            it = s.mappings.add()
            it.enabled = True
            it.original_name = src
            it.current_name = src
            it.target_name = tgt
            it.matched = bool(tgt)
        s.active_mapping_index = 0
    finally:
        _props._suspend_updates = prev
    try:
        _general_apply_text_meta(s, s.active_general_text)
        try:
            from .core.mapping import algorithms as _algo
            if s.base_object is not None and _algo.has_vg_snapshot(s.base_object):
                _sync_general_source_from_table(s, save_backup=False)
        except Exception:
            pass
        _general_write_text_meta(s.active_general_text, s)
    except Exception:
        pass


class VELO_OT_match_row_add(bpy.types.Operator):
    bl_idname = "velo.match_row_add"
    bl_label = 'Add a new blank row'
    bl_description = 'Insert a blank line after the current line; pass -1 to append to the end'
    bl_options = {'REGISTER', 'UNDO'}

    after_index: IntProperty(default=-1)

    def execute(self, context):
        s = context.scene.velo_tools
        from . import properties as _props
        _props._suspend_updates = True
        try:
            it = s.mappings.add()
            it.source_index = len(s.mappings) - 1
            it.original_name = ""
            it.target_name = ""
            it.matched = False
            it.enabled = True
            new_idx = len(s.mappings) - 1
            ai = int(self.after_index)
            if 0 <= ai < new_idx:
                s.mappings.move(new_idx, ai + 1)
                s.active_mapping_index = ai + 1
            else:
                s.active_mapping_index = new_idx
        finally:
            _props._suspend_updates = False
        _general_autosync_to_text(s)
        return {'FINISHED'}


class VELO_OT_match_row_remove(bpy.types.Operator):
    bl_idname = "velo.match_row_remove"
    bl_label = 'Delete current row'
    bl_description = 'Delete specified row; pass -1 to delete the currently selected row'
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1)

    def execute(self, context):
        s = context.scene.velo_tools
        i = int(self.index)
        if i < 0:
            i = int(s.active_mapping_index)
        if not (0 <= i < len(s.mappings)):
            self.report({'ERROR'}, iface_('No Valid Row to Delete'))
            return {'CANCELLED'}
        from . import properties as _props
        _props._suspend_updates = True
        try:
            s.mappings.remove(i)
            s.active_mapping_index = max(0, min(i, len(s.mappings) - 1))
        finally:
            _props._suspend_updates = False
        _general_autosync_to_text(s)
        return {'FINISHED'}


class VELO_OT_match_to_table_lite(bpy.types.Operator):
    """Lightweight match operator consistent with the MMD section's vg_match_to_mmd_table:
    KDTree centroid nearest-neighbor, only writes mappings' target_name; suppresses update callbacks throughout,
    does not trigger renames, does not compute candidates, does not compute centroids, does not grow the unmatched table."""
    bl_idname = "velo.match_to_table_lite"
    bl_label = 'Match by position → write to this table.'
    bl_description = "KDTree centroid matching identical to MMD region; only writes to table, renaming will not occur unless 'Rename Source Object to Unified Number' is clicked"
    bl_options = {'REGISTER', 'UNDO'}

    overwrite_existing: BoolProperty(name='Overwrite existing target name', default=True)

    def execute(self, context):
        from mathutils.kdtree import KDTree
        from . import operators as _ops_self  # reuse vgroup_centroid_local
        from .core.mapping.operators import _compute_centroids_world

        s = context.scene.velo_tools
        base = s.base_object
        target = s.target_object
        if not (base and base.type == 'MESH' and target and target.type == 'MESH'):
            self.report({'ERROR'}, iface_('Please specify the source and target objects first (both must be meshes)'))
            return {'CANCELLED'}

        src_centroids = _compute_centroids_world(base)
        tgt_centroids = _compute_centroids_world(target)
        if not src_centroids:
            self.report({'ERROR'}, iface_('Source object {0} has no available vertex groups with weights').format(base.name))
            return {'CANCELLED'}
        if not tgt_centroids:
            self.report({'ERROR'}, iface_('Target Object {0} has no available vertex groups with weights').format(target.name))
            return {'CANCELLED'}

        # V0.1.6 fix: also compute local centroids for the overlay snapshot (unaffected by VG name changes)
        src_local = compute_all_centroids_local(base)
        tgt_local = compute_all_centroids_local(target)

        tgt_items = list(tgt_centroids.items())
        kd = KDTree(len(tgt_items))
        for i, (_gi, w) in enumerate(tgt_items):
            kd.insert(w, i)
        kd.balance()

        src_name_by_idx = {vg.index: vg.name for vg in base.vertex_groups}
        tgt_name_by_idx = {vg.index: vg.name for vg in target.vertex_groups}
        existing = {it.original_name: it for it in s.mappings}

        from . import properties as _props
        prev_su = _props._suspend_updates
        prev_sh = _props._suspend_history
        _props._suspend_updates = True
        _props._suspend_history = True
        added = updated = skipped = 0
        try:
            for vg in base.vertex_groups:
                src_w = src_centroids.get(vg.index)
                if src_w is None:
                    continue
                src_name = src_name_by_idx.get(vg.index, "")
                if not src_name:
                    continue
                co, idx, dist = kd.find(src_w)
                if idx is None:
                    skipped += 1
                    continue
                if s.use_max_distance and dist > s.max_match_distance:
                    skipped += 1
                    continue
                tgt_gi, _ = tgt_items[idx]
                best_name = tgt_name_by_idx.get(tgt_gi, "")
                if not best_name:
                    skipped += 1
                    continue
                row = existing.get(src_name)
                if row is None:
                    row = s.mappings.add()
                    row.source_index = vg.index
                    row.original_name = src_name
                    row.current_name = src_name
                    row.target_name = best_name
                    row.target_vg_index = tgt_gi
                    row.matched = True
                    row.distance = float(dist)
                    row.enabled = True
                    existing[src_name] = row
                    added += 1
                else:
                    if row.target_name and not self.overwrite_existing:
                        skipped += 1
                        continue
                    if row.target_name != best_name:
                        row.target_name = best_name
                        updated += 1
                    row.target_vg_index = tgt_gi
                    row.matched = True
                    row.distance = float(dist)
                # V0.1.6 fix: also write the overlay snapshot (local centroid)
                bc = src_local.get(vg.index)
                if bc is not None:
                    row.base_centroid_local = bc
                    row.has_base_centroid = True
                tc = tgt_local.get(tgt_gi)
                if tc is not None:
                    row.target_centroid_local = tc
                    row.has_target_centroid = True
        finally:
            _props._suspend_updates = prev_su
            _props._suspend_history = prev_sh

        try:
            from .core.mapping import algorithms as _algo
            if base is not None and _algo.has_vg_snapshot(base):
                _sync_general_source_from_table(s, save_backup=False)
            _general_autosync_to_text(s)
        except Exception:
            pass
        self.report({'INFO'}, iface_('Match (KDTree): Added {0}, Updated {1}, Skipped {2}').format(added, updated, skipped))
        return {'FINISHED'}


class VELO_OT_match_stage_from_source(bpy.types.Operator):
    bl_idname = "velo.match_stage_from_source"
    bl_label = 'Fill Rows from Source Object'
    bl_description = "Append the source object's currently weighted vertex groups as new rows; automatically skip mmd_edge_scale / mmd_vertex_order / UV_*"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.velo_tools
        obj = s.base_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, iface_('Source object not specified'))
            return {'CANCELLED'}
        existing = {(it.original_name or "") for it in s.mappings}
        # Collect names of weighted VGs (excluding special names)
        used = set()
        for v in obj.data.vertices:
            for g in v.groups:
                if g.weight > 0.0:
                    used.add(g.group)
        added = 0
        for vg in obj.vertex_groups:
            if vg.index not in used:
                continue
            if _is_special_match_vg(vg.name):
                continue
            if vg.name in existing:
                continue
            it = s.mappings.add()
            it.source_index = len(s.mappings) - 1
            it.original_name = vg.name
            it.target_name = ""
            it.matched = False
            existing.add(vg.name)
            added += 1
        _general_autosync_to_text(s)
        self.report({'INFO'}, iface_('Pad row: added {0} (source={1})').format(added, obj.name))
        return {'FINISHED'}


class VELO_OT_match_table_to_text(bpy.types.Operator):
    bl_idname = "velo.match_table_to_text"
    bl_label = '→ Built-in Text'
    bl_description = 'Write the current general mapping table to the .blend internal Text (velo_general_mapping.txt)'
    bl_options = {'REGISTER'}

    def execute(self, context):
        s = context.scene.velo_tools
        if len(s.mappings) == 0:
            self.report({'ERROR'}, iface_('The mapping table is empty'))
            return {'CANCELLED'}
        tb = _ensure_general_text(s)
        tb.from_string(_general_serialize_to_text(s))
        self.report({'INFO'}, iface_('Synchronized to Text: {0}').format(tb.name))
        return {'FINISHED'}


class VELO_OT_match_table_from_text(bpy.types.Operator):
    bl_idname = "velo.match_table_from_text"
    bl_label = '← Built-in Text'
    bl_description = 'Write back target_name from the .blend built-in Text (velo_general_mapping.txt).'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.velo_tools
        tb = s.active_general_text
        if tb is None:
            self.report({'ERROR'}, iface_('Unselected mapping table text'))
            return {'CANCELLED'}
        pairs = _general_parse_text(tb.as_string())
        if not pairs:
            self.report({'ERROR'}, iface_('No valid entries in built-in Text'))
            return {'CANCELLED'}
        idx_by_orig = {it.original_name: i for i, it in enumerate(s.mappings)}
        updated = added = 0
        for orig, tgt in pairs:
            i = idx_by_orig.get(orig)
            if i is not None:
                if s.mappings[i].target_name != tgt:
                    s.mappings[i].target_name = tgt
                    updated += 1
            else:
                it = s.mappings.add()
                it.source_index = len(s.mappings) - 1
                it.original_name = orig
                it.target_name = tgt
                it.matched = False
                idx_by_orig[orig] = len(s.mappings) - 1
                added += 1
        try:
            from .core.mapping import algorithms as _algo
            if s.base_object is not None and _algo.has_vg_snapshot(s.base_object):
                _sync_general_source_from_table(s, save_backup=False)
        except Exception:
            pass
        _general_autosync_to_text(s)
        self.report({'INFO'}, iface_('Sync from Text: Updated {0}, Added {1}').format(updated, added))
        return {'FINISHED'}


class VELO_OT_match_table_export(bpy.types.Operator):
    bl_idname = "velo.match_table_export"
    bl_label = 'Export mapping table'
    bl_description = 'Export the current match table as .txt (each line: original name <space> target name)'
    bl_options = {'REGISTER'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filename_ext = ".txt"
    filter_glob: bpy.props.StringProperty(default="*.txt", options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = "velo_match_table.txt"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        s = context.scene.velo_tools
        if len(s.mappings) == 0:
            self.report({'ERROR'}, iface_('Current matching table is empty'))
            return {'CANCELLED'}
        try:
            with open(self.filepath, 'w', encoding='utf-8') as fh:
                fh.write("# velo_tools 通用顶点组匹配表\n")
                fh.write("# 格式: <原名>  <目标名>\n")
                for it in s.mappings:
                    src = (it.original_name or "").strip()
                    tgt = (it.target_name or it.current_name or "").strip()
                    if not src or not tgt:
                        continue
                    fh.write(f"{src} {tgt}\n")
        except Exception as e:
            self.report({'ERROR'}, iface_('Write failed: {0}').format(e))
            return {'CANCELLED'}
        self.report({'INFO'}, iface_('Exported to {0}').format(self.filepath))
        return {'FINISHED'}


class VELO_OT_match_table_import(bpy.types.Operator):
    bl_idname = "velo.match_table_import"
    bl_label = 'Import Mapping Table'
    bl_description = "Read the 'original name target name' mapping from a .txt file and only write back target_name of the current mappings."
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filename_ext = ".txt"
    filter_glob: bpy.props.StringProperty(default="*.txt", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        import os
        if not self.filepath or not os.path.exists(self.filepath):
            self.report({'ERROR'}, iface_('No valid file selected'))
            return {'CANCELLED'}
        s = context.scene.velo_tools
        if len(s.mappings) == 0:
            self.report({'ERROR'}, iface_('Please first perform a match once before importing (import only updates the target column)'))
            return {'CANCELLED'}
        pairs = {}
        try:
            with open(self.filepath, 'r', encoding='utf-8') as fh:
                for raw in fh:
                    line = raw.split('#', 1)[0].strip()
                    if not line:
                        continue
                    parts = line.split(None, 1)
                    if len(parts) != 2:
                        continue
                    pairs[parts[0].strip()] = parts[1].strip()
        except Exception as e:
            self.report({'ERROR'}, iface_('Failed to read: {0}').format(e))
            return {'CANCELLED'}
        updated = 0
        for it in s.mappings:
            new_t = pairs.get(it.original_name)
            if new_t and new_t != it.target_name:
                it.target_name = new_t
                updated += 1
        try:
            from .core.mapping import algorithms as _algo
            if s.base_object is not None and _algo.has_vg_snapshot(s.base_object):
                _sync_general_source_from_table(s, save_backup=False)
            _general_autosync_to_text(s)
        except Exception:
            pass
        self.report({'INFO'}, iface_('Import completed: updated {0} rows').format(updated))
        return {'FINISHED'}


# ============================================================
# V0.1.6 Task 4: general vertex-group matching - 4 buttons (apply/revert for each of source/target)
# ============================================================

def _build_general_ordered_pairs(s):
    """Return four ordered lists in s.mappings order:
    src_o2t: [(original_name, target_name)]
    src_t2o: [(target_name, original_name)]
    tgt_t2o: [(target_name, original_name)] (dedup on first occurrence)
    tgt_o2t: [(original_name, target_name)]
    """
    src_o2t, src_t2o, tgt_o2t = [], [], []
    seen_t = set()
    tgt_t2o = []
    for it in s.mappings:
        if not getattr(it, "matched", True):
            continue
        o, t = it.original_name, it.target_name
        if not o or not t:
            continue
        src_o2t.append((o, t))
        src_t2o.append((t, o))
        tgt_o2t.append((o, t))
        if t not in seen_t:
            seen_t.add(t)
            tgt_t2o.append((t, o))
    return src_o2t, src_t2o, tgt_t2o, tgt_o2t


def _matched_general_rows(s):
    rows = []
    for it in s.mappings:
        if not getattr(it, "matched", True):
            continue
        o = (it.original_name or "").strip()
        t = (it.target_name or "").strip()
        if not o or not t:
            continue
        rows.append(it)
    return rows


def _general_family_rows(s, root: str):
    root = (root or "").strip()
    if not root:
        return []
    rows = []
    for it in s.mappings:
        if not getattr(it, "enabled", True):
            continue
        original_name = (getattr(it, "original_name", "") or "").strip()
        target_name = (getattr(it, "target_name", "") or "").strip()
        if original_name and target_name == root:
            rows.append(it)
    return rows


def _detach_general_source_row(s, row):
    from .core.mapping import algorithms as _algo

    obj = s.base_object
    if obj is None or obj.type != 'MESH':
        return "", 0

    current_actual = (getattr(row, "current_name", "") or row.original_name or "").strip()
    desired_root = (row.original_name or current_actual).strip()
    fallback = [row.original_name, row.target_name]
    arm = s.armature_object
    extra_taken = []
    if arm is not None and arm.type == 'ARMATURE':
        extra_taken = [b.name for b in arm.data.bones if b.name != current_actual]

    rep = _algo.rename_single_vg_no_merge(
        obj,
        current_actual,
        desired_root,
        fallback_names=fallback,
        extra_taken_names=extra_taken,
    )
    final_name = rep.get("final_name", "") or current_actual or desired_root

    bone_n = 0
    if arm is not None and arm.type == 'ARMATURE':
        bone_n = _algo.rename_single_bone_no_merge(
            arm,
            current_actual,
            final_name,
            fallback_names=fallback,
        )

    return final_name, bone_n


def _sync_general_source_from_table(s, *, save_backup: bool = False):
    """If the source object is currently in the unified-numbering state, replay the current mapping table from the original snapshot and move numeric groups to the front."""
    from .core.mapping import algorithms as _algo

    obj = s.base_object
    if obj is None or obj.type != 'MESH':
        return {"renamed": 0, "final_names": []}, 0

    rows = _matched_general_rows(s)
    ordered = [(it.original_name, it.target_name) for it in rows]
    if not ordered:
        return {"renamed": 0, "final_names": []}, 0

    report = _algo.reapply_no_merge_with_suffix_from_snapshot(obj, ordered, save_backup=save_backup)
    _algo.reorder_numeric_vertex_groups_first(obj)

    bone_n = 0
    arm = s.armature_object
    if arm is not None and arm.type == 'ARMATURE':
        bone_n = _algo.reapply_armature_bones_with_suffix_from_snapshot(arm, ordered, save_backup=save_backup)

    prev = _props._suspend_updates
    _props._suspend_updates = True
    try:
        for it in s.mappings:
            it.current_name = it.original_name or it.current_name
        for it, final_name in zip(rows, report.get("final_names", [])):
            it.current_name = final_name
    finally:
        _props._suspend_updates = prev

    try:
        _props._refresh_available_base_vgs(s)
        _props.set_per_object_state(obj, 'RENAMED')
        _props.sync_mappings_to_base_object(s)
    except Exception:
        pass
    return report, bone_n


def _sync_general_source_row_incremental(s, row, new_root: str):
    """In the unified-numbering state, only reorder the actual source VG / bone names of the affected unified family."""
    from .core.mapping import algorithms as _algo

    obj = s.base_object
    if obj is None or obj.type != 'MESH':
        return "", 0

    current_actual = (getattr(row, "current_name", "") or row.original_name or "").strip()
    arm = s.armature_object
    bone_n = 0
    final_name = current_actual
    detached_name = ""
    roots = []
    old_root = _algo.strip_dup_suffix(current_actual)
    if old_root:
        roots.append(old_root)
    new_root = (new_root or "").strip()
    if new_root and new_root not in roots:
        roots.append(new_root)

    prev = _props._suspend_updates
    _props._suspend_updates = True
    try:
        if not new_root:
            detached_name, bone_delta = _detach_general_source_row(s, row)
            bone_n += bone_delta
            if detached_name:
                row.current_name = detached_name
                final_name = detached_name
        for root in roots:
            family_rows = _general_family_rows(s, root)
            if not family_rows:
                continue
            specs = []
            family_actual_names = set()
            for it in family_rows:
                actual_name = (getattr(it, "current_name", "") or it.original_name or it.target_name or "").strip()
                if actual_name:
                    family_actual_names.add(actual_name)
                specs.append({
                    "current": actual_name,
                    "fallbacks": [it.original_name, it.target_name],
                })

            if arm is not None and arm.type == 'ARMATURE':
                final_names, bone_delta = _algo.rename_synced_vg_bone_family_no_merge(
                    obj,
                    arm,
                    specs,
                    root,
                )
                bone_n += bone_delta
            else:
                final_names = _algo.rename_vg_family_no_merge(
                    obj,
                    specs,
                    root,
                )

            for it, assigned in zip(family_rows, final_names):
                if assigned:
                    it.current_name = assigned
                if it == row:
                    final_name = assigned or final_name
    finally:
        _props._suspend_updates = prev

    if not new_root and detached_name:
        final_name = detached_name

    # Reinsert at the correct numeric-sequence position: the numeric family goes entirely to the front, non-numeric ones are appended keeping their existing order.
    _algo.reorder_numeric_vertex_groups_first(obj)
    try:
        _props._refresh_available_base_vgs(s)
        _props.set_per_object_state(obj, 'RENAMED')
        _props.sync_mappings_to_base_object(s)
    except Exception:
        pass
    return final_name, bone_n


def _restore_general_source_to_original(s):
    from .core.mapping import algorithms as _algo

    obj = s.base_object
    if obj is None or obj.type != 'MESH':
        return False, 0
    if not _algo.restore_vg_snapshot(obj):
        return False, 0

    bone_n = 0
    arm = s.armature_object
    if arm is not None and arm.type == 'ARMATURE':
        if _algo.restore_armature_bone_snapshot(arm):
            bone_n = len(arm.data.bones)

    prev = _props._suspend_updates
    _props._suspend_updates = True
    try:
        for it in s.mappings:
            it.current_name = it.original_name or it.current_name
    finally:
        _props._suspend_updates = prev

    try:
        _props._refresh_available_base_vgs(s)
        _props.set_per_object_state(obj, 'ORIGINAL')
        _props.sync_mappings_to_base_object(s)
    except Exception:
        pass
    return True, bone_n


class VELO_OT_general_source_to_unified(bpy.types.Operator):
    bl_idname = "velo.general_source_to_unified"
    bl_label = 'Rename the source object to a unified number'
    bl_description = 'Rename the source object vertex groups to unified numbers according to the mapping table; do not merge duplicates, add .001/.002 suffixes in order; create snapshot for restoration'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.velo_tools
        obj = s.base_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, iface_('Source object not specified'))
            return {'CANCELLED'}
        src_o2t, _st, _tt, _to = _build_general_ordered_pairs(s)
        if not src_o2t:
            self.report({'ERROR'}, iface_('The mapping table is empty, please perform matching first'))
            return {'CANCELLED'}
        r, bone_n = _sync_general_source_from_table(s, save_backup=True)
        try:
            from . import overlay as _ov
            _ov.invalidate_cache()
        except Exception:
            pass
        self.report({'INFO'}, iface_('Source→Unified ID: Renamed {0} (Not merged; Skeleton {1})').format(r['renamed'], bone_n))
        return {'FINISHED'}


class VELO_OT_general_source_to_original(bpy.types.Operator):
    bl_idname = "velo.general_source_to_original"
    bl_label = 'Restore the source object to the original name'
    bl_description = "Use a snapshot for lossless restoration of the source object's vertex groups and original skeleton names"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        s = context.scene.velo_tools
        obj = s.base_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, iface_('Source object not specified'))
            return {'CANCELLED'}
        ok, bone_n = _restore_general_source_to_original(s)
        if not ok:
            self.report({'ERROR'}, iface_('VG snapshot not found, cannot restore losslessly'))
            return {'CANCELLED'}
        try:
            from . import overlay as _ov
            _ov.invalidate_cache()
        except Exception:
            pass
        self.report({'INFO'}, iface_('Source→Original Name: Restored without loss using snapshot (Skeleton {0})').format(bone_n))
        return {'FINISHED'}


class VELO_OT_general_target_to_mmd(bpy.types.Operator):
    bl_idname = "velo.general_target_to_mmd"
    bl_label = 'Rename target object to source object name'
    bl_description = 'Rename the unified vertex groups of the target object to the original names of the source object and reorder according to the mapping table; for the same target group, only take the first occurrence of the source name'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .core.mapping import algorithms as _algo
        s = context.scene.velo_tools
        obj = s.target_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, iface_('Target object not specified'))
            return {'CANCELLED'}
        _so, _st, tgt_t2o, _to = _build_general_ordered_pairs(s)
        if not tgt_t2o:
            self.report({'ERROR'}, iface_('The mapping table is empty, please perform matching first'))
            return {'CANCELLED'}
        r = _algo.rename_no_merge_with_suffix(obj, tgt_t2o, save_backup=True)
        desired = [o for _t, o in tgt_t2o]
        _algo.reorder_vertex_groups_by_order(obj, desired)
        self.report({'INFO'}, iface_('Target→Source Name: Rename {0} (Target={1})').format(r['renamed'], obj.name))
        return {'FINISHED'}


class VELO_OT_general_target_to_unified(bpy.types.Operator):
    bl_idname = "velo.general_target_to_unified"
    bl_label = 'Rename the target object to a unified number'
    bl_description = 'Restore unified numbering names of target objects; prioritize lossless restoration using snapshots, if no snapshot is available, rename according to the mapping table in reverse'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from .core.mapping import algorithms as _algo
        s = context.scene.velo_tools
        obj = s.target_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, iface_('Target object not specified'))
            return {'CANCELLED'}
        if _algo.restore_vg_snapshot(obj):
            self.report({'INFO'}, iface_('Target → Unified Number: Restored from used snapshot without loss (Target={0})').format(obj.name))
            return {'FINISHED'}
        # Reverse: source name -> target name (dedup on first occurrence)
        seen = set()
        ordered = []
        for it in s.mappings:
            if not getattr(it, "matched", True):
                continue
            o, t = it.original_name, it.target_name
            if not o or not t or o in seen:
                continue
            seen.add(o)
            ordered.append((o, t))
        if not ordered:
            self.report({'ERROR'}, iface_('The mapping table is empty and no snapshot is available to restore'))
            return {'CANCELLED'}
        r = _algo.rename_no_merge_with_suffix(obj, ordered, save_backup=False)
        self.report({'INFO'}, iface_('Target → Unified Number: Renamed {0} (Target={1})').format(r['renamed'], obj.name))
        return {'FINISHED'}


# ============================================================
# Task 4: general-section "mapping table" Text selector (template_ID style, no separate slot)
# ============================================================


class VELO_OT_general_text_new(bpy.types.Operator):
    """Create a new empty mapping table Text and bind it to the current source object; the current mappings will be cleared."""
    bl_idname = "velo.general_text_new"
    bl_label = 'Create Mapping Table'
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name='Name', default="")

    def invoke(self, context, event):
        s = context.scene.velo_tools
        base = s.base_object
        default = f"velo_general_{base.name}.txt" if base is not None else "velo_general_new.txt"
        # Increment if the name collides
        i = 1
        candidate = default
        while bpy.data.texts.get(candidate) is not None:
            i += 1
            stem = default[:-4] if default.endswith(".txt") else default
            candidate = f"{stem}.{i:03d}.txt"
        self.name = candidate
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        s = context.scene.velo_tools
        nm = (self.name or "").strip() or "velo_general_new.txt"
        if not nm.endswith(".txt"):
            nm += ".txt"
        tb = bpy.data.texts.get(nm) or bpy.data.texts.new(nm)
        tb.from_string("# velo_tools 通用顶点组匹配表\n# 格式: <原名>\\t<目标名>\n")
        from . import properties as _props
        prev = _props._suspend_updates
        _props._suspend_updates = True
        try:
            s.mappings.clear()
            s.active_general_text = tb
        finally:
            _props._suspend_updates = prev
        # Write the binding to base
        base = s.base_object
        if base is not None:
            try:
                base["velo_general_text"] = tb.name
            except Exception:
                pass
        self.report({'INFO'}, iface_('Created and switched to mapping table: {0}').format(tb.name))
        return {'FINISHED'}


_classes = ()


def register():
    return


def unregister():
    return
