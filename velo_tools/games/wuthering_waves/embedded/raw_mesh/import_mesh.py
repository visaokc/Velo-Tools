"""Driver-layer import for the Velo raw-mesh tool.

Builds one Blender object per component:
  - faces from the (rebased) Component {i}.ib,
  - editable geometry from the Position element decoded out of its slot,
  - EVERY input slot's raw per-vertex bytes stored verbatim as packed int32
    POINT attributes (``velo_raw_s{slot}_{k}``), so BGRA / packed / aliased
    attributes the core decoder cannot model survive untouched for a faithful
    re-export.

No coordinate transform is applied (import and export are identity), which
guarantees the Faithful round-trip is byte-exact. The per-component source
hashes + layout are stashed on the object (``obj['velo_raw']``) so export is
self-contained. Never touches _wwmi_core or the stock importer.
"""

import json
from pathlib import Path

import bpy
import numpy

from ..._wwmi_core.blender_import.buffers import EncoderDecoder, format_size

from . import schema


class RawMeshImportError(Exception):
    pass


OBJ_KEY = 'velo_raw'


def _element_name(e: dict) -> str:
    return e['semantic'] if e['index'] == 0 else f"{e['semantic']}{e['index']}"


def _find_position(velo_comp: dict):
    """Return (slot_dict, element_dict) for the element mapped to Position."""
    name = velo_comp['position_element']
    for s in velo_comp['input_slots']:
        for e in s['elements']:
            if _element_name(e) == name:
                return s, e
    s = velo_comp['input_slots'][0]
    return s, s['elements'][0]


def _read_faces(folder: Path, i: int, ib_format: str):
    data = (folder / f'Component {i}.ib').read_bytes()
    decode = EncoderDecoder(ib_format)[1]
    idx = list(decode(data))
    return [tuple(idx[k:k + 3]) for k in range(0, len(idx), 3)]


def _decode_positions(raw: bytes, stride: int, offset: int, fmt: str, n: int):
    decode = EncoderDecoder(fmt)[1]
    fsize = format_size(fmt)
    cos = numpy.zeros((n, 3), dtype=numpy.float32)
    for v in range(n):
        base = v * stride + offset
        comps = decode(raw[base:base + fsize])
        for c in range(min(3, len(comps))):
            cos[v, c] = comps[c]
    return cos


def _store_slot_blob(mesh, slot_id: int, raw: bytes, stride: int, n: int):
    """Store a slot's raw per-vertex bytes as ceil(stride/4) int32 POINT attrs."""
    arr = numpy.frombuffer(raw, dtype=numpy.uint8).reshape(n, stride)
    nwords = (stride + 3) // 4
    padded = numpy.zeros((n, nwords * 4), dtype=numpy.uint8)
    padded[:, :stride] = arr
    words = padded.view(numpy.int32).reshape(n, nwords)
    for k in range(nwords):
        attr = mesh.attributes.new(f'velo_raw_s{slot_id}_{k}', 'INT', 'POINT')
        attr.data.foreach_set('value', numpy.ascontiguousarray(words[:, k]))


def _import_component(folder: Path, i: int, velo_comp: dict, coll):
    slots = velo_comp['input_slots']
    slot0 = next(s for s in slots if s['slot'] == 0)
    raw0 = (folder / f'Component {i} vb0.buf').read_bytes()
    if slot0['stride'] == 0 or len(raw0) % slot0['stride'] != 0:
        raise RawMeshImportError(f'Component {i}: vb0 size {len(raw0)} not a multiple of stride {slot0["stride"]}.')
    n = len(raw0) // slot0['stride']

    faces = _read_faces(folder, i, velo_comp['ib_format'])

    pos_slot, pos_elem = _find_position(velo_comp)
    raw_pos = raw0 if pos_slot['slot'] == 0 else (folder / f'Component {i} vb{pos_slot["slot"]}.buf').read_bytes()
    cos = _decode_positions(raw_pos, pos_slot['stride'], pos_elem['offset'], pos_elem['format'], n)

    name = f'{folder.name}_C{i}'
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(c) for c in cos.tolist()], [], faces)
    mesh.update()

    # Store every slot's raw bytes verbatim (incl. the position slot).
    for s in slots:
        raw = raw0 if s['slot'] == 0 else (folder / f'Component {i} vb{s["slot"]}.buf').read_bytes()
        expect = n * s['stride']
        if len(raw) != expect:
            raise RawMeshImportError(
                f'Component {i}: vb{s["slot"]} size {len(raw)} != {n}*{s["stride"]}={expect}.')
        _store_slot_blob(mesh, s['slot'], raw, s['stride'], n)

    obj = bpy.data.objects.new(name, mesh)
    obj[OBJ_KEY] = json.dumps({
        'component': velo_comp,
        'orig_vertex_count': n,
        'orig_index_count': len(faces) * 3,
        'source_folder': str(folder),
        'component_index': i,
    })
    coll.objects.link(obj)
    return obj


def import_folder(folder_path: str, context) -> dict:
    folder = Path(folder_path)
    meta_path = folder / 'Metadata.json'
    if not meta_path.is_file():
        raise RawMeshImportError('文件夹缺少 Metadata.json。')
    block = schema.get_velo_block(schema.load(meta_path))
    if block is None:
        raise RawMeshImportError('该文件夹不是本工具提取的 raw-mesh 文件夹（Metadata.json 缺 velo_raw_mesh 块）。')

    coll = bpy.data.collections.new(folder.name)
    context.scene.collection.children.link(coll)

    created = []
    for i, vc in enumerate(block['components']):
        created.append(_import_component(folder, i, vc, coll))

    return {'collection': coll.name, 'objects': len(created)}
