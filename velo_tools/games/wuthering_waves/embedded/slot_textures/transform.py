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
#   2. The per-slot `CheckTextureOverride = ps-tN` trigger lines are dropped
#      from [CommandListTriggerResourceOverrides] (kept when a blind-zone
#      hash section still needs them).
#   3. Every non-comment `run = CommandListTriggerResourceOverrides` line that
#      lives in a section whose name carries a component id gets
#      `run = CommandList<prefix>TexturesC{id}` injected right after it, and
#      the matching non-comment `run = CommandListCleanupSharedResources` in
#      the same section gets `run = CommandList<prefix>Restore` injected right
#      before it (set/restore always paired - a missing cleanup anchor aborts).
#   4. Multi-form only: `global $velo_form = 0` is added to [Constants].
#   5. The generated slot-style block is appended at the end.
#
# Any missing anchor raises SlotStyleDegrade: the caller falls back to the
# untouched stock output (fail-safe, never a half-transformed ini).

import re

from typing import Dict, List, Optional, Set, Tuple

from . import constants
from .generator import SlotPlan, SlotStyleDegrade

_SECTION_RE = re.compile(r'^\[([^\]]+)\]\s*$')
_COMP_ID_RE = re.compile(r'component\s*(\d+)\s*$', re.I)
_CHECK_PS_T_RE = re.compile(r'^checktextureoverride\s*=\s*ps-t\d+$', re.I)

_TRIGGER_LINE = 'run = commandlisttriggerresourceoverrides'
_CLEANUP_LINE = 'run = commandlistcleanupsharedresources'
_TRIGGER_SECTION = 'commandlisttriggerresourceoverrides'
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


def apply(ini_text: str, plan: SlotPlan) -> str:
    lines = ini_text.split('\n')
    spans = _section_spans(lines)
    if not spans:
        raise SlotStyleDegrade('rendered ini has no sections')

    drop_sections = {f'textureoverridetexture{i}'
                     for i in plan.covered_resource_indices}
    keep_ps_t_checks = bool(plan.blind_zone)

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

        if lname == _TRIGGER_SECTION and not keep_ps_t_checks:
            for i in range(start + 1, end):
                if _CHECK_PS_T_RE.match(lines[i].strip()):
                    deleted.add(i)
            continue

        if lname == _CONSTANTS_SECTION:
            found_constants = True
            if plan.multi_form:
                insert_after.setdefault(start, []).append(
                    'global $velo_form = 0')
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
        for i in trigger_indices:
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            insert_after.setdefault(i, []).append(f'{indent}run = {list_name}')
        for i in cleanup_indices:
            indent = lines[i][:len(lines[i]) - len(lines[i].lstrip())]
            insert_before.setdefault(i, []).append(
                f'{indent}run = CommandList{constants.SECTION_PREFIX}Restore')
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
        raise SlotStyleDegrade('[Constants] section not found for $velo_form')

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
