# Structural transformation of a rendered WWMI mod.ini into slot-style form
# (pure python, no bpy — unit-testable headless).
#
# Anchor-based and template-agnostic on purpose: it operates on the RENDERED
# ini, so the stock merged template, the velo LOD fork templates (whose shared
# [CommandListDrawComponent{c}] lists carry the same trigger/cleanup anchors)
# and future templates are all covered without forking any of them:
#
#   1. [TextureOverrideTexture{i}] hash sections whose texture is covered by
#      the slot maps are removed ([ResourceTexture{i}] filename sections stay;
#      blind-zone textures keep their stock hash section as a fallback).
#      The per-slot `CheckTextureOverride = ps-tN` trigger lines always stay:
#      v3 conditions read the filter_index of bound slots, which is exactly
#      what those checks resolve.
#   2. Every non-comment `run = CommandListTriggerResourceOverrides` line that
#      lives in a section whose name carries a component id gets
#      `run = CommandListSetTexturesComponent{id}` injected right after it,
#      and the matching non-comment `run = CommandListCleanupSharedResources`
#      in the same section gets `run = CommandListRestoreTextures` injected
#      right before it (set/restore always paired - a missing cleanup anchor
#      aborts).
#   3. Multi-form only: `global $form_id = 1` is added to [Constants]
#      (1 = base form; form markers latch it to the active form at runtime).
#   4. The generated slot-style block is appended at the end.
#
# Any missing anchor raises SlotStyleDegrade: the caller falls back to the
# untouched stock output (fail-safe, never a half-transformed ini).

import re

from typing import Dict, List, Optional, Set, Tuple

from . import constants
from .generator import SlotPlan, SlotStyleDegrade

_SECTION_RE = re.compile(r'^\[([^\]]+)\]\s*$')
_COMP_ID_RE = re.compile(r'component\s*(\d+)\s*$', re.I)
_RANGE_SECTION_RE = re.compile(r'^textureoverridecomponent(\d+)$')
_FIRST_INDEX_RE = re.compile(r'^match_first_index\s*=\s*(\d+)\s*$', re.I)
_INDEX_COUNT_RE = re.compile(r'^match_index_count\s*=\s*(\d+)\s*$', re.I)

_TRIGGER_LINE = 'run = commandlisttriggerresourceoverrides'
_CLEANUP_LINE = 'run = commandlistcleanupsharedresources'
_CONSTANTS_SECTION = 'constants'


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

    drop_sections = {f'textureoverridetexture{i}'
                     for i in plan.covered_resource_indices}

    deleted: Set[int] = set()
    # after-insertions keyed by line index -> list of new lines, before- likewise.
    insert_after: Dict[int, List[str]] = {}
    insert_before: Dict[int, List[str]] = {}

    removed_sections = set()
    injected_components = set()
    found_constants = False

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
                globals_to_add.append(f'global {constants.VAR_FORM} = 1')
            globals_to_add.extend(f'global {var} = 0'
                                  for var in plan.extra_globals)
            if globals_to_add:
                insert_after.setdefault(start, []).extend(globals_to_add)
            continue

        comp_match = _COMP_ID_RE.search(name.strip())
        if not comp_match:
            continue
        comp_id = int(comp_match.group(1))
        list_name = plan.component_list_names.get(comp_id)
        if list_name is None:
            continue

        trigger_indices = []
        cleanup_indices = []
        for i in range(start + 1, end):
            stripped = lines[i].strip()
            if stripped.startswith(';'):
                continue
            low = stripped.lower()
            if low == _TRIGGER_LINE:
                trigger_indices.append(i)
            elif low == _CLEANUP_LINE:
                cleanup_indices.append(i)
        if not trigger_indices:
            continue
        if len(cleanup_indices) < len(trigger_indices):
            raise SlotStyleDegrade(
                f'section [{name}] has a resource-override trigger without a '
                f'matching cleanup anchor - unknown template structure')
        probe_name = plan.probe_list_names.get(comp_id)
        for i in trigger_indices:
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            if probe_name is not None:
                # Probes run BEFORE the stock trigger: the FIRST check of a
                # resource is ours, so the probe-key bracketing cannot be
                # starved by a once-per-draw check cache.
                insert_before.setdefault(i, []).append(f'{indent}run = {probe_name}')
            insert_after.setdefault(i, []).append(f'{indent}run = {list_name}')
        for i in cleanup_indices:
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            insert_before.setdefault(i, []).append(
                f'{indent}run = {constants.CMDLIST_RESTORE}')
        injected_components.add(comp_id)

    if not injected_components:
        raise SlotStyleDegrade(
            'no component draw anchors found in the rendered ini - unknown '
            'template structure')
    missing = drop_sections - removed_sections
    if missing:
        raise SlotStyleDegrade(
            f'expected stock texture sections not found: {sorted(missing)}')
    if plan.multi_form and not found_constants:
        raise SlotStyleDegrade(
            f'[Constants] section not found for {constants.VAR_FORM}')

    out: List[str] = []
    for i, line in enumerate(lines):
        if i in insert_before:
            out.extend(insert_before[i])
        if i not in deleted:
            out.append(line)
        if i in insert_after:
            out.extend(insert_after[i])

    result = '\n'.join(out)
    if not result.endswith('\n'):
        result += '\n'
    result += plan.block_text
    if not result.endswith('\n'):
        result += '\n'
    return result
