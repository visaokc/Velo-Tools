# Slot-style texture layer generator (pure python, no bpy / no _wwmi_core
# imports — unit-testable headless).
#
# Input: per-form per-(component x shader-pair x slot) texture maps (the
# "Component N" keys of ShaderTextureUsage.json plus its "extra_forms" key),
# the export texture list, and optional DDS descriptors read live from the
# source-folder files. Output: the ini text block implementing the slot-style
# replacement layer plus the metadata transform.py needs to rewire the
# rendered mod.ini.
#
# Replacement model (mechanizes the hand-validated Aemeath conversion, see
# docs/adr/0006): per known PS a deterministic ShaderOverride tag; component
# command lists branch on `ps == <tag>` and rebind only slots whose dumped
# hash belongs to the mod's texture set (backup -> ref rebind -> restore after
# the draws); multi-form characters (any count >= 2) get a persistent $form_id
# flag driven by form-unique material PS; a structural ShaderRegex family tag
# plus negative sentinel guards covers material-pass variants absent from
# every dump (menu transition pipeline etc.) with the per-form
# safe-intersection map.
#
# Material classification is STRUCTURAL (slot-set fingerprint: a pair binding
# all of t0..t3, with an optional square+mipmapped DDS belt) — independent of
# which textures the author kept, so pruned and unpruned folders derive the
# same pass structure. The membership-based rule this replaces broke under
# unpruned sets (screen-space/face pairs classified material, emptying the
# fallback intersections — the in-game "transition reverts to vanilla" bug).

import json
import re

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

_PS_RE = re.compile(r'ps=([0-9a-f]{16})')
_COMP_RE = re.compile(r'component\s*(\d+)\s*$', re.I)
_SLOT_RE = re.compile(r'^ps-t(\d+)$')

# Top-level keys of ShaderTextureUsage.json that are not component maps.
_RESERVED_KEYS = {constants.EXTRA_FORMS_KEY, 'version'}


# ---------------------------------------------------------------- loading --

def normalize_usage(raw: dict, source: str, warnings: List[str]) -> FormData:
    """Converts one ShaderTextureUsage-shaped dict into FormData."""
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
        for pair_key, slots in (pairs or {}).items():
            ps_found = _PS_RE.search(pair_key)
            if not ps_found:
                warnings.append(f'{source}: pair "{pair_key}" has no ps hash, skipped')
                continue
            ps_hash = ps_found.group(1)
            pair_out = comp_out.setdefault(ps_hash, {})
            for slot_name, tex_hash in (slots or {}).items():
                slot_found = _SLOT_RE.match(slot_name)
                if not slot_found:
                    continue
                slot = int(slot_found.group(1))
                if not isinstance(tex_hash, str):
                    tex_hash = None
                if slot in pair_out and pair_out[slot] != tex_hash:
                    # Same (component, ps, slot) seen with different content
                    # (multi-state variant / vs-merge conflict): fail-safe.
                    pair_out[slot] = None
                else:
                    pair_out[slot] = tex_hash
    return out


def load_forms(object_source_folder: Path) -> Tuple[List[Tuple[str, FormData]], List[str]]:
    """Loads base + extra form maps from the single ShaderTextureUsage.json
    (schema v2: extra forms under the reserved "extra_forms" key). A pre-v2
    sidecar file is still read for compatibility until the next merge migrates
    it. forms[0] is always the base extraction."""
    warnings: List[str] = []
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

    forms: List[Tuple[str, FormData]] = [('base', normalize_usage(base_raw, 'base', warnings))]

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
        forms.append((label, normalize_usage(entry.get('components'), label, warnings)))

    if not any(form_data for _, form_data in forms):
        raise SlotStyleDegrade('no usable shader/texture usage data found')
    return forms, warnings


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


def _modded_map(pair_map: Dict[int, Optional[str]],
                mod_hashes: Dict[str, str]) -> Dict[int, str]:
    return {slot: mod_hashes[h] for slot, h in pair_map.items()
            if h is not None and h in mod_hashes}


