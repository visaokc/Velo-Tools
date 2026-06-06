"""Generate Velo unified vertex-group sidecars from EFMI 0.4.3 objects."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import vgmap
from .bone_signature import (
    _build_bone_signature_from_blob,
    _read_palette_bases_from_vs_cb1,
    parse_vs_cb1_first_constants,
)
from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object import MigotoObject
from ._efmi_core.migoto_io.migoto_model.frame_model.resources import ConstantBuffer, Resource

from .vgmap_signature import (
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
    """Return per-component local-to-global vertex-group maps.

    CPU-posed and unweighted/static components intentionally receive empty
    maps. Merged import/export will reject them later, which matches Velo's
    rule that CPU-posed components are texture/INI-only.
    """
    adapters = [_component_signature_adapter(component) for component in migoto_object.components]
    signature_to_canonical: dict[bytes, int] = {}
    next_global_id = 0
    signature_records: list[_BoneSignatureRecord] = []

    for component_id, (component, adapter) in enumerate(zip(migoto_object.components, adapters)):
        metadata = component.metadata
        if metadata is not None and getattr(metadata, "cpu_posed", False):
            log.info("Skipping Velo VG sidecar for CPU-posed Component_%s of %s", component_id, migoto_object.id)
            continue
        if not adapter.vs_t0_path or not adapter.vs_cb1_path:
            log.info(
                "Skipping Velo VG sidecar for Component_%s of %s: missing vs-t0 or vs-cb1",
                component_id,
                migoto_object.id,
            )
            continue
        if adapter.bone_count <= 0:
            continue

        first_constant = _fallback_first_constant(adapter)
        if first_constant is None:
            log.warning(
                "No vs-cb1 first_constant for Component_%s of %s; sidecar map skipped",
                component_id,
                migoto_object.id,
            )
            continue
        adapter.vs_cb1_first_constant = int(first_constant)

        try:
            current_base, previous_base = _read_palette_bases_from_vs_cb1(
                adapter.vs_cb1_path,
                adapter.vs_cb1_first_constant,
            )
            with open(adapter.vs_t0_path, "rb") as fh:
                vs_t0_blob = fh.read()
        except Exception as exc:
            log.warning(
                "Failed to read Velo VG signature buffers for Component_%s of %s: %s",
                component_id,
                migoto_object.id,
                exc,
            )
            continue

        total_rows = len(vs_t0_blob) // 16
        if total_rows <= 0:
            continue

        for local_bone in range(adapter.bone_count):
            try:
                signature = _build_bone_signature_from_blob(
                    vs_t0_blob=vs_t0_blob,
                    total_rows=total_rows,
                    current_base=current_base,
                    previous_base=previous_base,
                    local_bone=local_bone,
                )
            except Exception as exc:
                log.warning(
                    "Skipping Velo VG signature bone %s for Component_%s of %s: %s",
                    local_bone,
                    component_id,
                    migoto_object.id,
                    exc,
                )
                continue
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
        log.info("Applied %s Velo near-signature VG aliases for %s", aliases_applied, migoto_object.id)

    return [dict(adapter.vg_map) for adapter in adapters]


def build_sidecar(migoto_object: MigotoObject) -> vgmap.VertexGroupMap:
    if migoto_object.metadata is None:
        migoto_object.build_metadata()
    component_maps = build_component_maps(migoto_object)
    return vgmap.build_from_metadata_and_maps(migoto_object.metadata, component_maps)


def write_sidecar(migoto_object: MigotoObject, output_root: Path) -> Path | None:
    sidecar = build_sidecar(migoto_object)
    if not any(component.vg_map for component in sidecar.components):
        log.warning("%s: no Velo unified vertex-group maps were produced", migoto_object.id)
        return None
    return vgmap.write_map(Path(output_root) / migoto_object.id, sidecar)
