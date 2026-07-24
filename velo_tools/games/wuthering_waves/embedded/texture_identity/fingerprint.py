"""Runtime-v3 canonical pixel fingerprint primitives for DDS resources."""

from __future__ import annotations

import base64
import hashlib
import itertools
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from ..slot_textures.dds_meta import DdsMeta, read_dds_meta


ALGORITHM_NAME = "canonical-pixel-fingerprint"
ALGORITHM_VERSION = 3
PAYLOAD_ENCODING = "v3:rN:rgba8-zlib:<base64url>"
CANONICAL_COLOR_DOMAIN = "encoded-rgba8"
CANONICAL_VIEW_POLICY = "compatible-non-srgb-srv"
MIP_SELECTION_POLICY = "visible-exact-square-absolute-mip-v3"
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


def select_exact_mip(decoded: DecodedDds, resolution: int) -> DecodedMip | None:
    if resolution not in RESOLUTIONS:
        raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
    for mip in decoded.mips:
        if mip.width == resolution and mip.height == resolution:
            return mip
    return None


def encode_fingerprint(rgba8: bytes, resolution: int) -> str:
    if resolution not in RESOLUTIONS:
        raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
    expected = resolution * resolution * 4
    if len(rgba8) != expected:
        raise FingerprintError(
            f"Fingerprint payload length {len(rgba8)} does not match r{resolution} RGBA8 ({expected})"
        )
    payload = base64.urlsafe_b64encode(zlib.compress(rgba8, level=9)).decode("ascii")
    return f"v3:r{resolution}:rgba8-zlib:{payload}"


def decode_fingerprint(payload: str) -> tuple[int, bytes]:
    parts = str(payload).split(":", 3)
    if len(parts) != 4 or parts[0] != "v3" or parts[2] != "rgba8-zlib":
        raise FingerprintError("Unsupported runtime-v3 fingerprint payload")
    if not parts[1].startswith("r"):
        raise FingerprintError("Fingerprint resolution tag is missing")
    try:
        resolution = int(parts[1][1:])
    except ValueError as exc:
        raise FingerprintError("Fingerprint resolution tag is invalid") from exc
    if resolution not in RESOLUTIONS:
        raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
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
        if resolution not in RESOLUTIONS:
            raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
        mip = select_exact_mip(decoded, resolution)
        common = {
            "algorithm_version": ALGORITHM_VERSION,
            "canonical_color_domain": CANONICAL_COLOR_DOMAIN,
            "canonical_view_policy": CANONICAL_VIEW_POLICY,
            "canonical_resolution": resolution,
            "canonical_format": decoded.canonical_format,
            "declared_mips": decoded.declared_mips,
            "available_complete_mips": len(decoded.mips),
            "mip_chain_complete": decoded.mip_chain_complete,
            "mip_selection_policy": MIP_SELECTION_POLICY,
            "source_is_srgb": decoded.source_is_srgb,
        }
        if mip is None:
            results[str(resolution)] = {
                **common,
                "status": "unavailable",
                "unavailable_reason": "no-exact-square-mip",
                "payload_abi_compatible": False,
                "runtime_compatible": False,
            }
            continue
        results[str(resolution)] = {
            **common,
            "status": "payload-ready",
            "payload": encode_fingerprint(mip.rgba, resolution),
            "absolute_mip": mip.level,
            "source_mip_width": mip.width,
            "source_mip_height": mip.height,
            "srv_visibility_status": "unproven",
            "payload_abi_compatible": True,
            "runtime_compatible": False,
            "runtime_compatibility_blockers": [
                "srv-visible-absolute-mip-witness-unavailable",
                "runtime-v3-game-parity-not-validated",
            ],
            "offline_cache_identity": {
                "resource_version": decoded.source_sha256,
                "algorithm_version": ALGORITHM_VERSION,
                "resolution": resolution,
                "canonical_format": decoded.canonical_format,
                "absolute_mip": mip.level,
            },
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
        if record.get("status") != "payload-ready":
            continue
        if int(record.get("algorithm_version", -1)) != ALGORITHM_VERSION:
            raise FingerprintError("Fingerprint algorithm version mismatch")
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
            "No resolution has an exact square mip for every identity variant"
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
