"""FrameAnalysis-backed CrossIB transparency classification.

This module deliberately uses rendered evidence instead of DDS filename or
pass-shape guesses:

* non-default OM blend state on the draw,
* a real DDS alpha channel with alpha < 255,
* and UVs from the draw's indexed geometry hitting that alpha region.
"""
from __future__ import annotations

import json
import re
import struct
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


_DRAW_FILE_RE = re.compile(
    r"^(?P<event>\d+)-ib=(?P<ib>[a-f0-9]+)(?:\([a-f0-9]+\))?"
    r"-vs=(?P<vs>[a-f0-9]+)-ps=(?P<ps>[a-f0-9]+)\.txt$",
    re.IGNORECASE,
)
_LOG_PREFIX_RE = re.compile(r"^(?P<event>\d{6})\s+(?P<body>.*)$")
_BLEND_RE = re.compile(r"OMSetBlendState\(pBlendState:(?P<value>0x[a-fA-F0-9]+)")
_DEPTH_RE = re.compile(r"OMSetDepthStencilState\(pDepthStencilState:(?P<value>0x[a-fA-F0-9]+)")
_RASTER_RE = re.compile(r"RSSetState\(pRasterizerState:(?P<value>0x[a-fA-F0-9]+)")
_VS_RE = re.compile(r"VSSetShader\(.*\)\s+hash=(?P<hash>[a-f0-9]+)", re.IGNORECASE)
_PS_RE = re.compile(r"PSSetShader\(.*\)\s+hash=(?P<hash>[a-f0-9]+)", re.IGNORECASE)
_DRAW_CALL_RE = re.compile(
    r"DrawIndexedInstanced\(IndexCountPerInstance:(?P<count>\d+),\s*"
    r"InstanceCount:(?P<instances>\d+),\s*"
    r"StartIndexLocation:(?P<start>\d+),\s*"
    r"BaseVertexLocation:(?P<base>-?\d+),\s*"
    r"StartInstanceLocation:(?P<first_instance>\d+)\)"
)
_TEXTURE_USAGE_RE = re.compile(
    r"^(?P<texture>[a-f0-9]{8})-vs=(?P<vs>[a-f0-9]+)-ps=(?P<ps>[a-f0-9]+)"
    r"-(?P<event>\d+)-(?P<format>[A-Z0-9_]+)$",
    re.IGNORECASE,
)
_TEXTURE_ASSET_RE = re.compile(
    r"^Components-(?P<components>[0-9-]+)\s+t=(?P<texture>[a-f0-9]{8})\s+"
    r"(?P<label>.+)\.dds$",
    re.IGNORECASE,
)
_COMPONENT_RE = re.compile(r"^Component\s+(?P<id>\d+)\.(?P<kind>fmt|vb|ib)$", re.IGNORECASE)


@dataclass(frozen=True)
class DrawFileRecord:
    event: int
    ib_hash: str
    vs_hash: str
    ps_hash: str


@dataclass(frozen=True)
class DrawState:
    event: int
    blend_state: Optional[str]
    depth_state: Optional[str]
    vs_hash: Optional[str]
    ps_hash: Optional[str]
    index_count: int
    start_index: int
    base_vertex: int
    raster_state: Optional[str] = None


@dataclass(frozen=True)
class TextureUsageRecord:
    component_id: int
    texture_hash: str
    vs_hash: str
    ps_hash: str
    event: int
    slot: str


@dataclass(frozen=True)
class TextureAsset:
    texture_hash: str
    components: frozenset[int]
    path: Path
    color_space_label: str


@dataclass(frozen=True)
class ComponentGeometry:
    component_id: int
    fmt_path: Path
    vb_path: Path
    ib_path: Path
    stride: int
    index_format: str
    texcoord_offset: Optional[int]
    texcoord_format: Optional[str]
    vertex_count: int
    index_count: int


@dataclass(frozen=True)
class TransparencyEvidence:
    component_id: int
    draw_event: int
    blend_state: str
    texture_hash: str
    texture_path: str
    alpha_min: int
    alpha_max: int
    uv_alpha_hit_ratio: float


@dataclass(frozen=True)
class _AlphaPixels:
    width: int
    height: int
    data: array

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def getpixel(self, xy) -> int:
        x, y = xy
        return int(self.data[int(y) * self.width + int(x)])


