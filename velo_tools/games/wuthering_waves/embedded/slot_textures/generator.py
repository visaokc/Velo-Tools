# Slot-style texture layer generator v3 (pure python, no bpy / no _wwmi_core
# imports — unit-testable headless). Zero shader-hash architecture, see
# docs/adr/0007.
#
# Input: per-form per-(component x shader-pair x slot) texture records (the
# "Component N" keys of ShaderTextureUsage.json plus its "extra_forms" key,
# both old flat and v3 nested rich schemas accepted), the export texture list
# and the component index ranges of the rendered ini. Output: the ini text
# block implementing the slot-style replacement layer plus the metadata
# transform.py needs to rewire the rendered mod.ini.
#
# Replacement model (XQFA-fork compatible, github.com/XQFAAAA/WWMI-Tools):
#
#   * Fuzzy TextureOverride sections tag every texture of a DXGI format
#     family with the deterministic 83.{ascii} filter_index. Tags are
#     residency-invariant (all streaming mip levels share the family), which
#     is what makes the whole scheme immune to texture streaming.
#   * Each component's draw command list branches on the tags of the bound
#     ps-t slots ("signature" = recorded MAIN_SLOTS format families) and
#     rebinds mod resources. Unknown pipeline variants (e.g. the menu
#     transition PS, present in no dump) bind the same per-slot layout as the
#     steady state, so the conditions cover them structurally — no
#     ShaderOverride / ShaderRegex / shader hash anywhere.
#   * Same-signature conflicts (two texture sets whose recorded formats are
#     identical, e.g. an own-material vs a shared-overlay set on one
#     component) are resolved by marking ONE replaced texture per non-
#     preferred set with a deterministic per-texture filter_index and testing
#     it exactly; when no mark is resident (far camera, stale hash after a
#     game update) the chain falls back to the component-local set — stable
#     wrong-at-worst, never flickering.
#   * Multi-form characters get a persistent $form_id driven by a marker
#     ladder: M1 format markers (zero-hash, when one form has a format family
#     unique at some (component, slot)) else M2 texture-hash markers (every
#     form-unique replaced texture sets $form_id on bind; detection-only —
#     a stale marker degrades to the default form, the slot rebinding itself
#     never depends on texture hashes).
#
# Marked textures read back their mark value instead of the family tag, so
# every condition term whose slot can carry a marked texture is emitted as an
# OR of (family tag, mark values) — correct under either hash-vs-fuzzy
# precedence a 3DMigoto build implements.

import json
import re
import struct

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import constants


class SlotStyleDegrade(Exception):
    """Raised when slot-style generation cannot proceed safely; the export
    hook falls back to the stock hash-style output and reports why."""


# comp_id -> ps_hash -> slot -> texture hash (None = conflicting multi-state
# binding seen for that slot; generator must not assign it).
FormData = Dict[int, Dict[str, Dict[int, Optional[str]]]]
# texture hash -> {'format': canonical DXGI name or '', 'width', 'height'}
TextureInfo = Dict[str, dict]

_PS_RE = re.compile(r'ps=([0-9a-f]{16})')
_VS_KEY_RE = re.compile(r'^vs=[0-9a-f?]+$')
_COMP_RE = re.compile(r'component\s*(\d+)\s*$', re.I)
_SLOT_RE = re.compile(r'^ps-t(\d+)$')

# Top-level keys of ShaderTextureUsage.json that are not component maps.
_RESERVED_KEYS = {constants.EXTRA_FORMS_KEY, 'version'}


def _f32(value: float) -> float:
    """float32 round-trip — 3DMigoto ini expressions evaluate in 32-bit, so
    family keys must compare at that precision (long format prefixes like
    R8G8B8A8 vs R8G8 genuinely collide there)."""
    return struct.unpack('<f', struct.pack('<f', value))[0]


# ---------------------------------------------------------------- loading --

def _ingest_slot(pair_out: Dict[int, Optional[str]], slot: int,
                 tex_hash: Optional[str]):
    if slot in pair_out and pair_out[slot] != tex_hash:
        # Same (component, ps, slot) seen with different content
        # (multi-state variant / vs-merge conflict): fail-safe.
        pair_out[slot] = None
    else:
        pair_out[slot] = tex_hash


