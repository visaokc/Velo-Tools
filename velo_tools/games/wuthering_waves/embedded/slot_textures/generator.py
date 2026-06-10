# Slot-style texture layer generator (pure python, no bpy / no _wwmi_core
# imports — unit-testable headless).
#
# Input: per-form per-(component x shader-pair x slot) texture maps (base
# ShaderTextureUsage.json + optional ShaderTextureUsageForms.json sidecar) and
# the export texture list. Output: the ini text block implementing the
# slot-style replacement layer plus the metadata transform.py needs to rewire
# the rendered mod.ini.
#
# Replacement model (mechanizes the hand-validated Aemeath conversion, see
# docs/adr/0006): per known PS a deterministic ShaderOverride tag; component
# command lists branch on `ps == <tag>` and rebind only slots whose dumped
# hash belongs to the mod's texture set (backup -> ref rebind -> restore after
# the draws); multi-form characters get a persistent $velo_form flag driven by
# form-unique material PS; a structural ShaderRegex family tag plus negative
# sentinel guards covers material-pass variants absent from every dump (menu
# transition pipeline etc.) with the per-form safe-intersection map.

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


# ---------------------------------------------------------------- loading --

def normalize_usage(raw: dict, source: str, warnings: List[str]) -> FormData:
    """Converts one ShaderTextureUsage-shaped dict into FormData."""
    out: FormData = {}
    for comp_name, pairs in (raw or {}).items():
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
    """Loads base + extra form maps. forms[0] is always the base extraction."""
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

    sidecar_path = Path(object_source_folder) / constants.FORMS_SIDECAR_FILENAME
    if sidecar_path.is_file():
        try:
            with open(sidecar_path, encoding='utf-8') as f:
                sidecar = json.load(f)
        except Exception as e:
            raise SlotStyleDegrade(f'failed to read {constants.FORMS_SIDECAR_FILENAME}: {e}')
        for entry in sidecar.get('extra_forms') or []:
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
                      mod_hashes: Dict[str, str]) -> bool:
    modded_main = sum(1 for slot, h in pair_map.items()
                      if slot in constants.MAIN_SLOTS
                      and h is not None and h in mod_hashes)
    return modded_main >= constants.MATERIAL_MIN_MODDED


def _fallback_map(comp_pairs: Dict[str, Dict[int, Optional[str]]],
                  mod_hashes: Dict[str, str]) -> Optional[Dict[int, str]]:
    """Safe intersection of the material pairs of one (form, component):
    a slot is included only when EVERY material pair binds the same modded
    texture there (divergent / unknown / unmodded slots are skipped)."""
    material = [m for m in comp_pairs.values() if _is_material_pair(m, mod_hashes)]
    if not material:
        return None
    out: Dict[int, str] = {}
    for slot in constants.MAIN_SLOTS:
        resources = set()
        for pair_map in material:
            h = pair_map.get(slot)
            if h is None or h not in mod_hashes:
                resources = None
                break
            resources.add(mod_hashes[h])
        if resources is not None and len(resources) == 1:
            out[slot] = resources.pop()
    return out or None


def _collect_sentinels(forms: List[Tuple[str, FormData]],
                       mod_hashes: Dict[str, str]) -> List[str]:
    """Texture hashes pinned at the sentinel slot of NON-material pairs —
    distinctive of the screen-space / outline / face binding families. Used as
    negative guards so the structural fallback never fires on those."""
    counter: Dict[str, int] = {}
    for _, form_data in forms:
        for comp_pairs in form_data.values():
            for pair_map in comp_pairs.values():
                if _is_material_pair(pair_map, mod_hashes):
                    continue
                h = pair_map.get(constants.SENTINEL_SLOT)
                if h is not None and h not in mod_hashes:
                    counter[h] = counter.get(h, 0) + 1
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [h for h, _ in ranked[:constants.MAX_SENTINELS]]


def _render_assignments(assign: Dict[int, str], indent: str) -> List[str]:
    lines = []
    for slot in sorted(assign):
        lines.append(f'{indent}Resource{constants.SECTION_PREFIX}TempT{slot} = ref ps-t{slot}')
        lines.append(f'{indent}ps-t{slot} = ref {assign[slot]}')
    return lines


