"""Shared source-folder texture ownership policy for game exporters."""

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


def managed_texture_hash(filename: str) -> str | None:
    """Return the target hash only for stock exporter-managed texture names."""
    suffix = Path(filename).suffix.casefold()
    if suffix not in {".dds", ".jpg"}:
        return None
    for pattern in (_NEW_TEXTURE_RE, _OLD_TEXTURE_RE):
        match = pattern.match(filename)
        if match is not None:
            return match.group(1).lower()
    return None


def collect_managed_textures(
        object_source_folder: Path,
        exclude_hashes,
        *,
        texture_type,
):
    """Collect standard DDS/JPG assets while ignoring author-managed extras."""
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
