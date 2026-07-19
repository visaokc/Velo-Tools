"""Split WWMI ShapeKey payloads into native batches and external CSR data."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence, Tuple


BATCH_SLOTS = 127
BATCH_OFFSET_COUNT = 128
MAX_EXTERNAL_CHANNELS = 128
VERTEX_RECORD_SIZE = 12


class ShapeKeyPlanError(ValueError):
    pass


@dataclass(frozen=True)
class CustomShapeRecord:
    shape_id: int
    vertex_id: int
    delta: Tuple[float, float, float]


@dataclass(frozen=True)
class ParsedShapeKeys:
    native_offset_bytes: bytes
    native_vertex_id_bytes: bytes
    native_vertex_offset_bytes: bytes
    native_batch_record_counts: Tuple[int, ...]
    custom_records: Tuple[CustomShapeRecord, ...]
    mesh_vertex_count: int

    @property
    def custom_ids(self) -> Tuple[int, ...]:
        return tuple(sorted({record.shape_id for record in self.custom_records}))

    @property
    def native_record_count(self) -> int:
        return sum(self.native_batch_record_counts)


@dataclass(frozen=True)
class ExternalShapeKeyCSR:
    vertex_offset_bytes: bytes
    record_channel_bytes: bytes
    record_delta_bytes: bytes
    record_count: int
    custom_ids: Tuple[int, ...]


def _unpack_u32(payload: bytes, label: str) -> Tuple[int, ...]:
    if len(payload) % 4:
        raise ShapeKeyPlanError(f"{label} byte size is not divisible by 4")
    if not payload:
        return ()
    return struct.unpack(f"<{len(payload) // 4}I", payload)


def _pack_u32(values: Iterable[int]) -> bytes:
    values = tuple(int(value) for value in values)
    if not values:
        return b""
    return struct.pack(f"<{len(values)}I", *values)


def _is_effective_delta(delta: Sequence[float]) -> bool:
    if not all(math.isfinite(value) for value in delta):
        raise ShapeKeyPlanError("ShapeKey position delta contains NaN or infinity")
    return any(value != 0.0 for value in delta)


def split_shape_key_payload(
        offset_bytes: bytes,
        vertex_id_bytes: bytes,
        vertex_offset_bytes: bytes,
        *,
        native_counts: Sequence[int],
        mesh_vertex_count: int,
) -> ParsedShapeKeys:
    """Keep Metadata-owned native slots and extract the remaining position deltas."""
    offsets = _unpack_u32(offset_bytes, "ShapeKeyOffset")
    vertex_ids = _unpack_u32(vertex_id_bytes, "ShapeKeyVertexId")
    if len(offsets) % BATCH_OFFSET_COUNT:
        raise ShapeKeyPlanError(
            "ShapeKeyOffset entry count must be a multiple of 128")
    if len(vertex_offset_bytes) % VERTEX_RECORD_SIZE:
        raise ShapeKeyPlanError(
            "ShapeKeyVertexOffset byte size must be a multiple of 12")
    if len(vertex_offset_bytes) // VERTEX_RECORD_SIZE != len(vertex_ids):
        raise ShapeKeyPlanError(
            "ShapeKey vertex ID and vertex-offset record counts disagree")
    if mesh_vertex_count < 0:
        raise ShapeKeyPlanError("mesh vertex count cannot be negative")

    exported_batch_count = len(offsets) // BATCH_OFFSET_COUNT
    if exported_batch_count < len(native_counts):
        raise ShapeKeyPlanError(
            "ShapeKey payload has fewer batches than Metadata.json")
    normalized_native_counts = []
    for batch_id, count in enumerate(native_counts):
        count = int(count)
        if not 0 <= count <= BATCH_SLOTS:
            raise ShapeKeyPlanError(
                f"Metadata batch {batch_id} shapekey_count is outside 0..127")
        normalized_native_counts.append(count)

    native_offsets = []
    native_vertex_ids = []
    native_vertex_offsets = bytearray()
    native_batch_record_counts = []
    custom_records = []
    source_record_base = 0

    for batch_id in range(exported_batch_count):
        start = batch_id * BATCH_OFFSET_COUNT
        batch_offsets = offsets[start:start + BATCH_OFFSET_COUNT]
        if batch_offsets[0] != 0:
            raise ShapeKeyPlanError(
                f"ShapeKey batch {batch_id} does not start at record 0")
        if any(left > right for left, right in zip(
                batch_offsets, batch_offsets[1:])):
            raise ShapeKeyPlanError(
                f"ShapeKey batch {batch_id} offsets are not monotonic")
        batch_record_count = batch_offsets[-1]
        if source_record_base + batch_record_count > len(vertex_ids):
            raise ShapeKeyPlanError(
                f"ShapeKey batch {batch_id} exceeds the vertex record payload")

        native_count = (
            normalized_native_counts[batch_id]
            if batch_id < len(normalized_native_counts) else 0
        )
        if batch_id < len(normalized_native_counts):
            native_end = batch_offsets[native_count]
            native_offsets.extend(batch_offsets[:native_count + 1])
            native_offsets.extend(
                [native_end] * (BATCH_OFFSET_COUNT - native_count - 1))
            native_batch_record_counts.append(native_end)
            native_vertex_ids.extend(
                vertex_ids[source_record_base:source_record_base + native_end])
            native_vertex_offsets.extend(vertex_offset_bytes[
                source_record_base * VERTEX_RECORD_SIZE:
                (source_record_base + native_end) * VERTEX_RECORD_SIZE
            ])

        for local_id in range(native_count, BATCH_SLOTS):
            first = batch_offsets[local_id]
            end = batch_offsets[local_id + 1]
            shape_id = batch_id * BATCH_SLOTS + local_id
            for local_record_id in range(first, end):
                record_id = source_record_base + local_record_id
                vertex_id = vertex_ids[record_id]
                if vertex_id >= mesh_vertex_count:
                    raise ShapeKeyPlanError(
                        f"ShapeKey {shape_id} references vertex {vertex_id}, "
                        f"outside mesh vertex count {mesh_vertex_count}")
                record_start = record_id * VERTEX_RECORD_SIZE
                delta = struct.unpack_from(
                    "<3e", vertex_offset_bytes, record_start)
                if not _is_effective_delta(delta):
                    continue
                custom_records.append(CustomShapeRecord(
                    shape_id=shape_id,
                    vertex_id=vertex_id,
                    delta=tuple(float(value) for value in delta),
                ))

        source_record_base += batch_record_count

    if source_record_base != len(vertex_ids):
        raise ShapeKeyPlanError(
            "ShapeKey batches do not consume the complete vertex record payload")

    return ParsedShapeKeys(
        native_offset_bytes=_pack_u32(native_offsets),
        native_vertex_id_bytes=_pack_u32(native_vertex_ids),
        native_vertex_offset_bytes=bytes(native_vertex_offsets),
        native_batch_record_counts=tuple(native_batch_record_counts),
        custom_records=tuple(custom_records),
        mesh_vertex_count=int(mesh_vertex_count),
    )


def assign_shape_key_channels(
        parsed_domains: Iterable[ParsedShapeKeys],
) -> Mapping[int, int]:
    shape_ids = sorted({
        record.shape_id
        for parsed in parsed_domains
        for record in parsed.custom_records
    })
    if len(shape_ids) > MAX_EXTERNAL_CHANNELS:
        raise ShapeKeyPlanError(
            "External custom ShapeKeys require "
            f"{len(shape_ids)} channels; the supported limit is 128")
    return {shape_id: channel for channel, shape_id in enumerate(shape_ids)}


def build_external_csr(
        parsed: ParsedShapeKeys,
        channels: Mapping[int, int],
) -> Optional[ExternalShapeKeyCSR]:
    if not parsed.custom_records:
        return None
    missing = sorted({
        record.shape_id for record in parsed.custom_records
        if record.shape_id not in channels
    })
    if missing:
        raise ShapeKeyPlanError(
            "External ShapeKey channel map is missing IDs: "
            + ", ".join(map(str, missing)))

    records = sorted(
        parsed.custom_records,
        key=lambda record: (record.vertex_id, record.shape_id),
    )
    vertex_offsets = [0] * (parsed.mesh_vertex_count + 1)
    record_channels = []
    record_deltas = bytearray()
    cursor = 0
    for vertex_id in range(parsed.mesh_vertex_count):
        vertex_offsets[vertex_id] = cursor
        while cursor < len(records) and records[cursor].vertex_id == vertex_id:
            record = records[cursor]
            record_channels.append(channels[record.shape_id])
            record_deltas.extend(struct.pack("<3f", *record.delta))
            cursor += 1
    vertex_offsets[-1] = cursor
    if cursor != len(records):
        raise ShapeKeyPlanError("External ShapeKey CSR contains an invalid vertex ID")

    return ExternalShapeKeyCSR(
        vertex_offset_bytes=_pack_u32(vertex_offsets),
        record_channel_bytes=_pack_u32(record_channels),
        record_delta_bytes=bytes(record_deltas),
        record_count=len(records),
        custom_ids=tuple(sorted({record.shape_id for record in records})),
    )
