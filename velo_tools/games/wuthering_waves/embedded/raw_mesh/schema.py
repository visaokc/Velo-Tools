"""Metadata for raw-mesh authoring: a stock-valid ExtractedObject JSON plus
an additive ``raw_mesh_layout`` block carrying the per-component
source hashes and the faithful per-slot layout (the reverse map export needs).

The stock fields are filled with safe values (single vb0, cb4='', empty
shapekeys, per-component vg_map={}/vg_count=0) so the stock ``read_metadata``
parses the file without choking; the raw-mesh importer reads the extra block.
No bpy import.
"""

import json
from typing import List, Optional

from ..._wwmi_core.extract_frame_data.metadata_format import (
    ExtractedObject, ExtractedObjectComponent, ExtractedObjectShapeKeys,
)

RAW_MESH_KEY = 'raw_mesh_layout'
LEGACY_RAW_MESH_KEYS = ('velo_raw_mesh',)
VERSION = 1


def build_metadata_json(components_meta: List[dict]) -> str:
    """components_meta: per component, in output order, each a dict with:
        vertex_count, index_count, source_vb0_hash, source_ib_hash,
        source_call_id, position_element, ib_format, input_slots
    """
    stock_components = []
    raw_components = []
    total_v = 0
    total_i = 0
    for cm in components_meta:
        stock_components.append(ExtractedObjectComponent(
            vertex_offset=total_v, vertex_count=cm['vertex_count'],
            index_offset=total_i, index_count=cm['index_count'],
            vg_offset=0, vg_count=0, vg_map={},
        ))
        total_v += cm['vertex_count']
        total_i += cm['index_count']
        raw_components.append({
            'source_vb0_hash': cm['source_vb0_hash'],
            'source_ib_hash': cm['source_ib_hash'],
            'source_call_id': cm['source_call_id'],
            # Original draw range (the in-game draw the export override must match).
            'source_start_index': cm['source_start_index'],
            'source_index_count': cm['source_index_count'],
            'source_base_vertex': cm['source_base_vertex'],
            'position_element': cm['position_element'],
            'ib_format': cm['ib_format'],
            'input_slots': cm['input_slots'],
        })

    obj = ExtractedObject(
        vb0_hash=(components_meta[0]['source_vb0_hash'] if components_meta else ''),
        cb4_hash='',
        vertex_count=total_v,
        index_count=total_i,
        components=stock_components,
        shapekeys=ExtractedObjectShapeKeys(),
        export_format={},
    )
    data = json.loads(obj.as_json())
    data[RAW_MESH_KEY] = {'version': VERSION, 'components': raw_components}
    return json.dumps(data, indent=4)


def load(metadata_path) -> dict:
    with open(metadata_path, encoding='utf-8') as f:
        return json.load(f)


def get_raw_mesh_block(metadata: dict) -> Optional[dict]:
    """Return raw-mesh layout metadata, including legacy input compatibility."""
    for key in (RAW_MESH_KEY, *LEGACY_RAW_MESH_KEYS):
        block = metadata.get(key)
        if isinstance(block, dict) and isinstance(block.get('components'), list):
            return block
    return None
