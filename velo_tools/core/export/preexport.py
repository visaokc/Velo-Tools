"""MMD 源物体导出预处理。

调用方应是「导出适配器」（efmi/wwmi）在调用游戏自带导出操作前执行的最后一步。
默认对源物体执行：
  1. 按 profile 把 MMD 顶点组改名为 unified（与「实际改名」算子同语义）。
  2. 删除特殊命名顶点组（mmd_edge_scale / mmd_vertex_order / UV_* 等）。
  3. 删除完全无权重的顶点组。
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
    """O(Verts) 单遍找出无任何权重的 VG 名。"""
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
    """对 obj（MMD 源物体）执行导出前预处理。

    返回统计 {renamed, merged, dropped_special, dropped_empty, missing}。
    幂等：多次调用结果稳定（已是 unified 名的不会再被改）。
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
