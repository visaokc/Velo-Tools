# Slot-style texture layer generator (pure python, no bpy / no _wwmi_core
# imports). The emitted INI mirrors the concise XQFA slot model: format-tagged
# ps-t conditions inside the component draw scope, followed by direct texture
# assignments. Unverified dirty/stale slots are filtered out before branch
# construction when ShaderTextureUsage.json carries v4 freshness evidence.

import json
import re
from dataclasses import dataclass, field, replace
from itertools import combinations
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import constants
from . import stu_metadata


class SlotStyleDegrade(Exception):
    """Raised when slot-style generation cannot proceed safely."""


class LocalDiscriminatorConflict(SlotStyleDegrade):
    """Carries components whose root-catalog subset cannot stay slot-style."""

    def __init__(self, problems: List[dict]):
        self.problems = list(problems)
        self.components = frozenset(
            int(problem["component"]) for problem in self.problems)
        super().__init__(_format_local_conflict_message(self.problems))


# comp_id -> ps_hash -> slot -> texture hash (None = conflicting multi-state
# binding seen for that slot; generator must not assign it).
FormData = Dict[int, Dict[str, Dict[int, Optional[str]]]]
# texture hash -> {'format': canonical DXGI name or '', 'width', 'height'}
TextureInfo = Dict[str, dict]
# (component, ps_hash) -> True when every observed pass for that ps was
# depth-only. A single non-depth observation makes the pass usable as color.
PassDepth = Dict[Tuple[int, str], bool]
RouteContext = Dict[str, Set[int]]
FormRoutes = Dict[Tuple[int, int], str]
BindingFreshness = Dict[Tuple[int, str, int], object]
VERIFIED_INHERITED = 'verified_inherited'
_BINDING_RANK = {
    False: 0,
    VERIFIED_INHERITED: 1,
    True: 2,
}

_PS_RE = re.compile(r'ps=([0-9a-f]{16})')
_VS_KEY_RE = re.compile(r'^vs=[0-9a-f?]+$')
_COMP_RE = re.compile(r'component\s*(\d+)\s*$', re.I)
_SLOT_RE = re.compile(r'^ps-t(\d+)$')
_ROUTE_RE = re.compile(r'^[0-9a-f]{8}$')

_RESERVED_KEYS = {
    constants.EXTRA_FORMS_KEY,
    constants.LOCAL_FORM_DISCRIMINATOR_KEY,
    constants.LOCAL_COMPONENT_SOURCES_KEY,
    'form_component_modes',
    constants.FORM_ANCHORS_KEY,
    constants.FORM_ANCHOR_VB0_KEY,
    constants.FORM_ANCHOR_LABEL_KEY,
    constants.FORM_ANCHOR_SOURCE_KEY,
    constants.FORM_ANCHOR_RANK_KEY,
    'version',
}

_COMPONENT_METADATA_KEYS = {
    constants.FORM_COMPONENT_MODE_KEY,
    constants.COMPONENT_SOURCES_KEY,
    constants.FORM_VARIANTS_KEY,
    *constants.LEGACY_FORM_COMPONENT_MODE_KEYS,
}

_FORM_VARIANT_METADATA_KEYS = {
    'label',
    'source',
    'matched_by',
    'vb0_hash',
    constants.FORM_ANCHOR_VB0_KEY,
    constants.FORM_ANCHOR_LABEL_KEY,
    constants.FORM_ANCHOR_SOURCE_KEY,
    constants.FORM_ANCHOR_RANK_KEY,
    constants.COMPONENT_SOURCES_KEY,
}


@dataclass(frozen=True)
class ComponentHashFallback:
    hash: str
    component_id: int
    resource: str
    section: str
    reason: str


# ---------------------------------------------------------------- loading --

def _ingest_slot(pair_out: Dict[int, Optional[str]], slot: int,
                 tex_hash: Optional[str]):
    if slot in pair_out and pair_out[slot] != tex_hash:
        pair_out[slot] = None
    else:
        pair_out[slot] = tex_hash


def normalize_usage(raw: dict, source: str, warnings: List[str],
                    texture_info: Optional[TextureInfo] = None,
                    freshness: Optional[BindingFreshness] = None,
                    pass_depth: Optional[PassDepth] = None) -> FormData:
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
            if pair_key in _COMPONENT_METADATA_KEYS:
                continue
            if pair_key in _FORM_VARIANT_METADATA_KEYS:
                continue
            if _VS_KEY_RE.match(pair_key) and isinstance(value, dict):
                for ps_key, slots in value.items():
                    ps_found = _PS_RE.search(ps_key)
                    if not ps_found:
                        warnings.append(f'{source}: pair "{pair_key}/{ps_key}" has no ps hash, skipped')
                        continue
                    ps_hash = ps_found.group(1)
                    if pass_depth is not None:
                        depth_only = bool(slots.get('depth_only'))
                        depth_key = (comp_id, ps_hash)
                        pass_depth[depth_key] = (
                            pass_depth[depth_key] and depth_only
                            if depth_key in pass_depth else depth_only)
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
                        rec_binding = (
                            VERIFIED_INHERITED
                            if (record.get('verified_inherited') is True
                                and rec_fresh is not True)
                            else rec_fresh)
                        if (freshness is not None and rec_binding is not None
                                and slot_id in pair_out
                                and pair_out[slot_id] != tex_hash):
                            seat_key = (comp_id, ps_hash, slot_id)
                            seated = freshness.get(seat_key)
                            rec_rank = _BINDING_RANK.get(rec_binding, -1)
                            seated_rank = _BINDING_RANK.get(seated, -1)
                            if rec_rank > seated_rank:
                                pair_out[slot_id] = tex_hash
                                freshness[seat_key] = rec_binding
                            elif rec_rank < seated_rank:
                                pass
                            else:
                                pair_out[slot_id] = None
                            continue
                        _ingest_slot(pair_out, slot_id, tex_hash)
                        if (freshness is not None and rec_binding is not None
                                and pair_out.get(slot_id) == tex_hash):
                            seat_key = (comp_id, ps_hash, slot_id)
                            seated = freshness.get(seat_key)
                            freshness[seat_key] = max(
                                (seated, rec_binding),
                                key=lambda value: _BINDING_RANK.get(value, -1))
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


def _multi_component_ids_from_usage(raw: dict) -> Set[int]:
    stu_metadata.sync_form_component_modes(raw)
    out: Set[int] = set()
    for comp_id in stu_metadata.component_ids_in_usage(raw):
        block = raw.get(stu_metadata.component_key(comp_id))
        if not isinstance(block, dict):
            continue
        mode = str(block.get(constants.FORM_COMPONENT_MODE_KEY)
                   or '').strip().lower()
        if mode == 'multi':
            out.add(comp_id)
    return out


def _filter_extra_form_components(components: object,
                                  multi_components: Set[int]) -> object:
    if not isinstance(components, dict):
        return components
    out = {}
    for comp_name, block in components.items():
        found = _COMP_RE.search(str(comp_name))
        if found and int(found.group(1)) in multi_components:
            out[comp_name] = block
    return out


