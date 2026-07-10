# Structural transformation of a rendered WWMI mod.ini into slot-style form
# (pure python, no bpy — unit-testable headless).
#
# Anchor-based and template-agnostic on purpose: it operates on the RENDERED
# ini, so the stock merged template, the velo LOD fork templates (whose shared
# [CommandListDrawComponent{c}] lists carry the same trigger/cleanup anchors)
# and future templates are all covered without forking any of them:
#
#   1. [TextureOverrideTexture{i}] hash sections whose texture is covered by
#      the slot maps, or only came from stale-inherited phantom pairs, are
#      removed ([ResourceTexture{i}] filename sections stay; blind-zone and
#      excluded-component fallback textures keep their stock hash section).
#      The global per-slot `CheckTextureOverride = ps-tN` trigger lines stay
#      for ordinary format tags.
#   2. Every non-comment `run = CommandListTriggerResourceOverrides` line that
#      lives in a section whose name carries a component id gets
#      `run = CommandListSetTexturesComponent{id}` injected right after it.
#      Each transaction is wrapped by the generated ps-t scope contract.
#   3. Multi-form only: `global $form_id = {plan.default_form_id}` is added
#      to [Constants] (the unanchored form while the anchor watchdog is
#      active, else 1 = base form; form markers latch it at runtime).
#   4. Anchor watchdog only: plan.watchdog_lines are appended at the end of
#      the stock [Present] section (a [Present] section is created if the
#      template has none).
#   5. The generated slot-style block is appended at the end.
#
# Any missing anchor raises SlotStyleDegrade: the caller falls back to the
# untouched stock output (fail-safe, never a half-transformed ini).

import re

from typing import Dict, List, Optional, Set, Tuple

from . import constants
from . import format_tags
from . import ps_resource_scope
from .generator import SlotPlan, SlotStyleDegrade

_SECTION_RE = re.compile(r'^\[([^\]]+)\]\s*$')
_COMP_ID_RE = re.compile(r'component\s*(\d+)\s*$', re.I)
_RANGE_SECTION_RE = re.compile(r'^textureoverridecomponent(\d+)$')
_FIRST_INDEX_RE = re.compile(r'^match_first_index\s*=\s*(\d+)\s*$', re.I)
_INDEX_COUNT_RE = re.compile(r'^match_index_count\s*=\s*(\d+)\s*$', re.I)

_TRIGGER_LINE = 'run = commandlisttriggerresourceoverrides'
_CLEANUP_LINE = 'run = commandlistcleanupsharedresources'
_CONSTANTS_SECTION = 'constants'
_PRESENT_SECTION = 'present'


def _section_spans(lines: List[str]) -> List[Tuple[str, int, int]]:
    """Returns (section name, header index, end index exclusive) spans."""
    headers = [(m.group(1), i) for i, line in enumerate(lines)
               if (m := _SECTION_RE.match(line))]
    spans = []
    for pos, (name, start) in enumerate(headers):
        end = headers[pos + 1][1] if pos + 1 < len(headers) else len(lines)
        spans.append((name, start, end))
    return spans


def extract_component_ranges(ini_text: str) -> Dict[int, Tuple[int, int]]:
    """Reads each [TextureOverrideComponent{N}] section's
    (match_first_index, match_index_count) from the rendered ini — the ranges
    the fuzzy format tag sections are scoped to (XQFA-style)."""
    lines = ini_text.split('\n')
    ranges: Dict[int, Tuple[int, int]] = {}
    for name, start, end in _section_spans(lines):
        m = _RANGE_SECTION_RE.match(name.strip().lower())
        if not m:
            continue
        comp_id = int(m.group(1))
        first = count = None
        for i in range(start + 1, end):
            stripped = lines[i].strip()
            if (fm := _FIRST_INDEX_RE.match(stripped)):
                first = int(fm.group(1))
            elif (cm := _INDEX_COUNT_RE.match(stripped)):
                count = int(cm.group(1))
        if first is not None and count is not None:
            ranges[comp_id] = (first, count)
    return ranges


