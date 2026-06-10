# Cross-scene LOD metadata helpers (pure dicts, stdlib only — unit-testable
# outside Blender, same loading pattern as remap.py).
#
# The producer (xscene_merge.py) merges per-IB "lods" entries into the merged
# root Metadata.json, mirroring the automatic vg merge feel:
#   - foldable dungeon IBs: entries are translated to BASE component numbers
#     via fold.comp_map, and the vg chain is PRE-COMPOSED into base space:
#       base local --fold.vg_remap--> IB local --entry.vg_map--> LOD local
#     The body export (COMPONENT runtime, blend already base-local) then
#     consumes these entries with the stock LOD hook math unchanged.
#   - editable IBs: entries shift to the merged component numbers unchanged
#     (informational: their actual consumption happens in their own sub-export
#     against scene_ibs/<hash>/, whose metadata keeps the IB-local entries).
#   - own-buffer (non-foldable scene) IBs: entries stay in scene_ibs metadata
#     only; their LOD sections come from their own sub-export.


def compose_lod_entry_for_base(entry: dict, base_vg_remap) -> dict:
    """Rewrites one IB-side lods entry into base-component vg space.

    entry["vg_map"]: IB local -> LOD local (None = identity skeletons).
    base_vg_remap:   base local -> IB local (None = naturally aligned fold).
    Composed result: base local -> LOD local (or carried-over None when both
    sides are identity).
    """
    composed_entry = dict(entry)
    if base_vg_remap is None:
        # base local == IB local; the entry applies to base space as-is.
        return composed_entry

    ib_to_lod = entry.get("vg_map")
    composed = {}
    for base_local, ib_local in base_vg_remap.items():
        if ib_to_lod is not None:
            lod_local = ib_to_lod.get(str(ib_local))
            if lod_local is None:
                # IB local id absent from the matcher map; a weighted use
                # surfaces as a clear export-time error (stray-weight policy).
                continue
        else:
            lod_local = int(ib_local)
        composed[str(base_local)] = int(lod_local)
    composed_entry["vg_map"] = composed
    return composed_entry


def _merge_entry(component_meta: dict, entry: dict):
    """Same per-component merge semantics as the single-IB extract flow:
    same lod_object_name overwrites, list sorted by vertex_count desc."""
    lods = [
        existing for existing in (component_meta.get("lods") or [])
        if existing.get("lod_object_name") != entry.get("lod_object_name")
    ]
    lods.append(entry)
    lods.sort(key=lambda lod_entry: lod_entry.get("vertex_count", 0), reverse=True)
    component_meta["lods"] = lods


def merge_lods_into_base(merged_meta: dict, routing: dict, scene_metas: dict) -> int:
    """Merges per-IB lods entries into the merged root Metadata.json dict.

    Args:
        merged_meta: the merged folder's Metadata.json (mutated in place).
        routing: the CrossSceneRouting.json dict (fold comp_map/vg_remap,
            editable merged/local component lists).
        scene_metas: {ib_hash: parsed scene_ibs/<hash>/Metadata.json}.

    Returns the number of entries merged (0 = nothing to write back).
    """
    components = merged_meta.get("components") or []
    merged_count = 0

    # Foldable dungeon IBs -> base components, vg chain pre-composed.
    for scene in routing.get("scene_ibs") or []:
        fold = scene.get("fold")
        if not scene.get("foldable") or not fold:
            continue
        ib_meta = scene_metas.get(scene.get("ib_hash")) or {}
        ib_components = ib_meta.get("components") or []
        comp_map = fold.get("comp_map") or {}
        vg_remap = fold.get("vg_remap") or {}
        for ib_comp_str, base_comp in comp_map.items():
            ib_comp = int(ib_comp_str)
            base_comp = int(base_comp)
            if ib_comp >= len(ib_components) or base_comp >= len(components):
                continue
            base_remap = vg_remap.get(str(base_comp))
            for entry in (ib_components[ib_comp].get("lods") or []):
                _merge_entry(components[base_comp],
                             compose_lod_entry_for_base(entry, base_remap))
                merged_count += 1

    # Editable IBs -> merged component numbers, entries unchanged.
    for record in routing.get("editable_ibs") or []:
        ib_meta = scene_metas.get(record.get("ib_hash")) or {}
        ib_components = ib_meta.get("components") or []
        for local_index, merged_index in zip(record.get("local_components") or [],
                                             record.get("merged_components") or []):
            if local_index >= len(ib_components) or merged_index >= len(components):
                continue
            for entry in (ib_components[local_index].get("lods") or []):
                _merge_entry(components[merged_index], dict(entry))
                merged_count += 1

    return merged_count