def load_forms(object_source_folder: Path,
               freshness_out: Optional[List[BindingFreshness]] = None,
               pass_depth_out: Optional[List[PassDepth]] = None,
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
    forms, texture_info, memory_warnings = load_forms_from_usage(
        base_raw,
        freshness_out=freshness_out,
        pass_depth_out=pass_depth_out,
    )
    warnings.extend(memory_warnings)

    if len(forms) > 1:
        return forms, texture_info, warnings

    multi_components = _multi_component_ids_from_usage(base_raw)

    def _form_freshness() -> Optional[BindingFreshness]:
        return {} if freshness_out is not None else None

    def _form_pass_depth() -> Optional[PassDepth]:
        return {} if pass_depth_out is not None else None

    base_fresh = _form_freshness()
    base_depth = _form_pass_depth()
    forms = forms

    extra_entries = stu_metadata.form_entries(base_raw)
    if not extra_entries:
        legacy_path = Path(object_source_folder) / constants.LEGACY_SIDECAR_FILENAME
        if legacy_path.is_file():
            try:
                with open(legacy_path, encoding='utf-8') as f:
                    extra_entries = stu_metadata.form_entries({
                        constants.EXTRA_FORMS_KEY:
                            (json.load(f) or {}).get(constants.EXTRA_FORMS_KEY)
                    })
                warnings.append(
                    f'legacy {constants.LEGACY_SIDECAR_FILENAME} used - re-run the form '
                    f'merge once to migrate it into {constants.BASE_USAGE_FILENAME}')
            except Exception as e:
                raise SlotStyleDegrade(
                    f'failed to read {constants.LEGACY_SIDECAR_FILENAME}: {e}')
    for entry in extra_entries or []:
        label = entry.get('label') or entry.get('source') or f'form{len(forms) + 1}'
        entry_fresh = _form_freshness()
        entry_depth = _form_pass_depth()
        components = _filter_extra_form_components(
            entry.get('components'), multi_components)
        forms.append((label, normalize_usage(components, label,
                                             warnings, texture_info, entry_fresh,
                                             entry_depth)))
        if freshness_out is not None:
            freshness_out.append(entry_fresh)
        if pass_depth_out is not None:
            pass_depth_out.append(entry_depth)

    if freshness_out is not None and not any(freshness_out):
        warnings.append(
            f'{constants.BASE_USAGE_FILENAME} has no binding-freshness flags - '
            'dirty slot filtering is unavailable for this extraction')
    return forms, texture_info, warnings


def load_forms_from_usage(
        base_raw: dict,
        freshness_out: Optional[List[BindingFreshness]] = None,
        pass_depth_out: Optional[List[PassDepth]] = None,
) -> Tuple[List[Tuple[str, FormData]], TextureInfo, List[str]]:
    """Load base and embedded form maps from an in-memory STU document."""
    if not isinstance(base_raw, dict):
        raise SlotStyleDegrade(
            f'{constants.BASE_USAGE_FILENAME} has an unexpected shape')
    warnings: List[str] = []
    texture_info: TextureInfo = {}
    multi_components = _multi_component_ids_from_usage(base_raw)

    def _freshness():
        value = {} if freshness_out is not None else None
        if freshness_out is not None:
            freshness_out.append(value)
        return value

    def _pass_depth():
        value = {} if pass_depth_out is not None else None
        if pass_depth_out is not None:
            pass_depth_out.append(value)
        return value

    forms: List[Tuple[str, FormData]] = [(
        'base',
        normalize_usage(
            base_raw, 'base', warnings, texture_info,
            _freshness(), _pass_depth()),
    )]
    for entry in stu_metadata.form_entries(base_raw):
        if not isinstance(entry, dict):
            continue
        label = entry.get('label') or entry.get('source') or f'form{len(forms) + 1}'
        components = _filter_extra_form_components(
            entry.get('components'), multi_components)
        forms.append((
            label,
            normalize_usage(
                components, label, warnings, texture_info,
                _freshness(), _pass_depth()),
        ))
    if freshness_out is not None and not any(freshness_out):
        warnings.append(
            f'{constants.BASE_USAGE_FILENAME} has no binding-freshness flags - '
            'dirty slot filtering is unavailable for this extraction')
    return forms, texture_info, warnings


def read_local_discriminator_audit(
        object_source_folder: Path,
        route_context: Optional[RouteContext] = None) -> object:
    """Build the local discriminator audit from current STU facts at export time."""
    base_path = Path(object_source_folder) / constants.BASE_USAGE_FILENAME
    try:
        with open(base_path, encoding='utf-8') as f:
            raw = json.load(f)
    except Exception as e:
        raise SlotStyleDegrade(f'failed to read local discriminator audit: {e}')
    if not isinstance(raw, dict):
        raise SlotStyleDegrade(
            f'{constants.BASE_USAGE_FILENAME} has an unexpected shape')
    return build_local_discriminator_audit_from_usage(
        raw, route_context=route_context)


def _normalized_route_context(
        route_context: Optional[RouteContext]) -> RouteContext:
    normalized: RouteContext = {}
    for raw_route, raw_components in (route_context or {}).items():
        route = str(raw_route or '').strip().lower()
        if not _ROUTE_RE.fullmatch(route):
            continue
        try:
            components = {int(value) for value in raw_components}
        except (TypeError, ValueError):
            continue
        normalized.setdefault(route, set()).update(components)
    return normalized


def _component_form_routes(
        entries: List[dict],
        route_context: Optional[RouteContext],
        multi_components: Set[int],
        invalid_out: Optional[Set[int]] = None,
        profiles_out: Optional[Dict[int, Set[str]]] = None) -> FormRoutes:
    """Return validated component-local scene routes for audit forms.

    A component enters route mode only when every extra form containing that
    component names a route that is present in CrossSceneManifest.json and maps
    back to that merged component. Partial route evidence is rejected so a
    normal local discriminator conflict remains visible instead of being
    silently split into an unverified route.
    """
    context = _normalized_route_context(route_context)
    if not context:
        return {}
    expected_by_component: Dict[int, Set[str]] = {}
    for route, components in context.items():
        for comp_id in components & multi_components:
            expected_by_component.setdefault(comp_id, set()).add(route)
    candidates: Dict[int, List[Tuple[int, Optional[str]]]] = {}
    for form_id, entry in enumerate(entries, start=2):
        components = _filter_extra_form_components(
            entry.get('components'), multi_components)
        if not isinstance(components, dict):
            continue
        entry_route = str(entry.get('vb0_hash') or '').strip().lower()
        for comp_name, block in components.items():
            found = _COMP_RE.search(str(comp_name))
            if not found or not isinstance(block, dict):
                continue
            comp_id = int(found.group(1))
            route = str(block.get('vb0_hash') or entry_route).strip().lower()
            valid = (_ROUTE_RE.fullmatch(route) is not None
                     and comp_id in context.get(route, set()))
            candidates.setdefault(comp_id, []).append(
                (form_id, route if valid else None))

    routes: FormRoutes = {}
    for comp_id in sorted(set(candidates) | set(expected_by_component)):
        form_candidates = candidates.get(comp_id, [])
        expected = expected_by_component.get(comp_id, set())
        observed = {
            route for _form_id, route in form_candidates if route is not None
        }
        if (not expected or not form_candidates
                or any(route is None for _form_id, route in form_candidates)
                or observed != expected):
            if invalid_out is not None:
                invalid_out.add(comp_id)
            continue
        routes[(1, comp_id)] = 'base'
        for form_id, route in form_candidates:
            routes[(form_id, comp_id)] = str(route)
        if profiles_out is not None:
            profiles_out[comp_id] = {'base', *expected}
    return routes


def build_local_discriminator_audit_from_usage(
        usage: dict,
        route_context: Optional[RouteContext] = None) -> dict:
    warnings: List[str] = []
    texture_info: TextureInfo = {}
    freshness: List[BindingFreshness] = []
    pass_depth: List[PassDepth] = []
    source_meta: Dict[Tuple[str, int], List[str]] = {}
    multi_components = _multi_component_ids_from_usage(usage)

    def _freshness() -> BindingFreshness:
        fresh: BindingFreshness = {}
        freshness.append(fresh)
        return fresh

    def _pass_depth() -> PassDepth:
        depth: PassDepth = {}
        pass_depth.append(depth)
        return depth

    def _add_source_meta(label: str, comp_name: str, values):
        found = _COMP_RE.search(str(comp_name))
        if not found:
            return
        if isinstance(values, str):
            items = [values]
        elif isinstance(values, list):
            items = [item for item in values if isinstance(item, str)]
        else:
            return
        if items:
            source_meta.setdefault((label, int(found.group(1))), []).extend(items)

    def _collect_source_meta(label: str, container: dict):
        if not isinstance(container, dict):
            return
        raw = container.get(constants.LOCAL_COMPONENT_SOURCES_KEY)
        if isinstance(raw, dict):
            for comp_name, values in raw.items():
                _add_source_meta(label, comp_name, values)
        for comp_name, block in container.items():
            found = _COMP_RE.search(str(comp_name))
            if not found or not isinstance(block, dict):
                continue
            _add_source_meta(
                label, comp_name, block.get(constants.COMPONENT_SOURCES_KEY))

    forms: List[Tuple[str, FormData]] = [
        ('base', normalize_usage(
            usage, 'base', warnings, texture_info, _freshness(), _pass_depth()))]
    _collect_source_meta('base', usage)
    entries = [entry for entry in stu_metadata.form_entries(usage)
               if isinstance(entry, dict)]
    for entry in entries:
        label = entry.get('label') or entry.get('source') or f'form{len(forms) + 1}'
        components = _filter_extra_form_components(
            entry.get('components'), multi_components)
        forms.append((label, normalize_usage(components, label,
                                             warnings, texture_info,
                                             _freshness(), _pass_depth())))
        _collect_source_meta(label, entry)
    invalid_route_components: Set[int] = set()
    route_profiles: Dict[int, Set[str]] = {}
    form_routes = _component_form_routes(
        entries, route_context, multi_components,
        invalid_out=invalid_route_components, profiles_out=route_profiles)
    return build_local_discriminator_audit(
        forms, texture_info, freshness, warnings, source_meta=source_meta,
        pass_depth=pass_depth, form_routes=form_routes,
        invalid_route_components=invalid_route_components,
        route_profiles=route_profiles)


# -------------------------------------------------------------------- plan --

@dataclass(frozen=True)
class SlotSection:
    name: str
    lines: Tuple[str, ...]
    kind: str
    component_id: Optional[int] = None
    level: Optional[int] = None


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
    component_hash_fallbacks: Dict[str, List[ComponentHashFallback]] = field(default_factory=dict)
    slot_unrepresented: List[Dict[str, object]] = field(default_factory=list)
    unsafe_fallback: List[Dict[str, object]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, object] = field(default_factory=dict)
    format_diagnostics: Dict[str, object] = field(default_factory=dict)
    branch_contract: Dict[str, Dict[str, object]] = field(default_factory=dict)
    restore_contract: Dict[str, Dict[str, object]] = field(default_factory=dict)
    component_route_lists: Dict[int, Dict[str, str]] = field(default_factory=dict)
    sections: Tuple[SlotSection, ...] = ()


def _slot_issue_entry(tex_hash: str, comp_id: int, reason: str,
                      source: str, slot: Optional[int] = None) -> Dict[str, object]:
    entry: Dict[str, object] = {
        'hash': tex_hash,
        'component': comp_id,
        'reason': reason,
        'source': source,
    }
    if slot is not None:
        entry['slot'] = slot
    return entry


def _format_slot_unrepresented(entries: List[Dict[str, object]]) -> str:
    lines = [
        'slot-style texture coverage failed: the following slot-owned '
        'texture(s) cannot be represented by a safe ps-t assignment branch. '
        'Hash fallback is disabled for these textures because it is unstable '
        'under shader/pass changes and same-IB multi-instance rendering.',
    ]
    for entry in entries[:8]:
        slot = entry.get('slot')
        slot_text = f' ps-t{slot}' if slot is not None else ''
        lines.append(
            f"Component {entry.get('component')} texture {entry.get('hash')}"
            f"{slot_text}: {entry.get('reason')} ({entry.get('source')})")
    if len(entries) > 8:
        lines.append(f'... {len(entries) - 8} more unrepresented texture(s).')
    lines.append(
        'Refresh STU/local slot-layout evidence or exclude the component '
        'from the slot layer explicitly; excluded components return to stock '
        'hash-style output.')
    return '\n'.join(lines)


def _add_component_hash_fallback(
        fallbacks: Dict[str, List[ComponentHashFallback]],
        tex_hash: str,
        comp_id: int,
        resource: str,
        section: str,
        reason: str):
    entries = fallbacks.setdefault(tex_hash, [])
    for entry in entries:
        if entry.component_id == comp_id and entry.resource == resource:
            return
    entries.append(ComponentHashFallback(
        hash=tex_hash,
        component_id=comp_id,
        resource=resource,
        section=section,
        reason=reason,
    ))


def _anchor_runtime_state(forms: List[Tuple[str, FormData]],
                          manual_anchors: Optional[List[Tuple[str, int]]],
                          warnings: List[str]) -> Tuple[List[Tuple[str, int]], Optional[int], Set[int]]:
    anchor_resources: List[Tuple[str, int]] = []
    for anchor_hash, form_id in (manual_anchors or []):
        if not isinstance(anchor_hash, str) or form_id < 1 or form_id > len(forms):
            warnings.append(f'form anchor {anchor_hash!r} skipped (bad form id)')
            continue
        h = anchor_hash.strip().lower()
        if re.fullmatch(r'[0-9a-f]{8}', h):
            anchor_resources.append((h, form_id))
        elif re.fullmatch(r'[0-9a-f]{16}', h):
            warnings.append(
                f'form anchor {anchor_hash!r} skipped (shader-hash anchors are '
                'audit-only and cannot be emitted as runtime slot conditions)')
        else:
            warnings.append(
                f'form anchor {anchor_hash!r} skipped (expected an 8-hex '
                f'vb0 hash or a 16-hex ps hash)')
    anchored_forms = {f for _, f in anchor_resources}
    unanchored_forms = set(range(1, len(forms) + 1)) - anchored_forms
    watchdog_form = None
    if len(forms) > 1 and anchored_forms and len(unanchored_forms) == 1:
        watchdog_form = next(iter(unanchored_forms))
    gated_forms = set(anchored_forms)
    if watchdog_form is not None:
        gated_forms.add(watchdog_form)
    return anchor_resources, watchdog_form, gated_forms


@dataclass(eq=False)
class _Branch:
    signature: Tuple[Tuple[int, float], ...]
    assign: Dict[int, str]
    form_gate: Optional[int]
    source: str
    negative_signature: Tuple[Tuple[int, float], ...] = ()
    full_signature: Tuple[Tuple[int, float], ...] = ()
    pass_role: str = 'material'
    condition_slots: Tuple[int, ...] = ()
    assignment_slots: Tuple[int, ...] = ()
    inherited_slots: Tuple[int, ...] = ()
    ps: str = ''
    observed: Dict[int, str] = field(default_factory=dict)


@dataclass(eq=False)
class _LocalBranch:
    signature: Tuple[Tuple[int, float], ...]
    negative_signature: Tuple[Tuple[int, float], ...]
    assign: Dict[int, str]
    form_id: Optional[int]
    label: str
    ps: str
    source: str
    assign_hashes: Dict[int, str] = field(default_factory=dict)
    route_id: Optional[str] = None
    inherited_slots: Tuple[int, ...] = ()


def _serialized_branch_contract(
        comp_id: int,
        branches: List[object],
        route: Optional[str] = None,
        emitted_form_gates: Optional[Set[int]] = None) -> Dict[str, object]:
    assignment_slots: Set[int] = set()
    serialized = []
    for branch in branches:
        branch_assignments = sorted(getattr(branch, 'assign', {}))
        assignment_slots.update(branch_assignments)
        entry = {
            'positive_signature': [
                [slot, _fi_str(key)]
                for slot, key in getattr(branch, 'signature', ())
            ],
            'negative_signature': [
                [slot, _fi_str(key)]
                for slot, key in getattr(branch, 'negative_signature', ())
            ],
            'assignment_slots': branch_assignments,
            'assignment_hashes': {
                str(slot): tex_hash for slot, tex_hash in sorted(
                    getattr(branch, 'assign_hashes', {}).items())
            },
            'assignment_resources': {
                str(slot): resource for slot, resource in sorted(
                    getattr(branch, 'assign', {}).items())
            },
        }
        form_gate = getattr(branch, 'form_id', None)
        if form_gate is None:
            form_gate = getattr(branch, 'form_gate', None)
        if (emitted_form_gates is not None
                and form_gate not in emitted_form_gates):
            form_gate = None
        if form_gate is not None:
            entry['form_gate'] = int(form_gate)
        serialized.append(entry)
    contract = {
        'component': comp_id,
        'direct_setter_slots': sorted(assignment_slots),
        'branches': serialized,
    }
    if route is not None:
        contract['route'] = route
    return contract


def _local_restore_policy(comp_id: int,
                          branches: List[_LocalBranch],
                          audit: object,
                          volatile_assignment_hashes: Optional[Set[str]] = None,
                          volatile_assignment_component_hashes: Optional[
                              Set[Tuple[int, str]]] = None,
                          canon_fn=None) -> Dict[str, object]:
    """Derive one persistent seat from final branches and collision evidence."""
    full_restore: Dict[str, object] = {'mode': 'full'}
    if not branches or not isinstance(audit, dict):
        return full_restore
    if any(branch.inherited_slots for branch in branches):
        return full_restore
    if audit.get('schema') != constants.LOCAL_FORM_DISCRIMINATOR_SCHEMA:
        return full_restore
    branch_routes = {branch.route_id for branch in branches}
    if None in branch_routes and len(branch_routes) != 1:
        return full_restore
    allowed_routes = None if branch_routes == {None} else branch_routes

    def _canon_hash(value: object) -> Optional[str]:
        if not isinstance(value, str) or not value.strip():
            return None
        canon = canon_fn(value) if canon_fn is not None else value
        if not isinstance(canon, str) or not canon.strip():
            return None
        return canon.strip().lower()

    def _slot_set(value: object) -> Optional[Set[int]]:
        if not isinstance(value, list):
            return None
        out: Set[int] = set()
        try:
            for raw_slot in value:
                slot = int(raw_slot)
                if slot < 0 or slot > 8:
                    return None
                out.add(slot)
        except (TypeError, ValueError):
            return None
        return out

    def _hash_map(value: object) -> Optional[Dict[int, str]]:
        if not isinstance(value, dict):
            return None
        out: Dict[int, str] = {}
        try:
            for raw_slot, raw_hash in value.items():
                slot = int(raw_slot)
                if slot < 0 or slot > 8 or slot in out:
                    return None
                tex_hash = _canon_hash(raw_hash)
                if tex_hash is None:
                    return None
                out[slot] = tex_hash
        except (TypeError, ValueError):
            return None
        return out

    def _signature_map(value: object,
                       *, allow_empty: bool = False) -> Optional[Dict[int, float]]:
        if not isinstance(value, list):
            return None
        signature = _signature_from_audit(value)
        if not signature and not allow_empty:
            return None
        out = dict(signature)
        if len(out) != len(signature):
            return None
        return out

    raw_service_slots = _slot_set(audit.get('service_slots'))
    required_service_slots = set(constants.SERVICE_SLOTS)
    if (raw_service_slots is None
            or not required_service_slots.issubset(raw_service_slots)):
        return full_restore
    excluded_slots = set(raw_service_slots)
    for key in ('drift_slots', 'volatile_slots'):
        if key not in audit:
            continue
        extra_slots = _slot_set(audit.get(key))
        if extra_slots is None:
            return full_restore
        excluded_slots.update(extra_slots)

    rows = audit.get('rows')
    if not isinstance(rows, list):
        return full_restore

    parsed_rows: List[Dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict):
            return full_restore
        try:
            row_component = int(row.get('component'))
        except (TypeError, ValueError):
            return full_restore
        if row_component != comp_id:
            continue
        row_route = row.get('route')
        if row_route is not None:
            row_route = str(row_route).strip().lower()
            if row_route != 'base' and not _ROUTE_RE.fullmatch(row_route):
                return full_restore
        if allowed_routes is not None and row_route not in allowed_routes:
            continue
        try:
            form_id = int(row.get('form_id'))
        except (TypeError, ValueError):
            return full_restore
        if (not isinstance(row.get('primary_pass'), bool)
                or not isinstance(row.get('depth_only'), bool)
                or not isinstance(row.get('ps'), str)
                or not isinstance(row.get('pass_role'), str)
                or not isinstance(row.get('condition_source'), str)):
            return full_restore
        signature = _signature_map(row.get('signature'))
        positive = _signature_map(row.get('positive_signature'))
        negative = _signature_map(
            row.get('negative_signature'), allow_empty=True)
        observed = _hash_map(row.get('observed_hashes'))
        assigned = _hash_map(row.get('assign_hashes'))
        fresh = _slot_set(row.get('fresh_slots'))
        condition_slots = _slot_set(row.get('condition_slots'))
        assignment_slots = _slot_set(row.get('assignment_slots'))
        canonical_slots = _slot_set(row.get('canonical_slots'))
        if (signature is None or positive is None or negative is None
                or observed is None or assigned is None or fresh is None
                or condition_slots is None or assignment_slots is None
                or canonical_slots is None):
            return full_restore
        if (signature != positive
                or condition_slots != set(signature)
                or assignment_slots != set(assigned)):
            return full_restore
        parsed_rows.append({
            'form_id': form_id,
            'primary': row['primary_pass'],
            'depth_only': row['depth_only'],
            'signature': signature,
            'negative': negative,
            'observed': observed,
            'assigned': assigned,
            'fresh': fresh,
        })
    if not parsed_rows:
        return full_restore

    hash_families: Dict[str, float] = {}
    service_seats_by_hash: Dict[str, Set[int]] = {}
    for row in parsed_rows:
        signature = row['signature']
        observed = row['observed']
        for slot, tex_hash in observed.items():
            family = signature.get(slot)
            if family is None:
                continue
            previous = hash_families.setdefault(tex_hash, family)
            if previous != family:
                return full_restore
            if slot in raw_service_slots:
                service_seats_by_hash.setdefault(tex_hash, set()).add(slot)
    excluded_hashes = {
        tex_hash for tex_hash, seats in service_seats_by_hash.items()
        if len(seats) > 1
    }
    for key in ('drift_hashes', 'volatile_hashes'):
        if key not in audit:
            continue
        values = audit.get(key)
        if not isinstance(values, list):
            return full_restore
        for value in values:
            tex_hash = _canon_hash(value)
            if tex_hash is None:
                return full_restore
            excluded_hashes.add(tex_hash)
    for value in volatile_assignment_hashes or ():
        tex_hash = _canon_hash(value)
        if tex_hash is None:
            return full_restore
        excluded_hashes.add(tex_hash)
    for component_id, value in volatile_assignment_component_hashes or ():
        if int(component_id) != comp_id:
            continue
        tex_hash = _canon_hash(value)
        if tex_hash is None:
            return full_restore
        excluded_hashes.add(tex_hash)

    branch_facts: List[Tuple[_LocalBranch, Dict[int, float], Dict[int, str]]] = []
    assignment_seats_by_hash: Dict[str, Set[int]] = {}
    for branch in branches:
        if branch.form_id is None:
            return full_restore
        try:
            form_id = int(branch.form_id)
        except (TypeError, ValueError):
            return full_restore
        positive = dict(branch.signature)
        negative = dict(branch.negative_signature)
        if (not positive or len(positive) != len(branch.signature)
                or len(negative) != len(branch.negative_signature)
                or set(positive) & set(negative)):
            return full_restore
        assigned_slots = set(branch.assign)
        if (not assigned_slots or assigned_slots != set(branch.assign_hashes)
                or not assigned_slots.issubset(positive)):
            return full_restore
        assign_hashes: Dict[int, str] = {}
        for slot, value in branch.assign_hashes.items():
            tex_hash = _canon_hash(value)
            if tex_hash is None:
                return full_restore
            assign_hashes[slot] = tex_hash
            assignment_seats_by_hash.setdefault(tex_hash, set()).add(slot)
        branch_facts.append((branch, positive, assign_hashes))

    if any(len(seats) > 1 and seats & raw_service_slots
           for seats in assignment_seats_by_hash.values()):
        return full_restore

    candidates = set(branch_facts[0][2])
    for _branch, positive, assign_hashes in branch_facts[1:]:
        candidates &= set(assign_hashes)
    candidates -= excluded_slots
    candidates = {
        slot for slot in candidates
        if all(slot in positive for _branch, positive, _assign in branch_facts)
        and len({positive[slot]
                 for _branch, positive, _assign in branch_facts}) == 1
        and all(assign_hashes[slot] not in excluded_hashes
                for _branch, _positive, assign_hashes in branch_facts)
    }
    if not candidates:
        return full_restore

    def _matches(branch: _LocalBranch,
                 positive: Dict[int, float],
                 state: Dict[int, float]) -> bool:
        if any(state.get(slot) != family
               for slot, family in positive.items()):
            return False
        for slot, family in branch.negative_signature:
            if slot not in state or state[slot] == family:
                return False
        return True

    for branch, positive, assign_hashes in branch_facts:
        form_rows = [
            row for row in parsed_rows
            if row['form_id'] == int(branch.form_id)
        ]
        primary_rows = [row for row in form_rows if row['primary']]
        if len(primary_rows) != 1:
            return full_restore
        primary = primary_rows[0]
        if primary['depth_only']:
            return full_restore
        primary_observed = primary['observed']
        primary_fresh = primary['fresh']
        if (not set(assign_hashes).issubset(primary_observed)
                or not set(assign_hashes).issubset(primary_fresh)
                or any(primary_observed[slot] != tex_hash
                       for slot, tex_hash in assign_hashes.items())
                or not _matches(branch, positive, primary['signature'])):
            return full_restore

        matched_collisions: List[Dict[str, object]] = []
        for collision in form_rows:
            if collision['primary'] or collision['depth_only']:
                continue
            if collision['assigned']:
                return full_restore
            fresh_slots = collision['fresh']
            if not fresh_slots:
                continue
            collision_observed = collision['observed']
            collision_signature = collision['signature']
            if (not fresh_slots.issubset(collision_observed)
                    or not fresh_slots.issubset(collision_signature)):
                return full_restore
            state_hashes = dict(primary_observed)
            state_families = dict(primary['signature'])
            for slot in fresh_slots:
                state_hashes[slot] = collision_observed[slot]
                state_families[slot] = collision_signature[slot]
            if not _matches(branch, positive, state_families):
                continue
            displaced = any(
                primary_slot != collision_slot
                and primary_hash == collision_observed[collision_slot]
                for collision_slot in fresh_slots
                for primary_slot, primary_hash in primary_observed.items()
            )
            if not displaced:
                return full_restore
            matched_collisions.append({
                'fresh': fresh_slots,
                'hashes': state_hashes,
                'families': state_families,
            })
        if not matched_collisions:
            return full_restore
        for collision in matched_collisions:
            fresh_slots = collision['fresh']
            state_hashes = collision['hashes']
            state_families = collision['families']
            candidates = {
                slot for slot in candidates
                if (slot not in fresh_slots
                    and slot in state_hashes
                    and slot in state_families
                    and slot in primary_observed
                    and slot in primary['signature']
                    and state_hashes[slot] == primary_observed[slot]
                    and state_families[slot] == primary['signature'][slot])
            }
        if not candidates:
            return full_restore

    if len(candidates) != 1:
        return full_restore
    return {'mode': 'except', 'persistent_slot': next(iter(candidates))}


def _branch_with_assign(branch: _Branch,
                        assign: Dict[int, str],
                        form_gate: Optional[int]) -> _Branch:
    return _Branch(
        signature=branch.signature,
        assign=assign,
        form_gate=form_gate,
        source=branch.source,
        negative_signature=branch.negative_signature,
        full_signature=branch.full_signature,
        pass_role=branch.pass_role,
        condition_slots=branch.condition_slots,
        assignment_slots=tuple(sorted(assign)),
        inherited_slots=tuple(
            slot for slot in branch.inherited_slots if slot in assign),
        ps=branch.ps,
        observed=dict(branch.observed),
    )


def _is_weak_anchor_branch(branch: _Branch,
                           needs_form_gate: bool = True) -> bool:
    condition_slots = branch.condition_slots or tuple(
        slot for slot, _key in branch.signature)
    assignment_slots = branch.assignment_slots or tuple(sorted(branch.assign))
    if (len(condition_slots) == 1
            and not branch.negative_signature
            and len(assignment_slots) == 1):
        return True
    return (branch.pass_role == 'auxiliary'
            and len(condition_slots) == 1
            and not branch.negative_signature
            and len(assignment_slots) == 1)


def _local_branch_is_weak(signature: Tuple[Tuple[int, float], ...],
                          negative_signature: Tuple[Tuple[int, float], ...],
                          assign: Dict[int, str],
                          pass_role: str) -> bool:
    return len(signature) == 1 and not negative_signature and len(assign) == 1


def _branch_covered_by_stronger_layout(branch: _Branch,
                                       candidates: List[_Branch]) -> bool:
    condition = _branch_key(branch)
    if not branch.signature:
        return False
    for candidate in candidates:
        if candidate is branch:
            continue
        other = _branch_key(candidate)
        if other == condition:
            return True
    return False


def _branch_observed_rows_covered_by_candidate(
        branch: _Branch,
        candidate: _Branch,
        comp_id: int,
        forms: List[Tuple[str, FormData]],
        texture_info: TextureInfo,
        alias: Dict[str, str]) -> bool:
    if branch.form_gate is not None:
        if branch.form_gate < 1 or branch.form_gate > len(forms):
            return False
        scoped_forms = [forms[branch.form_gate - 1]]
    else:
        scoped_forms = forms

    matched = False
    for _label, form_data in scoped_forms:
        for pair_map in form_data.get(comp_id, {}).values():
            if not _row_matches_condition(
                    pair_map, texture_info, branch.signature,
                    branch.negative_signature, alias):
                continue
            matched = True
            if not _row_matches_condition(
                    pair_map, texture_info, candidate.signature,
                    candidate.negative_signature, alias):
                return False
    return matched


def _branch_resources_covered_by_stronger_layout(
        branch: _Branch,
        comp_id: int,
        candidates: List[_Branch],
        forms: List[Tuple[str, FormData]],
        texture_info: TextureInfo,
        alias: Dict[str, str]) -> bool:
    resources = set(branch.assign.values())
    if not resources:
        return False
    for candidate in candidates:
        if candidate is branch or candidate.form_gate != branch.form_gate:
            continue
        if _is_weak_anchor_branch(candidate, needs_form_gate=False):
            continue
        if not set(candidate.assign.values()).issuperset(resources):
            continue
        if not _branch_observed_rows_covered_by_candidate(
                branch, candidate, comp_id, forms, texture_info, alias):
            continue
        return True
    return False


def _branch_key(branch: _Branch) -> Tuple[
        Tuple[Tuple[int, float], ...],
        Tuple[Tuple[int, float], ...],
        Optional[int],
        Tuple[Tuple[int, str], ...]]:
    return (
        branch.signature,
        branch.negative_signature,
        branch.form_gate,
        tuple(sorted(branch.assign.items())),
    )


def _row_matches_condition(pair_map: Dict[int, Optional[str]],
                           texture_info: TextureInfo,
                           positive: Tuple[Tuple[int, float], ...],
                           negative: Tuple[Tuple[int, float], ...],
                           alias: Dict[str, str]) -> bool:
    for slot, expected in positive:
        tex_hash = pair_map.get(slot)
        if tex_hash is not None:
            tex_hash = alias.get(tex_hash, tex_hash)
        if _family_key(tex_hash, texture_info) != expected:
            return False
    for slot, forbidden in negative:
        tex_hash = pair_map.get(slot)
        if tex_hash is not None:
            tex_hash = alias.get(tex_hash, tex_hash)
        if _family_key(tex_hash, texture_info) == forbidden:
            return False
    return True


def _branch_observed_matches(branch: _Branch, observed: Dict[int, str]) -> bool:
    if not branch.observed:
        return False
    for slot, tex_hash in branch.observed.items():
        if observed.get(slot) != tex_hash:
            return False
    return True


def _branch_assignments_match_observed_scope(
        branch: _Branch,
        comp_id: int,
        forms: List[Tuple[str, FormData]],
        texture_info: TextureInfo,
        alias: Dict[str, str],
        resource_to_hash: Dict[str, str]) -> bool:
    expected: Dict[int, str] = {}
    for slot, resource in branch.assign.items():
        tex_hash = resource_to_hash.get(resource)
        if not tex_hash:
            return False
        expected[slot] = alias.get(tex_hash, tex_hash)
    if not expected:
        return False

    if branch.form_gate is not None:
        if branch.form_gate < 1 or branch.form_gate > len(forms):
            return False
        scoped_forms = [forms[branch.form_gate - 1]]
    else:
        scoped_forms = forms

    matched = False
    for _label, form_data in scoped_forms:
        for pair_map in form_data.get(comp_id, {}).values():
            if not _row_matches_condition(
                    pair_map, texture_info, branch.signature,
                    branch.negative_signature, alias):
                continue
            matched = True
            for slot, tex_hash in expected.items():
                observed = pair_map.get(slot)
                observed = alias.get(observed, observed) if observed else None
                if observed != tex_hash:
                    return False
    return matched


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
                                    alias: Dict[str, str],
                                    form_routes: Optional[FormRoutes] = None,
                                    ) -> Dict[Tuple[int, Optional[str], str], int]:
    out: Dict[Tuple[int, Optional[str], str], int] = {}
    score_by_key: Dict[
        Tuple[int, Optional[str], str], Tuple[int, int, int]
    ] = {}
    for form_id, (_label, form_data) in enumerate(forms, start=1):
        for comp_id, comp_pairs in form_data.items():
            route = (form_routes or {}).get((form_id, comp_id))
            for pair_map in comp_pairs.values():
                role = _pass_role(pair_map, {})
                role_score = 2 if role == 'material' else (1 if role == 'outline' else 0)
                layout_score = len(_eligible_slots(pair_map))
                for slot, tex_hash in pair_map.items():
                    if not isinstance(tex_hash, str):
                        continue
                    canon = alias.get(tex_hash, tex_hash)
                    key = (comp_id, route, canon)
                    score = (role_score, layout_score, -slot)
                    previous = score_by_key.get(key)
                    if previous is None or score > previous:
                        out[key] = slot
                        score_by_key[key] = score
    return out


