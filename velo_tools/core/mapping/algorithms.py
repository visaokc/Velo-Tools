"""Three-stage mapping core algorithms (R3 phase).

PLAN §2.2 splits into four phases:
- Phase A: auto-build UnifiedVGTable on MOD import (already done in R2 importer)
- Phase B: user edits MMD -> unified mapping (covered by this module's 5 operators)
- Phase C: user splits + annotates ownership (manual + collection naming convention)
- Phase D: on export, MMD -> unified -> native three-stage translation (R4 calls this module from the exporter)

This file holds algorithms only and does not handle UI directly; operators live in operators.py.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable


_SUFFIX_RE = re.compile(r"\.\d{3}$")


def strip_dup_suffix(name: str) -> str:
    if not name:
        return name
    return _SUFFIX_RE.sub("", name)


# ============================================================
# Dictionary building
# ============================================================

def build_mmd_to_unified(profile) -> dict:
    """profile: VELO_EF_MmdMappingProfile -> {mmd_name: unified_name}"""
    out = {}
    if profile is None:
        return out
    for row in profile.rows:
        if not row.mmd_name or not row.unified_name:
            continue
        out[row.mmd_name] = row.unified_name
    return out


def build_unified_to_mmd(profile) -> dict:
    """Reverse: {unified_name: mmd_name} (when one unified maps to multiple mmd, keep only the first)."""
    out = {}
    if profile is None:
        return out
    for row in profile.rows:
        if not row.mmd_name or not row.unified_name:
            continue
        out.setdefault(row.unified_name, row.mmd_name)
    return out


def build_ordered_mmd_pairs(profile):
    """Return a [(mmd_name, unified_root)] list in profile.rows order, for use by
    rename_no_merge_with_suffix / rename_armature_bones_with_suffix."""
    out = []
    if profile is None:
        return out
    for row in profile.rows:
        if not row.mmd_name or not row.unified_name:
            continue
        out.append((row.mmd_name, row.unified_name))
    return out


def build_unified_to_native(component_map) -> dict:
    """component_map: VELO_EF_ComponentMap -> {unified_name: native_name}"""
    out = {}
    if component_map is None:
        return out
    for p in component_map.pairs:
        if not p.unified_name or not p.native_name:
            continue
        out[p.unified_name] = p.native_name
    return out


# ============================================================
# Vertex group rename + same-name merge
# ============================================================

def _collect_weights(obj, vg_index: int) -> dict:
    """Return {vertex_index: weight} (only weight > 0)."""
    result = {}
    if obj is None or obj.type != 'MESH' or vg_index < 0:
        return result
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == vg_index and g.weight > 0.0:
                result[v.index] = g.weight
                break
    return result


def _collect_all_weights(obj) -> dict:
    """Single-pass scan over the whole mesh, returning {vg_index: {vertex_index: weight}}.

    The old _collect_weights re-traversed the whole mesh for each vg, making
    rename_vertex_groups_with_merge O(VG * Verts); this function scans once in O(Verts),
    the root-cause fix for the V0.1.4 stall.
    """
    out = {}
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return out
    for v in obj.data.vertices:
        vi = v.index
        for g in v.groups:
            w = g.weight
            if w <= 0.0:
                continue
            d = out.get(g.group)
            if d is None:
                d = {}
                out[g.group] = d
            d[vi] = w
    return out


_NUMERIC_RE = re.compile(r"^\d+$")
_NUMERIC_FAMILY_RE = re.compile(r"^(\d+)(?:\.(\d{3}))?$")


def _numeric_sort_key(name: str):
    """Sort pure-numeric names by numeric value; non-numeric names sort lexically and after them."""
    if name is None:
        return (1, "")
    if _NUMERIC_RE.match(name):
        try:
            return (0, int(name), name)
        except Exception:
            return (1, name)
    return (1, name)


def _numeric_family_sort_key(name: str):
    """Sort numeric families (e.g. 5 / 5.001 / 12 / 12.001) to the front by numeric value.
    Non-numeric names stay after, appended in original order by reorder_vertex_groups_by_order."""
    if name is None:
        return (1, 0, 0, "")
    m = _NUMERIC_FAMILY_RE.match(name)
    if not m:
        return (1, 0, 0, name)
    root = int(m.group(1))
    suffix = int(m.group(2) or 0)
    return (0, root, suffix, name)


def allocate_unique_no_merge_name(desired_root: str, existing_names, *, current_name: str = "") -> str:
    """Allocate a unique name for no-merge renaming.

    - The mapping table always stores the root (e.g. 36)
    - If the root already exists on the actual object, allocate 36.001 / 36.002 ...
    - If current_name already belongs to that root family, prefer keeping current_name to avoid pointless churn
    """
    root = (desired_root or "").strip()
    if not root:
        return (current_name or "").strip()

    current = (current_name or "").strip()
    taken = {n for n in (existing_names or []) if n}
    if current:
        taken.discard(current)
        if strip_dup_suffix(current) == root:
            return current

    if root not in taken:
        return root

    index = 1
    while True:
        candidate = f"{root}.{index:03d}"
        if candidate not in taken:
            return candidate
        index += 1


_VG_ORDER_BACKUP_KEY = "velo_vg_order_backup"
_VG_SNAPSHOT_KEY = "velo_vg_snapshot_json"
_BONE_SNAPSHOT_KEY = "velo_bone_snapshot_json"


def has_vg_snapshot(obj, key: str = _VG_SNAPSHOT_KEY) -> bool:
    if obj is None:
        return False
    try:
        return bool(obj.get(key))
    except Exception:
        return False


def has_armature_bone_snapshot(arm_obj, key: str = _BONE_SNAPSHOT_KEY) -> bool:
    if arm_obj is None:
        return False
    try:
        return bool(arm_obj.get(key))
    except Exception:
        return False


def _unique_temp_names(existing_names, count: int, prefix: str):
    names = set(existing_names or [])
    out = []
    cursor = 0
    while len(out) < count:
        candidate = f"{prefix}{cursor}"
        cursor += 1
        if candidate in names:
            continue
        names.add(candidate)
        out.append(candidate)
    return out


def _family_spec_candidates(spec):
    candidates = []
    for name in [spec.get("current", ""), *(spec.get("fallbacks", []) or [])]:
        name = (name or "").strip()
        if name and name not in candidates:
            candidates.append(name)
    return candidates


def rename_synced_vg_bone_family_no_merge(obj, arm_obj, row_specs, desired_root: str, *, extra_taken_names=()):
    """Linked rename of VG/bones in the same family via shared temporary names.

    First rename each row's VG to that row's exclusive temp name, then rename the same row's bone
    to the same temp name; finally rename the bone to its final name and let Blender carry the
    same-named VG along to the final name too, avoiding the bone-temp-name stage from
    accidentally hijacking other already-reassigned VGs.
    """
    if obj is None or obj.type != 'MESH' or obj.data is None or not row_specs:
        return [], 0
    if arm_obj is None or arm_obj.type != 'ARMATURE':
        return rename_vg_family_no_merge(obj, row_specs, desired_root, extra_taken_names=extra_taken_names), 0

    resolved_vgs = []
    used_vg_indices = set()
    for spec in row_specs:
        vg = None
        for name in _family_spec_candidates(spec):
            cand = obj.vertex_groups.get(name)
            if cand is not None and cand.index not in used_vg_indices:
                vg = cand
                used_vg_indices.add(cand.index)
                break
        resolved_vgs.append(vg)

    resolved_bones = []
    used_bone_names = set()
    for spec in row_specs:
        bone = None
        for name in _family_spec_candidates(spec):
            cand = arm_obj.data.bones.get(name)
            if cand is not None and cand.name not in used_bone_names:
                bone = cand
                used_bone_names.add(cand.name)
                break
        resolved_bones.append(bone)

    family_vg_indices = {vg.index for vg in resolved_vgs if vg is not None}
    family_bone_names = {bone.name for bone in resolved_bones if bone is not None}
    outside = {vg.name for vg in obj.vertex_groups if vg.index not in family_vg_indices}
    outside.update({bone.name for bone in arm_obj.data.bones if bone.name not in family_bone_names})
    outside.update({n for n in (extra_taken_names or []) if n})

    final_names = []
    taken = set(outside)
    for spec, vg, bone in zip(row_specs, resolved_vgs, resolved_bones):
        current_name = ""
        if vg is not None:
            current_name = vg.name
        elif bone is not None:
            current_name = bone.name
        else:
            current_name = (spec.get("current", "") or "")
        if vg is None and bone is None:
            final_name = current_name
        else:
            final_name = allocate_unique_no_merge_name(desired_root, taken, current_name="")
            if not final_name:
                final_name = current_name
        final_names.append(final_name)
        if final_name:
            taken.add(final_name)

    if not any(vg is not None or bone is not None for vg, bone in zip(resolved_vgs, resolved_bones)):
        return final_names, 0

    temp_names = _unique_temp_names(
        {vg.name for vg in obj.vertex_groups}
        | {bone.name for bone in arm_obj.data.bones}
        | set(extra_taken_names or []),
        len(row_specs),
        "__velo_family_tmp_",
    )

    for vg, temp_name in zip(resolved_vgs, temp_names):
        if vg is None:
            continue
        try:
            vg.name = temp_name
        except Exception:
            pass

    for bone, temp_name in zip(resolved_bones, temp_names):
        if bone is None:
            continue
        try:
            bone.name = temp_name
        except Exception:
            pass

    renamed_bones = 0
    for bone, temp_name, final_name in zip(resolved_bones, temp_names, final_names):
        if bone is None or not final_name:
            continue
        cur = arm_obj.data.bones.get(temp_name) or bone
        try:
            cur.name = final_name
            renamed_bones += 1
        except Exception:
            pass

    for vg, temp_name, final_name in zip(resolved_vgs, temp_names, final_names):
        if vg is None or not final_name:
            continue
        cur = obj.vertex_groups.get(temp_name) or vg
        try:
            cur.name = final_name
        except Exception:
            pass
    return final_names, renamed_bones


def rename_vg_family_no_merge(obj, row_specs, desired_root: str, *, extra_taken_names=()):
    """Two-stage rename of a group of VGs in the same unified-root family only; does not replay the whole table."""
    if obj is None or obj.type != 'MESH' or obj.data is None or not row_specs:
        return []

    resolved = []
    used_indices = set()
    for spec in row_specs:
        vg = None
        candidates = []
        for name in [spec.get("current", ""), *(spec.get("fallbacks", []) or [])]:
            name = (name or "").strip()
            if name and name not in candidates:
                candidates.append(name)
        for name in candidates:
            cand = obj.vertex_groups.get(name)
            if cand is not None and cand.index not in used_indices:
                vg = cand
                used_indices.add(cand.index)
                break
        resolved.append((spec, vg))

    family_vgs = [vg for _spec, vg in resolved if vg is not None]
    family_indices = {vg.index for vg in family_vgs}
    outside = {vg.name for vg in obj.vertex_groups if vg.index not in family_indices}
    outside.update({n for n in (extra_taken_names or []) if n})

    final_names = []
    taken = set(outside)
    for spec, vg in resolved:
        current_name = vg.name if vg is not None else (spec.get("current", "") or "")
        if vg is None:
            final_name = current_name
        else:
            final_name = allocate_unique_no_merge_name(desired_root, taken, current_name="")
            if not final_name:
                final_name = current_name
        final_names.append(final_name)
        if final_name:
            taken.add(final_name)

    if not family_vgs:
        return final_names

    temp_names = _unique_temp_names(
        {vg.name for vg in obj.vertex_groups} | set(extra_taken_names or []),
        len(family_vgs),
        "__velo_vg_family_tmp_",
    )
    temp_iter = iter(temp_names)
    temp_by_index = {}
    for _spec, vg in resolved:
        if vg is None:
            continue
        temp_name = next(temp_iter)
        temp_by_index[vg.index] = temp_name
        try:
            vg.name = temp_name
        except Exception:
            pass

    for (_spec, vg), final_name in zip(resolved, final_names):
        if vg is None or not final_name:
            continue
        cur = obj.vertex_groups.get(temp_by_index.get(vg.index, "")) or vg
        try:
            cur.name = final_name
        except Exception:
            pass
    return final_names


def rename_bone_family_no_merge(arm_obj, row_specs, final_names):
    """Bone version of two-stage family rename; final_names reuses the VG family allocation result directly."""
    if arm_obj is None or arm_obj.type != 'ARMATURE' or not row_specs or not final_names:
        return 0

    resolved = []
    used_names = set()
    for spec in row_specs:
        bone = None
        candidates = []
        for name in [spec.get("current", ""), *(spec.get("fallbacks", []) or [])]:
            name = (name or "").strip()
            if name and name not in candidates:
                candidates.append(name)
        for name in candidates:
            cand = arm_obj.data.bones.get(name)
            if cand is not None and cand.name not in used_names:
                bone = cand
                used_names.add(cand.name)
                break
        resolved.append(bone)

    family_bones = [b for b in resolved if b is not None]
    if not family_bones:
        return 0

    temp_names = _unique_temp_names(
        {b.name for b in arm_obj.data.bones},
        len(family_bones),
        "__velo_bone_family_tmp_",
    )
    temp_iter = iter(temp_names)
    temp_slots = []
    for bone in resolved:
        if bone is None:
            temp_slots.append("")
            continue
        temp_name = next(temp_iter)
        temp_slots.append(temp_name)
        try:
            bone.name = temp_name
        except Exception:
            pass

    renamed = 0
    for bone, temp_name, final_name in zip(resolved, temp_slots, final_names):
        if bone is None or not final_name:
            continue
        cur = arm_obj.data.bones.get(temp_name) or bone
        try:
            cur.name = final_name
            renamed += 1
        except Exception:
            pass
    return renamed


def rename_single_vg_no_merge(obj, current_name: str, desired_root: str, *, fallback_names=(), extra_taken_names=()):
    """Rename a single vertex group only, without replaying the whole table; returns {renamed, old_name, final_name}."""
    report = {"renamed": 0, "old_name": "", "final_name": ""}
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return report

    vg = None
    candidates = []
    for name in (current_name, *fallback_names):
        name = (name or "").strip()
        if name and name not in candidates:
            candidates.append(name)
    for name in candidates:
        vg = obj.vertex_groups.get(name)
        if vg is not None:
            break
    if vg is None:
        return report

    old_name = vg.name
    taken = {g.name for g in obj.vertex_groups if g.index != vg.index}
    taken.update({n for n in (extra_taken_names or []) if n and n != old_name})
    final_name = allocate_unique_no_merge_name(desired_root, taken, current_name=old_name)
    report["old_name"] = old_name
    report["final_name"] = final_name
    if not final_name or final_name == old_name:
        return report
    try:
        vg.name = final_name
        report["renamed"] = 1
    except Exception:
        pass
    return report


def rename_single_bone_no_merge(arm_obj, current_name: str, desired_final_name: str, *, fallback_names=()):
    """Rename a single bone only; the caller must ensure desired_final_name is already unique."""
    if arm_obj is None or arm_obj.type != 'ARMATURE' or not desired_final_name:
        return 0
    candidates = []
    for name in (current_name, *fallback_names):
        name = (name or "").strip()
        if name and name not in candidates:
            candidates.append(name)
    bone = None
    for name in candidates:
        bone = arm_obj.data.bones.get(name)
        if bone is not None:
            break
    if bone is None or bone.name == desired_final_name:
        return 0
    try:
        bone.name = desired_final_name
        return 1
    except Exception:
        return 0


# ============================================================
# Full VG snapshot (V0.1.6 lossless roundtrip fix)
# ============================================================
def _serialize_snapshot(obj) -> str:
    """Serialize all of obj's current VGs (order + per-vertex weights) into a JSON string."""
    import json as _json
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return ""
    order = [vg.name for vg in obj.vertex_groups]
    weights_by_vg = _collect_all_weights(obj)
    idx_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
    weights = {n: [] for n in order}
    for gi, wmap in weights_by_vg.items():
        name = idx_to_name.get(gi)
        if name is None:
            continue
        weights[name] = [(int(vi), float(w)) for vi, w in wmap.items()]
    return _json.dumps({"order": order, "weights": weights})


