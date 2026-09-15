"""WWMI texture collection policy for author-managed source folders."""

from __future__ import annotations

import os
from pathlib import Path
import re


_NEW_TEXTURE_RE = re.compile(
    r"^Components-\d+(?:-\d+)*\s+t=([a-f0-9]{8})"
    r"(?:\s+[^\\/]*)?\.(?:dds|jpg)$",
    re.I,
)
_OLD_TEXTURE_RE = re.compile(
    r"^.*component_\d+-ps-t\d+-([a-f0-9]{8})"
    r"(?:\s+[^\\/]*)?\.(?:dds|jpg)$",
    re.I,
)
_PATCHES = []


def managed_texture_hash(filename: str) -> str | None:
    """Return the game texture hash only for stock WWMI texture names."""
    suffix = Path(filename).suffix.casefold()
    if suffix not in {".dds", ".jpg"}:
        return None
    for pattern in (_NEW_TEXTURE_RE, _OLD_TEXTURE_RE):
        match = pattern.match(filename)
        if match is not None:
            return match.group(1).lower()
    return None


def get_managed_textures(object_source_folder: Path, exclude_hashes, *, texture_type=None):
    """Collect only exporter-owned textures and ignore arbitrary author files."""
    if texture_type is None:
        from .._wwmi_core.blender_export.texture_collector import Texture
        texture_type = Texture

    excluded = {str(value).lower() for value in (exclude_hashes or ())}
    textures = {}
    for filename in sorted(os.listdir(object_source_folder), key=str.casefold):
        texture_hash = managed_texture_hash(filename)
        if texture_hash is None or texture_hash in excluded:
            continue
        textures[texture_hash] = texture_type(
            hash=texture_hash,
            path=Path(object_source_folder) / filename,
            filename=filename,
        )
    return list(textures.values())


def install() -> None:
    """Patch imported WWMI collector bindings without modifying vendored core."""
    if _PATCHES:
        return
    from .._wwmi_core.blender_export import blender_export, texture_collector

    for module in (texture_collector, blender_export):
        _PATCHES.append((module, module.get_textures))
        module.get_textures = get_managed_textures


def remove() -> None:
    while _PATCHES:
        module, original = _PATCHES.pop()
        module.get_textures = original
