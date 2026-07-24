"""Runtime-v3 canonical pixel fingerprint primitives for DDS resources."""

from __future__ import annotations

import base64
import hashlib
import itertools
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..slot_textures.dds_meta import DdsMeta, read_dds_meta


ALGORITHM_NAME = "canonical-pixel-fingerprint"
ALGORITHM_VERSION = 3
PAYLOAD_ENCODING = "v3:rN:rgba8-phash:<base64url>"
PAYLOAD_PROFILE = "rgba8-phash"
CANONICAL_COLOR_DOMAIN = "encoded-rgba8"
CANONICAL_VIEW_POLICY = "compatible-non-srgb-srv"
MIP_SELECTION_POLICY = "visible-most-detailed-normalized-cell-center-v3"
RESOLUTIONS = (16, 32, 64, 128, 256)
DEFAULT_TOLERANCE_FLOOR = 2.0 / 255.0
DEFAULT_MINIMUM_MARGIN = 4.0 / 255.0

_DESCRIPTOR_FAMILIES = {
    "R8G8B8A8_UNORM": "r8g8b8a8",
    "R8G8B8A8_UNORM_SRGB": "r8g8b8a8",
    "B8G8R8A8_UNORM": "b8g8r8a8",
    "B8G8R8A8_UNORM_SRGB": "b8g8r8a8",
    "B8G8R8X8_UNORM": "b8g8r8x8",
    "B8G8R8X8_UNORM_SRGB": "b8g8r8x8",
    "BC1_UNORM": "bc1",
    "BC1_UNORM_SRGB": "bc1",
    "BC2_UNORM": "bc2",
    "BC2_UNORM_SRGB": "bc2",
    "BC3_UNORM": "bc3",
    "BC3_UNORM_SRGB": "bc3",
    "BC4_UNORM": "bc4",
    "BC4_SNORM": "bc4",
    "BC5_UNORM": "bc5",
    "BC5_SNORM": "bc5",
    "BC6H_UF16": "bc6h",
    "BC6H_SF16": "bc6h",
    "BC7_UNORM": "bc7",
    "BC7_UNORM_SRGB": "bc7",
}

# Runtime uses dxgi-<numeric enum> for formats outside its named families.
_DXGI_FORMAT_VALUES = {
    "R8G8_UNORM": 49,
    "R8G8_SNORM": 51,
    "R8_UNORM": 61,
    "R8_SNORM": 63,
    "A8_UNORM": 65,
}


def _float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _quantize_unit(value: float) -> int:
    clamped = max(0.0, min(1.0, value))
    scaled = _float32(_float32(clamped) * _float32(255.0))
    return int(math.floor(scaled + 0.5))


_BYTE_TO_FLOAT = tuple(_float32(value / 255.0) for value in range(256))


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
    canonical_format: str
    source_sha256: str
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
    unavailable_resolutions: tuple[int, ...]


def format_family(format_name: str) -> str:
    """Return the exact descriptor family emitted by runtime v3."""
    name = str(format_name or "").upper()
    family = _DESCRIPTOR_FAMILIES.get(name)
    if family:
        return family
    numeric = _DXGI_FORMAT_VALUES.get(name)
    return f"dxgi-{numeric}" if numeric is not None else ""


def canonical_format(format_name: str) -> str:
    """Return the non-SRGB format used by the runtime analysis SRV."""
    name = str(format_name or "").upper()
    if name.endswith("_UNORM_SRGB"):
        return name[: -len("_SRGB")]
    return name


def _canonical_rgba(source: bytes, *, bgra: bool, force_alpha: bool) -> bytes:
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
        source_is_srgb=meta.format.endswith("_SRGB"),
        canonical_format=canonical_format(meta.format),
        source_sha256=hashlib.sha256(data).hexdigest(),
    )