def save_vg_snapshot(obj, key: str = _VG_SNAPSHOT_KEY) -> bool:
    """Save the snapshot to obj[key]. If a snapshot already exists -> do not overwrite (keep the earliest real snapshot)."""
    if obj is None:
        return False
    try:
        if obj.get(key):
            return False  # snapshot already exists, do not overwrite
        snap = _serialize_snapshot(obj)
        if not snap:
            return False
        obj[key] = snap
        return True
    except Exception:
        return False


def restore_vg_snapshot(obj, key: str = _VG_SNAPSHOT_KEY) -> bool:
    """Fully restore VG names + order + weights from obj[key], and clear the key. Return False on failure."""
    import json as _json
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return False
    raw = obj.get(key)
    if not raw:
        return False
    try:
        snap = _json.loads(raw)
    except Exception:
        return False
    order = snap.get("order") or []
    weights = snap.get("weights") or {}
    if not order:
        return False
    # delete all existing VGs
    for vg in list(obj.vertex_groups):
        try:
            obj.vertex_groups.remove(vg)
        except Exception:
            pass
    for name in order:
        new_vg = obj.vertex_groups.new(name=name)
        wlist = weights.get(name) or []
        if not wlist:
            continue
        # bucket by weight value and write in batches
        buckets = defaultdict(list)
        for vi, w in wlist:
            ww = float(w)
            if ww > 1.0:
                ww = 1.0
            if ww <= 0.0:
                continue
            buckets[ww].append(int(vi))
        for ww, vis in buckets.items():
            try:
                new_vg.add(vis, ww, 'REPLACE')
            except Exception:
                pass
    try:
        del obj[key]
    except Exception:
        pass
    return True


