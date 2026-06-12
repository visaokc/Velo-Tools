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
#     component) and multi-state seats (shared outline/shadow passes binding
#     a different part per draw) need per-texture runtime identity, which
#     the field WWMI fork does not provide (hash-matched sections never join
#     the per-draw command-list path - ADR 0007 rev 10): such textures are
#     routed to LIVE FALLBACK - the slot layer leaves them alone, their
#     stock hash sections keep replacing them (family-expanded by callers
#     with a hash family table), and plan.live_fallback reports them.
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
# Per-texture mark sections survive ONLY for form detection (fork latches):
# they merely degrade there (anchors/M1 markers carry the switch when the
# latch ladder is inert on a loader build). Condition terms whose slot can
# carry a latch-marked texture are emitted as an OR of (family tag, mark
# value) — correct under either hash-vs-fuzzy precedence.

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
                    texture_info: Optional[TextureInfo] = None,
                    freshness: Optional[Dict[Tuple[int, str, int], bool]] = None) -> FormData:
    """Converts one ShaderTextureUsage-shaped dict into FormData. Accepts both
    the old flat schema ("vs=..-ps=.." -> slot -> hash string) and the v3
    nested rich schema ("vs=.." -> "ps=.." -> slot -> record); rich records
    also feed texture_info (hash -> format/size).

    freshness (optional out-dict, ADR 0007 rev 12): filled with
    (comp_id, ps_hash, slot) -> bool from v4 ``fresh`` record flags - the
    freshness of the SEATED content, OR-aggregated across the vs-merge. When
    flags are present, cross-vs seat conflicts arbitrate fresh-beats-stale
    before the legacy conflict-None fail-safe."""
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
                            # Harvested residency-level hashes of this texture
                            # (merged from extra dumps): extra latch keys.
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
                            # Cross-vs seat conflict with freshness evidence:
                            # fresh beats stale, stale never demotes fresh.
                            seat_key = (comp_id, ps_hash, slot_id)
                            seated = freshness.get(seat_key)
                            if rec_fresh and seated is False:
                                pair_out[slot_id] = tex_hash
                                freshness[seat_key] = True
                            elif not rec_fresh and seated is True:
                                pass  # keep the fresh seat
                            else:
                                pair_out[slot_id] = None  # unarbitrable conflict
                            continue
                        _ingest_slot(pair_out, slot_id, tex_hash)
                        if (freshness is not None and rec_fresh is not None
                                and pair_out.get(slot_id) == tex_hash):
                            seat_key = (comp_id, ps_hash, slot_id)
                            freshness[seat_key] = bool(
                                freshness.get(seat_key)) or rec_fresh
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


def load_forms(object_source_folder: Path,
               freshness_out: Optional[List[Dict[Tuple[int, str, int], bool]]] = None,
               ) -> Tuple[List[Tuple[str, FormData]], TextureInfo, List[str]]:
    """Loads base + extra form maps from the single ShaderTextureUsage.json.
    Returns (forms, texture_info, warnings); forms[0] is always the base
    extraction. texture_info is empty for old-schema files (the caller may
    fall back to reading the model-folder DDS files).

    freshness_out (optional, ADR 0007 rev 12): receives one per-form seat
    freshness dict (see normalize_usage) aligned with the returned forms, for
    forwarding into build_plan(freshness=...)."""
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
        ('base', normalize_usage(base_raw, 'base', warnings, texture_info,
                                 base_fresh))]
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
                                             warnings, texture_info,
                                             entry_fresh)))
        if freshness_out is not None:
            freshness_out.append(entry_fresh)

    if freshness_out is not None and not any(freshness_out):
        warnings.append(
            'ShaderTextureUsage.json carries no binding-freshness flags - '
            'stale-inherited records cannot be filtered out (re-extract the '
            'object with a current Velo Tools build)')

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
    # Anchor-watchdog block appended to the stock [Present] section (empty
    # unless the two-forms/one-anchored-form topology holds).
    watchdog_lines: List[str] = field(default_factory=list)
    # [Constants] default for $form_id: the unanchored form when the
    # watchdog is active (instant-correct on load either way), else 1.
    default_form_id: int = 1
    # Mod textures the slot layer cannot serve safely with format conditions
    # alone (multi-state seats, indistinguishable same-signature sets):
    # canonical mod hash -> human-readable reason. The caller must keep their
    # stock hash sections live (and may family-expand them); their resource
    # indices are excluded from covered_resource_indices. Callers that can
    # re-run the plan should feed these back via live_seed until the set
    # stops growing (the exclusion of a live texture from conditions can
    # surface new conflicts).
    live_fallback: Dict[str, str] = field(default_factory=dict)
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