@dataclass
class TransparencyClassificationResult:
    frame_analysis_root: Path
    character_folder: Path
    default_blend_state: Optional[str]
    actual_transparent_components: set[int] = field(default_factory=set)
    translucent_without_usable_texture_components: set[int] = field(default_factory=set)
    evidence_by_component: dict[int, list[TransparencyEvidence]] = field(default_factory=dict)
    translucent_draws_by_component: dict[int, list[int]] = field(default_factory=dict)
    report_lines: list[str] = field(default_factory=list)

    def is_actual_transparent_component(self, component_id: int) -> bool:
        return int(component_id) in self.actual_transparent_components


def classify_frame_analysis_transparency(path, frame_root=None) -> TransparencyClassificationResult:
    frame_root, character_folder = _resolve_frame_analysis_paths(Path(path), frame_root)
    metadata = _read_json(character_folder / "Metadata.json")
    ib_to_component = _metadata_ib_to_component(metadata)
    draw_files = _scan_draw_files(frame_root)
    draw_states = _parse_log_draw_states(frame_root / "log.txt")
    usage = _load_texture_usage(character_folder)
    assets = _load_texture_assets(character_folder)
    geometries = _load_component_geometries(character_folder)

    mapped_draws = []
    for event, draw_file in draw_files.items():
        component_id = ib_to_component.get(draw_file.ib_hash.lower())
        draw_state = draw_states.get(event)
        if component_id is None or draw_state is None:
            continue
        mapped_draws.append((component_id, draw_file, draw_state))

    default_blend = _infer_default_blend_state(mapped_draws)
    result = TransparencyClassificationResult(
        frame_analysis_root=frame_root,
        character_folder=character_folder,
        default_blend_state=default_blend,
    )

    for component_id, draw_file, draw_state in mapped_draws:
        if not draw_state.blend_state or draw_state.blend_state == default_blend:
            continue
        result.translucent_draws_by_component.setdefault(component_id, []).append(draw_state.event)
        evidence = _classify_draw(
            component_id=component_id,
            draw_file=draw_file,
            draw_state=draw_state,
            usage=usage,
            assets=assets,
            geometry=geometries.get(component_id),
        )
        if evidence is None:
            result.translucent_without_usable_texture_components.add(component_id)
            continue
        result.actual_transparent_components.add(component_id)
        result.evidence_by_component.setdefault(component_id, []).append(evidence)

    for component_id in sorted(result.actual_transparent_components):
        evs = result.evidence_by_component.get(component_id, [])
        draws = ",".join(f"{ev.draw_event:06d}" for ev in evs)
        best = max((ev.uv_alpha_hit_ratio for ev in evs), default=0.0)
        result.report_lines.append(
            "actual transparent by BlendState + alpha + UV: "
            f"C{component_id} draws={draws} uv_alpha_hit={best:.1%}"
        )
    for component_id in sorted(result.translucent_without_usable_texture_components):
        draws = ",".join(
            f"{event:06d}" for event in result.translucent_draws_by_component.get(component_id, [])
        )
        result.report_lines.append(
            f"translucent draw but no usable component texture: C{component_id} draws={draws}"
        )
    return result


def _resolve_character_folder(path: Path) -> Path:
    """The folder holding Metadata.json: path itself, or the best Character* child."""
    if (path / "Metadata.json").is_file():
        return path
    candidates = [
        child for child in path.iterdir()
        if child.is_dir() and (child / "Metadata.json").is_file()
    ] if path.is_dir() else []
    if not candidates:
        raise FileNotFoundError(f"No character folder with Metadata.json under {path}")

    def score(candidate: Path):
        name = candidate.name.lower()
        is_character = 1 if name.startswith("character") else 0
        is_copy = 1 if ("副本" in candidate.name or "copy" in name) else 0
        return (is_character, -is_copy, _metadata_component_count(candidate), candidate.name)

    return max(candidates, key=score)


def _resolve_frame_analysis_paths(path: Path, frame_root_override=None) -> tuple[Path, Path]:
    path = path.resolve()

    # Explicit frame_root (e.g. the frame dump) for the non-co-located case: the
    # character folder (Metadata/DDS/geometry) and the frame dump (log.txt +
    # *-ib=*.txt) may live in different trees — resolve them independently.
    if frame_root_override is not None:
        frame_root = Path(frame_root_override).resolve()
        if not (frame_root / "log.txt").is_file():
            raise FileNotFoundError(f"FrameAnalysis log.txt not found under {frame_root}")
        return frame_root, _resolve_character_folder(path)

    if (path / "Metadata.json").is_file():
        character_folder = path
        frame_root = path.parent if (path.parent / "log.txt").is_file() else path
        return frame_root, character_folder

    frame_root = path
    if not (frame_root / "log.txt").is_file():
        raise FileNotFoundError(f"FrameAnalysis log.txt not found under {path}")
    return frame_root, _resolve_character_folder(frame_root)


