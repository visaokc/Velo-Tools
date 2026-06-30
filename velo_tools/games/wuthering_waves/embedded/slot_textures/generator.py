# Slot-style texture layer generator (pure python, no bpy / no _wwmi_core
# imports). The emitted INI mirrors the concise XQFA slot model: format-tagged
# ps-t conditions inside the component draw scope, followed by direct texture
# assignments. Dirty/stale slots are filtered out before branch construction
# when ShaderTextureUsage.json carries v4 freshness evidence.

import json
import re
import struct

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import constants


class SlotStyleDegrade(Exception):
    """Raised when slot-style generation cannot proceed safely."""


# comp_id -> ps_hash -> slot -> texture hash (None = conflicting multi-state
# binding seen for that slot; generator must not assign it).
FormData = Dict[int, Dict[str, Dict[int, Optional[str]]]]
# texture hash -> {'format': canonical DXGI name or '', 'width', 'height'}
TextureInfo = Dict[str, dict]

_PS_RE = re.compile(r'ps=([0-9a-f]{16})')
_VS_KEY_RE = re.compile(r'^vs=[0-9a-f?]+$')
_COMP_RE = re.compile(r'component\s*(\d+)\s*$', re.I)
_SLOT_RE = re.compile(r'^ps-t(\d+)$')

_RESERVED_KEYS = {
    constants.EXTRA_FORMS_KEY,
    constants.LOCAL_FORM_DISCRIMINATOR_KEY,
    constants.LOCAL_COMPONENT_SOURCES_KEY,
    'version',
}


def _f32(value: float) -> float:
    """float32 round-trip: 3DMigoto ini expressions compare at that precision."""
    return struct.unpack('<f', struct.pack('<f', value))[0]


# ---------------------------------------------------------------- loading --

def _ingest_slot(pair_out: Dict[int, Optional[str]], slot: int,
                 tex_hash: Optional[str]):
    if slot in pair_out and pair_out[slot] != tex_hash:
        pair_out[slot] = None
    else:
        pair_out[slot] = tex_hash


def normalize_usage(raw: dict, source: str, warnings: List[str],
                    texture_info: Optional[TextureInfo] = None,
                    freshness: Optional[Dict[Tuple[int, str, int], bool]] = None) -> FormData:
    """Convert one ShaderTextureUsage-shaped dict into FormData.

    Accepts both the old flat schema and the v3/v4 nested rich schema. Rich
    records feed texture_info and optional v4 freshness flags.
    """
    out: FormData = {}
    for comp_name, pairs in (raw or {}).items():
        if comp_name in _RESERVED_KEYS:
            continue
        found = _COMP_RE.search(comp_name)
        if not found:
            warnings.append(f'{source}: unrecognized component key "{comp_name}" skipped')
            continue
        comp_id = int(found.group(1))
        comp_out = out.setdefault(comp_id, {})
        for pair_key, value in (pairs or {}).items():
            if _VS_KEY_RE.match(pair_key) and isinstance(value, dict):
                for ps_key, slots in value.items():
                    ps_found = _PS_RE.search(ps_key)
                    if not ps_found:
                        warnings.append(f'{source}: pair "{pair_key}/{ps_key}" has no ps hash, skipped')
                        continue
                    ps_hash = ps_found.group(1)
                    pair_out = comp_out.setdefault(ps_hash, {})
                    for slot_name, record in (slots or {}).items():
                        slot_found = _SLOT_RE.match(slot_name)
                        if not slot_found or not isinstance(record, dict):
                            continue
                        tex_hash = record.get('hash')
                        if not isinstance(tex_hash, str):
                            tex_hash = None
                        elif texture_info is not None and record.get('format'):
                            info = texture_info.setdefault(tex_hash, {
                                'format': record.get('format') or '',
                                'width': record.get('width') or 0,
                                'height': record.get('height') or 0,
                            })
                            for variant in record.get('variants') or []:
                                if isinstance(variant, str):
                                    info.setdefault('variants', [])
                                    if variant not in info['variants']:
                                        info['variants'].append(variant)
                        slot_id = int(slot_found.group(1))
                        rec_fresh = record.get('fresh')
                        if not isinstance(rec_fresh, bool):
                            rec_fresh = None
                        if (freshness is not None and rec_fresh is not None
                                and slot_id in pair_out
                                and pair_out[slot_id] != tex_hash):
                            seat_key = (comp_id, ps_hash, slot_id)
                            seated = freshness.get(seat_key)
                            if rec_fresh and seated is False:
                                pair_out[slot_id] = tex_hash
                                freshness[seat_key] = True
                            elif not rec_fresh and seated is True:
                                pass
                            else:
                                pair_out[slot_id] = None
                            continue
                        _ingest_slot(pair_out, slot_id, tex_hash)
                        if (freshness is not None and rec_fresh is not None
                                and pair_out.get(slot_id) == tex_hash):
                            seat_key = (comp_id, ps_hash, slot_id)
                            freshness[seat_key] = bool(freshness.get(seat_key)) or rec_fresh
                continue

            ps_found = _PS_RE.search(pair_key)
            if not ps_found:
                warnings.append(f'{source}: pair "{pair_key}" has no ps hash, skipped')
                continue
            pair_out = comp_out.setdefault(ps_found.group(1), {})
            for slot_name, tex_hash in (value or {}).items():
                slot_found = _SLOT_RE.match(slot_name)
                if not slot_found:
                    continue
                if not isinstance(tex_hash, str):
                    tex_hash = None
                _ingest_slot(pair_out, int(slot_found.group(1)), tex_hash)
    return out


def load_forms(object_source_folder: Path,
               freshness_out: Optional[List[Dict[Tuple[int, str, int], bool]]] = None,
               ) -> Tuple[List[Tuple[str, FormData]], TextureInfo, List[str]]:
    """Load base + extra form maps from ShaderTextureUsage.json."""
    warnings: List[str] = []
    texture_info: TextureInfo = {}
    base_path = Path(object_source_folder) / constants.BASE_USAGE_FILENAME
    if not base_path.is_file():
        raise SlotStyleDegrade(
            f'{constants.BASE_USAGE_FILENAME} not found in the object source folder '
            f'(re-extract the object with a current Velo Tools build)')
    try:
        with open(base_path, encoding='utf-8') as f:
            base_raw = json.load(f)
    except Exception as e:
        raise SlotStyleDegrade(f'failed to read {constants.BASE_USAGE_FILENAME}: {e}')

    def _form_freshness() -> Optional[Dict[Tuple[int, str, int], bool]]:
        return {} if freshness_out is not None else None

    base_fresh = _form_freshness()
    forms: List[Tuple[str, FormData]] = [
        ('base', normalize_usage(base_raw, 'base', warnings, texture_info, base_fresh))]
    if freshness_out is not None:
        freshness_out.append(base_fresh)

    extra_entries = base_raw.get(constants.EXTRA_FORMS_KEY)
    if not extra_entries:
        legacy_path = Path(object_source_folder) / constants.LEGACY_SIDECAR_FILENAME
        if legacy_path.is_file():
            try:
                with open(legacy_path, encoding='utf-8') as f:
                    extra_entries = (json.load(f) or {}).get(constants.EXTRA_FORMS_KEY)
                warnings.append(
                    f'legacy {constants.LEGACY_SIDECAR_FILENAME} used - re-run the form '
                    f'merge once to migrate it into {constants.BASE_USAGE_FILENAME}')
            except Exception as e:
                raise SlotStyleDegrade(
                    f'failed to read {constants.LEGACY_SIDECAR_FILENAME}: {e}')
    for entry in extra_entries or []:
        label = entry.get('label') or entry.get('source') or f'form{len(forms) + 1}'
        entry_fresh = _form_freshness()
        forms.append((label, normalize_usage(entry.get('components'), label,
                                             warnings, texture_info, entry_fresh)))
        if freshness_out is not None:
            freshness_out.append(entry_fresh)

    if freshness_out is not None and not any(freshness_out):
        warnings.append(
            f'{constants.BASE_USAGE_FILENAME} has no binding-freshness flags - '
            'dirty slot filtering is unavailable for this extraction')
    return forms, texture_info, warnings


def read_local_discriminator_audit(object_source_folder: Path) -> object:
    base_path = Path(object_source_folder) / constants.BASE_USAGE_FILENAME
    try:
        with open(base_path, encoding='utf-8') as f:
            raw = json.load(f)
    except Exception as e:
        raise SlotStyleDegrade(f'failed to read local discriminator audit: {e}')
    if not isinstance(raw, dict):
        raise SlotStyleDegrade(
            f'{constants.BASE_USAGE_FILENAME} has an unexpected shape')
    return raw.get(constants.LOCAL_FORM_DISCRIMINATOR_KEY)


