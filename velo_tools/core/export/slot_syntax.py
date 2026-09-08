"""Lower generated slot-family conditions to native resource format getters."""

import re

from . import slot_formats


MODE_ITEMS = (
    ("NATIVE", "Native Format Read",
     "Read slot formats with ->Format and DXGI_FORMAT literals; requires XXMI Libs 1.1.0 or newer"),
    ("FUZZY", "Fuzzy Format Matching",
     "Use legacy match_format and filter_index sections for slot format matching"),
)

_HEADER = re.compile(r"^\[([^\]]+)\][ \t]*\r?$", re.M)
_FORMAT_HEADER = re.compile(
    r"^TextureOverride(?:SlotFormatC\d+|Component\d+|Lod\d+Component\d+"
    r"|RouteFormat_[0-9a-f]+_C\d+_|_FoldHost_[0-9a-f]+_C\d+_)", re.I)
_SETTER_HEADER = re.compile(r"^CommandListSetTexturesComponent(\d+)", re.I)
_COMPONENT = re.compile(r"^TextureOverride(?:SlotFormatC|(?:Lod\d+)?Component)(\d+)", re.I)
_TERM = re.compile(r"\bps-t(\d+)\s*(==|!=)\s*(83\.\d+)\b")
_FIELDS = {"match_first_index", "match_index_count", "match_priority",
           "match_format", "filter_index"}


def _format_marker(name, body):
    if not _FORMAT_HEADER.match(name):
        return None
    fields = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip().lower()
        if not sep or key not in _FIELDS or key in fields:
            return None
        fields[key] = value.strip()
    if set(fields) != _FIELDS:
        return None
    member = fields["match_format"]
    if (member not in slot_formats.DXGI_FORMAT_NAMES
            or fields["filter_index"] != slot_formats.filter_index_text(member)
            or fields["match_priority"] != str(slot_formats.FORMAT_TAG_PRIORITY)):
        return None
    return fields["filter_index"], member


def lower_ini(text: str, mode: str, *, component_markers=()) -> str:
    """Change only generated format markers and their component-local readers.

    Planning remains family-based. A typeless resource marker must expand to
    its typed views because ->Format prefers the bound SRV format. Typed
    markers retain their exact observed formats, including sRGB distinctions.
    Hash fallbacks, assignments, backups and restore transactions stay intact.
    """
    if mode == "FUZZY":
        return text
    if mode != "NATIVE":
        raise ValueError(f"Unknown slot export mode: {mode}")
    headers = list(_HEADER.finditer(text))
    sections = []
    members = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        body = text[header.end():end]
        marker = _format_marker(header.group(1), body)
        sections.append((header, end, marker))
        component = _COMPONENT.match(header.group(1))
        if marker and component:
            tag, member = marker
            members.setdefault(int(component.group(1)), {}).setdefault(tag, set()).add(member)
    for component_id, name, lines in component_markers:
        marker = _format_marker(name, "\n".join(lines))
        if marker:
            tag, member = marker
            members.setdefault(component_id, {}).setdefault(tag, set()).add(member)
    if not members:
        return text

    def native_term(match):
        slot, operator, tag = match.groups()
        if tag not in active_members:
            raise ValueError(f"Slot condition has no generated format marker: {tag}")
        formats = set()
        for member in active_members[tag]:
            if member.endswith("_TYPELESS"):
                prefix = slot_formats.format_prefix(member)
                formats.update(value for value in slot_formats.DXGI_FORMAT_NAMES
                               if slot_formats.format_prefix(value) == prefix)
            else:
                formats.add(member)
        terms = [f"ps-t{slot}->Format {operator} DXGI_FORMAT_{value}"
                 for value in sorted(formats)]
        if len(terms) == 1:
            return terms[0]
        joiner = " || " if operator == "==" else " && "
        return "(" + joiner.join(terms) + ")"

    out = [text[:headers[0].start()]]
    for header, end, marker in sections:
        if marker:
            continue
        block = text[header.start():end]
        setter = _SETTER_HEADER.match(header.group(1))
        if setter:
            active_members = members.get(int(setter.group(1)), {})
            lines = block.splitlines(keepends=True)
            block = "".join(
                _TERM.sub(native_term, line)
                if line.lstrip().startswith(("if ", "else if ")) else line
                for line in lines)
        out.append(block)
    return "".join(out)