def reorder_numeric_vertex_groups_first(obj):
    """Sort all numeric-family vertex groups to the front by numeric value; other VGs keep current order and are appended after."""
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return
    current = [vg.name for vg in obj.vertex_groups]
    desired = [n for n in current if _NUMERIC_FAMILY_RE.match(n or "")]
    desired.sort(key=_numeric_family_sort_key)
    if desired:
        reorder_vertex_groups_by_order(obj, desired)


def reapply_no_merge_with_suffix_from_snapshot(obj, ordered_pairs, *, save_backup: bool = False) -> dict:
    """If the object already has an original snapshot, temporarily restore to the original state first, then replay a no-merge rename once per the current mapping.
    The restore keeps the earliest snapshot, ensuring the original names/order can still be losslessly recovered afterwards."""
    raw_snapshot = None
    try:
        raw_snapshot = obj.get(_VG_SNAPSHOT_KEY) if obj is not None else None
    except Exception:
        raw_snapshot = None

    if raw_snapshot:
        restore_vg_snapshot(obj)
        try:
            obj[_VG_SNAPSHOT_KEY] = raw_snapshot
        except Exception:
            pass
        return rename_no_merge_with_suffix(obj, ordered_pairs, save_backup=False)
    return rename_no_merge_with_suffix(obj, ordered_pairs, save_backup=save_backup)


