"""Generic vertex-group filtering utilities (V0.1.1).

Shared across MMD mapping, generic vertex-group renaming, and similar flows:
- Skip MMD model special vertex groups (mmd_edge_scale / mmd_vertex_order / UV_*)
- Skip vertex groups that have no actual vertex weights
"""

from __future__ import annotations

# Exact-match special names (lowercased; prefixes also included for comparison)
_SPECIAL_EXACT = {"mmd_edge_scale", "mmd_vertex_order"}
# Prefix match
_SPECIAL_PREFIXES = ("uv_",)


def is_special_vg_name(name: str) -> bool:
    """Whether this is a special vertex group that should NOT take part in matching/renaming."""
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
    """Single-pass scan of the mesh; returns the set of vertex-group indices that actually own a weight>0 vertex.

    Faster than per-vertex-group .weight() calls: iterate me.vertices once, and for
    each vertex iterate the g.groups it holds.
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
    """Yields (vg_index, vg_name) — special-named and weightless vertex groups already removed."""
    if obj is None or obj.type != 'MESH':
        return
    weighted = collect_weighted_vg_indices(obj)
    for vg in obj.vertex_groups:
        if vg.index not in weighted:
            continue
        if is_special_vg_name(vg.name):
            continue
        yield vg.index, vg.name