def _duplicate_service_assignment_variants(
        assign_hashes: Dict[int, str],
        alias: Dict[str, str]) -> List[Dict[int, str]]:
    by_hash: Dict[str, List[int]] = {}
    for slot, tex_hash in assign_hashes.items():
        if slot not in constants.SERVICE_SLOTS:
            continue
        by_hash.setdefault(alias.get(tex_hash, tex_hash), []).append(slot)
    duplicate_slots = [
        set(slots) for slots in by_hash.values()
        if len(slots) > 1
    ]
    if not duplicate_slots:
        return [dict(assign_hashes)]

    variants: List[Dict[int, str]] = [dict(assign_hashes)]
    for slots in duplicate_slots:
        next_variants: List[Dict[int, str]] = []
        for variant in variants:
            for slot in sorted(slots):
                item = {
                    current_slot: tex_hash
                    for current_slot, tex_hash in variant.items()
                    if current_slot not in slots
                }
                item[slot] = assign_hashes[slot]
                next_variants.append(item)
        variants = next_variants

    deduped: List[Dict[int, str]] = []
    seen: Set[Tuple[Tuple[int, str], ...]] = set()
    for variant in variants:
        key = tuple(sorted(variant.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(variant)
    return deduped or [dict(assign_hashes)]


def _signature_for_duplicate_service_variant(
        signature: Tuple[Tuple[int, float], ...],
        assign_hashes: Dict[int, str],
        variant_assign_hashes: Dict[int, str],
        alias: Dict[str, str]) -> Tuple[Tuple[int, float], ...]:
    by_hash: Dict[str, List[int]] = {}
    for slot, tex_hash in assign_hashes.items():
        if slot not in constants.SERVICE_SLOTS:
            continue
        by_hash.setdefault(alias.get(tex_hash, tex_hash), []).append(slot)
    keep_slots = set(variant_assign_hashes)
    drop_slots: Set[int] = set()
    for slots in by_hash.values():
        if len(slots) <= 1:
            continue
        chosen = [slot for slot in slots if slot in keep_slots]
        if len(chosen) == 1:
            drop_slots.update(slot for slot in slots if slot != chosen[0])
    if not drop_slots:
        return signature
    return tuple(term for term in signature if term[0] not in drop_slots)


def _primary_passes_by_form(forms: List[Tuple[str, FormData]],
                           texture_info: TextureInfo,
                           freshness: Optional[List[BindingFreshness]],
                           pass_depth: Optional[List[PassDepth]],
                           alias: Dict[str, str],
                           form_routes: Optional[FormRoutes] = None,
                           ambiguous_out: Optional[List[str]] = None,
                           ) -> Dict[Tuple[int, int], str]:
    chosen: Dict[Tuple[int, int], str] = {}
    scores: Dict[Tuple[int, int], Tuple[int, int, int, Tuple[Tuple[int, float], ...], str]] = {}
    route_candidates: Dict[Tuple[int, int], List[dict]] = {}
    for form_id, (_label, form_data) in enumerate(forms, start=1):
        form_fresh = (freshness[form_id - 1]
                      if freshness is not None and form_id - 1 < len(freshness)
                      else None)
        form_depth = (pass_depth[form_id - 1]
                      if pass_depth is not None and form_id - 1 < len(pass_depth)
                      else None) or {}
        for comp_id, comp_pairs in form_data.items():
            for ps, pair_map in comp_pairs.items():
                fresh_slots = _fresh_signature_slots(
                    comp_id, ps, pair_map, form_fresh)
                inherited_slots = _inherited_assignment_slots(
                    comp_id, ps, pair_map, form_fresh)
                role = _pass_role(pair_map, texture_info)
                assignment_slots = _local_assignment_slots(
                    pair_map, texture_info, role, fresh_slots,
                    inherited_slots)
                assign_count = sum(
                    1 for slot in assignment_slots
                    if isinstance(pair_map.get(slot), str))
                if assign_count <= 0:
                    continue
                signature = _signature_key(
                    pair_map, texture_info, _local_condition_slots(
                        pair_map, role, fresh_slots,
                        has_freshness=form_fresh is not None), alias)
                if not signature:
                    continue
                non_depth = 0 if form_depth.get((comp_id, ps), False) else 1
                role_score = 2 if role == 'material' else (1 if role == 'outline' else 0)
                score = (non_depth, assign_count, role_score, signature, ps)
                key = (form_id, comp_id)
                if key not in scores or score > scores[key]:
                    scores[key] = score
                    chosen[key] = ps
                if form_routes and key in form_routes:
                    assignment_key = tuple(sorted(
                        (slot, alias.get(tex_hash, tex_hash))
                        for slot in assignment_slots
                        for tex_hash in [pair_map.get(slot)]
                        if isinstance(tex_hash, str)
                    ))
                    route_candidates.setdefault(key, []).append({
                        'ps': ps,
                        'signature': signature,
                        'semantic_score': (
                            non_depth, assign_count, role_score, len(signature)),
                        'assignment_key': assignment_key,
                    })
    if not form_routes:
        return chosen

    route_components = sorted({
        comp_id for form_id, comp_id in form_routes
        if form_id == 1 and form_routes[(form_id, comp_id)] == 'base'
    })
    for comp_id in route_components:
        base_key = (1, comp_id)
        base_candidates = route_candidates.get(base_key, [])
        if not base_candidates:
            continue
        best_base_score = max(
            candidate['semantic_score'] for candidate in base_candidates)
        top_base = [
            candidate for candidate in base_candidates
            if candidate['semantic_score'] == best_base_score
        ]
        if len({candidate['assignment_key'] for candidate in top_base}) > 1:
            if ambiguous_out is not None:
                ambiguous_out.append(
                    f'component {comp_id} base route has an ambiguous primary pass')
            continue
        base_choice = max(
            top_base, key=lambda candidate: (
                candidate['signature'], candidate['ps']))
        chosen[base_key] = base_choice['ps']

        for form_id, route_comp_id in sorted(form_routes):
            if route_comp_id != comp_id or form_id == 1:
                continue
            key = (form_id, comp_id)
            candidates = route_candidates.get(key, [])
            if not candidates:
                continue
            best_score = max(
                candidate['semantic_score'] for candidate in candidates)
            top = [candidate for candidate in candidates
                   if candidate['semantic_score'] == best_score]
            shared = [candidate for candidate in top
                      if candidate['ps'] == base_choice['ps']]
            if shared:
                choice = max(
                    shared, key=lambda candidate: (
                        candidate['signature'], candidate['ps']))
            elif len({candidate['assignment_key'] for candidate in top}) > 1:
                if ambiguous_out is not None:
                    route = form_routes.get(key) or '?'
                    ambiguous_out.append(
                        f'component {comp_id} route {route} has an ambiguous '
                        'primary pass without a base-shared shader')
                continue
            else:
                choice = max(
                    top, key=lambda candidate: (
                        candidate['signature'], candidate['ps']))
            chosen[key] = choice['ps']
    return chosen


def _fresh_signature_slots(comp_id: int,
                           ps: str,
                           pair_map: Dict[int, Optional[str]],
                           form_fresh: Optional[BindingFreshness]) -> Set[int]:
    if form_fresh is None:
        return set()
    return {
        slot for slot in _local_signature_slots(pair_map)
        if form_fresh.get((comp_id, ps, slot)) is True
    }


def _inherited_assignment_slots(
        comp_id: int,
        ps: str,
        pair_map: Dict[int, Optional[str]],
        form_fresh: Optional[BindingFreshness]) -> Set[int]:
    if form_fresh is None:
        return set()
    return {
        slot for slot in _local_signature_slots(pair_map)
        if form_fresh.get((comp_id, ps, slot)) == VERIFIED_INHERITED
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
                           fresh_slots: Set[int],
                           has_freshness: bool = True) -> Set[int]:
    slots = _local_signature_slots(pair_map)
    return slots & fresh_slots if has_freshness else slots


def _local_assignment_slots(pair_map: Dict[int, Optional[str]],
                            texture_info: TextureInfo,
                            role: str,
                            fresh_slots: Set[int],
                            inherited_slots: Optional[Set[int]] = None) -> Set[int]:
    slots = _canonical_override_slots(pair_map, texture_info)
    return slots & (fresh_slots | set(inherited_slots or ()))


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
    return constants.format_filter_index(fmt)


def _fi_str(value: float) -> str:
    text = repr(value)
    return text[:-2] if text.endswith('.0') else text


def _hash_fingerprint(forms: List[Tuple[str, FormData]],
                      texture_info: TextureInfo,
                      freshness: Optional[List[BindingFreshness]] = None,
                      pass_depth: Optional[List[PassDepth]] = None) -> str:
    """Stable fingerprint for the STU facts the local discriminator consumes."""
    import hashlib
    payload = []
    for form_id, (label, form_data) in enumerate(forms, start=1):
        form_fresh = (freshness[form_id - 1]
                      if freshness is not None and form_id - 1 < len(freshness)
                      else None)
        form_depth = (pass_depth[form_id - 1]
                      if pass_depth is not None and form_id - 1 < len(pass_depth)
                      else None) or {}
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
                form_rows.append([comp_id, ps, bool(form_depth.get((comp_id, ps), False)), row])
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


def _ordered_signature(terms: Set[Tuple[int, float]] | Tuple[Tuple[int, float], ...]
                       ) -> Tuple[Tuple[int, float], ...]:
    return tuple(sorted(terms))


def _signature_common(signatures: List[Tuple[Tuple[int, float], ...]]
                      ) -> Tuple[Tuple[int, float], ...]:
    if not signatures:
        return ()
    common = set(signatures[0])
    for signature in signatures[1:]:
        common &= set(signature)
    return _ordered_signature(common)


def _volatile_condition_slot(slot: int) -> bool:
    return slot >= 5


def _condition_term_sort_key(term: Tuple[int, float],
                             assignment_slots: Set[int]) -> Tuple[int, int, float]:
    slot, key = term
    assigned = slot in assignment_slots
    volatile = _volatile_condition_slot(slot)
    if assigned and not volatile:
        group = 0
    elif not volatile:
        group = 1
    elif assigned:
        group = 2
    else:
        group = 3
    return (group, slot, key)


def _safe_condition_shape(positive: Tuple[Tuple[int, float], ...],
                          negative: Tuple[Tuple[int, float], ...]) -> bool:
    return len(positive) > 1 or bool(negative)


def _default_condition_signature(common: Tuple[Tuple[int, float], ...],
                                 assignment_slots: Set[int]
                                 ) -> Tuple[Tuple[int, float], ...]:
    terms = sorted(common, key=lambda term: _condition_term_sort_key(
        term, assignment_slots))
    selected: List[Tuple[int, float]] = [
        term for term in terms
        if term[0] in assignment_slots and not _volatile_condition_slot(term[0])
    ]
    if selected:
        return _ordered_signature(tuple(selected))

    selected = [
        term for term in terms
        if not _volatile_condition_slot(term[0])
    ]
    if selected:
        return _ordered_signature(tuple(selected))

    for term in terms:
        if term[0] in assignment_slots:
            selected.append(term)
    if not selected:
        for term in terms:
            selected.append(term)
    return _ordered_signature(tuple(selected))


def _safe_default_condition_signature(
        common: Tuple[Tuple[int, float], ...],
        assignment_slots: Set[int]) -> Tuple[Tuple[int, float], ...]:
    default = _default_condition_signature(common, assignment_slots)
    if _safe_condition_shape(default, ()) or len(common) <= 1:
        return default
    terms = sorted(common, key=lambda term: _condition_term_sort_key(
        term, assignment_slots))
    return _ordered_signature(tuple(terms[:2]))


def _minimal_condition_signature(
        own_signatures: List[Tuple[Tuple[int, float], ...]],
        other_signatures: List[Tuple[Tuple[int, float], ...]],
        assignment_slots: Set[int],
        blocked_negative_slots: Optional[Set[int]] = None,
        allow_negative: bool = True,
        ) -> Tuple[Tuple[Tuple[int, float], ...], Tuple[Tuple[int, float], ...]]:
    common = _signature_common(own_signatures)
    if not common:
        return (), ()
    required = _ordered_signature(tuple(
        term for term in common if term[0] in assignment_slots))
    if assignment_slots and {
            slot for slot, _key in required} != assignment_slots:
        return (), ()
    optional_terms = sorted(
        (term for term in common if term not in required),
        key=lambda term: _condition_term_sort_key(term, assignment_slots))

    def candidates():
        start = 0 if required else 1
        for size in range(start, len(optional_terms) + 1):
            for extra in combinations(optional_terms, size):
                yield _ordered_signature(required + tuple(extra))

    if not other_signatures:
        for candidate in candidates():
            if _safe_condition_shape(candidate, ()):
                return candidate, ()
        return required or _safe_default_condition_signature(
            common, assignment_slots), ()

    own_set = set(own_signatures)
    other_set = set(other_signatures)
    for subset in candidates():
        blockers = [
            other for other in other_set
            if all(term in other for term in subset)
        ]
        if blockers or not _safe_condition_shape(subset, ()):
            continue
        return subset, ()

    if allow_negative:
        for subset in candidates():
            blockers = [
                other for other in other_set
                if all(term in other for term in subset)
            ]
            if not blockers:
                continue
            negative = _minimal_negative_signature(
                subset, own_set, set(blockers), blocked_negative_slots)
            if negative is None or not _safe_condition_shape(subset, negative):
                continue
            return subset, negative

    fallback = required
    if not _safe_condition_shape(fallback, ()):
        for candidate in candidates():
            if _safe_condition_shape(candidate, ()):
                fallback = candidate
                break
    if not fallback:
        fallback = _default_condition_signature(common, assignment_slots)
    if not allow_negative:
        if not _safe_condition_shape(fallback, ()):
            return _safe_default_condition_signature(common, assignment_slots), ()
        return fallback, ()

    blockers = [
        other for other in other_set
        if all(term in other for term in fallback)
    ]
    if blockers:
        negative = _minimal_negative_signature(
            fallback, own_set, set(blockers), blocked_negative_slots)
        return fallback, negative or ()
    if not _safe_condition_shape(fallback, ()) and len(common) > 1:
        return common, ()
    return fallback, ()


def _minimal_condition_signature_options(
        own_signatures: List[Tuple[Tuple[int, float], ...]],
        other_signatures: List[Tuple[Tuple[int, float], ...]],
        assignment_slots: Set[int],
        blocked_negative_slots: Optional[Set[int]] = None,
        allow_negative: bool = True,
        ) -> List[Tuple[Tuple[Tuple[int, float], ...], Tuple[Tuple[int, float], ...]]]:
    positive, negative = _minimal_condition_signature(
        own_signatures, other_signatures, assignment_slots,
        blocked_negative_slots, allow_negative)
    return [(positive, negative)]

def _minimize_anchor_branches(branches: List[_Branch]) -> None:
    blocked_negative_slots = {
        slot
        for branch in branches
        for slot in branch.assign
    }
    scoped: Dict[Optional[int], List[_Branch]] = {}
    for branch in branches:
        scoped.setdefault(branch.form_gate, []).append(branch)
    for scope_branches in scoped.values():
        groups: Dict[Tuple[Tuple[Tuple[int, str], ...], str], List[_Branch]] = {}
        for branch in scope_branches:
            assign_key = tuple(sorted(branch.assign.items()))
            groups.setdefault((assign_key, branch.pass_role), []).append(branch)
        for (assign_key, _role), members in groups.items():
            own_signatures = [
                member.full_signature or member.signature
                for member in members
            ]
            other_signatures = [
                other.full_signature or other.signature
                for other in scope_branches
                if tuple(sorted(other.assign.items())) != assign_key
            ]
            assignment_slots = {slot for slot, _res in assign_key}
            inherited_slots = set.intersection(*(
                set(member.inherited_slots) for member in members))
            required_slots = assignment_slots - inherited_slots
            positive, negative = _minimal_condition_signature(
                own_signatures, other_signatures, required_slots,
                blocked_negative_slots)
            missing_slots = required_slots - {
                slot for slot, _key in positive}
            if missing_slots:
                raise SlotStyleDegrade(
                    'slot condition does not cover assignment slot(s): '
                    + ', '.join(f'ps-t{slot}' for slot in sorted(missing_slots)))
            for member in members:
                member.signature = positive
                member.negative_signature = negative
                member.condition_slots = tuple(slot for slot, _key in positive)


def _condition_source(comp_id: int,
                      ps: str,
                      slots: Set[int],
                      form_fresh: Optional[BindingFreshness]) -> str:
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
                                other_signatures: Set[Tuple[Tuple[int, float], ...]],
                                blocked_negative_slots: Optional[Set[int]] = None,
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
    stable_candidates = [
        term for term in candidates
        if not _volatile_condition_slot(term[0])
        and term[0] not in (blocked_negative_slots or set())
    ]
    for term in sorted(stable_candidates):
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
            out.append((int(item[0]), float(item[1])))
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
                                    freshness: Optional[List[BindingFreshness]] = None,
                                    warnings: Optional[List[str]] = None,
                                    source_meta: Optional[Dict[Tuple[str, int], List[str]]] = None,
                                    pass_depth: Optional[List[PassDepth]] = None,
                                    form_routes: Optional[FormRoutes] = None,
                                    invalid_route_components: Optional[Set[int]] = None,
                                    route_profiles: Optional[Dict[int, Set[str]]] = None,
                                    ) -> dict:
    """Build the STU audit block for local form discriminator export.

    The audit is intentionally data-only: export still recomputes and verifies
    the fingerprint before trusting it, so stale STU edits fail closed.
    """
    local_warnings: List[str] = list(warnings or [])
    filtered_forms, _dirty_hashes, dirty_slots, phantom_pairs = _filtered_forms(
        forms, freshness, [])
    audit_forms = _local_audit_forms(forms, filtered_forms, texture_info)
    alias = _variant_aliases(texture_info)
    canonical_seats = _component_hash_canonical_slots(
        filtered_forms, alias, form_routes)
    ambiguous_primary: List[str] = []
    primary_passes = _primary_passes_by_form(
        audit_forms, texture_info, freshness, pass_depth, alias,
        form_routes=form_routes, ambiguous_out=ambiguous_primary)
    if ambiguous_primary:
        raise SlotStyleDegrade('; '.join(ambiguous_primary))
    service_drift_branches: Set[Tuple[int, int, str, str]] = set()
    for form_id, (_label, form_data) in enumerate(audit_forms, start=1):
        form_fresh = (freshness[form_id - 1]
                      if freshness is not None and form_id - 1 < len(freshness)
                      else None)
        form_depth = (pass_depth[form_id - 1]
                      if pass_depth is not None and form_id - 1 < len(pass_depth)
                      else None) or {}
        for comp_id, comp_pairs in form_data.items():
            primary_ps = primary_passes.get((form_id, comp_id))
            primary_pair = comp_pairs.get(primary_ps) if primary_ps else None
            if not isinstance(primary_pair, dict):
                continue
            primary_role = _pass_role(primary_pair, texture_info)
            primary_depth = bool(form_depth.get((comp_id, primary_ps), False))
            writers_by_hash_slot: Dict[str, Dict[int, Set[str]]] = {}
            for ps, pair_map in comp_pairs.items():
                if (_pass_role(pair_map, texture_info) != primary_role
                        or bool(form_depth.get((comp_id, ps), False)) != primary_depth):
                    continue
                fresh_slots = _fresh_signature_slots(
                    comp_id, ps, pair_map, form_fresh)
                for slot, tex_hash in pair_map.items():
                    if (slot not in constants.SERVICE_SLOTS
                            or slot not in fresh_slots
                            or not isinstance(tex_hash, str)):
                        continue
                    canon = alias.get(tex_hash, tex_hash)
                    writers_by_hash_slot.setdefault(canon, {}).setdefault(
                        slot, set()).add(ps)
            for tex_hash, writers_by_slot in writers_by_hash_slot.items():
                if len(writers_by_slot) <= 1:
                    continue
                for writers in writers_by_slot.values():
                    for ps in writers:
                        service_drift_branches.add(
                            (form_id, comp_id, ps, tex_hash))
    rows = []
    branch_samples: Dict[
        Tuple[int, Optional[str], Tuple[Tuple[int, str], ...],
              Tuple[Tuple[int, float], ...]],
        dict,
    ] = {}
    component_assignments: Dict[
        Tuple[int, Optional[str]],
        Dict[Tuple[Tuple[int, str], ...], Set[Tuple[Tuple[int, float], ...]]],
    ] = {}
    conflict_details: Dict[
        Tuple[int, Optional[str]],
        Dict[Tuple[Tuple[int, str], ...], dict],
    ] = {}

    for form_id, (label, form_data) in enumerate(audit_forms, start=1):
        form_fresh = (freshness[form_id - 1]
                      if freshness is not None and form_id - 1 < len(freshness)
                      else None)
        for comp_id, comp_pairs in form_data.items():
            route = (form_routes or {}).get((form_id, comp_id))
            scope = (comp_id, route)
            for ps, pair_map in comp_pairs.items():
                is_primary_pass = primary_passes.get((form_id, comp_id)) == ps
                primary_ps = primary_passes.get((form_id, comp_id))
                primary_pair = comp_pairs.get(primary_ps, {})
                fresh_slots = _fresh_signature_slots(
                    comp_id, ps, pair_map, form_fresh)
                inherited_slots = _inherited_assignment_slots(
                    comp_id, ps, pair_map, form_fresh)
                role = _pass_role(pair_map, texture_info)
                condition_slots = _local_condition_slots(
                    pair_map, role, fresh_slots,
                    has_freshness=form_fresh is not None)
                override_slots = _local_assignment_slots(
                    pair_map, texture_info, role, fresh_slots,
                    inherited_slots)
                sig = _signature_key(pair_map, texture_info,
                                     condition_slots, alias)
                if not sig:
                    continue
                depth_only = False
                if pass_depth is not None and form_id - 1 < len(pass_depth):
                    depth_only = bool((pass_depth[form_id - 1] or {}).get(
                        (comp_id, ps), False))
                primary_fresh_slots = _fresh_signature_slots(
                    comp_id, primary_ps, primary_pair, form_fresh)
                primary_inherited_slots = _inherited_assignment_slots(
                    comp_id, primary_ps, primary_pair, form_fresh)
                primary_override_slots = _local_assignment_slots(
                    primary_pair, texture_info,
                    _pass_role(primary_pair, texture_info),
                    primary_fresh_slots, primary_inherited_slots)
                partial_material_candidate = bool(
                    not is_primary_pass
                    and depth_only
                    and role == 'material'
                    and not inherited_slots
                    and set(constants.MAIN_SLOTS).issubset(override_slots)
                    and override_slots
                    and override_slots < primary_override_slots
                    and override_slots.issubset(fresh_slots)
                    and all(
                        isinstance(pair_map.get(slot), str)
                        and alias.get(pair_map[slot], pair_map[slot])
                        == alias.get(primary_pair.get(slot), primary_pair.get(slot))
                        for slot in override_slots))
                condition_source = _condition_source(
                    comp_id, ps, set(slot for slot, _key in sig), form_fresh)
                observed_hashes = {
                    slot: tex_hash for slot, tex_hash in sorted(pair_map.items())
                    if isinstance(tex_hash, str)
                }
                assign_hashes = {}
                for slot, tex_hash in observed_hashes.items():
                    canon = alias.get(tex_hash, tex_hash)
                    service_drift_branch = (
                        (form_id, comp_id, ps, canon) in service_drift_branches)
                    if ((not is_primary_pass and not service_drift_branch
                         and not partial_material_candidate)
                            or slot not in override_slots
                            or canon not in texture_info):
                        continue
                    duplicate_service_seat = (
                        slot in constants.SERVICE_SLOTS
                        and sum(
                            1 for other_slot, other_hash
                            in observed_hashes.items()
                            if (other_slot in constants.SERVICE_SLOTS
                                and other_slot in override_slots
                                and alias.get(other_hash, other_hash) == canon)
                        ) > 1)
                    if (canonical_seats.get((comp_id, route, canon)) == slot
                            or duplicate_service_seat
                            or service_drift_branch):
                        assign_hashes[slot] = tex_hash
                is_service_drift_branch = any(
                    (form_id, comp_id, ps, alias.get(tex_hash, tex_hash))
                    in service_drift_branches
                    for tex_hash in assign_hashes.values())
                partial_material_branch = bool(
                    partial_material_candidate and assign_hashes)
                row = {
                    'form_id': form_id,
                    'form': label,
                    'component': comp_id,
                    'ps': ps,
                    'pass_role': role,
                    'depth_only': depth_only,
                    'primary_pass': is_primary_pass,
                    'service_drift_branch': is_service_drift_branch,
                    'partial_material_branch': partial_material_branch,
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
                    'inherited_slots': sorted(inherited_slots),
                    'canonical_slots': (
                        sorted(assign_hashes)
                        if (is_primary_pass or is_service_drift_branch
                            or partial_material_branch) else []),
                }
                if route is not None:
                    row['route'] = route
                remap_sources = _source_meta_for(source_meta, label, comp_id)
                if remap_sources:
                    row['remap_sources'] = remap_sources
                rows.append(row)
                if assign_hashes and (is_primary_pass or is_service_drift_branch
                                      or partial_material_branch):
                    for branch_assign_hashes in _duplicate_service_assignment_variants(
                            assign_hashes, alias):
                        branch_sig = _signature_for_duplicate_service_variant(
                            sig, assign_hashes, branch_assign_hashes, alias)
                        akey = tuple(sorted(
                            (slot, tex_hash)
                            for slot, tex_hash in branch_assign_hashes.items()))
                        component_assignments.setdefault(
                            scope, {}).setdefault(akey, set()).add(branch_sig)
                        detail = conflict_details.setdefault(scope, {}).setdefault(
                            akey, {'assign_hashes': branch_assign_hashes, 'sources': []})
                        detail['sources'].append({
                            'form': label,
                            'ps': ps,
                            'signature': _serialized_signature(branch_sig),
                            'pass_role': role,
                            'remap_sources': remap_sources,
                        })
                        sample_key = (comp_id, route, akey, branch_sig)
                        sample = branch_samples.get(sample_key)
                        if sample is None:
                            branch_samples[sample_key] = {
                                'component': comp_id,
                                'signature': _serialized_signature(branch_sig),
                                'positive_signature': _serialized_signature(branch_sig),
                                'negative_signature': [],
                                'condition_slots': [slot for slot, _key in branch_sig],
                                'assignment_slots': sorted(branch_assign_hashes),
                                'inherited_slots': sorted(
                                    set(branch_assign_hashes) & inherited_slots),
                                'assign_hashes': {
                                    str(slot): tex_hash
                                    for slot, tex_hash in branch_assign_hashes.items()},
                                'pass_role': role,
                                'depth_only': depth_only,
                                'primary_pass': is_primary_pass,
                                'service_drift_branch': is_service_drift_branch,
                                'partial_material_branch': partial_material_branch,
                                'condition_source': condition_source,
                                'forms': [label],
                                'sources': [{'form': label, 'ps': ps}],
                            }
                            if remap_sources:
                                branch_samples[sample_key]['remap_sources'] = remap_sources
                            if route is not None:
                                branch_samples[sample_key]['route'] = route
                        else:
                            sample['service_drift_branch'] = bool(
                                sample.get('service_drift_branch')
                                or is_service_drift_branch)
                            sample['partial_material_branch'] = bool(
                                sample.get('partial_material_branch')
                                or partial_material_branch)
                            if label not in sample['forms']:
                                sample['forms'].append(label)
                            sample['sources'].append({'form': label, 'ps': ps})

    conflicts = []

    unresolved_scopes: Set[Tuple[int, Optional[str]]] = set()
    for scope, by_assign in sorted(
            component_assignments.items(),
            key=lambda item: (item[0][0], item[0][1] or '')):
        comp_id, route = scope
        if len(by_assign) <= 1:
            continue
        for assign_key, signatures in sorted(by_assign.items()):
            has_branch = False
            for sample_key, sample in branch_samples.items():
                sample_comp, sample_route, sample_assign, _sample_sig = sample_key
                if ((sample_comp, sample_route) != scope
                        or sample_assign != assign_key):
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
                unresolved_scopes.add(scope)
        if scope in unresolved_scopes:
            conflict = {
                'component': comp_id,
                'reason': 'local slot-layout conditions cannot separate different assignments',
                'members': [
                    {
                        'assign_hashes': {str(slot): tex_hash
                                          for slot, tex_hash in assign_key},
                        'sources': detail['sources'],
                    }
                    for assign_key, detail in sorted(
                        conflict_details.get(scope, {}).items())
                ],
            }
            if route is not None:
                conflict['route'] = route
            conflicts.append(conflict)

    branches = list(branch_samples.values())
    for branch in branches:
        branch['forms'] = sorted(branch['forms'])

    result = {
        'schema': constants.LOCAL_FORM_DISCRIMINATOR_SCHEMA,
        'fingerprint': _hash_fingerprint(
            forms, texture_info, freshness, pass_depth),
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
    if invalid_route_components:
        result['invalid_route_components'] = sorted(invalid_route_components)
    if route_profiles:
        result['route_profiles'] = {
            str(comp_id): sorted(routes, key=lambda value: (
                value != 'base', value))
            for comp_id, routes in sorted(route_profiles.items())
        }
    return result


def validate_local_discriminator_audit(audit: object,
                                       forms: List[Tuple[str, FormData]],
                                       texture_info: TextureInfo,
                                       freshness: Optional[List[BindingFreshness]] = None,
                                       pass_depth: Optional[List[PassDepth]] = None) -> dict:
    if not isinstance(audit, dict):
        raise SlotStyleDegrade(
            '局部形态判据审计缺失；请先刷新 ShaderTextureUsage.json 后再导出。')
    if audit.get('schema') != constants.LOCAL_FORM_DISCRIMINATOR_SCHEMA:
        raise SlotStyleDegrade(
            '局部形态判据审计版本过旧；请刷新 ShaderTextureUsage.json 后再导出。')
    expected = _hash_fingerprint(forms, texture_info, freshness, pass_depth)
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
    row_by_scope: Dict[Tuple[int, Optional[str]], List[dict]] = {}
    branch_by_scope: Dict[Tuple[int, Optional[str]], List[dict]] = {}
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
        route = record.get('route')
        scope = (comp_id, route if isinstance(route, str) else None)
        if record.get('kind') == 'row':
            row_by_scope.setdefault(scope, []).append(record)
        elif record.get('kind') == 'branch':
            branch_by_scope.setdefault(scope, []).append(record)
    problems = []
    for scope, branches in sorted(
            branch_by_scope.items(),
            key=lambda item: (item[0][0], item[0][1] or '')):
        comp_id, route = scope
        evidence = row_by_scope.get(scope, [])
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
            branch_assign = tuple(branch['assign_key'])
            branch_set = set(branch_assign)
            unsafe_matches = {
                assign_key: sources for assign_key, sources in matched.items()
                if not branch_set.issubset(set(assign_key))
            }
            if unsafe_matches:
                issue_key = (
                    branch['signature'],
                    branch.get('negative_signature') or (),
                    tuple(sorted(unsafe_matches)),
                )
                if issue_key in seen_issues:
                    continue
                seen_issues.add(issue_key)
                issues.append({
                    'signature': branch['signature'],
                    'negative_signature': branch.get('negative_signature') or (),
                    'branch_assign': branch_assign,
                    'branch_source': branch.get('source') or '?',
                    'members': [
                        {'assign': assign_key, 'sources': sorted(set(sources))}
                        for assign_key, sources in sorted(unsafe_matches.items())
                    ],
                })
        if issues:
            problem = {'component': comp_id, 'issues': issues}
            if route is not None:
                problem['route'] = route
            problems.append(problem)
    return problems


def _format_local_conflict_message(problems: List[dict]) -> str:
    lines = [
        '局部形态判据无法为以下 Component 生成安全的本地 slot-layout 分支，已阻断导出：',
    ]
    for problem in problems:
        comp_id = problem['component']
        route = problem.get('route')
        route_suffix = f' route {route}' if route is not None else ''
        lines.append(f'- Component {comp_id}{route_suffix}')
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
        '2. 关闭“插槽风格贴图”，整体回到 hash-style。',
        '工具不会自动取消这些 Component，以免误以为它们仍在 local slot layer。',
    ])
    return '\n'.join(lines)


