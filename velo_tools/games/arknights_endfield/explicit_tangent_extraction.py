"""Recover explicit tangent layouts and export their handedness."""

import numpy

from ._efmi_core.migoto_io.data_model.byte_buffer import AbstractSemantic, Semantic
from ._efmi_core.migoto_io.data_model.dxgi_format import DXGIFormat
from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object_builder import MigotoObjectBuilder
from ._efmi_core.data_models.data_model_efmi import DataModelEFMI


_ORIGINAL_COLLECT_VERTEX_DATA = None
_ORIGINAL_BUILD_BUFFERS = None


def _call_has_explicit_tangent(builder, shader_call) -> bool:
    for resource_slot, resource in shader_call.resources.vertex_buffers.items():
        if resource_slot.slot_id not in builder.vb_data_import_slots or not resource.data_descriptor:
            continue
        resource.load_format(from_file=True)
        for semantic in resource.migoto_format.vb_layout.get_elements_in_slot(resource_slot.slot_id):
            if (
                semantic.abstract == AbstractSemantic(Semantic.Tangent, 0)
                and semantic.format == DXGIFormat.R32G32B32A32_FLOAT
            ):
                return True
    return False


def _collect_vertex_data(self, raw_component):
    original_calls = raw_component.shader_calls
    tangent_calls = [call for call in original_calls if _call_has_explicit_tangent(self, call)]
    if not tangent_calls or tangent_calls[0] is original_calls[0]:
        return _ORIGINAL_COLLECT_VERTEX_DATA(self, raw_component)

    first = tangent_calls[0]
    raw_component.shader_calls = [first] + [call for call in original_calls if call is not first]
    try:
        return _ORIGINAL_COLLECT_VERTEX_DATA(self, raw_component)
    finally:
        raw_component.shader_calls = original_calls


def _build_buffers(self, context, index_data, vertex_buffer, excluded_buffers, buffers_format):
    tangent = AbstractSemantic(Semantic.Tangent, 0)
    bitangent_sign = vertex_buffer.get_field(AbstractSemantic(Semantic.BitangentSign, 1))
    has_float4_tangent = any(
        semantic.abstract == tangent
        and semantic.format == DXGIFormat.R32G32B32A32_FLOAT
        for layout in buffers_format.values()
        for semantic in layout.semantics
    )
    if not has_float4_tangent or bitangent_sign is None:
        return _ORIGINAL_BUILD_BUFFERS(
            self, context, index_data, vertex_buffer, excluded_buffers, buffers_format
        )

    original_encoders = self.format_encoders
    patched_encoders = dict(original_encoders)

    def append_bitangent_sign(data):
        result = numpy.empty((len(data), 4), dtype=data.dtype)
        result[:, :3] = data[:, :3]
        result[:, 3] = numpy.asarray(bitangent_sign).reshape(-1)
        return result

    patched_encoders[tangent] = [append_bitangent_sign]
    self.format_encoders = patched_encoders
    try:
        return _ORIGINAL_BUILD_BUFFERS(
            self, context, index_data, vertex_buffer, excluded_buffers, buffers_format
        )
    finally:
        self.format_encoders = original_encoders


def install_patch() -> None:
    global _ORIGINAL_COLLECT_VERTEX_DATA, _ORIGINAL_BUILD_BUFFERS
    if _ORIGINAL_COLLECT_VERTEX_DATA is not None:
        return
    _ORIGINAL_COLLECT_VERTEX_DATA = MigotoObjectBuilder.collect_vertex_data
    _ORIGINAL_BUILD_BUFFERS = DataModelEFMI.build_buffers
    MigotoObjectBuilder.collect_vertex_data = _collect_vertex_data
    DataModelEFMI.build_buffers = _build_buffers


def remove_patch() -> None:
    global _ORIGINAL_COLLECT_VERTEX_DATA, _ORIGINAL_BUILD_BUFFERS
    if _ORIGINAL_COLLECT_VERTEX_DATA is None:
        return
    MigotoObjectBuilder.collect_vertex_data = _ORIGINAL_COLLECT_VERTEX_DATA
    DataModelEFMI.build_buffers = _ORIGINAL_BUILD_BUFFERS
    _ORIGINAL_COLLECT_VERTEX_DATA = None
    _ORIGINAL_BUILD_BUFFERS = None
