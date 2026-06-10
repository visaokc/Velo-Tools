# "Merge form dump" pipeline for the WWMI slot-style texture layer.
#
# A multi-form character binds different texture sets (and partially different
# material PS) per form, so exact per-form slot maps require one frame dump
# per form (data-determined, see ADR 0006/0007; any form count >= 2 is
# supported, forms are an ordered list). This module consumes a RAW
# FrameAnalysis folder of an extra form directly — no second object extraction
# is needed: it runs the stock in-memory extraction chain up to
# ComponentBuilder (the same five steps extract_frame_data() runs before
# OutputBuilder; mirrors embedded/lod/extract.py), picks the object matching
# the already-extracted one (vb0 hash first, skeleton cb4 hash as fallback)
# and persists its per-(component x shader-pair x slot) texture records under
# the "extra_forms" key INSIDE ShaderTextureUsage.json (single file; a legacy
# ShaderTextureUsageForms.json sidecar is auto-migrated in and deleted).
#
# Schema v3: slot records are rich objects {filename, hash, format, width,
# height} (XQFA-fork compatible), with format/size read from the dump DDS
# headers — the slot-style combination conditions match ORIGINAL game formats,
# so they must be captured while the dump files are still around.

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
from ..._wwmi_core.extract_frame_data.output_builder import OutputBuilder, TextureFilter

from . import constants
from . import dds_meta

# Same exclusion list the stock extraction / export use for well-known cubemaps.
KNOWN_CUBEMAP_HASHES = ['af26db30', '1320a071', '10d7937d', '87505b2b',
                        'e5df00a8', 'ec2fecec', 'd313d349']


def texture_filter_from_cfg(cfg) -> TextureFilter:
    """Mirrors the TextureFilter the stock extraction builds from the same
    settings, so extra-form maps share the base maps' shape. Critical: raw
    per-draw dump records include INHERITED bindings (stale state from earlier
    draws); the stock filter (size / cubemap / same-slot-hash rules) is what
    makes the base maps structurally clean, so the merged forms must pass
    through the very same sieve or the slot-set material fingerprint drowns
    in inherited-slot noise."""
    return TextureFilter(
        min_file_size=(cfg.skip_small_textures_size * 1024
                       if getattr(cfg, 'skip_small_textures', False) else 0),
        exclude_extensions=['jpg'] if getattr(cfg, 'skip_jpg_textures', False) else [],
        exclude_same_slot_hash_textures=getattr(cfg, 'skip_same_slot_hash_textures', False),
        exclude_hashes=(KNOWN_CUBEMAP_HASHES
                        if getattr(cfg, 'skip_known_cubemap_textures', False) else []),
    )


class FormMergeError(Exception):
    pass


def _shader_keys(descriptor):
    """Same nested-schema keys ("vs=<hash>", "ps=<hash>") as
    _shader_texture_usage.py."""
    vs = next((s for s in descriptor.shaders if s.type is ShaderType.Vertex), None)
    ps = next((s for s in descriptor.shaders if s.type is ShaderType.Pixel), None)
    return (vs.raw if vs is not None else 'vs=?',
            ps.raw if ps is not None else 'ps=?')


def _texture_record(descriptor) -> OrderedDict:
    """Rich slot record; extra forms write no texture files, so filename stays
    empty (same convention as the XQFA exporter for unavailable data)."""
    meta = dds_meta.read_dds_meta(descriptor.path)
    return OrderedDict((
        ('filename', ''),
        ('hash', descriptor.hash),
        ('format', meta.format if meta else ''),
        ('width', meta.width if meta else 0),
        ('height', meta.height if meta else 0),
    ))


def collect_mesh_objects(dump_path: Path, texture_filter: TextureFilter):
    """Stock in-memory extraction chain. Returns both the raw mesh objects
    (full per-draw descriptor lists, with shader-pair attribution) and the
    per-object SURVIVING slot-hash sets after the stock OutputBuilder texture
    filter — the same combination _shader_texture_usage.py uses for the base
    maps, so extra-form maps come out shape-identical."""
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
    output_builder = OutputBuilder(
        shapekeys=shapekeys.shapekeys,
        mesh_objects=component_builder.mesh_objects,
        texture_filter=texture_filter,
    )
    surviving = {}
    for vb0_hash, object_data in output_builder.objects.items():
        surviving[vb0_hash] = [
            {t.get_slot_hash() for t in component.textures}
            for component in object_data.components
        ]
    return component_builder.mesh_objects, surviving


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