def reapply_armature_bones_with_suffix_from_snapshot(arm_obj, ordered_pairs, *, save_backup: bool = False) -> int:
    """Bone version of the snapshot-aware replay."""
    raw_snapshot = None
    try:
        raw_snapshot = arm_obj.get(_BONE_SNAPSHOT_KEY) if arm_obj is not None else None
    except Exception:
        raw_snapshot = None

    if raw_snapshot:
        restore_armature_bone_snapshot(arm_obj)
        try:
            arm_obj[_BONE_SNAPSHOT_KEY] = raw_snapshot
        except Exception:
            pass
        return rename_armature_bones_with_suffix(arm_obj, ordered_pairs)

    if save_backup:
        save_armature_bone_snapshot(arm_obj)
    return rename_armature_bones_with_suffix(arm_obj, ordered_pairs)


# ============================================================
# Synchronized bone-name rename (V0.1.6 task 2)
# ============================================================
def rename_armature_bones(arm_obj, name_map: dict) -> int:
    """Rename the armature object's bones per name_map, returning the number successfully renamed.
    Requires arm_obj to be ARMATURE; automatically avoids same-name collisions (skip if already exists)."""
    if arm_obj is None or arm_obj.type != 'ARMATURE' or not name_map:
        return 0
    arm = arm_obj.data
    existing = {b.name for b in arm.bones}
    renamed = 0
    for old, new in list(name_map.items()):
        if not old or not new or old == new:
            continue
        b = arm.bones.get(old)
        if b is None:
            continue
        if new in existing and new != old:
            continue
        try:
            b.name = new
        except Exception:
            continue
        existing.discard(old)
        existing.add(new)
        renamed += 1
    return renamed


