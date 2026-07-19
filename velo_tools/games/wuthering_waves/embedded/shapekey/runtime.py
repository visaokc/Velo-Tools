"""Adapt planned ShapeKey payloads to WWMI exporter buffer objects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

from ..._wwmi_core.migoto_io.data_model.byte_buffer import (
    AbstractSemantic,
    BufferLayout,
    BufferSemantic,
    NumpyBuffer,
    Semantic,
)
from ..._wwmi_core.migoto_io.data_model.dxgi_format import DXGIFormat

from .planner import (
    ExternalShapeKeyCSR,
    ParsedShapeKeys,
    ShapeKeyPlanError,
    assign_shape_key_channels,
    build_external_csr,
    split_shape_key_payload,
)


NATIVE_BUFFER_NAMES = (
    "ShapeKeyOffset",
    "ShapeKeyVertexId",
    "ShapeKeyVertexOffset",
)
EXTERNAL_BUFFER_NAMES = (
    "ExternalShapeKeyVertexOffset",
    "ExternalShapeKeyRecordChannel",
    "ExternalShapeKeyRecordDelta",
)


@dataclass(frozen=True)
class DomainShapeKeyPlan:
    parsed: ParsedShapeKeys
    external: Optional[ExternalShapeKeyCSR]
    channels: Mapping[int, int]
    enabled: bool

    @property
    def has_native(self) -> bool:
        return bool(self.parsed.native_record_count)

    @property
    def has_external(self) -> bool:
        return self.external is not None


def _native_counts(extracted_object: Any) -> Tuple[int, ...]:
    shapes = getattr(extracted_object, "shapekeys", None)
    if shapes is None or not hasattr(shapes, "batches"):
        raise ShapeKeyPlanError(
            "Metadata.json does not provide ShapeKey batch metadata")
    batches = tuple(getattr(shapes, "batches", ()) or ())
    for batch_id, batch in enumerate(batches):
        if not hasattr(batch, "shapekey_count"):
            raise ShapeKeyPlanError(
                f"Metadata ShapeKey batch {batch_id} has no shapekey_count")
    return tuple(int(batch.shapekey_count) for batch in batches)


def _parse_domain(domain: Any) -> Optional[ParsedShapeKeys]:
    buffers = domain.buffers
    present = [name in buffers for name in NATIVE_BUFFER_NAMES]
    if not any(present):
        return None
    if not all(present):
        missing = [
            name for name, available in zip(NATIVE_BUFFER_NAMES, present)
            if not available
        ]
        raise ShapeKeyPlanError(
            "ShapeKey buffer set is incomplete; missing " + ", ".join(missing))
    native_counts = _native_counts(domain.extracted_object)
    if not native_counts:
        raise ShapeKeyPlanError(
            "ShapeKey buffers exist but Metadata.json has no native batch ranges")
    vertex_count = int(getattr(domain.merged_object, "vertex_count", 0))
    return split_shape_key_payload(
        buffers["ShapeKeyOffset"].get_bytes(),
        buffers["ShapeKeyVertexId"].get_bytes(),
        buffers["ShapeKeyVertexOffset"].get_bytes(),
        native_counts=native_counts,
        mesh_vertex_count=vertex_count,
    )


def _buffer(payload: bytes, fmt: DXGIFormat) -> NumpyBuffer:
    layout = BufferLayout([
        BufferSemantic(AbstractSemantic(Semantic.RawData), fmt),
    ])
    result = NumpyBuffer(layout)
    result.import_raw_data(payload)
    return result


def _update_merged_metadata(domain: Any, parsed: ParsedShapeKeys) -> None:
    shapes = domain.merged_object.shapekeys
    existing = list(getattr(shapes, "batches", ()) or ())
    if len(existing) < len(parsed.native_batch_record_counts):
        raise ShapeKeyPlanError(
            "Merged ShapeKey batch metadata is shorter than Metadata.json")
    batches = existing[:len(parsed.native_batch_record_counts)]
    record_offset = 0
    for batch, count in zip(batches, parsed.native_batch_record_counts):
        batch.vertex_offset = record_offset
        batch.vertex_count = count
        record_offset += count
    shapes.batches = batches
    shapes.vertex_count = record_offset
    shapes.vertex_count_batch0 = batches[0].vertex_count if batches else 0
    shapes.vertex_count_batch1 = batches[1].vertex_count if len(batches) > 1 else 0
    shapes.shapekey_count = 127 * len(batches)


def _apply_domain(
        domain: Any,
        parsed: ParsedShapeKeys,
        channels: Mapping[int, int],
        enabled: bool,
) -> DomainShapeKeyPlan:
    buffers = domain.buffers
    buffers["ShapeKeyOffset"].import_raw_data(parsed.native_offset_bytes)
    buffers["ShapeKeyVertexId"].import_raw_data(parsed.native_vertex_id_bytes)
    buffers["ShapeKeyVertexOffset"].import_raw_data(
        parsed.native_vertex_offset_bytes)
    _update_merged_metadata(domain, parsed)

    for name in EXTERNAL_BUFFER_NAMES:
        buffers.pop(name, None)
    external = build_external_csr(parsed, channels) if enabled else None
    if external is not None:
        buffers["ExternalShapeKeyVertexOffset"] = _buffer(
            external.vertex_offset_bytes, DXGIFormat.R32_UINT)
        buffers["ExternalShapeKeyRecordChannel"] = _buffer(
            external.record_channel_bytes, DXGIFormat.R32_UINT)
        buffers["ExternalShapeKeyRecordDelta"] = _buffer(
            external.record_delta_bytes, DXGIFormat.R32G32B32_FLOAT)
    plan = DomainShapeKeyPlan(
        parsed=parsed,
        external=external,
        channels=dict(channels),
        enabled=bool(enabled),
    )
    domain.velo_shape_key_plan = plan
    return plan


def prepare_domains(
        domains: Sequence[Any],
        *,
        enabled: bool,
) -> Tuple[Mapping[int, int], Tuple[Optional[DomainShapeKeyPlan], ...]]:
    parsed = tuple(_parse_domain(domain) for domain in domains)
    channels = assign_shape_key_channels(
        item for item in parsed if item is not None) if enabled else {}
    plans = tuple(
        None if item is None else _apply_domain(domain, item, channels, enabled)
        for domain, item in zip(domains, parsed)
    )
    return channels, plans


def prepare_exporter(exporter: Any, *, enabled: bool) -> Optional[DomainShapeKeyPlan]:
    _channels, plans = prepare_domains((exporter,), enabled=enabled)
    return plans[0]


def prepare_units(
        units: Sequence[Any], *, enabled: bool,
) -> Tuple[Mapping[int, int], Tuple[Optional[DomainShapeKeyPlan], ...]]:
    return prepare_domains(units, enabled=enabled)