def _build_components_usage(mesh_object, surviving_sets):
    """Component N -> "vs=.." -> "ps=.." -> "ps-tN" -> rich record (exactly
    the base ShaderTextureUsage.json shape: only descriptors surviving the
    stock texture filter are seated, which strips the inherited-binding noise
    raw per-draw records carry). Also returns {hash: (dump path, component id
    set)} of the surviving textures for the copy step."""
    out = OrderedDict()
    record_cache = {}
    sources = {}
    for component_id, component_data in enumerate(mesh_object.components_data):
        keep = (surviving_sets[component_id]
                if surviving_sets and component_id < len(surviving_sets) else None)
        pairs = {}
        for descriptor in component_data.draw_data.textures:
            if keep is not None and descriptor.get_slot_hash() not in keep:
                continue
            slot = descriptor.get_slot()
            vs_key, ps_key = _shader_keys(descriptor)
            pair = pairs.setdefault(vs_key, {}).setdefault(ps_key, {})
            if slot in pair and pair[slot].get('hash') != descriptor.hash:
                # Conflicting bindings for the same (pair, slot) within one
                # frame: multi-state variant, mark unknown (generator skips).
                pair[slot] = OrderedDict((('filename', ''), ('hash', None),
                                          ('format', ''), ('width', 0), ('height', 0)))
            else:
                if descriptor.hash not in record_cache:
                    record_cache[descriptor.hash] = _texture_record(descriptor)
                pair[slot] = record_cache[descriptor.hash]
                entry = sources.setdefault(descriptor.hash, (descriptor.path, set()))
                entry[1].add(str(component_id))
        component_out = OrderedDict()
        for vs_key in sorted(pairs):
            vs_out = OrderedDict()
            for ps_key in sorted(pairs[vs_key]):
                vs_out[ps_key] = OrderedDict(sorted(pairs[vs_key][ps_key].items()))
            component_out[vs_key] = vs_out
        out[f'Component {component_id}'] = component_out
    return out, sources


def _copy_form_textures(object_source_folder: Path, sources: dict) -> int:
    """Copies the form's surviving textures into the object source folder
    (stock extraction naming) unless a file for the hash already exists.
    Without the files the form is map-only: its slots could never be rebound
    to anything and no form-unique REPLACED texture would exist to drive the
    runtime form detection — the author edits these files like any extracted
    texture."""
    import shutil
    copied = 0
    for tex_hash, (path, comp_ids) in sorted(sources.items()):
        if any(object_source_folder.glob(f'* t={tex_hash}.*')):
            continue
        src = Path(path)
        joined = '-'.join(sorted(comp_ids))
        try:
            shutil.copyfile(src, object_source_folder / f'Components-{joined} t={tex_hash}{src.suffix}')
            copied += 1
        except OSError:
            pass  # unreadable dump file: the map entry alone still helps
    return copied


def _merge_variant_records(dst_components, src_components):
    """Folds a re-merged dump of the SAME form into existing records:
    same (component, pair, slot) with same format but different size means
    another streaming residency level of the texture — recorded under the
    record's "variants" key (extra latch keys shrink the form-switch latency
    after the shader fast path goes stale). New pairs/slots are added for
    coverage; contradicting records (same size or different format) keep the
    existing data. Returns (variants_added, records_added)."""
    variants_added = 0
    records_added = 0
    for comp, src_vs in src_components.items():
        dst_vs = dst_components.setdefault(comp, OrderedDict())
        for vs_key, src_ps in src_vs.items():
            dst_ps = dst_vs.setdefault(vs_key, OrderedDict())
            for ps_key, src_slots in src_ps.items():
                dst_slots = dst_ps.setdefault(ps_key, OrderedDict())
                for slot, record in src_slots.items():
                    existing = dst_slots.get(slot)
                    if existing is None:
                        dst_slots[slot] = record
                        records_added += 1
                        continue
                    new_hash = record.get('hash')
                    if (not new_hash or new_hash == existing.get('hash')
                            or new_hash in (existing.get('variants') or [])):
                        continue
                    if (record.get('format') and existing.get('format')
                            and record['format'] == existing['format']
                            and record.get('width') != existing.get('width')):
                        existing.setdefault('variants', [])
                        existing['variants'].append(new_hash)
                        variants_added += 1
    return variants_added, records_added


