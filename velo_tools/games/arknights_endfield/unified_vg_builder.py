"""Build compact authoring and runtime-safe unified vertex-group maps."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy

from .bone_signature import (
    _build_bone_signature_from_blob,
    _read_palette_bases_from_instance_config,
    parse_vs_cb_first_constants,
)
from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object import MigotoObject
from ._efmi_core.migoto_io.data_model.byte_buffer import Semantic
from ._efmi_core.migoto_io.migoto_model.frame_model.resources import ConstantBuffer, Resource
from ._efmi_core.migoto_io.migoto_model.types import ShaderType
from .unified_vg_signature import (
    _BoneSignatureRecord,
    _apply_guarded_near_signature_aliases,
    _canonical_signature,
    _compute_bone_count,
    _signature_matrix_values,
)


log = logging.getLogger(__name__)


class _BufferProxy:
    def __init__(self, buffer):
        self._buffer = buffer

    def get_field(self, field):
        if hasattr(field, "get_name"):
            field = field.get_name()
        return self._buffer.get_field(field)


@dataclass
class _SignatureComponent:
    buffers: dict[str, _BufferProxy]
    vs_t0_path: Optional[str] = None
    instance_config_path: Optional[str] = None
    instance_config_first_constant: Optional[int] = None
    instance_config_slot: Optional[int] = None
    bone_count: int = 0
    vg_map: dict[int, int] = field(default_factory=dict)
    resource_candidates: list[tuple[str, str, int | None, int]] = field(default_factory=list)


@dataclass(frozen=True)
class UnifiedVertexGroupMaps:
    authoring_maps: list[dict[int, int]]
    runtime_maps: list[dict[int, int]]
    vg_offsets: list[int]
    vg_counts: list[int]

    @property
    def authoring_group_count(self) -> int:
        ids = {global_id for mapping in self.authoring_maps for global_id in mapping.values()}
        return len(ids)


def _resource_path(resource: Resource | None) -> str | None:
    if resource is None:
        return None
    for candidate in (resource.bin_path, resource.bin_path_deduped):
        if candidate:
            return str(candidate)
    return None


def _signature_resource_candidates(
    raw_component,
) -> list[tuple[str, str, int | None, int]]:
    if raw_component is None:
        return []
    candidates = []
    for shader_call in raw_component.shader_calls:
        resources = shader_call.resources
        if resources is None:
            continue
        vs_t0 = resources.get_by_slot("vs-t0")
        vs_t0_path = _resource_path(vs_t0)
        for slot, constant_buffer in resources.constant_buffers.items():
            if slot.shader_type != ShaderType.Vertex or constant_buffer.num_constants != 4096:
                continue
            instance_config_path = _resource_path(constant_buffer)
            if not vs_t0_path or not instance_config_path:
                continue
            first_constant = (
                int(constant_buffer.first_constant)
                if isinstance(constant_buffer, ConstantBuffer)
                else None
            )
            candidate = (
                vs_t0_path,
                instance_config_path,
                first_constant,
                int(slot.slot_id),
            )
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _component_signature_adapter(component) -> _SignatureComponent:
    vertex_buffer = component.mesh.vertex_buffer
    candidates = _signature_resource_candidates(component.raw_data)
    first = candidates[0] if candidates else (None, None, None, None)
    vs_t0_path, instance_config_path, first_constant, instance_config_slot = first
    return _SignatureComponent(
        buffers={"VB": _BufferProxy(vertex_buffer)},
        vs_t0_path=vs_t0_path,
        instance_config_path=instance_config_path,
        instance_config_first_constant=first_constant,
        instance_config_slot=instance_config_slot,
        bone_count=_compute_bone_count(vertex_buffer),
        resource_candidates=candidates,
    )


def _fallback_first_constant(component: _SignatureComponent) -> int | None:
    if component.instance_config_first_constant is not None:
        return component.instance_config_first_constant
    if not component.instance_config_path or component.instance_config_slot is None:
        return None
    log_dir = str(Path(component.instance_config_path).parent)
    call_id_str = Path(component.instance_config_path).name.split("-", 1)[0]
    try:
        call_id_int = int(call_id_str)
    except ValueError:
        return None
    return parse_vs_cb_first_constants(str(Path(log_dir) / "log.txt")).get(
        (call_id_int, component.instance_config_slot)
    )


def _candidate_signatures(
    candidate: tuple[str, str, int | None, int],
    bone_count: int,
) -> tuple[bytes, ...]:
    vs_t0_path, instance_config_path, first_constant, slot = candidate
    if first_constant is None:
        adapter = _SignatureComponent(
            buffers={},
            instance_config_path=instance_config_path,
            instance_config_slot=slot,
        )
        first_constant = _fallback_first_constant(adapter)
    if first_constant is None:
        raise ValueError("instance-config resource has no first_constant")
    current_base, previous_base = _read_palette_bases_from_instance_config(
        instance_config_path,
        first_constant,
    )
    with open(vs_t0_path, "rb") as fh:
        vs_t0_blob = fh.read()
    if len(vs_t0_blob) % 16 != 0:
        raise ValueError("vs-t0 bone buffer size is not aligned to 16-byte rows")
    total_rows = len(vs_t0_blob) // 16
    if total_rows <= 0:
        raise ValueError("vs-t0 bone buffer is empty")
    return tuple(
        _canonical_signature(
            _build_bone_signature_from_blob(
                vs_t0_blob=vs_t0_blob,
                total_rows=total_rows,
                current_base=current_base,
                previous_base=previous_base,
                local_bone=local_bone,
            )
        )
        for local_bone in range(bone_count)
    )


def _weighted_position_bounds(component, bone_count: int):
    positions = component.mesh.get_data(Semantic.Position)
    vg_ids = component.mesh.get_data(Semantic.Blendindices)
    vg_weights = component.mesh.get_data(Semantic.Blendweights)
    if positions is None or vg_ids is None:
        return [None] * bone_count

    positions = numpy.asarray(positions, dtype=numpy.float64)[..., :3]
    vg_ids = numpy.asarray(vg_ids)
    if vg_ids.ndim == 1:
        vg_ids = vg_ids[:, None]
    if vg_weights is None:
        vg_weights = numpy.zeros_like(vg_ids, dtype=numpy.float32)
        vg_weights[..., 0] = 1.0
    else:
        vg_weights = numpy.asarray(vg_weights)
        if vg_weights.ndim == 1:
            vg_weights = vg_weights[:, None]

    row_ids = numpy.broadcast_to(numpy.arange(len(positions))[:, None], vg_ids.shape)
    active = (vg_weights != 0) & (vg_ids >= 0) & (vg_ids < bone_count)
    active_bones = vg_ids[active].astype(numpy.intp, copy=False)
    active_positions = positions[row_ids[active]]
    minimums = numpy.full((bone_count, 3), numpy.inf, dtype=numpy.float64)
    maximums = numpy.full((bone_count, 3), -numpy.inf, dtype=numpy.float64)
    numpy.minimum.at(minimums, active_bones, active_positions)
    numpy.maximum.at(maximums, active_bones, active_positions)
    return [
        None if not numpy.isfinite(minimums[local_bone]).all() else (minimums[local_bone], maximums[local_bone])
        for local_bone in range(bone_count)
    ]


def _build_component_maps_and_signatures(
    migoto_object: MigotoObject,
) -> tuple[list[dict[int, int]], list[_BoneSignatureRecord]]:
    adapters = [_component_signature_adapter(component) for component in migoto_object.components]
    signature_to_canonical: dict[bytes, int] = {}
    next_global_id = 0
    signature_records: list[_BoneSignatureRecord] = []

    for component_id, (component, adapter) in enumerate(zip(migoto_object.components, adapters)):
        metadata = component.metadata
        if metadata is not None and getattr(metadata, "cpu_posed", False):
            log.info("Skipping unified VG map for CPU-posed Component_%s of %s", component_id, migoto_object.id)
            continue
        if not adapter.resource_candidates:
            raise ValueError(f"Component_{component_id} is missing vs-t0 or instance-config CB")
        if adapter.bone_count <= 0:
            raise ValueError(f"Component_{component_id} has no explicit bone indices")

        valid_candidates = []
        failures = []
        for candidate in adapter.resource_candidates:
            try:
                valid_candidates.append((candidate, _candidate_signatures(candidate, adapter.bone_count)))
            except (OSError, ValueError) as exc:
                failures.append(str(exc))
        if not valid_candidates:
            detail = failures[0] if failures else "no readable candidate"
            raise ValueError(f"Component_{component_id} has no valid matrix resource pair: {detail}")
        distinct_signatures = {signatures for _candidate, signatures in valid_candidates}
        if len(distinct_signatures) != 1:
            raise ValueError(
                f"Component_{component_id} has ambiguous matrix resource pairs "
                f"({len(distinct_signatures)} distinct signature sets)"
            )
        selected_candidate, selected_signatures = valid_candidates[0]
        (
            adapter.vs_t0_path,
            adapter.instance_config_path,
            adapter.instance_config_first_constant,
            adapter.instance_config_slot,
        ) = selected_candidate
        weighted_bounds = _weighted_position_bounds(component, adapter.bone_count)

        for local_bone, signature in enumerate(selected_signatures):
            canonical = signature_to_canonical.get(signature)
            if canonical is None:
                canonical = next_global_id
                signature_to_canonical[signature] = canonical
                next_global_id += 1
            adapter.vg_map[local_bone] = canonical
            signature_records.append(
                _BoneSignatureRecord(
                    component_id=component_id,
                    component=adapter,
                    local_bone=local_bone,
                    global_id=canonical,
                    signature=signature,
                    matrix_values=_signature_matrix_values(signature),
                    weighted_bounds=weighted_bounds[local_bone],
                )
            )

    aliases_applied = _apply_guarded_near_signature_aliases(signature_records, migoto_object.id)
    if aliases_applied:
        log.info("Applied %s guarded near-signature VG aliases for %s", aliases_applied, migoto_object.id)

    for component_id, (component, adapter) in enumerate(zip(migoto_object.components, adapters)):
        if component.metadata is not None and getattr(component.metadata, "cpu_posed", False):
            continue
        expected = set(range(adapter.bone_count))
        actual = set(adapter.vg_map)
        if actual != expected:
            missing = sorted(expected - actual)
            raise ValueError(f"Component_{component_id} has incomplete bone signatures: {missing[:12]}")

    return [dict(adapter.vg_map) for adapter in adapters], signature_records


def build_component_maps(migoto_object: MigotoObject) -> list[dict[int, int]]:
    """Return dense per-component local-to-authoring vertex-group maps."""
    maps, _records = _build_component_maps_and_signatures(migoto_object)
    return maps


def _weighted_vertex_counts(component, count: int) -> list[int]:
    vg_ids = component.mesh.get_data(Semantic.Blendindices)
    vg_weights = component.mesh.get_data(Semantic.Blendweights)
    if vg_weights is None:
        vg_weights = numpy.zeros_like(vg_ids, dtype=numpy.float32)
        vg_weights[..., 0] = 1.0
    counts = numpy.bincount(vg_ids[vg_weights != 0], minlength=count)
    return [int(value) for value in counts[:count]]


def build_unified_maps(
    migoto_object: MigotoObject,
    valid_source_checker: Callable[[object], bool] | None = None,
) -> UnifiedVertexGroupMaps:
    """Build compact authoring IDs plus valid merged-skeleton runtime IDs."""
    if migoto_object.metadata is None:
        migoto_object.build_metadata()

    authoring_maps, signature_records = _build_component_maps_and_signatures(migoto_object)
    counts: list[int] = []
    offsets: list[int] = []
    next_offset = 0
    for component, mapping in zip(migoto_object.components, authoring_maps):
        cpu_posed = bool(component.metadata and getattr(component.metadata, "cpu_posed", False))
        count = 0 if cpu_posed else len(mapping)
        offsets.append(0 if cpu_posed else next_offset)
        counts.append(count)
        if not cpu_posed:
            next_offset += count

    signatures = {
        (record.component_id, record.local_bone): record.signature
        for record in signature_records
    }
    runtime_candidates: dict[tuple[int, bytes], list[tuple[bool, int, int]]] = {}
    for component_id, (component, mapping) in enumerate(zip(migoto_object.components, authoring_maps)):
        offset = offsets[component_id]
        weighted_counts = _weighted_vertex_counts(component, counts[component_id]) if mapping else []
        valid_source = bool(mapping) and (
            True if valid_source_checker is None else bool(valid_source_checker(component))
        )
        for local_id, compact_id in sorted(mapping.items()):
            identity = (compact_id, signatures[(component_id, local_id)])
            runtime_candidates.setdefault(identity, []).append(
                (valid_source, weighted_counts[local_id], offset + local_id)
            )

    identity_to_runtime = {}
    for identity, candidates in runtime_candidates.items():
        valid_candidates = [candidate for candidate in candidates if candidate[0]]
        if valid_candidates:
            source = max(valid_candidates, key=lambda candidate: candidate[1])
        else:
            source = candidates[0]
        identity_to_runtime[identity] = source[2]

    runtime_maps = [
        {
            local_id: identity_to_runtime[(compact_id, signatures[(component_id, local_id)])]
            for local_id, compact_id in mapping.items()
        }
        for component_id, mapping in enumerate(authoring_maps)
    ]
    return UnifiedVertexGroupMaps(
        authoring_maps,
        runtime_maps,
        offsets,
        counts,
    )


def apply_to_metadata(
    migoto_object: MigotoObject,
    valid_source_checker: Callable[[object], bool] | None = None,
) -> UnifiedVertexGroupMaps:
    """Replace extraction metadata maps with the matrix-signature mapping."""
    result = build_unified_maps(migoto_object, valid_source_checker)
    for component, authoring_map, runtime_map, offset, count in zip(
        migoto_object.metadata.components,
        result.authoring_maps,
        result.runtime_maps,
        result.vg_offsets,
        result.vg_counts,
    ):
        component.vg_map = authoring_map
        component.vg_offset = offset
        component.vg_count = count
        component.runtime_vg_map = runtime_map
    return result
