"""Rename generated INI identities without rewriting paths or authored text."""
from __future__ import annotations

from collections import Counter
import re


DISPLAY_NAME_KEY = "_export_display_name"
_HEADER = re.compile(r"^(\s*\[)([^\]]+)(\]\s*)$")
_FLOW = re.compile(r"^(?:if|elif|else\s+if|while|condition)\b", re.I)
_ASSIGNMENT = re.compile(
    r"^(?:post\s+)?(?:this|ib|vb\d+|[vhdgpc]s-[tub]\d+|o\d+|od|"
    r"resource[\w\\]+|pool[\w\\]+(?:\[[^\]]+\])?|run|"
    r"(?:(?:global|local|persist)\s+)*\$[\w\\]+(?:\[[^\]]+\])?|"
    r"[xyzw]\d+)\s*=", re.I)


def _code_and_comment(line):
    """Keep semicolons in quoted operands separate from actual comments."""
    quote = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char == ';':
            return line[:index], line[index:]
    return line, ''


def rewrite_identifiers(text, renames):
    """Rewrite section names and executable operands, never data/path fields."""
    if not renames:
        return text
    lookup = {name.casefold(): value for name, value in renames.items()}
    tokens = re.compile(
        r'"[^"\r\n]*"|\'[^\'\r\n]*\'|(?<![\w$\\])(?:'
        + '|'.join(re.escape(name) for name in sorted(renames, key=len, reverse=True))
        + r')(?![\w\\])', re.I)

    def replace(match):
        return lookup.get(match[0].casefold(), match[0])

    result = []
    for line in text.splitlines(keepends=True):
        header = _HEADER.fullmatch(line)
        if header:
            line = header[1] + lookup.get(header[2].casefold(), header[2]) + header[3]
        else:
            code, comment = _code_and_comment(line)
            if _FLOW.match(code.lstrip()) or _ASSIGNMENT.match(code.lstrip()):
                line = tokens.sub(replace, code) + comment
        result.append(line)
    return ''.join(result)


def generated_object_names(cfg):
    """Read explicit provenance from disposable host copies, not suffix guesses."""
    root = getattr(cfg, 'component_collection', None)
    return {obj.name: obj.get(DISPLAY_NAME_KEY) for obj in getattr(root, 'all_objects', ())
            if getattr(obj, 'get', None) and obj.get(DISPLAY_NAME_KEY)}


def sanitize_draw_labels(text, names):
    """Clean exact generated labels, including late borrowed-geometry draws."""
    result = []
    for line in text.splitlines(keepends=True):
        for pattern in (r'^(\s*;\s*Draw object ")(.*)(":\s*)$',
                        r'^(\s*;\s*Draw provider )(.*?)(\s*)$',
                        r'^(\s*;\s*Draw )(.*?)(\s*)$'):
            match = re.fullmatch(pattern, line)
            if match and match[2] in names:
                line = match[1] + names[match[2]] + match[3]
                break
        result.append(line)
    return ''.join(result)


def sanitize_generated_object_names(text, names, format_drawvar=None, *, renames_out=None):
    """Clean labels and expose the exact collision-resolved control identities."""
    names = dict(names)
    if not names:
        return text
    text = sanitize_draw_labels(text, names)
    if format_drawvar is None:
        return text
    candidates = [(format_drawvar(name), format_drawvar(display))
                  for name, display in sorted(names.items()) if name != display]
    counts = Counter(old.casefold() for old, _ in candidates)
    occupied = {token.casefold() for token in re.findall(r'\$[\w\\]+', text)}
    renames = {}
    for old, target in candidates:
        if old == target or counts[old.casefold()] != 1:
            continue
        base, ordinal = target, 0
        while target.casefold() in occupied:
            ordinal += 1
            target = f'{base}_{ordinal:03d}'
        occupied.add(target.casefold())
        renames[old] = target
    if renames_out is not None:
        renames_out.update(renames)
    return rewrite_identifiers(text, renames)