def build_local_discriminator_audit_from_usage(usage: dict) -> dict:
    warnings: List[str] = []
    texture_info: TextureInfo = {}
    freshness: List[Dict[Tuple[int, str, int], bool]] = []
    source_meta: Dict[Tuple[str, int], List[str]] = {}

    def _freshness() -> Dict[Tuple[int, str, int], bool]:
        fresh: Dict[Tuple[int, str, int], bool] = {}
        freshness.append(fresh)
        return fresh

    def _collect_source_meta(label: str, container: dict):
        raw = (container.get(constants.LOCAL_COMPONENT_SOURCES_KEY)
               if isinstance(container, dict) else None)
        if not isinstance(raw, dict):
            return
        for comp_name, values in raw.items():
            found = _COMP_RE.search(str(comp_name))
            if not found:
                continue
            if isinstance(values, str):
                items = [values]
            elif isinstance(values, list):
                items = [item for item in values if isinstance(item, str)]
            else:
                continue
            if items:
                source_meta.setdefault((label, int(found.group(1))), []).extend(items)

    forms: List[Tuple[str, FormData]] = [
        ('base', normalize_usage(usage, 'base', warnings, texture_info, _freshness()))]
    _collect_source_meta('base', usage)
    for entry in usage.get(constants.EXTRA_FORMS_KEY) or []:
        if not isinstance(entry, dict):
            continue
        label = entry.get('label') or entry.get('source') or f'form{len(forms) + 1}'
        forms.append((label, normalize_usage(entry.get('components'), label,
                                             warnings, texture_info, _freshness())))
        _collect_source_meta(label, entry)
    return build_local_discriminator_audit(
        forms, texture_info, freshness, warnings, source_meta=source_meta)


# -------------------------------------------------------------------- plan --

@dataclass
class SlotPlan:
    block_text: str
    component_list_names: Dict[int, str]
    covered_resource_indices: Set[int]
    blind_zone: List[Tuple[str, str]]
    multi_form: bool
    used_slots: List[int]
    phantom_only_resource_indices: Set[int] = field(default_factory=set)
    phantom_suppressed: List[Tuple[str, str]] = field(default_factory=list)
    extra_globals: List[str] = field(default_factory=list)
    watchdog_lines: List[str] = field(default_factory=list)
    default_form_id: int = 1
    live_fallback: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, object] = field(default_factory=dict)
    format_diagnostics: Dict[str, object] = field(default_factory=dict)


@dataclass(eq=False)
class _Branch:
    signature: Tuple[Tuple[int, float], ...]
    assign: Dict[int, str]
    form_gate: Optional[int]
    source: str


@dataclass(eq=False)
class _LocalBranch:
    signature: Tuple[Tuple[int, float], ...]
    negative_signature: Tuple[Tuple[int, float], ...]
    ps_signature: Optional[int]
    assign: Dict[int, str]
    form_id: int
    label: str
    ps: str
    source: str


def _common_assignments(by_assign: Dict[Tuple[Tuple[int, str], ...], Set[int]]) -> Dict[int, str]:
    common: Optional[Dict[int, str]] = None
    for assign_key in by_assign:
        current = dict(assign_key)
        if common is None:
            common = current
            continue
        common = {
            slot: resource for slot, resource in common.items()
            if current.get(slot) == resource
        }
    return common or {}


def _eligible_slots(pair_map: Dict[int, Optional[str]]) -> Set[int]:
    return set(pair_map)


def _local_signature_slots(pair_map: Dict[int, Optional[str]]) -> Set[int]:
    return set(pair_map) & set(constants.LOCAL_DISCRIMINATOR_SLOTS)


def _canonical_override_slots(pair_map: Dict[int, Optional[str]],
                              texture_info: TextureInfo) -> Set[int]:
    return _eligible_slots(pair_map)


def _component_hash_canonical_slots(forms: List[Tuple[str, FormData]],
                                    alias: Dict[str, str]) -> Dict[Tuple[int, str], int]:
    out: Dict[Tuple[int, str], int] = {}
    for _label, form_data in forms:
        for comp_id, comp_pairs in form_data.items():
            for pair_map in comp_pairs.values():
                for slot, tex_hash in pair_map.items():
                    if not isinstance(tex_hash, str):
                        continue
                    canon = alias.get(tex_hash, tex_hash)
                    key = (comp_id, canon)
                    previous = out.get(key)
                    if previous is None or slot < previous:
                        out[key] = slot
    return out


def _fresh_signature_slots(comp_id: int,
                           ps: str,
                           pair_map: Dict[int, Optional[str]],
                           form_fresh: Optional[Dict[Tuple[int, str, int], bool]]) -> Set[int]:
    if form_fresh is None:
        return set()
    return {
        slot for slot in _local_signature_slots(pair_map)
        if form_fresh.get((comp_id, ps, slot)) is True
    }


def _pass_role(pair_map: Dict[int, Optional[str]],
               texture_info: TextureInfo) -> str:
    if _is_material_pair(pair_map, texture_info):
        return 'material'
    slots = set(pair_map)
    if 2 in slots and 3 in slots and slots & set(constants.SERVICE_SLOTS):
        return 'outline'
    return 'auxiliary'


def _local_condition_slots(pair_map: Dict[int, Optional[str]],
                           role: str,
                           fresh_slots: Set[int]) -> Set[int]:
    return _local_signature_slots(pair_map)


def _local_assignment_slots(pair_map: Dict[int, Optional[str]],
                            texture_info: TextureInfo,
                            role: str,
                            fresh_slots: Set[int]) -> Set[int]:
    slots = _canonical_override_slots(pair_map, texture_info)
    if role == 'outline':
        return slots
    return slots & fresh_slots


def _is_material_pair(pair_map: Dict[int, Optional[str]],
                      texture_info: TextureInfo) -> bool:
    if not all(slot in pair_map for slot in constants.MAIN_SLOTS):
        return False
    if constants.MATERIAL_REQUIRE_SQUARE:
        for slot in constants.MAIN_SLOTS:
            h = pair_map.get(slot)
            info = texture_info.get(h) if h else None
            if info and info.get('width') and info.get('height') \
                    and info['width'] != info['height']:
                return False
    return True


def _family_key(tex_hash: Optional[str], texture_info: TextureInfo) -> Optional[float]:
    if not tex_hash:
        return None
    fmt = (texture_info.get(tex_hash) or {}).get('format')
    if not fmt:
        return None
    return _f32(constants.format_filter_index(fmt))


def _fi_str(value: float) -> str:
    text = repr(value)
    return text[:-2] if text.endswith('.0') else text


def _hash_fingerprint(forms: List[Tuple[str, FormData]],
                      texture_info: TextureInfo,
                      freshness: Optional[List[Dict[Tuple[int, str, int], bool]]] = None) -> str:
    """Stable fingerprint for the STU facts the local discriminator consumes."""
    import hashlib
    payload = []
    for form_id, (label, form_data) in enumerate(forms, start=1):
        form_fresh = (freshness[form_id - 1]
                      if freshness is not None and form_id - 1 < len(freshness)
                      else None)
        form_rows = []
        for comp_id in sorted(form_data):
            for ps in sorted(form_data[comp_id]):
                row = []
                for slot, tex_hash in sorted(form_data[comp_id][ps].items()):
                    info = texture_info.get(tex_hash or '') or {}
                    fresh = None
                    if form_fresh is not None:
                        fresh = form_fresh.get((comp_id, ps, slot))
                    row.append([slot, tex_hash, info.get('format') or '', fresh])
                form_rows.append([comp_id, ps, row])
        payload.append([label, form_rows])
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _signature_key(pair_map: Dict[int, Optional[str]],
                   texture_info: TextureInfo,
                   slots: Set[int],
                   alias: Optional[Dict[str, str]] = None) -> Tuple[Tuple[int, float], ...]:
    signature: List[Tuple[int, float]] = []
    for slot in sorted(slots):
        tex_hash = pair_map.get(slot)
        if tex_hash is not None and alias is not None:
            tex_hash = alias.get(tex_hash, tex_hash)
        key = _family_key(tex_hash, texture_info)
        if key is not None:
            signature.append((slot, key))
    return tuple(signature)


def _condition_source(comp_id: int,
                      ps: str,
                      slots: Set[int],
                      form_fresh: Optional[Dict[Tuple[int, str, int], bool]]) -> str:
    if not slots:
        return 'none'
    if form_fresh is None:
        return 'observed'
    flags = [form_fresh.get((comp_id, ps, slot)) for slot in sorted(slots)]
    if all(flag is True for flag in flags):
        return 'fresh'
    if any(flag is True for flag in flags):
        return 'mixed'
    return 'observed'


def _source_meta_for(source_meta: Optional[Dict[Tuple[str, int], List[str]]],
                     label: str,
                     comp_id: int) -> List[str]:
    if not source_meta:
        return []
    return list(source_meta.get((label, comp_id), ()))


