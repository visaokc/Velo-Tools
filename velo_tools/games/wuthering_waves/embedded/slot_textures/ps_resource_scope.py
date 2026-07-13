"""Pixel-shader resource backup/restore wrapper for slot-style texture lists."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_RUN_RE = re.compile(r"^(\s*)run\s*=\s*([A-Za-z0-9_]+)\s*$", re.I)
_TRIGGER_RE = re.compile(
    r"^CommandListTriggerResourceOverrides((?:_ib\d+)*)$", re.I)
_CLEANUP_RE = re.compile(
    r"^CommandListCleanupSharedResources((?:_ib\d+)*)$", re.I)
_SETTER_RE = re.compile(
    r"^(CommandListSetTexturesComponent\d+"
    r"(?:Route(?:Base|[0-9a-f]{8}))?)((?:_ib\d+)*)$", re.I)
_RESTORE_RE = re.compile(
    r"^CommandListRestorePixelShaderResources(?:ExceptT([0-8]))?"
    r"((?:_ib\d+)*)$",
    re.I,
)
_BACKUP_RE = re.compile(
    r"^CommandListBackupPixelShaderResources((?:_ib\d+)*)$", re.I)
_BYPASS_RE = re.compile(
    r"^ResourceBypassPST([0-8])((?:_ib\d+)*)$", re.I)
_BYPASS_COMMENT_RE = re.compile(
    r"^\s*;\s*(?:Temporary PS texture backup reference|"
    r"Runtime ps-t0\.\.8 backup handles)\b",
    re.I,
)
_SUPPORT_PASSTHROUGH_COMMENT_RE = re.compile(
    r"^\s*;\s*SHA256 CHECKSUM:\s*[0-9a-f]{64}\s*$",
    re.I,
)
_BYPASS_GROUP_COMMENT = (
    "; Runtime ps-t0..8 backup handles for this IB; intentionally empty."
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
    return (match.group(1) or "").casefold()


def _cleanup_suffix(command: str) -> Optional[str]:
    match = _CLEANUP_RE.fullmatch(command)
    if not match:
        return None
    return (match.group(1) or "").casefold()


def _backup_name(suffix: str) -> str:
    return f"CommandListBackupPixelShaderResources{suffix}"


def _backup_suffix(command: str) -> Optional[str]:
    match = _BACKUP_RE.fullmatch(command)
    if not match:
        return None
    return (match.group(1) or "").casefold()


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


def _bypass_parts(resource: str) -> Optional[Tuple[int, str]]:
    match = _BYPASS_RE.fullmatch(resource)
    if not match:
        return None
    return int(match.group(1)), (match.group(2) or "").casefold()


def _run_command(line: str) -> Optional[Tuple[str, str]]:
    match = _RUN_RE.match(line)
    if not match:
        return None
    return match.group(1), match.group(2)


def _command_equals(line: str, command: str) -> bool:
    parsed = _run_command(line.strip())
    return parsed is not None and parsed[1].casefold() == command.casefold()


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
    return slot, (match.group(2) or "").casefold()


def _setter_parts(command: str) -> Optional[Tuple[str, str]]:
    match = _SETTER_RE.fullmatch(command)
    if not match:
        return None
    return match.group(1), (match.group(2) or "").casefold()


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
    if str(policy.get("mode") or "").strip().casefold() != "except":
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
    if parts is None or parts[1].casefold() != suffix.casefold():
        return None
    policies = _contract_map(contract)
    policy = policies.get(setter)
    if policy is None:
        lower_setter = setter.casefold()
        policy = next(
            (value for name, value in policies.items()
             if str(name).casefold() == lower_setter),
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
        trigger_suffix = _trigger_suffix(parsed[1])
        if trigger_suffix is None:
            continue
        limit = _next_trigger(lines, index + 1, end, trigger_suffix)
        suffix = trigger_suffix
        cleanup = _find_next_run(
            lines, index + 1, limit, _cleanup_name(suffix))
        if cleanup is None and trigger_suffix == "":
            scoped_cleanups = []
            for candidate in range(index + 1, limit):
                candidate_run = _run_command(lines[candidate].strip())
                if candidate_run is None:
                    continue
                candidate_suffix = _cleanup_suffix(candidate_run[1])
                if candidate_suffix:
                    scoped_cleanups.append((candidate, candidate_suffix))
            if len(scoped_cleanups) == 1:
                cleanup, suffix = scoped_cleanups[0]
        boundary = previous_cleanup.get(suffix, start) + 1
        if cleanup is not None:
            previous_cleanup[suffix] = cleanup
        yield index, cleanup, limit, boundary, suffix


def _support_bodies(
        restore_specs: Iterable[Tuple[str, Optional[int]]]
        ) -> Dict[str, List[str]]:
    specs = {
        (str(suffix).casefold(), persistent_slot)
        for suffix, persistent_slot in restore_specs
    }
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


def _support_body_lines(lines: Sequence[str], start: int,
                        end: int) -> List[str]:
    return [
        stripped.casefold()
        for line in lines[start + 1:end]
        if (stripped := line.strip()) and not stripped.startswith(";")
    ]


def _trailing_prefix_start(lines: Sequence[str], start: int, end: int) -> int:
    for separator in range(start + 1, end):
        if lines[separator].strip():
            continue
        prefix = separator + 1
        while prefix < end and not lines[prefix].strip():
            prefix += 1
        if prefix < end and all(
                not line.strip() or line.lstrip().startswith(";")
                for line in lines[prefix:end]):
            return separator
    return end


def _unknown_owned_comments(lines: Sequence[str], start: int,
                            end: int) -> List[str]:
    owned_end = _trailing_prefix_start(lines, start, end)
    return [
        line
        for line in lines[start + 1:owned_end]
        if line.lstrip().startswith(";")
        and not _BYPASS_COMMENT_RE.match(line)
        and not _SUPPORT_PASSTHROUGH_COMMENT_RE.match(line)
    ]


def _generated_support_groups(
        lines: Sequence[str]) -> Dict[str, Dict[str, object]]:
    groups: Dict[str, Dict[str, object]] = {}
    for name, start, end in _section_spans(lines):
        suffix = _backup_suffix(name)
        kind = "backup"
        detail: object = None
        if suffix is None:
            restore = _restore_parts(name)
            if restore is not None:
                detail, suffix = restore
                kind = "restore"
            else:
                bypass = _bypass_parts(name)
                if bypass is None:
                    continue
                detail, suffix = bypass
                kind = "bypass"
        key = suffix.casefold()
        group = groups.setdefault(key, {
            "suffix": suffix,
            "backup": [],
            "restore": [],
            "bypass": [],
        })
        group[kind].append((name, start, end, detail))

    generated_groups: Dict[str, Dict[str, object]] = {}
    for key, group in groups.items():
        suffix = str(group["suffix"])
        backups = list(group["backup"])
        restores = list(group["restore"])
        bypasses = list(group["bypass"])
        if len(backups) != 1 or not restores or len(bypasses) != 9:
            continue
        slots = [int(entry[3]) for entry in bypasses]
        if set(slots) != set(range(9)) or len(set(slots)) != 9:
            continue

        backup_expected = [
            line.casefold()
            for line in _support_bodies({(suffix, None)})[
                _backup_name(suffix)]
        ]
        if _support_body_lines(lines, backups[0][1], backups[0][2]) \
                != backup_expected:
            continue

        generated = True
        for name, start, end, persistent_slot in restores:
            expected = [
                line.casefold()
                for line in _support_bodies({(suffix, persistent_slot)})[
                    _restore_name(suffix, persistent_slot)]
            ]
            if _support_body_lines(lines, start, end) != expected:
                generated = False
                break
        if not generated or any(
                _support_body_lines(lines, start, end)
                for _name, start, end, _slot in bypasses):
            continue

        if any(
                _unknown_owned_comments(lines, start, end)
                for _name, start, end, _detail
                in backups + restores + bypasses):
            continue

        group["entries"] = backups + restores + bypasses
        generated_groups[key] = group
    return generated_groups


def _remove_orphan_generated_support(
        lines: List[str], active_suffixes: Iterable[str]) -> None:
    active = {suffix.casefold() for suffix in active_suffixes}
    remove_spans: Dict[int, Tuple[int, List[str]]] = {}
    for key, group in _generated_support_groups(lines).items():
        if key in active:
            continue
        entries = list(group["entries"])

        owned_indices = {
            index
            for _name, start, end, _detail in entries
            for index in range(start, end)
        }
        names = [name.casefold() for name, *_rest in entries]
        externally_referenced = any(
            any(re.search(
                r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])"
                % re.escape(name), line, re.I)
                for name in names)
            for index, line in enumerate(lines)
            if index not in owned_indices
        )
        if externally_referenced:
            continue
        for _name, start, end, _detail in entries:
            prefix_start = _trailing_prefix_start(lines, start, end)
            remove_spans[start] = (end, list(lines[prefix_start:end]))

    if not remove_spans:
        return
    out: List[str] = []
    index = 0
    while index < len(lines):
        replacement = remove_spans.get(index)
        if replacement is None:
            out.append(lines[index])
            index += 1
        else:
            end, preserved_prefix = replacement
            out.extend(preserved_prefix)
            index = end
    lines[:] = out


def _ensure_bypass_comments(lines: List[str], suffixes: Iterable[str]) -> None:
    managed_suffixes = {suffix.casefold() for suffix in suffixes}
    remove_indices: Set[int] = set()
    for name, start, end in _section_spans(lines):
        parts = _bypass_parts(name)
        if parts is None or parts[1].casefold() not in managed_suffixes:
            continue
        owned_end = _trailing_prefix_start(lines, start, end)
        remove_indices.update(
            index for index in range(start + 1, owned_end)
            if _BYPASS_COMMENT_RE.match(lines[index])
        )
    if remove_indices:
        lines[:] = [
            line for index, line in enumerate(lines)
            if index not in remove_indices
        ]

    insert_after: List[int] = []
    for name, start, end in _section_spans(lines):
        parts = _bypass_parts(name)
        if (parts is None or parts[0] != 0
                or parts[1].casefold() not in managed_suffixes):
            continue
        insert_after.append(start)
    for start in reversed(insert_after):
        lines.insert(start + 1, _BYPASS_GROUP_COMMENT)


def _ensure_support_sections(
        lines: List[str],
        restore_specs: Iterable[Tuple[str, Optional[int]]]) -> None:
    specs = {
        (str(suffix).casefold(), persistent_slot)
        for suffix, persistent_slot in restore_specs
    }
    if not specs:
        return
    required_bodies = _support_bodies(specs)
    required_by_key = {
        name.casefold(): (name, body)
        for name, body in required_bodies.items()
    }
    managed_suffixes = {suffix.casefold() for suffix, _slot in specs}
    support_entries: Dict[str, List[str]] = {}
    for name, _start, _end in _section_spans(lines):
        if (_backup_suffix(name) is not None
                or _restore_parts(name) is not None
                or _bypass_parts(name) is not None):
            support_entries.setdefault(name.casefold(), []).append(name)
    duplicates = {
        key: names for key, names in support_entries.items()
        if len(names) > 1
    }
    if duplicates:
        names = next(iter(duplicates.values()))
        raise ValueError(
            "duplicate ps-t support section header (case-insensitive): "
            + " / ".join(f"[{name}]" for name in names)
        )

    stale_spans: Dict[int, int] = {}
    for name, start, end in _section_spans(lines):
        parts = _restore_parts(name)
        if (parts is None
                or parts[1].casefold() not in managed_suffixes
                or name.casefold() in required_by_key
                or _unknown_owned_comments(lines, start, end)):
            continue
        stale_spans[start] = _trailing_prefix_start(lines, start, end)
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
    replacements: Dict[int, Tuple[int, int, str, List[str]]] = {}
    for name, start, end in spans:
        required = required_by_key.get(name.casefold())
        if required is None:
            continue
        canonical_name, body = required
        if _unknown_owned_comments(lines, start, end):
            lines[start] = f"[{canonical_name}]"
            continue
        rewrite_end = _trailing_prefix_start(lines, start, end)
        replacements[start] = (
            rewrite_end, end, canonical_name, body)
    if replacements:
        out: List[str] = []
        index = 0
        while index < len(lines):
            replacement = replacements.get(index)
            if replacement is None:
                out.append(lines[index])
                index += 1
                continue
            rewrite_end, section_end, name, body = replacement
            out.append(f"[{name}]")
            out.extend(body)
            if rewrite_end == section_end:
                out.append("")
            index = rewrite_end
        lines[:] = out

    canonical_headers = {
        start: _bypass_name(parts[0], parts[1])
        for name, start, _end in _section_spans(lines)
        if (parts := _bypass_parts(name)) is not None
        and parts[1].casefold() in managed_suffixes
    }
    for start, canonical_name in canonical_headers.items():
        lines[start] = f"[{canonical_name}]"

    existing = {
        name.casefold() for name, _start, _end in _section_spans(lines)
    }
    for name, body in required_bodies.items():
        key = name.casefold()
        if key in existing:
            continue
        if lines and lines[-1] != "":
            lines.append("")
        lines.append(f"[{name}]")
        lines.extend(body)
        lines.append("")
        existing.add(key)

    suffixes = sorted(
        {suffix for suffix, _slot in specs},
        key=lambda value: (value != "", value),
    )
    for suffix in suffixes:
        for slot in range(9):
            resource = _bypass_name(slot, suffix)
            key = resource.casefold()
            if key in existing:
                continue
            if lines and lines[-1] != "":
                lines.append("")
            lines.extend([f"[{resource}]", ""])
            existing.add(key)
    _ensure_bypass_comments(lines, suffixes)


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
                if (restore is not None
                        and restore[1].casefold() == suffix.casefold()):
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

    _remove_orphan_generated_support(
        out, (suffix for suffix, _slot in restore_specs))
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
    errors: List[str] = []
    section_bodies: Dict[str, str] = {}
    section_names: Dict[str, str] = {}
    for name, start, end in spans:
        key = name.casefold()
        is_support = (
            _backup_suffix(name) is not None
            or _restore_parts(name) is not None
            or _bypass_parts(name) is not None
        )
        if is_support and _unknown_owned_comments(lines, start, end):
            errors.append(
                f"{name} contains unknown comment inside generated ps-t "
                "support; ownership is ambiguous"
            )
        if key in section_bodies:
            if is_support:
                errors.append(
                    "duplicate ps-t support section header "
                    f"(case-insensitive): [{section_names[key]}] / [{name}]"
                )
            continue
        section_names[key] = name
        section_bodies[key] = "\n".join(lines[start + 1:end])
    restore_specs: Set[Tuple[str, Optional[int]]] = set()
    transaction_suffixes: Set[str] = set()
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
            transaction_suffixes.add(suffix.casefold())

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
                setter.casefold() for setter in setters))
            if len(unique_setters) > 1:
                errors.append(
                    f"{name} runs multiple direct texture setters in one "
                    f"transaction: {', '.join(setters)}")
            elif setters:
                setter_parts = _setter_parts(setters[0])
                setter_suffix = setter_parts[1] if setter_parts else ""
                if setter_suffix.casefold() != suffix.casefold():
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
                if restore is None or restore[1].casefold() != suffix.casefold():
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
        if restore_name.casefold() not in section_bodies:
            errors.append(f"{restore_name} missing for slot-style ps-t scope")

    suffixes = sorted(
        {suffix for suffix, _slot in restore_specs},
        key=lambda value: (value != "", value),
    )
    for suffix in suffixes:
        backup = _backup_name(suffix)
        backup_body = section_bodies.get(backup.casefold(), "")
        for slot in range(9):
            resource = _bypass_name(slot, suffix)
            if resource.casefold() not in section_bodies:
                errors.append(f"{resource} missing for slot-style ps-t backup")
            if not re.search(
                    r"^\s*%s\s*=\s*ref\s+ps-t%d\s*$"
                    % (re.escape(resource), slot), backup_body, re.M | re.I):
                errors.append(f"{backup} does not backup ps-t{slot}")

    for suffix, persistent_slot in sorted(
            restore_specs,
            key=lambda item: (item[0] != "", item[0],
                              -1 if item[1] is None else item[1])):
        restore = _restore_name(suffix, persistent_slot)
        body = section_bodies.get(restore.casefold(), "")
        for slot in range(9):
            resource = _bypass_name(slot, suffix)
            found = bool(re.search(
                r"^\s*ps-t%d\s*=\s*ref\s+%s\s*$"
                % (slot, re.escape(resource)), body, re.M | re.I))
            if slot == persistent_slot:
                if found:
                    errors.append(f"{restore} unexpectedly restores ps-t{slot}")
            elif not found:
                errors.append(f"{restore} does not restore ps-t{slot}")

    for resource, _start, _end in spans:
        parts = _bypass_parts(resource)
        if parts is None:
            continue
        slot, suffix = parts
        backup_reference = re.search(
            r"^\s*%s\s*=\s*ref\s+ps-t%d\s*$"
            % (re.escape(resource), slot),
            section_bodies.get(_backup_name(suffix).casefold(), ""),
            re.M | re.I,
        )
        restore_reference = any(
            re.search(
                r"^\s*ps-t%d\s*=\s*ref\s+%s\s*$"
                % (slot, re.escape(resource)), body, re.M | re.I)
            for name, body in section_bodies.items()
            if ((restore := _restore_parts(name)) is not None
                and restore[1].casefold() == suffix.casefold())
        )
        if not backup_reference and not restore_reference:
            errors.append(
                f"{resource} is declared but never referenced by a ps-t "
                "backup or restore command")

    generated_groups = _generated_support_groups(lines)
    support_suffixes: Dict[str, str] = {}
    for name, _start, _end in spans:
        suffix = _backup_suffix(name)
        if suffix is None:
            restore = _restore_parts(name)
            if restore is not None:
                suffix = restore[1]
            else:
                bypass = _bypass_parts(name)
                if bypass is not None:
                    suffix = bypass[1]
        if suffix is not None:
            support_suffixes.setdefault(suffix.casefold(), suffix)

    for key, suffix_value in support_suffixes.items():
        if key in transaction_suffixes:
            continue
        suffix = suffix_value or "<global>"
        if key in generated_groups:
            errors.append(
                f"generated ps-t support group {suffix} has no "
                "Trigger-to-Cleanup transaction caller")
        else:
            errors.append(
                f"ps-t support suffix {suffix} has no "
                "Trigger-to-Cleanup transaction caller")
    return errors
