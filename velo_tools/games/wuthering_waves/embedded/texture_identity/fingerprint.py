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

import numpy

from ..slot_textures.dds_meta import DdsMeta, read_dds_meta


ALGORITHM_NAME = "canonical-pixel-fingerprint"
ALGORITHM_VERSION = 3
PAYLOAD_ENCODING = "v3:rN:rgba8-phash:<base64url>"
PAYLOAD_PROFILE = "rgba8-phash"
CANONICAL_COLOR_DOMAIN = "encoded-rgba8"
CANONICAL_VIEW_POLICY = "compatible-non-srgb-srv"
MIP_SELECTION_POLICY = "simulated-streaming-chain-to-256-area-average-v3"
RESOLUTIONS = (16, 32, 64, 128, 256)
MINIMUM_STREAMING_EXTENT = 256
DEFAULT_TOLERANCE_FLOOR = 2.0 / 255.0
MINIMUM_MARGIN_SAFETY_FACTOR = 0.25
PREFERRED_MINIMUM_MATCH_MARGIN = 0.002

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
    observed_minimum_margin: float | None
    preferred_minimum_margin: float
    preferred_margin_met: bool
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


def _decode_dds_with_blender(
    path: Path,
    meta: DdsMeta,
    source_data: bytes,
) -> DecodedDds:
    try:
        import bpy
    except ImportError as exc:
        raise FingerprintError(
            f"Unsupported prototype DDS format outside Blender: {meta.format or 'unknown'}"
        ) from exc
    if getattr(getattr(bpy, "data", None), "images", None) is None:
        raise FingerprintError(
            f"Unsupported prototype DDS format outside Blender: {meta.format or 'unknown'}"
        )

    image = None
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
        image.colorspace_settings.name = "Non-Color"
        width, height = (int(value) for value in image.size)
        if width <= 0 or height <= 0:
            raise FingerprintError(f"Blender could not decode DDS pixels: {path}")
        pixels = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(pixels)
        rgba = numpy.clip(
            numpy.floor(pixels.reshape(height, width, 4)[::-1] * 255.0 + 0.5),
            0,
            255,
        ).astype(numpy.uint8)
        decoded_mips = (
            DecodedMip(
                level=0,
                width=width,
                height=height,
                rgba=rgba.tobytes(),
            ),
        )
    finally:
        if image is not None:
            bpy.data.images.remove(image)

    declared_mips = max(int(meta.mips), 1)
    return DecodedDds(
        meta=meta,
        mips=decoded_mips,
        declared_mips=declared_mips,
        mip_chain_complete=declared_mips == 1,
        source_is_srgb=meta.format.endswith("_SRGB"),
        canonical_format=canonical_format(meta.format),
        source_sha256=hashlib.sha256(source_data).hexdigest(),
    )


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
    data = path.read_bytes()
    if meta.format not in supported:
        return _decode_dds_with_blender(path, meta, data)
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


def _sample_grid_values(mip: DecodedMip, resolution: int) -> tuple[float, ...]:
    """Match the runtime v3 integer cell area-average Load coordinates."""
    if resolution not in RESOLUTIONS:
        raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
    if mip.width <= 0 or mip.height <= 0:
        raise FingerprintError("DDS mip dimensions must be positive")
    source = numpy.frombuffer(mip.rgba, dtype=numpy.uint8).reshape(
        mip.height,
        mip.width,
        4,
    )
    if mip.width % resolution == 0 and mip.height % resolution == 0:
        block_width = mip.width // resolution
        block_height = mip.height // resolution
        sums = source.reshape(
            resolution,
            block_height,
            resolution,
            block_width,
            4,
        ).sum(axis=(1, 3), dtype=numpy.uint64)
        counts = block_width * block_height
    else:
        integral = numpy.pad(
            source.astype(numpy.uint64).cumsum(axis=0).cumsum(axis=1),
            ((1, 0), (1, 0), (0, 0)),
        )
        begin_x = numpy.arange(resolution) * mip.width // resolution
        begin_y = numpy.arange(resolution) * mip.height // resolution
        end_x = numpy.minimum(
            mip.width,
            numpy.maximum(
                (numpy.arange(resolution) + 1) * mip.width // resolution,
                begin_x + 1,
            ),
        )
        end_y = numpy.minimum(
            mip.height,
            numpy.maximum(
                (numpy.arange(resolution) + 1) * mip.height // resolution,
                begin_y + 1,
            ),
        )
        sums = (
            integral[end_y[:, None], end_x[None, :]]
            - integral[begin_y[:, None], end_x[None, :]]
            - integral[end_y[:, None], begin_x[None, :]]
            + integral[begin_y[:, None], begin_x[None, :]]
        )
        counts = (
            (end_y - begin_y)[:, None]
            * (end_x - begin_x)[None, :]
        )
        counts = counts[:, :, None]
    sampled = sums.astype(numpy.float32)
    sampled /= numpy.float32(counts)
    sampled /= numpy.float32(255.0)
    return tuple(float(value) for value in sampled.reshape(-1))


