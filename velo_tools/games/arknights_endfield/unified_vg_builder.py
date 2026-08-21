"""Build compact authoring and runtime-safe unified vertex-group maps."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy

from .bone_signature import (
    _build_bone_signature_from_blob,
    _read_palette_bases_from_vs_cb1,
    parse_vs_cb1_first_constants,
)
from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object import MigotoObject
from ._efmi_core.migoto_io.data_model.byte_buffer import Semantic
from ._efmi_core.migoto_io.migoto_model.frame_model.resources import ConstantBuffer, Resource
from .unified_vg_signature import (
    _BoneSignatureRecord,
    _apply_guarded_near_signature_aliases,
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
    vs_cb1_path: Optional[str] = None
    vs_cb1_first_constant: Optional[int] = None
    bone_count: int = 0
    vg_map: dict[int, int] = field(default_factory=dict)


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
    for candidate in (resource.bin_path, resource.bin_path_deduped, resource.txt_path, resource.txt_path_deduped):
        if candidate:
            return str(candidate)
    return None


def _first_signature_resources(raw_component) -> tuple[str | None, str | None, int | None]:
    if raw_component is None:
        return None, None, None
    for shader_call in raw_component.shader_calls:
        resources = shader_call.model_resources or shader_call.resources
        if resources is None:
            continue
        vs_t0 = resources.get_by_slot("vs-t0")
        vs_cb1 = resources.get_by_slot("vs-cb1")
        vs_t0_path = _resource_path(vs_t0)
        vs_cb1_path = _resource_path(vs_cb1)
        if not vs_t0_path or not vs_cb1_path:
            continue
        first_constant = int(vs_cb1.first_constant) if isinstance(vs_cb1, ConstantBuffer) else None
        return vs_t0_path, vs_cb1_path, first_constant
    return None, None, None


def _component_signature_adapter(component) -> _SignatureComponent:
    vertex_buffer = component.mesh.vertex_buffer
    vs_t0_path, vs_cb1_path, first_constant = _first_signature_resources(component.raw_data)
    return _SignatureComponent(
        buffers={"VB": _BufferProxy(vertex_buffer)},
        vs_t0_path=vs_t0_path,
        vs_cb1_path=vs_cb1_path,
        vs_cb1_first_constant=first_constant,
        bone_count=_compute_bone_count(vertex_buffer),
    )


def _fallback_first_constant(component: _SignatureComponent) -> int | None:
    if component.vs_cb1_first_constant is not None:
        return component.vs_cb1_first_constant
    if not component.vs_cb1_path:
        return None
    log_dir = str(Path(component.vs_cb1_path).parent)
    call_id_str = Path(component.vs_cb1_path).name.split("-", 1)[0]
    try:
        call_id_int = int(call_id_str)
    except ValueError:
        return None
    return parse_vs_cb1_first_constants(str(Path(log_dir) / "log.txt")).get((call_id_int, 1))


def build_component_maps(migoto_object: MigotoObject) -> list[dict[int, int]]:
    """Return dense per-component local-to-authoring vertex-group maps."""
    adapters = [_component_signature_adapter(component) for component in migoto_object.components]
    signature_to_canonical: dict[bytes, int] = {}
    next_global_id = 0
    signature_records: list[_BoneSignatureRecord] = []

    for component_id, (component, adapter) in enumerate(zip(migoto_object.components, adapters)):
        metadata = component.metadata
        if metadata is not None and getattr(metadata, "cpu_posed", False):
            log.info("Skipping unified VG map for CPU-posed Component_%s of %s", component_id, migoto_object.id)
            continue
        if not adapter.vs_t0_path or not adapter.vs_cb1_path:
            raise ValueError(f"Component_{component_id} is missing vs-t0 or vs-cb1")
        if adapter.bone_count <= 0:
            raise ValueError(f"Component_{component_id} has no explicit bone indices")

        first_constant = _fallback_first_constant(adapter)
        if first_constant is None:
            raise ValueError(f"Component_{component_id} has no vs-cb1 first_constant")
        adapter.vs_cb1_first_constant = int(first_constant)

        current_base, previous_base = _read_palette_bases_from_vs_cb1(
            adapter.vs_cb1_path,
            adapter.vs_cb1_first_constant,
        )
        with open(adapter.vs_t0_path, "rb") as fh:
            vs_t0_blob = fh.read()
        total_rows = len(vs_t0_blob) // 16
        if total_rows <= 0:
            raise ValueError(f"Component_{component_id} has an empty vs-t0 bone buffer")

        for local_bone in range(adapter.bone_count):
            signature = _build_bone_signature_from_blob(
                vs_t0_blob=vs_t0_blob,
                total_rows=total_rows,
                current_base=current_base,
                previous_base=previous_base,
                local_bone=local_bone,
            )
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

    return [dict(adapter.vg_map) for adapter in adapters]


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

    authoring_maps = build_component_maps(migoto_object)
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

    runtime_candidates: dict[int, list[tuple[bool, int, int]]] = {}
    for component_id, (component, mapping) in enumerate(zip(migoto_object.components, authoring_maps)):
        offset = offsets[component_id]
        weighted_counts = _weighted_vertex_counts(component, counts[component_id]) if mapping else []
        valid_source = True if valid_source_checker is None else bool(valid_source_checker(component))
        for local_id, compact_id in sorted(mapping.items()):
            runtime_candidates.setdefault(compact_id, []).append(
                (valid_source, weighted_counts[local_id], offset + local_id)
            )

    compact_to_runtime = {}
    for compact_id, candidates in runtime_candidates.items():
        valid_candidates = [candidate for candidate in candidates if candidate[0]]
        source = max(valid_candidates or candidates, key=lambda candidate: candidate[1])
        compact_to_runtime[compact_id] = source[2]

    runtime_maps = [
        {local_id: compact_to_runtime[compact_id] for local_id, compact_id in mapping.items()}
        for mapping in authoring_maps
    ]
    return UnifiedVertexGroupMaps(authoring_maps, runtime_maps, offsets, counts)


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
