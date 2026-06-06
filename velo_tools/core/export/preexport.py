"""MMD source object pre-export processing.

The caller should be the "export adapter" (efmi/wwmi), running this as the last step
before invoking the game's built-in export operator.
By default, performs the following on the source object:
  1. Rename MMD vertex groups to unified per the profile (same semantics as the "actual rename" operator).
  2. Remove specially named vertex groups (mmd_edge_scale / mmd_vertex_order / UV_* etc.).
  3. Remove vertex groups with no weight at all.
"""
from __future__ import annotations

from ..mapping import algorithms as _algo
from ..mapping.filters import is_special_vg_name


def _vg_has_any_weight(obj, vg_index: int) -> bool:
    if obj is None or obj.data is None:
        return False
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == vg_index and g.weight > 0.0:
                return True
    return False


def _collect_empty_vg_names(obj) -> list:
    """Single O(Verts) pass to find names of VGs with no weight at all."""
    if obj is None or obj.data is None:
        return []
    have = set()
    for v in obj.data.vertices:
        for g in v.groups:
            if g.weight > 0.0:
                have.add(g.group)
    return [vg.name for vg in obj.vertex_groups if vg.index not in have]


def apply_mmd_pre_export(
    obj,
    profile,
    *,
    drop_special: bool = True,
    drop_empty: bool = True,
    rename_to_unified: bool = True,
) -> dict:
    """Run pre-export processing on obj (the MMD source object).

    Returns stats {renamed, merged, dropped_special, dropped_empty, missing}.
    Idempotent: repeated calls give stable results (names already unified are not changed again).
    """
    report = {
        "renamed": 0,
        "merged": 0,
        "dropped_special": 0,
        "dropped_empty": 0,
        "missing": [],
    }
    if obj is None or obj.type != 'MESH':
        return report

    if rename_to_unified and profile is not None:
        name_map = _algo.build_mmd_to_unified(profile)
        if name_map:
            r = _algo.rename_and_reorder(
                obj, name_map,
                sort_renamed_numerically=True,
                untouched_to_end=True,
                save_backup=True,
                restore_backup=False,
            )
            report["renamed"] = r.get("renamed", 0)
            report["merged"] = r.get("merged", 0)
            report["missing"] = list(r.get("missing", []))

    if drop_special:
        for vg in [vg for vg in obj.vertex_groups if is_special_vg_name(vg.name)]:
            try:
                obj.vertex_groups.remove(vg)
                report["dropped_special"] += 1
            except Exception:
                pass

    if drop_empty:
        for name in _collect_empty_vg_names(obj):
            vg = obj.vertex_groups.get(name)
            if vg is not None:
                try:
                    obj.vertex_groups.remove(vg)
                    report["dropped_empty"] += 1
                except Exception:
                    pass

    return report