def _metadata_component_count(folder: Path) -> int:
    try:
        components = _read_json(folder / "Metadata.json").get("components") or []
    except Exception:
        return 0
    return len(components) if isinstance(components, list) else 0


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _metadata_ib_to_component(metadata: dict) -> dict[str, int]:
    result: dict[str, int] = {}
    for component_id, component in enumerate(metadata.get("components") or []):
        if not isinstance(component, dict):
            continue
        ib_hash = str(component.get("ib_hash") or "").lower()
        if ib_hash:
            result[ib_hash] = component_id
        for lod in component.get("lods") or []:
            if isinstance(lod, dict):
                lod_hash = str(lod.get("ib_hash") or "").lower()
                if lod_hash:
                    result[lod_hash] = component_id
    return result


def _scan_draw_files(frame_root: Path) -> dict[int, DrawFileRecord]:
    records: dict[int, DrawFileRecord] = {}
    for path in frame_root.glob("*-ib=*.txt"):
        match = _DRAW_FILE_RE.match(path.name)
        if not match:
            continue
        event = int(match.group("event"))
        records[event] = DrawFileRecord(
            event=event,
            ib_hash=match.group("ib").lower(),
            vs_hash=match.group("vs").lower(),
            ps_hash=match.group("ps").lower(),
        )
    return records


def _parse_log_draw_states(log_path: Path) -> dict[int, DrawState]:
    current_blend = None
    current_depth = None
    current_raster = None
    current_vs = None
    current_ps = None
    result: dict[int, DrawState] = {}
    for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        prefix = _LOG_PREFIX_RE.match(raw_line)
        if not prefix:
            continue
        event = int(prefix.group("event"))
        body = prefix.group("body")

        blend_match = _BLEND_RE.search(body)
        if blend_match:
            current_blend = _normalize_hex(blend_match.group("value"))
        depth_match = _DEPTH_RE.search(body)
        if depth_match:
            current_depth = _normalize_hex(depth_match.group("value"))
        raster_match = _RASTER_RE.search(body)
        if raster_match:
            current_raster = _normalize_hex(raster_match.group("value"))
        vs_match = _VS_RE.search(body)
        if vs_match:
            current_vs = vs_match.group("hash").lower()
        ps_match = _PS_RE.search(body)
        if ps_match:
            current_ps = ps_match.group("hash").lower()

        draw_match = _DRAW_CALL_RE.search(body)
        if draw_match:
            result[event] = DrawState(
                event=event,
                blend_state=current_blend,
                depth_state=current_depth,
                vs_hash=current_vs,
                ps_hash=current_ps,
                index_count=int(draw_match.group("count")),
                start_index=int(draw_match.group("start")),
                base_vertex=int(draw_match.group("base")),
                raster_state=current_raster,
            )
    return result


def _normalize_hex(value: str) -> str:
    return f"0x{int(value, 16):016X}"


def _infer_default_blend_state(mapped_draws: Iterable[tuple[int, DrawFileRecord, DrawState]]) -> Optional[str]:
    totals: dict[str, tuple[int, int, int]] = {}
    first_seen = 0
    for _component_id, _draw_file, draw_state in mapped_draws:
        if not draw_state.blend_state:
            continue
        if draw_state.blend_state not in totals:
            first_seen += 1
            totals[draw_state.blend_state] = (0, 0, first_seen)
        calls, indices, order = totals[draw_state.blend_state]
        totals[draw_state.blend_state] = (calls + 1, indices + draw_state.index_count, order)
    if not totals:
        return None
    return max(totals.items(), key=lambda item: (item[1][0], item[1][1], -item[1][2]))[0]


def _load_texture_usage(character_folder: Path) -> dict[tuple[int, int, str, str], list[TextureUsageRecord]]:
    usage_path = character_folder / "TextureUsage.json"
    if not usage_path.is_file():
        return {}
    payload = _read_json(usage_path)
    result: dict[tuple[int, int, str, str], list[TextureUsageRecord]] = {}
    for component_name, slots in payload.items():
        component_match = re.search(r"Component\s+(\d+)", str(component_name), re.IGNORECASE)
        if not component_match or not isinstance(slots, dict):
            continue
        component_id = int(component_match.group(1))
        for slot, entries in slots.items():
            for entry in entries or []:
                match = _TEXTURE_USAGE_RE.match(str(entry))
                if not match:
                    continue
                record = TextureUsageRecord(
                    component_id=component_id,
                    texture_hash=match.group("texture").lower(),
                    vs_hash=match.group("vs").lower(),
                    ps_hash=match.group("ps").lower(),
                    event=int(match.group("event")),
                    slot=str(slot),
                )
                key = (component_id, record.event, record.vs_hash, record.ps_hash)
                result.setdefault(key, []).append(record)
    return result


