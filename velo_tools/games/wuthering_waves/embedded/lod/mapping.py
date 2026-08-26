"""Canonical WWMI LOD bone mapping.

The exported vertex Blend buffer keeps the full-detail canonical indices for
every LOD.  Each LOD record supplies only a source lookup from canonical
component-local bones to the native LOD palette used by the calling draw.
"""

from typing import Mapping, Sequence

import numpy

from ..._wwmi_core.migoto_io.data_model.byte_buffer import (
    AbstractSemantic,
    BufferLayout,
    BufferSemantic,
    NumpyBuffer,
    Semantic,
)
from ..._wwmi_core.migoto_io.data_model.dxgi_format import DXGIFormat


from .mapping_core import (
    CanonicalLodMap,
    LodMappingError,
    build_canonical_lod_map,
)


def make_map_buffer(mapping: CanonicalLodMap) -> NumpyBuffer:
    layout = BufferLayout([
        BufferSemantic(
            AbstractSemantic(Semantic.RawData, 0),
            DXGIFormat.R32_UINT,
        ),
    ])
    result = NumpyBuffer(layout)
    result.set_data(numpy.asarray(mapping.sources, dtype=numpy.uint32))
    return result


def build_level_maps(
        level: int,
        entries: Mapping[int, Mapping],
        components: Sequence,
        *,
        merged: bool,
) -> list[CanonicalLodMap]:
    result = []
    for component_id, entry in sorted(entries.items()):
        if component_id >= len(components):
            continue
        component = components[component_id]
        if not getattr(component, "objects", None):
            continue
        result.append(build_canonical_lod_map(
            component_id,
            level,
            int(getattr(component, "vg_offset", 0)),
            int(getattr(component, "vg_count", 0)),
            entry,
            merged=merged,
        ))
    return result