def merge_form_dump(object_source_folder, dump_path, form_label: str = '',
                    texture_filter: TextureFilter = None) -> dict:
    """Parses one extra-form RAW dump and merges it into ShaderTextureUsage.json.

    texture_filter should mirror the settings the base extraction ran with
    (texture_filter_from_cfg); None falls back to an unfiltered merge.
    Returns a summary dict for operator reporting."""
    object_source_folder = Path(object_source_folder)
    dump_path = Path(dump_path)
    if texture_filter is None:
        texture_filter = TextureFilter(
            min_file_size=0, exclude_extensions=[],
            exclude_same_slot_hash_textures=False, exclude_hashes=[])

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

    mesh_objects, surviving = collect_mesh_objects(dump_path, texture_filter)
    mesh_object, matched_by = _match_object(mesh_objects, vb0_hash, cb4_hash)

    if base_components and len(mesh_object.components_data) != len(base_components):
        raise FormMergeError(
            f'component count mismatch: dump object has '
            f'{len(mesh_object.components_data)}, extracted object has '
            f'{len(base_components)} - forms must share the same mesh')

    components_usage, texture_sources = _build_components_usage(
        mesh_object, surviving.get(mesh_object.vb0_hash))
    textures_copied = _copy_form_textures(object_source_folder, texture_sources)

    label = form_label.strip()
    if label.lower() == 'base':
        # Residency harvest INTO the base maps: another-distance dump of the
        # base form adds variant hashes (and new pairs) to the top-level
        # records; the recorded canonical hashes never change.
        usage_path = object_source_folder / constants.BASE_USAGE_FILENAME
        if not usage_path.is_file():
            raise FormMergeError(
                f'{constants.BASE_USAGE_FILENAME} is missing - re-extract the '
                f'object before harvesting into the base maps')
        with open(usage_path, encoding='utf-8') as f:
            usage = json.load(f)
        base_components = OrderedDict(
            (k, v) for k, v in usage.items() if k.startswith('Component'))
        variants_added, records_added = _merge_variant_records(
            base_components, components_usage)
        usage.update(base_components)
        with open(usage_path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(usage, indent=4))
        return {
            'usage_file': str(usage_path),
            'label': 'base',
            'source': dump_path.name,
            'matched_by': matched_by,
            'replaced': True,
            'migrated_sidecar': False,
            'components': len(components_usage),
            'pairs': sum(len(ps_map) for vs_map in components_usage.values()
                         for ps_map in vs_map.values()),
            'textures_copied': textures_copied,
            'variants_added': variants_added,
            'total_forms': 1 + len(usage.get(constants.EXTRA_FORMS_KEY) or []),
        }

    # Single-file schema v2: extra forms live INSIDE ShaderTextureUsage.json.
    usage_path = object_source_folder / constants.BASE_USAGE_FILENAME
    if not usage_path.is_file():
        raise FormMergeError(
            f'{constants.BASE_USAGE_FILENAME} is missing - re-extract the object '
            f'with a current Velo Tools build before merging forms')
    with open(usage_path, encoding='utf-8') as f:
        usage = json.load(f)
    if not isinstance(usage, dict):
        raise FormMergeError(f'{constants.BASE_USAGE_FILENAME} has an unexpected shape')

    extra_forms = usage.get(constants.EXTRA_FORMS_KEY)
    if not isinstance(extra_forms, list):
        extra_forms = []

    # Migrate a pre-v2 sidecar (fold its entries in, then delete the file).
    migrated = False
    legacy_path = object_source_folder / constants.LEGACY_SIDECAR_FILENAME
    if legacy_path.is_file():
        try:
            with open(legacy_path, encoding='utf-8') as f:
                legacy = json.load(f)
            for legacy_entry in (legacy or {}).get(constants.EXTRA_FORMS_KEY) or []:
                if not any(e.get('source') == legacy_entry.get('source')
                           for e in extra_forms):
                    extra_forms.append(legacy_entry)
            migrated = True
        except Exception:
            pass  # unreadable sidecar: drop it, the new entry supersedes

    source = dump_path.name
    entry = {
        'label': form_label.strip() or f'form{len(extra_forms) + 2}',
        'source': source,
        'matched_by': matched_by,
        'vb0_hash': mesh_object.vb0_hash,
        'components': components_usage,
    }

    replaced = False
    variants_added = 0
    for index, existing in enumerate(extra_forms):
        if existing.get('source') == source:
            # Same dump re-merged: full overwrite.
            entry['label'] = form_label.strip() or existing.get('label') or entry['label']
            extra_forms[index] = entry
            replaced = True
            break
        if form_label.strip() and existing.get('label') == form_label.strip():
            # Same form, ANOTHER dump (e.g. a different camera distance):
            # residency harvest into the existing entry instead of replacing
            # it or spawning a spurious extra form.
            variants_added, _ = _merge_variant_records(
                existing['components'], components_usage)
            entry = existing
            replaced = True
            break
    if not replaced:
        extra_forms.append(entry)

    usage[constants.EXTRA_FORMS_KEY] = extra_forms
    with open(usage_path, 'w', encoding='utf-8') as f:
        f.write(json.dumps(usage, indent=4))
    if legacy_path.is_file():
        legacy_path.unlink()

    pair_count = sum(len(ps_map) for vs_map in components_usage.values()
                     for ps_map in vs_map.values())
    return {
        'usage_file': str(usage_path),
        'label': entry['label'],
        'source': source,
        'matched_by': matched_by,
        'replaced': replaced,
        'migrated_sidecar': migrated,
        'components': len(components_usage),
        'pairs': pair_count,
        'textures_copied': textures_copied,
        'variants_added': variants_added,
        'total_forms': 1 + len(extra_forms),
    }
