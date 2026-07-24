"""Canonical pixel fingerprint primitives for DDS resources."""

from __future__ import annotations

import base64
import itertools
import math
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..slot_textures.dds_meta import DdsMeta, read_dds_meta


ALGORITHM_NAME = "canonical-pixel-fingerprint"
ALGORITHM_VERSION = 4
CANONICAL_COLOR_DOMAIN = "srv-load-linear-rgba8-v1"
MIP_SELECTION_POLICY = "closest-major-axis-real-mip-v1"
RESAMPLE_POLICY = "independent-axis-nearest-center-to-square-v1"
RESOLUTIONS = (16, 32, 64, 128, 256)
DEFAULT_TOLERANCE_FLOOR = 2.0 / 255.0
DEFAULT_MINIMUM_MARGIN = 4.0 / 255.0


class FingerprintError(ValueError):
    pass


@dataclass(frozen=True)
class DecodedMip:
    level: int
    width: int
    height: int
    rgba: bytes


@dataclass(frozen=True)
class DecodedDds:
    meta: DdsMeta
    mips: tuple[DecodedMip, ...]
    declared_mips: int
    mip_chain_complete: bool
    source_is_srgb: bool
    canonical_color_domain: str = CANONICAL_COLOR_DOMAIN


@dataclass(frozen=True)
class CollisionSelection:
    resolution: int
    fingerprints: Mapping[str, str]
    reference_variants: Mapping[str, str]
    tolerance: float
    tolerance_floor: float
    minimum_margin: float
    maximum_intra_distance: float
    nearest_inter_distance: float | None
    pixel_ambiguous: bool