def _route_split_components(
        audit: dict,
        component_branches: Dict[int, List[_LocalBranch]],
        slot_eligible_components: Optional[Set[int]]) -> Set[int]:
    """Return selected components that require route-specific setters."""
    raw_profiles = audit.get('route_profiles')
    if not isinstance(raw_profiles, dict):
        return set()
    complete: Set[int] = set()
    for raw_comp_id, raw_routes in raw_profiles.items():
        try:
            comp_id = int(raw_comp_id)
        except (TypeError, ValueError):
            continue
        if (slot_eligible_components is not None
                and comp_id not in slot_eligible_components):
            continue
        if not isinstance(raw_routes, list):
            continue
        routes = [str(route).strip().lower() for route in raw_routes]
        route_set = set(routes)
        if (len(route_set) != len(routes) or 'base' not in route_set
                or len(route_set) < 2
                or any(route != 'base' and not _ROUTE_RE.fullmatch(route)
                       for route in route_set)):
            continue
        branches = component_branches.get(comp_id) or []
        by_route: Dict[str, List[_LocalBranch]] = {}
        valid = True
        for branch in branches:
            route = branch.route_id
            if route not in route_set:
                valid = False
                break
            positive = dict(branch.signature)
            negative = dict(branch.negative_signature)
            assigned_slots = set(branch.assign)
            required_slots = assigned_slots - set(branch.inherited_slots)
            if (not positive or len(positive) != len(branch.signature)
                    or len(negative) != len(branch.negative_signature)
                    or set(positive) & set(negative)
                    or not assigned_slots
                    or assigned_slots != set(branch.assign_hashes)
                    or not required_slots.issubset(positive)
                    or (len(positive) == 1 and not negative)):
                valid = False
                break
            by_route.setdefault(str(route), []).append(branch)
        if not valid or set(by_route) != route_set:
            continue
        for route_branches in by_route.values():
            condition_assignments: Dict[
                Tuple[
                    Tuple[Tuple[int, float], ...],
                    Tuple[Tuple[int, float], ...],
                ],
                Tuple[Tuple[int, str], ...],
            ] = {}
            for branch in route_branches:
                condition = (branch.signature, branch.negative_signature)
                assignment = tuple(sorted(branch.assign_hashes.items()))
                previous = condition_assignments.setdefault(
                    condition, assignment)
                if previous != assignment:
                    valid = False
                    break
            if not valid:
                break
        if not valid:
            continue
        assignments_by_condition: Dict[
            Tuple[
                Tuple[Tuple[int, float], ...],
                Tuple[Tuple[int, float], ...],
            ],
            Tuple[str, Tuple[Tuple[int, str], ...]],
        ] = {}
        cross_route_collision = False
        for route, route_branches in by_route.items():
            for branch in route_branches:
                condition = (branch.signature, branch.negative_signature)
                assignment = tuple(sorted(branch.assign_hashes.items()))
                previous = assignments_by_condition.setdefault(
                    condition, (route, assignment))
                if previous[0] != route and previous[1] != assignment:
                    cross_route_collision = True
                    break
            if cross_route_collision:
                break
        if cross_route_collision:
            complete.add(comp_id)
    return complete