def build_plan(forms: List[Tuple[str, FormData]],
               textures: List[Tuple[str, str]],
               load_warnings: Optional[List[str]] = None) -> SlotPlan:
    """textures: (texture hash, resource section name) in template order."""
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

    # Per-PS presence across forms (form ids are 1-based, base = 1).
    ps_forms: Dict[str, Set[int]] = {}
    for form_id, (_, form_data) in enumerate(forms, start=1):
        for comp_pairs in form_data.values():
            for ps in comp_pairs:
                ps_forms.setdefault(ps, set()).add(form_id)

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

    sentinels = _collect_sentinels(forms, mod_hashes)
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
                    if ps_forms[ps] == {form_id} and _is_material_pair(pair_map, mod_hashes):
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
                        (f'ps == {tag} && $velo_form == {form_id}', nonempty[form_id]))

        # Structural family fallback (only when sentinels are derivable).
        if sentinel_values:
            per_form_fb: Dict[int, Dict[int, str]] = {}
            for form_id, (_, form_data) in enumerate(forms, start=1):
                fb = _fallback_map(form_data.get(comp_id, {}), mod_hashes)
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
                            (f'{base_cond} && $velo_form == {form_id}', per_form_fb[form_id]))
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
    prefix = constants.SECTION_PREFIX
    out: List[str] = []
    form_sources = ', '.join(label for label, _ in forms)
    out.append('')
    out.append('; ============================================================')
    out.append(f'; Velo slot-style texture layer (generated - do not edit by hand)')
    out.append(f'; Forms: {form_sources}')
    out.append('; Textures are rebound by slot inside the component draw scope; game')
    out.append('; texture hashes (which churn with streaming) are never matched. See')
    out.append('; games/wuthering_waves/docs/adr/0006-slot-style-texture-overrides.md')
    out.append('; ============================================================')
    out.append('')
    out.append(constants.FAMILY_REGEX_SECTIONS.rstrip())
    out.append('')

    out.append(f'; -- Deterministic per-PS tags (filter_index = f(ps hash): identical')
    out.append(f';    across all velo slot-style mods, duplicates coexist harmlessly)')
    for ps in sorted(ps_tags):
        out.append('')
        out.append(f'[ShaderOverride{prefix}Ps{ps}]')
        out.append(f'hash = {ps}')
        out.append('allow_duplicate_hash = true')
        out.append(f'filter_index = {ps_tags[ps]}')

    if sentinel_values:
        out.append('')
        out.append(f'; -- Sentinel marks: non-material binding families pin these textures')
        out.append(f';    at ps-t{constants.SENTINEL_SLOT}; the structural fallback rejects such draws')
        for h in sorted(sentinel_values):
            out.append('')
            out.append(f'[TextureOverride{prefix}Sentinel{h}]')
            out.append(f'hash = {h}')
            out.append('match_priority = 0')
            out.append(f'filter_index = {sentinel_values[h]}')

    out.append('')
    out.append('; -- Per-draw backup slots (restored right after the component draws)')
    for slot in sorted(used_slots):
        out.append(f'[Resource{prefix}TempT{slot}]')

    if multi_form:
        out.append('')
        out.append(f'[CommandList{prefix}DetectForm]')
        out.append('; Persistent form flag driven by form-unique material PS (shared or')
        out.append('; transition PS are excluded - their binding roles swap between forms).')
        first = True
        for form_id in sorted(detection):
            tags = detection[form_id]
            if not tags:
                continue
            cond = ' || '.join(f'ps == {t}' for t in tags)
            out.append(f'{"if" if first else "else if"} {cond}')
            out.append(f'    $velo_form = {form_id}')
            first = False
        if not first:
            out.append('endif')

    component_list_names: Dict[int, str] = {}
    for comp_id, branches in sorted(component_branches.items()):
        name = f'CommandList{prefix}TexturesC{comp_id}'
        component_list_names[comp_id] = name
        out.append('')
        out.append(f'[{name}]')
        if multi_form:
            out.append(f'run = CommandList{prefix}DetectForm')
        first = True
        for cond, assign in branches:
            out.append(f'{"if" if first else "else if"} {cond}')
            out.extend(_render_assignments(assign, '    '))
            first = False
        out.append('endif')

    out.append('')
    out.append(f'[CommandList{prefix}Restore]')
    out.append('; Rebind original game textures after our draws so later draws that')
    out.append('; inherit pipeline state never see mod textures.')
    for slot in sorted(used_slots):
        out.append(f'if Resource{prefix}TempT{slot} !== null')
        out.append(f'    ps-t{slot} = ref Resource{prefix}TempT{slot}')
        out.append(f'    Resource{prefix}TempT{slot} = null')
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
            'covered_textures': len(covered_resource_indices),
            'blind_zone_textures': len(blind_zone),
        },
    )
