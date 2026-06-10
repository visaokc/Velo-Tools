# Pure map-composition and compaction math for the LOD export hook.
#
# No bpy / _wwmi_core imports: everything here is plain numpy + dicts so it
# can be unit-tested outside Blender.
#
# Id spaces involved (see CONTEXT.md):
#   full local  -- component-local VG ids of the full object (8-bit blend data)
#   full merged -- unified ids of the full object's merged skeleton
#   lod local   -- component-local VG ids of the LOD object
#   lod merged  -- unified ids of the LOD object's merged skeleton
#
# The exported Blend data references *full merged* ids; LOD draws build their
# own merged skeleton (per-LOD SkeletonMerger mirror), so the LOD blend buffer
# must reference *lod merged* ids. The chain per component c (field names per
# the EFMI-aligned per-component lods entry):
#   full merged --invert(full.vg_map[c])--> full local
#               --lods.vg_map[c]--> lod local
#               --lods.lod_vg_map[c]--> lod merged

import numpy


class LodRemapError(Exception):
    pass


def _to_int_map(mapping):
    """Normalizes a JSON-loaded {str|int: int} map to {int: int}; None stays None."""
    if mapping is None:
        return None
    return {int(k): int(v) for k, v in mapping.items()}


def compose_full_to_lod_merged(full_components_meta, entry_components) -> dict:
    """Composes the global full-merged -> lod-merged VG id map.

    Args:
        full_components_meta: stock Metadata.json "components" list of the full
            object (vg_offset / vg_count / vg_map per component).
        entry_components: index-aligned list of per-component lods entries for
            ONE LOD object (None = component has no entry for it); each entry
            carries vg_map (full local -> lod local matcher remap, None =
            identity) and lod_vg_map (lod local -> lod merged).

    A full merged id may be reachable through several components (WWMI dedups
    identical bones across components); the chain of the component that *owns*
    the id (vg_offset <= id < vg_offset + vg_count) wins.
    """
    result = {}
    owner_resolved = set()

    for component_id, full_meta in enumerate(full_components_meta):
        if component_id >= len(entry_components):
            break
        entry = entry_components[component_id]
        if entry is None:
            continue

        full_map = _to_int_map(full_meta["vg_map"])
        full_to_lod = _to_int_map(entry.get("vg_map"))
        lod_map = _to_int_map(entry["lod_vg_map"])

        vg_offset = full_meta["vg_offset"]
        vg_count = full_meta["vg_count"]

        for full_local, full_merged in full_map.items():
            lod_local = full_to_lod.get(full_local) if full_to_lod is not None else full_local
            if lod_local is None:
                continue
            lod_merged = lod_map.get(lod_local)
            if lod_merged is None:
                continue
            if vg_offset <= full_merged < vg_offset + vg_count:
                result[full_merged] = lod_merged
                owner_resolved.add(full_merged)
            elif full_merged not in owner_resolved and full_merged not in result:
                result[full_merged] = lod_merged

    return result


def build_vertex_component_map(index_data, index_layout, vertex_count) -> numpy.ndarray:
    """Maps every vertex id to its component id (-1 = not referenced).

    Same index-segment attribution as stock build_blend_remap: component c owns
    the vertices referenced by its index_layout[c] indices.
    """
    component_of_vertex = numpy.full(vertex_count, -1, dtype=numpy.int32)
    index_offset = 0
    for component_id, index_count in enumerate(index_layout):
        if index_count == 0:
            continue
        vertex_ids = numpy.unique(index_data[index_offset:index_offset + index_count])
        component_of_vertex[vertex_ids] = component_id
        index_offset += index_count
    return component_of_vertex