def _half_size_mip(mip: DecodedMip, level: int) -> DecodedMip:
    target_width = max(1, mip.width // 2)
    target_height = max(1, mip.height // 2)
    source = numpy.frombuffer(mip.rgba, dtype=numpy.uint8).reshape(
        mip.height,
        mip.width,
        4,
    )
    if mip.width == target_width * 2 and mip.height == target_height * 2:
        sums = source.reshape(
            target_height,
            2,
            target_width,
            2,
            4,
        ).sum(axis=(1, 3), dtype=numpy.uint16)
        resized = ((sums + 2) // 4).astype(numpy.uint8)
    else:
        resized = numpy.empty((target_height, target_width, 4), dtype=numpy.uint8)
        for y in range(target_height):
            begin_y = y * mip.height // target_height
            end_y = min(
                mip.height,
                max((y + 1) * mip.height // target_height, begin_y + 1),
            )
            for x in range(target_width):
                begin_x = x * mip.width // target_width
                end_x = min(
                    mip.width,
                    max((x + 1) * mip.width // target_width, begin_x + 1),
                )
                block = source[begin_y:end_y, begin_x:end_x].astype(numpy.uint64)
                count = block.shape[0] * block.shape[1]
                resized[y, x] = ((block.sum(axis=(0, 1)) + count // 2) // count)
    return DecodedMip(
        level=level,
        width=target_width,
        height=target_height,
        rgba=resized.tobytes(),
    )


def simulated_streaming_chain(source_mip: DecodedMip) -> tuple[DecodedMip, ...]:
    chain = [source_mip]
    current = source_mip
    level = 1
    while max(current.width, current.height) > MINIMUM_STREAMING_EXTENT:
        next_mip = _half_size_mip(current, level)
        if max(next_mip.width, next_mip.height) < MINIMUM_STREAMING_EXTENT:
            break
        chain.append(next_mip)
        current = next_mip
        level += 1
    return tuple(chain)


def sample_grid(mip: DecodedMip, resolution: int) -> bytes:
    return bytes(_quantize_unit(value) for value in _sample_grid_values(mip, resolution))


def _encode_fingerprint_samples(samples: Sequence[float], resolution: int) -> str:
    expected = resolution * resolution * 4
    if resolution not in RESOLUTIONS or len(samples) != expected:
        raise FingerprintError(
            f"Fingerprint sample count {len(samples)} does not match r{resolution} RGBA ({expected})"
        )
    sample_count = resolution * resolution
    sums = [0.0] * 4
    squared_sums = [0.0] * 4
    covered_alpha = 0
    for offset in range(0, len(samples), 4):
        for component in range(4):
            value = _float32(samples[offset + component])
            sums[component] += value
            squared_sums[component] += _float32(value * value)
        if samples[offset + 3] >= 0.5:
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

    pi = math.acos(-1.0)
    basis = []
    for index in range(8):
        frequency = (index * (resolution - 1) + 3) // 7
        basis.append(
            tuple(
                math.cos(
                    pi * (2.0 * position + 1.0) * frequency
                    / (2.0 * resolution)
                )
                for position in range(resolution)
            )
        )

    channel_hashes = []
    for component in range(4):
        coefficients = []
        for v in range(8):
            for u in range(8):
                coefficient = 0.0
                for y in range(resolution):
                    basis_y = basis[v][y]
                    for x in range(resolution):
                        coefficient += (
                            samples[(y * resolution + x) * 4 + component]
                            * basis[u][x]
                            * basis_y
                        )
                coefficients.append(coefficient)
        median = sorted(coefficients[1:])[31]
        channel_hash = 0
        for index, coefficient in enumerate(coefficients):
            if coefficient > median:
                channel_hash |= 1 << index
        channel_hashes.append(channel_hash)

    compact = (
        struct.pack(">4Q", *channel_hashes)
        + bytes(means)
        + bytes(deviations)
        + bytes((alpha_coverage,))
    )
    payload = base64.urlsafe_b64encode(compact).decode("ascii")
    return f"v3:r{resolution}:rgba8-phash:{payload}"


def encode_fingerprint(rgba8: bytes, resolution: int) -> str:
    expected = resolution * resolution * 4
    if len(rgba8) != expected:
        raise FingerprintError(
            f"Fingerprint payload length {len(rgba8)} does not match r{resolution} RGBA8 ({expected})"
        )
    return _encode_fingerprint_samples(
        tuple(_BYTE_TO_FLOAT[value] for value in rgba8),
        resolution,
    )


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
    streaming_chain = simulated_streaming_chain(source_mip)
    for resolution in resolutions:
        if resolution not in RESOLUTIONS:
            raise FingerprintError(f"Unsupported fingerprint resolution: {resolution}")
        streaming_variants = []
        for streaming_mip in streaming_chain:
            streaming_variants.append(
                {
                    "variant": (
                        f"{streaming_mip.width}x{streaming_mip.height}"
                    ),
                    "width": streaming_mip.width,
                    "height": streaming_mip.height,
                    "simulated": streaming_mip.level != source_mip.level,
                    "payload": _encode_fingerprint_samples(
                        _sample_grid_values(streaming_mip, resolution),
                        resolution,
                    ),
                }
            )
        results[str(resolution)] = {
            "status": "payload-ready",
            "payload": streaming_variants[0]["payload"],
            "streaming_variants": streaming_variants,
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
            "minimum_streaming_extent": MINIMUM_STREAMING_EXTENT,
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
    distance = _float32(hash_distance * _float32(0.064))
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
            return []
        if record.get("status") != "payload-ready":
            return []
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
        streaming_variants = record.get("streaming_variants")
        has_streaming_variants = (
            isinstance(streaming_variants, Sequence)
            and not isinstance(streaming_variants, (str, bytes))
        )
        if not has_streaming_variants:
            streaming_variants = [
                {
                    "variant": "source",
                    "payload": record.get("payload"),
                }
            ]
        if isinstance(streaming_variants, (str, bytes)):
            return []
        if not isinstance(streaming_variants, Sequence):
            return []
        if not streaming_variants:
            return []
        for streaming_variant in streaming_variants:
            if not isinstance(streaming_variant, Mapping):
                return []
            streaming_id = str(streaming_variant.get("variant") or "")
            payload = str(streaming_variant.get("payload") or "")
            payload_resolution, _ = decode_fingerprint(payload)
            if (
                not streaming_id
                or payload_resolution != resolution
                or int(record.get("canonical_resolution", -1)) != resolution
            ):
                raise FingerprintError(
                    f"Manifest key r{resolution} disagrees with its fingerprint record"
                )
            sample_id = (
                f"{variant_id}@{streaming_id}"
                if has_streaming_variants
                else variant_id
            )
            payloads.append((sample_id, payload))
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
    minimum_margin: float | None = None,
) -> CollisionSelection:
    if not identities:
        raise FingerprintError("A collision group must contain at least one identity")

    last = None
    viable = []
    unavailable = []
    for resolution in RESOLUTIONS:
        per_identity = []
        references = {}
        reference_variants = {}
        complete = True
        for identity in identities:
            identity_id = str(identity.get("identity") or "")
            payloads = _variant_payloads(identity, resolution)
            if not identity_id or not payloads:
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
        classification_margins = []
        for identity_id, payloads in per_identity:
            reference = references[identity_id]
            other_references = [
                payload
                for other_id, payload in references.items()
                if other_id != identity_id
            ]
            if not other_references:
                continue
            for _, payload in payloads:
                own_distance = fingerprint_distance(payload, reference)
                second_distance = min(
                    fingerprint_distance(payload, other)
                    for other in other_references
                )
                classification_margins.append(second_distance - own_distance)
        observed_minimum_margin = (
            min(classification_margins)
            if classification_margins
            else None
        )
        derived_minimum_margin = max(
            0.0,
            float(observed_minimum_margin or 0.0)
            * MINIMUM_MARGIN_SAFETY_FACTOR,
        )
        selected_minimum_margin = (
            float(minimum_margin)
            if minimum_margin is not None
            else derived_minimum_margin
        )
        last = (
            resolution,
            references,
            reference_variants,
            effective_tolerance,
            maximum_intra,
            nearest_inter,
            observed_minimum_margin,
            selected_minimum_margin,
        )
        if observed_minimum_margin is None:
            return CollisionSelection(
                resolution=resolution,
                fingerprints=references,
                reference_variants=reference_variants,
                tolerance=effective_tolerance,
                tolerance_floor=tolerance_floor,
                minimum_margin=selected_minimum_margin,
                maximum_intra_distance=maximum_intra,
                nearest_inter_distance=nearest_inter,
                observed_minimum_margin=observed_minimum_margin,
                preferred_minimum_margin=PREFERRED_MINIMUM_MATCH_MARGIN,
                preferred_margin_met=True,
                pixel_ambiguous=False,
                unavailable_resolutions=tuple(unavailable),
            )
        if (
            observed_minimum_margin > 0.0
            and observed_minimum_margin >= selected_minimum_margin
        ):
            viable.append(last)
            preferred_target = (
                float(minimum_margin)
                if minimum_margin is not None
                else PREFERRED_MINIMUM_MATCH_MARGIN
            )
            if selected_minimum_margin >= preferred_target:
                return CollisionSelection(
                    resolution=resolution,
                    fingerprints=references,
                    reference_variants=reference_variants,
                    tolerance=effective_tolerance,
                    tolerance_floor=tolerance_floor,
                    minimum_margin=selected_minimum_margin,
                    maximum_intra_distance=maximum_intra,
                    nearest_inter_distance=nearest_inter,
                    observed_minimum_margin=observed_minimum_margin,
                    preferred_minimum_margin=PREFERRED_MINIMUM_MATCH_MARGIN,
                    preferred_margin_met=True,
                    pixel_ambiguous=False,
                    unavailable_resolutions=tuple(unavailable),
                )

    if last is None:
        raise FingerprintError(
            "No resolution has a fingerprint for every identity variant"
        )
    if viable:
        last = max(viable, key=lambda item: (item[7], -item[0]))
    (
        resolution,
        references,
        reference_variants,
        effective_tolerance,
        maximum_intra,
        nearest_inter,
        observed_minimum_margin,
        selected_minimum_margin,
    ) = last
    return CollisionSelection(
        resolution=resolution,
        fingerprints=references,
        reference_variants=reference_variants,
        tolerance=effective_tolerance,
        tolerance_floor=tolerance_floor,
        minimum_margin=selected_minimum_margin,
        maximum_intra_distance=maximum_intra,
        nearest_inter_distance=nearest_inter,
        observed_minimum_margin=observed_minimum_margin,
        preferred_minimum_margin=PREFERRED_MINIMUM_MATCH_MARGIN,
        preferred_margin_met=False,
        pixel_ambiguous=not bool(viable),
        unavailable_resolutions=tuple(unavailable),
    )