# NOTE: assignment rendering lives inside build_plan (_guarded_assignments):
# non-signature slots self-guard on their recorded format-family tag, which
# needs the plan's texture_info / marks / fi_text context.


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
               freshness: Optional[List[Dict[Tuple[int, str, int], bool]]] = None) -> SlotPlan:
    """textures: (texture hash, resource section name) in template order.
    texture_info: hash -> format/size of the ORIGINAL game textures.
    component_ranges: comp_id -> (match_first_index, match_index_count) of the
    rendered ini, used to scope the fuzzy format tag sections XQFA-style.
    lod_ranges: lod level -> comp_id -> index range of the LOD object draws
    (from the velo lods metadata); LOD draws use the LOD component ranges, so
    each format tag section gets a twin per LOD level or the conditions go
    blind exactly at LOD distance.
    multi_state_seats: (comp_id, slot) -> ORIGINAL hashes a multi-state pass
    binds there across frames/draws (shared outline/shadow shaders draw a
    different part per draw). Format conditions cannot serve such seats, and
    the probe/content-key machinery of rev 9 is field-dead (hash-matched
    sections never join the per-draw command-list path on the user's WWMI
    fork: D1/D2 dye diagnostics, ADR 0007 rev 10) - every REPLACED candidate
    is routed to LIVE FALLBACK instead: the slot layer leaves it alone, the
    caller keeps its stock hash section live (family-expanded when a hash
    family table is available).
    live_seed: canonical mod hashes the CALLER already routed to live
    fallback (unstable seats, prior-iteration discoveries, sandbox
    offenders). They are excluded from every condition/assignment up front;
    same-signature conflicts discovered DURING this pass are returned in
    plan.live_fallback but not re-applied within the pass - re-run with the
    grown seed until the set stops growing.
    trusted_hashes: hashes whose slot content is SESSION-STABLE (the
    object's own textures: bound exclusively during its draws across the
    caller's dumps). When provided, condition signatures only use slots
    holding a replaced or trusted texture - scene inputs (shadow masks,
    lightmaps) recorded at aux slots change between sessions and a single
    stale term kills the whole branch in game (field evidence, the leg-pass
    bug: both dumps replayed green while the live session diverged on a
    scene slot). None = legacy behavior (every recorded slot qualifies).
    freshness: per-form seat freshness maps aligned with forms (from
    load_forms(freshness_out=...), ADR 0007 rev 12). Evidence/service split:
    stale-inherited seats never feed signatures, conflict clustering, fork
    latches, M1 markers or residency-dedup derivation; stale REPLACED seats
    degrade to per-slot self-guarded service assignments; pairs with zero
    fresh seats are phantoms (another draw's leftover state) and produce no
    branch at all. None or empty maps = legacy behavior."""
    warnings: List[str] = list(load_warnings or [])
    multi_form = len(forms) > 1
    mod_hashes = {h: res for h, res in textures}
    if not mod_hashes:
        raise SlotStyleDegrade('mod has no textures, nothing to do')

    # ------------------------------------------- freshness evidence split --
    # (ADR 0007 rev 12) Frame-analysis dumps record EVERY bound slot per
    # draw, including stale state inherited from earlier draws; only log-
    # evidenced fresh binds are material evidence. service_seats keeps the
    # stale REPLACED seats: they stay assigned (a shader may still sample an
    # inherited binding - hash-style mods covered that by replacing
    # globally) but only through their per-slot self-guards, never as
    # signature/conflict/latch/M1 evidence.
    service_seats: Set[Tuple[int, int, str, int]] = set()  # (form, comp, ps, slot)
    phantom_pairs = 0
    if freshness is not None and any(freshness):
        variant_canon: Dict[str, str] = {}
        for canon, info in texture_info.items():
            for variant in info.get('variants', ()):
                variant_canon.setdefault(variant, canon)
        for form_id, (form_label, form_data) in enumerate(forms, start=1):
            form_fresh = (freshness[form_id - 1]
                          if form_id - 1 < len(freshness) else None) or {}
            if not form_fresh:
                continue  # no flags for this form: legacy, keep everything
            for comp_id, comp_pairs in form_data.items():
                for ps in list(comp_pairs):
                    pair_map = comp_pairs[ps]
                    flags = {slot: form_fresh.get((comp_id, ps, slot))
                             for slot in pair_map}
                    known = [f for f in flags.values() if f is not None]
                    if not known:
                        continue  # unflagged pair: legacy, keep as-is
                    if not any(known):
                        # Phantom pair: every evidenced seat is stale-
                        # inherited - the pair is another draw's leftover
                        # state, not this component's material.
                        del comp_pairs[ps]
                        phantom_pairs += 1
                        warnings.append(
                            f'form "{form_label}" component {comp_id}: pair '
                            f'ps={ps} carries only stale-inherited bindings - '
                            f'phantom pair dropped (no branch, no evidence)')
                        continue
                    for slot in list(pair_map):
                        if flags.get(slot) is not False:
                            continue
                        h = pair_map[slot]
                        replaced = (h is not None
                                    and (h in mod_hashes
                                         or variant_canon.get(h) in mod_hashes))
                        if replaced:
                            service_seats.add((form_id, comp_id, ps, slot))
                        else:
                            del pair_map[slot]  # stale unreplaced: not evidence

    # ----------------------------------------------- residency-variant dedup --
    # Hashes seen at one (component, slot), same format, different sizes,
    # exactly one kept by the author: streaming residency levels of one
    # texture (3.4 per-mip hash churn), not a form difference. Collapse to
    # the kept one BEFORE any other derivation (else they fake a form fork /
    # get form-gated / spawn bogus detection keys).
    #
    # CROSS-FORM ONLY (field-proven constraint): a streamed texture shows
    # exactly ONE residency level within a captured frame, so two group
    # members seated in the SAME form's maps are different textures that
    # happen to share (component, slot, format) across pipeline passes - a
    # 512 aux mask living beside the 2048 diffuse, not its mip ladder.
    # Collapsing those binds the kept replacement onto the wrong passes
    # (visible wrong-texture bug on a converted third-party mod). Each form
    # may therefore contribute at most one member of a residency group; the
    # explicit "variants" record key (cross-dump harvest) is unaffected.
    slot_content: Dict[Tuple[int, int], Set[str]] = {}
    slot_form_seats: Dict[Tuple[int, int], Dict[str, Set[int]]] = {}
    for form_id, (_, form_data) in enumerate(forms, start=1):
        for comp_id, comp_pairs in form_data.items():
            for ps, pair_map in comp_pairs.items():
                for slot, h in pair_map.items():
                    # Service (stale-inherited) seats are not evidence: they
                    # must not drive the residency-group derivation (alias
                    # APPLICATION below still rewrites them).
                    if h is not None and (form_id, comp_id, ps, slot) not in service_seats:
                        slot_content.setdefault((comp_id, slot), set()).add(h)
                        slot_form_seats.setdefault((comp_id, slot), {}) \
                            .setdefault(h, set()).add(form_id)
    alias: Dict[str, str] = {}
    # Harvested residency variants (the "variants" record key) are aliases by
    # declaration: the merge step only records them after a same-slot, same-
    # format, different-size sighting in another dump.
    for canon, info in texture_info.items():
        for variant in info.get('variants', ()):
            alias.setdefault(variant, canon)
    for (comp_id, slot), hashes in sorted(slot_content.items()):
        # Multi-level groups: n hashes at one (component, slot) sharing a
        # format with pairwise-distinct sizes, of which the author kept
        # exactly ONE - the streaming mip ladder of one texture.
        by_format: Dict[str, List[str]] = {}
        for h in sorted(hashes):
            info = texture_info.get(h)
            if info and info.get('format'):
                by_format.setdefault(info['format'], []).append(h)
        for fmt_name, group in by_format.items():
            if len(group) < 2:
                continue
            sizes = [texture_info[h].get('width') for h in group]
            if None in sizes or 0 in sizes or len(set(sizes)) != len(group):
                continue
            kept = [h for h in group if h in mod_hashes]
            if len(kept) != 1:
                continue
            # One residency level per form: any two members co-seated in the
            # same form's maps are different textures - no collapse.
            member_forms = slot_form_seats.get((comp_id, slot), {})
            forms_seen: Set[int] = set()
            same_form_overlap = False
            for h in group:
                seats = member_forms.get(h, set())
                if forms_seen & seats:
                    same_form_overlap = True
                    break
                forms_seen |= seats
            if same_form_overlap:
                continue
            for h in group:
                if h != kept[0]:
                    alias.setdefault(h, kept[0])
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

    # -------------------------------------------------------- live routing --
    # Textures the slot layer must NOT touch: their replacement stays with
    # the stock hash sections (the caller keeps them live and may expand
    # them across the hash family). Two sources: the caller's live_seed,
    # and every replaced multi-state candidate (exact per-texture runtime
    # identity is unavailable on the field loader - ADR 0007 rev 10).
    # At runtime a live-replaced texture is a file-backed custom resource
    # the fuzzy format sections do not tag, so its slot reads fi 0: every
    # condition term, OR-arm and self-guard referencing it must be skipped
    # or the chain goes blind exactly where the live section fired.
    live_fallback: Dict[str, str] = {}
    for h in sorted(live_seed or ()):
        canon = alias.get(h, h)
        if canon in mod_hashes:
            live_fallback.setdefault(canon, 'caller-routed (live_seed)')
    for (ms_comp, ms_slot), ms_candidates in sorted(
            (multi_state_seats or {}).items()):
        for h in sorted(ms_candidates):
            canon = alias.get(h, h)
            if canon in mod_hashes:
                live_fallback.setdefault(
                    canon, f'multi-state seat (component {ms_comp}, '
                           f'ps-t{ms_slot}): needs per-texture identity')

    def _is_live(tex_hash: Optional[str]) -> bool:
        return (tex_hash is not None
                and alias.get(tex_hash, tex_hash) in live_fallback)

    def _is_trusted(tex_hash: str) -> bool:
        """Replaced textures are always condition-worthy; everything else
        must be in the caller's trusted set (session-stable object inputs)
        when one is provided."""
        canon = alias.get(tex_hash, tex_hash)
        if canon in mod_hashes:
            return True
        return (trusted_hashes is None or tex_hash in trusted_hashes
                or canon in trusted_hashes)

    def _route_live(tex_hash: str, reason: str):
        canon = alias.get(tex_hash, tex_hash)
        if canon in mod_hashes and canon not in live_fallback:
            live_fallback[canon] = reason
            warnings.append(
                f'texture {canon} routed to live hash fallback: {reason}')

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
                for ps, pair_map in comp_pairs.items():
                    is_mat = _is_material_pair(pair_map, texture_info)
                    for slot, h in pair_map.items():
                        if h is None or (form_id, comp_id, ps, slot) in service_seats:
                            continue  # stale-inherited seats are not form evidence
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
                for ps, pair_map in comp_pairs.items():
                    if not _is_material_pair(pair_map, texture_info):
                        continue
                    for slot in constants.MAIN_SLOTS:
                        h = pair_map.get(slot)
                        if (h is not None and h in mod_hashes
                                and (form_id, comp_id, ps, slot) not in service_seats):
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
        # ("no form fork derivable" warning is emitted below, once anchor
        # coverage is known - fully anchored mods need no texture fork.)

    # Zero-latency form anchors are USER-SPECIFIED only (manual_anchors): the
    # plugin never auto-picks shader hashes. A form-exclusive vb0 anchor
    # latches on the new form's very first draw (geometry never streams).
    # Field facts (v3.5): this WWMI fork matches draws by vertex-buffer hash
    # only — an ib hash never fires (vs untested, treated the same); shader
    # anchors are instant too but version-fragile. A stale anchor section
    # simply never fires and the texture latches take over.
    anchor_resources: List[Tuple[str, int]] = []  # 8-hex: vb0 hash
    anchor_shaders: List[Tuple[str, int]] = []    # 16-hex: ps hash
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
    # Heartbeat watchdog: with exactly ONE unanchored form (any form count),
    # a whole frame without an anchor heartbeat pins that form by
    # elimination — the anchors cover every switch direction with zero
    # latency. All anchors share one heartbeat: WHICH anchored form is
    # active is settled by the anchors' own direct latches; the absence
    # rule only needs "did any anchor fire this frame". Two-forms/one-
    # anchor is the field-proven special case. Known trade-off with
    # multiple anchors: ARMED is global, so one stale anchor among live
    # ones misreads its form's absence as the unanchored form (texture
    # latches then fight per frame until re-export) — geometry hashes
    # usually churn together, accepted and documented in ADR 0007 rev 6.
    anchored_forms = {f for _, f in anchor_resources + anchor_shaders}
    watchdog_form = None  # the UNANCHORED form the absence rule commits to
    unanchored_forms = set(range(1, len(forms) + 1)) - anchored_forms
    # Anchor coverage (ADR 0007 rev 12): anchors plus the watchdog fully
    # cover form detection when at most one form is unanchored - texture-
    # hash latches add only version-fragile dead weight there and are
    # suppressed (uncovered_forms keeps the fallback set for partial
    # coverage). M1 format markers are zero-hash and always kept.
    uncovered_forms = set(unanchored_forms)
    marks_fully_covered = bool(multi_form and anchored_forms
                               and len(unanchored_forms) <= 1)
    if multi_form and anchored_forms and len(unanchored_forms) == 1:
        watchdog_form = unanchored_forms.pop()
    if multi_form:
        for form_id, (label, _) in enumerate(forms, start=1):
            if form_id not in anchored_forms and form_id != watchdog_form:
                warnings.append(
                    f'form "{label}" has no manual anchor - switching TO it '
                    f'relies on the texture latches (streaming-delayed)')
        if (not fork_latches and not form_markers_m1
                and not marks_fully_covered):
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
                    if slot in sig_slots and not _is_live(h)
                    and (form_id, comp_id, ps, slot) not in service_seats
                    and _is_trusted(h)
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
                    if h is not None and h in mod_hashes and not _is_live(h)}
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
                            if h is not None and not _is_live(h):
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

    if not component_branches and not live_fallback:
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
                # Same signature, conflicting assignments, and no per-texture
                # runtime identity available on the field loader (hash-matched
                # mark sections never join the per-draw path - ADR 0007
                # rev 10): the member's own content goes to live fallback
                # (its stock hash sections keep replacing it) and the branch
                # is dropped. The preferred branch may still fire on the
                # member's draws, but the routed textures are live-replaced
                # there (untagged file resources), so signature terms and
                # self-guards referencing those slots fail closed.
                for slot, res in sorted(member.assign.items()):
                    if groups[0].assign.get(slot) == res:
                        continue
                    for h in sorted(member.cond_slots.get(slot, ())):
                        if mod_hashes.get(h) == res:
                            _route_live(h, f'component {comp_id}: same-'
                                           f'signature conflict at ps-t{slot}')
                warnings.append(
                    f'component {comp_id}: two texture sets share the same format '
                    f'signature - the conflicting set falls back to live hash '
                    f'sections, the preferred set keeps the slot layer')
                dropped.append(member)
        if dropped:
            component_branches[comp_id] = [b for b in branches if b not in dropped]

    # ---------------------------------------------- vanilla-pair guards --
    # A branch whose signature an UNREPLACED pair of the same component also
    # satisfies (no recorded shared slot with a differing family) would bind
    # mod content onto vanilla draws (the C6 scramble: the shared chameleon
    # set's bare condition matched the component's own unkept material).
    # With no per-texture runtime identity on the field loader (rev 10) the
    # remaining protection is the per-slot self-guards, so a collider is
    # DANGEROUS only where its recorded content shares a format family with
    # the branch's content at an ASSIGNED slot (the self-guard would pass
    # there and over-write the vanilla draw). Dangerous branches are dropped
    # and their content routed to live fallback; harmless ones keep the slot
    # layer (the self-guards fail closed on the collider's draws).
    # Multi-state seats no longer join as synthetic colliders: their
    # replaced candidates were routed to live fallback up front.
    for comp_id, branches in component_branches.items():
        unassigned = [(sig, pm) for sig, pm, has in comp_records.get(comp_id, [])
                      if not has and sig]
        if not unassigned:
            continue
        dropped = []
        for branch in branches:
            bsig = dict(branch.signature)
            colliders = []
            for osig, opm in unassigned:
                shared = set(bsig) & set(osig)
                if shared and any(bsig[s] != osig[s] for s in shared):
                    continue  # mutually exclusive on a recorded shared slot
                colliders.append(opm)
            if not colliders:
                continue
            guard_families = {
                slot: {key for h in branch.cond_slots.get(slot, ())
                       if (key := _family_key(h, texture_info)) is not None}
                for slot in branch.assign}
            dangerous_slots = set()
            for opm in colliders:
                for slot in branch.assign:
                    okey = _family_key(opm.get(slot), texture_info)
                    if okey is not None and okey in guard_families.get(slot, ()):
                        dangerous_slots.add(slot)
            if not dangerous_slots:
                continue
            # Per-slot precision: only the family-colliding seats lose the
            # slot layer; the rest of the branch keeps binding (slots the
            # collider never carries are protected by their self-guards,
            # leftover-state coincidences are the dump-replay sandbox's
            # job to surface).
            for slot in sorted(dangerous_slots):
                res = branch.assign.pop(slot)
                for h in sorted(branch.cond_slots.get(slot, ())):
                    if mod_hashes.get(h) == res:
                        _route_live(h, f'component {comp_id}: ps-t{slot} '
                                       f'collides with an unreplaced texture '
                                       f'set of the same family')
            warnings.append(
                f'component {comp_id}: assignment(s) at '
                f'{", ".join(f"ps-t{s}" for s in sorted(dangerous_slots))} '
                f'share their format family with an unreplaced texture set - '
                f'that content falls back to live hash sections')
            if not branch.assign:
                dropped.append(branch)
        if dropped:
            component_branches[comp_id] = [b for b in branches
                                           if not any(b is d for d in dropped)]
    component_branches = {c: b for c, b in component_branches.items() if b}
    if not component_branches and not live_fallback:
        raise SlotStyleDegrade('no component produced any slot assignment')
    # All-live is a valid outcome: an empty slot layer with every replaced
    # texture kept on (family-expandable) stock hash sections.

    # Anchor-coverage suppression of texture-hash latches (ADR 0007 rev 12):
    # with every form covered by anchors + watchdog the latches are version-
    # fragile dead weight; with partial coverage only the uncovered forms
    # keep their fallback latches. No anchors at all = unchanged fallback.
    suppressed_latches = 0
    if fork_latches and marks_fully_covered:
        suppressed_latches = len(fork_latches)
        fork_latches = []
        warnings.append(
            'form anchors fully cover form detection - texture-hash mark '
            'sections suppressed (zero-hash M1 format markers kept)')
    elif fork_latches and multi_form and anchored_forms and len(uncovered_forms) >= 2:
        kept_latches = [t for t in fork_latches if t[3] in uncovered_forms]
        suppressed_latches = len(fork_latches) - len(kept_latches)
        if suppressed_latches:
            fork_latches = kept_latches
            warnings.append(
                f'{suppressed_latches} texture-hash latch(es) for anchored '
                f'forms suppressed - fallback marks kept only for the '
                f'unanchored forms')

    # Fork latch keys populate the mark table (form detection only).
    fork_latches = [(c, s, h, f) for c, s, h, f in fork_latches
                    if _ensure_mark(h) is not None]

    # -------------------------------------------------- probe collection --
    # FORM DETECTION ONLY (rev 10). The rev-9 $hit content keys are gone:
    # the D1/D2 dye diagnostics proved hash-matched mark sections never join
    # the per-draw command-list path on the field WWMI fork (neither their
    # filter_index nor their CheckTextureOverride-driven command lists are
    # observable), so per-texture discrimination cannot be a binding-layer
    # dependency - replaced multi-state candidates go to live fallback
    # instead. The latch ladder is kept for form detection because it only
    # DEGRADES there (anchors and M1 markers carry the switch; a dead latch
    # leaves the last/default form).
    # (comp, slot, section hash) -> list of ('latch', form)
    probe_effects: Dict[Tuple[int, int, str], List[Tuple[str, object]]] = {}
    for comp_id, slot, h, form_id in fork_latches:
        probe_effects.setdefault((comp_id, slot, h), []).append(('latch', form_id))
    # Residency variants collapsed by the dedup react like their canonical
    # texture (e.g. both mip-level hashes of the eye texture latch one form).
    variants_of: Dict[str, List[str]] = {}
    for variant, canon in alias.items():
        variants_of.setdefault(canon, []).append(variant)
    for (comp_id, slot, h), effects in list(probe_effects.items()):
        for variant in variants_of.get(h, ()):
            if _ensure_mark(variant) is not None:
                probe_effects.setdefault((comp_id, slot, variant),
                                         []).extend(effects)
    probe_slots: Dict[int, Set[int]] = {}
    for (comp_id, slot, h), effects in probe_effects.items():
        probe_slots.setdefault(comp_id, set()).add(slot)

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
            # Latch-marked hashes (form detection) keep an OR-arm: on
            # 3DMigoto builds where the hash-matched mark section wins the
            # fi contest the texture reads its mark value, on the field
            # fork it reads the family tag - either arm matches.
            values: List[str] = [fi_text[key]]
            for h in sorted(branch.cond_slots.get(slot, ())):
                if h in marks:
                    values.append(str(marks[h]))
            used_families.setdefault(comp_id, set()).add(key)
            if len(values) == 1:
                terms.append(f'ps-t{slot} == {values[0]}')
            else:
                terms.append('(' + ' || '.join(f'ps-t{slot} == {v}' for v in values) + ')')
        return terms

    def _guarded_assignments(branch: _Branch) -> List[str]:
        """Backup+bind lines for one branch body. The branch condition only
        pins the SIGNATURE slots, so every other assigned slot self-guards on
        its recorded format-family tag (OR mark values, same dual-semantics
        convention as the condition terms): compatible-cluster unions and
        multi-state passes bind a slot only while it actually carries the
        recorded family - binding "harmlessly" onto whatever the pass left
        there over-wrote cubemap/depth inputs on shared outline/shadow passes
        (field bug, ADR 0007 rev 8). Slots with no verifiable state (no
        recorded format or mark anywhere) are skipped, never bound blind."""
        sig_slots = {slot for slot, _ in branch.signature}
        lines: List[str] = []
        for slot in sorted(branch.assign):
            backup = constants.RES_BACKUP.format(slot=slot)
            bind = [f'{backup} = ref ps-t{slot}',
                    f'ps-t{slot} = ref {branch.assign[slot]}']
            if slot in sig_slots:
                # the condition's family term already verified this slot
                # for the current draw
                lines.extend(f'    {line}' for line in bind)
                used_slots.add(slot)
                continue
            values: List[str] = []
            for h in sorted(branch.cond_slots.get(slot, ())):
                key = _family_key(h, texture_info)
                if key is not None:
                    used_families.setdefault(comp_id, set()).add(key)
                    if fi_text[key] not in values:
                        values.append(fi_text[key])
                if h in marks and str(marks[h]) not in values:
                    values.append(str(marks[h]))
            if not values:
                warnings.append(
                    f'component {comp_id}: assignment at ps-t{slot} skipped - '
                    f'no recorded format to verify the slot state '
                    f'(re-extract with a current build to record formats)')
                continue
            lines.append('    if ' + ' || '.join(f'ps-t{slot} == {v}'
                                                 for v in values))
            lines.extend(f'        {line}' for line in bind)
            lines.append('    endif')
            used_slots.add(slot)
        return lines

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
        out.append('; -- Per-texture marks (FORM DETECTION only). filter_index = f(hash):')
        out.append(';    deterministic, identical across all velo mods. The command bodies')
        out.append(';    react ONLY to their own probe keys (set around our')
        out.append(';    CheckTextureOverride calls), latching the form. Known to be inert')
        out.append(';    on some loader builds - anchors/M1 markers carry the switch there.')
        for h in sorted(marks):
            out.append('')
            out.append(f'[{constants.SEC_TEX_MARK.format(texture_hash=h)}]')
            out.append(f'hash = {h}')
            out.append('allow_duplicate_hash = true')
            out.append(f'match_priority = {constants.MARK_PRIORITY}')
            out.append(f'filter_index = {marks[h]}')
            # One block per distinct effect, probe keys OR-merged.
            by_effect: Dict[Tuple[str, object], List[int]] = {}
            for e_comp, e_slot, kind, payload in effects_by_hash.get(h, ()):
                by_effect.setdefault((kind, payload), []).append(
                    constants.probe_key(e_comp, e_slot))
            for (kind, payload), keys in sorted(by_effect.items(),
                                                key=lambda e: (e[0][0], str(e[0][1]))):
                cond = ' || '.join(f'{constants.VAR_LATCH_KEY} == {k}'
                                   for k in sorted(set(keys)))
                out.append(f'if {cond}')
                out.append(f'    {constants.VAR_FORM} = {payload}')
                out.append('endif')

    if anchor_resources or anchor_shaders:
        out.append('')
        out.append('; -- USER-SPECIFIED form anchors (detection only, zero-latency: a')
        out.append(';    form-exclusive vb0 fires on the form\'s very first draw). A')
        out.append(';    stale anchor never fires and the texture latches take over.')
        for h, form_id in sorted(anchor_resources):
            out.append('')
            out.append(f'[{constants.SEC_RESOURCE_ANCHOR.format(anchor_hash=h)}]')
            out.append(f'hash = {h}')
            out.append('allow_duplicate_hash = true')
            # Field-proven shape: without match_first_index a buffer-hash
            # section never enters the per-draw command-list path.
            out.append('match_priority = 0')
            out.append('match_first_index = 0')
            out.append(f'{constants.VAR_FORM} = {form_id}')
            if watchdog_form is not None:
                out.append(f'{constants.VAR_ANCHOR_SEEN} = 1')
                out.append(f'{constants.VAR_ANCHOR_ARMED} = 1')
        for h, form_id in sorted(anchor_shaders):
            out.append('')
            out.append(f'[{constants.SEC_SHADER_ANCHOR.format(anchor_hash=h)}]')
            out.append(f'hash = {h}')
            out.append('allow_duplicate_hash = true')
            out.append(f'filter_index = {constants.ps_mark_value(h)}')

    component_list_names: Dict[int, str] = {}
    body_chunks: List[str] = []
    for comp_id in sorted(component_branches):
        branches = component_branches.get(comp_id, [])
        name = constants.CMDLIST_SET_TEXTURES.format(component_id=comp_id)
        component_list_names[comp_id] = name
        chunk: List[str] = ['', f'[{name}]']
        # Manual shader anchors: instant on the switch frame, evaluated only
        # during this character's draws (other characters sharing the shader
        # never run these lists). The hash may be a vs or a ps - check both.
        for h, form_id in sorted(anchor_shaders):
            value = constants.ps_mark_value(h)
            chunk.append(f'if ps == {value} || vs == {value}')
            chunk.append(f'    {constants.VAR_FORM} = {form_id}')
            if watchdog_form is not None:
                chunk.append(f'    {constants.VAR_ANCHOR_SEEN} = 1')
                chunk.append(f'    {constants.VAR_ANCHOR_ARMED} = 1')
            chunk.append('endif')
        for form_id, markers in sorted(form_markers_m1.items()):
            for marker_comp, slot, key in markers:
                if marker_comp != comp_id:
                    continue
                used_families.setdefault(comp_id, set()).add(key)
                chunk.append(f'if ps-t{slot} == {fi_text[key]}')
                chunk.append(f'    {constants.VAR_FORM} = {form_id}')
                chunk.append('endif')
        # Branches by specificity (slot count desc, cluster winners before
        # shadowed leftovers) - subset conditions go last so a superset draw
        # is claimed by its own branch.
        ordered = sorted(
            branches,
            key=lambda b: (-len(b.signature),
                           not b.preferred, b.form_gate or 0, b.signature,
                           tuple(sorted(b.assign.items()))))
        first = True
        for branch in ordered:
            cond = ' && '.join(_terms(branch))
            if branch.form_gate is not None:
                cond += f' && {constants.VAR_FORM} == {branch.form_gate}'
            chunk.append(f'{"if" if first else "else if"} {cond}')
            chunk.extend(_guarded_assignments(branch))
            first = False
        if ordered:
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

    watchdog_lines: List[str] = []
    if watchdog_form is not None:
        extra_globals.append(constants.VAR_ANCHOR_SEEN)
        extra_globals.append(constants.VAR_ANCHOR_ARMED)
        watchdog_lines = [
            '; Form-anchor watchdog: the anchored form\'s exclusive part draws',
            '; every frame, so a whole frame without a heartbeat means the',
            '; other form is active. ARMED only after a real anchor hit - a',
            '; stale anchor leaves the texture latches in charge.',
            f'if {constants.VAR_ANCHOR_SEEN}',
            f'    post {constants.VAR_ANCHOR_SEEN} = 0',
            f'elif {constants.VAR_ANCHOR_ARMED}',
            f'    {constants.VAR_FORM} = {watchdog_form}',
            'endif',
        ]

    # Live-fallback textures are NOT covered by the slot layer: excluding
    # their indices makes the caller keep their stock hash sections live
    # (family-expanded when a hash family table is available).
    if live_fallback:
        for index, (h, _res) in enumerate(textures):
            if alias.get(h, h) in live_fallback:
                covered_resource_indices.discard(index)

    return SlotPlan(
        block_text='\n'.join(out),
        component_list_names=component_list_names,
        covered_resource_indices=covered_resource_indices,
        blind_zone=blind_zone,
        multi_form=multi_form,
        used_slots=sorted(used_slots),
        probe_list_names=probe_list_names,
        extra_globals=extra_globals,
        watchdog_lines=watchdog_lines,
        default_form_id=watchdog_form if watchdog_form is not None else 1,
        live_fallback=dict(live_fallback),
        warnings=warnings,
        stats={
            'forms': len(forms),
            'components': len(component_branches),
            'branches': sum(len(b) for b in component_branches.values()),
            'conflicts': conflict_count,
            'marks': len(marks),
            'fork_latches': len(fork_latches),
            'anchors': len(anchor_resources) + len(anchor_shaders),
            'anchor_watchdog': 1 if watchdog_form is not None else 0,
            'probes': len(probe_effects),
            'live_fallback': len(live_fallback),
            'format_sections': format_section_count,
            'covered_textures': len(covered_resource_indices),
            'blind_zone_textures': len(blind_zone),
            'phantom_pairs': phantom_pairs,
            'service_slots': len(service_seats),
            'suppressed_latches': suppressed_latches,
        },
    )
