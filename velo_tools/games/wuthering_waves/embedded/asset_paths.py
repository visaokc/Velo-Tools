"""Load Unreal texture asset paths captured alongside a frame dump."""

from __future__ import annotations

import json
from pathlib import Path


MANIFEST_FILENAME = "TextureAssetManifest.jsonl"


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
