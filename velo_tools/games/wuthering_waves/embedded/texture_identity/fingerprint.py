"""Canonical pixel fingerprint primitives for DDS resources."""

from __future__ import annotations

import base64
import itertools
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..slot_textures.dds_meta import DdsMeta, read_dds_meta


ALGORITHM_NAME = "canonical-pixel-fingerprint"
ALGORITHM_VERSION = 3
RESOLUTIONS = (16, 32, 64, 128, 256)
DEFAULT_TOLERANCE = 2.0 / 255.0
DEFAULT_MINIMUM_MARGIN = 4.0 / 255.0


class FingerprintError(ValueError):
    pass


@dataclass(frozen=True)
class DecodedDds:
    meta: DdsMeta
    rgba: bytes


@dataclass(frozen=True)
class CollisionSelection:
    resolution: int
    fingerprints: Mapping[str, str]
    tolerance: float
    minimum_margin: float
    maximum_intra_distance: float
    nearest_inter_distance: float | None
    pixel_ambiguous: bool


def format_family(format_name: str) -> str:
    name = str(format_name or "").upper()
    for suffix in ("_TYPELESS", "_UNORM_SRGB", "_UNORM", "_SNORM", "_UINT", "_SINT", "_FLOAT"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def decode_dds(path: str | Path) -> DecodedDds:
    path = Path(path)
    meta = read_dds_meta(path)
    if meta is None:
        raise FingerprintError(f"Not a readable DDS file: {path}")
    supported = {
        "B8G8R8A8_UNORM",
        "B8G8R8A8_UNORM_SRGB",
        "B8G8R8X8_UNORM",
        "B8G8R8X8_UNORM_SRGB",
        "R8G8B8A8_UNORM",
        "R8G8B8A8_UNORM_SRGB",
    }
    if meta.format not in supported:
        raise FingerprintError(f"Unsupported prototype DDS format: {meta.format or 'unknown'}")

    data = path.read_bytes()
    if len(data) < 128:
        raise FingerprintError(f"Truncated DDS header: {path}")
    has_dx10 = data[84:88] == b"DX10"
    offset = 148 if has_dx10 else 128
    pitch = struct.unpack_from("<I", data, 20)[0]
    row_bytes = meta.width * 4
    stride = pitch if pitch >= row_bytes else row_bytes
    required = offset + stride * meta.height
    if len(data) < required:
        raise FingerprintError(f"Truncated DDS pixel payload: {path}")

    source = bytearray(meta.width * meta.height * 4)
    source_offset = 0
    for y in range(meta.height):
        row = data[offset + y * stride : offset + y * stride + row_bytes]
        source[source_offset : source_offset + row_bytes] = row
        source_offset += row_bytes

    output = bytearray(len(source))
    bgra = meta.format.startswith("B8G8R8")
    force_alpha = meta.format.startswith("B8G8R8X8")
    if bgra:
        output[0::4] = source[2::4]
        output[1::4] = source[1::4]
        output[2::4] = source[0::4]
    else:
        output[0::4] = source[0::4]
        output[1::4] = source[1::4]
        output[2::4] = source[2::4]
    output[3::4] = b"\xff" * (meta.width * meta.height) if force_alpha else source[3::4]
    return DecodedDds(meta=meta, rgba=bytes(output))


def sample_grid(decoded: DecodedDds, resolution: int) -> bytes:
    if resolution not in RESOLUTIONS:
        raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
    width = decoded.meta.width
    height = decoded.meta.height
    if width <= 0 or height <= 0:
        raise FingerprintError("DDS dimensions must be positive")
    sampled = bytearray(resolution * resolution * 4)
    output_offset = 0
    for y in range(resolution):
        source_y = min(height - 1, ((2 * y + 1) * height) // (2 * resolution))
        for x in range(resolution):
            source_x = min(width - 1, ((2 * x + 1) * width) // (2 * resolution))
            source_offset = (source_y * width + source_x) * 4
            sampled[output_offset : output_offset + 4] = decoded.rgba[source_offset : source_offset + 4]
            output_offset += 4
    return bytes(sampled)


def encode_fingerprint(sampled_rgba: bytes, resolution: int) -> str:
    expected = resolution * resolution * 4
    if len(sampled_rgba) != expected:
        raise FingerprintError(
            f"Fingerprint sample length {len(sampled_rgba)} does not match r{resolution} RGBA8 ({expected})"
        )
    payload = base64.urlsafe_b64encode(zlib.compress(sampled_rgba, level=9)).decode("ascii")
    return f"v{ALGORITHM_VERSION}:r{resolution}:rgba8-zlib:{payload}"


def decode_fingerprint(payload: str) -> tuple[int, bytes]:
    parts = str(payload).split(":", 3)
    if len(parts) != 4 or parts[0] != f"v{ALGORITHM_VERSION}" or parts[2] != "rgba8-zlib":
        raise FingerprintError("Unsupported fingerprint payload")
    if not parts[1].startswith("r"):
        raise FingerprintError("Fingerprint resolution tag is missing")
    resolution = int(parts[1][1:])
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(parts[3].encode("ascii")))
    except Exception as exc:
        raise FingerprintError("Invalid fingerprint payload") from exc
    expected = resolution * resolution * 4
    if len(raw) != expected:
        raise FingerprintError(
            f"Fingerprint payload length {len(raw)} does not match r{resolution} RGBA8 ({expected})"
        )
    return resolution, raw


def fingerprint_dds(path: str | Path, resolutions: Iterable[int] = RESOLUTIONS) -> dict[str, str]:
    decoded = decode_dds(path)
    return {
        str(resolution): encode_fingerprint(sample_grid(decoded, resolution), resolution)
        for resolution in resolutions
    }


def fingerprint_distance(left: str, right: str) -> float:
    left_resolution, left_raw = decode_fingerprint(left)
    right_resolution, right_raw = decode_fingerprint(right)
    if left_resolution != right_resolution:
        raise FingerprintError("Fingerprint resolutions differ")
    return sum(abs(a - b) for a, b in zip(left_raw, right_raw)) / (255.0 * len(left_raw))


def _variant_payloads(identity: Mapping, resolution: int) -> list[str]:
    variants = identity.get("variants") or []
    payloads = []
    for variant in variants:
        fingerprints = variant.get("fingerprints") or {}
        payload = fingerprints.get(str(resolution))
        if payload:
            payload_resolution, _ = decode_fingerprint(payload)
            if payload_resolution != resolution:
                raise FingerprintError(
                    f"Manifest key r{resolution} disagrees with payload r{payload_resolution}"
                )
            payloads.append(payload)
    return payloads


def select_common_resolution(
    identities: Sequence[Mapping],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    minimum_margin: float = DEFAULT_MINIMUM_MARGIN,
) -> CollisionSelection:
    if not identities:
        raise FingerprintError("A collision group must contain at least one identity")

    last_payloads: dict[str, str] = {}
    last_resolution = None
    last_intra = 0.0
    last_inter = None
    for resolution in RESOLUTIONS:
        per_identity = []
        representative = {}
        complete = True
        for identity in identities:
            identity_id = str(identity.get("identity") or "")
            payloads = _variant_payloads(identity, resolution)
            if not identity_id or not payloads:
                complete = False
                break
            per_identity.append((identity_id, payloads))
            representative[identity_id] = payloads[0]
        if not complete:
            continue

        intra_distances = [
            fingerprint_distance(left, right)
            for _, payloads in per_identity
            for left, right in itertools.combinations(payloads, 2)
        ]
        inter_distances = [
            fingerprint_distance(left, right)
            for (_, left_payloads), (_, right_payloads) in itertools.combinations(per_identity, 2)
            for left in left_payloads
            for right in right_payloads
        ]
        maximum_intra = max(intra_distances, default=0.0)
        nearest_inter = min(inter_distances) if inter_distances else None
        last_payloads = representative
        last_resolution = resolution
        last_intra = maximum_intra
        last_inter = nearest_inter
        if nearest_inter is None or nearest_inter - (maximum_intra + tolerance) >= minimum_margin:
            return CollisionSelection(
                resolution=resolution,
                fingerprints=representative,
                tolerance=tolerance,
                minimum_margin=minimum_margin,
                maximum_intra_distance=maximum_intra,
                nearest_inter_distance=nearest_inter,
                pixel_ambiguous=False,
            )

    if not last_payloads or last_resolution != RESOLUTIONS[-1]:
        raise FingerprintError("A complete r256 fingerprint set is required to classify pixel ambiguity")
    return CollisionSelection(
        resolution=last_resolution,
        fingerprints=last_payloads,
        tolerance=tolerance,
        minimum_margin=minimum_margin,
        maximum_intra_distance=last_intra,
        nearest_inter_distance=last_inter,
        pixel_ambiguous=True,
    )