def _is_material_pair(pair_map: Dict[int, Optional[str]],
                      dds_lookup: Optional[Dict[str, "object"]] = None) -> bool:
    """Structural slot-set fingerprint: material pairs bind ALL main slots.
    When a slot's texture descriptor is known (file present in the source
    folder), it must look like a character texture (square; dump-extracted
    files carry no mip chain, so squareness is the only reliable belt);
    unknown descriptors never block."""
    if not all(slot in pair_map for slot in constants.MAIN_SLOTS):
        return False
    if dds_lookup and constants.MATERIAL_REQUIRE_SQUARE:
        for slot in constants.MAIN_SLOTS:
            h = pair_map.get(slot)
            meta = dds_lookup.get(h) if h else None
            if meta is not None and not meta.is_square:
                return False
    return True


def _fallback_map(comp_pairs: Dict[str, Dict[int, Optional[str]]],
                  mod_hashes: Dict[str, str],
                  dds_lookup,
                  local_ps: Optional[Set[str]] = None) -> Optional[Dict[int, str]]:
    """Safe intersection of the material pairs of one (form, component):
    a slot is included only when EVERY contributing pair binds the same modded
    texture there (divergent / unknown / unmodded slots are skipped).

    When the component has COMPONENT-LOCAL material PS (appearing in no other
    component), only those contribute: a component carrying both an own
    material set and a cross-component shared one (e.g. accessory components)
    would otherwise intersect to nothing, and the undumped variants of its
    own material pass are the ones the fallback exists for (field data: the
    transition variant observed for such a component carried the own-material
    layout)."""
    material = {ps: m for ps, m in comp_pairs.items()
                if _is_material_pair(m, dds_lookup)}
    if not material:
        return None
    if local_ps:
        local = {ps: m for ps, m in material.items() if ps in local_ps}
        if local:
            material = local
    out: Dict[int, str] = {}
    for slot in constants.MAIN_SLOTS:
        resources = set()
        for pair_map in material.values():
            h = pair_map.get(slot)
            if h is None or h not in mod_hashes:
                resources = None
                break
            resources.add(mod_hashes[h])
        if resources is not None and len(resources) == 1:
            out[slot] = resources.pop()
    return out or None


