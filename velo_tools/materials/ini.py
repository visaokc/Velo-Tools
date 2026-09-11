"""Apply material-local resources inside already validated slot transactions."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re

from .routing import SourcePlan

_HEADER = re.compile(r"^\s*\[([^]]+)\]\s*$")
_RUN = re.compile(r"^\s*run\s*=\s*([^;\s]+)\s*(?:;.*)?$", re.I)
_SET = re.compile(r"^(\s*)ps-t(\d+)\s*=\s*(?:(?:ref|reference|copy)\s+)?([^;\s]+)", re.I)
_DRAW = re.compile(r"^(\s*)(drawindexed(?:instanced)?)\s*=\s*([^;]+?)(\s*;.*)?$", re.I)
_COMPONENT = re.compile(r"component[_ ]*(\d+)(?:$|[^0-9])", re.I)
_ROOT = re.compile(r"^CommandListSetTexturesComponent(\d+)", re.I)
MATERIAL_IMAGE_EXTENSIONS = frozenset({".dds", ".png", ".jpg", ".jpeg", ".tga", ".bmp"})
_INVALID_MATERIAL_FILENAME_CHARS = frozenset('<>:"/\\|?*;\x00')


class BindingError(ValueError):
    """Retain the English message key for translation at the Blender boundary."""

    def __init__(self, message, *values):
        self.message = message
        self.values = values
        super().__init__(message.format(*values))


def safe_material_resource_filename(filename):
    """Accept one exact basename under Textures without rewriting it."""
    prefix = "Textures/"
    if not isinstance(filename, str) or not filename.startswith(prefix):
        return False
    basename = filename[len(prefix):]
    if (not basename or basename in {".", ".."} or basename != basename.strip()
            or basename.endswith(".")):
        return False
    if any(ord(char) < 32 or char in _INVALID_MATERIAL_FILENAME_CHARS
           for char in basename):
        return False
    dot = basename.rfind(".")
    return dot > 0 and basename[dot:].lower() in MATERIAL_IMAGE_EXTENSIONS


def material_resource_name(filename):
    """Use the file stem only; sanitize the identifier, never the output path."""
    if not safe_material_resource_filename(filename):
        raise BindingError("Unsafe material resource filename")
    stem = filename[len("Textures/"):].rsplit(".", 1)[0]
    stem = re.sub(r"[^A-Za-z0-9_]", "", stem.replace(" ", "_"))
    if not stem:
        raise BindingError(
            "Material image {0} has no usable resource name after removing unsupported characters; rename the source image",
            filename)
    return f"ResourceMaterialTexture_{stem}"


def allocate_material_resource_names(filenames):
    """Allocate deterministic section names without changing delivered files.

    Reserve every natural base first, so a generated suffix never takes a
    name such as Body_001 from a different file. Exact clean stems take
    priority within a collision group; input/draw traversal order is ignored.
    """
    bases = {filename: material_resource_name(filename) for filename in set(filenames)}
    buckets = defaultdict(list)
    for filename, base in bases.items():
        buckets[base.casefold()].append(filename)
    reserved = set(buckets)
    used, result = set(), {}
    for key in sorted(buckets):
        def priority(filename):
            stem = filename[len("Textures/"):].rsplit(".", 1)[0]
            exact = "ResourceMaterialTexture_" + stem == bases[filename]
            return not exact, filename.casefold(), filename

        suffix = 1
        for index, filename in enumerate(sorted(buckets[key], key=priority)):
            base = bases[filename]
            name = base
            if index:
                while True:
                    name = f"{base}_{suffix:03d}"
                    suffix += 1
                    if name.casefold() not in reserved and name.casefold() not in used:
                        break
            used.add(name.casefold())
            result[filename] = name
    return result


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
    wanted = {comp: {identity for draw in draws if draw.component == comp
                     for segment in draw.segments for identity, _target in segment.replacements}
              for comp in required}
    plan = SourcePlan(lines, spans, required, wanted, reverse)
    occurrences, reachable = plan.occurrences, plan.reachable
    groups = {}
    support = []
    used_resources = set()
    used_backups = set()

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
        for condition, assignments in plan.conditions(comp, key[1]):
            apply.append(f"if {condition}")
            restore.append(f"if {condition}")
            for slot, target in assignments:
                backup = f"ResourceMaterialBackupC{comp}T{slot}"
                used_backups.add(backup)
                apply.extend([f"    {backup} = reference ps-t{slot}",
                              f"    ps-t{slot} = reference {target}"])
                restore.extend([f"    ps-t{slot} = reference {backup}",
                                f"    {backup} = null"])
            apply.append("endif")
            restore.append("endif")
        support.extend(["", *apply, "", *restore])
        return group

    replacements_at = {}
    seen_draws = set()
    moved_comments = set()
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
            comment_end = index
            while comment_end > start+1 and not lines[comment_end-1].strip():
                comment_end -= 1
            guarded_label = (comment_end > start+1 and re.match(
                r"^\s*if\s+\$draw_\w+\s*(?:;.*)?$", lines[comment_end-1], re.I))
            if guarded_label:
                comment_end -= 1
            comment_start = comment_end
            while comment_start > start+1:
                previous = lines[comment_start-1].strip()
                if previous and not previous.startswith(";"):
                    break
                comment_start -= 1
            object_comments = [line for line in lines[comment_start:comment_end] if line.strip()]
            if guarded_label and not any(re.match(r'^\s*;\s*Draw object\b', line, re.I)
                                         for line in object_comments):
                object_comments = []
            if object_comments:
                moved_comments.update(range(comment_start, comment_end))
                object_comments = [indent + line.lstrip() for line in object_comments]
            out = []
            for segment in draw.segments:
                group = group_for(comp, segment.replacements)
                if group:
                    out.append(f"{indent}run = CommandListApplyMaterial{group}")
                out.extend(object_comments)
                local = list(args)
                local[0], local[offset_index] = str(segment.count), str(segment.offset)
                out.append(f"{indent}{command} = {', '.join(local)}{comment or ''}")
                if group:
                    out.append(f"{indent}run = CommandListRestoreMaterial{group}")
            replacements_at[index] = out
    if set(lookup) != seen_draws:
        raise BindingError("Some material draws have no supported INI anchor; custom or cross-scene draw layouts require explicit adaptation")
    output = []
    for index, line in enumerate(lines):
        if index in moved_comments:
            continue
        output.extend(plan.before.get(index, ()))
        output.extend(replacements_at.get(index, [plan.call_replacements.get(index, line)]))
        if index == bodies["constants"][1]:
            output.extend(plan.declarations)
        output.extend(plan.after.get(index, ()))
    for backup in sorted(used_backups):
        support.extend(["", f"[{backup}]"])
    support.extend(plan.clones)
    output = _coalesce_transactions(output)
    if batch_draws:
        output = _coalesce_transactions(_hoist_draw_guards(output))
    for name in sorted(used_resources):
        filename = resources[name]
        if not safe_material_resource_filename(filename):
            raise BindingError("Unsafe material resource filename")
        support.extend(["", f"[{name}]", f"filename = {filename}"])
    return "\n".join(output + support).rstrip() + "\n", {"groups": len(groups), "draws": len(seen_draws)}
