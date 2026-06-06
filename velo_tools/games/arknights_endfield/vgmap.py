"""Velo sidecar support for unified Endfield vertex-group maps."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


FILENAME = "VertexGroupMap.json"
FORMAT_VERSION = 1
METADATA_FORMAT_VERSION = 3


def _zh(text: str) -> str:
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return text


class VertexGroupMapError(ValueError):
    pass


@dataclass
class ComponentVertexGroupMap:
    mesh_name: str
    ib_hash: str
    vb0_hash: str
    vg_map: dict[str, int]


@dataclass
class VertexGroupMap:
    format_version: int
    metadata_format_version: int
    components: list[ComponentVertexGroupMap]

    def as_json(self) -> str:
        return json.dumps(asdict(self), indent=4, ensure_ascii=False)


def _component_identity(component: Any) -> tuple[str, str, str]:
    return (
        str(getattr(component, "mesh_name", "") or ""),
        str(getattr(component, "ib_hash", "") or ""),
        str(getattr(component, "vb0_hash", "") or ""),
    )


def _normalize_map(raw: Any) -> dict[str, int]:
    result: dict[str, int] = {}
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        try:
            local_id = int(key)
            global_id = int(value)
        except (TypeError, ValueError):
            continue
        if local_id < 0 or global_id < 0:
            continue
        result[str(local_id)] = global_id
    return dict(sorted(result.items(), key=lambda item: int(item[0])))


def _metadata_version(metadata: Any) -> int:
    return int(getattr(metadata, "format_version", METADATA_FORMAT_VERSION) or METADATA_FORMAT_VERSION)


def build_from_metadata(metadata: Any) -> VertexGroupMap:
    return build_from_metadata_and_maps(
        metadata,
        [
            getattr(component, "vg_map", {}) or {}
            for component in getattr(metadata, "components", []) or []
        ],
    )


def build_from_metadata_and_maps(metadata: Any, component_maps: list[dict[int, int]]) -> VertexGroupMap:
    components: list[ComponentVertexGroupMap] = []
    metadata_components = list(getattr(metadata, "components", []) or [])
    for component, component_map in zip(metadata_components, component_maps):
        mesh_name, ib_hash, vb0_hash = _component_identity(component)
        components.append(
            ComponentVertexGroupMap(
                mesh_name=mesh_name,
                ib_hash=ib_hash,
                vb0_hash=vb0_hash,
                vg_map=_normalize_map(component_map or {}),
            )
        )
    return VertexGroupMap(
        format_version=FORMAT_VERSION,
        metadata_format_version=_metadata_version(metadata),
        components=components,
    )


def write_map(folder: Path, vertex_group_map: VertexGroupMap) -> Path:
    path = Path(folder) / FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(vertex_group_map.as_json(), encoding="utf-8")
    return path


def write_from_metadata(folder: Path, metadata: Any) -> Path:
    return write_map(Path(folder), build_from_metadata(metadata))


def _set_attr_if_missing(obj: Any, name: str, value: Any) -> bool:
    if getattr(obj, name, None) is None:
        setattr(obj, name, value)
        return True
    return False


def upgrade_legacy_metadata_object(metadata: Any) -> bool:
    """Fill v3 metadata fields that older Velo/EFMI extractions lacked."""
    changed = False
    try:
        version = int(getattr(metadata, "format_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version < METADATA_FORMAT_VERSION:
        setattr(metadata, "format_version", METADATA_FORMAT_VERSION)
        changed = True

    if _set_attr_if_missing(metadata, "export_format", {}):
        changed = True

    for component_id, component in enumerate(getattr(metadata, "components", []) or []):
        if _set_attr_if_missing(component, "mesh_name", f"Component {component_id}"):
            changed = True
        if _set_attr_if_missing(component, "cpu_posed", False):
            changed = True
        if _set_attr_if_missing(component, "vg_map", {}):
            changed = True
        if _set_attr_if_missing(component, "lods", []):
            changed = True
        mesh_name = str(getattr(component, "mesh_name", None) or f"Component {component_id}")
        for lod_id, lod in enumerate(getattr(component, "lods", []) or []):
            if _set_attr_if_missing(lod, "lod_object_name", f"{mesh_name} LOD {lod_id + 1}"):
                changed = True
            if _set_attr_if_missing(lod, "vertex_offset", 0):
                changed = True
            if _set_attr_if_missing(lod, "index_offset", 0):
                changed = True
            if _set_attr_if_missing(lod, "vg_map", {}):
                changed = True
            if _set_attr_if_missing(lod, "vb_formats", {}):
                changed = True
    return changed


def _upgrade_legacy_metadata_dict(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    try:
        version = int(raw.get("format_version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version < METADATA_FORMAT_VERSION:
        raw["format_version"] = METADATA_FORMAT_VERSION
        changed = True
    if raw.get("export_format") is None:
        raw["export_format"] = {}
        changed = True
    shapekey_defaults = {
        "offsets_hash": "",
        "scale_hash": "",
        "vertex_count": 0,
        "dispatch_y": 0,
        "checksum": 0,
    }
    if raw.get("shapekeys") is None:
        raw["shapekeys"] = {
            **shapekey_defaults,
        }
        changed = True
    elif isinstance(raw.get("shapekeys"), dict):
        for key, value in shapekey_defaults.items():
            if raw["shapekeys"].get(key) is None:
                raw["shapekeys"][key] = value
                changed = True

    for component_id, component in enumerate(raw.get("components") or []):
        if component.get("mesh_name") is None:
            component["mesh_name"] = f"Component {component_id}"
            changed = True
        if component.get("cpu_posed") is None:
            component["cpu_posed"] = False
            changed = True
        if component.get("vg_map") is None:
            component["vg_map"] = {}
            changed = True
        if component.get("lods") is None:
            component["lods"] = []
            changed = True
        mesh_name = str(component.get("mesh_name") or f"Component {component_id}")
        for lod_id, lod in enumerate(component.get("lods") or []):
            if lod.get("lod_object_name") is None:
                lod["lod_object_name"] = f"{mesh_name} LOD {lod_id + 1}"
                changed = True
            if lod.get("vertex_offset") is None:
                lod["vertex_offset"] = 0
                changed = True
            if lod.get("index_offset") is None:
                lod["index_offset"] = 0
                changed = True
            if lod.get("vg_map") is None:
                lod["vg_map"] = {}
                changed = True
            if lod.get("vb_formats") is None:
                lod["vb_formats"] = {}
                changed = True
    return raw, changed


def upgrade_legacy_metadata_file(metadata_path: Path) -> bool:
    metadata_path = Path(metadata_path)
    raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    raw, changed = _upgrade_legacy_metadata_dict(raw)
    if changed:
        metadata_path.write_text(json.dumps(raw, indent=4, ensure_ascii=False), encoding="utf-8")
    return changed


def read_map(folder: Path) -> VertexGroupMap:
    path = Path(folder) / FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VertexGroupMapError(
            _zh(f"缺少 {FILENAME}。Merged 模式需要统一顶点组映射；请重新提取、执行显式转换，或改用 Per-Component 导入。")
        ) from exc
    except Exception as exc:
        raise VertexGroupMapError(_zh(f"读取 {FILENAME} 失败：{exc}")) from exc

    try:
        components = [
            ComponentVertexGroupMap(
                mesh_name=str(item.get("mesh_name", "") or ""),
                ib_hash=str(item.get("ib_hash", "") or ""),
                vb0_hash=str(item.get("vb0_hash", "") or ""),
                vg_map=_normalize_map(item.get("vg_map", {}) or {}),
            )
            for item in raw.get("components", [])
        ]
        return VertexGroupMap(
            format_version=int(raw.get("format_version", 0)),
            metadata_format_version=int(raw.get("metadata_format_version", 0)),
            components=components,
        )
    except Exception as exc:
        raise VertexGroupMapError(_zh(f"{FILENAME} 格式无效：{exc}")) from exc


def validate_against_metadata(vertex_group_map: VertexGroupMap, metadata: Any) -> None:
    if vertex_group_map.format_version != FORMAT_VERSION:
        raise VertexGroupMapError(
            _zh(f"{FILENAME} format_version={vertex_group_map.format_version} 不受支持，当前需要 {FORMAT_VERSION}。")
        )

    metadata_components = list(getattr(metadata, "components", []) or [])
    if len(vertex_group_map.components) != len(metadata_components):
        raise VertexGroupMapError(
            _zh(f"{FILENAME} 的组件数量 ({len(vertex_group_map.components)}) 与 Metadata.json ({len(metadata_components)}) 不一致。")
        )

    for idx, (entry, component) in enumerate(zip(vertex_group_map.components, metadata_components)):
        mesh_name, ib_hash, vb0_hash = _component_identity(component)
        mismatches = []
        if entry.mesh_name and mesh_name and entry.mesh_name != mesh_name:
            mismatches.append(f"mesh_name {entry.mesh_name!r} != {mesh_name!r}")
        if entry.ib_hash and ib_hash and entry.ib_hash != ib_hash:
            mismatches.append(f"ib_hash {entry.ib_hash!r} != {ib_hash!r}")
        if entry.vb0_hash and vb0_hash and entry.vb0_hash != vb0_hash:
            mismatches.append(f"vb0_hash {entry.vb0_hash!r} != {vb0_hash!r}")
        if mismatches:
            raise VertexGroupMapError(
                _zh(f"{FILENAME} Component {idx} 与 Metadata.json 不匹配：{'; '.join(mismatches)}。请重新生成映射文件。")
            )
        if not entry.vg_map and not bool(getattr(component, "cpu_posed", False)):
            raise VertexGroupMapError(
                _zh(f"{FILENAME} Component {idx} 没有 vg_map。Merged 模式不可用，请重新生成映射或改用 Per-Component。")
            )


def load_for_metadata(folder: Path, metadata: Any) -> VertexGroupMap:
    vertex_group_map = read_map(Path(folder))
    validate_against_metadata(vertex_group_map, metadata)
    return vertex_group_map


def apply_to_metadata(metadata: Any, vertex_group_map: VertexGroupMap) -> None:
    for entry, component in zip(vertex_group_map.components, getattr(metadata, "components", []) or []):
        component.vg_map = dict(entry.vg_map)


def global_palette(vertex_group_map: VertexGroupMap) -> list[int]:
    palette = set()
    for component in vertex_group_map.components:
        for value in component.vg_map.values():
            try:
                palette.add(int(value))
            except (TypeError, ValueError):
                continue
    return sorted(palette)


def convert_legacy_metadata(folder: Path, metadata: Any) -> Path:
    component_maps = [
        getattr(component, "vg_map", {}) or {}
        for component in getattr(metadata, "components", []) or []
    ]
    upgrade_legacy_metadata_object(metadata)
    vertex_group_map = build_from_metadata_and_maps(metadata, component_maps)
    vertex_group_map.metadata_format_version = METADATA_FORMAT_VERSION
    if not any(component.vg_map for component in vertex_group_map.components):
        raise VertexGroupMapError(_zh("旧 Metadata.json 中没有可转换的 component.vg_map。"))
    folder = Path(folder)
    metadata_path = folder / "Metadata.json"
    if metadata_path.is_file():
        upgrade_legacy_metadata_file(metadata_path)
    return write_map(folder, vertex_group_map)
