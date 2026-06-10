# "Extract LOD Data" pipeline for WWMI (velo driver layer).
#
# Mirrors the EFMI extract_lods flow: run the stock WWMI extraction chain on a
# LOD frame dump (in memory, nothing written to disk), match the resulting
# candidate objects against the already-extracted full object, then persist
# each match as a per-component "lods" entry nested inside the stock
# components[] dicts (EFMI-style, appended right after the stock vg_map key;
# unmatched components get no entry). The merge is JSON-level on purpose:
# round-tripping through _wwmi_core's dataclasses would silently drop unknown
# keys.

import json
import re

from pathlib import Path

from ..._wwmi_core.migoto_io.blender_interface.utility import resolve_path
from ..._wwmi_core.migoto_io.dump_parser.dump_parser import Dump
from ..._wwmi_core.migoto_io.dump_parser.data_collector import DataCollector
from ..._wwmi_core.extract_frame_data.extract_frame_data import configuration
from ..._wwmi_core.extract_frame_data.data_extractor import DataExtractor
from ..._wwmi_core.extract_frame_data.shapekey_builder import ShapeKeyBuilder
from ..._wwmi_core.extract_frame_data.component_builder import ComponentBuilder
from ..._wwmi_core.extract_frame_data.output_builder import OutputBuilder, TextureFilter

from . import model
from .matcher import LODMatcher, GeometryMatcherConfig, GeometryMatcherMethod


class LodExtractError(Exception):
    pass


class DuplicateLodDataError(LodExtractError):
    pass


def extract_candidate_objects(dump_path: Path) -> list:
    """Runs the stock WWMI extraction chain on a dump, fully in memory.

    Same 6-step chain as _wwmi_core.extract_frame_data.extract_frame_data(),
    minus write_objects: candidates never touch the disk. Textures are
    hard-skipped via an impossible min_file_size (the size check runs before
    the expensive per-file sha256 read in OutputBuilder.filter_textures);
    their hashes are captured beforehand for the lods metadata.
    """
    dump = Dump(dump_directory=dump_path)

    frame_data = DataCollector(
        dump=dump,
        shader_data_pattern=configuration.shader_data_pattern,
        shader_resources=configuration.shader_resources,
    )

    data_extractor = DataExtractor(call_branches=frame_data.call_branches)

    shapekeys = ShapeKeyBuilder(shapekey_data=data_extractor.shape_key_data)

    component_builder = ComponentBuilder(
        output_vb_layout=configuration.output_vb_layout,
        shader_hashes=data_extractor.shader_hashes,
        shapekeys=shapekeys.shapekeys,
        draw_data=data_extractor.draw_data,
    )

    # Capture texture hashes per component before OutputBuilder.filter_textures
    # drops them all.
    texture_hashes = {
        vb0_hash: [
            sorted({texture.hash for texture in component.textures.values()})
            for component in mesh_object.components
        ]
        for vb0_hash, mesh_object in component_builder.mesh_objects.items()
    }

    output_builder = OutputBuilder(
        shapekeys=shapekeys.shapekeys,
        mesh_objects=component_builder.mesh_objects,
        texture_filter=TextureFilter(
            min_file_size=2**60,
            exclude_extensions=[],
            exclude_same_slot_hash_textures=False,
            exclude_hashes=[],
        ),
    )

    return [
        model.from_output_object(vb0_hash, object_data, texture_hashes.get(vb0_hash, []))
        for vb0_hash, object_data in output_builder.objects.items()
    ]


def build_matcher(lod_cfg) -> LODMatcher:
    """Builds a LODMatcher from the velo LOD settings (mirrors EFMI's match_lods plumbing)."""
    error_threshold = (
        lod_cfg.geo_matcher_voxel_error_threshold
        if lod_cfg.geo_matcher_method == 'VOXEL'
        else lod_cfg.geo_matcher_error_threshold
    )
    return LODMatcher(
        component_min_vertex_count=(
            lod_cfg.skip_component_below_vertex_count
            if lod_cfg.skip_component_below_vertex_count_enabled else 0
        ),
        object_hash_blacklist=(
            lod_cfg.skip_object_hashes
            if lod_cfg.skip_object_hashes_enabled else ""
        ),
        object_similarity_threshold=error_threshold,
        component_similarity_threshold=error_threshold,
        skip_components_below_similarity_threshold=lod_cfg.skip_lods_below_error_threshold,
        geo_matcher_main_config=GeometryMatcherConfig(
            method=GeometryMatcherMethod(lod_cfg.geo_matcher_method),
            sensitivity=lod_cfg.geo_matcher_sensivity,
            voxel_size=lod_cfg.geo_matcher_voxel_size,
            samples_count=lod_cfg.geo_matcher_sample_size,
        ),
        geo_matcher_prefilter_config=GeometryMatcherConfig(
            method=GeometryMatcherMethod(lod_cfg.geo_matcher_method),
            sensitivity=lod_cfg.geo_matcher_sensivity,
            voxel_size=lod_cfg.geo_matcher_prefilter_voxel_size,
            samples_count=lod_cfg.geo_matcher_prefilter_sample_size,
        ),
        geo_matcher_prefilter_candidates_count=lod_cfg.geo_matcher_prefilter_candidates_count,
        vg_matcher_candidates_count=lod_cfg.vg_matcher_candidates_count,
    )


