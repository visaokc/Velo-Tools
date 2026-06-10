# "Merge form dump" pipeline for the WWMI slot-style texture layer.
#
# A multi-form character binds different texture sets (and partially different
# material PS) per form, so exact per-form slot maps require one frame dump
# per form (data-determined, see ADR 0006). This module consumes a RAW
# FrameAnalysis folder of an extra form directly — no second object extraction
# is needed: it runs the stock in-memory extraction chain up to
# ComponentBuilder (the same five steps extract_frame_data() runs before
# OutputBuilder; mirrors embedded/lod/extract.py), picks the object matching
# the already-extracted one (vb0 hash first, skeleton cb4 hash as fallback)
# and persists its per-(component x shader-pair x slot) texture maps into the
# ShaderTextureUsageForms.json sidecar next to Metadata.json.
#
# Unlike the base ShaderTextureUsage.json (which only seats textures surviving
# the extraction texture filter), the sidecar keeps ALL bound descriptors —
# more coverage costs nothing here because no texture files are written.

import json

from collections import OrderedDict
from pathlib import Path

from ..._wwmi_core.migoto_io.dump_parser.dump_parser import Dump
from ..._wwmi_core.migoto_io.dump_parser.data_collector import DataCollector
from ..._wwmi_core.migoto_io.dump_parser.filename_parser import ShaderType
from ..._wwmi_core.extract_frame_data.extract_frame_data import configuration
from ..._wwmi_core.extract_frame_data.data_extractor import DataExtractor
from ..._wwmi_core.extract_frame_data.shapekey_builder import ShapeKeyBuilder
from ..._wwmi_core.extract_frame_data.component_builder import ComponentBuilder

from . import constants


class FormMergeError(Exception):
    pass


def _pair_key(descriptor) -> str:
    """Same stable "vs=<hash>-ps=<hash>" key as _shader_texture_usage.py."""
    vs = next((s for s in descriptor.shaders if s.type is ShaderType.Vertex), None)
    ps = next((s for s in descriptor.shaders if s.type is ShaderType.Pixel), None)
    vs_part = vs.raw if vs is not None else 'vs=?'
    ps_part = ps.raw if ps is not None else 'ps=?'
    return f'{vs_part}-{ps_part}'


def collect_mesh_objects(dump_path: Path):
    """Stock in-memory extraction chain, stopped at ComponentBuilder (we need
    the full per-draw texture descriptors, which OutputBuilder would filter)."""
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
    return component_builder.mesh_objects


def _match_object(mesh_objects, vb0_hash: str, cb4_hash: str):
    found = mesh_objects.get(vb0_hash)
    if found is not None:
        return found, 'vb0'
    candidates = [obj for obj in mesh_objects.values() if obj.cb4_hash == cb4_hash]
    if len(candidates) == 1:
        return candidates[0], 'cb4'
    if not candidates:
        raise FormMergeError(
            f'no object in the dump matches vb0 {vb0_hash} or skeleton cb4 {cb4_hash} '
            f'(dump objects: {", ".join(sorted(mesh_objects))})')
    raise FormMergeError(
        f'vb0 {vb0_hash} not found and skeleton cb4 {cb4_hash} matches '
        f'{len(candidates)} objects - cannot pick the form object unambiguously')


def _build_components_usage(mesh_object) -> "OrderedDict[str, OrderedDict]":
    """Component N -> "vs=..-ps=.." -> "ps-tN" -> hash (sidecar shape; same
    layout as ShaderTextureUsage.json but unfiltered)."""
    out = OrderedDict()
    for component_id, component_data in enumerate(mesh_object.components_data):
        pairs = {}
        for descriptor in component_data.draw_data.textures:
            slot = descriptor.get_slot()
            pair = pairs.setdefault(_pair_key(descriptor), {})
            if slot in pair and pair[slot] != descriptor.hash:
                # Conflicting bindings for the same (pair, slot) within one
                # frame: multi-state variant, mark unknown (generator skips).
                pair[slot] = None
            else:
                pair[slot] = descriptor.hash
        component_out = OrderedDict()
        for pair in sorted(pairs):
            component_out[pair] = OrderedDict(sorted(pairs[pair].items()))
        out[f'Component {component_id}'] = component_out
    return out


def merge_form_dump(object_source_folder, dump_path, form_label: str = '') -> dict:
    """Parses one extra-form RAW dump and merges it into the sidecar.

    Returns a summary dict for operator reporting."""
    object_source_folder = Path(object_source_folder)
    dump_path = Path(dump_path)

    if not (dump_path / 'log.txt').is_file():
        raise FormMergeError('selected folder is not a frame dump (log.txt missing)')

    metadata_path = object_source_folder / 'Metadata.json'
    if not metadata_path.is_file():
        raise FormMergeError('object source folder is missing Metadata.json')
    with open(metadata_path, encoding='utf-8') as f:
        metadata = json.load(f)
    vb0_hash = metadata.get('vb0_hash')
    cb4_hash = metadata.get('cb4_hash')
    if not vb0_hash:
        raise FormMergeError('Metadata.json carries no vb0_hash')

    base_components = metadata.get('components') or []

    mesh_objects = collect_mesh_objects(dump_path)
    mesh_object, matched_by = _match_object(mesh_objects, vb0_hash, cb4_hash)

    if base_components and len(mesh_object.components_data) != len(base_components):
        raise FormMergeError(
            f'component count mismatch: dump object has '
            f'{len(mesh_object.components_data)}, extracted object has '
            f'{len(base_components)} - forms must share the same mesh')

    components_usage = _build_components_usage(mesh_object)

    sidecar_path = object_source_folder / constants.FORMS_SIDECAR_FILENAME
    sidecar = {'version': 1, 'extra_forms': []}
    if sidecar_path.is_file():
        try:
            with open(sidecar_path, encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get('extra_forms'), list):
                sidecar = loaded
        except Exception:
            pass  # unreadable sidecar is rebuilt from scratch

    source = dump_path.name
    entry = {
        'label': form_label.strip() or f'form{len(sidecar["extra_forms"]) + 2}',
        'source': source,
        'matched_by': matched_by,
        'vb0_hash': mesh_object.vb0_hash,
        'components': components_usage,
    }

    replaced = False
    for index, existing in enumerate(sidecar['extra_forms']):
        if existing.get('source') == source:
            entry['label'] = form_label.strip() or existing.get('label') or entry['label']
            sidecar['extra_forms'][index] = entry
            replaced = True
            break
    if not replaced:
        sidecar['extra_forms'].append(entry)

    with open(sidecar_path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(sidecar, indent=4))

    pair_count = sum(len(pairs) for pairs in components_usage.values())
    return {
        'sidecar': str(sidecar_path),
        'label': entry['label'],
        'source': source,
        'matched_by': matched_by,
        'replaced': replaced,
        'components': len(components_usage),
        'pairs': pair_count,
        'total_forms': 1 + len(sidecar['extra_forms']),
    }