# ============================================================
# Armature snapshot (V0.1.6 task 2 revision: lossless restore of bone names)
# ============================================================
def save_armature_bone_snapshot(arm_obj, key: str = _BONE_SNAPSHOT_KEY) -> bool:
    import json as _json
    if arm_obj is None or arm_obj.type != 'ARMATURE':
        return False
    try:
        if arm_obj.get(key):
            return False
        names = [b.name for b in arm_obj.data.bones]
        arm_obj[key] = _json.dumps({"names": names})
        return True
    except Exception:
        return False


def restore_armature_bone_snapshot(arm_obj, key: str = _BONE_SNAPSHOT_KEY) -> bool:
    """Restore bone names in snapshot order. Two-stage (first rename all to temp names, then write back) to avoid collisions."""
    import json as _json
    if arm_obj is None or arm_obj.type != 'ARMATURE':
        return False
    raw = arm_obj.get(key)
    if not raw:
        return False
    try:
        snap = _json.loads(raw)
    except Exception:
        return False
    target_names = snap.get("names") or []
    bones = list(arm_obj.data.bones)
    if not target_names or len(target_names) != len(bones):
        # if counts differ, still try to map the first N in current order
        n = min(len(target_names), len(bones))
        if n == 0:
            return False
        bones = bones[:n]
        target_names = target_names[:n]
    # stage 1: temp names
    for i, b in enumerate(bones):
        try:
            b.name = f"__velo_bone_tmp_{i}"
        except Exception:
            pass
    # stage 2: write back real names
    for b, name in zip(bones, target_names):
        try:
            b.name = name
        except Exception:
            pass
    try:
        del arm_obj[key]
    except Exception:
        pass
    return True