def _match_type_for(full_component, lod_component) -> str:
    if lod_component.content_hash == full_component.content_hash:
        return "hash"
    found = re.search(r"match=([0-9.]+)%", lod_component.mesh_name)
    if found:
        return f"geometry:{found.group(1)}"
    return "geometry"


def build_component_lod_entry(full_component, lod_component, vg_map, lod_object) -> dict:
    """Builds one per-component lods entry (EFMI ExtractedObjectComponentLOD-aligned).

    Field semantics: `vg_map` is the matcher remap (full component-local ->
    LOD component-local, None = identity), matching EFMI's per-component LOD
    vg_map; `lod_vg_map` is the LOD component's own stock map (LOD local ->
    LOD merged) needed to compose the merged-id chain at export time.
    """
    lod_meta = lod_component.meta
    return {
        "lod_object_name": lod_object.id,
        "vb0_hash": lod_object.meta["vb0_hash"],
        "cb4_hash": lod_object.meta["cb4_hash"],
        "vertex_offset": lod_meta["vertex_offset"],
        "vertex_count": lod_meta["vertex_count"],
        "index_offset": lod_meta["index_offset"],
        "index_count": lod_meta["index_count"],
        "vg_offset": lod_meta["vg_offset"],
        "vg_count": lod_meta["vg_count"],
        "vg_map": vg_map,
        "lod_vg_map": lod_meta["vg_map"],
        "shapekeys_offsets_hash": (lod_object.meta.get("shapekeys") or {}).get("offsets_hash", ""),
        "texture_hashes": lod_component.texture_hashes,
        "match_type": _match_type_for(full_component, lod_component),
        "lod_component_index": lod_component.index,
    }


def merge_component_lods(metadata: dict, lod_object_name: str, component_entries: dict,
                         allow_overwrite: bool) -> dict:
    """Merges per-component lods entries into a raw Metadata.json dict.

    component_entries maps full component index -> entry (matched components
    only). Duplicate semantics mirror EFMI's import_lod_metadata, lifted to
    the object level: any existing entry with the same lod_object_name on any
    component requires the overwrite flag (checked before any mutation).
    """
    components_meta = metadata["components"]

    if not allow_overwrite:
        for component_meta in components_meta:
            for lod_entry in component_meta.get("lods") or []:
                if lod_entry.get("lod_object_name") == lod_object_name:
                    raise DuplicateLodDataError(
                        f"LOD data for object {lod_object_name} already exists in Metadata.json! "
                        f"Enable 'Allow LoD Data Overwrite' to replace it."
                    )

    for component_id, component_meta in enumerate(components_meta):
        # Drop stale same-name entries everywhere (incl. components no longer matched).
        lods = [
            lod_entry for lod_entry in (component_meta.get("lods") or [])
            if lod_entry.get("lod_object_name") != lod_object_name
        ]
        entry = component_entries.get(component_id)
        if entry is not None:
            lods.append(entry)
        # Highest-resolution LOD first, same per-component ordering as EFMI.
        lods.sort(key=lambda lod_entry: lod_entry.get("vertex_count", 0), reverse=True)
        if lods:
            # First-time assignment appends right after the stock vg_map key;
            # re-assignment keeps the existing position.
            component_meta["lods"] = lods
        else:
            component_meta.pop("lods", None)

    # Silently retire the legacy top-level container (entries are regenerated).
    metadata.pop("lods", None)
    return metadata


def run_extract_lod_data(context) -> dict:
    """Operator entry point. Returns a summary dict for the UI report."""
    lod_cfg = context.scene.vtww_lod_settings
    wwmi_cfg = context.scene.VTWW_settings

    dump_path = resolve_path(lod_cfg.lod_frame_dump_folder)
    if not dump_path.is_dir():
        raise LodExtractError("Specified LOD frame dump folder does not exist!")
    if not (dump_path / 'log.txt').is_file():
        raise LodExtractError("Specified LOD frame dump folder is missing log.txt file!")

    source_folder = resolve_path(wwmi_cfg.object_source_folder)
    metadata_path = source_folder / 'Metadata.json'
    if not metadata_path.is_file():
        raise LodExtractError("Object Sources folder is missing Metadata.json! Extract the full object first.")

    full_object = model.load_full_object(source_folder)

    candidates = extract_candidate_objects(dump_path)
    print(f"Extracted {len(candidates)} LOD candidate objects from dump: "
          f"{', '.join(candidate.id for candidate in candidates)}")

    matcher = build_matcher(lod_cfg)
    lod_object, matched_components = matcher.find_matching_lods(full_object, candidates)

    component_entries = {
        full_component.index: build_component_lod_entry(full_component, lod_component, vg_map, lod_object)
        for full_component, (lod_component, vg_map) in matched_components.items()
    }
    metadata = merge_component_lods(full_object.meta, lod_object.id, component_entries,
                                    lod_cfg.allow_lod_overwrite)

    with open(metadata_path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(metadata, indent=4))

    matched_count = len(matched_components)
    full_mesh_count = sum(
        1 for full_component, (lod_component, _) in matched_components.items()
        if lod_component.content_hash == full_component.content_hash
    )

    return {
        "lod_object_name": lod_object.id,
        "matched_components": matched_count,
        "full_mesh_components": full_mesh_count,
        "total_components": len(full_object.components),
    }
