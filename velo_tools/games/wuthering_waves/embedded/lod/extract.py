# "Extract LOD Data" pipeline for WWMI (velo driver layer).
#
# Mirrors the EFMI extract_lods flow: run the stock WWMI extraction chain on a
# LOD frame dump (in memory, nothing written to disk), match the resulting
# candidate objects against the already-extracted full object, then persist
# the match as a velo-owned top-level "lods" key inside the full object's
# Metadata.json. The merge is JSON-level on purpose: round-tripping through
# _wwmi_core's dataclasses would silently drop unknown keys.

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

    # Capture texture hashes before OutputBuilder.filter_textures drops them all.
    texture_hashes = {
        vb0_hash: sorted({
            texture.hash
            for component in mesh_object.components
            for texture in component.textures.values()
        })
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


def build_lods_entry(full_object, lod_object, matched_components) -> dict:
    """Builds one velo "lods" entry (index-aligned with the full object's components)."""
    components = []
    for full_component in full_object.components:
        lod_component, vg_map = matched_components.get(full_component, (None, None))
        if lod_component is None:
            components.append({
                "matched": False,
                "match_type": "unmatched",
                "lod_component_index": None,
            })
            continue
        lod_meta = lod_component.meta
        components.append({
            "matched": True,
            "match_type": _match_type_for(full_component, lod_component),
            "lod_component_index": lod_component.index,
            "vertex_offset": lod_meta["vertex_offset"],
            "vertex_count": lod_meta["vertex_count"],
            "index_offset": lod_meta["index_offset"],
            "index_count": lod_meta["index_count"],
            "vg_offset": lod_meta["vg_offset"],
            "vg_count": lod_meta["vg_count"],
            # LOD component-local -> LOD merged ids (copied from the LOD object's metadata).
            "vg_map": lod_meta["vg_map"],
            # Matcher output: full component-local -> LOD component-local; None == identity.
            "full_to_lod_vg_map": vg_map,
        })

    return {
        "lod_object_name": lod_object.id,
        "vb0_hash": lod_object.meta["vb0_hash"],
        "cb4_hash": lod_object.meta["cb4_hash"],
        "vertex_count": lod_object.meta["vertex_count"],
        "index_count": lod_object.meta["index_count"],
        "shapekeys_offsets_hash": (lod_object.meta.get("shapekeys") or {}).get("offsets_hash", ""),
        "texture_hashes": lod_object.texture_hashes,
        "components": components,
    }


def merge_lods_entry(metadata: dict, entry: dict, allow_overwrite: bool) -> dict:
    """Merges a lods entry into a raw Metadata.json dict (returns the same dict)."""
    lods = metadata.get("lods") or []

    existing = [i for i, lod in enumerate(lods) if lod.get("lod_object_name") == entry["lod_object_name"]]
    if existing:
        if not allow_overwrite:
            raise DuplicateLodDataError(
                f"LOD data for object {entry['lod_object_name']} already exists in Metadata.json! "
                f"Enable 'Allow LoD Data Overwrite' to replace it."
            )
        for i in reversed(existing):
            del lods[i]

    lods.append(entry)
    # Highest-resolution LOD first (LOD1 = largest), same ordering as EFMI.
    lods.sort(key=lambda lod: lod.get("vertex_count", 0), reverse=True)

    metadata["lods"] = lods
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

    entry = build_lods_entry(full_object, lod_object, matched_components)
    metadata = merge_lods_entry(full_object.meta, entry, lod_cfg.allow_lod_overwrite)

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
