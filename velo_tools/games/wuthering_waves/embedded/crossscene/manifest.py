"""Schema-v3 contract for a self-contained WWMI cross-scene aggregate root."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping


MANIFEST_FILENAME = "CrossSceneManifest.json"
LEGACY_ROUTING_FILENAME = "CrossSceneRouting.json"
SCHEMA_VERSION = 3

_HASH_RE = re.compile(r"^[0-9a-fA-F]{8}$")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")
_COMPONENT_KEY_RE = re.compile(r"^Component\s+(\d+)$", re.I)
_DDS_NAME_RE = re.compile(
    r"^Components-(\d+(?:-\d+)*)\s+t=([0-9a-fA-F]{8})(?:\s|\.|$)",
    re.I,
)


class CrossSceneManifestError(ValueError):
    pass


@dataclass(frozen=True)
class RootDDS:
    path: Path
    name: str
    texture_hash: str
    component_ids: tuple[int, ...]
    digest: str


@dataclass(frozen=True)
class CrossSceneRoot:
    path: Path
    manifest: Mapping[str, Any]
    metadata: Mapping[str, Any]
    texture_usage: Mapping[str, Any]
    dds_catalog: Mapping[str, RootDDS]


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
    base = data["base"]
    if not _HASH_RE.fullmatch(str(base.get("vb0_hash") or "")):
        raise CrossSceneManifestError(
            "manifest.base.vb0_hash must be exactly eight hexadecimal digits")
    try:
        component_count = int(base.get("component_count"))
    except (TypeError, ValueError) as exc:
        raise CrossSceneManifestError(
            "manifest.base.component_count must be a non-negative integer") from exc
    if component_count < 0:
        raise CrossSceneManifestError(
            "manifest.base.component_count must be a non-negative integer")
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
        runtime_layout = entry.get("runtime_layout")
        if not isinstance(runtime_layout, dict):
            raise CrossSceneManifestError(
                f"{path}.runtime_layout must be an object")
        components = runtime_layout.get("components")
        if not isinstance(components, list):
            raise CrossSceneManifestError(
                f"{path}.runtime_layout.components must be an array")
        component_map = _component_map(
            entry.get("component_map"), f"{path}.component_map")
        if set(component_map) != set(range(len(components))):
            raise CrossSceneManifestError(
                f"{path}.component_map must cover every native component exactly once")

    for path, value in _walk(data):
        if path.endswith((".native_metadata", ".native_stu")):
            raise CrossSceneManifestError(
                "检测到携带重复 Metadata/STU 的 draft schema v3。请使用当前 "
                "Velo Tools 重新执行“合并跨场景”。")
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


def _load_root_json(root: Path, filename: str) -> dict:
    path = root / filename
    if not path.is_file():
        raise CrossSceneManifestError(
            f"聚合根缺少 {filename}；请重新执行“合并跨场景”。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CrossSceneManifestError(f"failed to read {filename}: {exc}") from exc
    if not isinstance(value, dict):
        raise CrossSceneManifestError(f"{filename} must contain a JSON object")
    return value


def _usage_component_ids(value: Any) -> Dict[str, set[int]]:
    result: Dict[str, set[int]] = {}

    def walk(item: Any, component_id: int | None = None) -> None:
        if isinstance(item, dict):
            texture_hash = str(item.get("hash") or "").lower()
            # A verified inherited service seat is effective ownership even
            # though the binding itself is intentionally recorded as stale.
            if (component_id is not None
                    and (item.get("fresh") is not False
                         or item.get("verified_inherited") is True)
                    and _HASH_RE.fullmatch(texture_hash)):
                result.setdefault(texture_hash, set()).add(component_id)
            for key, child in item.items():
                match = _COMPONENT_KEY_RE.fullmatch(str(key))
                walk(child, int(match.group(1)) if match else component_id)
        elif isinstance(item, list):
            for child in item:
                walk(child, component_id)

    walk(value)
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_dds_catalog(root: Path, texture_usage: Mapping[str, Any]) -> Dict[str, RootDDS]:
    ownership = _usage_component_ids(texture_usage)
    catalog: Dict[str, RootDDS] = {}
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.casefold() != ".dds":
            continue
        match = _DDS_NAME_RE.match(path.name)
        if match is None:
            raise CrossSceneManifestError(
                f"root DDS filename has no canonical Component ownership: {path.name}")
        component_ids = tuple(sorted({int(value) for value in match.group(1).split("-")}))
        texture_hash = match.group(2).lower()
        expected = tuple(sorted(ownership.get(texture_hash, set())))
        if component_ids != expected:
            raise CrossSceneManifestError(
                "DDS Component ownership differs from root ShaderTextureUsage.json: "
                f"{path.name} has {list(component_ids)}, STU has {list(expected)}")
        item = RootDDS(
            path=path,
            name=path.name,
            texture_hash=texture_hash,
            component_ids=component_ids,
            digest=_sha256(path),
        )
        previous = catalog.get(texture_hash)
        if previous is not None:
            if previous.digest != item.digest:
                raise CrossSceneManifestError(
                    f"root DDS hash {texture_hash} has conflicting payloads: "
                    f"{previous.name}, {item.name}")
            raise CrossSceneManifestError(
                f"root DDS hash {texture_hash} has multiple canonical filenames: "
                f"{previous.name}, {item.name}")
        catalog[texture_hash] = item
    return catalog


def load_cross_scene_root(root: Path | str) -> CrossSceneRoot:
    root = Path(root)
    manifest = load_manifest(root)
    metadata = _load_root_json(root, "Metadata.json")
    texture_usage = _load_root_json(root, "ShaderTextureUsage.json")
    components = metadata.get("components")
    if not isinstance(components, list):
        raise CrossSceneManifestError("Metadata.json components must be an array")
    base_count = int(manifest["base"]["component_count"])
    if len(components) < base_count:
        raise CrossSceneManifestError(
            "Metadata.json has fewer components than manifest.base.component_count")
    for entry in manifest.get("runtime_ibs") or []:
        for global_id in _component_map(
                entry.get("component_map"), "runtime component_map").values():
            if global_id >= len(components):
                raise CrossSceneManifestError(
                    f"runtime IB {entry['ib_hash']} maps to missing global Component {global_id}")
    return CrossSceneRoot(
        path=root,
        manifest=manifest,
        metadata=metadata,
        texture_usage=texture_usage,
        dds_catalog=_load_dds_catalog(root, texture_usage),
    )


def write_manifest(root: Path | str, data: dict) -> Path:
    root = Path(root)
    validate_manifest(data)
    path = root / MANIFEST_FILENAME
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path
