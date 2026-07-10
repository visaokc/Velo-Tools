"""Structured final-INI parsing and stable functional ordering."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, List, Sequence, Tuple


_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_AUTO_BANNER_RE = re.compile(r"^\s*;\s*=+\s*Velo INI:\s*.*?\s*=+\s*$", re.I)
_NATURAL_PART_RE = re.compile(r"(\d+)")


class IniDocumentError(ValueError):
    """Raised when a generated INI cannot be represented safely."""


@dataclass(frozen=True)
class IniSection:
    name: str
    prefix: Tuple[str, ...]
    body: Tuple[str, ...]
    ordinal: int


@dataclass(frozen=True)
class IniDocument:
    preamble: Tuple[str, ...]
    sections: Tuple[IniSection, ...]


def _is_leading_trivia(line: str) -> bool:
    stripped = line.strip()
    return not stripped or stripped.startswith(";")


def _next_prefix_start(lines: Sequence[str], previous_header: int, header: int) -> int:
    """Return a safe prefix boundary for an inter-section comment block."""

    region_start = previous_header + 1
    substantive = [
        index
        for index in range(region_start, header)
        if not _is_leading_trivia(lines[index])
    ]
    if not substantive:
        return header

    for separator in range(substantive[-1] + 1, header):
        if lines[separator].strip():
            continue
        prefix_start = separator + 1
        while prefix_start < header and not lines[prefix_start].strip():
            prefix_start += 1
        if prefix_start < header:
            return prefix_start
        return header
    return header


def parse_ini_document(text: str) -> IniDocument:
    """Parse sections while attaching explicitly separated prefixes safely."""

    lines = text.splitlines()
    headers = []
    for index, line in enumerate(lines):
        match = _HEADER_RE.match(line)
        if match:
            headers.append((index, match.group(1)))
    if not headers:
        raise IniDocumentError("generated INI has no sections")

    lowered = {}
    for _index, name in headers:
        key = name.casefold()
        if key in lowered:
            raise IniDocumentError(
                f"duplicate section header (case-insensitive): [{lowered[key]}] / [{name}]"
            )
        lowered[key] = name

    prefix_starts: List[int] = []
    for pos, (header_index, _name) in enumerate(headers):
        if pos == 0:
            prefix_starts.append(header_index)
            continue
        prefix_starts.append(
            _next_prefix_start(lines, headers[pos - 1][0], header_index)
        )

    preamble = tuple(
        line for line in lines[:prefix_starts[0]] if not _AUTO_BANNER_RE.match(line)
    )
    sections: List[IniSection] = []
    for pos, (header_index, name) in enumerate(headers):
        next_prefix = prefix_starts[pos + 1] if pos + 1 < len(headers) else len(lines)
        prefix = tuple(
            line
            for line in lines[prefix_starts[pos]:header_index]
            if not _AUTO_BANNER_RE.match(line)
        )
        body = tuple(lines[header_index + 1:next_prefix])
        sections.append(IniSection(name=name, prefix=prefix, body=body, ordinal=pos))
    return IniDocument(preamble=preamble, sections=tuple(sections))


def _natural_key(value: str) -> Tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in _NATURAL_PART_RE.split(value)
    )


def _category(section: IniSection) -> int:
    name = section.name.casefold()
    if name == "constants":
        return 0
    if name == "present":
        return 1
    if name.startswith(("textureoverride", "shaderoverride", "shaderregex")):
        return 2
    if name.startswith("commandlistdrawcomponent"):
        return 3
    if name.startswith("commandlistdrawgeometrycomponent"):
        return 4
    if name.startswith((
            "commandlistsettexturescomponent",
            "commandlistbackuppixelshaderresources",
            "commandlisttriggerresourceoverrides",
            "commandlistcleanupsharedresources",
            "commandlistrestorepixelshaderresources")):
        return 5
    if name.startswith("commandlist"):
        return 6
    if name.startswith(("resourcetexture", "resource_texture_")):
        return 7
    if name.startswith(("resource", "customshader")):
        return 8
    return 9


_BANNERS = {
    2: "; ===== Velo INI: Match and draw entry overrides =====",
    3: "; ===== Velo INI: Draw transactions =====",
    4: "; ===== Velo INI: Shared draw geometry =====",
    5: "; ===== Velo INI: Slot and PS-resource lifecycle =====",
    6: "; ===== Velo INI: Runtime command lists =====",
    7: "; ===== Velo INI: Texture resources =====",
    8: "; ===== Velo INI: Mesh and runtime resources =====",
    9: "; ===== Velo INI: Other sections =====",
}


def _ordered_sections(sections: Sequence[IniSection]) -> List[IniSection]:
    def key(section: IniSection):
        category = _category(section)
        if category in {0, 1, 2, 9}:
            return category, section.ordinal
        return category, _natural_key(section.name), section.ordinal

    return sorted(sections, key=key)


def _trim_boundary_blank_lines(lines: Iterable[str]) -> List[str]:
    out = list(lines)
    while out and not out[-1].strip():
        out.pop()
    return out


def _trim_blank_lines(lines: Iterable[str]) -> List[str]:
    out = _trim_boundary_blank_lines(lines)
    while out and not out[0].strip():
        out.pop(0)
    return out


def stable_functional_sort(text: str) -> str:
    """Return a deterministic category order without reordering match sections."""

    document = parse_ini_document(text)
    original_match_order = [
        section.name for section in document.sections if _category(section) == 2
    ]
    ordered = _ordered_sections(document.sections)
    if [section.name for section in ordered if _category(section) == 2] != original_match_order:
        raise IniDocumentError("match-bearing section order changed")

    out = _trim_boundary_blank_lines(document.preamble)
    if out:
        out.extend(["", ""])
    last_category = None
    for section in ordered:
        category = _category(section)
        if category != last_category and category in _BANNERS:
            if out and out[-1].strip():
                out.append("")
            out.extend([_BANNERS[category], ""])
        prefix = _trim_blank_lines(section.prefix)
        if prefix:
            out.extend(prefix)
        out.append(f"[{section.name}]")
        out.extend(section.body)
        while out and not out[-1].strip():
            out.pop()
        out.extend(["", ""])
        last_category = category
    return "\n".join(_trim_boundary_blank_lines(out)) + "\n"
