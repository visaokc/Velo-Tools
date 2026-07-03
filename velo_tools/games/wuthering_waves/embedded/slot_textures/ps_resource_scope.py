"""Pixel-shader resource backup/restore wrapper for slot-style texture lists."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_RUN_RE = re.compile(r"^(\s*)run\s*=\s*([A-Za-z0-9_]+)\s*$")
_TRIGGER_RE = re.compile(r"^CommandListTriggerResourceOverrides((?:_ib\d+)*)$")


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


def _restore_name(suffix: str) -> str:
    return f"CommandListRestorePixelShaderResources{suffix}"


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


def _line_runs(line: str, command: str) -> bool:
    parsed = _run_command(line.strip())
    return parsed is not None and parsed[1] == command


def _find_previous_run(lines: Sequence[str], start: int, stop: int, command: str) -> Optional[int]:
    for index in range(stop - 1, start - 1, -1):
        if _line_runs(lines[index], command):
            return index
    return None


def _find_next_run(lines: Sequence[str], start: int, stop: int, command: str) -> Optional[int]:
    for index in range(start, stop):
        if _line_runs(lines[index], command):
            return index
    return None


def _has_run_between(lines: Sequence[str], start: int, stop: int, command: str) -> bool:
    return _find_next_run(lines, start, stop, command) is not None


def _next_transaction_start(lines: Sequence[str], start: int, stop: int, suffix: str) -> int:
    trigger = _find_next_run(lines, start, stop, _trigger_name(suffix))
    return trigger if trigger is not None else stop


def _split_lines(text: str) -> List[str]:
    return text.splitlines()


def _append_support_sections(lines: List[str], suffixes: Iterable[str]) -> None:
    existing = {
        name
        for name, _start, _end in _section_spans(lines)
    }
    for suffix in sorted(set(suffixes), key=lambda value: (value != "", value)):
        backup = _backup_name(suffix)
        restore = _restore_name(suffix)
        if backup not in existing:
            lines.extend(["", "", f"[{backup}]"])
            lines.extend(
                f"{_bypass_name(slot, suffix)} = ref ps-t{slot}"
                for slot in range(9)
            )
            existing.add(backup)
        if restore not in existing:
            lines.extend(["", "", f"[{restore}]"])
            lines.extend(
                f"ps-t{slot} = ref {_bypass_name(slot, suffix)}"
                for slot in range(9)
            )
            existing.add(restore)
        for slot in range(9):
            resource = _bypass_name(slot, suffix)
            if resource not in existing:
                lines.extend(["", "", f"[{resource}]"])
                existing.add(resource)


def _top_up_support_sections(lines: List[str], suffixes: Iterable[str]) -> None:
    spans = _section_spans(lines)
    section_index = {name: (start, end) for name, start, end in spans}
    insert_before: Dict[int, List[str]] = {}
    for suffix in sorted(set(suffixes), key=lambda value: (value != "", value)):
        backup = _backup_name(suffix)
        if backup in section_index:
            start, end = section_index[backup]
            body = "\n".join(lines[start + 1:end])
            missing = [
                f"{_bypass_name(slot, suffix)} = ref ps-t{slot}"
                for slot in range(9)
                if not re.search(
                    r"^\s*%s\s*=\s*ref\s+ps-t%d\s*$"
                    % (re.escape(_bypass_name(slot, suffix)), slot),
                    body,
                    re.M,
                )
            ]
            if missing:
                insert_before.setdefault(end, []).extend(missing)
        restore = _restore_name(suffix)
        if restore in section_index:
            start, end = section_index[restore]
            body = "\n".join(lines[start + 1:end])
            missing = [
                f"ps-t{slot} = ref {_bypass_name(slot, suffix)}"
                for slot in range(9)
                if not re.search(
                    r"^\s*ps-t%d\s*=\s*ref\s+%s\s*$"
                    % (slot, re.escape(_bypass_name(slot, suffix))),
                    body,
                    re.M,
                )
            ]
            if missing:
                insert_before.setdefault(end, []).extend(missing)
    if not insert_before:
        return
    out: List[str] = []
    for index, line in enumerate(lines):
        if index in insert_before:
            out.extend(insert_before[index])
        out.append(line)
    if len(lines) in insert_before:
        out.extend(insert_before[len(lines)])
    lines[:] = out


def apply_ps_resource_scope(ini_text: str) -> str:
    """Wrap every texture resource transaction with ps-t backup/restore."""

    lines = _split_lines(ini_text)
    if not lines:
        return ini_text
    spans = _section_spans(lines)
    insert_before: Dict[int, List[str]] = {}
    insert_after: Dict[int, List[str]] = {}
    suffixes_with_scope: Set[str] = set()

    for _name, start, end in spans:
        for index in range(start + 1, end):
            parsed = _run_command(lines[index].strip())
            if not parsed:
                continue
            _indent, command = parsed
            suffix = _trigger_suffix(command)
            if suffix is None:
                continue
            suffixes_with_scope.add(suffix)
            trigger = index
            cleanup = _find_next_run(lines, index + 1, end, _cleanup_name(suffix))
            if cleanup is None:
                continue
            last_restore = _find_previous_run(
                lines, start + 1, trigger, _restore_name(suffix))
            boundary = last_restore + 1 if last_restore is not None else start + 1
            if not _has_run_between(lines, boundary, trigger, _backup_name(suffix)):
                indent = lines[trigger][:len(lines[trigger]) - len(lines[trigger].lstrip())]
                insert_before.setdefault(trigger, []).append(
                    f"{indent}run = {_backup_name(suffix)}")
            restore_limit = _next_transaction_start(lines, cleanup + 1, end, suffix)
            if not _has_run_between(lines, cleanup + 1, restore_limit, _restore_name(suffix)):
                indent = lines[cleanup][:len(lines[cleanup]) - len(lines[cleanup].lstrip())]
                insert_after.setdefault(cleanup, []).append(
                    f"{indent}run = {_restore_name(suffix)}")

    out: List[str] = []
    for index, line in enumerate(lines):
        if index in insert_before:
            for insert in dict.fromkeys(insert_before[index]):
                if not out or out[-1] != insert:
                    out.append(insert)
        out.append(line)
        if index in insert_after:
            for insert in dict.fromkeys(insert_after[index]):
                if not out or out[-1] != insert:
                    out.append(insert)
    if not suffixes_with_scope:
        result = "\n".join(out)
        return result + ("\n" if ini_text.endswith("\n") else "")

    _append_support_sections(out, suffixes_with_scope)
    _top_up_support_sections(out, suffixes_with_scope)
    result = "\n".join(out).rstrip() + "\n"
    return result


def audit_ps_resource_scope(ini_text: str) -> List[str]:
    """Return errors for texture transactions not covered by ps-t restore scope."""

    lines = _split_lines(ini_text)
    spans = _section_spans(lines)
    section_bodies = {
        name: "\n".join(lines[start + 1:end])
        for name, start, end in spans
    }
    errors: List[str] = []
    suffixes_with_scope: Set[str] = set()

    for name, start, end in spans:
        for index in range(start + 1, end):
            parsed = _run_command(lines[index].strip())
            if not parsed:
                continue
            _indent, command = parsed
            suffix = _trigger_suffix(command)
            if suffix is None:
                continue
            suffixes_with_scope.add(suffix)
            trigger = index
            cleanup = _find_next_run(lines, index + 1, end, _cleanup_name(suffix))
            if cleanup is None:
                errors.append(
                    f"{name} runs {command} without following {_cleanup_name(suffix)}")
            last_restore = _find_previous_run(
                lines, start + 1, trigger, _restore_name(suffix))
            boundary = last_restore + 1 if last_restore is not None else start + 1
            if not _has_run_between(lines, boundary, trigger, _backup_name(suffix)):
                errors.append(
                    f"{name} runs {command} without {_backup_name(suffix)} "
                    f"before {_trigger_name(suffix)}")
            if cleanup is not None:
                restore_limit = _next_transaction_start(lines, cleanup + 1, end, suffix)
                if not _has_run_between(lines, cleanup + 1, restore_limit, _restore_name(suffix)):
                    errors.append(
                        f"{name} runs {command} without {_restore_name(suffix)} "
                        f"after {_cleanup_name(suffix)}")

    for suffix in sorted(suffixes_with_scope, key=lambda value: (value != "", value)):
        backup = _backup_name(suffix)
        restore = _restore_name(suffix)
        if backup not in section_bodies:
            errors.append(f"{backup} missing for slot-style ps-t backup")
        if restore not in section_bodies:
            errors.append(f"{restore} missing for slot-style ps-t restore")
        backup_body = section_bodies.get(backup, "")
        restore_body = section_bodies.get(restore, "")
        for slot in range(9):
            resource = _bypass_name(slot, suffix)
            if resource not in section_bodies:
                errors.append(f"{resource} missing for slot-style ps-t backup")
            if backup_body and not re.search(
                    r"^\s*%s\s*=\s*ref\s+ps-t%d\s*$"
                    % (re.escape(resource), slot),
                    backup_body,
                    re.M):
                errors.append(f"{backup} does not backup ps-t{slot}")
            if restore_body and not re.search(
                    r"^\s*ps-t%d\s*=\s*ref\s+%s\s*$"
                    % (slot, re.escape(resource)),
                    restore_body,
                    re.M):
                errors.append(f"{restore} does not restore ps-t{slot}")
    return errors
