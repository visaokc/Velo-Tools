"""Pixel-shader resource backup/restore wrapper for slot-style texture lists."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_RUN_RE = re.compile(r"^(\s*)run\s*=\s*([A-Za-z0-9_]+)\s*$", re.I)
_TRIGGER_RE = re.compile(
    r"^CommandListTriggerResourceOverrides((?:_ib\d+)*)$", re.I)
_SETTER_RE = re.compile(
    r"^(CommandListSetTexturesComponent\d+"
    r"(?:Route(?:Base|[0-9a-f]{8}))?)((?:_ib\d+)*)$", re.I)
_RESTORE_RE = re.compile(
    r"^CommandListRestorePixelShaderResources(?:ExceptT([0-8]))?"
    r"((?:_ib\d+)*)$",
    re.I,
)


def _section_spans(lines: Sequence[str]) -> List[Tuple[str, int, int]]:
    headers = [
        (match.group(1), index)
        for index, line in enumerate(lines)
        if (match := _SECTION_RE.match(line))
    ]
    spans: List[Tuple[str, int, int]] = []
    for pos, (name, start) in enumerate(headers):
        end = headers[pos + 1][1] if pos + 1 < len(headers) else len(lines)
        spans.append((name, start, end))
    return spans


def _trigger_suffix(command: str) -> Optional[str]:
    match = _TRIGGER_RE.fullmatch(command)
    if not match:
        return None
    return match.group(1) or ""


def _backup_name(suffix: str) -> str:
    return f"CommandListBackupPixelShaderResources{suffix}"


def _restore_name(suffix: str, persistent_slot: Optional[int] = None) -> str:
    qualifier = (f"ExceptT{persistent_slot}"
                 if persistent_slot is not None else "")
    return f"CommandListRestorePixelShaderResources{qualifier}{suffix}"


def _trigger_name(suffix: str) -> str:
    return f"CommandListTriggerResourceOverrides{suffix}"


def _cleanup_name(suffix: str) -> str:
    return f"CommandListCleanupSharedResources{suffix}"


def _bypass_name(slot: int, suffix: str) -> str:
    return f"ResourceBypassPST{slot}{suffix}"


def _run_command(line: str) -> Optional[Tuple[str, str]]:
    match = _RUN_RE.match(line)
    if not match:
        return None
    return match.group(1), match.group(2)


def _command_equals(line: str, command: str) -> bool:
    parsed = _run_command(line.strip())
    return parsed is not None and parsed[1].lower() == command.lower()


def _find_next_run(lines: Sequence[str], start: int, stop: int,
                   command: str) -> Optional[int]:
    for index in range(start, stop):
        if _command_equals(lines[index], command):
            return index
    return None


def _next_trigger(lines: Sequence[str], start: int, stop: int,
                  suffix: str) -> int:
    index = _find_next_run(lines, start, stop, _trigger_name(suffix))
    return index if index is not None else stop


def _restore_parts(command: str) -> Optional[Tuple[Optional[int], str]]:
    match = _RESTORE_RE.fullmatch(command)
    if not match:
        return None
    slot = int(match.group(1)) if match.group(1) is not None else None
    return slot, match.group(2) or ""


def _setter_parts(command: str) -> Optional[Tuple[str, str]]:
    match = _SETTER_RE.fullmatch(command)
    if not match:
        return None
    return match.group(1), match.group(2) or ""


def _contract_map(contract: object) -> Dict[str, object]:
    if not isinstance(contract, dict):
        return {}
    nested = contract.get("restore_contract")
    if isinstance(nested, dict):
        return nested
    return contract


def _policy_slot(policy: object) -> Optional[int]:
    if not isinstance(policy, dict):
        return None
    if str(policy.get("mode") or "").strip().lower() != "except":
        return None
    try:
        slot = int(policy.get("persistent_slot"))
    except (TypeError, ValueError):
        return None
    return slot if 0 <= slot <= 8 else None


def _transaction_policy(lines: Sequence[str], trigger: int, cleanup: int,
                        suffix: str, contract: object) -> Optional[int]:
    setters = {
        parsed[1]
        for index in range(trigger + 1, cleanup)
        if (parsed := _run_command(lines[index].strip()))
        if _setter_parts(parsed[1]) is not None
    }
    if len(setters) != 1:
        return None
    setter = next(iter(setters))
    parts = _setter_parts(setter)
    if parts is None or parts[1].lower() != suffix.lower():
        return None
    policies = _contract_map(contract)
    policy = policies.get(setter)
    if policy is None:
        lower_setter = setter.lower()
        policy = next(
            (value for name, value in policies.items()
             if str(name).lower() == lower_setter),
            None,
        )
    return _policy_slot(policy)


def _transaction_setters(lines: Sequence[str], trigger: int, cleanup: int):
    return [
        parsed[1]
        for index in range(trigger + 1, cleanup)
        if (parsed := _run_command(lines[index].strip()))
        if _setter_parts(parsed[1]) is not None
    ]


def _transactions(lines: Sequence[str], start: int, end: int):
    previous_cleanup: Dict[str, int] = {}
    for index in range(start + 1, end):
        parsed = _run_command(lines[index].strip())
        if not parsed:
            continue
        suffix = _trigger_suffix(parsed[1])
        if suffix is None:
            continue
        limit = _next_trigger(lines, index + 1, end, suffix)
        cleanup = _find_next_run(
            lines, index + 1, limit, _cleanup_name(suffix))
        boundary = previous_cleanup.get(suffix, start) + 1
        if cleanup is not None:
            previous_cleanup[suffix] = cleanup
        yield index, cleanup, limit, boundary, suffix


def _support_bodies(
        restore_specs: Iterable[Tuple[str, Optional[int]]]
        ) -> Dict[str, List[str]]:
    specs = set(restore_specs)
    suffixes = {suffix for suffix, _slot in specs}
    bodies: Dict[str, List[str]] = {}
    for suffix in sorted(suffixes, key=lambda value: (value != "", value)):
        bodies[_backup_name(suffix)] = [
            f"{_bypass_name(slot, suffix)} = ref ps-t{slot}"
            for slot in range(9)
        ]
        policies = sorted(
            (slot for spec_suffix, slot in specs if spec_suffix == suffix),
            key=lambda slot: -1 if slot is None else slot,
        )
        for persistent_slot in policies:
            bodies[_restore_name(suffix, persistent_slot)] = [
                f"ps-t{slot} = ref {_bypass_name(slot, suffix)}"
                for slot in range(9)
                if slot != persistent_slot
            ]
    return bodies


def _ensure_support_sections(
        lines: List[str],
        restore_specs: Iterable[Tuple[str, Optional[int]]]) -> None:
    specs = set(restore_specs)
    if not specs:
        return
    required_bodies = _support_bodies(specs)
    managed_suffixes = {suffix.lower() for suffix, _slot in specs}
    stale_spans = {
        start: end
        for name, start, end in _section_spans(lines)
        if (parts := _restore_parts(name)) is not None
        and parts[1].lower() in managed_suffixes
        and name not in required_bodies
    }
    if stale_spans:
        out: List[str] = []
        index = 0
        while index < len(lines):
            end = stale_spans.get(index)
            if end is None:
                out.append(lines[index])
                index += 1
            else:
                index = end
        lines[:] = out
    spans = _section_spans(lines)
    replacements = {
        start: (end, name, required_bodies[name])
        for name, start, end in spans
        if name in required_bodies
    }
    if replacements:
        out: List[str] = []
        index = 0
        while index < len(lines):
            replacement = replacements.get(index)
            if replacement is None:
                out.append(lines[index])
                index += 1
                continue
            end, name, body = replacement
            out.append(f"[{name}]")
            out.extend(body)
            out.append("")
            index = end
        lines[:] = out

    existing = {name for name, _start, _end in _section_spans(lines)}
    for name, body in required_bodies.items():
        if name in existing:
            continue
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{name}]")
        lines.extend(body)
        lines.append("")
        existing.add(name)

    suffixes = sorted(
        {suffix for suffix, _slot in specs},
        key=lambda value: (value != "", value),
    )
    for suffix in suffixes:
        for slot in range(9):
            resource = _bypass_name(slot, suffix)
            if resource in existing:
                continue
            if lines and lines[-1] != "":
                lines.append("")
            lines.extend([f"[{resource}]", ""])
            existing.add(resource)


def apply_ps_resource_scope(ini_text: str,
                            restore_contract: object = None) -> str:
    """Normalize each texture transaction to one scoped ps-t restore."""
    lines = ini_text.splitlines()
    if not lines:
        return ini_text

    remove_indices: Set[int] = set()
    insert_before: Dict[int, List[str]] = {}
    insert_after: Dict[int, List[str]] = {}
    restore_specs: Set[Tuple[str, Optional[int]]] = set()

    for name, start, end in _section_spans(lines):
        for trigger, cleanup, limit, boundary, suffix in _transactions(
                lines, start, end):
            if cleanup is None:
                raise ValueError(
                    f"{name} runs {_trigger_name(suffix)} without following "
                    f"{_cleanup_name(suffix)}")
            persistent_slot = _transaction_policy(
                lines, trigger, cleanup, suffix, restore_contract)
            restore_specs.add((suffix, persistent_slot))

            for index in range(boundary, trigger):
                if _command_equals(lines[index], _backup_name(suffix)):
                    remove_indices.add(index)
            trigger_indent = lines[trigger][
                :len(lines[trigger]) - len(lines[trigger].lstrip())]
            insert_before.setdefault(trigger, []).append(
                f"{trigger_indent}run = {_backup_name(suffix)}")

            for index in range(cleanup + 1, limit):
                parsed = _run_command(lines[index].strip())
                if not parsed:
                    continue
                restore = _restore_parts(parsed[1])
                if restore is not None and restore[1].lower() == suffix.lower():
                    remove_indices.add(index)
            cleanup_indent = lines[cleanup][
                :len(lines[cleanup]) - len(lines[cleanup].lstrip())]
            insert_after.setdefault(cleanup, []).append(
                f"{cleanup_indent}run = "
                f"{_restore_name(suffix, persistent_slot)}")

    out: List[str] = []
    for index, line in enumerate(lines):
        if index in insert_before:
            out.extend(dict.fromkeys(insert_before[index]))
        if index not in remove_indices:
            out.append(line)
        if index in insert_after:
            out.extend(dict.fromkeys(insert_after[index]))

    if not restore_specs:
        result = "\n".join(out)
        return result + ("\n" if ini_text.endswith("\n") else "")

    _ensure_support_sections(out, restore_specs)
    return "\n".join(out).rstrip() + "\n"


def audit_ps_resource_scope(ini_text: str,
                            restore_contract: object = None) -> List[str]:
    """Return errors for transactions not covered by one valid ps-t scope."""
    lines = ini_text.splitlines()
    spans = _section_spans(lines)
    section_bodies = {
        name: "\n".join(lines[start + 1:end])
        for name, start, end in spans
    }
    errors: List[str] = []
    restore_specs: Set[Tuple[str, Optional[int]]] = set()
    enforce_contract = restore_contract is not None

    for name, start, end in spans:
        for trigger, cleanup, limit, boundary, suffix in _transactions(
                lines, start, end):
            command = _trigger_name(suffix)
            if cleanup is None:
                errors.append(
                    f"{name} runs {command} without following "
                    f"{_cleanup_name(suffix)}")
                continue

            backups = [
                index for index in range(boundary, trigger)
                if _command_equals(lines[index], _backup_name(suffix))
            ]
            if len(backups) != 1:
                errors.append(
                    f"{name} runs {command} with {len(backups)} "
                    f"{_backup_name(suffix)} calls before the trigger")

            setters = _transaction_setters(lines, trigger, cleanup)
            unique_setters = list(dict.fromkeys(
                setter.lower() for setter in setters))
            if len(unique_setters) > 1:
                errors.append(
                    f"{name} runs multiple direct texture setters in one "
                    f"transaction: {', '.join(setters)}")
            elif setters:
                setter_parts = _setter_parts(setters[0])
                setter_suffix = setter_parts[1] if setter_parts else ""
                if setter_suffix.lower() != suffix.lower():
                    errors.append(
                        f"{name} trigger suffix {suffix or '<none>'} does not "
                        f"match setter {setters[0]}")

            expected_slot = _transaction_policy(
                lines, trigger, cleanup, suffix, restore_contract)
            restores: List[Tuple[str, Optional[int]]] = []
            for index in range(cleanup + 1, limit):
                parsed = _run_command(lines[index].strip())
                if not parsed:
                    continue
                restore = _restore_parts(parsed[1])
                if restore is None or restore[1].lower() != suffix.lower():
                    continue
                restores.append((parsed[1], restore[0]))
                restore_specs.add((suffix, restore[0]))
            if len(restores) != 1:
                errors.append(
                    f"{name} runs {command} with {len(restores)} ps-t "
                    f"restore calls after {_cleanup_name(suffix)}")
            elif enforce_contract and restores[0][1] != expected_slot:
                errors.append(
                    f"{name} restores with {restores[0][0]} instead of "
                    f"{_restore_name(suffix, expected_slot)}")
            if enforce_contract:
                restore_specs.add((suffix, expected_slot))

    required_bodies = _support_bodies(restore_specs)
    for restore_name, _expected_body in required_bodies.items():
        if restore_name not in section_bodies:
            errors.append(f"{restore_name} missing for slot-style ps-t scope")

    suffixes = sorted(
        {suffix for suffix, _slot in restore_specs},
        key=lambda value: (value != "", value),
    )
    for suffix in suffixes:
        backup = _backup_name(suffix)
        backup_body = section_bodies.get(backup, "")
        for slot in range(9):
            resource = _bypass_name(slot, suffix)
            if resource not in section_bodies:
                errors.append(f"{resource} missing for slot-style ps-t backup")
            if not re.search(
                    r"^\s*%s\s*=\s*ref\s+ps-t%d\s*$"
                    % (re.escape(resource), slot), backup_body, re.M):
                errors.append(f"{backup} does not backup ps-t{slot}")

    for suffix, persistent_slot in sorted(
            restore_specs,
            key=lambda item: (item[0] != "", item[0],
                              -1 if item[1] is None else item[1])):
        restore = _restore_name(suffix, persistent_slot)
        body = section_bodies.get(restore, "")
        for slot in range(9):
            resource = _bypass_name(slot, suffix)
            found = bool(re.search(
                r"^\s*ps-t%d\s*=\s*ref\s+%s\s*$"
                % (slot, re.escape(resource)), body, re.M))
            if slot == persistent_slot:
                if found:
                    errors.append(f"{restore} unexpectedly restores ps-t{slot}")
            elif not found:
                errors.append(f"{restore} does not restore ps-t{slot}")
    return errors
