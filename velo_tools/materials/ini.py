"""Apply material-local resources inside already validated slot transactions."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re

_HEADER = re.compile(r"^\s*\[([^]]+)\]\s*$")
_RUN = re.compile(r"^\s*run\s*=\s*([^;\s]+)\s*(?:;.*)?$", re.I)
_SET = re.compile(r"^(\s*)ps-t(\d+)\s*=\s*(?:(?:ref|reference|copy)\s+)?([^;\s]+)", re.I)
_DRAW = re.compile(r"^(\s*)(drawindexed(?:instanced)?)\s*=\s*([^;]+?)(\s*;.*)?$", re.I)
_COMPONENT = re.compile(r"component[_ ]*(\d+)(?:$|[^0-9])", re.I)
_ROOT = re.compile(r"^CommandListSetTexturesComponent(\d+)", re.I)


class BindingError(ValueError):
    """Retain the English message key for translation at the Blender boundary."""

    def __init__(self, message, *values):
        self.message = message
        self.values = values
        super().__init__(message.format(*values))


@dataclass(frozen=True)
class Segment:
    count: int
    offset: int
    replacements: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Draw:
    component: int
    count: int
    offset: int
    segments: tuple[Segment, ...]


def _sections(lines):
    headers = [(m.group(1), i) for i, line in enumerate(lines) if (m := _HEADER.match(line))]
    return [(name, start, headers[i+1][1] if i+1 < len(headers) else len(lines))
            for i, (name, start) in enumerate(headers)]


def source_resources(text, textures):
    """Resolve renamed resource sections from their exported file provenance."""
    by_filename = {}
    for identity, filename in textures:
        key = ("Textures/" + filename.replace("\\", "/")).casefold()
        if key in by_filename and by_filename[key] != identity:
            raise BindingError("Ambiguous original texture filenames")
        by_filename[key] = identity
    aliases = defaultdict(list)
    lines = text.splitlines()
    for name, start, end in _sections(lines):
        if not name.casefold().startswith("resource"):
            continue
        for line in lines[start+1:end]:
            match = re.match(r"^\s*filename\s*=\s*(.+?)\s*$", line, re.I)
            if match:
                filename = match.group(1).strip('"').replace("\\", "/")
                if filename.startswith("./"):
                    filename = filename[2:]
                identity = by_filename.get(filename.casefold())
                if identity:
                    aliases[identity].append(name)
    return {identity: tuple(names) for identity, names in aliases.items()}


def _coalesce_transactions(output):
    """Remove only consecutive restore/apply pairs for the exact same scope."""
    previous_restore = None
    for index, line in enumerate(output):
        run = _RUN.match(line)
        if run and run.group(1).startswith("CommandListRestoreMaterial"):
            previous_restore = index
        elif run and run.group(1).startswith("CommandListApplyMaterial") and previous_restore is not None:
            old = output[previous_restore]
            if old.replace("RestoreMaterial", "ApplyMaterial") == line:
                output[previous_restore] = ""
                output[index] = ""
            previous_restore = None
        elif line.strip() and not line.lstrip().startswith(";"):
            previous_restore = None
    return output


def _hoist_draw_guards(lines):
    """Share bindings around pure per-object visibility guards, not arbitrary IFs.

    A guard must contain exactly apply -> draw(s) -> restore, with no nested
    control flow, side effects or state changes. Its original draw condition is
    preserved. Setting/restoring textures when every guard is false is a no-op.
    """
    output = []
    index = 0
    guard = re.compile(r"^(\s*)if\s+\$draw_\w+\s*(?:;.*)?$", re.I)
    while index < len(lines):
        match = guard.match(lines[index])
        if match is None:
            output.append(lines[index])
            index += 1
            continue
        end = index + 1
        executable = []
        while end < len(lines):
            code = lines[end].strip()
            if code and not code.startswith(";"):
                if code.lower() == "endif":
                    break
                if not (_RUN.match(lines[end]) or _DRAW.match(lines[end])):
                    break
                executable.append(end)
            end += 1
        valid = end < len(lines) and lines[end].strip().lower() == "endif" and len(executable) >= 3
        if valid:
            first, last = executable[0], executable[-1]
            apply, restore = _RUN.match(lines[first]), _RUN.match(lines[last])
            valid = (apply is not None and restore is not None
                     and apply.group(1).startswith("CommandListApplyMaterial")
                     and apply.group(1).replace("ApplyMaterial", "RestoreMaterial") == restore.group(1)
                     and all(_DRAW.match(lines[item]) for item in executable[1:-1]))
        if not valid:
            output.append(lines[index])
            index += 1
            continue
        indent = match.group(1)
        output.append(f"{indent}run = {apply.group(1)}")
        output.extend(lines[item] for item in range(index, end + 1) if item not in (first, last))
        output.append(f"{indent}run = {restore.group(1)}")
        index = end + 1
    return output


def _combine_constants(lines):
    """Normalize the native ShapeKey template's additive Constants fragments.

    Only this special section is combined. Keep declaration/initialization order
    exactly; duplicate executable/resource sections remain an export error.
    """
    spans = _sections(lines)
    fragments = [(start, end) for name, start, end in spans if name.casefold() == "constants"]
    if len(fragments) < 2:
        return lines
    first, end = fragments[0]
    extra = [line for start, stop in fragments[1:] for line in lines[start + 1:stop]]
    removed = {index for start, stop in fragments[1:] for index in range(start, stop)}
    result = []
    for index, line in enumerate(lines):
        if index == end:
            result.extend(extra)
        if index not in removed:
            result.append(line)
    return result


def transform(text, draws, resource_by_identity, resources, *, batch_draws=False):
    """Use integer witnesses emitted by safe setters, never runtime hash tests.

    Draw ranges come from the finalized temporary mesh, before native Join.
    Adjacent compatible transactions are coalesced without crossing commands,
    conditions, or reordering transparent geometry.
    """
    draws = [draw for draw in draws if any(s.replacements for s in draw.segments)]
    if not draws:
        return text, {"groups": 0, "draws": 0}
    lines = _combine_constants(text.splitlines())
    spans = _sections(lines)
    bodies = {name.casefold(): (name, start, end) for name, start, end in spans}
    if len(bodies) != len(spans):
        raise BindingError("Duplicate INI sections prevent material binding")
    if "constants" not in bodies:
        raise BindingError("Material bindings require an INI Constants section")
    if any(name.casefold().startswith(("commandlistapplymaterial", "resourcematerialtexture"))
           for name, _, _ in spans):
        raise BindingError("Material texture layer is already present")
    lookup = {(d.component, d.count, d.offset): d for d in draws}
    if len(lookup) != len(draws):
        raise BindingError("Ambiguous exported material draw ranges")
    for draw in draws:
        cursor = draw.offset
        for segment in draw.segments:
            if segment.count <= 0 or segment.offset != cursor or segment.count % 3:
                raise BindingError("Invalid finalized material draw partition")
            cursor += segment.count
        if cursor != draw.offset + draw.count:
            raise BindingError("Material ranges do not cover the original draw")
    required = {draw.component for draw in draws}
    reverse = {resource.casefold(): identity
               for identity, aliases in resource_by_identity.items()
               for resource in ((aliases,) if isinstance(aliases, str) else aliases)}
    tokens = {identity: i+1 for i, identity in enumerate(sorted(resource_by_identity))}
    root_calls = {}
    reachable = defaultdict(set)

    def walk(name, seen):
        key = name.casefold()
        if key in seen or key not in bodies:
            return
        seen.add(key)
        _, start, end = bodies[key]
        for line in lines[start+1:end]:
            match = _RUN.match(line)
            if match:
                walk(match.group(1), seen)

    for name, start, end in spans:
        comp_match = _COMPONENT.search(name)
        comp = int(comp_match.group(1)) if comp_match else None
        if comp not in required or _ROOT.match(name):
            continue
        for index in range(start+1, end):
            run = _RUN.match(lines[index])
            root = _ROOT.match(run.group(1)) if run else None
            if root and int(root.group(1)) == comp:
                root_calls[index] = comp
                walk(run.group(1), reachable[comp])
    occurrences = defaultdict(lambda: defaultdict(set))
    for comp, section_keys in reachable.items():
        for key in section_keys:
            _, start, end = bodies[key]
            for line in lines[start+1:end]:
                match = _SET.match(line)
                if match and (identity := reverse.get(match.group(3).casefold())):
                    occurrences[comp][identity].add(int(match.group(2)))
    slots = {comp: sorted({slot for values in occurrence.values() for slot in values})
             for comp, occurrence in occurrences.items()}
    groups = {}
    support = []
    used_resources = set()

    def witness(comp, slot):
        return f"$material_source_c{comp}_t{slot}"

    def group_for(comp, replacements):
        if not replacements:
            return None
        key = (comp, tuple(sorted(replacements)))
        if key in groups:
            return groups[key]
        group = f"C{comp}G{sum(c == comp for c, _ in groups)}"
        groups[key] = group
        apply = [f"[CommandListApplyMaterial{group}]"]
        restore = [f"[CommandListRestoreMaterial{group}]"]
        targets = {}
        for identity, target in key[1]:
            if identity in targets and targets[identity] != target:
                raise BindingError("Two semantic inputs replace the same original texture differently")
            if identity in targets:
                continue
            targets[identity] = target
            if target not in resources:
                raise BindingError("Missing material image resource")
            available = occurrences[comp].get(identity, ())
            if not available:
                raise BindingError("Component {0}: original texture {1} has no safe slot assignment; refresh its source mapping and slot selection", comp, identity)
            used_resources.add(target)
            for slot in sorted(available):
                condition = f"if {witness(comp, slot)} == {tokens[identity]}"
                backup = f"ResourceMaterialBackup{group}T{slot}"
                apply.extend([condition, f"    {backup} = reference ps-t{slot}",
                              f"    ps-t{slot} = reference {target}", "endif"])
                restore.extend([condition, f"    ps-t{slot} = reference {backup}",
                                f"    {backup} = null", "endif"])
        backups = sorted({line.strip().split(" =")[0] for line in apply if " = reference ps-t" in line})
        support.extend(["", *apply, "", *restore])
        for backup in backups:
            support.extend(["", f"[{backup}]"])
        return group

    replacements_at = {}
    seen_draws = set()
    for name, start, end in spans:
        comp_match = _COMPONENT.search(name)
        if not comp_match:
            continue
        comp = int(comp_match.group(1))
        for index in range(start+1, end):
            match = _DRAW.match(lines[index])
            if not match:
                continue
            indent, command, arguments, comment = match.groups()
            args = [part.strip() for part in arguments.split(",")]
            offset_index = 2 if command.lower() == "drawindexedinstanced" else 1
            try:
                key = (comp, int(args[0]), int(args[offset_index]))
            except (ValueError, IndexError):
                continue
            draw = lookup.get(key)
            if draw is None:
                continue
            if not reachable.get(comp):
                raise BindingError("Component {0} has no validated slot setter", comp)
            seen_draws.add(key)
            out = []
            for segment in draw.segments:
                group = group_for(comp, segment.replacements)
                if group:
                    out.append(f"{indent}run = CommandListApplyMaterial{group}")
                local = list(args)
                local[0], local[offset_index] = str(segment.count), str(segment.offset)
                out.append(f"{indent}{command} = {', '.join(local)}{comment or ''}")
                if group:
                    out.append(f"{indent}run = CommandListRestoreMaterial{group}")
            replacements_at[index] = out
    if set(lookup) != seen_draws:
        raise BindingError("Some material draws have no supported INI anchor; custom or cross-scene draw layouts require explicit adaptation")
    output = []
    current_key = ""
    for index, line in enumerate(lines):
        header = _HEADER.match(line)
        if header:
            current_key = header.group(1).casefold()
        if index in root_calls:
            comp = root_calls[index]
            indent = line[:len(line)-len(line.lstrip())]
            output.extend(f"{indent}{witness(comp, slot)} = 0" for slot in slots.get(comp, ()))
        output.extend(replacements_at.get(index, [line]))
        if index == bodies["constants"][1]:
            output.extend(f"global {witness(comp, slot)} = 0"
                          for comp in sorted(slots) for slot in slots[comp])
        assignment = _SET.match(line)
        if assignment:
            indent, slot, resource = assignment.groups()
            slot = int(slot)
            for comp in sorted(slots):
                if slot not in slots[comp]:
                    continue
                identity = reverse.get(resource.casefold()) if current_key in reachable[comp] else None
                output.append(f"{indent}{witness(comp, slot)} = {tokens.get(identity, 0)}")
    output = _coalesce_transactions(output)
    if batch_draws:
        output = _coalesce_transactions(_hoist_draw_guards(output))
    for name in sorted(used_resources):
        filename = resources[name]
        if not re.fullmatch(r"Textures/material_[a-f0-9]{24}\.[a-z0-9]+", filename):
            raise BindingError("Unsafe material resource filename")
        support.extend(["", f"[{name}]", f"filename = {filename}"])
    return "\n".join(output + support).rstrip() + "\n", {"groups": len(groups), "draws": len(seen_draws)}