def _load_texture_assets(character_folder: Path) -> dict[str, list[TextureAsset]]:
    result: dict[str, list[TextureAsset]] = {}
    for path in character_folder.glob("Components-* t=*.dds"):
        match = _TEXTURE_ASSET_RE.match(path.name)
        if not match:
            continue
        components = frozenset(int(part) for part in match.group("components").split("-") if part)
        texture_hash = match.group("texture").lower()
        result.setdefault(texture_hash, []).append(
            TextureAsset(
                texture_hash=texture_hash,
                components=components,
                path=path,
                color_space_label=match.group("label"),
            )
        )
    return result


def _load_component_geometries(character_folder: Path) -> dict[int, ComponentGeometry]:
    found: dict[int, dict[str, Path]] = {}
    for path in character_folder.iterdir():
        match = _COMPONENT_RE.match(path.name)
        if not match:
            continue
        component_id = int(match.group("id"))
        kind = match.group("kind").lower()
        found.setdefault(component_id, {})[kind] = path

    result = {}
    for component_id, paths in found.items():
        if not {"fmt", "vb", "ib"}.issubset(paths):
            continue
        parsed = _parse_fmt(paths["fmt"])
        stride = int(parsed.get("stride") or 0)
        if stride <= 0:
            continue
        texcoord = _find_texcoord(parsed)
        vb_size = paths["vb"].stat().st_size
        index_width = 4 if parsed.get("format") == "DXGI_FORMAT_R32_UINT" else 2
        result[component_id] = ComponentGeometry(
            component_id=component_id,
            fmt_path=paths["fmt"],
            vb_path=paths["vb"],
            ib_path=paths["ib"],
            stride=stride,
            index_format=str(parsed.get("format") or ""),
            texcoord_offset=texcoord[0] if texcoord else None,
            texcoord_format=texcoord[1] if texcoord else None,
            vertex_count=vb_size // stride,
            index_count=paths["ib"].stat().st_size // index_width,
        )
    return result


def _parse_fmt(fmt_path: Path) -> dict:
    data: dict = {"elements": []}
    current = None
    for raw_line in fmt_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("element["):
            current = {}
            data["elements"].append(current)
            continue
        if ":" not in line:
            continue
        key, value = [part.strip() for part in line.split(":", 1)]
        if current is None:
            data[key] = value
        else:
            current[key] = value
    return data


def _find_texcoord(parsed_fmt: dict) -> Optional[tuple[int, str]]:
    for element in parsed_fmt.get("elements") or []:
        if (
            str(element.get("SemanticName", "")).upper() == "TEXCOORD"
            and int(element.get("SemanticIndex") or 0) == 0
        ):
            return int(element.get("AlignedByteOffset") or 0), str(element.get("Format") or "")
    return None


def _classify_draw(
    component_id: int,
    draw_file: DrawFileRecord,
    draw_state: DrawState,
    usage: dict[tuple[int, int, str, str], list[TextureUsageRecord]],
    assets: dict[str, list[TextureAsset]],
    geometry: Optional[ComponentGeometry],
) -> Optional[TransparencyEvidence]:
    if geometry is None or geometry.texcoord_offset is None:
        return None
    key = (component_id, draw_file.event, draw_file.vs_hash, draw_file.ps_hash)
    usage_records = usage.get(key, [])
    if not usage_records:
        return None

    candidates: list[tuple[tuple[int, int, int, float], TransparencyEvidence]] = []
    for usage_record in usage_records:
        for asset in assets.get(usage_record.texture_hash, []):
            if component_id not in asset.components:
                continue
            alpha = _load_alpha(asset.path)
            if alpha is None:
                continue
            alpha_min, alpha_max, alpha_pixels = alpha
            if alpha_min >= 255:
                continue
            ratio = _sample_uv_alpha_hit_ratio(geometry, draw_state, alpha_pixels)
            if ratio <= 0.0:
                continue
            evidence = TransparencyEvidence(
                component_id=component_id,
                draw_event=draw_state.event,
                blend_state=draw_state.blend_state or "",
                texture_hash=asset.texture_hash,
                texture_path=str(asset.path),
                alpha_min=alpha_min,
                alpha_max=alpha_max,
                uv_alpha_hit_ratio=ratio,
            )
            # Component-local color textures are better CrossIB target evidence
            # than broad shared masks. Never use the DDS format label itself as
            # proof of transparency; the label only breaks ties after alpha+UV
            # have already been verified.
            score = (
                -len(asset.components),
                1 if "srgb" in asset.color_space_label.lower() else 0,
                alpha_max - alpha_min,
                ratio,
            )
            candidates.append((score, evidence))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _load_alpha(path: Path) -> Optional[tuple[int, int, object]]:
    try:
        from PIL import Image

        image = Image.open(path).convert("RGBA")
        alpha = image.getchannel("A")
        alpha_min, alpha_max = alpha.getextrema()
        return int(alpha_min), int(alpha_max), alpha
    except Exception:
        return _load_alpha_with_blender(path)


