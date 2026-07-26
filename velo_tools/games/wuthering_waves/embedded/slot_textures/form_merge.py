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
# each affected component's "form_variants" block inside ShaderTextureUsage.json
# (single file; legacy extra_forms sidecars are auto-migrated in and deleted).
#
# Schema v3: slot records are rich objects {filename, hash, format, width,
# height} (XQFA-fork compatible), with format/size read from the dump DDS
# headers — the slot-style combination conditions match ORIGINAL game formats,
# so they must be captured while the dump files are still around.

import json
import re

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
from . import log_freshness
from . import stu_metadata
from ..texture_identity import manifest as texture_identity_manifest

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


def _same_format_family(fmt_a, fmt_b) -> bool:
    """Runtime-equal format families (float32-collapsed filter_index)."""
    if not fmt_a or not fmt_b:
        return False
    import struct

    def f32(v):
        return struct.unpack('<f', struct.pack('<f', v))[0]
    return f32(constants.format_filter_index(fmt_a)) == \
        f32(constants.format_filter_index(fmt_b))


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


def _build_components_usage(mesh_object, surviving_sets, evidence=None):
    """Component N -> "vs=.." -> "ps=.." -> "ps-tN" -> rich record (exactly
    the base ShaderTextureUsage.json shape: only descriptors surviving the
    stock texture filter are seated, which strips the inherited-binding noise
    raw per-draw records carry). Also returns {hash: (dump path, component id
    set)} of the surviving textures for the copy step.

    With log-freshness ``evidence`` (ADR 0007 rev 12) records additionally
    carry ``fresh`` flags and pairs a ``depth_only`` flag; same-seat hash
    disagreements become fresh-beats-stale instead of conflict-None when
    exactly one side was freshly bound."""
    out = OrderedDict()
    record_cache = {}
    sources = {}
    for component_id, component_data in enumerate(mesh_object.components_data):
        keep = (surviving_sets[component_id]
                if surviving_sets and component_id < len(surviving_sets) else None)
        pairs = {}
        seat_fresh = {}
        pair_depth_only = {}
        for descriptor in component_data.draw_data.textures:
            survives = keep is None or descriptor.get_slot_hash() in keep
            slot = descriptor.get_slot()
            vs_key, ps_key = _shader_keys(descriptor)
            fresh = None
            observed_only = False
            if evidence is not None:
                fresh = log_freshness.slot_is_fresh(
                    evidence, descriptor.call_id, descriptor.slot_id,
                    descriptor.hash, descriptor.old_hash)
                rt = log_freshness.call_has_color_rt(evidence, descriptor.call_id)
                depth = (rt is False)
                prev = pair_depth_only.get((vs_key, ps_key))
                pair_depth_only[(vs_key, ps_key)] = (
                    depth if prev is None else (prev and depth))
                if not survives or fresh is False:
                    if rt is not True:
                        continue
                    observed_only = True
            elif not survives:
                continue
            pair = pairs.setdefault(vs_key, {}).setdefault(ps_key, {})
            seat = (vs_key, ps_key, slot)
            if slot in pair and pair[slot].get('hash') != descriptor.hash:
                seated_fresh = seat_fresh.get(seat)
                if fresh is not None and fresh and seated_fresh is False:
                    # Fresh newcomer replaces a stale-inherited seat.
                    pass  # fall through to the seating branch below
                elif fresh is not None and not fresh and seated_fresh:
                    continue  # stale newcomer never demotes a fresh seat
                else:
                    # Conflicting bindings for the same (pair, slot) within one
                    # frame: multi-state variant, mark unknown (generator skips).
                    pair[slot] = OrderedDict((('filename', ''), ('hash', None),
                                              ('format', ''), ('width', 0), ('height', 0)))
                    seat_fresh.pop(seat, None)
                    continue
            elif slot in pair and pair[slot].get('hash') == descriptor.hash:
                if fresh:
                    seat_fresh[seat] = True  # OR-aggregate across draws
                continue
            if descriptor.hash not in record_cache:
                record_cache[descriptor.hash] = _texture_record(descriptor)
            pair[slot] = record_cache[descriptor.hash]
            if fresh is not None:
                seat_fresh[seat] = fresh
            if not observed_only:
                entry = sources.setdefault(
                    descriptor.hash, (descriptor.path, set()))
                entry[1].add(str(component_id))
            if observed_only:
                pair[slot] = OrderedDict(pair[slot])
                pair[slot]['observed_only'] = True
        component_out = OrderedDict()
        for vs_key in sorted(pairs):
            vs_out = OrderedDict()
            for ps_key in sorted(pairs[vs_key]):
                ps_out = OrderedDict()
                for slot, record in sorted(pairs[vs_key][ps_key].items()):
                    seat = (vs_key, ps_key, slot)
                    if evidence is not None and record.get('hash') is not None:
                        # record_cache entries are shared: per-seat flags go on
                        # a copy.
                        record = OrderedDict(record)
                        record['fresh'] = bool(seat_fresh.get(seat, False))
                    ps_out[slot] = record
                if evidence is not None:
                    ps_out['depth_only'] = bool(
                        pair_depth_only.get((vs_key, ps_key), False))
                vs_out[ps_key] = ps_out
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
    coverage (whole pairs only - single slots are not grafted onto existing
    pairs, cross-frame layout variance would pollute the per-draw presence
    truth). The EXISTING (canonical) maps win every other contradiction:
    frame-state differences between captures (camera, expressions, pass
    states) are runtime-resolved by the per-slot family guards, and marking
    them as conflicts proved far too destructive in the field (evaporated
    signatures, un-replaced stable textures). The variant heuristic carries
    a coexistence guard: two hashes seated together in either frame cannot
    be residency levels of one texture. Returns (variants_added,
    records_added, kept_contradictions)."""
    variants_added = 0
    records_added = 0
    conflicts_marked = 0
    # Coexistence guard for the variant heuristic: a texture shows ONE
    # residency level per frame, so a hash seated ANYWHERE in the existing
    # maps cannot be "another level" of a different hash captured alongside
    # it - such same-format/different-size contradictions are multi-state
    # passes, not mip ladders.
    dst_seated = set()
    for dst_vs in dst_components.values():
        if not isinstance(dst_vs, dict):
            continue
        for vs_key, dst_ps in dst_vs.items():
            if not str(vs_key).startswith('vs=') or not isinstance(dst_ps, dict):
                continue
            for dst_slots in dst_ps.values():
                if not isinstance(dst_slots, dict):
                    continue
                for rec in dst_slots.values():
                    if isinstance(rec, dict) and rec.get('hash'):
                        dst_seated.add(rec['hash'])
    src_seated = set()
    for src_vs in src_components.values():
        if not isinstance(src_vs, dict):
            continue
        for vs_key, src_ps in src_vs.items():
            if not str(vs_key).startswith('vs=') or not isinstance(src_ps, dict):
                continue
            for src_slots in src_ps.values():
                if not isinstance(src_slots, dict):
                    continue
                for rec in src_slots.values():
                    if isinstance(rec, dict) and rec.get('hash'):
                        src_seated.add(rec['hash'])
    for comp, src_vs in src_components.items():
        dst_vs = dst_components.setdefault(comp, OrderedDict())
        for vs_key, src_ps in src_vs.items():
            if not str(vs_key).startswith('vs='):
                existing = dst_vs.get(vs_key)
                if existing is not None and existing != src_ps:
                    raise FormMergeError(
                        f'{comp} has conflicting {vs_key} route metadata')
                dst_vs[vs_key] = src_ps
                continue
            dst_ps = dst_vs.setdefault(vs_key, OrderedDict())
            for ps_key, src_slots in src_ps.items():
                dst_slots = dst_ps.get(ps_key)
                if dst_slots is None:
                    dst_ps[ps_key] = OrderedDict(src_slots)
                    records_added += sum(
                        1 for v in src_slots.values() if isinstance(v, dict))
                    continue
                for slot, record in src_slots.items():
                    if not isinstance(record, dict):
                        continue  # pair-level flags (depth_only): canonical wins
                    existing = dst_slots.get(slot)
                    if existing is None or not isinstance(existing, dict):
                        continue  # no slot grafting onto existing pairs
                    new_hash = record.get('hash')
                    src_fresh = record.get('fresh')
                    dst_fresh = existing.get('fresh')
                    if (not new_hash or new_hash == existing.get('hash')
                            or new_hash in (existing.get('variants') or [])):
                        if (new_hash and new_hash == existing.get('hash')
                                and src_fresh is True and dst_fresh is False):
                            # Same seat re-verified as freshly bound in the new
                            # dump: upgrade the canonical flag.
                            existing['fresh'] = True
                        continue
                    if src_fresh is False:
                        # Stale-inherited src records are not evidence: never
                        # harvest variants from them, never contradict the
                        # canonical seat with them (ADR 0007 rev 12).
                        continue
                    if src_fresh is True and dst_fresh is False:
                        # Fresh src replaces a stale-inherited canonical seat.
                        dst_slots[slot] = OrderedDict(record)
                        records_added += 1
                        continue
                    if (record.get('format') and existing.get('format')
                            and record['format'] == existing['format']
                            and record.get('width') != existing.get('width')
                            and new_hash not in dst_seated
                            and existing.get('hash') not in src_seated):
                        existing.setdefault('variants', [])
                        existing['variants'].append(new_hash)
                        variants_added += 1
                        continue
                    if (existing.get('hash') is not None
                            and _same_format_family(record.get('format'),
                                                    existing.get('format'))):
                        # same family on both sides: per-slot guards cannot
                        # arbitrate the states - never assign this seat
                        dst_slots[slot] = OrderedDict((
                            ('filename', ''), ('hash', None), ('format', ''),
                            ('width', 0), ('height', 0)))
                        conflicts_marked += 1
                        continue
                    conflicts_marked += 1  # different family: guards
                    #                        arbitrate, canonical stands
    return variants_added, records_added, conflicts_marked


def _upsert_extra_form(usage, components_usage, form_label, source,
                       matched_by, vb0_hash):
    """Insert or merge one form entry before canonical component-local write."""
    extra_forms = stu_metadata.form_entries(usage)

    label = form_label.strip()
    entry = {
        'label': label or f'form{len(extra_forms) + 2}',
        'source': source,
        'matched_by': matched_by,
        'vb0_hash': vb0_hash,
        'components': components_usage,
    }

    replaced = False
    variants_added = 0
    for index, existing in enumerate(extra_forms):
        if existing.get('source') == source:
            entry['label'] = label or existing.get('label') or entry['label']
            _preserve_anchor_metadata(existing, entry)
            extra_forms[index] = entry
            replaced = True
            break
        if label and existing.get('label') == label:
            variants_added, _, _ = _merge_variant_records(
                existing['components'], components_usage)
            entry = existing
            replaced = True
            break
    if not replaced:
        extra_forms.append(entry)

    usage[constants.EXTRA_FORMS_KEY] = extra_forms
    return entry, replaced, variants_added


def merge_extracted_form_usage(usage, form_usage, component_map, route_hash,
                               form_label='', source=''):
    """Merge one already-extracted same-VB0 STU as a routed form.

    This is the extraction-folder equivalent of ``merge_form_dump``.  It is
    used by the cross-scene producer when an IB row is explicitly marked as a
    form, so the result follows the same ``extra_forms`` canonicalization path
    as the established form-first workflow.
    """
    if not isinstance(usage, dict) or not isinstance(form_usage, dict):
        raise FormMergeError('extracted form ShaderTextureUsage.json has an unexpected shape')
    components_usage = OrderedDict()
    for name, block in form_usage.items():
        if (not re.fullmatch(r'Component\s+\d+', str(name))
                or not isinstance(block, dict)):
            continue
        # Extracted component blocks also carry canonicalization metadata such
        # as form_component_mode/form_variants.  A new form branch contains
        # only this extract's native shader-pair maps, matching raw-dump merge.
        components_usage[name] = OrderedDict(
            (key, value) for key, value in block.items()
            if str(key).startswith('vs='))
    if not components_usage:
        raise FormMergeError(
            'extracted form ShaderTextureUsage.json contains no Component records')
    remapped_usage = _remap_cross_scene_form_components(
        components_usage, component_map, route_hash)
    entry, replaced, variants_added = _upsert_extra_form(
        usage, remapped_usage, form_label, source, 'extracted-vb0', route_hash)
    multi_components = sorted(
        int(name.rsplit(' ', 1)[-1]) for name in remapped_usage)
    stu_metadata.sync_form_component_modes(
        usage, multi_components=multi_components)
    summary = {
        'label': entry['label'],
        'replaced': replaced,
        'variants_added': variants_added,
        'components': len(remapped_usage),
    }
    # Match the persisted form-first path before the aggregate producer reads
    # this in-memory STU again.
    stu_metadata.canonicalize_lean_usage(usage)
    return summary


_ANCHOR_METADATA_KEYS = (
    constants.FORM_ANCHOR_VB0_KEY,
    constants.FORM_ANCHOR_LABEL_KEY,
    constants.FORM_ANCHOR_SOURCE_KEY,
    constants.FORM_ANCHOR_RANK_KEY,
)


def _preserve_anchor_metadata(existing, replacement):
    for key in _ANCHOR_METADATA_KEYS:
        if key in existing and key not in replacement:
            replacement[key] = existing[key]


def _valid_hash8(value):
    return stu_metadata._valid_hash8(value)


def _entry_label(entry):
    return str((entry or {}).get('label') or (entry or {}).get('source')
               or (entry or {}).get('form_label') or '').strip().lower()


def _usage_paths_for_anchor_write(object_source_folder):
    root = Path(object_source_folder)
    base_path = root / constants.BASE_USAGE_FILENAME
    return [base_path] if base_path.is_file() else []


def _remap_cross_scene_form_components(components_usage, component_map,
                                       route_hash):
    mapping = {int(local): int(global_id)
               for local, global_id in (component_map or {}).items()}
    remapped = OrderedDict()
    for component_name, block in components_usage.items():
        try:
            local_id = int(str(component_name).rsplit(' ', 1)[-1])
            global_id = mapping[local_id]
        except (KeyError, TypeError, ValueError) as exc:
            raise FormMergeError(
                f'CrossSceneManifest component_map does not cover '
                f'{component_name}') from exc
        target_name = f'Component {global_id}'
        if target_name in remapped:
            raise FormMergeError(
                f'CrossSceneManifest maps multiple local components to '
                f'{target_name}')
        target = OrderedDict(block)
        target['vb0_hash'] = route_hash
        remapped[target_name] = target
    return remapped


def _merge_cross_scene_texture_sources(target, sources, component_map):
    mapping = {int(local): int(global_id)
               for local, global_id in (component_map or {}).items()}
    for texture_hash, (path, component_ids) in sources.items():
        try:
            mapped = {str(mapping[int(component_id)])
                      for component_id in component_ids}
        except (KeyError, TypeError, ValueError) as exc:
            raise FormMergeError(
                f'CrossSceneManifest component_map does not cover texture '
                f'{texture_hash}') from exc
        if texture_hash in target:
            target[texture_hash][1].update(mapped)
        else:
            target[texture_hash] = (path, mapped)


def refresh_local_discriminator_audit_in_usage(usage):
    """Return a fresh runtime audit without persisting it in the STU dict."""
    from . import generator
    stu_metadata.sync_form_component_modes(usage)
    usage.pop(constants.LOCAL_FORM_DISCRIMINATOR_KEY, None)
    stu_metadata.sync_form_anchors_field(usage)
    return generator.build_local_discriminator_audit_from_usage(usage)


def refresh_local_discriminator_audit_file(usage_path):
    usage_path = Path(usage_path)
    usage = json.loads(usage_path.read_text(encoding='utf-8'))
    if not isinstance(usage, dict):
        raise FormMergeError(f'{usage_path} has an unexpected shape')
    audit = refresh_local_discriminator_audit_in_usage(usage)
    stu_metadata.write_usage(usage_path, usage)
    return audit


def refresh_local_discriminator_audits(object_source_folder):
    updated = []
    for usage_path in _usage_paths_for_anchor_write(object_source_folder):
        refresh_local_discriminator_audit_file(usage_path)
        updated.append(str(usage_path))
    return updated


def write_trusted_form_anchor(object_source_folder, form_label, anchor_hash,
                              rank=1, source=constants.FORM_ANCHOR_SOURCE_TRUSTED):
    """Write trusted form-anchor metadata into matching STU form variants."""
    anchor_hash = str(anchor_hash or '').strip().lower()
    if not _valid_hash8(anchor_hash):
        raise FormMergeError(f'invalid form anchor vb0 hash: {anchor_hash!r}')
    label = str(form_label or '').strip().lower()
    if not label:
        raise FormMergeError('form label is required for trusted anchor metadata')
    updated = []
    usage_paths = _usage_paths_for_anchor_write(object_source_folder)
    base_path = Path(object_source_folder) / constants.BASE_USAGE_FILENAME
    if label == 'base' and base_path.is_file():
        usage_paths = [base_path]
    for usage_path in usage_paths:
        try:
            usage = json.loads(usage_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        changed = False
        if label == 'base':
            usage[constants.FORM_ANCHOR_LABEL_KEY] = 'base'
            usage[constants.FORM_ANCHOR_VB0_KEY] = anchor_hash
            usage[constants.FORM_ANCHOR_SOURCE_KEY] = source
            usage[constants.FORM_ANCHOR_RANK_KEY] = int(rank)
            changed = True
        else:
            forms = stu_metadata.form_entries(usage)
            if not forms:
                continue
            for entry in forms:
                if not isinstance(entry, dict) or _entry_label(entry) != label:
                    continue
                entry[constants.FORM_ANCHOR_LABEL_KEY] = label
                entry[constants.FORM_ANCHOR_VB0_KEY] = anchor_hash
                entry[constants.FORM_ANCHOR_SOURCE_KEY] = source
                entry[constants.FORM_ANCHOR_RANK_KEY] = int(rank)
                changed = True
            if changed:
                usage[constants.EXTRA_FORMS_KEY] = forms
        if changed:
            stu_metadata.write_usage(usage_path, usage)
            updated.append(str(usage_path))
    return updated


def read_trusted_form_anchors(object_source_folder):
    """Return (label, anchor_hash) pairs from trusted STU metadata."""
    out = []
    seen = set()
    for usage_path in _usage_paths_for_anchor_write(object_source_folder):
        try:
            usage = json.loads(usage_path.read_text(encoding='utf-8'))
        except Exception:
            continue
        for label, anchor_hash in stu_metadata.collect_anchor_pairs(usage):
            key = (label, anchor_hash)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _sync_texture_identity_manifest(
    object_source_folder,
    dump_path,
    enabled: bool,
):
    manifest_path = (
        Path(object_source_folder) / texture_identity_manifest.MANIFEST_FILENAME)
    if not enabled:
        manifest_path.unlink(missing_ok=True)
        return None
    return texture_identity_manifest.refresh_manifest(
        object_source_folder,
        source_directories=(dump_path,),
    )


def merge_form_dump(object_source_folder, dump_path, form_label: str = '',
                    texture_filter: TextureFilter = None,
                    refresh_texture_identity: bool = True) -> dict:
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

    # ADR 0007 rev 12: binding-freshness evidence for the extra-form records
    # (None -> legacy records without freshness flags).
    evidence = log_freshness.parse_log_freshness(dump_path)

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

    manifest_path = object_source_folder / 'CrossSceneManifest.json'
    legacy_path = object_source_folder / 'CrossSceneRouting.json'
    if legacy_path.is_file() and not manifest_path.is_file():
        raise FormMergeError(
            '检测到旧版 CrossSceneRouting.json schema v2；请先使用当前 '
            'Velo Tools 重新执行“合并跨场景”')
    if manifest_path.is_file():
        from ..crossscene.manifest import load_manifest
        manifest = load_manifest(object_source_folder)
        routes_by_ib = {}
        for entry in manifest.get('runtime_ibs') or []:
            hashes = {
                str(entry.get('ib_hash') or '').strip().lower(),
                str(entry.get('vb0_hash') or '').strip().lower(),
                str((entry.get('native_metadata') or {}).get('vb0_hash')
                    or '').strip().lower(),
            }
            for route_hash in hashes - {''}:
                routes_by_ib[route_hash] = entry
        combined = {}
        lifted = []
        fold_forms_written = []
        usage_path = object_source_folder / constants.BASE_USAGE_FILENAME
        if not usage_path.is_file():
            raise FormMergeError(
                f'{constants.BASE_USAGE_FILENAME} is missing from the '
                'schema-v3 aggregate root')
        with open(usage_path, encoding='utf-8') as f:
            usage = json.load(f)
        multi_components = set()
        merged_form_components = OrderedDict()
        fold_routes_written = []
        for vb0, obj in mesh_objects.items():
            key = vb0.lower()
            route = routes_by_ib.get(key)
            if route is None:
                continue
            components_usage, src = _build_components_usage(
                obj, surviving.get(vb0), evidence)
            component_map = route.get('component_map') or {}
            _merge_cross_scene_texture_sources(
                combined, src, component_map)
            lifted.append(vb0)
            if route.get('kind') != 'fold':
                continue
            route_hash = str(route.get('ib_hash') or key).lower()
            remapped_usage = _remap_cross_scene_form_components(
                components_usage, component_map, route_hash)
            duplicate_components = set(merged_form_components) & set(remapped_usage)
            if duplicate_components:
                raise FormMergeError(
                    'schema-v3 form dump maps multiple routes to the same '
                    'global component(s): '
                    + ', '.join(sorted(duplicate_components)))
            merged_form_components.update(remapped_usage)
            multi_components.update(
                int(component_name.rsplit(' ', 1)[-1])
                for component_name in remapped_usage)
            fold_routes_written.append((route_hash, len(remapped_usage)))
        if multi_components:
            first_route = fold_routes_written[0][0]
            entry, replaced, variants_added = _upsert_extra_form(
                usage, merged_form_components, form_label, dump_path.name,
                'manifest', first_route)
            stu_metadata.sync_form_component_modes(
                usage, multi_components=sorted(multi_components))
            refresh_local_discriminator_audit_in_usage(usage)
            stu_metadata.write_usage(usage_path, usage)
            fold_forms_written.extend({
                'ib_hash': route_hash,
                'usage_file': str(usage_path),
                'label': entry['label'],
                'replaced': replaced,
                'variants_added': variants_added,
                'components': component_count,
            } for route_hash, component_count in fold_routes_written)
        copied = _copy_form_textures(object_source_folder, combined)
        _sync_texture_identity_manifest(
            object_source_folder, dump_path, refresh_texture_identity)
        return {'mode': 'cross_scene', 'lifted_ibs': sorted(lifted),
                'textures_copied': copied, 'form_label': form_label.strip(),
                'fold_extra_forms': fold_forms_written,
                'fold_extra_forms_missing': []}

    mesh_object, matched_by = _match_object(mesh_objects, vb0_hash, cb4_hash)

    if base_components and len(mesh_object.components_data) != len(base_components):
        raise FormMergeError(
            f'component count mismatch: dump object has '
            f'{len(mesh_object.components_data)}, extracted object has '
            f'{len(base_components)} - forms must share the same mesh')

    components_usage, texture_sources = _build_components_usage(
        mesh_object, surviving.get(mesh_object.vb0_hash), evidence)
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
        variants_added, records_added, conflicts_marked = _merge_variant_records(
            base_components, components_usage)
        usage.update(base_components)
        refresh_local_discriminator_audit_in_usage(usage)
        stu_metadata.write_usage(usage_path, usage)
        _sync_texture_identity_manifest(
            object_source_folder, dump_path, refresh_texture_identity)
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
            'conflicts_marked': conflicts_marked,
            'freshness': evidence is not None,
            'total_forms': 1 + len(stu_metadata.form_entries(usage)),
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

    extra_forms = stu_metadata.form_entries(usage)

    # Migrate a pre-v2 sidecar (fold its entries in, then delete the file).
    migrated = False
    legacy_path = object_source_folder / constants.LEGACY_SIDECAR_FILENAME
    if legacy_path.is_file():
        try:
            with open(legacy_path, encoding='utf-8') as f:
                legacy = json.load(f)
            for legacy_entry in stu_metadata.form_entries({
                    constants.EXTRA_FORMS_KEY:
                        (legacy or {}).get(constants.EXTRA_FORMS_KEY)}) or []:
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
            _preserve_anchor_metadata(existing, entry)
            extra_forms[index] = entry
            replaced = True
            break
        if form_label.strip() and existing.get('label') == form_label.strip():
            # Same form, ANOTHER dump (e.g. a different camera distance):
            # residency harvest into the existing entry instead of replacing
            # it or spawning a spurious extra form.
            variants_added, _, _ = _merge_variant_records(
                existing['components'], components_usage)
            entry = existing
            replaced = True
            break
    if not replaced:
        extra_forms.append(entry)

    usage[constants.EXTRA_FORMS_KEY] = extra_forms
    stu_metadata.sync_form_component_modes(
        usage, multi_components=[
            int(c.rsplit(' ', 1)[-1])
            for c in components_usage
            if str(c).startswith('Component ')
        ])
    refresh_local_discriminator_audit_in_usage(usage)
    stu_metadata.write_usage(usage_path, usage)
    _sync_texture_identity_manifest(
        object_source_folder, dump_path, refresh_texture_identity)
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
        'freshness': evidence is not None,
        'total_forms': 1 + len(stu_metadata.form_entries(usage)),
    }