def remap_blend(true_vg_ids, weights, compose, relevant_vertex_mask=None):
    """Applies the composed map to per-vertex VG ids.

    Args:
        true_vg_ids: (N, K) int array of full merged ids (16-bit truth).
        weights: (N, K) weights array (zero weight = unused slot).
        compose: full-merged -> lod-merged dict.
        relevant_vertex_mask: optional (N,) bool; only these vertices must
            resolve (vertices of unmatched components are zero-filled).

    Returns (N, K) int32 lod merged ids. Raises LodRemapError when a used
    (weight > 0) id of a relevant vertex has no mapping.
    """
    true_vg_ids = numpy.asarray(true_vg_ids, dtype=numpy.int64)
    lookup_size = max(int(true_vg_ids.max()) + 1, max(compose.keys(), default=0) + 1)
    lookup = numpy.full(lookup_size, -1, dtype=numpy.int32)
    if compose:
        lookup[numpy.fromiter(compose.keys(), dtype=numpy.int64)] = (
            numpy.fromiter(compose.values(), dtype=numpy.int32))

    lod_ids = lookup[true_vg_ids]

    unresolved = (lod_ids < 0) & (numpy.asarray(weights) > 0)
    if relevant_vertex_mask is not None:
        unresolved &= numpy.asarray(relevant_vertex_mask)[:, None]

    if unresolved.any():
        bad_ids = numpy.unique(true_vg_ids[unresolved]).tolist()
        raise LodRemapError(
            f"Failed to remap {len(bad_ids)} merged VG id(s) to the LOD skeleton: {bad_ids}. "
            f"These bones belong to components without LOD match (or weights painted onto "
            f"an unmatched component's bones). Re-run Extract LOD Data or fix the weights."
        )

    lod_ids[lod_ids < 0] = 0
    return lod_ids


def plan_component_compaction(lod_ids, weights, index_data, index_layout, component_ids):
    """Plans per-component 8-bit compaction for the stock SkeletonRemapper mirror.

    For each component in component_ids whose used lod-merged ids exceed 255,
    builds a compact id space (sorted used ids -> 0..n-1) plus the 512-entry
    forward map (forward[i] = lod merged id) consumed by SkeletonRemapper.

    Returns {component_id: plan} where plan is:
        {"needs_compaction": bool,
         "vg_count": int,              # used id count (compacted only)
         "forward": (512,) uint16,     # compacted only
         "compact_lookup": (M,) int32} # lod merged id -> compact id (compacted only)
    Raises LodRemapError if a component uses more than 256 ids (cannot fit 8-bit).
    """
    plans = {}
    index_offset = 0
    offsets = {}
    for component_id, index_count in enumerate(index_layout):
        offsets[component_id] = (index_offset, index_count)
        index_offset += index_count

    for component_id in component_ids:
        index_offset, index_count = offsets[component_id]
        if index_count == 0:
            continue

        vertex_ids = numpy.unique(index_data[index_offset:index_offset + index_count])
        component_vg_ids = lod_ids[vertex_ids].flatten()
        component_weights = numpy.asarray(weights)[vertex_ids].flatten()

        used_ids = numpy.unique(component_vg_ids[component_weights > 0])
        if used_ids.size == 0 or int(used_ids.max()) < 256:
            plans[component_id] = {"needs_compaction": False}
            continue

        if used_ids.size > 256:
            raise LodRemapError(
                f"Component{component_id} references {used_ids.size} LOD skeleton bones, "
                f"exceeding the 256 VG limit of the 8-bit blend buffer.")

        forward = numpy.zeros(512, dtype=numpy.uint16)
        forward[numpy.arange(used_ids.size)] = used_ids

        compact_lookup = numpy.zeros(int(used_ids.max()) + 1, dtype=numpy.int32)
        compact_lookup[used_ids] = numpy.arange(used_ids.size)

        plans[component_id] = {
            "needs_compaction": True,
            "vg_count": int(used_ids.size),
            "forward": forward,
            "compact_lookup": compact_lookup,
            "vertex_ids": vertex_ids,
        }

    return plans


def compact_component_ids(lod_ids, plan) -> numpy.ndarray:
    """Returns a copy of lod_ids where the plan's component vertices use compact ids."""
    compacted = lod_ids.copy()
    vertex_ids = plan["vertex_ids"]
    lookup = plan["compact_lookup"]
    rows = compacted[vertex_ids]
    # Ids beyond the lookup belong to other components' slots within shared
    # vertices and are never read by this component's draws; clamp to 0.
    rows = numpy.where(rows < len(lookup), rows, 0)
    compacted[vertex_ids] = lookup[rows]
    return compacted
