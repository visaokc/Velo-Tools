"""Load Unreal texture asset paths captured alongside a frame dump."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


MANIFEST_FILENAME = "TextureAssetManifest.jsonl"
_TEXTURE_HASH = re.compile(r"^[0-9a-f]{8}$")


class AssetPathManifestError(ValueError):
    pass


def load_asset_paths(dump_root: str | Path | None) -> dict[str, str]:
    """Return dump-file stems mapped to canonical Unreal object paths."""
    if dump_root is None:
        return {}
    manifest_path = Path(dump_root) / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return {}

    result: dict[str, str] = {}
    for line_number, line in enumerate(
            manifest_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssetPathManifestError(
                f"{MANIFEST_FILENAME}:{line_number} is not valid JSON"
            ) from exc
        dump_name = str(record.get("dump_name") or "").strip()
        asset_path = str(record.get("asset_path") or "").strip()
        if not dump_name or not asset_path:
            continue
        key = Path(dump_name).stem.casefold()
        previous = result.get(key)
        if previous is not None and previous != asset_path:
            raise AssetPathManifestError(
                f"{MANIFEST_FILENAME} maps {Path(dump_name).stem} to "
                f"conflicting asset paths"
            )
        result[key] = asset_path
    return result


def asset_path_for_dump_file(
        asset_paths: dict[str, str],
        dump_file: str | Path,
) -> str:
    return asset_paths.get(Path(dump_file).stem.casefold(), "")


def _texture_records(value: Any):
    if isinstance(value, dict):
        texture_hash = str(value.get("hash") or "").strip().lower()
        if _TEXTURE_HASH.fullmatch(texture_hash):
            yield value
        for child in value.values():
            yield from _texture_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _texture_records(child)


def enrich_existing_texture_records(
        usage: dict,
        source_folder: str | Path,
        paths_by_hash: dict[str, str],
        *,
        resolve_missing_filenames: bool = False,
) -> None:
    """Attach paths only to records backed by a real, named extracted DDS."""
    source_folder = Path(source_folder)
    filenames_by_hash: dict[str, str] = {}
    if resolve_missing_filenames:
        for texture_hash in paths_by_hash:
            matches = sorted(source_folder.glob(f"* t={texture_hash}.*"))
            if len(matches) == 1:
                filenames_by_hash[texture_hash] = matches[0].name

    for record in _texture_records(usage):
        texture_hash = str(record.get("hash") or "").strip().lower()
        filename = str(record.get("filename") or "").strip()
        if not filename and resolve_missing_filenames:
            filename = filenames_by_hash.get(texture_hash, "")
            if filename:
                record["filename"] = filename
        if not filename or not (source_folder / filename).is_file():
            record.pop("asset_path", None)
            continue
        asset_path = paths_by_hash.get(texture_hash, "")
        if asset_path:
            record["asset_path"] = asset_path
        else:
            record.pop("asset_path", None)
