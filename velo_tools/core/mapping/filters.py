"""通用顶点组过滤工具（V0.1.1）。

供 MMD 映射、通用顶点组改名等流程共用：
- 跳过 MMD 模型的特殊顶点组（mmd_edge_scale / mmd_vertex_order / UV_*）
- 跳过实际无任何顶点权重的顶点组
"""

from __future__ import annotations

# 完全匹配的特殊名（小写比对前缀也包括）
_SPECIAL_EXACT = {"mmd_edge_scale", "mmd_vertex_order"}
# 前缀匹配
_SPECIAL_PREFIXES = ("uv_",)


def is_special_vg_name(name: str) -> bool:
    """是否属于「不应参与匹配/改名」的特殊顶点组。"""
    if not name:
        return True
    n = name.strip().lower()
    if n in _SPECIAL_EXACT:
        return True
    for p in _SPECIAL_PREFIXES:
        if n.startswith(p):
            return True
    return False


def collect_weighted_vg_indices(obj) -> set:
    """单遍扫描网格，返回「真实拥有 weight>0 顶点」的顶点组 index 集合。

    比对逐顶点组 .weight() 调用要快：只遍历 me.vertices 一次，每个顶点
    遍历它持有的 g.groups 即可。
    """
    out = set()
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return out
    for v in obj.data.vertices:
        for g in v.groups:
            if g.weight > 0.0:
                out.add(g.group)
    return out


def usable_vertex_groups(obj):
    """生成 (vg_index, vg_name) — 已剔除特殊命名 + 无实际权重的顶点组。"""
    if obj is None or obj.type != 'MESH':
        return
    weighted = collect_weighted_vg_indices(obj)
    for vg in obj.vertex_groups:
        if vg.index not in weighted:
            continue
        if is_special_vg_name(vg.name):
            continue
        yield vg.index, vg.name