# ============================================================
# No-merge + ordered-suffix rename (V0.1.6 revision core: source -> unified no longer merges)
# ============================================================
def rename_no_merge_with_suffix(obj, ordered_pairs, *, save_backup: bool = True) -> dict:
    """Rename VGs in ordered_pairs order, no merge; on collision add .001/.002 suffixes by order of appearance.

    Parameters:
        obj: MESH object
        ordered_pairs: List[Tuple[old_name, unified_root]], provided in mapping-table order
        save_backup: whether to save a full VG snapshot for restoration

    Returns dict: {"renamed": int, "missing": [old], "final_names": [str]}
    Flow:
      1) (optional) save a full VG snapshot
      2) compute the final name for each (old, root) (Nth occurrence of root -> root.NNN, N>=1)
      3) stage 1: rename matched VGs to temp names "__velo_vg_tmp_<i>"
      4) stage 2: temp name -> final name
      5) delete and rebuild in (final_names + untouched original order) (preserve weights, no merge)
    """
    report = {"renamed": 0, "missing": [], "final_names": []}
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return report

    if save_backup:
        save_vg_snapshot(obj)

    # collect vg name -> index
    name_to_idx = {vg.name: vg.index for vg in obj.vertex_groups}
    counts = defaultdict(int)
    plan = []  # [(old, final)]
    for old, root in ordered_pairs:
        if not old or not root:
            continue
        idx = name_to_idx.get(old)
        if idx is None:
            # try stripping the suffix and look up again
            idx = name_to_idx.get(strip_dup_suffix(old))
            if idx is None:
                report["missing"].append(old)
                continue
        c = counts[root]
        final = root if c == 0 else f"{root}.{c:03d}"
        counts[root] += 1
        plan.append((old, final))

    if not plan:
        return report

    # single-pass collection of all VG weights + original order
    weights_by_vg = _collect_all_weights(obj)
    idx_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
    weights_by_name = {idx_to_name[i]: w for i, w in weights_by_vg.items() if i in idx_to_name}
    original_order = [vg.name for vg in obj.vertex_groups]

    # compute final order
    renamed_set_old = {old for old, _ in plan}
    untouched = [n for n in original_order if n not in renamed_set_old]
    final_order = [final for _, final in plan] + untouched

    # rename the weight-table keys
    new_weights_by_name = {}
    for old, final in plan:
        new_weights_by_name[final] = weights_by_name.get(old, {})
    for n in untouched:
        new_weights_by_name[n] = weights_by_name.get(n, {})

    # delete all VGs and rebuild in final_order
    for vg in list(obj.vertex_groups):
        try:
            obj.vertex_groups.remove(vg)
        except Exception:
            pass
    for name in final_order:
        new_vg = obj.vertex_groups.new(name=name)
        wmap = new_weights_by_name.get(name) or {}
        if not wmap:
            continue
        buckets = defaultdict(list)
        for vi, w in wmap.items():
            ww = w if w <= 1.0 else 1.0
            if ww <= 0.0:
                continue
            buckets[ww].append(vi)
        for ww, vis in buckets.items():
            try:
                new_vg.add(vis, ww, 'REPLACE')
            except Exception:
                pass

    report["renamed"] = len(plan)
    report["final_names"] = [f for _, f in plan]
    return report


def rename_armature_bones_with_suffix(arm_obj, ordered_pairs) -> int:
    """Companion to rename_no_merge_with_suffix: bones get .NNN suffixes in the same order.
    Two-stage to avoid collisions."""
    if arm_obj is None or arm_obj.type != 'ARMATURE' or not ordered_pairs:
        return 0
    arm = arm_obj.data
    name_to_bone = {b.name: b for b in arm.bones}
    counts = defaultdict(int)
    plan = []  # [(bone, final)]
    for old, root in ordered_pairs:
        if not old or not root:
            continue
        b = name_to_bone.get(old)
        if b is None:
            continue
        c = counts[root]
        final = root if c == 0 else f"{root}.{c:03d}"
        counts[root] += 1
        plan.append((b, final))
    if not plan:
        return 0
    # stage 1: temp names
    for i, (b, _f) in enumerate(plan):
        try:
            b.name = f"__velo_bone_tmp_{i}"
        except Exception:
            pass
    # stage 2: final names
    n = 0
    for b, final in plan:
        try:
            b.name = final
            n += 1
        except Exception:
            pass
    return n


def reorder_vertex_groups_by_order(obj, desired_order):
    """Arrange obj's VGs into the order listed in desired_order; those not in desired_order keep their original order and are appended after.
    Implemented by delete + rebuild (preserving weights)."""
    if obj is None or obj.type != 'MESH' or not desired_order:
        return
    weights_by_vg = _collect_all_weights(obj)
    idx_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
    lock_by_name = {vg.name: bool(getattr(vg, "lock_weight", False)) for vg in obj.vertex_groups}
    active_name = ""
    try:
        if 0 <= obj.vertex_groups.active_index < len(obj.vertex_groups):
            active_name = obj.vertex_groups[obj.vertex_groups.active_index].name
    except Exception:
        active_name = ""
    weights_by_name = {}
    for gi, wmap in weights_by_vg.items():
        nm = idx_to_name.get(gi)
        if nm is not None:
            weights_by_name[nm] = wmap
    current = [vg.name for vg in obj.vertex_groups]
    in_desired = [n for n in desired_order if n in current]
    rest = [n for n in current if n not in set(in_desired)]
    final_order = in_desired + rest
    if final_order == current:
        return
    for vg in list(obj.vertex_groups):
        try:
            obj.vertex_groups.remove(vg)
        except Exception:
            pass
    for name in final_order:
        new_vg = obj.vertex_groups.new(name=name)
        try:
            new_vg.lock_weight = lock_by_name.get(name, False)
        except Exception:
            pass
        wmap = weights_by_name.get(name) or {}
        if not wmap:
            continue
        buckets = defaultdict(list)
        for vi, w in wmap.items():
            ww = w if w <= 1.0 else 1.0
            if ww <= 0.0:
                continue
            buckets[ww].append(vi)
        for ww, vis in buckets.items():
            try:
                new_vg.add(vis, ww, 'REPLACE')
            except Exception:
                pass
    if active_name:
        active_vg = obj.vertex_groups.get(active_name)
        if active_vg is not None:
            try:
                obj.vertex_groups.active_index = active_vg.index
            except Exception:
                pass