def _load_alpha_with_blender(path: Path) -> Optional[tuple[int, int, _AlphaPixels]]:
    try:
        import bpy
    except Exception:
        return None

    image = None
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
        width, height = [int(value) for value in image.size[:2]]
        if width <= 0 or height <= 0:
            return None

        rgba = array("f", [0.0]) * (width * height * 4)
        image.pixels.foreach_get(rgba)
        alpha = array("B", [255]) * (width * height)
        alpha_min = 255
        alpha_max = 0
        out_index = 0
        for rgba_index in range(3, len(rgba), 4):
            value = int(round(max(0.0, min(1.0, rgba[rgba_index])) * 255.0))
            alpha[out_index] = value
            if value < alpha_min:
                alpha_min = value
            if value > alpha_max:
                alpha_max = value
            out_index += 1
        return alpha_min, alpha_max, _AlphaPixels(width=width, height=height, data=alpha)
    except Exception:
        return None
    finally:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except Exception:
                pass


def _sample_uv_alpha_hit_ratio(geometry: ComponentGeometry, draw_state: DrawState, alpha_image) -> float:
    indices = _read_indices(geometry)
    if not indices:
        return 0.0
    start = draw_state.start_index
    count = draw_state.index_count
    if start + count > len(indices):
        if count <= len(indices):
            start = 0
        else:
            count = len(indices)
            start = 0
    draw_indices = indices[start:start + count]
    if not draw_indices:
        return 0.0

    vb_data = geometry.vb_path.read_bytes()
    width, height = alpha_image.size
    total = 0
    hits_normal = 0
    hits_flipped = 0
    seen = set()
    for raw_index in draw_indices:
        vertex_index = raw_index + draw_state.base_vertex
        if vertex_index in seen:
            continue
        seen.add(vertex_index)
        if vertex_index < 0 or vertex_index >= geometry.vertex_count:
            continue
        uv = _read_uv(vb_data, geometry, vertex_index)
        if uv is None:
            continue
        u, v = uv
        total += 1
        if _alpha_at_uv(alpha_image, width, height, u, v) < 255:
            hits_normal += 1
        if _alpha_at_uv(alpha_image, width, height, u, 1.0 - v) < 255:
            hits_flipped += 1
    if total == 0:
        return 0.0
    return max(hits_normal, hits_flipped) / total


def _read_indices(geometry: ComponentGeometry) -> list[int]:
    data = geometry.ib_path.read_bytes()
    if geometry.index_format == "DXGI_FORMAT_R32_UINT":
        if len(data) < 4:
            return []
        return list(struct.unpack("<" + "I" * (len(data) // 4), data[: len(data) // 4 * 4]))
    if len(data) < 2:
        return []
    return list(struct.unpack("<" + "H" * (len(data) // 2), data[: len(data) // 2 * 2]))


def _read_uv(data: bytes, geometry: ComponentGeometry, vertex_index: int) -> Optional[tuple[float, float]]:
    offset = vertex_index * geometry.stride + int(geometry.texcoord_offset or 0)
    fmt = geometry.texcoord_format
    try:
        if fmt == "R32G32_FLOAT":
            return struct.unpack_from("<ff", data, offset)
        if fmt == "R16G16_FLOAT":
            return struct.unpack_from("<ee", data, offset)
        if fmt == "R16G16_UNORM":
            u, v = struct.unpack_from("<HH", data, offset)
            return u / 65535.0, v / 65535.0
    except struct.error:
        return None
    return None


def _alpha_at_uv(alpha_image, width: int, height: int, u: float, v: float) -> int:
    if width <= 0 or height <= 0:
        return 255
    u = u % 1.0
    v = v % 1.0
    x = min(width - 1, max(0, int(u * width)))
    y = min(height - 1, max(0, int(v * height)))
    return int(alpha_image.getpixel((x, y)))