def normalize_usage(raw: dict, source: str, warnings: List[str],
                    texture_info: Optional[TextureInfo] = None) -> FormData:
    """Converts one ShaderTextureUsage-shaped dict into FormData. Accepts both
    the old flat schema ("vs=..-ps=.." -> slot -> hash string) and the v3
    nested rich schema ("vs=.." -> "ps=.." -> slot -> record); rich records
    also feed texture_info (hash -> format/size)."""
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
                # v3 nested: vs key -> {ps key -> {slot -> record}}
                for ps_key, slots in value.items():
                    ps_found = _PS_RE.search(ps_key)
                    if not ps_found:
                        warnings.append(f'{source}: pair "{pair_key}/{ps_key}" has no ps hash, skipped')
                        continue
                    pair_out = comp_out.setdefault(ps_found.group(1), {})
                    for slot_name, record in (slots or {}).items():
                        slot_found = _SLOT_RE.match(slot_name)
                        if not slot_found or not isinstance(record, dict):
                            continue
                        tex_hash = record.get('hash')
                        if not isinstance(tex_hash, str):
                            tex_hash = None
                        elif texture_info is not None and record.get('format'):
                            texture_info.setdefault(tex_hash, {
                                'format': record.get('format') or '',
                                'width': record.get('width') or 0,
                                'height': record.get('height') or 0,
                            })
                        _ingest_slot(pair_out, int(slot_found.group(1)), tex_hash)
                continue
            # old flat: "vs=..-ps=.." -> {slot -> hash string}
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


def load_forms(object_source_folder: Path) -> Tuple[List[Tuple[str, FormData]], TextureInfo, List[str]]:
    """Loads base + extra form maps from the single ShaderTextureUsage.json.
    Returns (forms, texture_info, warnings); forms[0] is always the base
    extraction. texture_info is empty for old-schema files (the caller may
    fall back to reading the model-folder DDS files)."""
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

    forms: List[Tuple[str, FormData]] = [
        ('base', normalize_usage(base_raw, 'base', warnings, texture_info))]

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
        forms.append((label, normalize_usage(entry.get('components'), label,
                                             warnings, texture_info)))

    if not any(form_data for _, form_data in forms):
        raise SlotStyleDegrade('no usable shader/texture usage data found')
    return forms, texture_info, warnings


# -------------------------------------------------------------------- plan --

@dataclass
class SlotPlan:
    block_text: str
    component_list_names: Dict[int, str]
    covered_resource_indices: Set[int]
    blind_zone: List[Tuple[str, str]]  # (texture hash, kept stock section name)
    multi_form: bool
    used_slots: List[int]
    warnings: List[str] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)


@dataclass(eq=False)  # identity semantics: branches are mutated and filtered by id
class _Branch:
    # condition slots -> ORIGINAL texture hashes that can be bound there
    # (union over this branch's forms); terms are rendered from these.
    cond_slots: Dict[int, Set[str]]
    signature: tuple
    assign: Dict[int, str]            # slot -> mod resource name
    form_gate: Optional[int]          # None = all forms / single-form mod
    ps_set: Set[str]                  # source pairs (internal bookkeeping only)
    local: bool                       # every source PS exclusive to this component
    material: bool
    preferred: bool = False           # cluster winner (bare-condition fallback)
    mark_slot: Optional[int] = None   # disambiguator slot (exact-mark branch)
    mark_hash: Optional[str] = None


def _is_material_pair(pair_map: Dict[int, Optional[str]],
                      texture_info: TextureInfo) -> bool:
    """Structural slot-set fingerprint: material pairs bind ALL main slots.
    When a slot's descriptor is known, it must look like a character texture
    (square); unknown descriptors never block."""
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


def _render_assignments(assign: Dict[int, str], indent: str) -> List[str]:
    lines = []
    for slot in sorted(assign):
        backup = constants.RES_BACKUP.format(slot=slot)
        lines.append(f'{indent}{backup} = ref ps-t{slot}')
        lines.append(f'{indent}ps-t{slot} = ref {assign[slot]}')
    return lines