def _local_conditions_overlap(left: _LocalBranch,
                              right: _LocalBranch) -> bool:
    left_positive = dict(left.signature)
    right_positive = dict(right.signature)
    for slot in set(left_positive) & set(right_positive):
        if left_positive[slot] != right_positive[slot]:
            return False
    left_negative = set(left.negative_signature)
    right_negative = set(right.negative_signature)
    if any(term in right_negative for term in left.signature):
        return False
    if any(term in left_negative for term in right.signature):
        return False
    return True


def _add_safe_base_route_fallbacks(
        component_branches: Dict[int, List[_LocalBranch]],
        route_split_components: Set[int]) -> None:
    """Expose unambiguous routed conditions to body/LOD base draws."""
    for comp_id in route_split_components:
        branches = component_branches.get(comp_id) or []
        base_branches = [branch for branch in branches
                         if branch.route_id == 'base']
        candidates = [branch for branch in branches
                      if branch.route_id not in (None, 'base')]
        additions: List[_LocalBranch] = []
        for candidate in candidates:
            candidate_condition = (
                candidate.signature, candidate.negative_signature)
            candidate_assignment = tuple(sorted(candidate.assign_hashes.items()))
            unsafe = False
            for peer in branches:
                if peer is candidate or not _local_conditions_overlap(
                        candidate, peer):
                    continue
                peer_condition = (peer.signature, peer.negative_signature)
                peer_assignment = tuple(sorted(peer.assign_hashes.items()))
                if (candidate_condition == peer_condition
                        and candidate_assignment != peer_assignment):
                    unsafe = True
                    break
                for slot in set(candidate.assign_hashes) & set(peer.assign_hashes):
                    if candidate.assign_hashes[slot] != peer.assign_hashes[slot]:
                        unsafe = True
                        break
                if unsafe:
                    break
            if unsafe:
                continue
            if any(
                    existing.signature == candidate.signature
                    and existing.negative_signature == candidate.negative_signature
                    and existing.assign_hashes == candidate.assign_hashes
                    for existing in base_branches + additions):
                continue
            additions.append(replace(
                candidate,
                route_id='base',
                source=f'{candidate.source} (base route fallback)',
            ))
        branches.extend(additions)