def format_family(format_name: str) -> str:
    """Return a channel/domain family suitable for runtime candidate filtering."""
    name = str(format_name or "").upper()
    if name.startswith(("R8G8B8A8_", "B8G8R8A8_", "B8G8R8X8_", "BC1_", "BC2_", "BC3_", "BC7_")):
        return "color-rgba8"
    if name.startswith(("R8_", "BC4_")):
        return "scalar-r8"
    if name.startswith(("R8G8_", "BC5_")):
        return "vector-rg8"
    if name.startswith("BC6H_"):
        return "color-rgb-float"
    for suffix in ("_TYPELESS", "_UNORM_SRGB", "_UNORM", "_SNORM", "_UINT", "_SINT", "_FLOAT"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return f"dxgi-family:{name.lower() or 'unknown'}"


def _linearize_srgb_byte(value: int) -> int:
    encoded = value / 255.0
    if encoded <= 0.04045:
        linear = encoded / 12.92
    else:
        linear = ((encoded + 0.055) / 1.055) ** 2.4
    return min(255, max(0, int(math.floor(linear * 255.0 + 0.5))))


_SRGB_TO_LINEAR = bytes(_linearize_srgb_byte(value) for value in range(256))


def _canonical_rgba(source: bytes, *, bgra: bool, force_alpha: bool, source_is_srgb: bool) -> bytes:
    output = bytearray(len(source))
    if bgra:
        output[0::4] = source[2::4]
        output[1::4] = source[1::4]
        output[2::4] = source[0::4]
    else:
        output[0::4] = source[0::4]
        output[1::4] = source[1::4]
        output[2::4] = source[2::4]
    output[3::4] = b"\xff" * (len(source) // 4) if force_alpha else source[3::4]
    if source_is_srgb:
        table = _SRGB_TO_LINEAR
        output[0::4] = bytes(table[value] for value in output[0::4])
        output[1::4] = bytes(table[value] for value in output[1::4])
        output[2::4] = bytes(table[value] for value in output[2::4])
    return bytes(output)


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
    base_pitch = struct.unpack_from("<I", data, 20)[0]
    declared_mips = max(int(meta.mips), 1)
    bgra = meta.format.startswith("B8G8R8")
    force_alpha = meta.format.startswith("B8G8R8X8")
    source_is_srgb = meta.format.endswith("_SRGB")
    decoded_mips = []

    width = meta.width
    height = meta.height
    for level in range(declared_mips):
        row_bytes = width * 4
        stride = max(row_bytes, base_pitch) if level == 0 else row_bytes
        required = offset + stride * height
        if required > len(data):
            break
        source = bytearray(row_bytes * height)
        target_offset = 0
        for row_id in range(height):
            row = data[
                offset + row_id * stride :
                offset + row_id * stride + row_bytes
            ]
            source[target_offset : target_offset + row_bytes] = row
            target_offset += row_bytes
        decoded_mips.append(
            DecodedMip(
                level=level,
                width=width,
                height=height,
                rgba=_canonical_rgba(
                    bytes(source),
                    bgra=bgra,
                    force_alpha=force_alpha,
                    source_is_srgb=source_is_srgb,
                ),
            )
        )
        offset = required
        width = max(1, width // 2)
        height = max(1, height // 2)

    if not decoded_mips:
        raise FingerprintError(f"DDS has no complete mip payload: {path}")
    return DecodedDds(
        meta=meta,
        mips=tuple(decoded_mips),
        declared_mips=declared_mips,
        mip_chain_complete=len(decoded_mips) == declared_mips,
        source_is_srgb=source_is_srgb,
    )


def select_real_mip(decoded: DecodedDds, resolution: int) -> tuple[DecodedMip, str]:
    if resolution not in RESOLUTIONS:
        raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
    if not decoded.mips:
        raise FingerprintError("DDS has no decoded mip")

    def rank(mip: DecodedMip) -> tuple[float, int, int]:
        major_axis = max(mip.width, mip.height)
        ratio_distance = abs(math.log2(major_axis / resolution))
        return ratio_distance, 0 if major_axis >= resolution else 1, mip.level

    selected = min(decoded.mips, key=rank)
    major_axis = max(selected.width, selected.height)
    if major_axis == resolution:
        reason = "exact-major-axis"
    elif max(decoded.mips[0].width, decoded.mips[0].height) < resolution:
        reason = "base-smaller-than-target-upsample"
    elif selected.level == decoded.mips[-1].level and major_axis > resolution:
        reason = "target-mip-unavailable-use-smallest-complete"
    else:
        reason = "closest-available-real-mip"
    if not decoded.mip_chain_complete:
        reason += "-incomplete-chain"
    return selected, reason


def sample_grid(mip: DecodedMip, resolution: int) -> bytes:
    if resolution not in RESOLUTIONS:
        raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
    if mip.width <= 0 or mip.height <= 0:
        raise FingerprintError("DDS mip dimensions must be positive")
    sampled = bytearray(resolution * resolution * 4)
    output_offset = 0
    for y in range(resolution):
        source_y = min(mip.height - 1, ((2 * y + 1) * mip.height) // (2 * resolution))
        for x in range(resolution):
            source_x = min(mip.width - 1, ((2 * x + 1) * mip.width) // (2 * resolution))
            source_offset = (source_y * mip.width + source_x) * 4
            sampled[output_offset : output_offset + 4] = mip.rgba[source_offset : source_offset + 4]
            output_offset += 4
    return bytes(sampled)


def encode_fingerprint(sampled_rgba: bytes, resolution: int) -> str:
    expected = resolution * resolution * 4
    if len(sampled_rgba) != expected:
        raise FingerprintError(
            f"Fingerprint sample length {len(sampled_rgba)} does not match r{resolution} RGBA8 ({expected})"
        )
    payload = base64.urlsafe_b64encode(zlib.compress(sampled_rgba, level=9)).decode("ascii")
    return f"v{ALGORITHM_VERSION}:r{resolution}:linear-rgba8-zlib:{payload}"


def decode_fingerprint(payload: str) -> tuple[int, bytes]:
    parts = str(payload).split(":", 3)
    if (
        len(parts) != 4
        or parts[0] != f"v{ALGORITHM_VERSION}"
        or parts[2] != "linear-rgba8-zlib"
    ):
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


def fingerprint_dds(
    path: str | Path,
    resolutions: Iterable[int] = RESOLUTIONS,
) -> dict[str, dict]:
    decoded = decode_dds(path)
    results = {}
    for resolution in resolutions:
        mip, selection_reason = select_real_mip(decoded, resolution)
        results[str(resolution)] = {
            "payload": encode_fingerprint(sample_grid(mip, resolution), resolution),
            "algorithm_version": ALGORITHM_VERSION,
            "canonical_color_domain": CANONICAL_COLOR_DOMAIN,
            "canonical_resolution": resolution,
            "source_mip_level": mip.level,
            "source_mip_width": mip.width,
            "source_mip_height": mip.height,
            "declared_mips": decoded.declared_mips,
            "available_complete_mips": len(decoded.mips),
            "mip_chain_complete": decoded.mip_chain_complete,
            "mip_selection_policy": MIP_SELECTION_POLICY,
            "mip_selection_reason": selection_reason,
            "resample_policy": RESAMPLE_POLICY,
            "source_is_srgb": decoded.source_is_srgb,
        }
    return results


def fingerprint_distance(left: str, right: str) -> float:
    left_resolution, left_raw = decode_fingerprint(left)
    right_resolution, right_raw = decode_fingerprint(right)
    if left_resolution != right_resolution:
        raise FingerprintError("Fingerprint resolutions differ")
    return sum(abs(a - b) for a, b in zip(left_raw, right_raw)) / (255.0 * len(left_raw))


def _variant_payloads(identity: Mapping, resolution: int) -> list[tuple[str, str]]:
    payloads = []
    for variant in identity.get("variants") or []:
        variant_id = str(variant.get("variant") or "")
        record = (variant.get("fingerprints") or {}).get(str(resolution))
        if not variant_id or not isinstance(record, Mapping):
            continue
        if int(record.get("algorithm_version", -1)) != ALGORITHM_VERSION:
            raise FingerprintError("Fingerprint algorithm version mismatch")
        if record.get("canonical_color_domain") != CANONICAL_COLOR_DOMAIN:
            raise FingerprintError("Fingerprint canonical color domain mismatch")
        payload = str(record.get("payload") or "")
        payload_resolution, _ = decode_fingerprint(payload)
        if payload_resolution != resolution or int(record.get("canonical_resolution", -1)) != resolution:
            raise FingerprintError(
                f"Manifest key r{resolution} disagrees with its fingerprint record"
            )
        payloads.append((variant_id, payload))
    return payloads


def _reference_variant(payloads: list[tuple[str, str]]) -> tuple[str, str]:
    ranked = []
    for variant_id, payload in payloads:
        radius = max(
            (fingerprint_distance(payload, other) for _, other in payloads),
            default=0.0,
        )
        ranked.append((radius, variant_id, payload))
    _, variant_id, payload = min(ranked)
    return variant_id, payload


def select_common_resolution(
    identities: Sequence[Mapping],
    *,
    tolerance_floor: float = DEFAULT_TOLERANCE_FLOOR,
    minimum_margin: float = DEFAULT_MINIMUM_MARGIN,
) -> CollisionSelection:
    if not identities:
        raise FingerprintError("A collision group must contain at least one identity")

    last = None
    for resolution in RESOLUTIONS:
        per_identity = []
        references = {}
        reference_variants = {}
        complete = True
        for identity in identities:
            identity_id = str(identity.get("identity") or "")
            payloads = _variant_payloads(identity, resolution)
            expected_variants = len(identity.get("variants") or [])
            if not identity_id or not payloads or len(payloads) != expected_variants:
                complete = False
                break
            per_identity.append((identity_id, payloads))
            variant_id, reference = _reference_variant(payloads)
            references[identity_id] = reference
            reference_variants[identity_id] = variant_id
        if not complete:
            continue

        intra_distances = [
            fingerprint_distance(left, right)
            for _, payloads in per_identity
            for (_, left), (_, right) in itertools.combinations(payloads, 2)
        ]
        inter_distances = [
            fingerprint_distance(left, right)
            for (_, left_payloads), (_, right_payloads) in itertools.combinations(per_identity, 2)
            for _, left in left_payloads
            for _, right in right_payloads
        ]
        maximum_intra = max(intra_distances, default=0.0)
        effective_tolerance = max(float(tolerance_floor), maximum_intra)
        nearest_inter = min(inter_distances) if inter_distances else None
        last = (
            resolution,
            references,
            reference_variants,
            effective_tolerance,
            maximum_intra,
            nearest_inter,
        )
        if nearest_inter is None or nearest_inter - effective_tolerance >= minimum_margin:
            return CollisionSelection(
                resolution=resolution,
                fingerprints=references,
                reference_variants=reference_variants,
                tolerance=effective_tolerance,
                tolerance_floor=tolerance_floor,
                minimum_margin=minimum_margin,
                maximum_intra_distance=maximum_intra,
                nearest_inter_distance=nearest_inter,
                pixel_ambiguous=False,
            )

    if last is None or last[0] != RESOLUTIONS[-1]:
        raise FingerprintError("A complete r256 fingerprint set is required to classify pixel ambiguity")
    resolution, references, reference_variants, effective_tolerance, maximum_intra, nearest_inter = last
    return CollisionSelection(
        resolution=resolution,
        fingerprints=references,
        reference_variants=reference_variants,
        tolerance=effective_tolerance,
        tolerance_floor=tolerance_floor,
        minimum_margin=minimum_margin,
        maximum_intra_distance=maximum_intra,
        nearest_inter_distance=nearest_inter,
        pixel_ambiguous=True,
    )