def sample_grid(mip: DecodedMip, resolution: int) -> bytes:
    """Match the runtime v3 integer cell-center Load coordinates."""
    if resolution not in RESOLUTIONS:
        raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
    if mip.width <= 0 or mip.height <= 0:
        raise FingerprintError("DDS mip dimensions must be positive")
    sampled = bytearray(resolution * resolution * 4)
    output_offset = 0
    for y in range(resolution):
        source_y = min(
            mip.height - 1,
            ((2 * y + 1) * mip.height) // (2 * resolution),
        )
        for x in range(resolution):
            source_x = min(
                mip.width - 1,
                ((2 * x + 1) * mip.width) // (2 * resolution),
            )
            source_offset = (source_y * mip.width + source_x) * 4
            sampled[output_offset : output_offset + 4] = mip.rgba[
                source_offset : source_offset + 4
            ]
            output_offset += 4
    return bytes(sampled)


def encode_fingerprint(rgba8: bytes, resolution: int) -> str:
    if resolution not in RESOLUTIONS:
        raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
    expected = resolution * resolution * 4
    if len(rgba8) != expected:
        raise FingerprintError(
            f"Fingerprint payload length {len(rgba8)} does not match r{resolution} RGBA8 ({expected})"
        )
    sample_count = resolution * resolution
    sums = [0.0] * 4
    squared_sums = [0.0] * 4
    covered_alpha = 0
    for offset in range(0, len(rgba8), 4):
        for component in range(4):
            value = _BYTE_TO_FLOAT[rgba8[offset + component]]
            sums[component] += value
            squared_sums[component] += _float32(value * value)
        if _BYTE_TO_FLOAT[rgba8[offset + 3]] >= 0.5:
            covered_alpha += 1

    means = []
    deviations = []
    for component in range(4):
        mean = sums[component] / sample_count
        variance = max(0.0, squared_sums[component] / sample_count - mean * mean)
        means.append(_quantize_unit(_float32(mean)))
        deviations.append(_quantize_unit(_float32(math.sqrt(variance))))
    alpha_coverage = _quantize_unit(
        _float32(_float32(float(covered_alpha)) / _float32(float(sample_count)))
    )

    channel_hashes = []
    for component in range(4):
        channel_hash = 0
        bit = 1
        for y in range(8):
            sample_y = min(resolution - 1, y * 2 * resolution // 16)
            for x in range(8):
                left_x = min(resolution - 1, x * 2 * resolution // 16)
                right_x = min(
                    resolution - 1,
                    (x * 2 + 1) * resolution // 16,
                )
                left = (sample_y * resolution + left_x) * 4 + component
                right = (sample_y * resolution + right_x) * 4 + component
                if rgba8[left] < rgba8[right]:
                    channel_hash |= bit
                bit <<= 1
        channel_hashes.append(channel_hash)

    compact = (
        struct.pack(">4Q", *channel_hashes)
        + bytes(means)
        + bytes(deviations)
        + bytes((alpha_coverage,))
    )
    payload = base64.urlsafe_b64encode(compact).decode("ascii")
    return f"v3:r{resolution}:rgba8-phash:{payload}"


def decode_fingerprint(payload: str) -> tuple[int, bytes]:
    parts = str(payload).split(":", 3)
    if len(parts) != 4 or parts[0] != "v3" or parts[2] != PAYLOAD_PROFILE:
        raise FingerprintError("Unsupported runtime-v3 fingerprint payload")
    if not parts[1].startswith("r"):
        raise FingerprintError("Fingerprint resolution tag is missing")
    try:
        resolution = int(parts[1][1:])
    except ValueError as exc:
        raise FingerprintError("Fingerprint resolution tag is invalid") from exc
    if resolution not in RESOLUTIONS:
        raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
    if len(parts[3]) != 56:
        raise FingerprintError("Compact fingerprint payload must contain 56 base64url characters")
    try:
        raw = base64.b64decode(
            parts[3].encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except Exception as exc:
        raise FingerprintError("Invalid fingerprint payload") from exc
    if len(raw) != 41:
        raise FingerprintError("Compact fingerprint payload must decode to 41 bytes")
    return resolution, raw


def fingerprint_dds(
    path: str | Path,
    resolutions: Iterable[int] = RESOLUTIONS,
) -> dict[str, dict]:
    decoded = decode_dds(path)
    results = {}
    source_mip = decoded.mips[0]
    for resolution in resolutions:
        if resolution not in RESOLUTIONS:
            raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
        results[str(resolution)] = {
            "status": "payload-ready",
            "payload": encode_fingerprint(
                sample_grid(source_mip, resolution),
                resolution,
            ),
            "algorithm_version": ALGORITHM_VERSION,
            "canonical_color_domain": CANONICAL_COLOR_DOMAIN,
            "canonical_view_policy": CANONICAL_VIEW_POLICY,
            "canonical_resolution": resolution,
            "canonical_format": decoded.canonical_format,
            "absolute_mip": source_mip.level,
            "source_mip_role": "dump-top-level-image",
            "runtime_source_mip_role": "srv-visible-most-detailed",
            "source_mip_width": source_mip.width,
            "source_mip_height": source_mip.height,
            "declared_mips": decoded.declared_mips,
            "available_complete_mips": len(decoded.mips),
            "mip_chain_complete": decoded.mip_chain_complete,
            "mip_selection_policy": MIP_SELECTION_POLICY,
            "source_is_srgb": decoded.source_is_srgb,
            "payload_abi_compatible": True,
            "payload_profile": PAYLOAD_PROFILE,
            "runtime_compatible": False,
            "runtime_compatibility_blockers": [
                "runtime-v3-game-parity-not-validated",
            ],
            "offline_cache_identity": {
                "resource_version": decoded.source_sha256,
                "algorithm_version": ALGORITHM_VERSION,
                "payload_profile": PAYLOAD_PROFILE,
                "resolution": resolution,
                "canonical_format": decoded.canonical_format,
                "absolute_mip": source_mip.level,
            },
        }
    return results


def fingerprint_distance(left: str, right: str) -> float:
    left_resolution, left_raw = decode_fingerprint(left)
    right_resolution, right_raw = decode_fingerprint(right)
    if left_resolution != right_resolution:
        raise FingerprintError("Fingerprint resolutions differ")
    left_hashes = struct.unpack(">4Q", left_raw[:32])
    right_hashes = struct.unpack(">4Q", right_raw[:32])
    different_bits = sum(
        (left_hash ^ right_hash).bit_count()
        for left_hash, right_hash in zip(left_hashes, right_hashes)
    )
    hash_distance = _float32(different_bits / 256.0)
    mean_distance = _float32(
        sum(abs(a - b) for a, b in zip(left_raw[32:36], right_raw[32:36]))
        / (255.0 * 4.0)
    )
    deviation_distance = _float32(
        sum(abs(a - b) for a, b in zip(left_raw[36:40], right_raw[36:40]))
        / (255.0 * 4.0)
    )
    alpha_distance = _float32(abs(left_raw[40] - right_raw[40]) / 255.0)
    distance = _float32(hash_distance * _float32(0.75))
    distance = _float32(distance + _float32(mean_distance * _float32(0.15)))
    distance = _float32(
        distance + _float32(deviation_distance * _float32(0.07))
    )
    return _float32(distance + _float32(alpha_distance * _float32(0.03)))


def _variant_payloads(identity: Mapping, resolution: int) -> list[tuple[str, str]]:
    payloads = []
    for variant in identity.get("variants") or []:
        variant_id = str(variant.get("variant") or "")
        record = (variant.get("fingerprints") or {}).get(str(resolution))
        if not variant_id or not isinstance(record, Mapping):
            continue
        if record.get("status") != "payload-ready":
            continue
        if int(record.get("algorithm_version", -1)) != ALGORITHM_VERSION:
            raise FingerprintError("Fingerprint algorithm version mismatch")
        if record.get("payload_profile") != PAYLOAD_PROFILE:
            raise FingerprintError("Fingerprint payload profile mismatch")
        if record.get("canonical_color_domain") != CANONICAL_COLOR_DOMAIN:
            raise FingerprintError("Fingerprint canonical color domain mismatch")
        if record.get("canonical_view_policy") != CANONICAL_VIEW_POLICY:
            raise FingerprintError("Fingerprint canonical view policy mismatch")
        if int(record.get("absolute_mip", -1)) < 0:
            raise FingerprintError("Runtime-v3 fingerprint requires an absolute mip")
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
    unavailable = []
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
            unavailable.append(resolution)
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
                unavailable_resolutions=tuple(unavailable),
            )

    if last is None:
        raise FingerprintError(
            "No resolution has a fingerprint for every identity variant"
        )
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
        unavailable_resolutions=tuple(unavailable),
    )