def rename_and_reorder(
    obj,
    name_map: dict,
    *,
    sort_renamed_numerically: bool = False,
    untouched_to_end: bool = False,
    save_backup: bool = False,
    restore_backup: bool = False,
) -> dict:
    """Single-pass rename + merge + reorder (V0.1.4 performance/sorting/restore unified).

    Parameters:
        name_map: {old_name: new_name} (matching the strip(.NNN) name also counts).
        sort_renamed_numerically: when True, renamed items are sorted numerically to the front.
        untouched_to_end: when True, VGs not renamed keep their original order and go at the end.
        save_backup: when True, write the pre-rename VG name order to obj["velo_vg_order_backup"].
        restore_backup: when True, prefer writing back in obj["velo_vg_order_backup"] order
            (known names first in backup order, unknown names appended in current order), and clear the backup.

    Returns: {"renamed": int, "merged": int, "skipped": int, "missing": [old_name]}
    """
    report = {"renamed": 0, "merged": 0, "skipped": 0, "missing": []}
    if obj is None or obj.type != 'MESH':
        return report

    name_map = name_map or {}

    # V0.1.6 lossless restore: prefer the full snapshot
    if restore_backup:
        if restore_vg_snapshot(obj):
            # also clear the old order-only backup (if any)
            try:
                if _VG_ORDER_BACKUP_KEY in obj.keys():
                    del obj[_VG_ORDER_BACKUP_KEY]
            except Exception:
                pass
            report["renamed"] = len(obj.vertex_groups)
            return report

    # V0.1.6 lossless backup: save the full pre-rename snapshot (only when save_backup)
    if save_backup:
        save_vg_snapshot(obj)

    # 1) single-pass collection of each VG's (vi -> w)
    weights_by_vg = _collect_all_weights(obj)

    # 2) snapshot of the current VG order
    original_order = [vg.name for vg in obj.vertex_groups]
    idx_by_name = {vg.name: vg.index for vg in obj.vertex_groups}

    # 3) back up the original order (only when save_backup)
    if save_backup:
        try:
            obj[_VG_ORDER_BACKUP_KEY] = list(original_order)
        except Exception:
            pass

    # 4) decide each VG's new name + whether it was renamed
    renamed_pairs = []  # [(old_name, new_name)]
    untouched = []     # [name] (not matched by name_map)
    matched_keys = set()
    for old in original_order:
        new = name_map.get(old)
        if new is None:
            new = name_map.get(strip_dup_suffix(old))
        if new is None:
            untouched.append(old)
        else:
            renamed_pairs.append((old, new))
            matched_keys.add(old)

    # missing: keys in name_map that do not appear in the object
    keys_in_obj = set(original_order) | {strip_dup_suffix(n) for n in original_order}
    for k in name_map.keys():
        if k not in keys_in_obj:
            report["missing"].append(k)

    # 5) merge: new_name -> accumulated weights; record the number of sources for each new_name
    merged_weights = defaultdict(lambda: defaultdict(float))
    sources_count = defaultdict(int)
    for old, new in renamed_pairs:
        gi = idx_by_name.get(old)
        if gi is None:
            continue
        wmap = weights_by_vg.get(gi, {})
        sources_count[new] += 1
        target = merged_weights[new]
        for vi, w in wmap.items():
            target[vi] += w

    renamed_target_names = {new for _old, new in renamed_pairs}
    colliding_untouched = set()

    # 6) preserve untouched weights: if a same-named target group already exists, merge it into merged_weights too,
    # to avoid leaving behind the .001 duplicate family Blender auto-adds.
    untouched_weights = {}
    for name in untouched:
        gi = idx_by_name.get(name)
        wmap = {} if gi is None else weights_by_vg.get(gi, {})
        if name in renamed_target_names:
            colliding_untouched.add(name)
            sources_count[name] += 1
            target = merged_weights[name]
            for vi, w in wmap.items():
                target[vi] += w
            continue
        untouched_weights[name] = wmap

    if colliding_untouched:
        untouched = [name for name in untouched if name not in colliding_untouched]

    # 7) compute the final order
    renamed_names_unique = []
    seen = set()
    for _old, new in renamed_pairs:
        if new not in seen:
            renamed_names_unique.append(new)
            seen.add(new)

    if restore_backup:
        # prefer restoring in backup order
        backup = []
        try:
            backup = list(obj.get(_VG_ORDER_BACKUP_KEY) or [])
        except Exception:
            backup = []
        # names appearing in backup (and present in the object's current "post-rename name set") follow backup order
        all_after = set(renamed_names_unique) | set(untouched)
        ordered = [n for n in backup if n in all_after]
        # names not in backup are appended in (renamed_unique ++ untouched) order
        rest = [n for n in renamed_names_unique + untouched if n not in set(ordered)]
        final_order = ordered + rest
        # clear once used, to avoid misuse by later unrelated renames
        try:
            if _VG_ORDER_BACKUP_KEY in obj.keys():
                del obj[_VG_ORDER_BACKUP_KEY]
        except Exception:
            pass
    else:
        if sort_renamed_numerically:
            renamed_part = sorted(renamed_names_unique, key=_numeric_sort_key)
        else:
            renamed_part = renamed_names_unique
        if untouched_to_end:
            final_order = renamed_part + untouched
        else:
            # keep original order (renamed items land at their old name's position; untouched stay in place)
            slots = []
            seen2 = set()
            renamed_map_old_to_new = {old: new for old, new in renamed_pairs}
            for old in original_order:
                if old in renamed_map_old_to_new:
                    new = renamed_map_old_to_new[old]
                    if new not in seen2:
                        slots.append(new)
                        seen2.add(new)
                else:
                    if old not in seen2:
                        slots.append(old)
                        seen2.add(old)
            final_order = slots

    # 8) delete all original VGs, rebuild in final_order and write weights
    for vg in list(obj.vertex_groups):
        try:
            obj.vertex_groups.remove(vg)
        except Exception:
            pass

    for name in final_order:
        new_vg = obj.vertex_groups.new(name=name)
        if name in untouched_weights:
            wmap = untouched_weights[name]
        else:
            wmap = merged_weights.get(name, {})
        # batch-add same weights (bucket by weight value to reduce .add call count)
        if not wmap:
            continue
        buckets = defaultdict(list)
        for vi, w in wmap.items():
            ww = w if w <= 1.0 else 1.0
            if ww <= 0.0:
                continue
            buckets[ww].append(vi)
        for ww, vis in buckets.items():
            new_vg.add(vis, ww, 'REPLACE')

    # 9) statistics
    for new, count in sources_count.items():
        if count > 1:
            report["merged"] += 1
            report["renamed"] += count
        else:
            report["renamed"] += 1
    report["skipped"] = len(untouched)
    return report