def _minimal_negative_signature(positive: Tuple[Tuple[int, float], ...],
                                own_signatures: Set[Tuple[Tuple[int, float], ...]],
                                other_signatures: Set[Tuple[Tuple[int, float], ...]]
                                ) -> Optional[Tuple[Tuple[int, float], ...]]:
    blocking = [
        other for other in other_signatures
        if all(term in other for term in positive)
    ]
    if not blocking:
        return ()
    candidates: List[Tuple[int, float]] = []
    for other in blocking:
        for term in other:
            if term not in positive and term not in candidates:
                candidates.append(term)
    for term in sorted(candidates):
        if any(term in own for own in own_signatures):
            continue
        if all(term in other for other in blocking):
            return (term,)
    return None


def _serialized_signature(signature: Tuple[Tuple[int, float], ...]) -> List[List[object]]:
    return [[slot, _fi_str(key)] for slot, key in signature]


def _signature_from_audit(value: object) -> Tuple[Tuple[int, float], ...]:
    if not isinstance(value, list):
        return ()
    out: List[Tuple[int, float]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            return ()
        try:
            out.append((int(item[0]), _f32(float(item[1]))))
        except (TypeError, ValueError):
            return ()
    return tuple(out)


def _format_signature(signature: Tuple[Tuple[int, float], ...]) -> str:
    if not signature:
        return '[]'
    return '[' + ', '.join(
        f'ps-t{slot}={_fi_str(key)}' for slot, key in signature) + ']'


def _format_condition(positive: Tuple[Tuple[int, float], ...],
                      negative: Tuple[Tuple[int, float], ...]) -> str:
    terms = [f'ps-t{slot} == {_fi_str(key)}' for slot, key in positive]
    terms.extend(f'ps-t{slot} != {_fi_str(key)}' for slot, key in negative)
    return ' && '.join(terms) if terms else '(empty)'


def _local_audit_forms(forms: List[Tuple[str, FormData]],
                       filtered_forms: List[Tuple[str, FormData]],
                       texture_info: TextureInfo) -> List[Tuple[str, FormData]]:
    out: List[Tuple[str, FormData]] = []
    for index, (label, form_data) in enumerate(forms):
        filtered_data = (filtered_forms[index][1]
                         if index < len(filtered_forms) else {})
        out_form: FormData = {}
        for comp_id, comp_pairs in form_data.items():
            for ps, raw_pair in comp_pairs.items():
                role = _pass_role(raw_pair, texture_info)
                pair_map = raw_pair if role == 'outline' else (
                    filtered_data.get(comp_id, {}).get(ps))
                if not pair_map:
                    continue
                out_form.setdefault(comp_id, {})[ps] = pair_map
        out.append((label, out_form))
    return out


def build_local_discriminator_audit(forms: List[Tuple[str, FormData]],
                                    texture_info: TextureInfo,
                                    freshness: Optional[List[Dict[Tuple[int, str, int], bool]]] = None,
                                    warnings: Optional[List[str]] = None,
                                    source_meta: Optional[Dict[Tuple[str, int], List[str]]] = None) -> dict:
    """Build the STU audit block for local form discriminator export.

    The audit is intentionally data-only: export still recomputes and verifies
    the fingerprint before trusting it, so stale STU edits fail closed.
    """
    local_warnings: List[str] = list(warnings or [])
    filtered_forms, _dirty_hashes, dirty_slots, phantom_pairs = _filtered_forms(
        forms, freshness, [])
    audit_forms = _local_audit_forms(forms, filtered_forms, texture_info)
    alias = _variant_aliases(texture_info)
    canonical_seats = _component_hash_canonical_slots(audit_forms, alias)
    rows = []
    branch_samples: Dict[
        Tuple[int, Tuple[Tuple[int, str], ...], Tuple[Tuple[int, float], ...]],
        dict,
    ] = {}
    component_assignments: Dict[
        int,
        Dict[Tuple[Tuple[int, str], ...], Set[Tuple[Tuple[int, float], ...]]],
    ] = {}
    conflict_details: Dict[int, Dict[Tuple[Tuple[int, str], ...], dict]] = {}

    for form_id, (label, form_data) in enumerate(audit_forms, start=1):
        form_fresh = (freshness[form_id - 1]
                      if freshness is not None and form_id - 1 < len(freshness)
                      else None)
        for comp_id, comp_pairs in form_data.items():
            for ps, pair_map in comp_pairs.items():
                fresh_slots = _fresh_signature_slots(
                    comp_id, ps, pair_map, form_fresh)
                role = _pass_role(pair_map, texture_info)
                condition_slots = _local_condition_slots(
                    pair_map, role, fresh_slots)
                override_slots = _local_assignment_slots(
                    pair_map, texture_info, role, fresh_slots)
                sig = _signature_key(pair_map, texture_info,
                                     condition_slots, alias)
                if not sig:
                    continue
                condition_source = _condition_source(
                    comp_id, ps, set(slot for slot, _key in sig), form_fresh)
                observed_hashes = {
                    slot: tex_hash for slot, tex_hash in sorted(pair_map.items())
                    if isinstance(tex_hash, str)
                }
                assign_hashes = {
                    slot: tex_hash for slot, tex_hash in observed_hashes.items()
                    if (slot in override_slots
                        and canonical_seats.get((comp_id, alias.get(tex_hash, tex_hash))) == slot)
                }
                row = {
                    'form_id': form_id,
                    'form': label,
                    'component': comp_id,
                    'ps': ps,
                    'pass_role': role,
                    'condition_source': condition_source,
                    'signature': _serialized_signature(sig),
                    'positive_signature': _serialized_signature(sig),
                    'negative_signature': [],
                    'condition_slots': [slot for slot, _key in sig],
                    'assignment_slots': sorted(assign_hashes),
                    'assign_hashes': {str(slot): tex_hash
                                      for slot, tex_hash in assign_hashes.items()},
                    'observed_hashes': {str(slot): tex_hash
                                        for slot, tex_hash in observed_hashes.items()},
                    'fresh_slots': sorted(fresh_slots),
                    'canonical_slots': sorted(override_slots),
                }
                remap_sources = _source_meta_for(source_meta, label, comp_id)
                if remap_sources:
                    row['remap_sources'] = remap_sources
                rows.append(row)
                if assign_hashes:
                    akey = tuple(sorted((slot, tex_hash)
                                        for slot, tex_hash in assign_hashes.items()))
                    component_assignments.setdefault(
                        comp_id, {}).setdefault(akey, set()).add(sig)
                    detail = conflict_details.setdefault(comp_id, {}).setdefault(
                        akey, {'assign_hashes': assign_hashes, 'sources': []})
                    detail['sources'].append({
                        'form': label,
                        'ps': ps,
                        'signature': _serialized_signature(sig),
                        'pass_role': role,
                        'remap_sources': remap_sources,
                    })
                    sample_key = (comp_id, akey, sig)
                    sample = branch_samples.get(sample_key)
                    if sample is None:
                        branch_samples[sample_key] = {
                            'component': comp_id,
                            'signature': _serialized_signature(sig),
                            'positive_signature': _serialized_signature(sig),
                            'negative_signature': [],
                            'condition_slots': [slot for slot, _key in sig],
                            'assignment_slots': sorted(assign_hashes),
                            'assign_hashes': {str(slot): tex_hash
                                              for slot, tex_hash in assign_hashes.items()},
                            'pass_role': role,
                            'condition_source': condition_source,
                            'forms': [label],
                            'sources': [{'form': label, 'ps': ps}],
                        }
                        if remap_sources:
                            branch_samples[sample_key]['remap_sources'] = remap_sources
                    else:
                        if label not in sample['forms']:
                            sample['forms'].append(label)
                        sample['sources'].append({'form': label, 'ps': ps})

    conflicts = []
    for comp_id, by_assign in sorted(component_assignments.items()):
        if len(by_assign) <= 1:
            continue
        for assign_key, signatures in sorted(by_assign.items()):
            other_signatures = set()
            for other_key, values in by_assign.items():
                if other_key != assign_key:
                    other_signatures.update(values)
            for sig in sorted(signatures, key=lambda value: (-len(value), value)):
                negative = _minimal_negative_signature(
                    sig, set(signatures), other_signatures)
                sample = branch_samples.get((comp_id, assign_key, sig))
                if sample is None:
                    continue
                if negative is None:
                    sample['unresolved'] = True
                else:
                    sample['negative_signature'] = _serialized_signature(negative)

    unresolved_components: Set[int] = set()
    for comp_id, by_assign in sorted(component_assignments.items()):
        if len(by_assign) <= 1:
            continue
        for assign_key, signatures in sorted(by_assign.items()):
            has_branch = False
            for sig in signatures:
                sample = branch_samples.get((comp_id, assign_key, sig))
                if sample is None:
                    continue
                pos = _signature_from_audit(sample.get('positive_signature')
                                            or sample.get('signature'))
                neg = _signature_from_audit(sample.get('negative_signature'))
                blocked = False
                for other_key, other_signatures in by_assign.items():
                    if other_key == assign_key:
                        continue
                    for other_sig in other_signatures:
                        if all(term in other_sig for term in pos) and not any(
                                term in other_sig for term in neg):
                            blocked = True
                            break
                    if blocked:
                        break
                if not blocked:
                    has_branch = True
                    break
            if not has_branch:
                unresolved_components.add(comp_id)
        if comp_id in unresolved_components:
            conflicts.append({
                'component': comp_id,
                'reason': 'local slot-layout conditions cannot separate different assignments',
                'members': [
                    {
                        'assign_hashes': {str(slot): tex_hash
                                          for slot, tex_hash in assign_key},
                        'sources': detail['sources'],
                    }
                    for assign_key, detail in sorted(
                        conflict_details.get(comp_id, {}).items())
                ],
            })

    branches = list(branch_samples.values())
    for branch in branches:
        branch['forms'] = sorted(branch['forms'])

    return {
        'schema': constants.LOCAL_FORM_DISCRIMINATOR_SCHEMA,
        'fingerprint': _hash_fingerprint(forms, texture_info, freshness),
        'slots': list(constants.LOCAL_DISCRIMINATOR_SLOTS),
        'service_slots': list(constants.SERVICE_SLOTS),
        'rows': rows,
        'branches': branches,
        'conflicts': conflicts,
        'stats': {
            'forms': len(forms),
            'rows': len(rows),
            'branches': len(branches),
            'conflicts': len(conflicts),
            'dirty_slots': dirty_slots,
            'phantom_pairs': phantom_pairs,
        },
        'warnings': local_warnings,
    }


def validate_local_discriminator_audit(audit: object,
                                       forms: List[Tuple[str, FormData]],
                                       texture_info: TextureInfo,
                                       freshness: Optional[List[Dict[Tuple[int, str, int], bool]]] = None) -> dict:
    if not isinstance(audit, dict):
        raise SlotStyleDegrade(
            '局部形态判据审计缺失；请先刷新 ShaderTextureUsage.json 后再导出。')
    if audit.get('schema') != constants.LOCAL_FORM_DISCRIMINATOR_SCHEMA:
        raise SlotStyleDegrade(
            '局部形态判据审计版本过旧；请刷新 ShaderTextureUsage.json 后再导出。')
    expected = _hash_fingerprint(forms, texture_info, freshness)
    if audit.get('fingerprint') != expected:
        raise SlotStyleDegrade(
            '局部形态判据审计已过期；当前 STU 数据与审计 fingerprint 不一致，请刷新 STU 审计。')
    return audit


def _local_condition_matches(signature: Tuple[Tuple[int, float], ...],
                             negative_signature: Tuple[Tuple[int, float], ...],
                             target: Tuple[Tuple[int, float], ...]) -> bool:
    return (all(term in target for term in signature)
            and not any(term in target for term in negative_signature))


def _local_conflict_messages(records: List[dict],
                             slot_eligible_components: Optional[Set[int]]) -> List[dict]:
    row_by_component: Dict[int, List[dict]] = {}
    branch_by_component: Dict[int, List[dict]] = {}
    for record in records:
        assign_key = record.get('assign_key') or ()
        if not assign_key:
            assign_key = ()
        try:
            comp_id = int(record.get('component'))
        except (TypeError, ValueError):
            continue
        if slot_eligible_components is not None and comp_id not in slot_eligible_components:
            continue
        if record.get('kind') == 'row':
            row_by_component.setdefault(comp_id, []).append(record)
        elif record.get('kind') == 'branch':
            branch_by_component.setdefault(comp_id, []).append(record)
    problems = []
    for comp_id, branches in sorted(branch_by_component.items()):
        evidence = row_by_component.get(comp_id, [])
        issues = []
        seen_issues: Set[
            Tuple[
                Tuple[Tuple[int, float], ...],
                Tuple[Tuple[int, float], ...],
                Tuple[Tuple[Tuple[int, str], ...], ...],
            ],
        ] = set()
        for branch in branches:
            matched: Dict[Tuple[Tuple[int, str], ...], List[str]] = {}
            for record in evidence:
                if _local_condition_matches(
                        branch['signature'],
                        branch.get('negative_signature') or (),
                        record['signature']):
                    matched.setdefault(
                        record['assign_key'], []).append(record.get('source') or '?')
            if any(assign_key != branch['assign_key'] for assign_key in matched):
                issue_key = (
                    branch['signature'],
                    branch.get('negative_signature') or (),
                    tuple(sorted(matched)),
                )
                if issue_key in seen_issues:
                    continue
                seen_issues.add(issue_key)
                issues.append({
                    'signature': branch['signature'],
                    'negative_signature': branch.get('negative_signature') or (),
                    'branch_assign': branch['assign_key'],
                    'branch_source': branch.get('source') or '?',
                    'members': [
                        {'assign': assign_key, 'sources': sorted(set(sources))}
                        for assign_key, sources in sorted(matched.items())
                    ],
                })
        if issues:
            problems.append({'component': comp_id, 'issues': issues})
    return problems


def _format_local_conflict_message(problems: List[dict]) -> str:
    lines = [
        '局部形态判据无法为以下 Component 生成安全的本地 slot-layout 分支，已阻断导出：',
    ]
    for problem in problems:
        comp_id = problem['component']
        lines.append(f'- Component {comp_id}')
        for issue in problem['issues'][:4]:
            condition = _format_condition(
                issue["signature"], issue.get("negative_signature") or ())
            lines.append(f'  条件 {condition} 下存在多个有效贴图 assignment：')
            for member in issue['members'][:4]:
                assigns = ', '.join(
                    f'ps-t{slot}->{tex_hash}' for slot, tex_hash in member['assign'])
                sources = '; '.join(member['sources'])
                lines.append(f'    {assigns or "(no assignment)"} 来自 {sources}')
        if len(problem['issues']) > 4:
            lines.append(f'  另有 {len(problem["issues"]) - 4} 个冲突条件未展开。')
    lines.extend([
        '可选补救：',
        '1. 在“按组件选插槽风格”里取消上述 Component，只让它们回到 hash-style。',
        '2. 关闭“局部形态判据”，回到旧 form anchor 导出。',
        '3. 关闭“插槽风格贴图”，整体回到 hash-style。',
        '工具不会自动取消这些 Component，以免误以为它们仍在 local slot layer。',
    ])
    return '\n'.join(lines)


def _local_branches_from_audit(audit: dict,
                               mod_hashes: Dict[str, str],
                               canon_fn,
                               slot_eligible_components: Optional[Set[int]] = None,
                               ) -> Tuple[Dict[int, List[_LocalBranch]], Set[str]]:
    component_branches: Dict[int, List[_LocalBranch]] = {}
    assigned_hashes: Set[str] = set()
    branch_records: List[dict] = []

    def _source_from(entry: dict, fallback: str) -> str:
        sources = entry.get('sources') or []
        if isinstance(sources, list) and sources:
            joined = ','.join(
                f"{src.get('form', '?')}/ps={src.get('ps', '?')}"
                for src in sources if isinstance(src, dict))
            if joined:
                remap = _remap_source_from(entry)
                return joined + (f' ({remap})' if remap else '')
        form = entry.get('form')
        ps = entry.get('ps')
        if isinstance(form, str) or isinstance(ps, str):
            return f'{form or "?"}/ps={ps or "?"}'
        return fallback

    def _remap_source_from(entry: dict) -> str:
        values = entry.get('remap_sources')
        if isinstance(values, list):
            return ', '.join(str(value) for value in values if isinstance(value, str))
        return ''

    def _effective_assignment(raw_assign: object,
                              comp_id: int,
                              source: str) -> Tuple[Dict[int, str], Tuple[Tuple[int, str], ...]]:
        if not isinstance(raw_assign, dict):
            raise SlotStyleDegrade(
                f'component {comp_id}: local form discriminator {source} '
                'has no assignment map')
        assign: Dict[int, str] = {}
        assign_hashes: Dict[int, str] = {}
        for raw_slot, tex_hash in raw_assign.items():
            if not isinstance(tex_hash, str):
                continue
            canon = canon_fn(tex_hash)
            if canon in mod_hashes:
                try:
                    slot = int(raw_slot)
                except (TypeError, ValueError):
                    raise SlotStyleDegrade(
                        f'component {comp_id}: local form discriminator {source} '
                        f'has an invalid ps-t slot {raw_slot!r}')
                assign[slot] = mod_hashes[canon]
                assign_hashes[slot] = canon
                assigned_hashes.add(canon)
        return assign, tuple(sorted(assign_hashes.items()))

    rows = audit.get('rows')
    if not isinstance(rows, list):
        raise SlotStyleDegrade(
            '局部形态判据审计没有 row evidence；请刷新 STU 审计。')
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SlotStyleDegrade(
                f'local form discriminator row #{index} has an unsupported shape')
        try:
            comp_id = int(row['component'])
        except (KeyError, TypeError, ValueError):
            raise SlotStyleDegrade(
                f'local form discriminator row #{index} has no component id')
        if (slot_eligible_components is not None
                and comp_id not in slot_eligible_components):
            continue
        signature = _signature_from_audit(row.get('signature'))
        if not signature:
            continue
        source = _source_from(row, f'row#{index}')
        _assign, _assign_key = _effective_assignment(
            row.get('assign_hashes'), comp_id, source)
        branch_records.append({
            'kind': 'row',
            'component': comp_id,
            'signature': signature,
            'negative_signature': (),
            'assign_key': _assign_key,
            'source': source,
        })

    branches = audit.get('branches')
    if not isinstance(branches, list):
        raise SlotStyleDegrade(
            '局部形态判据审计没有 slot override branch；请刷新 STU 审计。')
    for index, entry in enumerate(branches):
        if not isinstance(entry, dict):
            raise SlotStyleDegrade(
                f'local form discriminator branch #{index} has an unsupported shape')
        try:
            comp_id = int(entry['component'])
        except (KeyError, TypeError, ValueError):
            raise SlotStyleDegrade(
                f'local form discriminator branch #{index} has no component id')
        signature = _signature_from_audit(
            entry.get('positive_signature') or entry.get('signature'))
        if not signature:
            raise SlotStyleDegrade(
                f'component {comp_id}: local form discriminator branch #{index} '
                'has no usable slot-format signature')
        source = _source_from(entry, f'branch#{index}')
        assign, _assign_key = _effective_assignment(
            entry.get('assign_hashes'), comp_id, f'branch #{index}')
        if not assign:
            continue
        negative_signature = _signature_from_audit(entry.get('negative_signature'))
        assign_key = tuple(sorted(
            (int(slot), canon_fn(tex_hash))
            for slot, tex_hash in (entry.get('assign_hashes') or {}).items()
            if str(slot).isdigit()
            and isinstance(tex_hash, str)
            and canon_fn(tex_hash) in mod_hashes
        ))
        branch_records.append({
            'kind': 'branch',
            'component': comp_id,
            'signature': signature,
            'negative_signature': negative_signature,
            'assign_key': assign_key,
            'source': source,
        })
        component_branches.setdefault(comp_id, []).append(_LocalBranch(
            signature=signature,
            negative_signature=negative_signature,
            ps_signature=None,
            assign=assign,
            form_id=1,
            label='local',
            ps='',
            source=source,
        ))
    problems = _local_conflict_messages(branch_records, slot_eligible_components)
    if problems:
        raise SlotStyleDegrade(_format_local_conflict_message(problems))
    if not component_branches:
        raise SlotStyleDegrade('no component produced any slot assignment')
    return component_branches, assigned_hashes


def _build_local_plan(forms: List[Tuple[str, FormData]],
                      textures: List[Tuple[str, str]],
                      texture_info: TextureInfo,
                      load_warnings: Optional[List[str]] = None,
                      component_ranges: Optional[Dict[int, Tuple[int, int]]] = None,
                      lod_ranges: Optional[Dict[int, Dict[int, Tuple[int, int]]]] = None,
                      multi_state_seats: Optional[Dict[Tuple[int, int], Set[str]]] = None,
                      live_seed: Optional[Set[str]] = None,
                      freshness: Optional[List[Dict[Tuple[int, str, int], bool]]] = None,
                      slot_eligible_components: Optional[Set[int]] = None,
                      local_discriminator_audit: Optional[dict] = None) -> SlotPlan:
    warnings: List[str] = list(load_warnings or [])
    validate_local_discriminator_audit(
        local_discriminator_audit, forms, texture_info, freshness)
    forms, dirty_hashes_raw, dirty_slots, phantom_pairs = _filtered_forms(
        forms, freshness, warnings)
    alias = _variant_aliases(texture_info)
    mod_hashes = {h: res for h, res in textures}
    if not mod_hashes:
        raise SlotStyleDegrade('mod has no textures, nothing to do')

    def _canon(tex_hash: Optional[str]) -> Optional[str]:
        if tex_hash is None:
            return None
        return alias.get(tex_hash, tex_hash)

    live_fallback: Dict[str, str] = {}

    def _route_live(tex_hash: str, reason: str):
        canon = _canon(tex_hash) or tex_hash
        if canon in mod_hashes:
            live_fallback.setdefault(canon, reason)

    for h in sorted(live_seed or ()):
        _route_live(h, 'caller-routed live seed')
    for (comp_id, slot), hashes in sorted((multi_state_seats or {}).items()):
        for h in sorted(hashes):
            _route_live(h, f'multi-state seat (component {comp_id}, ps-t{slot})')

    fi_text: Dict[float, str] = {}
    group_families: Dict[float, Dict[str, Tuple[str, str]]] = {}
    for info in texture_info.values():
        fmt = info.get('format')
        if not fmt:
            continue
        fi = constants.format_filter_index(fmt)
        key = _f32(fi)
        fi_text.setdefault(key, _fi_str(fi))
        group_families.setdefault(key, {}).setdefault(
            constants.format_prefix(fmt), (fmt, _fi_str(fi)))

    component_branches, raw_assigned_hashes = _local_branches_from_audit(
        local_discriminator_audit, mod_hashes, _canon, slot_eligible_components)
    if slot_eligible_components is not None:
        resource_to_hash_for_exclusion = {res: h for h, res in textures}
        excluded_hashes: Set[str] = set()
        for comp_id in list(component_branches):
            if comp_id in slot_eligible_components:
                continue
            for branch in component_branches.pop(comp_id):
                for resource in branch.assign.values():
                    tex_hash = resource_to_hash_for_exclusion.get(resource)
                    if tex_hash:
                        excluded_hashes.add(tex_hash)
        included_hashes = {
            resource_to_hash_for_exclusion[resource]
            for branches in component_branches.values()
            for branch in branches
            for resource in branch.assign.values()
            if resource in resource_to_hash_for_exclusion
        }
        for h in sorted(excluded_hashes - included_hashes):
            _route_live(h, 'component excluded from slot layer')
    if not component_branches:
        if live_fallback:
            raise SlotStyleDegrade(
                'all slot candidates were routed to stock hash sections; no '
                'slot command lists were emitted')
        raise SlotStyleDegrade('no component produced any slot assignment')

    merged_component_branches: Dict[int, List[_LocalBranch]] = {}
    for comp_id, branches in component_branches.items():
        merged: List[_LocalBranch] = []
        seen: Dict[
            Tuple[
                Tuple[Tuple[int, float], ...],
                Tuple[Tuple[int, float], ...],
                Tuple[Tuple[int, str], ...],
            ],
            _LocalBranch,
        ] = {}
        for branch in branches:
            key = (
                branch.signature,
                branch.negative_signature,
                tuple(sorted(branch.assign.items())),
            )
            previous = seen.get(key)
            if previous is not None:
                warnings.append(
                    f'component {comp_id}: duplicate local discriminator '
                    f'condition for {previous.source} and {branch.source}; '
                    'kept one identical assignment branch')
                continue
            seen[key] = branch
            merged.append(branch)
        merged_component_branches[comp_id] = merged
    component_branches = merged_component_branches

    resource_to_hash = {res: h for h, res in textures}
    all_assigned_hashes: Set[str] = set()
    for branches in component_branches.values():
        for branch in branches:
            for resource in branch.assign.values():
                tex_hash = resource_to_hash.get(resource)
                if tex_hash:
                    all_assigned_hashes.add(tex_hash)

    used_slots: Set[int] = set()
    used_families: Dict[int, Set[float]] = {}
    component_list_names: Dict[int, str] = {}
    body_chunks: List[str] = []

    def _terms(comp_id: int, branch: _LocalBranch) -> List[str]:
        terms: List[str] = []
        for slot, key in branch.signature:
            text = fi_text.get(key)
            if text is None:
                continue
            used_families.setdefault(comp_id, set()).add(key)
            terms.append(f'ps-t{slot} == {text}')
        for slot, key in branch.negative_signature:
            text = fi_text.get(key)
            if text is None:
                continue
            used_families.setdefault(comp_id, set()).add(key)
            terms.append(f'ps-t{slot} != {text}')
        if branch.ps_signature is not None:
            terms.append(
                f'(ps == {branch.ps_signature} || vs == {branch.ps_signature})')
        return terms

    def _append_assignments(chunk: List[str], assign: Dict[int, str], indent: str):
        for slot, res in sorted(assign.items()):
            chunk.append(f'{indent}ps-t{slot} = ref {res}')
            used_slots.add(slot)

    for comp_id in sorted(component_branches):
        name = constants.CMDLIST_SET_TEXTURES.format(component_id=comp_id)
        component_list_names[comp_id] = name
        chunk: List[str] = ['', f'[{name}]', 'if $object_detected == 1']
        ordered = sorted(
            component_branches[comp_id],
            key=lambda b: (-len(b.signature), b.form_id, b.signature,
                           tuple(sorted(b.assign.items()))))
        first = True
        for branch in ordered:
            terms = _terms(comp_id, branch)
            if not terms:
                continue
            chunk.append(f'    {"if" if first else "else if"} ' + ' && '.join(terms))
            _append_assignments(chunk, branch.assign, '        ')
            first = False
        if not first:
            chunk.append('    endif')
            chunk.append('endif')
            body_chunks.append('\n'.join(chunk))
        else:
            component_list_names.pop(comp_id, None)

    if not body_chunks:
        raise SlotStyleDegrade('no component produced a complete slot condition')

    covered_resource_indices: Set[int] = set()
    blind_zone: List[Tuple[str, str]] = []
    dirty_hashes = {_canon(h) or h for h in dirty_hashes_raw}
    dirty_only_hashes = dirty_hashes - all_assigned_hashes
    phantom_only_resource_indices: Set[int] = set()
    phantom_suppressed: List[Tuple[str, str]] = []
    has_freshness_evidence = freshness is not None and any(freshness)
    for index, (h, _res) in enumerate(textures):
        canon = _canon(h) or h
        if canon in all_assigned_hashes and canon not in live_fallback:
            covered_resource_indices.add(index)
        elif canon in dirty_only_hashes:
            section = f'TextureOverrideTexture{index}'
            phantom_only_resource_indices.add(index)
            phantom_suppressed.append((h, section))
        elif canon not in live_fallback:
            section = f'TextureOverrideTexture{index}'
            if has_freshness_evidence:
                phantom_only_resource_indices.add(index)
                phantom_suppressed.append((h, section))
            else:
                blind_zone.append((h, section))

    out: List[str] = []
    form_sources = ', '.join(label for label, _ in forms)
    out.append('')
    out.append('; ============================================================')
    out.append('; Slot-style texture layer (local form discriminator)')
    out.append(f'; Forms: {form_sources}')
    out.append('; Conditions are audited per-draw slot-layout branches;')
    out.append('; ps-t0..8 may be used as condition slots, while assignment')
    out.append('; slots are limited to canonical override seats with mod resources.')
    out.append('; object_detected is an outer scope gate, not a form discriminator.')
    out.append('; no global form state or form-anchor watchdog is used.')
    out.append('; ============================================================')
    out.extend(body_chunks)

    format_section_count = 0
    out.append('')
    out.append('; -- Format-family tags')
    for comp_id in sorted(used_families):
        crange = (component_ranges or {}).get(comp_id)
        if crange is None:
            raise SlotStyleDegrade(
                f'component {comp_id} index range unknown - cannot emit its '
                f'format tag sections')
        ranges = [(constants.SEC_FORMAT_TAG, None, crange)]
        for level, lranges in sorted((lod_ranges or {}).items()):
            if comp_id in lranges:
                ranges.append((constants.SEC_FORMAT_TAG_LOD, level, lranges[comp_id]))
        for key in sorted(used_families[comp_id]):
            for prefix in sorted(group_families.get(key, {})):
                name, text = group_families[key][prefix]
                for member in constants.emitted_format_members(name):
                    for template, level, (first, count) in ranges:
                        out.append('')
                        out.append('[' + template.format(component_id=comp_id,
                                                         format_name=member,
                                                         level=level) + ']')
                        out.append(f'match_first_index = {first}')
                        out.append(f'match_index_count = {count}')
                        out.append(f'match_priority = {constants.FORMAT_TAG_PRIORITY}')
                        out.append(f'match_format = {member}')
                        out.append(f'filter_index = {text}')
                        format_section_count += 1
    out.append('')

    return SlotPlan(
        block_text='\n'.join(out),
        component_list_names=component_list_names,
        covered_resource_indices=covered_resource_indices,
        blind_zone=blind_zone,
        multi_form=False,
        used_slots=sorted(used_slots),
        phantom_only_resource_indices=phantom_only_resource_indices,
        phantom_suppressed=phantom_suppressed,
        extra_globals=[],
        watchdog_lines=[],
        default_form_id=1,
        live_fallback=dict(live_fallback),
        warnings=warnings,
        stats={
            'forms': len(forms),
            'components': len(component_list_names),
            'branches': sum(len(b) for b in component_branches.values()),
            'conflicts': 0,
            'marks': 0,
            'fork_latches': 0,
            'anchors': 0,
            'anchor_watchdog': 0,
            'probes': 0,
            'live_fallback': len(live_fallback),
            'format_sections': format_section_count,
            'format_sections_raw': format_section_count,
            'format_sections_unique': format_section_count,
            'format_sections_removed': 0,
            'covered_textures': len(covered_resource_indices),
            'blind_zone_textures': len(blind_zone),
            'phantom_suppressed_textures': len(phantom_only_resource_indices),
            'phantom_pairs': phantom_pairs,
            'dirty_slots': dirty_slots,
            'service_slots': len([s for s in used_slots if s in constants.SERVICE_SLOTS]),
            'suppressed_latches': 0,
            'local_form_discriminator': 1,
        },
    )


def _variant_aliases(texture_info: TextureInfo) -> Dict[str, str]:
    alias: Dict[str, str] = {}
    for canon, info in texture_info.items():
        for variant in info.get('variants', ()) or ():
            if isinstance(variant, str):
                alias.setdefault(variant, canon)
    return alias


def _filtered_forms(forms: List[Tuple[str, FormData]],
                    freshness: Optional[List[Dict[Tuple[int, str, int], bool]]],
                    warnings: List[str]) -> Tuple[List[Tuple[str, FormData]], Set[str], int, int]:
    if freshness is None or not any(freshness):
        return forms, set(), 0, 0

    dirty_hashes: Set[str] = set()
    dirty_slots = 0
    phantom_pairs = 0
    filtered: List[Tuple[str, FormData]] = []
    for form_id, (label, form_data) in enumerate(forms, start=1):
        form_fresh = (freshness[form_id - 1]
                      if form_id - 1 < len(freshness) else None) or {}
        out_form: FormData = {}
        for comp_id, comp_pairs in form_data.items():
            out_pairs: Dict[str, Dict[int, Optional[str]]] = {}
            for ps, pair_map in comp_pairs.items():
                out_pair: Dict[int, Optional[str]] = {}
                flags_seen = False
                for slot, tex_hash in pair_map.items():
                    flag = form_fresh.get((comp_id, ps, slot))
                    if flag is not None:
                        flags_seen = True
                    if flag is False:
                        dirty_slots += 1
                        if isinstance(tex_hash, str):
                            dirty_hashes.add(tex_hash)
                        continue
                    out_pair[slot] = tex_hash
                if out_pair:
                    out_pairs[ps] = out_pair
                elif flags_seen:
                    phantom_pairs += 1
                    warnings.append(
                        f'form "{label}" component {comp_id}: pair ps={ps} '
                        f'contains only dirty/stale slots and was dropped')
            if out_pairs:
                out_form[comp_id] = out_pairs
        filtered.append((label, out_form))
    if dirty_slots:
        warnings.append(
            f'dirty slot filtering removed {dirty_slots} stale-inherited slot '
            f'record(s) before branch generation')
    return filtered, dirty_hashes, dirty_slots, phantom_pairs


def build_plan(forms: List[Tuple[str, FormData]],
               textures: List[Tuple[str, str]],
               texture_info: TextureInfo,
               load_warnings: Optional[List[str]] = None,
               component_ranges: Optional[Dict[int, Tuple[int, int]]] = None,
               lod_ranges: Optional[Dict[int, Dict[int, Tuple[int, int]]]] = None,
               manual_anchors: Optional[List[Tuple[str, int]]] = None,
               multi_state_seats: Optional[Dict[Tuple[int, int], Set[str]]] = None,
               live_seed: Optional[Set[str]] = None,
               trusted_hashes: Optional[Set[str]] = None,
               freshness: Optional[List[Dict[Tuple[int, str, int], bool]]] = None,
               slot_eligible_components: Optional[Set[int]] = None,
               local_form_discriminator: bool = False,
               local_discriminator_audit: Optional[dict] = None) -> SlotPlan:
    """Build a concise XQFA-style slot plan.

    The legacy probe/mark/backup/restore machinery is intentionally absent:
    if a form or same-signature case cannot be represented by format slots and
    user-provided form anchors, generation degrades instead of emitting a
    complex fallback path.

    Local discriminator mode is stricter: it consumes audited slot override
    branches only and never adds form-anchor or current-shader fallback state.
    """
    if local_form_discriminator:
        return _build_local_plan(
            forms, textures, texture_info, load_warnings,
            component_ranges=component_ranges,
            lod_ranges=lod_ranges,
            multi_state_seats=multi_state_seats,
            live_seed=live_seed,
            freshness=freshness,
            slot_eligible_components=slot_eligible_components,
            local_discriminator_audit=local_discriminator_audit)
    warnings: List[str] = list(load_warnings or [])
    multi_form = len(forms) > 1
    mod_hashes = {h: res for h, res in textures}
    if not mod_hashes:
        raise SlotStyleDegrade('mod has no textures, nothing to do')

    has_freshness_evidence = freshness is not None and any(freshness)
    forms, dirty_hashes_raw, dirty_slots, phantom_pairs = _filtered_forms(
        forms, freshness, warnings)
    alias = _variant_aliases(texture_info)

    live_fallback: Dict[str, str] = {}

    def _canon(tex_hash: Optional[str]) -> Optional[str]:
        if tex_hash is None:
            return None
        return alias.get(tex_hash, tex_hash)

    def _route_live(tex_hash: str, reason: str):
        canon = _canon(tex_hash) or tex_hash
        if canon in mod_hashes:
            live_fallback.setdefault(canon, reason)

    for h in sorted(live_seed or ()):
        _route_live(h, 'caller-routed live seed')
    for (comp_id, slot), hashes in sorted((multi_state_seats or {}).items()):
        for h in sorted(hashes):
            _route_live(h, f'multi-state seat (component {comp_id}, ps-t{slot})')

    anchor_resources: List[Tuple[str, int]] = []
    anchor_shaders: List[Tuple[str, int]] = []
    for anchor_hash, form_id in (manual_anchors or []):
        if not isinstance(anchor_hash, str) or form_id < 1 or form_id > len(forms):
            warnings.append(f'form anchor {anchor_hash!r} skipped (bad form id)')
            continue
        h = anchor_hash.strip().lower()
        if re.fullmatch(r'[0-9a-f]{8}', h):
            anchor_resources.append((h, form_id))
        elif re.fullmatch(r'[0-9a-f]{16}', h):
            anchor_shaders.append((h, form_id))
        else:
            warnings.append(
                f'form anchor {anchor_hash!r} skipped (expected an 8-hex '
                f'vb0 hash or a 16-hex ps hash)')

    anchored_forms = {f for _, f in anchor_resources + anchor_shaders}
    unanchored_forms = set(range(1, len(forms) + 1)) - anchored_forms
    watchdog_form = None
    if multi_form and anchored_forms and len(unanchored_forms) == 1:
        watchdog_form = next(iter(unanchored_forms))
    forms_fully_covered = (not multi_form) or (bool(anchored_forms) and len(unanchored_forms) <= 1)

    fi_text: Dict[float, str] = {}
    group_families: Dict[float, Dict[str, Tuple[str, str]]] = {}
    for info in texture_info.values():
        fmt = info.get('format')
        if not fmt:
            continue
        fi = constants.format_filter_index(fmt)
        key = _f32(fi)
        fi_text.setdefault(key, _fi_str(fi))
        group_families.setdefault(key, {}).setdefault(
            constants.format_prefix(fmt), (fmt, _fi_str(fi)))

    raw_branches: Dict[int, List[_Branch]] = {}
    raw_assigned_hashes: Set[str] = set()
    excluded_component_hashes: Set[str] = set()

    for form_id, (label, form_data) in enumerate(forms, start=1):
        for comp_id, comp_pairs in form_data.items():
            for ps, pair_map in comp_pairs.items():
                eligible_slots = _eligible_slots(pair_map)
                if not has_freshness_evidence:
                    eligible_slots -= set(constants.SERVICE_SLOTS)
                assign_slots = eligible_slots
                assigned: Dict[int, str] = {}
                assigned_hashes: Dict[int, str] = {}
                for slot, tex_hash in pair_map.items():
                    if slot not in assign_slots:
                        continue
                    canon = _canon(tex_hash)
                    if canon in mod_hashes and canon not in live_fallback:
                        assigned[slot] = mod_hashes[canon]
                        assigned_hashes[slot] = canon
                if not assigned:
                    continue
                if slot_eligible_components is not None and comp_id not in slot_eligible_components:
                    excluded_component_hashes.update(assigned_hashes.values())
                    continue

                sig_slots = eligible_slots
                signature: List[Tuple[int, float]] = []
                for slot in sorted(sig_slots):
                    canon = _canon(pair_map.get(slot))
                    key = _family_key(canon, texture_info)
                    if key is not None:
                        signature.append((slot, key))
                if not signature:
                    warnings.append(
                        f'form "{label}" component {comp_id} ps={ps}: no '
                        f'recorded DXGI formats for slot conditions, skipped')
                    continue

                raw_branches.setdefault(comp_id, []).append(_Branch(
                    signature=tuple(signature),
                    assign=assigned,
                    form_gate=form_id if multi_form else None,
                    source=f'{label}/ps={ps}',
                ))
                raw_assigned_hashes.update(assigned_hashes.values())

    for h in sorted(excluded_component_hashes - raw_assigned_hashes):
        _route_live(h, 'component excluded from slot layer')

    if not raw_branches:
        if live_fallback:
            raise SlotStyleDegrade(
                'all slot candidates were routed to stock hash sections; no '
                'slot command lists were emitted')
        raise SlotStyleDegrade('no component produced any slot assignment')

    component_branches: Dict[int, List[_Branch]] = {}
    conflict_count = 0
    for comp_id, branches in raw_branches.items():
        by_signature: Dict[Tuple[Tuple[int, float], ...], List[_Branch]] = {}
        for branch in branches:
            by_signature.setdefault(branch.signature, []).append(branch)

        merged: List[_Branch] = []
        for signature, members in sorted(by_signature.items()):
            by_assign: Dict[Tuple[Tuple[int, str], ...], Set[int]] = {}
            sample: Dict[Tuple[Tuple[int, str], ...], _Branch] = {}
            for member in members:
                assign_key = tuple(sorted(member.assign.items()))
                by_assign.setdefault(assign_key, set()).add(member.form_gate or 0)
                sample.setdefault(assign_key, member)
            if len(by_assign) > 1:
                conflict_count += len(by_assign) - 1
                seen_forms: Set[int] = set()
                overlapping_forms: Set[int] = set()
                for form_ids in by_assign.values():
                    overlapping_forms.update(seen_forms & form_ids)
                    seen_forms |= form_ids
                if overlapping_forms:
                    common = _common_assignments(by_assign)
                    if not common:
                        raise SlotStyleDegrade(
                            f'component {comp_id}: one form has multiple texture '
                            f'sets under the same format signature')
                    warnings.append(
                        f'component {comp_id}: ambiguous same-form texture slots '
                        f'under one format signature were skipped; common slots '
                        f'{sorted(common)} kept')
                    gate_ids = sorted(seen_forms)
                    if not multi_form:
                        gate_ids = [0]
                    elif not forms_fully_covered:
                        raise SlotStyleDegrade(
                            'multi-form slot branches need manual form anchors; '
                            'add vb0:label anchors with the form finder before '
                            'using concise slot export')
                    for form_id in gate_ids:
                        merged.append(_Branch(
                            signature=signature,
                            assign=dict(common),
                            form_gate=form_id or None,
                            source=sample[next(iter(by_assign))].source,
                        ))
                    continue
                if not multi_form or not forms_fully_covered:
                    raise SlotStyleDegrade(
                        f'component {comp_id}: multiple texture sets share one '
                        f'format signature; add/refresh form anchors or re-extract '
                        f'with Skip Dirty Slot enabled')
                for assign_key, form_ids in sorted(by_assign.items()):
                    member = sample[assign_key]
                    for form_id in sorted(form_ids):
                        if form_id == 0:
                            raise SlotStyleDegrade(
                                f'component {comp_id}: single-form branch joined '
                                f'a multi-form conflict')
                        merged.append(_Branch(
                            signature=signature,
                            assign=dict(assign_key),
                            form_gate=form_id,
                            source=member.source,
                        ))
                continue
            assign_key, form_ids = next(iter(by_assign.items()))
            member = sample[assign_key]
            if multi_form and form_ids != set(range(1, len(forms) + 1)):
                if not forms_fully_covered:
                    raise SlotStyleDegrade(
                        'multi-form slot branches need manual form anchors; '
                        'add vb0:label anchors with the form finder before '
                        'using concise slot export')
                for form_id in sorted(form_ids):
                    merged.append(_Branch(
                        signature=signature,
                        assign=dict(assign_key),
                        form_gate=form_id,
                        source=member.source,
                    ))
            else:
                merged.append(_Branch(
                    signature=signature,
                    assign=dict(assign_key),
                    form_gate=None,
                    source=member.source,
                ))
        component_branches[comp_id] = merged

    resource_to_hash = {res: h for h, res in textures}
    all_assigned_hashes: Set[str] = set()
    for branches in component_branches.values():
        for branch in branches:
            for resource in branch.assign.values():
                tex_hash = resource_to_hash.get(resource)
                if tex_hash:
                    all_assigned_hashes.add(tex_hash)

    used_slots: Set[int] = set()
    used_families: Dict[int, Set[float]] = {}
    component_list_names: Dict[int, str] = {}
    body_chunks: List[str] = []

    def _terms(comp_id: int, branch: _Branch) -> List[str]:
        terms: List[str] = []
        for slot, key in branch.signature:
            text = fi_text.get(key)
            if text is None:
                continue
            used_families.setdefault(comp_id, set()).add(key)
            terms.append(f'ps-t{slot} == {text}')
        return terms

    def _condition(terms: List[str], form_gate: Optional[int] = None) -> str:
        parts = list(terms)
        if form_gate is not None:
            parts.append(f'{constants.VAR_FORM} == {form_gate}')
        return ' && '.join(parts)

    def _append_assignments(chunk: List[str], assign: Dict[int, str],
                            indent: str):
        for slot, res in sorted(assign.items()):
            chunk.append(f'{indent}ps-t{slot} = ref {res}')
            used_slots.add(slot)

    for comp_id in sorted(component_branches):
        name = constants.CMDLIST_SET_TEXTURES.format(component_id=comp_id)
        component_list_names[comp_id] = name
        chunk: List[str] = ['', f'[{name}]']
        for h, form_id in sorted(anchor_shaders):
            value = constants.ps_mark_value(h)
            chunk.append(f'if ps == {value} || vs == {value}')
            chunk.append(f'    {constants.VAR_FORM} = {form_id}')
            if watchdog_form is not None:
                chunk.append(f'    {constants.VAR_ANCHOR_SEEN} = 1')
            chunk.append('endif')

        ordered = sorted(
            component_branches[comp_id],
            key=lambda b: (-len(b.signature), b.form_gate or 0,
                           b.signature, tuple(sorted(b.assign.items()))))
        first = True
        by_signature: Dict[Tuple[Tuple[int, float], ...], List[_Branch]] = {}
        signature_order: List[Tuple[Tuple[int, float], ...]] = []
        for branch in ordered:
            if branch.signature not in by_signature:
                signature_order.append(branch.signature)
            by_signature.setdefault(branch.signature, []).append(branch)

        for signature in signature_order:
            branches = sorted(
                by_signature[signature],
                key=lambda b: (b.form_gate or 0, tuple(sorted(b.assign.items()))))
            terms = _terms(comp_id, branches[0])
            if not terms:
                continue

            for branch in branches:
                chunk.append(f'{"if" if first else "else if"} '
                             f'{_condition(terms, branch.form_gate)}')
                _append_assignments(chunk, branch.assign, '    ')
                first = False
        if not first:
            chunk.append('endif')
            body_chunks.append('\n'.join(chunk))
        else:
            component_list_names.pop(comp_id, None)

    if not body_chunks:
        raise SlotStyleDegrade('no component produced a complete slot condition')

    covered_resource_indices: Set[int] = set()
    blind_zone: List[Tuple[str, str]] = []
    dirty_hashes = {_canon(h) or h for h in dirty_hashes_raw}
    dirty_only_hashes = dirty_hashes - all_assigned_hashes
    phantom_only_resource_indices: Set[int] = set()
    phantom_suppressed: List[Tuple[str, str]] = []
    for index, (h, _res) in enumerate(textures):
        canon = _canon(h) or h
        if canon in all_assigned_hashes and canon not in live_fallback:
            covered_resource_indices.add(index)
        elif canon in dirty_only_hashes:
            section = f'TextureOverrideTexture{index}'
            phantom_only_resource_indices.add(index)
            phantom_suppressed.append((h, section))
        elif canon not in live_fallback:
            section = f'TextureOverrideTexture{index}'
            if has_freshness_evidence:
                phantom_only_resource_indices.add(index)
                phantom_suppressed.append((h, section))
            else:
                blind_zone.append((h, section))

    out: List[str] = []
    form_sources = ', '.join(label for label, _ in forms)
    out.append('')
    out.append('; ============================================================')
    out.append('; Slot-style texture layer (XQFA-style concise path)')
    out.append(f'; Forms: {form_sources}')
    out.append('; Conditions read DXGI format-family tags from active ps-t slots;')
    out.append('; bodies directly bind only the fresh slots recorded in STU.')
    out.append('; ============================================================')

    if anchor_resources or anchor_shaders:
        out.append('')
        out.append('; -- User-specified form anchors')
        for h, form_id in sorted(anchor_resources):
            out.append('')
            out.append(f'[{constants.SEC_RESOURCE_ANCHOR.format(anchor_hash=h)}]')
            out.append(f'hash = {h}')
            out.append('allow_duplicate_hash = true')
            out.append('match_priority = 0')
            out.append('match_first_index = 0')
            out.append(f'{constants.VAR_FORM} = {form_id}')
            if watchdog_form is not None:
                out.append(f'{constants.VAR_ANCHOR_SEEN} = 1')
        for h, form_id in sorted(anchor_shaders):
            out.append('')
            out.append(f'[{constants.SEC_SHADER_ANCHOR.format(anchor_hash=h)}]')
            out.append(f'hash = {h}')
            out.append('allow_duplicate_hash = true')
            out.append(f'filter_index = {constants.ps_mark_value(h)}')

    out.extend(body_chunks)

    format_section_count = 0
    out.append('')
    out.append('; -- Format-family tags')
    for comp_id in sorted(used_families):
        crange = (component_ranges or {}).get(comp_id)
        if crange is None:
            raise SlotStyleDegrade(
                f'component {comp_id} index range unknown - cannot emit its '
                f'format tag sections')
        ranges = [(constants.SEC_FORMAT_TAG, None, crange)]
        for level, lranges in sorted((lod_ranges or {}).items()):
            if comp_id in lranges:
                ranges.append((constants.SEC_FORMAT_TAG_LOD, level, lranges[comp_id]))
        for key in sorted(used_families[comp_id]):
            for prefix in sorted(group_families.get(key, {})):
                name, text = group_families[key][prefix]
                for member in constants.emitted_format_members(name):
                    for template, level, (first, count) in ranges:
                        out.append('')
                        out.append('[' + template.format(component_id=comp_id,
                                                         format_name=member,
                                                         level=level) + ']')
                        out.append(f'match_first_index = {first}')
                        out.append(f'match_index_count = {count}')
                        out.append(f'match_priority = {constants.FORMAT_TAG_PRIORITY}')
                        out.append(f'match_format = {member}')
                        out.append(f'filter_index = {text}')
                        format_section_count += 1
    out.append('')

    extra_globals: List[str] = []
    watchdog_lines: List[str] = []
    if watchdog_form is not None:
        extra_globals.append(constants.VAR_ANCHOR_SEEN)
        watchdog_lines = [
            '; Form-anchor watchdog: a frame without an anchor heartbeat commits',
            '; the one unanchored/default form by elimination.',
            f'if {constants.VAR_ANCHOR_SEEN}',
            f'    post {constants.VAR_ANCHOR_SEEN} = 0',
            'else',
            f'    {constants.VAR_FORM} = {watchdog_form}',
            'endif',
        ]

    return SlotPlan(
        block_text='\n'.join(out),
        component_list_names=component_list_names,
        covered_resource_indices=covered_resource_indices,
        blind_zone=blind_zone,
        multi_form=multi_form,
        used_slots=sorted(used_slots),
        phantom_only_resource_indices=phantom_only_resource_indices,
        phantom_suppressed=phantom_suppressed,
        extra_globals=extra_globals,
        watchdog_lines=watchdog_lines,
        default_form_id=watchdog_form if watchdog_form is not None else 1,
        live_fallback=dict(live_fallback),
        warnings=warnings,
        stats={
            'forms': len(forms),
            'components': len(component_list_names),
            'branches': sum(len(b) for b in component_branches.values()),
            'conflicts': conflict_count,
            'marks': 0,
            'fork_latches': 0,
            'anchors': len(anchor_resources) + len(anchor_shaders),
            'anchor_watchdog': 1 if watchdog_form is not None else 0,
            'probes': 0,
            'live_fallback': len(live_fallback),
            'format_sections': format_section_count,
            'format_sections_raw': format_section_count,
            'format_sections_unique': format_section_count,
            'format_sections_removed': 0,
            'covered_textures': len(covered_resource_indices),
            'blind_zone_textures': len(blind_zone),
            'phantom_suppressed_textures': len(phantom_only_resource_indices),
            'phantom_pairs': phantom_pairs,
            'dirty_slots': dirty_slots,
            'service_slots': len([s for s in used_slots if s in constants.SERVICE_SLOTS]),
            'suppressed_latches': 0,
        },
    )