def build_plan(forms: List[Tuple[str, FormData]],
               textures: List[Tuple[str, str]],
               texture_info: TextureInfo,
               load_warnings: Optional[List[str]] = None,
               component_ranges: Optional[Dict[int, Tuple[int, int]]] = None) -> SlotPlan:
    """textures: (texture hash, resource section name) in template order.
    texture_info: hash -> format/size of the ORIGINAL game textures.
    component_ranges: comp_id -> (match_first_index, match_index_count) of the
    rendered ini, used to scope the fuzzy format tag sections XQFA-style."""
    warnings: List[str] = list(load_warnings or [])
    multi_form = len(forms) > 1
    mod_hashes = {h: res for h, res in textures}
    if not mod_hashes:
        raise SlotStyleDegrade('mod has no textures, nothing to do')

    # ------------------------------------------------ coverage / blind zone --
    seen_hashes: Set[str] = set()
    form_hashes: Dict[int, Set[str]] = {}
    for form_id, (_, form_data) in enumerate(forms, start=1):
        bucket = form_hashes.setdefault(form_id, set())
        for comp_pairs in form_data.values():
            for pair_map in comp_pairs.values():
                for h in pair_map.values():
                    if h is not None:
                        bucket.add(h)
                        seen_hashes.add(h)
    covered_resource_indices: Set[int] = set()
    blind_zone: List[Tuple[str, str]] = []
    for index, (h, _res) in enumerate(textures):
        if h in seen_hashes:
            covered_resource_indices.add(index)
        else:
            blind_zone.append((h, f'TextureOverrideTexture{index}'))
    if not covered_resource_indices:
        raise SlotStyleDegrade(
            'none of the mod textures appear in the shader/texture usage maps '
            '(stale extraction data?)')
    missing_formats = sorted(
        h for h in seen_hashes & set(mod_hashes) if _family_key(h, texture_info) is None)
    if missing_formats:
        warnings.append(
            'no DXGI format known for replaced texture(s) '
            f'{", ".join(missing_formats)} - they cannot join the combination '
            f'conditions (re-extract with a current build to record formats)')

    # ---------------------------------------------------- per-PS bookkeeping --
    ps_components: Dict[str, Set[int]] = {}
    for _, form_data in forms:
        for comp_id, comp_pairs in form_data.items():
            for ps in comp_pairs:
                ps_components.setdefault(ps, set()).add(comp_id)

    # ------------------------------------------------------- form detection --
    # M1: a format family unique to one form at some (component, slot) on
    # material-class pairs, with the other forms having recorded data at the
    # same (component, slot) (two-sided evidence; absence proves nothing).
    # M2: every form-unique REPLACED texture hash becomes a marker section.
    form_markers_m1: Dict[int, List[Tuple[int, int, float]]] = {}  # form -> [(comp, slot, fi)]
    form_markers_m2: Dict[int, List[str]] = {}
    if multi_form:
        slot_families: Dict[Tuple[int, int], Dict[int, Set[float]]] = {}
        slot_presence: Dict[Tuple[int, int], Set[int]] = {}
        for form_id, (_, form_data) in enumerate(forms, start=1):
            for comp_id, comp_pairs in form_data.items():
                for pair_map in comp_pairs.values():
                    is_mat = _is_material_pair(pair_map, texture_info)
                    for slot, h in pair_map.items():
                        if h is None:
                            continue
                        slot_presence.setdefault((comp_id, slot), set()).add(form_id)
                        if not is_mat:
                            continue
                        key = _family_key(h, texture_info)
                        if key is not None:
                            slot_families.setdefault((comp_id, slot), {}) \
                                .setdefault(form_id, set()).add(key)
        all_form_ids = set(range(1, len(forms) + 1))
        for (comp_id, slot), per_form in sorted(slot_families.items()):
            if slot_presence.get((comp_id, slot)) != all_form_ids:
                continue
            for form_id, keys in per_form.items():
                others = set().union(*(v for f, v in per_form.items() if f != form_id)) \
                    if len(per_form) > 1 else set()
                for key in sorted(keys - others):
                    if len(per_form) == len(forms):
                        form_markers_m1.setdefault(form_id, []).append((comp_id, slot, key))

        for form_id in sorted(form_hashes):
            others = set().union(*(v for f, v in form_hashes.items() if f != form_id))
            unique_replaced = sorted((form_hashes[form_id] - others) & set(mod_hashes))
            # Small (residency-stable) textures first: they are the markers
            # most likely to be bound verbatim at any camera distance.
            unique_replaced.sort(key=lambda h: (
                (texture_info.get(h) or {}).get('width', 1 << 20) *
                (texture_info.get(h) or {}).get('height', 1 << 20), h))
            form_markers_m2[form_id] = unique_replaced
            if not unique_replaced and not form_markers_m1.get(form_id):
                label = forms[form_id - 1][0]
                warnings.append(
                    f'form "{label}" has no detectable marker (no unique format, no '
                    f'unique replaced texture) - it cannot be auto-detected at runtime')

    # ------------------------------------------------------ branch building --
    all_comp_ids = sorted({c for _, fd in forms for c in fd})
    component_branches: Dict[int, List[_Branch]] = {}
    conflict_count = 0
    for comp_id in all_comp_ids:
        seeds: List[_Branch] = []
        ps_union = sorted({ps for _, fd in forms for ps in fd.get(comp_id, {})})
        for ps in ps_union:
            local = ps_components.get(ps) == {comp_id}
            per_form: Dict[int, Dict[int, Optional[str]]] = {}
            for form_id, (_, form_data) in enumerate(forms, start=1):
                pair_map = form_data.get(comp_id, {}).get(ps)
                if pair_map is not None:
                    per_form[form_id] = pair_map
            # Group this pair's forms by condition signature; identical
            # signatures share one branch (form-gated bodies when the
            # assignments diverge), distinct signatures separate naturally
            # (the condition itself distinguishes the forms).
            by_sig: Dict[tuple, List[int]] = {}
            for form_id, pair_map in per_form.items():
                sig = tuple(sorted(
                    (slot, key) for slot, h in pair_map.items()
                    if slot in constants.MAIN_SLOTS
                    and (key := _family_key(h, texture_info)) is not None))
                by_sig.setdefault(sig, []).append(form_id)
            for sig, form_ids in by_sig.items():
                if not sig:
                    continue  # nothing testable: a vacuous condition would fire on every draw
                assigns = {}
                for form_id in form_ids:
                    assigns[form_id] = {
                        slot: mod_hashes[h] for slot, h in per_form[form_id].items()
                        if h is not None and h in mod_hashes}
                nonempty = {f: a for f, a in assigns.items() if a}
                if not nonempty:
                    continue
                material = any(_is_material_pair(per_form[f], texture_info) for f in form_ids)
                distinct = {tuple(sorted(a.items())) for a in nonempty.values()}
                if len(nonempty) == len(forms) and len(distinct) == 1:
                    gates = {None: next(iter(nonempty.values()))}
                else:
                    gates = {f: nonempty[f] for f in sorted(nonempty)}
                for gate, assign in gates.items():
                    cond_slots = {
                        slot: {h for f in (form_ids if gate is None else [gate])
                               if (h := per_form[f].get(slot)) is not None}
                        for slot, _key in sig}
                    seeds.append(_Branch(cond_slots=cond_slots, signature=sig,
                                         assign=assign, form_gate=gate,
                                         ps_set={ps}, local=local, material=material))

        # Dedupe identical branches (same signature, gate and assignment from
        # different shader pairs, e.g. several overlay pairs of one component).
        merged: List[_Branch] = []
        for seed in seeds:
            for other in merged:
                if (other.signature == seed.signature
                        and other.form_gate == seed.form_gate
                        and other.assign == seed.assign):
                    other.ps_set |= seed.ps_set
                    other.local = other.local and seed.local
                    other.material = other.material or seed.material
                    for slot, hashes in seed.cond_slots.items():
                        other.cond_slots.setdefault(slot, set()).update(hashes)
                    break
            else:
                merged.append(seed)
        if merged:
            component_branches[comp_id] = merged

    if not component_branches:
        raise SlotStyleDegrade('no component produced any slot assignment')

    # --------------------------------------------------- conflict resolution --
    # Same (signature, form gate) with different assignments: keep the
    # component-local material set as the bare-condition fallback and require
    # an exact per-texture mark for every other member.
    marks: Dict[str, Dict] = {}  # hash -> {'value': int, 'form': Optional[int]}

    def _ensure_mark(tex_hash: str) -> Optional[int]:
        if tex_hash in marks:
            return marks[tex_hash]['value']
        value = constants.mark_value(tex_hash)
        if any(m['value'] == value for m in marks.values()):
            warnings.append(f'mark value collision on {tex_hash}, mark dropped')
            return None
        marks[tex_hash] = {'value': value, 'form': None}
        return value

    for comp_id, branches in component_branches.items():
        clusters: Dict[tuple, List[_Branch]] = {}
        for branch in branches:
            clusters.setdefault((branch.signature, branch.form_gate), []).append(branch)
        dropped: List[_Branch] = []
        for (sig, gate), members in clusters.items():
            if len(members) < 2:
                continue
            # First pass: merge members whose assignments are COMPATIBLE
            # (no slot bound to different resources — e.g. two overlay pairs
            # touching complementary aux slots). Assigning a slot some member's
            # shader never reads is harmless (restored right after the draw);
            # only true same-slot contradictions need disambiguation.
            members.sort(key=lambda b: (not (b.local and b.material),
                                        not b.local, sorted(b.ps_set)))
            groups: List[_Branch] = []
            for member in members:
                for target in groups:
                    if all(target.assign.get(s, r) == r
                           for s, r in member.assign.items()):
                        target.assign.update(member.assign)
                        target.ps_set |= member.ps_set
                        target.local = target.local and member.local
                        target.material = target.material or member.material
                        for slot, hashes in member.cond_slots.items():
                            target.cond_slots.setdefault(slot, set()).update(hashes)
                        dropped.append(member)
                        break
                else:
                    groups.append(member)
            if len(groups) < 2:
                continue
            conflict_count += len(groups) - 1
            groups[0].preferred = True
            for member in groups[1:]:
                candidate = None
                for slot in sorted(member.cond_slots):
                    for h in sorted(member.cond_slots[slot],
                                    key=lambda h: ((texture_info.get(h) or {}).get('width', 1 << 20), h)):
                        if h not in mod_hashes:
                            continue
                        others = set()
                        for other in members:
                            if other is not member:
                                others |= other.cond_slots.get(slot, set())
                        if h not in others:
                            candidate = (slot, h)
                            break
                    if candidate:
                        break
                value = _ensure_mark(candidate[1]) if candidate else None
                if value is None:
                    # Indistinguishable at runtime: an identical bare condition
                    # would be dead elif chain noise — drop it, the preferred
                    # set claims those draws (stable wrong-at-worst).
                    warnings.append(
                        f'component {comp_id}: two texture sets share the same format '
                        f'signature and no replaced texture distinguishes them - the '
                        f'preferred set wins, the other set is shadowed')
                    dropped.append(member)
                    continue
                member.mark_slot, member.mark_hash = candidate
        if dropped:
            component_branches[comp_id] = [b for b in branches if b not in dropped]

    # M2 form markers join the same mark table (a hash can be both).
    for form_id, hashes in sorted(form_markers_m2.items()):
        for h in hashes:
            value = _ensure_mark(h)
            if value is not None:
                marks[h]['form'] = form_id

    # ------------------------------------------------------------ emission --
    used_slots: Set[int] = set()
    used_families: Dict[int, Set[float]] = {}  # comp -> family fi keys used in conditions

    # The float32 round-trip is for COMPARISON only (signature keys); the
    # emitted text must carry the exact XQFA-contract value (str of the
    # original double, e.g. 83.8256 - not the f32-roundtrip 83.82559967...).
    # Several XQFA prefix families collapse to ONE float32 value (BC1/BC2/BC3
    # and BC4/BC5/BC6H/BC7 are runtime-indistinguishable groups): conditions
    # may use any member text, but tag sections must be emitted for EVERY
    # family in a group or its other members' textures would stay untagged.
    fi_text: Dict[float, str] = {}
    group_families: Dict[float, Dict[str, Tuple[str, str]]] = {}
    for info in texture_info.values():
        fmt = info.get('format')
        if fmt:
            fi = constants.format_filter_index(fmt)
            key = _f32(fi)
            fi_text.setdefault(key, _fi_str(fi))
            group_families.setdefault(key, {}).setdefault(
                constants.format_prefix(fmt), (fmt, _fi_str(fi)))

    def _term(branch: _Branch, slot: int, key: float) -> str:
        values: List[str] = []
        if not (branch.mark_slot == slot and branch.mark_hash):
            values.append(fi_text[key])
            for h in sorted(branch.cond_slots.get(slot, ())):
                if h in marks:
                    values.append(str(marks[h]['value']))
            used_families.setdefault(comp_id, set()).add(key)
        else:
            values.append(str(marks[branch.mark_hash]['value']))
        if len(values) == 1:
            return f'ps-t{slot} == {values[0]}'
        return '(' + ' || '.join(f'ps-t{slot} == {v}' for v in values) + ')'

    out: List[str] = []
    form_sources = ', '.join(label for label, _ in forms)
    out.append('')
    out.append('; ============================================================')
    out.append('; Slot-style texture layer (generated - do not edit by hand)')
    out.append(f'; Forms: {form_sources}')
    out.append('; Textures are rebound by slot inside the component draw scope, keyed')
    out.append('; on DXGI format-family tags (streaming/residency-invariant) - no')
    out.append('; shader hashes anywhere; texture hashes only mark form detection /')
    out.append('; set disambiguation and degrade gracefully when stale.')
    out.append('; ============================================================')

    if marks:
        out.append('')
        out.append('; -- Per-texture marks (deterministic filter_index = f(hash): identical')
        out.append(';    across all velo mods, duplicates coexist harmlessly)')
        for h in sorted(marks):
            entry = marks[h]
            out.append('')
            out.append(f'[{constants.SEC_TEX_MARK.format(texture_hash=h)}]')
            out.append(f'hash = {h}')
            out.append('allow_duplicate_hash = true')
            out.append(f'match_priority = {constants.MARK_PRIORITY}')
            out.append(f'filter_index = {entry["value"]}')
            if entry['form'] is not None:
                out.append(f'{constants.VAR_FORM} = {entry["form"]}')

    component_list_names: Dict[int, str] = {}
    body_chunks: List[str] = []
    for comp_id, branches in sorted(component_branches.items()):
        name = constants.CMDLIST_SET_TEXTURES.format(component_id=comp_id)
        component_list_names[comp_id] = name
        chunk: List[str] = ['', f'[{name}]']
        for form_id, markers in sorted(form_markers_m1.items()):
            for marker_comp, slot, key in markers:
                if marker_comp != comp_id:
                    continue
                used_families.setdefault(comp_id, set()).add(key)
                chunk.append(f'if ps-t{slot} == {fi_text[key]}')
                chunk.append(f'    {constants.VAR_FORM} = {form_id}')
                chunk.append('endif')
        # Exact-mark branches first, then bare-condition branches by
        # specificity (slot count desc, cluster winners before shadowed
        # leftovers) - subset conditions go last so a superset draw is
        # claimed by its own branch.
        ordered = sorted(
            branches,
            key=lambda b: (b.mark_hash is None, -len(b.signature),
                           not b.preferred, b.form_gate or 0, b.signature,
                           tuple(sorted(b.assign.items()))))
        first = True
        for branch in ordered:
            terms = [_term(branch, slot, key) for slot, key in branch.signature]
            cond = ' && '.join(terms)
            if branch.form_gate is not None:
                cond += f' && {constants.VAR_FORM} == {branch.form_gate}'
            chunk.append(f'{"if" if first else "else if"} {cond}')
            chunk.extend(_render_assignments(branch.assign, '    '))
            first = False
            used_slots.update(branch.assign)
        chunk.append('endif')
        body_chunks.append('\n'.join(chunk))

    out.append('')
    out.append('; -- Per-draw backup slots (restored right after the component draws)')
    for slot in sorted(used_slots):
        out.append(f'[{constants.RES_BACKUP.format(slot=slot)}]')

    out.extend(body_chunks)

    out.append('')
    out.append(f'[{constants.CMDLIST_RESTORE}]')
    out.append('; Rebind original game textures after our draws so later draws that')
    out.append('; inherit pipeline state never see mod textures.')
    for slot in sorted(used_slots):
        backup = constants.RES_BACKUP.format(slot=slot)
        out.append(f'if {backup} !== null')
        out.append(f'    ps-t{slot} = ref {backup}')
        out.append(f'    {backup} = null')
        out.append('endif')

    # Fuzzy format-family tag sections (XQFA-compatible: one section per
    # family member format per component, scoped to the component index range,
    # all members of one family sharing the same filter_index).
    format_section_count = 0
    out.append('')
    out.append('; -- Format-family tags (filter_index identical to XQFA-fork exports:')
    out.append(';    83.{ascii of prefix letters}; equal values across mods never fight)')
    for comp_id in sorted(used_families):
        crange = (component_ranges or {}).get(comp_id)
        if crange is None:
            raise SlotStyleDegrade(
                f'component {comp_id} index range unknown - cannot emit its '
                f'format tag sections')
        for key in sorted(used_families[comp_id]):
            for prefix in sorted(group_families.get(key, {})):
                name, text = group_families[key][prefix]
                for member in constants.same_prefix_formats(name):
                    out.append('')
                    out.append(f'[{constants.SEC_FORMAT_TAG.format(component_id=comp_id, format_name=member)}]')
                    out.append(f'match_first_index = {crange[0]}')
                    out.append(f'match_index_count = {crange[1]}')
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
        multi_form=multi_form,
        used_slots=sorted(used_slots),
        warnings=warnings,
        stats={
            'forms': len(forms),
            'components': len(component_branches),
            'branches': sum(len(b) for b in component_branches.values()),
            'conflicts': conflict_count,
            'marks': len(marks),
            'form_markers': sum(1 for m in marks.values() if m['form'] is not None),
            'format_sections': format_section_count,
            'covered_textures': len(covered_resource_indices),
            'blind_zone_textures': len(blind_zone),
        },
    )
