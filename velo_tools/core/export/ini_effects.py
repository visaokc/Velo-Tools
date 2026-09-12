"""Verified external command effects shared by independent INI postprocessors."""
from __future__ import annotations

import re

_HEADER = re.compile(r"^\[([^\]]+)\]$")
_REFERENCE = re.compile(
    r"^(?:resource|pool)[\w\\]+(?:\[[^\]\r\n]+\])?\s*=\s*"
    r"(?:ref|reference)\s+(?:resource|pool)[\w\\]+(?:\[[^\]\r\n]+\])?$", re.I)
_APPLY = r"commandlist\efmiv1\shapekeys_apply"
_CALLBACK = r"commandlist\efmiv1\callback_shapekeys_attachcomponent"


def preserved_graphics_calls(text):
    """Certify the native shape updater, never arbitrary compute/helper names.

    The EFMIv1 ShapeKeys_Apply ABI updates position pools using compute shaders,
    preserves graphics bindings and restores its x0/y0/z0/w0 scratch parameters.
    It does not draw or modify the native outline parameter. Its mod-provided
    callback is the exception: only reference-only callback bodies are accepted.
    Missing, rebound or edited callbacks keep the normal unknown-call barrier.
    """
    bodies, assignments = {}, []
    current = None
    for line in text.splitlines():
        code = line.split(';', 1)[0].strip().casefold()
        if not code:
            continue
        header = _HEADER.fullmatch(code)
        if header:
            current = header[1]
            bodies.setdefault(current, [])
        else:
            if current is not None:
                bodies[current].append(code)
            if '=' in code:
                left, right = (part.strip() for part in code.split('=', 1))
                assignments.append((re.sub(r'^post\s+', 'post ', left), right))
    if (_APPLY in bodies
            or any(left.removeprefix('post ') == _APPLY for left, _ in assignments)
            or any(left == 'namespace' and right == 'efmiv1' for left, right in assignments)
            or any(not _REFERENCE.fullmatch(code) for code in bodies.get(_CALLBACK, ()))):
        return frozenset()
    targets = set()
    for left, right in assignments:
        if left.removeprefix('post ') != _CALLBACK:
            continue
        match = re.fullmatch(r'(?:ref|reference)\s+(commandlist[\w\\]+)', right)
        if left != _CALLBACK or match is None:
            return frozenset()
        targets.add(match[1])
    if not targets:
        return frozenset()
    for target in targets:
        if target not in bodies or any(not _REFERENCE.fullmatch(code) for code in bodies[target]):
            return frozenset()
        if any(left.removeprefix('post ') == target for left, _ in assignments):
            return frozenset()
    return frozenset({_APPLY})
