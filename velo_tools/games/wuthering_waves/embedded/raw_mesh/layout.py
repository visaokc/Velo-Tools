"""Pure per-slot input-layout parsing for the Velo raw-mesh tool.

Each vbN .txt header lists the FULL IA element list (every element tagged with
its InputSlot), while its ``stride:`` is that slot's own stride and its data is
that slot's bytes only. We therefore read the element list once and the stride
from every present slot.

Picks the element that maps to Position (so the mesh shows geometry on import),
and exposes per-slot strides/elements so the raw vertex bytes round-trip
faithfully (incl. BGRA / packed / aliased elements the core decoder cannot
model). No bpy import.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..._wwmi_core.blender_import.buffers import (
    VertexBuffer, IndexBuffer, InputLayoutElement, format_components,
)


class RawMeshLayoutError(Exception):
    pass


@dataclass
class SlotLayout:
    slot: int
    stride: int
    elements: List[InputLayoutElement]


@dataclass
class ComponentLayout:
    slots: List[SlotLayout]                   # ordered by slot id
    full: List[InputLayoutElement]            # full IA element list, original order
    position: InputLayoutElement              # element remapped to Position on import

    def slot(self, slot_id) -> Optional[SlotLayout]:
        for s in self.slots:
            if s.slot == slot_id:
                return s
        return None

    def serialize_slots(self) -> List[dict]:
        """Per-slot layout for the Metadata velo_raw_mesh block (faithful)."""
        out = []
        for s in self.slots:
            out.append({
                'slot': s.slot,
                'stride': s.stride,
                'elements': [{
                    'semantic': e.SemanticName,
                    'index': e.SemanticIndex,
                    'format': e.Format,
                    'offset': e.AlignedByteOffset,
                } for e in s.elements],
            })
        return out


def _read_layout(txt_path) -> "object":
    with open(txt_path, 'r') as f:
        vb = VertexBuffer(f, load_vertices=False)
    return vb.layout  # InputLayout: .stride (this slot), .elems (full IA)


def parse_layout(vb_paths: Dict[int, str], position_override: Optional[str] = None) -> ComponentLayout:
    if 0 not in vb_paths:
        raise RawMeshLayoutError('Draw call has no vb0 (slot-0 vertex buffer).')

    # Full IA element list is identical across slot files; take it from slot 0.
    full_elements = list(_read_layout(Path(vb_paths[0]).with_suffix('.txt')).elems.values())

    slots = []
    for slot_id in sorted(vb_paths):
        stride = _read_layout(Path(vb_paths[slot_id]).with_suffix('.txt')).stride
        slot_elems = [e for e in full_elements if e.InputSlot == slot_id]
        slots.append(SlotLayout(slot=slot_id, stride=stride, elements=slot_elems))

    position = _pick_position(full_elements, position_override)
    return ComponentLayout(slots=slots, full=full_elements, position=position)


def _pick_position(elements, override) -> InputLayoutElement:
    if override:
        ov = override.strip().lower()
        for e in elements:
            if e.name.lower() == ov:
                return e
        raise RawMeshLayoutError(
            f'Position override "{override}" matches no element '
            f'(available: {", ".join(e.name for e in elements)}).')
    # Preferred: slot 0, offset 0, float with >= 3 components (the usual position).
    for e in elements:
        if (e.InputSlot == 0 and e.AlignedByteOffset == 0
                and e.is_float() and format_components(e.Format) >= 3):
            return e
    # Fallback: any float with >= 3 components.
    for e in elements:
        if e.is_float() and format_components(e.Format) >= 3:
            return e
    raise RawMeshLayoutError(
        'Could not identify a Position element (no 3+ component float attribute); '
        'specify the position attribute manually.')


def read_ib(ib_buf_path: str):
    """Read a draw call's index buffer from its .txt sibling.

    Returns (faces, ib_format, vertex_offset, vertex_count). vertex_offset /
    count come from the referenced index range (min/max), mirroring the stock
    character extractor.
    """
    txt = Path(ib_buf_path).with_suffix('.txt')
    with open(txt, 'r') as f:
        ib = IndexBuffer(f)
    if not ib.faces:
        raise RawMeshLayoutError(f'Index buffer {txt.name} has no faces.')
    flat = [i for face in ib.faces for i in face]
    vertex_offset = min(flat)
    vertex_count = max(flat) - vertex_offset + 1
    return ib.faces, ib.format, vertex_offset, vertex_count


def build_fmt(layout: ComponentLayout, ib_format: str) -> str:
    """Human-readable .fmt for familiarity. The authoritative, faithful layout
    lives in Metadata.json's velo_raw_mesh block; our importer reads that, not
    this file (the stock importer cannot parse a multi-slot layout)."""
    lines = ['; Raw-mesh extract - multi-slot layout',
             '; authoritative per-slot layout is in Metadata.json (velo_raw_mesh)']
    for s in layout.slots:
        lines.append(f'; slot {s.slot} stride: {s.stride}')
    p = layout.position
    lines.append(f'; position element (imported as POSITION): {p.name} '
                 f'slot {p.InputSlot} offset {p.AlignedByteOffset} {p.Format}')
    lines.append(f'format: {ib_format}')
    lines.append('topology: trianglelist')
    for i, e in enumerate(layout.full):
        lines.append(f'element[{i}]:')
        lines.append(e.to_string().rstrip('\n'))
    return '\n'.join(lines) + '\n'