def apply(ini_text: str, plan: SlotPlan) -> str:
    lines = ini_text.split('\n')
    spans = _section_spans(lines)
    if not spans:
        raise SlotStyleDegrade('rendered ini has no sections')

    component_fallbacks = getattr(plan, 'component_hash_fallbacks', None) or {}
    fallback_by_section = {}
    fallback_components = set()
    for entries in component_fallbacks.values():
        for entry in entries:
            section = str(getattr(entry, 'section', '') or '').strip().lower()
            if not section:
                continue
            fallback_by_section.setdefault(section, []).append(entry)
            fallback_components.add(int(getattr(entry, 'component_id')))

    drop_indices = (set(plan.covered_resource_indices)
                    | set(getattr(plan, 'phantom_only_resource_indices', set())))
    drop_sections = {f'textureoverridetexture{i}' for i in drop_indices}
    drop_sections.difference_update(fallback_by_section)

    deleted: Set[int] = set()
    # after-insertions keyed by line index -> list of new lines, before- likewise.
    insert_after: Dict[int, List[str]] = {}
    insert_before: Dict[int, List[str]] = {}
    replace_line: Dict[int, List[str]] = {}

    removed_sections = set()
    injected_components = set()
    found_constants = False
    found_present = False

    for name, start, end in spans:
        lname = name.strip().lower()

        if lname in drop_sections:
            deleted.update(range(start, end))
            removed_sections.add(lname)
            continue

        if lname == _CONSTANTS_SECTION:
            found_constants = True
            globals_to_add = []
            if plan.multi_form:
                globals_to_add.append(
                    f'global {constants.VAR_FORM} = {plan.default_form_id}')
            globals_to_add.extend(f'global {var} = 0'
                                  for var in plan.extra_globals)
            for comp_id in sorted(fallback_components):
                globals_to_add.append(
                    f'global {constants.COMPONENT_HASH_FALLBACK_VAR.format(component_id=comp_id)} = 0')
            if globals_to_add:
                insert_after.setdefault(start, []).extend(globals_to_add)
            continue

        if lname == _PRESENT_SECTION:
            found_present = True
            if plan.watchdog_lines:
                # Append at the end of the stock per-frame logic (after the
                # last non-blank line, before the inter-section gap).
                last = start
                for i in range(start + 1, end):
                    if lines[i].strip():
                        last = i
                insert_after.setdefault(last, []).extend(plan.watchdog_lines)
            continue

        if lname in fallback_by_section:
            entries = sorted(fallback_by_section[lname],
                             key=lambda e: int(getattr(e, 'component_id')))
            gates = [
                f'{constants.COMPONENT_HASH_FALLBACK_VAR.format(component_id=int(getattr(entry, "component_id")))} == 1'
                for entry in entries
            ]
            gate = ' || '.join(gates)
            comments = [
                '; component_scoped_hash_fallback = 1',
                '; fallback_component = ' + ','.join(
                    str(int(getattr(entry, 'component_id'))) for entry in entries),
            ]
            insert_after.setdefault(start, []).extend(comments)
            for i in range(start + 1, end):
                line = lines[i]
                if re.match(r'\s*this\s*=\s*ResourceTexture\d+\b', line, re.I):
                    indent = line[:len(line) - len(line.lstrip())]
                    replace_line[i] = [
                        f'{indent}if {gate}',
                        f'{indent}    {line.strip()}',
                        f'{indent}endif',
                    ]
            continue

        comp_match = _COMP_ID_RE.search(name.strip())
        if not comp_match:
            continue
        comp_id = int(comp_match.group(1))
        list_name = plan.component_list_names.get(comp_id)
        has_component_fallback = comp_id in fallback_components
        if list_name is None and not has_component_fallback:
            continue

        trigger_indices = []
        for i in range(start + 1, end):
            stripped = lines[i].strip()
            if stripped.startswith(';'):
                continue
            low = stripped.lower()
            if low == _TRIGGER_LINE:
                trigger_indices.append(i)
        if not trigger_indices:
            continue
        for i in trigger_indices:
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            if has_component_fallback:
                var = constants.COMPONENT_HASH_FALLBACK_VAR.format(component_id=comp_id)
                cleanup_index = None
                for candidate in range(i + 1, end):
                    candidate_line = lines[candidate].strip()
                    if candidate_line.startswith(';'):
                        continue
                    candidate_low = candidate_line.lower()
                    if candidate_low == _TRIGGER_LINE:
                        break
                    if candidate_low == _CLEANUP_LINE:
                        cleanup_index = candidate
                        break
                if cleanup_index is None:
                    raise SlotStyleDegrade(
                        f'component {comp_id} hash fallback transaction has no '
                        'matching cleanup anchor')
                insert_before.setdefault(i, []).append(f'{indent}{var} = 1')
                cleanup_indent = lines[cleanup_index][
                    :len(lines[cleanup_index])
                    - len(lines[cleanup_index].lstrip())]
                insert_after.setdefault(cleanup_index, []).append(
                    f'{cleanup_indent}{var} = 0')
            if list_name is not None:
                insert_after.setdefault(i, []).append(f'{indent}run = {list_name}')
        injected_components.add(comp_id)

    required_components = set(plan.component_list_names) | fallback_components
    missing_components = required_components - injected_components
    if missing_components:
        raise SlotStyleDegrade(
            'no component draw anchor found for planned Component(s): '
            + ', '.join(str(comp_id) for comp_id in sorted(missing_components)))
    if not injected_components:
        raise SlotStyleDegrade(
            'no component draw anchors found in the rendered ini - unknown '
            'template structure')
    if plan.multi_form and not found_constants:
        raise SlotStyleDegrade(
            f'[Constants] section not found for {constants.VAR_FORM}')
    if fallback_components and not found_constants:
        raise SlotStyleDegrade(
            '[Constants] section not found for component-scoped hash fallback')

    out: List[str] = []
    for i, line in enumerate(lines):
        if i in insert_before:
            out.extend(insert_before[i])
        if i not in deleted:
            if i in replace_line:
                out.extend(replace_line[i])
            else:
                out.append(line)
        if i in insert_after:
            out.extend(insert_after[i])

    result = '\n'.join(out)
    if not result.endswith('\n'):
        result += '\n'
    if plan.watchdog_lines and not found_present:
        # Template has no [Present]: create one (no duplicate can exist).
        result += '\n[Present]\n' + '\n'.join(plan.watchdog_lines) + '\n'
    result += plan.block_text
    if not result.endswith('\n'):
        result += '\n'
    result, format_stats = format_tags.dedupe_format_tag_sections(result)
    try:
        result = ps_resource_scope.apply_ps_resource_scope(
            result, getattr(plan, 'restore_contract', None) or {})
    except ValueError as exc:
        raise SlotStyleDegrade(str(exc)) from exc
    plan.format_diagnostics = format_stats
    plan.stats.update({
        key: value for key, value in format_stats.items()
        if key != 'format_sections_summary'
    })
    return result
