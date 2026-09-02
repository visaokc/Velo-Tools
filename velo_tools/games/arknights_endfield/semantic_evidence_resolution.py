"""Resolve incomplete vertex layouts from unambiguous draw evidence."""

import copy
from collections import defaultdict

import numpy

from ._efmi_core.data_models.data_model_efmi import DataModelEFMI
from ._efmi_core.migoto_io.data_model.byte_buffer import (
    AbstractSemantic,
    BufferLayout,
    NumpyBuffer,
    Semantic,
)
from ._efmi_core.migoto_io.data_model.dxgi_format import DXGIFormat
from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object_builder import (
    MigotoObjectBuilder,
    SemanticVertexData,
)


_ORIGINAL_COLLECT_VERTEX_DATA = None
_ORIGINAL_BUILD_BUFFERS = None


def _semantic_signature(semantic):
    return semantic.abstract, semantic.format.format, semantic.stride


def _layout_is_decodable(layout):
    current_offset = 0
    for semantic in sorted(layout.semantics, key=lambda item: item.offset):
        if semantic.offset != current_offset:
            return False
        current_offset += semantic.stride
    return current_offset == layout.stride


def _prepare_layout(builder, resource, slot_id):
    resource.load_format(from_file=True)
    layout = BufferLayout(
        semantics=copy.deepcopy(
            resource.migoto_format.vb_layout.get_elements_in_slot(slot_id)
        ),
        auto_offsets=False,
        auto_stride=False,
    )
    layout.stride = resource.migoto_format.stride
    layout.sort()
    layout.remove_data_views()
    if builder.semantic_remap:
        layout.remap_semantics(builder.semantic_remap)
    layout.dedupe_semantics()
    return layout


def _collect_region_evidence(builder, raw_component):
    definitions = defaultdict(dict)
    observed_layouts = []

    for shader_call in raw_component.shader_calls:
        for resource_slot, resource in shader_call.resources.vertex_buffers.items():
            slot_id = resource_slot.slot_id
            if slot_id not in builder.vb_data_import_slots or not resource.data_descriptor:
                continue

            source_resource = copy.copy(resource)
            source_resource.buffer = None
            source_resource.views = {}
            layout = _prepare_layout(builder, source_resource, slot_id)
            observed_layouts.append((resource.hash, slot_id, layout))
            for semantic in layout.get_elements_in_slot(slot_id):
                if semantic.abstract.enum == Semantic.Unknown:
                    continue
                key = resource.hash, slot_id, semantic.offset
                signature = _semantic_signature(semantic)
                candidate = {
                    "semantic": copy.deepcopy(semantic),
                    "layout": copy.deepcopy(layout),
                    "resource": source_resource,
                    "shader_call": shader_call,
                }
                current = definitions[key].get(signature)
                if current is None or (
                    not _layout_is_decodable(current["layout"])
                    and _layout_is_decodable(layout)
                ):
                    definitions[key][signature] = candidate

    evidence = []
    for key, candidates in definitions.items():
        if len(candidates) != 1:
            continue
        candidate = next(iter(candidates.values()))
        semantic = candidate["semantic"]
        resource_hash, slot_id, offset = key
        end = offset + semantic.stride
        has_unknown_observation = any(
            observed_hash == resource_hash
            and observed_slot == slot_id
            and end <= layout.stride
            and not any(
                _ranges_overlap(
                    offset,
                    end,
                    existing.offset,
                    existing.offset + existing.stride,
                )
                for existing in layout.get_elements_in_slot(slot_id)
            )
            for observed_hash, observed_slot, layout in observed_layouts
        )
        if not has_unknown_observation:
            continue
        if not _layout_is_decodable(candidate["layout"]):
            continue

        evidence.append(candidate)

    return evidence


def _ranges_overlap(start_a, end_a, start_b, end_b):
    return start_a < end_b and start_b < end_a


def _isolate_semantic_data(candidate, raw_component):
    semantic = candidate["semantic"]
    resource = candidate["resource"]
    migoto_format = copy.deepcopy(resource.migoto_format)
    migoto_format.vb_layout = candidate["layout"]
    if migoto_format.vertex_count != raw_component.vertex_count:
        migoto_format.first_vertex = raw_component.vertex_offset
        migoto_format.vertex_count = raw_component.vertex_count
    resource.build_numpy_buffer(migoto_format=migoto_format)
    data = numpy.array(resource.buffer.get_field(semantic.abstract), copy=True)

    isolated_layout = BufferLayout([semantic])
    isolated_buffer = NumpyBuffer(isolated_layout, size=len(data))
    isolated_buffer.set_field(semantic.abstract, data)
    resource.buffer = isolated_buffer


def _collect_vertex_data(self, raw_component):
    result = _ORIGINAL_COLLECT_VERTEX_DATA(self, raw_component)
    evidence = _collect_region_evidence(self, raw_component)
    known_semantics = {item.semantic.abstract for item in result}

    for candidate in sorted(
        evidence,
        key=lambda item: (item["semantic"].input_slot, item["semantic"].offset),
    ):
        semantic = candidate["semantic"]
        if semantic.abstract in known_semantics:
            continue

        try:
            _isolate_semantic_data(candidate, raw_component)
        except Exception as error:
            if self.verbose_logging:
                print(
                    f"Skipped recovery of {semantic}: unable to isolate source data ({error})"
                )
            continue
        recovered = SemanticVertexData(
            semantic=semantic,
            layout=candidate["layout"],
            resource=candidate["resource"],
            shader_call=candidate["shader_call"],
        )
        sort_key = semantic.input_slot, semantic.offset
        insert_at = next(
            (
                index
                for index, item in enumerate(result)
                if (item.semantic.input_slot, item.semantic.offset) > sort_key
            ),
            len(result),
        )
        result.insert(insert_at, recovered)
        known_semantics.add(semantic.abstract)

    return result


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