def _collect_sentinels(forms: List[Tuple[str, FormData]],
                       dds_lookup) -> List[str]:
    """Texture hashes pinned at the sentinel slot of NON-material pairs and
    never seen at that slot of a material pair — distinctive of the
    screen-space / outline / face binding families. Mod-set membership is
    irrelevant on purpose (marking is not replacing), so the sentinel set
    survives unpruned texture folders."""
    counter: Dict[str, int] = {}
    material_t2: Set[str] = set()
    for _, form_data in forms:
        for comp_pairs in form_data.values():
            for pair_map in comp_pairs.values():
                h = pair_map.get(constants.SENTINEL_SLOT)
                if h is None:
                    continue
                if _is_material_pair(pair_map, dds_lookup):
                    material_t2.add(h)
                else:
                    counter[h] = counter.get(h, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [h for h, _ in ranked if h not in material_t2][:constants.MAX_SENTINELS]


def _render_assignments(assign: Dict[int, str], indent: str) -> List[str]:
    lines = []
    for slot in sorted(assign):
        backup = constants.RES_BACKUP.format(slot=slot)
        lines.append(f'{indent}{backup} = ref ps-t{slot}')
        lines.append(f'{indent}ps-t{slot} = ref {assign[slot]}')
    return lines


def build_plan(forms: List[Tuple[str, FormData]],
               textures: List[Tuple[str, str]],
               load_warnings: Optional[List[str]] = None,
               dds_lookup: Optional[Dict[str, "object"]] = None) -> SlotPlan:
    """textures: (texture hash, resource section name) in template order.
    dds_lookup: optional {texture hash: DdsMeta} read from the source folder."""
    warnings: List[str] = list(load_warnings or [])
    multi_form = len(forms) > 1
    mod_hashes = {h: res for h, res in textures}
    if not mod_hashes:
        raise SlotStyleDegrade('mod has no textures, nothing to do')

    # Coverage / blind zone.
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

    # Per-PS presence across forms (form ids are 1-based, base = 1) and
    # across components (for the fallback locality preference).
    ps_forms: Dict[str, Set[int]] = {}
    ps_components: Dict[str, Set[int]] = {}
    for form_id, (_, form_data) in enumerate(forms, start=1):
        for comp_id, comp_pairs in form_data.items():
            for ps in comp_pairs:
                ps_forms.setdefault(ps, set()).add(form_id)
                ps_components.setdefault(ps, set()).add(comp_id)

    # Deterministic PS tags + collision check.
    ps_tags: Dict[str, int] = {}
    value_owner: Dict[int, str] = {}
    for ps in sorted(ps_forms):
        value = constants.ps_tag_value(ps)
        other = value_owner.get(value)
        if other is not None and other != ps:
            raise SlotStyleDegrade(
                f'deterministic filter_index collision between PS {other} and {ps} '
                f'(value {value}); slot-style export is not possible for this object')
        value_owner[value] = ps
        ps_tags[ps] = value

    sentinels = _collect_sentinels(forms, dds_lookup)
    sentinel_values = {}
    for h in sentinels:
        value = constants.sentinel_value(h)
        if value in sentinel_values.values():
            warnings.append(f'sentinel value collision on {h}, sentinel dropped')
            continue
        sentinel_values[h] = value

    # Form detection sets: form-unique PS that are material-classified there.
    detection: Dict[int, List[int]] = {}
    if multi_form:
        for form_id, (label, form_data) in enumerate(forms, start=1):
            det: List[int] = []
            for comp_pairs in form_data.values():
                for ps, pair_map in comp_pairs.items():
                    if ps_forms[ps] == {form_id} and _is_material_pair(pair_map, dds_lookup):
                        tag = ps_tags[ps]
                        if tag not in det:
                            det.append(tag)
            detection[form_id] = sorted(det)
            if not det:
                warnings.append(
                    f'form "{label}" has no form-unique material PS — it cannot be '
                    f'auto-detected at runtime; its form-gated branches will stay inactive')

    # Per-component branches.
    all_comp_ids = sorted({c for _, fd in forms for c in fd})
    component_branches: Dict[int, List[Tuple[str, Dict[int, str]]]] = {}
    used_slots: Set[int] = set()
    guard = ''
    if sentinel_values:
        guard = ''.join(
            f' && ps-t{constants.SENTINEL_SLOT} != {v}'
            for v in sorted(sentinel_values.values()))

    for comp_id in all_comp_ids:
        branches: List[Tuple[str, Dict[int, str]]] = []
        ps_in_comp = sorted({ps for _, fd in forms for ps in fd.get(comp_id, {})})
        for ps in ps_in_comp:
            per_form: Dict[int, Dict[int, str]] = {}
            for form_id, (_, form_data) in enumerate(forms, start=1):
                pair_map = form_data.get(comp_id, {}).get(ps)
                if pair_map is not None:
                    per_form[form_id] = _modded_map(pair_map, mod_hashes)
            nonempty = {f: m for f, m in per_form.items() if m}
            if not nonempty:
                continue
            tag = ps_tags[ps]
            distinct = {tuple(sorted(m.items())) for m in nonempty.values()}
            if len(nonempty) == len(per_form) and len(distinct) == 1:
                branches.append((f'ps == {tag}', next(iter(nonempty.values()))))
            else:
                for form_id in sorted(nonempty):
                    branches.append(
                        (f'ps == {tag} && {constants.VAR_FORM} == {form_id}',
                         nonempty[form_id]))

        # Structural family fallback (only when sentinels are derivable).
        if sentinel_values:
            local_ps = {ps for ps in ps_in_comp if ps_components.get(ps) == {comp_id}}
            per_form_fb: Dict[int, Dict[int, str]] = {}
            for form_id, (_, form_data) in enumerate(forms, start=1):
                fb = _fallback_map(form_data.get(comp_id, {}), mod_hashes,
                                   dds_lookup, local_ps=local_ps)
                if fb:
                    per_form_fb[form_id] = fb
            if per_form_fb:
                base_cond = f'ps == {constants.FAMILY_TAG_VALUE}{guard}'
                distinct = {tuple(sorted(m.items())) for m in per_form_fb.values()}
                if len(per_form_fb) == len(forms) and len(distinct) == 1:
                    branches.append((base_cond, next(iter(per_form_fb.values()))))
                else:
                    for form_id in sorted(per_form_fb):
                        branches.append(
                            (f'{base_cond} && {constants.VAR_FORM} == {form_id}',
                             per_form_fb[form_id]))
        elif comp_id == all_comp_ids[0]:
            warnings.append(
                'no sentinel textures derivable — structural fallback disabled '
                '(unknown pipeline variants like the menu transition stay uncovered)')

        if branches:
            component_branches[comp_id] = branches
            for _, assign in branches:
                used_slots.update(assign)

    if not component_branches:
        raise SlotStyleDegrade('no component produced any slot assignment')

    # ------------------------------------------------------------ emission --
    out: List[str] = []
    form_sources = ', '.join(label for label, _ in forms)
    out.append('')
    out.append('; ============================================================')
    out.append('; Slot-style texture layer (generated - do not edit by hand)')
    out.append(f'; Forms: {form_sources}')
    out.append('; Textures are rebound by slot inside the component draw scope; game')
    out.append('; texture hashes (which churn with streaming) are never matched.')
    out.append('; ============================================================')
    out.append('')
    out.append(constants.FAMILY_REGEX_SECTIONS.rstrip())
    out.append('')

    out.append('; -- Deterministic per-PS tags (filter_index = f(ps hash): identical')
    out.append(';    across all slot-style mods, duplicates coexist harmlessly)')
    for ps in sorted(ps_tags):
        out.append('')
        out.append(f'[{constants.SEC_PS_MARK.format(ps_hash=ps)}]')
        out.append(f'hash = {ps}')
        out.append('allow_duplicate_hash = true')
        out.append(f'filter_index = {ps_tags[ps]}')

    if sentinel_values:
        out.append('')
        out.append('; -- Sentinel marks: non-material binding families pin these textures')
        out.append(f';    at ps-t{constants.SENTINEL_SLOT}; the structural fallback rejects such draws')
        for h in sorted(sentinel_values):
            out.append('')
            out.append(f'[{constants.SEC_TEX_MARK.format(texture_hash=h)}]')
            out.append(f'hash = {h}')
            out.append('match_priority = 0')
            out.append(f'filter_index = {sentinel_values[h]}')

    out.append('')
    out.append('; -- Per-draw backup slots (restored right after the component draws)')
    for slot in sorted(used_slots):
        out.append(f'[{constants.RES_BACKUP.format(slot=slot)}]')

    if multi_form:
        out.append('')
        out.append(f'[{constants.CMDLIST_DETECT_FORM}]')
        out.append('; Persistent form flag driven by form-unique material PS (shared or')
        out.append('; transition PS are excluded - their binding roles swap between forms).')
        first = True
        for form_id in sorted(detection):
            tags = detection[form_id]
            if not tags:
                continue
            cond = ' || '.join(f'ps == {t}' for t in tags)
            out.append(f'{"if" if first else "else if"} {cond}')
            out.append(f'    {constants.VAR_FORM} = {form_id}')
            first = False
        if not first:
            out.append('endif')

    component_list_names: Dict[int, str] = {}
    for comp_id, branches in sorted(component_branches.items()):
        name = constants.CMDLIST_SET_TEXTURES.format(component_id=comp_id)
        component_list_names[comp_id] = name
        out.append('')
        out.append(f'[{name}]')
        if multi_form:
            out.append(f'run = {constants.CMDLIST_DETECT_FORM}')
        first = True
        for cond, assign in branches:
            out.append(f'{"if" if first else "else if"} {cond}')
            out.extend(_render_assignments(assign, '    '))
            first = False
        out.append('endif')

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
            'ps_tags': len(ps_tags),
            'sentinels': len(sentinel_values),
            'components': len(component_branches),
            'branches': sum(len(b) for b in component_branches.values()),
            'fallback_branches': sum(
                1 for b in component_branches.values()
                for cond, _ in b if f'ps == {constants.FAMILY_TAG_VALUE}' in cond),
            'covered_textures': len(covered_resource_indices),
            'blind_zone_textures': len(blind_zone),
        },
    )
