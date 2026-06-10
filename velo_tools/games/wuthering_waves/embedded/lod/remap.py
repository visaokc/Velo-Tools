# Pure remap math for the per-draw LOD export. No bpy / _wwmi_core imports:
# plain numpy + dicts, unit-testable outside Blender.
#
# Id spaces involved (see CONTEXT.md):
#   full local  -- component-local VG ids of the full object (per-draw bone palette)
#   full merged -- unified ids of the full object's merged skeleton (MERGED export)
#   lod local   -- component-local VG ids of the LOD object (its native per-draw palette)
#
# The exported Blend data references *full merged* ids. LOD draws are stateless
# per-draw overrides that keep the game's native bone constant buffers, so the
# LOD blend buffer must reference *lod component-LOCAL* ids. Chain per component c:
#   full merged --invert(full.vg_map[c])--> full local --lods.vg_map[c]--> lod local
#
# Inverting only component c's OWN vg_map is sufficient: get_merged_vg_map maps
# every local id of c to a merged id (including duplicates collapsed into other
# components' ranges), so every merged id legitimately reachable from c's
# vertices appears in c's own map. Ids outside that inverse are true
# user-painted cross-component weights — inexpressible in the LOD draw's native
# per-component skeleton (same situation as per_from_merged's stray weights).

import numpy


class LodRemapError(Exception):
    pass


def _to_int_map(mapping):
    """Normalizes a JSON-loaded {str|int: int} map to {int: int}; None stays None."""
    if mapping is None:
        return None
    return {int(k): int(v) for k, v in mapping.items()}


def build_inverse_vg_map(vg_map) -> dict:
    """Inverts one component's stock vg_map: {merged id: smallest full local id}.

    Smallest-local-wins on duplicates, mirroring per_from_merged.build_inverse_vg_map.
    """
    inverse = {}
    for local, merged in _to_int_map(vg_map).items():
        if merged not in inverse or local < inverse[merged]:
            inverse[merged] = local
    return inverse


def compose_full_to_lod_local(full_component_meta: dict, entry: dict) -> dict:
    """Composes ONE component's {full merged id -> LOD component-local id} map.

    Chain: invert(full vg_map) -> entry vg_map (matcher remap, None = identity).
    """
    inverse = build_inverse_vg_map(full_component_meta["vg_map"])
    full_to_lod = _to_int_map(entry.get("vg_map"))

    result = {}
    for merged, full_local in inverse.items():
        lod_local = full_to_lod.get(full_local) if full_to_lod is not None else full_local
        if lod_local is None:
            continue
        if lod_local >= 256:
            # LOD palettes are 8-bit per-draw by construction; this would mean
            # corrupt LOD metadata rather than a real skeleton.
            raise LodRemapError(
                f"LOD component-local VG id {lod_local} exceeds the 8-bit blend bound "
                f"(corrupt LOD metadata?)")
        result[merged] = lod_local
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


def remap_blend_component_local(true_vg_ids, weights, component_of_vertex, component_maps,
                                excluded_vertex_mask=None):
    """Builds the per-draw LOD blend ids: each vertex row holds its own
    component's LOD-local ids.

    Args:
        true_vg_ids: (N, K) int array of full merged ids (16-bit truth).
        weights: (N, K) weights array (zero weight = unused slot).
        component_of_vertex: (N,) component attribution (-1 = unreferenced).
        component_maps: {component_id: {full merged -> lod local}} for components
            that get LOD draws.
        excluded_vertex_mask: optional (N,) bool; True rows are excluded from
            the LOD path entirely (zero-filled, never error) — e.g. cross-scene
            split objects whose geometry does not exist in the LOD object.

    Returns (N, K) uint8. Rows of components without a map (and unreferenced
    vertices) are zeroed — they are never read by the LOD draws. A weighted
    slot that does not resolve means weights painted onto another component's
    bones -> LodRemapError (per-draw native skeletons cannot express them).
    """
    true_vg_ids = numpy.asarray(true_vg_ids, dtype=numpy.int64)
    weights = numpy.asarray(weights)
    component_of_vertex = numpy.asarray(component_of_vertex)

    lod_ids = numpy.zeros(true_vg_ids.shape, dtype=numpy.uint8)
    unresolved = {}

    for component_id, mapping in component_maps.items():
        rows = component_of_vertex == component_id
        if excluded_vertex_mask is not None:
            rows = rows & ~numpy.asarray(excluded_vertex_mask)
        if not rows.any():
            continue

        lookup_size = max(int(true_vg_ids[rows].max()) + 1,
                          max(mapping.keys(), default=0) + 1)
        lookup = numpy.full(lookup_size, -1, dtype=numpy.int32)
        if mapping:
            lookup[numpy.fromiter(mapping.keys(), dtype=numpy.int64)] = (
                numpy.fromiter(mapping.values(), dtype=numpy.int32))

        mapped = lookup[true_vg_ids[rows]]
        bad = (mapped < 0) & (weights[rows] > 0)
        if bad.any():
            bad_ids = numpy.unique(true_vg_ids[rows][bad]).tolist()
            unresolved[component_id] = bad_ids
            continue

        mapped[mapped < 0] = 0
        lod_ids[rows] = mapped.astype(numpy.uint8)

    if unresolved:
        details = '; '.join(
            f"Component{component_id}: merged VG ids {ids}"
            for component_id, ids in sorted(unresolved.items()))
        raise LodRemapError(
            f"Weights painted onto another component's bones cannot be expressed in the "
            f"LOD draw's native per-component skeleton ({details}). Move those weights "
            f"back to the component's own bones or zero them "
            f"(cf. Per-Component (from Merged) stray-weight rules).")

    return lod_ids
