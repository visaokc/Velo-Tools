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


def formats_from_forms(forms, texture_info):
    """Keep observed WWMI formats at component/slot granularity."""
    return tuple(sorted({
        (component_id, slot, texture_info[texture_hash]["format"])
        for _label, components in forms
        for component_id, shaders in components.items()
        for slots in shaders.values()
        for slot, texture_hash in slots.items()
        if texture_hash in texture_info and texture_info[texture_hash].get("format")
    }))


def lower_ini(text: str, mode: str, *, component_markers=(), format_evidence=()) -> str:
    """Change only generated format markers and their component-local readers.

    Planning remains family-based, but native conditions use recorded formats
    for each component/slot. Never expand TYPELESS into unobserved formats.
    Multiple actually observed formats remain alternatives, not guessed types.
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

    observed = {}
    for component_id, slot, format_name in format_evidence:
        if format_name in slot_formats.DXGI_FORMAT_NAMES:
            key = (component_id, slot, slot_formats.filter_index_text(format_name))
            observed.setdefault(key, set()).add(format_name)

    def native_term(match):
        slot, operator, tag = match.groups()
        if tag not in active_members:
            raise ValueError(f"Slot condition has no generated format marker: {tag}")
        formats = observed.get((component_id, int(slot), tag), active_members[tag])
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
            component_id = int(setter.group(1))
            active_members = members.get(component_id, {})
            lines = block.splitlines(keepends=True)
            block = "".join(
                _TERM.sub(native_term, line)
                if line.lstrip().startswith(("if ", "else if ")) else line
                for line in lines)
        out.append(block)
    return "".join(out)