def _local_branches_from_audit(audit: dict,
                               mod_hashes: Dict[str, str],
                               canon_fn,
                               form_label_to_id: Optional[Dict[str, int]] = None,
                               slot_eligible_components: Optional[Set[int]] = None,
                               volatile_assignment_hashes: Optional[Set[str]] = None,
                               volatile_assignment_component_hashes: Optional[
                                   Set[Tuple[int, str]]] = None,
                               ) -> Tuple[
                                   Dict[int, List[_LocalBranch]], Set[str],
                                   int, Set[str], Set[int]
                               ]:
    component_branches: Dict[int, List[_LocalBranch]] = {}
    invalid_routes = {
        int(value) for value in (audit.get('invalid_route_components') or [])
        if str(value).isdigit()
    }
    blocking_invalid_routes = (
        invalid_routes if slot_eligible_components is None
        else invalid_routes & set(slot_eligible_components)
    )
    if blocking_invalid_routes:
        raise SlotStyleDegrade(
            'cross-scene route validation failed for component(s): '
            + ', '.join(str(value) for value in sorted(
                blocking_invalid_routes)))
    assigned_hashes: Set[str] = set()
    suppressed_weak_hashes: Set[str] = set()
    suppressed_weak_branches = 0
    branch_records: List[dict] = []
    pending_branches: List[
        Tuple[int, Optional[str], dict, Dict[int, str],
              Tuple[Tuple[int, str], ...], str]
    ] = []
    def _route_from(entry: dict, source: str) -> Optional[str]:
        raw_route = entry.get('route')
        if raw_route is None:
            return None
        route = str(raw_route).strip().lower()
        if route != 'base' and not _ROUTE_RE.fullmatch(route):
            raise SlotStyleDegrade(
                f'{source} has an invalid cross-scene route {raw_route!r}')
        return route

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
        return assign, tuple(sorted(assign_hashes.items()))

    rows = audit.get('rows')
    if not isinstance(rows, list):
        raise SlotStyleDegrade(
            '局部形态判据审计没有 row evidence；请刷新 STU 审计。')
    primary_row_keys: Set[Tuple[int, int, str]] = set()
    row_signature_records: List[dict] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SlotStyleDegrade(
                f'local form discriminator row #{index} has an unsupported shape')
        try:
            comp_id = int(row['component'])
        except (KeyError, TypeError, ValueError):
            raise SlotStyleDegrade(
                f'local form discriminator row #{index} has no component id')
        signature = _signature_from_audit(row.get('signature'))
        if not signature:
            continue
        source = _source_from(row, f'row#{index}')
        route = _route_from(row, f'local form discriminator row #{index}')
        form_id_raw = row.get('form_id')
        try:
            form_id = int(form_id_raw)
        except (TypeError, ValueError):
            form_id = None
        ps = str(row.get('ps') or '')
        accepted_row = bool(
            row.get('primary_pass') or row.get('partial_material_branch'))
        if accepted_row and form_id is not None and ps:
            primary_row_keys.add((form_id, comp_id, ps))
        _assign, _assign_key = _effective_assignment(
            row.get('assign_hashes'), comp_id, source)
        if not accepted_row:
            continue
        row_assign_key = tuple(sorted(
            (int(slot), canon_fn(tex_hash))
            for slot, tex_hash in (row.get('assign_hashes') or {}).items()
            if str(slot).isdigit()
            and isinstance(tex_hash, str)
            and canon_fn(tex_hash) in mod_hashes
        ))
        row_assign_hashes = dict(row_assign_key)
        for variant in _duplicate_service_assignment_variants(
                row_assign_hashes, {}):
            variant_signature = _signature_for_duplicate_service_variant(
                signature, row_assign_hashes, variant, {})
            row_signature_records.append({
                'component': comp_id,
                'route': route,
                'signature': variant_signature,
                'assign_key': tuple(sorted(variant.items())),
            })
            branch_records.append({
                'kind': 'row',
                'component': comp_id,
                'route': route,
                'signature': variant_signature,
                'negative_signature': (),
                'assign_key': tuple(sorted(variant.items())),
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
        route = _route_from(entry, f'local form discriminator branch #{index}')
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
        branch_primary_keys: Set[Tuple[int, int, str]] = set()
        for src in (entry.get('sources') or []):
            if not isinstance(src, dict):
                continue
            label = str(src.get('form') or '').strip().lower()
            form_id = (form_label_to_id or {}).get(label)
            ps = str(src.get('ps') or '')
            if form_id is not None and ps:
                branch_primary_keys.add((form_id, comp_id, ps))
        if (branch_primary_keys
                and not branch_primary_keys.issubset(primary_row_keys)
                and not entry.get('service_drift_branch')
                and not entry.get('partial_material_branch')):
            continue
        pending_branches.append(
            (comp_id, route, entry, assign, assign_key, source))

    row_records = [record for record in branch_records
                   if record.get('kind') == 'row']
    kept_branch_records: List[dict] = []
    grouped_pending: Dict[
        Tuple[int, Optional[str], Tuple[Tuple[int, str], ...], str],
        List[Tuple[int, Optional[str], dict, Dict[int, str],
                   Tuple[Tuple[int, str], ...], str]],
    ] = {}
    for item in pending_branches:
        comp_id, route, entry, _assign, assign_key, _source = item
        pass_role = str(entry.get('pass_role') or '')
        grouped_pending.setdefault(
            (comp_id, route, assign_key, pass_role), []).append(item)

    row_signatures: Dict[
        Tuple[int, Optional[str]],
        Dict[Tuple[Tuple[int, str], ...], Set[Tuple[Tuple[int, float], ...]]],
    ] = {}
    for record in row_signature_records:
        comp_id = int(record.get('component'))
        scope = (comp_id, record.get('route'))
        row_signatures.setdefault(scope, {}).setdefault(
            record.get('assign_key') or (), set()).add(record['signature'])
    global_drift_hashes = set()
    for tex_hash in volatile_assignment_hashes or ():
        canon = canon_fn(tex_hash)
        if canon:
            global_drift_hashes.add(canon)
    service_branch_facts: Dict[
        Tuple[int, str], List[Tuple[int, bool]]] = {}
    for comp_id, _route, entry, _assign, assign_key, _source in pending_branches:
        signature = _signature_from_audit(
            entry.get('positive_signature') or entry.get('signature'))
        safe = _safe_condition_shape(signature, ())
        for slot, tex_hash in assign_key:
            if _volatile_condition_slot(slot):
                service_branch_facts.setdefault(
                    (comp_id, tex_hash), []).append((slot, safe))
    drift_component_hashes = {
        component_hash
        for component_hash, facts in service_branch_facts.items()
        if len({slot for slot, _safe in facts}) > 1
    }
    safe_drift_component_hashes = {
        component_hash
        for component_hash in drift_component_hashes
        if all(safe for _slot, safe in service_branch_facts[component_hash])
    }
    for component_id, tex_hash in volatile_assignment_component_hashes or ():
        canon = canon_fn(tex_hash)
        if canon:
            drift_component_hashes.add((int(component_id), canon))
    for (comp_id, route, evidence_assign_key, pass_role), items in sorted(
            grouped_pending.items(),
            key=lambda item: (
                item[0][0], item[0][1] or '', item[0][2], item[0][3])):
        scope = (comp_id, route)
        entry_signatures = [
            _signature_from_audit(item[2].get('positive_signature')
                                  or item[2].get('signature'))
            for item in items
        ]
        own_signatures = list(
            row_signatures.get(scope, {}).get(evidence_assign_key, set()))
        if not own_signatures:
            own_signatures = [sig for sig in entry_signatures if sig]
        other_signatures = [
            sig for other_key, values in row_signatures.get(scope, {}).items()
            if other_key != evidence_assign_key for sig in values
        ]
        blocked_negative_slots = {
            slot for other_key in row_signatures.get(scope, {})
            for slot, _tex_hash in other_key
        }
        assign_key = tuple(
            (slot, tex_hash) for slot, tex_hash in evidence_assign_key
            if not (_volatile_condition_slot(slot)
                    and (tex_hash in global_drift_hashes
                         or ((comp_id, tex_hash) in drift_component_hashes
                             and (comp_id, tex_hash)
                             not in safe_drift_component_hashes)))
        )
        if not assign_key:
            continue
        inherited_sets = []
        for item in items:
            raw_inherited = item[2].get('inherited_slots')
            if not isinstance(raw_inherited, list):
                inherited_sets.append(set())
                continue
            try:
                inherited_sets.append({int(slot) for slot in raw_inherited})
            except (TypeError, ValueError):
                inherited_sets.append(set())
        inherited_slots = (
            set.intersection(*inherited_sets) if inherited_sets else set())
        condition_options = _minimal_condition_signature_options(
            own_signatures, other_signatures,
            {slot for slot, _tex_hash in assign_key} - inherited_slots,
            blocked_negative_slots,
            allow_negative=False)
        assignment_slots = {slot for slot, _tex_hash in assign_key}
        assign = {
            slot: resource for slot, resource in items[0][3].items()
            if slot in assignment_slots
        }
        source = ','.join(item[5] for item in items)
        branch_form_id = None
        forms_seen: Set[int] = set()
        for (_comp_id, _route, entry, _assign,
             _assign_key, _source) in items:
            forms_value = entry.get('forms')
            if isinstance(forms_value, list):
                labels = [str(value).strip().lower()
                          for value in forms_value if isinstance(value, str)]
            else:
                labels = []
            if not labels:
                labels = [
                    str(src.get('form')).strip().lower()
                    for src in (entry.get('sources') or [])
                    if isinstance(src, dict) and src.get('form') is not None
                ]
            for label in labels:
                form_id = (form_label_to_id or {}).get(label)
                if form_id is not None:
                    forms_seen.add(form_id)
        if len(forms_seen) == 1:
            branch_form_id = next(iter(forms_seen))
        component_excluded = (
            slot_eligible_components is not None
            and comp_id not in slot_eligible_components)
        assign_hash_by_slot = dict(assign_key)
        for signature, negative_signature in condition_options:
            condition_slots = {slot for slot, _key in signature}
            missing_slots = (
                assignment_slots - inherited_slots - condition_slots)
            if missing_slots:
                raise SlotStyleDegrade(
                    f'component {comp_id}: local slot condition does not cover '
                    'assignment slot(s): '
                    + ', '.join(
                        f'ps-t{slot}' for slot in sorted(missing_slots)))
            condition_hashes = {
                tex_hash for slot, tex_hash in assign_key
                if slot in condition_slots and _volatile_condition_slot(slot)
            }
            filtered_assign = {
                slot: resource for slot, resource in sorted(assign.items())
                if (not _volatile_condition_slot(slot)
                    or slot in condition_slots
                    or (
                        assign_hash_by_slot.get(slot) not in global_drift_hashes
                        and (comp_id, assign_hash_by_slot.get(slot))
                        not in drift_component_hashes
                        and assign_hash_by_slot.get(slot) not in condition_hashes
                    ))
            }
            if not filtered_assign:
                filtered_assign = dict(assign)
            filtered_assign_key = tuple(
                (slot, tex_hash) for slot, tex_hash in assign_key
                if slot in filtered_assign)
            weak_blocked = False
            if (not component_excluded and _local_branch_is_weak(
                    signature, negative_signature, filtered_assign, pass_role)):
                weak_blocked = any(
                    other != evidence_assign_key and any(
                        all(term in other_sig for term in signature)
                        for other_sig in values)
                    for other, values in row_signatures.get(scope, {}).items())
            if weak_blocked:
                suppressed_weak_branches += 1
                for _slot, tex_hash in assign_key:
                    suppressed_weak_hashes.add(tex_hash)
                continue
            kept_branch_records.append({
                'kind': 'branch',
                'component': comp_id,
                'route': route,
                'signature': signature,
                'negative_signature': negative_signature,
                'assign_key': filtered_assign_key,
                'source': source,
            })
            for _slot, tex_hash in filtered_assign_key:
                assigned_hashes.add(tex_hash)
            component_branches.setdefault(comp_id, []).append(_LocalBranch(
                signature=signature,
                negative_signature=negative_signature,
                assign=filtered_assign,
                form_id=branch_form_id,
                label='local',
                ps='',
                source=source,
                assign_hashes=dict(filtered_assign_key),
                route_id=route,
                inherited_slots=tuple(sorted(
                    set(filtered_assign) & inherited_slots)),
            ))
    branch_records.extend(kept_branch_records)
    route_split_components = _route_split_components(
        audit, component_branches, slot_eligible_components)
    routed_components = {
        comp_id for comp_id, branches in component_branches.items()
        if any(branch.route_id is not None for branch in branches)
    }
    generic_route_components = {
        comp_id for comp_id in routed_components
        if comp_id not in route_split_components
        and (slot_eligible_components is None
             or comp_id in slot_eligible_components)
    }
    if generic_route_components:
        for comp_id in generic_route_components:
            for branch in component_branches.get(comp_id, []):
                branch.route_id = None
        for record in branch_records:
            try:
                comp_id = int(record.get('component'))
            except (TypeError, ValueError):
                continue
            if comp_id in generic_route_components:
                record['route'] = None
    problems = _local_conflict_messages(
        branch_records, slot_eligible_components)
    if problems:
        raise LocalDiscriminatorConflict(problems)
    _add_safe_base_route_fallbacks(
        component_branches, route_split_components)
    return (component_branches, assigned_hashes,
            suppressed_weak_branches, suppressed_weak_hashes,
            route_split_components)


def _build_local_plan(forms: List[Tuple[str, FormData]],
                      textures: List[Tuple[str, str]],
                      texture_info: TextureInfo,
                      load_warnings: Optional[List[str]] = None,
                      component_ranges: Optional[Dict[int, Tuple[int, int]]] = None,
                      lod_ranges: Optional[Dict[int, Dict[int, Tuple[int, int]]]] = None,
                      multi_state_seats: Optional[Dict[Tuple[int, int], Set[str]]] = None,
                      live_seed: Optional[Set[str]] = None,
                      freshness: Optional[List[BindingFreshness]] = None,
                      pass_depth: Optional[List[PassDepth]] = None,
                      slot_eligible_components: Optional[Set[int]] = None,
                      local_discriminator_audit: Optional[dict] = None,
                      formid_auxiliary_anchors: Optional[List[Tuple[str, int]]] = None,
                      volatile_assignment_hashes: Optional[Set[str]] = None,
                      volatile_assignment_component_hashes: Optional[
                          Set[Tuple[int, str]]] = None,
                      texture_hash_allowlist: Optional[Set[str]] = None) -> SlotPlan:
    warnings: List[str] = list(load_warnings or [])
    anchor_resources, watchdog_form, gated_forms = _anchor_runtime_state(
        forms, formid_auxiliary_anchors, warnings)
    validate_local_discriminator_audit(
        local_discriminator_audit, forms, texture_info, freshness, pass_depth)
    forms, dirty_hashes_raw, dirty_slots, phantom_pairs = _filtered_forms(
        forms, freshness, warnings)
    alias = _variant_aliases(texture_info)
    allowed_hashes = (None if texture_hash_allowlist is None else {
        str(texture_hash).strip().lower()
        for texture_hash in texture_hash_allowlist
    })

    def _allowed_texture(texture_hash: str) -> bool:
        return allowed_hashes is None or texture_hash.lower() in allowed_hashes

    mod_hashes = {
        h: res for h, res in textures if _allowed_texture(h)
    }
    resource_to_section_for_exclusion = {}
    for texture_hash, resource in textures:
        if not _allowed_texture(texture_hash):
            continue
        match = re.fullmatch(r'ResourceTexture(\d+)', resource)
        if match:
            resource_to_section_for_exclusion[resource] = (
                f'TextureOverrideTexture{match.group(1)}')
    if not mod_hashes:
        raise SlotStyleDegrade('mod has no textures, nothing to do')

    def _canon(tex_hash: Optional[str]) -> Optional[str]:
        if tex_hash is None:
            return None
        return alias.get(tex_hash, tex_hash)

    live_fallback: Dict[str, str] = {}
    component_hash_fallbacks: Dict[str, List[ComponentHashFallback]] = {}
    unsafe_fallback: List[Dict[str, object]] = []
    suppressed_weak_branches = 0
    suppressed_weak_hashes: Set[str] = set()

    def _route_live(tex_hash: str, reason: str):
        canon = _canon(tex_hash) or tex_hash
        if canon in mod_hashes:
            live_fallback.setdefault(canon, reason)

    def _flag_unsafe_live(tex_hash: str, comp_id: int, reason: str,
                          source: str, slot: Optional[int] = None):
        canon = _canon(tex_hash) or tex_hash
        if canon in mod_hashes and canon in live_fallback:
            unsafe_fallback.append(
                _slot_issue_entry(canon, comp_id, reason, source, slot))

    def _audit_source_from(entry: dict, fallback: str) -> str:
        sources = entry.get('sources') or []
        if isinstance(sources, list) and sources:
            joined = ','.join(
                f"{src.get('form', '?')}/ps={src.get('ps', '?')}"
                for src in sources if isinstance(src, dict))
            if joined:
                return joined
        form = entry.get('form')
        ps = entry.get('ps')
        if isinstance(form, str) or isinstance(ps, str):
            return f'{form or "?"}/ps={ps or "?"}'
        return fallback

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
        key = fi
        fi_text.setdefault(key, _fi_str(fi))
        group_families.setdefault(key, {}).setdefault(
            constants.format_prefix(fmt), (fmt, _fi_str(fi)))

    label_to_id = {
        label.strip().lower(): form_id
        for form_id, (label, _data) in enumerate(forms, start=1)
    }
    (component_branches, raw_assigned_hashes, suppressed_weak_branches,
     suppressed_weak_hashes,
     route_split_components) = _local_branches_from_audit(
         local_discriminator_audit, mod_hashes, _canon, label_to_id,
         slot_eligible_components, volatile_assignment_hashes,
         volatile_assignment_component_hashes)
    if suppressed_weak_branches:
        warnings.append(
            f'suppressed {suppressed_weak_branches} weak local slot branch(es); '
            'single-slot positive conditions without a negative discriminator '
            'are unsafe for same-IB multi-instance rendering')
    slot_unrepresented: List[Dict[str, object]] = []
    raw_slot_evidence: Dict[str, List[Dict[str, object]]] = {}
    raw_eligible_hashes: Set[str] = set()
    audit_rows = local_discriminator_audit.get('rows') or []
    if isinstance(audit_rows, list):
        for row in audit_rows:
            if not isinstance(row, dict):
                continue
            try:
                comp_id = int(row.get('component'))
            except (TypeError, ValueError):
                continue
            source = _audit_source_from(row, 'row')
            hash_source = (row.get('assign_hashes') or {})
            component_excluded = (
                slot_eligible_components is not None
                and comp_id not in slot_eligible_components)
            if component_excluded:
                hash_source = row.get('observed_hashes') or hash_source
            for raw_slot, tex_hash in hash_source.items():
                if not isinstance(tex_hash, str):
                    continue
                canon = _canon(tex_hash) or tex_hash
                if canon not in mod_hashes:
                    continue
                try:
                    slot = int(raw_slot)
                except (TypeError, ValueError):
                    slot = None
                raw_slot_evidence.setdefault(canon, []).append(
                    _slot_issue_entry(
                        canon, comp_id,
                        'not represented by a safe local slot branch',
                        source, slot))
                if component_excluded:
                    resource = mod_hashes[canon]
                    _add_component_hash_fallback(
                        component_hash_fallbacks,
                        canon,
                        comp_id,
                        resource,
                        resource_to_section_for_exclusion.get(resource, ''),
                        'component excluded from slot layer')
                else:
                    if slot is None or not _volatile_condition_slot(slot):
                        raw_eligible_hashes.add(canon)
                    _flag_unsafe_live(
                        canon, comp_id,
                        live_fallback.get(canon, 'unsafe hash fallback'),
                        source, slot)
    if slot_eligible_components is not None:
        resource_to_hash_for_exclusion = {
            res: h for h, res in textures if _allowed_texture(h)
        }
        for comp_id in list(component_branches):
            if comp_id in slot_eligible_components:
                continue
            for branch in component_branches.pop(comp_id):
                for resource in branch.assign.values():
                    tex_hash = resource_to_hash_for_exclusion.get(resource)
                    if tex_hash:
                        _add_component_hash_fallback(
                            component_hash_fallbacks,
                            tex_hash,
                            comp_id,
                            resource,
                            resource_to_section_for_exclusion.get(
                                resource, ''),
                            'component excluded from slot layer')
    merged_component_branches: Dict[int, List[_LocalBranch]] = {}
    for comp_id, branches in component_branches.items():
        merged: List[_LocalBranch] = []
        seen: Dict[
            Tuple[
                Optional[str],
                Tuple[Tuple[int, float], ...],
                Tuple[Tuple[int, float], ...],
                Tuple[Tuple[int, str], ...],
            ],
            _LocalBranch,
        ] = {}
        for branch in branches:
            key = (
                branch.route_id,
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

    resource_to_hash = {
        res: h for h, res in textures if _allowed_texture(h)
    }
    all_assigned_hashes: Set[str] = set()
    for branches in component_branches.values():
        for branch in branches:
            for resource in branch.assign.values():
                tex_hash = resource_to_hash.get(resource)
                if tex_hash:
                    all_assigned_hashes.add(tex_hash)
    for tex_hash in sorted(set(component_hash_fallbacks) & all_assigned_hashes):
        live_fallback.pop(tex_hash, None)
    for tex_hash in sorted(suppressed_weak_hashes - all_assigned_hashes):
        slot_unrepresented.extend(
            raw_slot_evidence.get(tex_hash) or [
                _slot_issue_entry(
                    tex_hash, -1,
                    'not represented by a safe local slot branch',
                    'local discriminator audit')])
    missing_slot_hashes = (raw_eligible_hashes - all_assigned_hashes
                           - set(live_fallback))
    for tex_hash in sorted(missing_slot_hashes):
        slot_unrepresented.extend(
            raw_slot_evidence.get(tex_hash) or [
                _slot_issue_entry(
                    tex_hash, -1,
                    'not represented by a safe local slot branch',
                    'local discriminator audit')])
    if slot_unrepresented or unsafe_fallback:
        raise SlotStyleDegrade(
            _format_slot_unrepresented(unsafe_fallback + slot_unrepresented))

    if not component_branches:
        if live_fallback or component_hash_fallbacks:
            return SlotPlan(
                block_text='',
                component_list_names={},
                covered_resource_indices=set(),
                blind_zone=[],
                multi_form=False,
                used_slots=[],
                phantom_only_resource_indices=set(),
                phantom_suppressed=[],
                extra_globals=[],
                watchdog_lines=[],
                default_form_id=1,
                live_fallback=dict(live_fallback),
                component_hash_fallbacks={
                    h: list(entries)
                    for h, entries in component_hash_fallbacks.items()
                },
                warnings=warnings,
                stats={
                    'forms': len(forms),
                    'components': 0,
                    'branches': 0,
                    'conflicts': 0,
                    'marks': 0,
                    'fork_latches': 0,
                    'anchors': 0,
                    'anchor_watchdog': 0,
                    'probes': 0,
                    'live_fallback': len(live_fallback),
                    'format_sections': 0,
                    'format_sections_raw': 0,
                    'format_sections_unique': 0,
                    'format_sections_removed': 0,
                    'covered_textures': 0,
                    'blind_zone_textures': 0,
                    'phantom_suppressed_textures': 0,
                    'phantom_pairs': phantom_pairs,
                    'dirty_slots': dirty_slots,
                    'service_slots': 0,
                    'suppressed_latches': 0,
                    'local_form_discriminator': 1,
                    'suppressed_weak_branches': suppressed_weak_branches,
                },
            )
        raise SlotStyleDegrade('no component produced any safe slot assignment')

    used_slots: Set[int] = set()
    used_families: Dict[int, Set[float]] = {}
    component_list_names: Dict[int, str] = {}
    body_chunks: List[str] = []
    typed_sections: List[SlotSection] = []
    branch_contract: Dict[str, Dict[str, object]] = {}
    restore_contract: Dict[str, Dict[str, object]] = {}
    component_route_lists: Dict[int, Dict[str, str]] = {}

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
        return terms

    def _condition(terms: List[str], branch: _LocalBranch) -> str:
        parts = list(terms)
        if branch.form_id in gated_forms:
            parts.append(f'{constants.VAR_FORM} == {branch.form_id}')
        return ' && '.join(parts)

    def _append_assignments(chunk: List[str], assign: Dict[int, str], indent: str):
        for slot, res in sorted(assign.items()):
            chunk.append(f'{indent}ps-t{slot} = ref {res}')
            used_slots.add(slot)

    for comp_id in sorted(component_branches):
        by_route: Dict[Optional[str], List[_LocalBranch]] = {}
        for branch in component_branches[comp_id]:
            by_route.setdefault(branch.route_id, []).append(branch)
        routed = any(route is not None for route in by_route)
        if routed and (None in by_route or 'base' not in by_route):
            raise SlotStyleDegrade(
                f'component {comp_id}: cross-scene route profile is incomplete')

        emitted_names: Dict[str, str] = {}
        emitted_by_name: Dict[str, List[_LocalBranch]] = {}
        route_order = sorted(
            by_route, key=lambda route: (route != 'base', route or ''))
        for route in route_order:
            if route is None:
                name = constants.CMDLIST_SET_TEXTURES.format(
                    component_id=comp_id)
            else:
                suffix = 'Base' if route == 'base' else route
                name = (f'CommandListSetTexturesComponent{comp_id}'
                        f'Route{suffix}')
            chunk: List[str] = ['', f'[{name}]', 'if $object_detected == 1']
            ordered = sorted(
                by_route[route],
                key=lambda b: (-len(b.assign), -len(b.signature),
                               b.form_id or 0, b.signature,
                               tuple(sorted(b.assign.items()))))
            first = True
            emitted: List[_LocalBranch] = []
            for branch in ordered:
                terms = _terms(comp_id, branch)
                if not terms:
                    continue
                chunk.append(
                    f'    {"if" if first else "else if"} '
                    + _condition(terms, branch))
                _append_assignments(chunk, branch.assign, '        ')
                emitted.append(branch)
                first = False
            if first:
                continue
            chunk.append('    endif')
            chunk.append('endif')
            body_chunks.append('\n'.join(chunk))
            typed_sections.append(SlotSection(
                name=name,
                lines=tuple(chunk[2:]),
                kind='setter',
                component_id=comp_id,
            ))
            branch_contract[name] = _serialized_branch_contract(
                comp_id, list(emitted), route=route,
                emitted_form_gates=gated_forms)
            emitted_by_name[name] = emitted
            if route is not None:
                emitted_names[route] = name

        if routed:
            if 'base' not in emitted_names:
                raise SlotStyleDegrade(
                    f'component {comp_id}: base route emitted no texture branch')
            component_list_names[comp_id] = emitted_names['base']
            component_route_lists[comp_id] = emitted_names
            policy = _local_restore_policy(
                comp_id,
                [branch for emitted in emitted_by_name.values()
                 for branch in emitted],
                local_discriminator_audit,
                volatile_assignment_hashes=volatile_assignment_hashes,
                volatile_assignment_component_hashes=
                volatile_assignment_component_hashes,
                canon_fn=_canon,
            )
            for name in emitted_names.values():
                restore_contract[name] = dict(policy)
        else:
            generic_name = constants.CMDLIST_SET_TEXTURES.format(
                component_id=comp_id)
            if generic_name in branch_contract:
                component_list_names[comp_id] = generic_name
                restore_contract[generic_name] = _local_restore_policy(
                    comp_id,
                    emitted_by_name[generic_name],
                    local_discriminator_audit,
                    volatile_assignment_hashes=volatile_assignment_hashes,
                    volatile_assignment_component_hashes=
                    volatile_assignment_component_hashes,
                    canon_fn=_canon,
                )

    covered_resource_indices: Set[int] = set()
    blind_zone: List[Tuple[str, str]] = []
    dirty_hashes = {_canon(h) or h for h in dirty_hashes_raw}
    dirty_only_hashes = dirty_hashes - all_assigned_hashes
    phantom_only_resource_indices: Set[int] = set()
    phantom_suppressed: List[Tuple[str, str]] = []
    has_freshness_evidence = freshness is not None and any(freshness)
    for index, (h, _res) in enumerate(textures):
        if not _allowed_texture(h):
            continue
        canon = _canon(h) or h
        if canon in all_assigned_hashes and canon not in live_fallback:
            covered_resource_indices.add(index)
        elif canon in component_hash_fallbacks:
            pass
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

    if not body_chunks:
        return SlotPlan(
            block_text='',
            component_list_names={},
            covered_resource_indices=covered_resource_indices,
            blind_zone=blind_zone,
            multi_form=False,
            used_slots=[],
            phantom_only_resource_indices=phantom_only_resource_indices,
            phantom_suppressed=phantom_suppressed,
            extra_globals=[],
            watchdog_lines=[],
            default_form_id=1,
            live_fallback=dict(live_fallback),
            component_hash_fallbacks={
                h: list(entries)
                for h, entries in component_hash_fallbacks.items()
            },
            warnings=warnings,
            stats={
                'forms': len(forms),
                'components': 0,
                'branches': 0,
                'conflicts': 0,
                'marks': 0,
                'fork_latches': 0,
                'anchors': 0,
                'anchor_watchdog': 0,
                'probes': 0,
                'live_fallback': len(live_fallback),
                'format_sections': 0,
                'format_sections_raw': 0,
                'format_sections_unique': 0,
                'format_sections_removed': 0,
                'covered_textures': len(covered_resource_indices),
                'blind_zone_textures': len(blind_zone),
                'phantom_suppressed_textures': len(phantom_only_resource_indices),
                'phantom_pairs': phantom_pairs,
                'dirty_slots': dirty_slots,
                'service_slots': 0,
                'suppressed_latches': 0,
                'local_form_discriminator': 1,
                'suppressed_weak_branches': suppressed_weak_branches,
            },
        )

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
    if anchor_resources:
        out.append('; formid auxiliary gate only narrows already slot-safe branches.')
    else:
        out.append('; no global form state or form-anchor watchdog is used.')
    out.append('; ============================================================')
    if anchor_resources:
        out.append('')
        out.append('; -- Optional formid auxiliary anchors')
        for h, form_id in sorted(anchor_resources):
            anchor_name = constants.SEC_RESOURCE_ANCHOR.format(anchor_hash=h)
            anchor_lines = [
                f'hash = {h}',
                'allow_duplicate_hash = true',
                'match_priority = 0',
                'match_first_index = 0',
                f'{constants.VAR_FORM} = {form_id}',
            ]
            if watchdog_form is not None:
                anchor_lines.append(f'{constants.VAR_ANCHOR_SEEN} = 1')
            typed_sections.append(SlotSection(
                name=anchor_name,
                lines=tuple(anchor_lines),
                kind='anchor',
            ))
            out.append('')
            out.append(f'[{anchor_name}]')
            out.extend(anchor_lines)
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
                        section_name = template.format(
                            component_id=comp_id,
                            format_name=member,
                            level=level,
                        )
                        section_lines = (
                            f'match_first_index = {first}',
                            f'match_index_count = {count}',
                            f'match_priority = {constants.FORMAT_TAG_PRIORITY}',
                            f'match_format = {member}',
                            f'filter_index = {text}',
                        )
                        typed_sections.append(SlotSection(
                            name=section_name,
                            lines=section_lines,
                            kind='format_tag',
                            component_id=comp_id,
                            level=level,
                        ))
                        out.append('')
                        out.append(f'[{section_name}]')
                        out.extend(section_lines)
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
    formid_active = bool(anchor_resources) or watchdog_form is not None

    return SlotPlan(
        block_text='\n'.join(out),
        component_list_names=component_list_names,
        covered_resource_indices=covered_resource_indices,
        blind_zone=blind_zone,
        multi_form=formid_active,
        used_slots=sorted(used_slots),
        phantom_only_resource_indices=phantom_only_resource_indices,
        phantom_suppressed=phantom_suppressed,
        extra_globals=extra_globals,
        watchdog_lines=watchdog_lines,
        default_form_id=watchdog_form if watchdog_form is not None else 1,
        live_fallback=dict(live_fallback),
        component_hash_fallbacks={
            h: list(entries)
            for h, entries in component_hash_fallbacks.items()
        },
        branch_contract=branch_contract,
        restore_contract=restore_contract,
        component_route_lists=component_route_lists,
        sections=tuple(typed_sections),
        warnings=warnings,
        stats={
            'forms': len(forms),
            'components': len(component_list_names),
            'branches': sum(len(b) for b in component_branches.values()),
            'conflicts': 0,
            'marks': 0,
            'fork_latches': 0,
            'anchors': len(anchor_resources),
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
            'local_form_discriminator': 1,
            'suppressed_weak_branches': suppressed_weak_branches,
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
                    freshness: Optional[List[BindingFreshness]],
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
               freshness: Optional[List[BindingFreshness]] = None,
               pass_depth: Optional[List[PassDepth]] = None,
               slot_eligible_components: Optional[Set[int]] = None,
               local_form_discriminator: bool = False,
               local_discriminator_audit: Optional[dict] = None,
               formid_auxiliary_anchors: Optional[List[Tuple[str, int]]] = None,
               volatile_assignment_hashes: Optional[Set[str]] = None,
               volatile_assignment_component_hashes: Optional[
                   Set[Tuple[int, str]]] = None,
               texture_hash_allowlist: Optional[Set[str]] = None) -> SlotPlan:
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
            pass_depth=pass_depth,
            slot_eligible_components=slot_eligible_components,
            local_discriminator_audit=local_discriminator_audit,
            formid_auxiliary_anchors=formid_auxiliary_anchors,
            volatile_assignment_hashes=volatile_assignment_hashes,
            volatile_assignment_component_hashes=
            volatile_assignment_component_hashes,
            texture_hash_allowlist=texture_hash_allowlist)
    warnings: List[str] = list(load_warnings or [])
    multi_form = len(forms) > 1
    allowed_hashes = (None if texture_hash_allowlist is None else {
        str(texture_hash).strip().lower()
        for texture_hash in texture_hash_allowlist
    })

    def _allowed_texture(texture_hash: str) -> bool:
        return allowed_hashes is None or texture_hash.lower() in allowed_hashes

    mod_hashes = {
        h: res for h, res in textures if _allowed_texture(h)
    }
    if not mod_hashes:
        raise SlotStyleDegrade('mod has no textures, nothing to do')

    has_freshness_evidence = freshness is not None and any(freshness)
    forms, dirty_hashes_raw, dirty_slots, phantom_pairs = _filtered_forms(
        forms, freshness, warnings)
    alias = _variant_aliases(texture_info)

    live_fallback: Dict[str, str] = {}
    component_hash_fallbacks: Dict[str, List[ComponentHashFallback]] = {}
    unsafe_fallback: List[Dict[str, object]] = []
    resource_to_section_for_exclusion = {}
    for texture_hash, resource in textures:
        if not _allowed_texture(texture_hash):
            continue
        match = re.fullmatch(r'ResourceTexture(\d+)', resource)
        if match:
            resource_to_section_for_exclusion[resource] = (
                f'TextureOverrideTexture{match.group(1)}')

    def _canon(tex_hash: Optional[str]) -> Optional[str]:
        if tex_hash is None:
            return None
        return alias.get(tex_hash, tex_hash)

    def _route_live(tex_hash: str, reason: str):
        canon = _canon(tex_hash) or tex_hash
        if canon in mod_hashes:
            live_fallback.setdefault(canon, reason)

    def _flag_unsafe_live(tex_hash: str, comp_id: int, reason: str,
                          source: str, slot: Optional[int] = None):
        canon = _canon(tex_hash) or tex_hash
        if canon in mod_hashes and canon in live_fallback:
            unsafe_fallback.append(
                _slot_issue_entry(canon, comp_id, reason, source, slot))

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
            warnings.append(
                f'form anchor {anchor_hash!r} skipped (shader-hash anchors are '
                'audit-only and cannot be emitted as runtime slot conditions)')
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
        key = fi
        fi_text.setdefault(key, _fi_str(fi))
        group_families.setdefault(key, {}).setdefault(
            constants.format_prefix(fmt), (fmt, _fi_str(fi)))

    raw_branches: Dict[int, List[_Branch]] = {}
    raw_assigned_hashes: Set[str] = set()
    raw_slot_evidence: Dict[str, List[Dict[str, object]]] = {}
    suppressed_weak_hashes: Set[str] = set()
    suppressed_weak_branches = 0
    suppressed_covered_hashes: Set[str] = set()
    suppressed_covered_branches = 0

    for form_id, (label, form_data) in enumerate(forms, start=1):
        form_fresh = (freshness[form_id - 1]
                      if freshness is not None and form_id - 1 < len(freshness)
                      else None)
        for comp_id, comp_pairs in form_data.items():
            for ps, pair_map in comp_pairs.items():
                pass_role = _pass_role(pair_map, texture_info)
                eligible_slots = _eligible_slots(pair_map)
                if not has_freshness_evidence:
                    eligible_slots -= set(constants.SERVICE_SLOTS)
                fresh_slots = _fresh_signature_slots(
                    comp_id, ps, pair_map, form_fresh)
                inherited_slots = _inherited_assignment_slots(
                    comp_id, ps, pair_map, form_fresh)
                if has_freshness_evidence:
                    eligible_slots &= fresh_slots | inherited_slots
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
                    elif canon in live_fallback:
                        _flag_unsafe_live(
                            canon, comp_id, live_fallback[canon],
                            f'{label}/ps={ps}', slot)
                if not assigned:
                    continue
                if slot_eligible_components is not None and comp_id not in slot_eligible_components:
                    for tex_hash in assigned_hashes.values():
                        _add_component_hash_fallback(
                            component_hash_fallbacks,
                            tex_hash,
                            comp_id,
                            mod_hashes[tex_hash],
                            resource_to_section_for_exclusion.get(
                                mod_hashes[tex_hash], ''),
                            'component excluded from slot layer')
                    continue

                sig_slots = (
                    eligible_slots & fresh_slots
                    if has_freshness_evidence else eligible_slots)
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
                    full_signature=tuple(signature),
                    pass_role=pass_role,
                    condition_slots=tuple(slot for slot, _key in signature),
                    assignment_slots=tuple(sorted(assigned)),
                    inherited_slots=tuple(sorted(
                        set(assigned) & inherited_slots)),
                    ps=ps,
                    observed={
                        slot: canon for slot, canon in sorted(
                            (_slot, _canon(_hash))
                            for _slot, _hash in pair_map.items()
                            if _canon(_hash) is not None)
                    },
                ))
                raw_assigned_hashes.update(assigned_hashes.values())
                for slot, tex_hash in assigned_hashes.items():
                    raw_slot_evidence.setdefault(tex_hash, []).append(
                        _slot_issue_entry(
                            tex_hash, comp_id,
                            'not represented by a safe slot branch',
                            f'{label}/ps={ps}', slot))

    if not raw_branches:
        if unsafe_fallback:
            raise SlotStyleDegrade(_format_slot_unrepresented(unsafe_fallback))
        if component_hash_fallbacks:
            return SlotPlan(
                block_text='',
                component_list_names={},
                covered_resource_indices=set(),
                blind_zone=[],
                multi_form=multi_form,
                used_slots=[],
                phantom_only_resource_indices=set(),
                phantom_suppressed=[],
                extra_globals=[],
                watchdog_lines=[],
                default_form_id=1,
                live_fallback=dict(live_fallback),
                component_hash_fallbacks={
                    h: list(entries)
                    for h, entries in component_hash_fallbacks.items()
                },
                warnings=warnings,
                stats={
                    'forms': len(forms),
                    'components': 0,
                    'branches': 0,
                    'conflicts': 0,
                    'anchors': len(anchor_resources) + len(anchor_shaders),
                    'probes': 0,
                    'live_fallback': len(live_fallback),
                    'format_sections': 0,
                    'format_sections_raw': 0,
                    'format_sections_removed': 0,
                    'branch_writes': 0,
                    'dirty_ignored': 0,
                    'dirty_slot_hashes': 0,
                    'phantom_suppressed': 0,
                    'suppressed_latches': 0,
                    'local_form_discriminator': 0,
                    'suppressed_weak_branches': suppressed_weak_branches,
                },
            )
        if live_fallback:
            raise SlotStyleDegrade(
                'all slot candidates were routed to stock hash sections; no '
                'slot command lists were emitted')
        raise SlotStyleDegrade('no component produced any slot assignment')

    resource_to_hash = {
        res: h for h, res in textures if _allowed_texture(h)
    }

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
                weak_members_covered = True
                for member in members:
                    if not _is_weak_anchor_branch(member, needs_form_gate=False):
                        weak_members_covered = False
                        break
                    if not _branch_resources_covered_by_stronger_layout(
                            member, comp_id, branches, forms, texture_info,
                            alias):
                        weak_members_covered = False
                        break
                if weak_members_covered:
                    suppressed_weak_branches += len(members)
                    for member in members:
                        for resource in member.assign.values():
                            tex_hash = resource_to_hash.get(resource)
                            if tex_hash:
                                suppressed_weak_hashes.add(tex_hash)
                    continue
                raise SlotStyleDegrade(
                    f'component {comp_id}: multiple texture sets share the '
                    'same slot-layout signature; pure 0hash slot export cannot '
                    'distinguish them. Exclude this component from the slot '
                    'layer or disable slot-style textures.')
            assign_key, form_ids = next(iter(by_assign.items()))
            member = sample[assign_key]
            if multi_form and form_ids != set(range(1, len(forms) + 1)):
                merged.append(_branch_with_assign(
                    member, dict(assign_key), None))
            else:
                merged.append(_branch_with_assign(
                    member, dict(assign_key), None))
        _minimize_anchor_branches(merged)
        kept: List[_Branch] = []
        seen_branch_keys: Set[
            Tuple[
                Tuple[Tuple[int, float], ...],
                Tuple[Tuple[int, float], ...],
                Optional[int],
                Tuple[Tuple[int, str], ...],
                Tuple[str, ...],
            ]
        ] = set()
        for branch in merged:
            key = _branch_key(branch)
            if key in seen_branch_keys:
                suppressed_covered_branches += 1
                for resource in branch.assign.values():
                    tex_hash = resource_to_hash.get(resource)
                    if tex_hash:
                        suppressed_covered_hashes.add(tex_hash)
                continue
            seen_branch_keys.add(key)
            if _is_weak_anchor_branch(branch, needs_form_gate=False):
                if _branch_assignments_match_observed_scope(
                        branch, comp_id, forms, texture_info, alias,
                        resource_to_hash):
                    if _branch_resources_covered_by_stronger_layout(
                            branch, comp_id, merged, forms, texture_info,
                            alias):
                        suppressed_weak_branches += 1
                        for resource in branch.assign.values():
                            tex_hash = resource_to_hash.get(resource)
                            if tex_hash:
                                suppressed_weak_hashes.add(tex_hash)
                        continue
                    kept.append(branch)
                    continue
                suppressed_weak_branches += 1
                for resource in branch.assign.values():
                    tex_hash = resource_to_hash.get(resource)
                    if tex_hash:
                        suppressed_weak_hashes.add(tex_hash)
                continue
            if _branch_covered_by_stronger_layout(branch, merged):
                suppressed_covered_branches += 1
                for resource in branch.assign.values():
                    tex_hash = resource_to_hash.get(resource)
                    if tex_hash:
                        suppressed_covered_hashes.add(tex_hash)
                continue
            kept.append(branch)
        component_branches[comp_id] = kept

    if suppressed_weak_branches:
        warnings.append(
            f'suppressed {suppressed_weak_branches} weak single-slot or '
            f'auxiliary slot branch(es); format-only ps-t conditions are '
            f'unsafe for same-IB multi-instance rendering')
    if suppressed_covered_branches:
        warnings.append(
            f'suppressed {suppressed_covered_branches} slot branch(es) covered '
            'by a stronger same-form layout')

    all_assigned_hashes: Set[str] = set()
    for tex_hash in suppressed_covered_hashes:
        all_assigned_hashes.add(tex_hash)
    for branches in component_branches.values():
        for branch in branches:
            for resource in branch.assign.values():
                tex_hash = resource_to_hash.get(resource)
                if tex_hash:
                    all_assigned_hashes.add(tex_hash)
    unrepresented_slot_hashes = (
        raw_assigned_hashes | suppressed_weak_hashes) - all_assigned_hashes
    slot_unrepresented: List[Dict[str, object]] = []
    for tex_hash in sorted(unrepresented_slot_hashes):
        slot_unrepresented.extend(
            raw_slot_evidence.get(tex_hash) or [
                _slot_issue_entry(
                    tex_hash, -1,
                    'not represented by a safe slot branch',
                    'anchor-driven slot plan')])
    if slot_unrepresented or unsafe_fallback:
        raise SlotStyleDegrade(
            _format_slot_unrepresented(unsafe_fallback + slot_unrepresented))

    used_slots: Set[int] = set()
    used_families: Dict[int, Set[float]] = {}
    component_list_names: Dict[int, str] = {}
    body_chunks: List[str] = []

    branch_contract: Dict[str, Dict[str, object]] = {}
    restore_contract: Dict[str, Dict[str, object]] = {}

    def _terms(comp_id: int, branch: _Branch) -> List[str]:
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

        ordered = sorted(
            component_branches[comp_id],
            key=lambda b: (-len(b.assign), -len(b.signature), b.form_gate or 0,
                           b.signature, b.negative_signature,
                           tuple(sorted(b.assign.items()))))
        first = True
        emitted: List[_Branch] = []
        for branch in ordered:
            terms = _terms(comp_id, branch)
            if not terms:
                continue

            chunk.append(f'{"if" if first else "else if"} '
                          f'{_condition(terms, branch.form_gate)}')
            _append_assignments(chunk, branch.assign, '    ')
            emitted.append(branch)
            first = False
        if not first:
            chunk.append('endif')
            body_chunks.append('\n'.join(chunk))
            branch_contract[name] = _serialized_branch_contract(
                comp_id, list(emitted))
            restore_contract[name] = {'mode': 'full'}
        else:
            component_list_names.pop(comp_id, None)

    covered_resource_indices: Set[int] = set()
    blind_zone: List[Tuple[str, str]] = []
    dirty_hashes = {_canon(h) or h for h in dirty_hashes_raw}
    dirty_only_hashes = dirty_hashes - all_assigned_hashes
    phantom_only_resource_indices: Set[int] = set()
    phantom_suppressed: List[Tuple[str, str]] = []
    for index, (h, _res) in enumerate(textures):
        if not _allowed_texture(h):
            continue
        canon = _canon(h) or h
        if canon in all_assigned_hashes and canon not in live_fallback:
            covered_resource_indices.add(index)
        elif canon in component_hash_fallbacks:
            pass
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
        component_hash_fallbacks={
            h: list(entries)
            for h, entries in component_hash_fallbacks.items()
        },
        branch_contract=branch_contract,
        restore_contract=restore_contract,
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
            'suppressed_weak_branches': suppressed_weak_branches,
            'suppressed_covered_branches': suppressed_covered_branches,
        },
    )
