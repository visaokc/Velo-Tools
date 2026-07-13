"""Schema-v3 contract for a self-contained WWMI cross-scene aggregate root."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict


MANIFEST_FILENAME = "CrossSceneManifest.json"
LEGACY_ROUTING_FILENAME = "CrossSceneRouting.json"
SCHEMA_VERSION = 3

_HASH_RE = re.compile(r"^[0-9a-fA-F]{8}$")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")


class CrossSceneManifestError(ValueError):
    pass


def _walk(value: Any, path: str = "$"):
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, f"{path}[{index}]")


def _component_map(value: Any, path: str) -> Dict[int, int]:
    if not isinstance(value, dict):
        raise CrossSceneManifestError(f"{path} must be an object")
    result: Dict[int, int] = {}
    for local, merged in value.items():
        try:
            local_id = int(local)
            merged_id = int(merged)
        except (TypeError, ValueError) as exc:
            raise CrossSceneManifestError(
                f"{path} contains a non-integer component mapping") from exc
        if local_id < 0 or merged_id < 0:
            raise CrossSceneManifestError(
                f"{path} contains a negative component id")
        if local_id in result:
            raise CrossSceneManifestError(
                f"{path} contains duplicate local Component {local_id}")
        result[local_id] = merged_id
    return result


def validate_manifest(data: Any) -> dict:
    if not isinstance(data, dict):
        raise CrossSceneManifestError("cross-scene manifest must be a JSON object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CrossSceneManifestError(
            "unsupported cross-scene manifest schema; re-run Cross-Scene Merge "
            f"to create schema v{SCHEMA_VERSION}")
    if not isinstance(data.get("base"), dict):
        raise CrossSceneManifestError("manifest.base must be an object")
    if not isinstance(data["base"].get("native_metadata"), dict):
        raise CrossSceneManifestError(
            "manifest.base.native_metadata must be an object")
    if not isinstance(data["base"].get("native_stu"), dict):
        raise CrossSceneManifestError(
            "manifest.base.native_stu must be an object")
    runtime_ibs = data.get("runtime_ibs")
    if not isinstance(runtime_ibs, list):
        raise CrossSceneManifestError("manifest.runtime_ibs must be an array")

    seen_hashes = set()
    for index, entry in enumerate(runtime_ibs):
        path = f"manifest.runtime_ibs[{index}]"
        if not isinstance(entry, dict):
            raise CrossSceneManifestError(f"{path} must be an object")
        kind = entry.get("kind")
        if kind not in {"fold", "own_buffer", "editable"}:
            raise CrossSceneManifestError(
                f"{path}.kind must be fold, own_buffer, or editable")
        ib_hash = str(entry.get("ib_hash") or "")
        if not _HASH_RE.fullmatch(ib_hash):
            raise CrossSceneManifestError(
                f"{path}.ib_hash must be exactly eight hexadecimal digits")
        key = ib_hash.casefold()
        if key in seen_hashes:
            raise CrossSceneManifestError(
                f"manifest contains duplicate IB hash {ib_hash}")
        seen_hashes.add(key)
        if not isinstance(entry.get("native_metadata"), dict):
            raise CrossSceneManifestError(
                f"{path}.native_metadata must be an object")
        if not isinstance(entry.get("native_stu"), dict):
            raise CrossSceneManifestError(
                f"{path}.native_stu must be an object")
        component_map = _component_map(
            entry.get("component_map"), f"{path}.component_map")
        component_count = len(entry["native_metadata"].get("components") or [])
        if set(component_map) != set(range(component_count)):
            raise CrossSceneManifestError(
                f"{path}.component_map must cover every native component exactly once")

    for path, value in _walk(data):
        if path.endswith(".source_folder"):
            raise CrossSceneManifestError(
                f"{path} is forbidden in self-contained schema v3")
        if not isinstance(value, str):
            continue
        normalized = value.replace("\\", "/").casefold()
        if "scene_ibs/" in normalized:
            raise CrossSceneManifestError(
                f"{path} references forbidden scene_ibs storage")
        if _ABSOLUTE_PATH_RE.match(value):
            raise CrossSceneManifestError(
                f"{path} contains a machine-local absolute path")
    return data


def load_manifest(root: Path | str) -> dict:
    root = Path(root)
    path = root / MANIFEST_FILENAME
    if not path.is_file():
        legacy = root / LEGACY_ROUTING_FILENAME
        if legacy.is_file():
            raise CrossSceneManifestError(
                "检测到旧版 CrossSceneRouting.json schema v2。新版跨场景导出不再读取 "
                "scene_ibs 子目录；请使用当前 Velo Tools 重新执行“合并跨场景”。")
        raise CrossSceneManifestError(
            f"聚合根缺少 {MANIFEST_FILENAME}；请重新执行“合并跨场景”。")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CrossSceneManifestError(
            f"failed to read {MANIFEST_FILENAME}: {exc}") from exc
    return validate_manifest(data)


def write_manifest(root: Path | str, data: dict) -> Path:
    root = Path(root)
    validate_manifest(data)
    path = root / MANIFEST_FILENAME
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