def rename_vertex_groups_with_merge(obj, name_map: dict, *, allow_passthrough: bool = True) -> dict:
    """Rename the current object's vertex groups per name_map={old_name: new_name} (since V0.1.4, delegates to rename_and_reorder).

    - multiple old_name under the same new_name -> weights summed, clamped to [0,1]
    - old_name matching the same name after strip(.NNN) is also handled
    - when allow_passthrough=True, vertex groups not in name_map are left as-is
    """
    if obj is None or obj.type != 'MESH' or not name_map:
        return {"renamed": 0, "merged": 0, "skipped": 0, "missing": []}
    return rename_and_reorder(
        obj, name_map,
        sort_renamed_numerically=False,
        untouched_to_end=False,
        save_backup=False,
        restore_backup=False,
    )


# ============================================================
# Three-stage mapping (used at export, R4 hooks into the exporter)
# ============================================================

def build_three_stage_map(profile, component_map) -> dict:
    """Compose {source_name: native_name}.

    source_name may be either an MMD name or already a unified name (PLAN §2.2 phase D step2).
    """
    mmd_to_unified = build_mmd_to_unified(profile)
    unified_to_native = build_unified_to_native(component_map)

    out = {}
    # sources already unified -> native
    for u, n in unified_to_native.items():
        out[u] = n
    # MMD source -> unified -> native
    for m, u in mmd_to_unified.items():
        n = unified_to_native.get(u)
        if n:
            out[m] = n
    return out


def apply_three_stage_rename(obj, profile, component_map) -> dict:
    """Called on the export side: rename the object's vertex groups to native names per the three-stage mapping."""
    name_map = build_three_stage_map(profile, component_map)
    return rename_vertex_groups_with_merge(obj, name_map, allow_passthrough=True)
