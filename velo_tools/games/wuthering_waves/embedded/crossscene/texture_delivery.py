"""Preflight and delivery helpers for cross-scene DDS assets."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import shutil
from typing import Dict, Iterable, List, Optional, Tuple


_HASH_RE = re.compile(r"t=([0-9a-fA-F]+)")


class TextureDeliveryError(ValueError):
    """Raised before output mutation when DDS payload identity is ambiguous."""


@dataclass(frozen=True)
class DdsFile:
    path: Path
    name: str
    texture_hash: Optional[str]
    digest: str


@dataclass(frozen=True)
class TextureDeliveryInventory:
    root_files: Tuple[DdsFile, ...]
    root_unique_hashes: Tuple[str, ...]
    root_duplicate_identical: Tuple[str, ...]
    root_unhashed_files: Tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _texture_hash(name: str) -> Optional[str]:
    match = _HASH_RE.search(name)
    return match.group(1).lower() if match else None


def _scan_dds(folder: Path) -> List[DdsFile]:
    if not folder.is_dir():
        return []
    return [
        DdsFile(
            path=path,
            name=path.name,
            texture_hash=_texture_hash(path.name),
            digest=_sha256(path),
        )
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold())
        if path.is_file() and path.suffix.casefold() == ".dds"
    ]


def _group_by_hash(files: Iterable[DdsFile]) -> Dict[str, List[DdsFile]]:
    grouped: Dict[str, List[DdsFile]] = {}
    for item in files:
        if item.texture_hash is not None:
            grouped.setdefault(item.texture_hash, []).append(item)
    return grouped


def build_delivery_inventory(texture_root, mods) -> TextureDeliveryInventory:
    """Validate root/per-IB DDS identity before any output is cleaned or written."""

    root_path = Path(texture_root) if texture_root is not None else None
    root_files = _scan_dds(root_path) if root_path is not None else []
    root_by_hash = _group_by_hash(root_files)
    duplicate_identical = []
    for texture_hash, files in sorted(root_by_hash.items()):
        digests = {item.digest for item in files}
        if len(digests) > 1:
            names = ", ".join(item.name for item in files)
            raise TextureDeliveryError(
                f"root DDS hash {texture_hash} has conflicting payloads: {names}"
            )
        if len(files) > 1:
            duplicate_identical.append(texture_hash)

    per_ib_by_hash: Dict[str, List[DdsFile]] = {}
    for mod in mods or ():
        for item in _scan_dds(Path(mod) / "Textures"):
            if item.texture_hash is not None:
                per_ib_by_hash.setdefault(item.texture_hash, []).append(item)
    for texture_hash, files in sorted(per_ib_by_hash.items()):
        if texture_hash in root_by_hash:
            continue
        if len({item.digest for item in files}) > 1:
            names = ", ".join(str(item.path) for item in files)
            raise TextureDeliveryError(
                f"per-IB DDS hash {texture_hash} has conflicting payloads without a root copy: {names}"
            )

    return TextureDeliveryInventory(
        root_files=tuple(root_files),
        root_unique_hashes=tuple(sorted(root_by_hash)),
        root_duplicate_identical=tuple(duplicate_identical),
        root_unhashed_files=tuple(
            item.name for item in root_files if item.texture_hash is None
        ),
    )


def deliver_root_dds(inventory: TextureDeliveryInventory, textures_dir) -> dict:
    """Copy missing root files while preserving every existing author-edited output."""

    output = Path(textures_dir)
    output.mkdir(parents=True, exist_ok=True)
    copied = []
    reused = []
    preserved_modified = []
    for item in inventory.root_files:
        destination = output / item.name
        if destination.exists():
            if not destination.is_file():
                raise TextureDeliveryError(
                    f"DDS destination is not a file: {destination}"
                )
            if _sha256(destination) == item.digest:
                reused.append(item.name)
            else:
                preserved_modified.append(item.name)
            continue
        shutil.copy2(item.path, destination)
        copied.append(item.name)

    report = inspect_root_dds(inventory, output)
    report.update({
        "root_dds_copied": copied,
        "root_dds_reused": reused,
        "preserved_modified": preserved_modified,
    })
    return report


def inspect_root_dds(inventory: TextureDeliveryInventory, textures_dir) -> dict:
    """Report delivery state without mutating the output directory."""

    output = Path(textures_dir)
    output_files = (
        [path for path in output.iterdir() if path.is_file()]
        if output.is_dir() else []
    )
    root_names = {item.name.casefold() for item in inventory.root_files}
    root_by_name = {item.name.casefold(): item for item in inventory.root_files}
    reused = []
    preserved_modified = []
    for path in output_files:
        item = root_by_name.get(path.name.casefold())
        if item is None:
            continue
        if _sha256(path) == item.digest:
            reused.append(item.name)
        else:
            preserved_modified.append(item.name)
    missing = [
        item.name for item in inventory.root_files
        if not (output / item.name).is_file()
    ]
    return {
        "root_dds_files": len(inventory.root_files),
        "root_unique_hashes": len(inventory.root_unique_hashes),
        "root_duplicate_identical": list(inventory.root_duplicate_identical),
        "root_unhashed_files": list(inventory.root_unhashed_files),
        "root_dds_copied": [],
        "root_dds_reused": reused,
        "preserved_modified": preserved_modified,
        "root_dds_missing": missing,
        "textures_dds_files": sum(
            1 for path in output_files if path.suffix.casefold() == ".dds"
        ),
        "textures_non_dds_files": sum(
            1 for path in output_files if path.suffix.casefold() != ".dds"
        ),
        "tex_output_extras": sorted(
            path.name for path in output_files
            if path.suffix.casefold() == ".dds" and path.name.casefold() not in root_names
        ),
    }
