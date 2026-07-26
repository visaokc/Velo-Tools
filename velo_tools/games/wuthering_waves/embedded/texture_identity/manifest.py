"""Compact r16 texture identity manifest construction."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .fingerprint import FingerprintError
from .runtime_fingerprint import fingerprint_dds_r16
from ..slot_textures.dds_meta import read_dds_meta


MANIFEST_FILENAME = "TextureIdentityManifest.json"
SCHEMA_VERSION = 1
_DDS_HASH = re.compile(r"\bt=([0-9a-fA-F]{8})\b")


def build_manifest(
    object_directory: str | Path,
    object_hash: str = "",
    *,
    source_profile: str = "",
    capture: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the export-facing inventory from DDS files that actually exist."""
    del object_hash, source_profile, capture
    object_directory = Path(object_directory)
    textures: dict[str, dict[str, str]] = {}

    for dds_path in sorted(
        object_directory.glob("*.dds"),
        key=lambda path: path.name.lower(),
    ):
        match = _DDS_HASH.search(dds_path.name)
        if match is None:
            continue
        texture_hash = match.group(1).lower()
        meta = read_dds_meta(dds_path)
        if meta is None:
            continue
        fingerprint = fingerprint_dds_r16(dds_path)
        identity = {
            "fingerprint": fingerprint,
            "format": meta.format,
        }
        existing = textures.get(texture_hash)
        if existing is not None and existing != identity:
            raise FingerprintError(
                f"Conflicting DDS files share texture Hash {texture_hash}"
            )
        textures[texture_hash] = identity

    return {
        "schema_version": SCHEMA_VERSION,
        "textures": textures,
    }


def write_manifest(
    object_directory: str | Path,
    object_hash: str = "",
    *,
    source_profile: str = "",
    capture: Mapping[str, Any] | None = None,
) -> Path:
    object_directory = Path(object_directory)
    manifest = build_manifest(
        object_directory,
        object_hash,
        source_profile=source_profile,
        capture=capture,
    )
    path = object_directory / MANIFEST_FILENAME
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def refresh_manifest(
    object_directory: str | Path,
    *,
    source_directories: tuple[str | Path, ...] = (),
) -> Path:
    """Rebuild the compact inventory after a form or Cross-Scene DDS merge."""
    del source_directories
    return write_manifest(object_directory)
