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
#     unique at some (component, slot)) else FORK LATCHES: at a (component,
#     MAIN slot) where the forms' material pairs bind disjoint KEPT content
#     (the per-slot content fork of a form switch), each side's texture
#     latches its form — checked INLINE inside the component's own command
#     list at that exact slot, so unrelated draws binding the texture (or
#     dump contamination from transition frames) can never flip the form.
#     Detection-only: a stale latch degrades to the last/default form, the
#     slot rebinding itself never depends on texture hashes.
#   * Residency dedup: two hashes at the same (component, slot) with the same
#     format but different sizes, of which the author kept exactly ONE file,
#     are streaming residency levels of one texture (per-mip hash churn), not
#     a form difference — they collapse to the kept one before any other
#     derivation.
#   * Non-material pairs (eye/face/screen-space overlays, whose slot content
#     drifts per scene pipeline) condition on their FULL recorded format
#     signature (aux slots included) — specific enough to need no hash keys
#     and residency-invariant by construction.
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
    # comp -> probe command list name, injected BEFORE the trigger anchor.
    probe_list_names: Dict[int, str] = field(default_factory=dict)
    # ini variables the transform must declare global in [Constants].
    extra_globals: List[str] = field(default_factory=list)
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
               component_ranges: Optional[Dict[int, Tuple[int, int]]] = None,
               lod_ranges: Optional[Dict[int, Dict[int, Tuple[int, int]]]] = None) -> SlotPlan:
    """textures: (texture hash, resource section name) in template order.
    texture_info: hash -> format/size of the ORIGINAL game textures.
    component_ranges: comp_id -> (match_first_index, match_index_count) of the
    rendered ini, used to scope the fuzzy format tag sections XQFA-style.
    lod_ranges: lod level -> comp_id -> index range of the LOD object draws
    (from the velo lods metadata); LOD draws use the LOD component ranges, so
    each format tag section gets a twin per LOD level or the conditions go
    blind exactly at LOD distance."""
    warnings: List[str] = list(load_warnings or [])
    multi_form = len(forms) > 1
    mod_hashes = {h: res for h, res in textures}
    if not mod_hashes:
        raise SlotStyleDegrade('mod has no textures, nothing to do')

    # ----------------------------------------------- residency-variant dedup --
    # Exactly two hashes ever seen at one (component, slot), same format,
    # different size, exactly one kept by the author: streaming residency
    # levels of one texture (3.4 per-mip hash churn), not a form difference.
    # Collapse to the kept one BEFORE any other derivation (else they fake a
    # form fork / get form-gated / spawn bogus detection keys).
    slot_content: Dict[Tuple[int, int], Set[str]] = {}
    for _, form_data in forms:
        for comp_id, comp_pairs in form_data.items():
            for pair_map in comp_pairs.values():
                for slot, h in pair_map.items():
                    if h is not None:
                        slot_content.setdefault((comp_id, slot), set()).add(h)
    alias: Dict[str, str] = {}
    for (comp_id, slot), hashes in sorted(slot_content.items()):
        if len(hashes) != 2:
            continue
        a, b = sorted(hashes)
        ia, ib = texture_info.get(a), texture_info.get(b)
        if not ia or not ib or not ia.get('format'):
            continue
        if ia.get('format') != ib.get('format') or ia.get('width') == ib.get('width'):
            continue
        kept = [h for h in (a, b) if h in mod_hashes]
        if len(kept) != 1:
            continue
        other = b if kept[0] == a else a
        alias.setdefault(other, kept[0])
    if alias:
        for _, form_data in forms:
            for comp_pairs in form_data.values():
                for pair_map in comp_pairs.values():
                    for slot, h in list(pair_map.items()):
                        if h in alias:
                            pair_map[slot] = alias[h]
        for variant, canon in sorted(alias.items()):
            warnings.append(
                f'texture {variant} treated as a residency level of {canon} '
                f'(same slot/format, different size, only one kept)')

    # ------------------------------------------------ coverage / blind zone --
    seen_hashes: Set[str] = set()
    for _, form_data in forms:
        for comp_pairs in form_data.values():
            for pair_map in comp_pairs.values():
                for h in pair_map.values():
                    if h is not None:
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
    # Fork latches: at a (component, MAIN slot) where the forms' material
    # pairs bind disjoint KEPT content (the per-slot content fork of a form
    # switch), each side's texture latches its form. Checked inline at that
    # exact slot during the component's own draws — dump transition
    # contamination and unrelated draws binding the texture cannot flip the
    # form (the root cause of the v3 form-flip bug, whose global hash-section
    # detection this replaces).
    form_markers_m1: Dict[int, List[Tuple[int, int, float]]] = {}  # form -> [(comp, slot, fi)]
    fork_latches: List[Tuple[int, int, str, int]] = []  # (comp, slot, hash, form)
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
                        # MAIN slots of material pairs only: aux-slot family
                        # differences between dumps are coverage noise, not a
                        # form signal (an aux-slot M1 marker re-latched the
                        # wrong form on every draw in the field).
                        if not is_mat or slot not in constants.MAIN_SLOTS:
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

        mat_content: Dict[int, Dict[Tuple[int, int], Set[str]]] = {}
        for form_id, (_, form_data) in enumerate(forms, start=1):
            bucket = mat_content.setdefault(form_id, {})
            for comp_id, comp_pairs in form_data.items():
                for pair_map in comp_pairs.values():
                    if not _is_material_pair(pair_map, texture_info):
                        continue
                    for slot in constants.MAIN_SLOTS:
                        h = pair_map.get(slot)
                        if h is not None and h in mod_hashes:
                            bucket.setdefault((comp_id, slot), set()).add(h)
        fork_keys = set()
        for bucket in mat_content.values():
            fork_keys.update(bucket)
        for key in sorted(fork_keys):
            per_form = {f: bucket.get(key, set())
                        for f, bucket in mat_content.items()}
            nonempty = {f: s for f, s in per_form.items() if s}
            if len(nonempty) < 2:
                continue
            all_hashes = [h for s in nonempty.values() for h in s]
            if len(all_hashes) != len(set(all_hashes)):
                continue  # shared content at this slot: not a fork
            for form_id, hashes in sorted(nonempty.items()):
                for h in sorted(hashes):
                    fork_latches.append((key[0], key[1], h, form_id))
        if not fork_latches and not form_markers_m1:
            warnings.append(
                'no form fork derivable (no (component, slot) where the forms '
                'bind disjoint kept material content) - forms cannot be '
                'auto-detected at runtime')

    # ------------------------------------------------------ branch building --
    all_comp_ids = sorted({c for _, fd in forms for c in fd})
    component_branches: Dict[int, List[_Branch]] = {}
    # Every (pair, form) record of a component, assigned or not — unassigned
    # ones act as VANILLA GUARDS below: a branch whose signature an
    # unreplaced set also satisfies would bind mod content onto vanilla draws.
    comp_records: Dict[int, List[Tuple[dict, Dict[int, Optional[str]], bool]]] = {}
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
            # (the condition itself distinguishes the forms). Material pairs
            # condition on MAIN slots only (aux slots vary across pipeline
            # variants and would break the structural transition coverage);
            # non-material pairs (eye/face/screen-space overlays) condition
            # on their FULL recorded signature — specific enough to need no
            # hash keys and immune to look-alike pipeline draws.
            by_sig: Dict[tuple, List[int]] = {}
            for form_id, pair_map in per_form.items():
                if _is_material_pair(pair_map, texture_info):
                    sig_slots = set(constants.MAIN_SLOTS)
                else:
                    sig_slots = set(pair_map)
                sig = tuple(sorted(
                    (slot, key) for slot, h in pair_map.items()
                    if slot in sig_slots
                    and (key := _family_key(h, texture_info)) is not None))
                by_sig.setdefault(sig, []).append(form_id)
                comp_records.setdefault(comp_id, []).append(
                    (dict(sig), pair_map,
                     any(h in mod_hashes for h in pair_map.values() if h)))
            # When the pair's per-form assignments are COMPATIBLE (no slot
            # bound to different resources — identical content, or one form's
            # record merely sparser than another's, e.g. a residency-deduped
            # eye texture with per-dump coverage gaps), form gates add
            # nothing but fragility: every signature variant fires ungated
            # with the union assignment, binding what every form wants
            # anyway. Only a real content fork (same slot, different
            # resource, like a form-switched diffuse) keeps its gates.
            pair_assigns = {
                f: {slot: mod_hashes[h] for slot, h in pm.items()
                    if h is not None and h in mod_hashes}
                for f, pm in per_form.items()}
            union_assign: Dict[int, str] = {}
            compatible = True
            for a in pair_assigns.values():
                for slot, res in a.items():
                    if union_assign.get(slot, res) != res:
                        compatible = False
                        break
                    union_assign[slot] = res
                if not compatible:
                    break
            # Uniform ONLY when the pair covers EVERY form with a nonempty,
            # compatible assignment. A single-form pair (form-unique PS) or a
            # pair whose other form keeps nothing must stay form-gated:
            # dropping the gate runs both forms' branches at once (the form
            # lock), and the union would project one form's content onto the
            # other form's signature variant (the C6 scramble).
            pair_uniform = (compatible and len(pair_assigns) == len(forms)
                            and all(pair_assigns.values()))
            for sig, form_ids in by_sig.items():
                if not sig:
                    continue  # nothing testable: a vacuous condition would fire on every draw
                nonempty = {f: pair_assigns[f] for f in form_ids if pair_assigns[f]}
                if pair_uniform:
                    nonempty = {f: union_assign for f in form_ids}
                if not nonempty:
                    continue
                material = any(_is_material_pair(per_form[f], texture_info) for f in form_ids)
                distinct = {tuple(sorted(a.items())) for a in nonempty.values()}
                if pair_uniform or (len(nonempty) == len(forms) and len(distinct) == 1):
                    gates = {None: next(iter(nonempty.values()))}
                else:
                    gates = {f: nonempty[f] for f in sorted(nonempty)}
                for gate, assign in gates.items():
                    # ALL recorded slots (terms only read the signature slots;
                    # the extra entries feed OR-arms and content-key search)
                    cond_slots: Dict[int, Set[str]] = {}
                    for f in (form_ids if gate is None else [gate]):
                        for slot, h in per_form[f].items():
                            if h is not None:
                                cond_slots.setdefault(slot, set()).add(h)
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
    marks: Dict[str, int] = {}  # hash -> filter_index value

    def _ensure_mark(tex_hash: str) -> Optional[int]:
        if tex_hash in marks:
            return marks[tex_hash]
        value = constants.mark_value(tex_hash)
        if value in marks.values():
            warnings.append(f'mark value collision on {tex_hash}, mark dropped')
            return None
        marks[tex_hash] = value
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

    # ---------------------------------------------- vanilla-pair guards --
    # A branch whose signature an UNREPLACED pair of the same component also
    # satisfies (no recorded shared slot with a differing family) would bind
    # mod content onto vanilla draws (the C6 scramble: the shared chameleon
    # set's bare condition matched the component's own unkept material).
    # Such branches must be content-keyed on a kept original that differs by
    # hash from every collider, or be dropped.
    for comp_id, branches in component_branches.items():
        unassigned = [(sig, pm) for sig, pm, has in comp_records.get(comp_id, [])
                      if not has and sig]
        if not unassigned:
            continue
        dropped = []
        for branch in branches:
            if branch.mark_hash is not None:
                continue  # already content-keyed by cluster disambiguation
            bsig = dict(branch.signature)
            colliders = []
            for osig, opm in unassigned:
                shared = set(bsig) & set(osig)
                if shared and any(bsig[s] != osig[s] for s in shared):
                    continue  # mutually exclusive on a recorded shared slot
                colliders.append(opm)
            if not colliders:
                continue
            candidate = None
            for slot in sorted(branch.cond_slots):
                for h in sorted(branch.cond_slots[slot]):
                    if h not in mod_hashes:
                        continue
                    if all(opm.get(slot) != h for opm in colliders):
                        candidate = (slot, h)
                        break
                if candidate:
                    break
            if candidate is not None and _ensure_mark(candidate[1]) is not None:
                branch.mark_slot, branch.mark_hash = candidate
            else:
                dropped.append(branch)
                warnings.append(
                    f'component {comp_id}: a branch shares its format signature '
                    f'with an unreplaced texture set and no kept texture '
                    f'distinguishes them - branch dropped (those draws stay vanilla)')
        if dropped:
            component_branches[comp_id] = [b for b in branches
                                           if not any(b is d for d in dropped)]
    component_branches = {c: b for c, b in component_branches.items() if b}
    if not component_branches:
        raise SlotStyleDegrade('no component produced any slot assignment')

    # Fork latch keys join the same mark table (a hash can be both a latch
    # key and a disambiguator); entries whose mark collided are dropped.
    fork_latches = [(c, s, h, f) for c, s, h, f in fork_latches
                    if _ensure_mark(h) is not None]

    # -------------------------------------------------- probe collection --
    # Field evidence (v3.1): `ps-tN == <mark fi>` reads do NOT work while
    # fuzzy format sections cover the texture — but hash-section command
    # lists DO run on CheckTextureOverride. All exact per-texture
    # discrimination therefore goes through PROBES: the component's probe
    # command list (injected BEFORE the stock trigger) brackets its own
    # CheckTextureOverride calls with $latch_key, and the mark sections
    # react only to their own (component, slot) keys — latching $form_id or
    # raising a per-draw $hit flag the branch conditions read.
    # (comp, slot, section hash) -> list of ('latch', form) / ('hit', canon)
    probe_effects: Dict[Tuple[int, int, str], List[Tuple[str, object]]] = {}
    for comp_id, slot, h, form_id in fork_latches:
        probe_effects.setdefault((comp_id, slot, h), []).append(('latch', form_id))
    for comp_id, branches in component_branches.items():
        for branch in branches:
            if branch.mark_hash is not None and branch.mark_slot is not None:
                effects = probe_effects.setdefault(
                    (comp_id, branch.mark_slot, branch.mark_hash), [])
                if ('hit', branch.mark_hash) not in effects:
                    effects.append(('hit', branch.mark_hash))
    # Residency variants collapsed by the dedup react like their canonical
    # texture (e.g. both mip-level hashes of the eye texture raise one flag).
    variants_of: Dict[str, List[str]] = {}
    for variant, canon in alias.items():
        variants_of.setdefault(canon, []).append(variant)
    for (comp_id, slot, h), effects in list(probe_effects.items()):
        for variant in variants_of.get(h, ()):
            if _ensure_mark(variant) is not None:
                probe_effects.setdefault((comp_id, slot, variant),
                                         []).extend(effects)
    probe_slots: Dict[int, Set[int]] = {}
    probe_hits: Dict[int, Set[str]] = {}
    for (comp_id, slot, h), effects in probe_effects.items():
        probe_slots.setdefault(comp_id, set()).add(slot)
        for kind, payload in effects:
            if kind == 'hit':
                probe_hits.setdefault(comp_id, set()).add(payload)

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

    def _terms(branch: _Branch) -> List[str]:
        terms: List[str] = []
        for slot, key in branch.signature:
            if branch.mark_slot == slot and branch.mark_hash:
                continue  # the exact content key below covers this slot
            values: List[str] = [fi_text[key]]
            for h in sorted(branch.cond_slots.get(slot, ())):
                if h in marks:
                    values.append(str(marks[h]))
            used_families.setdefault(comp_id, set()).add(key)
            if len(values) == 1:
                terms.append(f'ps-t{slot} == {values[0]}')
            else:
                terms.append('(' + ' || '.join(f'ps-t{slot} == {v}' for v in values) + ')')
        if branch.mark_hash:
            # Exact content key: probe-driven flag, NOT an fi comparison
            # (mark fi reads lose to the fuzzy format tags in the field).
            # Works for aux-slot keys outside the signature too.
            terms.append(f'{constants.VAR_HIT.format(texture_hash=branch.mark_hash)} == 1')
        return terms

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

    effects_by_hash: Dict[str, List[Tuple[int, int, str, object]]] = {}
    for (e_comp, e_slot, e_hash), effects in probe_effects.items():
        for kind, payload in effects:
            effects_by_hash.setdefault(e_hash, []).append((e_comp, e_slot, kind, payload))
    if marks:
        out.append('')
        out.append('; -- Per-texture marks. filter_index = f(hash): deterministic, identical')
        out.append(';    across all velo mods. The command bodies react ONLY to their own')
        out.append(';    probe keys (set around our CheckTextureOverride calls), latching')
        out.append(';    the form or raising a per-draw flag for the branch conditions.')
        for h in sorted(marks):
            out.append('')
            out.append(f'[{constants.SEC_TEX_MARK.format(texture_hash=h)}]')
            out.append(f'hash = {h}')
            out.append('allow_duplicate_hash = true')
            out.append(f'match_priority = {constants.MARK_PRIORITY}')
            out.append(f'filter_index = {marks[h]}')
            for e_comp, e_slot, kind, payload in sorted(
                    effects_by_hash.get(h, ()), key=lambda e: (e[0], e[1], e[2], str(e[3]))):
                out.append(f'if {constants.VAR_LATCH_KEY} == {constants.probe_key(e_comp, e_slot)}')
                if kind == 'latch':
                    out.append(f'    {constants.VAR_FORM} = {payload}')
                else:
                    out.append(f'    {constants.VAR_HIT.format(texture_hash=payload)} = 1')
                out.append('endif')

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
            cond = ' && '.join(_terms(branch))
            if branch.form_gate is not None:
                cond += f' && {constants.VAR_FORM} == {branch.form_gate}'
            chunk.append(f'{"if" if first else "else if"} {cond}')
            chunk.extend(_render_assignments(branch.assign, '    '))
            first = False
            used_slots.update(branch.assign)
        chunk.append('endif')
        body_chunks.append('\n'.join(chunk))

    probe_list_names: Dict[int, str] = {}
    if probe_slots:
        out.append('')
        out.append('; -- Probe lists (run BEFORE the stock resource-override trigger):')
        out.append(';    bracket our own slot checks with the probe key so the mark')
        out.append(';    sections above know which (component, slot) is being checked')
        for comp_id in sorted(probe_slots):
            name = constants.CMDLIST_PROBE.format(component_id=comp_id)
            probe_list_names[comp_id] = name
            out.append('')
            out.append(f'[{name}]')
            for h in sorted(probe_hits.get(comp_id, ())):
                out.append(f'{constants.VAR_HIT.format(texture_hash=h)} = 0')
            for slot in sorted(probe_slots[comp_id]):
                out.append(f'{constants.VAR_LATCH_KEY} = {constants.probe_key(comp_id, slot)}')
                out.append(f'CheckTextureOverride = ps-t{slot}')
            out.append(f'{constants.VAR_LATCH_KEY} = 0')

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
    # all members of one family sharing the same filter_index). LOD object
    # draws use the LOD component index ranges, so every section gets a twin
    # per LOD level — without them the conditions go blind exactly at LOD
    # distance (field-proven: the LOD texture reversion of the v3 test mod).
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
        ranges = [(constants.SEC_FORMAT_TAG, None, crange)]
        for level, lranges in sorted((lod_ranges or {}).items()):
            if comp_id in lranges:
                ranges.append((constants.SEC_FORMAT_TAG_LOD, level, lranges[comp_id]))
        for key in sorted(used_families[comp_id]):
            for prefix in sorted(group_families.get(key, {})):
                name, text = group_families[key][prefix]
                for member in constants.same_prefix_formats(name):
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
    if probe_effects:
        extra_globals.append(constants.VAR_LATCH_KEY)
        all_hits = {payload for effects in probe_effects.values()
                    for kind, payload in effects if kind == 'hit'}
        extra_globals.extend(constants.VAR_HIT.format(texture_hash=h)
                             for h in sorted(all_hits))

    return SlotPlan(
        block_text='\n'.join(out),
        component_list_names=component_list_names,
        covered_resource_indices=covered_resource_indices,
        blind_zone=blind_zone,
        multi_form=multi_form,
        used_slots=sorted(used_slots),
        probe_list_names=probe_list_names,
        extra_globals=extra_globals,
        warnings=warnings,
        stats={
            'forms': len(forms),
            'components': len(component_branches),
            'branches': sum(len(b) for b in component_branches.values()),
            'conflicts': conflict_count,
            'marks': len(marks),
            'fork_latches': len(fork_latches),
            'probes': len(probe_effects),
            'format_sections': format_section_count,
            'covered_textures': len(covered_resource_indices),
            'blind_zone_textures': len(blind_zone),
        },
    )
