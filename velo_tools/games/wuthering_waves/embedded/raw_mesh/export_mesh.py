"""Driver-layer export for the raw-mesh tool.

Each imported component is re-emitted as an INDEPENDENT plain-3dmigoto override
keyed on its OWN source vb0 hash + original index range (static VFX/scene meshes
need no WWMI skinning/shapekey registration). One encode path serves both modes:

  - Faithful: topology unchanged -> per-slot bytes come straight from the stored
    blobs; only the Position element is re-encoded from the (possibly edited)
    vertex coordinates. Byte-exact for everything else (BGRA/aliased included).
  - Rebuild: topology changed -> same path, but slot blobs are best-effort
    (Blender-carried for surviving verts, zero for new ones) and Position is
    rebuilt from the mesh. Lossy for non-standard attributes, by design.

``export_mode`` AUTO picks per object; FAITHFUL refuses a topology change;
REBUILD always allows it. Never touches _wwmi_core or the stock exporter.
"""

import json
import re
import shutil
from pathlib import Path

import bpy
import numpy

from ..._wwmi_core.blender_import.buffers import EncoderDecoder, IndexBuffer, format_size

from .import_mesh import OBJ_KEY


class RawMeshExportError(Exception):
    pass


LEGACY_OBJ_KEYS = ('velo_raw',)


def _object_info_key(obj):
    for key in (OBJ_KEY, *LEGACY_OBJ_KEYS):
        if key in obj.keys():
            return key
    return None


def _element_name(e):
    return e['semantic'] if e['index'] == 0 else f"{e['semantic']}{e['index']}"


def _find_position(velo_comp):
    name = velo_comp['position_element']
    for s in velo_comp['input_slots']:
        for e in s['elements']:
            if _element_name(e) == name:
                return s, e
    s = velo_comp['input_slots'][0]
    return s, s['elements'][0]


def _read_slot_bytes(mesh, slot, stride, n) -> bytearray:
    nwords = (stride + 3) // 4
    words = numpy.zeros((n, nwords), dtype=numpy.int32)
    for k in range(nwords):
        attr = mesh.attributes.get(f'raw_mesh_s{slot}_{k}')
        if attr is None:
            attr = mesh.attributes.get(f'velo_raw_s{slot}_{k}')
        if attr is None:
            continue  # missing -> zero-fill (rebuild degradation)
        col = numpy.empty(n, dtype=numpy.int32)
        attr.data.foreach_get('value', col)
        words[:, k] = col
    flat = numpy.ascontiguousarray(words).view(numpy.uint8).reshape(n, nwords * 4)[:, :stride]
    return bytearray(numpy.ascontiguousarray(flat).tobytes())


def _overwrite_position(slot_bytes, stride, offset, fmt, cos, n):
    encode, decode = EncoderDecoder(fmt)
    fsize = format_size(fmt)
    for v in range(n):
        base = v * stride + offset
        comps = list(decode(bytes(slot_bytes[base:base + fsize])))
        co = cos[v]
        for c in range(min(3, len(comps))):
            comps[c] = co[c]
        slot_bytes[base:base + fsize] = encode(comps)


def _triangles(mesh):
    mesh.calc_loop_triangles()
    return [(lt.vertices[0], lt.vertices[1], lt.vertices[2]) for lt in mesh.loop_triangles]


def _encode_ib(tris, ib_format, n):
    fmt = ib_format
    if (n - 1) > 0xFFFF and '16' in fmt:
        fmt = 'DXGI_FORMAT_R32_UINT'
    ib = IndexBuffer(fmt)
    ib.faces = tris
    return bytes(ib.encode('ib')), fmt


def _build_component(obj, mode):
    info = json.loads(obj[_object_info_key(obj)])
    vc = info['component']
    mesh = obj.data
    n = len(mesh.vertices)

    tris = _triangles(mesh)
    index_count = len(tris) * 3
    topo_changed = (n != info['orig_vertex_count']) or (index_count != info['orig_index_count'])
    if topo_changed and mode == 'FAITHFUL':
        raise RawMeshExportError(
            f'对象「{obj.name}」拓扑已改变（顶点 {info["orig_vertex_count"]}→{n}，索引 '
            f'{info["orig_index_count"]}→{index_count}）；Faithful 模式要求拓扑不变。'
            f'改用 Rebuild 模式（非标准属性会有损）。')

    cos = [tuple(v.co) for v in mesh.vertices]
    pos_slot, pos_elem = _find_position(vc)

    slot_data = {}
    for s in vc['input_slots']:
        data = _read_slot_bytes(mesh, s['slot'], s['stride'], n)
        if len(data) != n * s['stride']:
            data = bytearray(n * s['stride'])
        if s['slot'] == pos_slot['slot']:
            _overwrite_position(data, s['stride'], pos_elem['offset'], pos_elem['format'], cos, n)
        slot_data[s['slot']] = bytes(data)

    ib_bytes, ib_fmt = _encode_ib(tris, vc['ib_format'], n)
    return {
        'vc': vc, 'slot_data': slot_data, 'ib_bytes': ib_bytes, 'ib_fmt': ib_fmt,
        'index_count': index_count, 'source_folder': info.get('source_folder', ''),
        'topo_changed': topo_changed,
    }


_TEX_RE = re.compile(r' t=([0-9a-fA-F]+)\b')


def _collect_textures(built):
    """hash -> source dds path, from each component's source extract folder."""
    out = {}
    for b in built:
        folder = Path(b['source_folder'])
        if not folder.is_dir():
            continue
        for p in folder.glob('Components-* t=*'):
            m = _TEX_RE.search(p.name)
            if m:
                out.setdefault(m.group(1).lower(), p)
    return out


def export_mod(collection, mod_output_folder: str, mode: str = 'AUTO') -> dict:
    objs = [o for o in collection.objects if _object_info_key(o) is not None]
    objs.sort(key=lambda o: json.loads(o[_object_info_key(o)]).get('component_index', 0))
    if not objs:
        raise RawMeshExportError(f'集合「{collection.name}」里没有本工具导入的 raw-mesh 对象。')

    built = [_build_component(o, mode) for o in objs]

    out = Path(mod_output_folder)
    meshes = out / 'Meshes'
    textures = out / 'Textures'
    meshes.mkdir(parents=True, exist_ok=True)
    textures.mkdir(parents=True, exist_ok=True)

    overrides = []
    resources = []
    for i, b in enumerate(built):
        vc = b['vc']
        (meshes / f'RawC{i}.ib').write_bytes(b['ib_bytes'])
        slot_lines = []
        for s in vc['input_slots']:
            slot = s['slot']
            (meshes / f'RawC{i}_vb{slot}.buf').write_bytes(b['slot_data'][slot])
            slot_lines.append(f'vb{slot} = ResourceRawC{i}_vb{slot}')
            resources.append(
                f'[ResourceRawC{i}_vb{slot}]\ntype = Buffer\nstride = {s["stride"]}\n'
                f'filename = Meshes/RawC{i}_vb{slot}.buf\n')
        resources.append(
            f'[ResourceRawC{i}_ib]\ntype = Buffer\nformat = {b["ib_fmt"]}\n'
            f'filename = Meshes/RawC{i}.ib\n')
        ov = [f'[TextureOverrideRawC{i}]',
              f'hash = {vc["source_vb0_hash"]}',
              f'match_first_index = {vc["source_start_index"]}',
              f'match_index_count = {vc["source_index_count"]}',
              'handling = skip']
        ov.extend(slot_lines)
        ov.append(f'ib = ResourceRawC{i}_ib')
        ov.append(f'drawindexed = {b["index_count"]}, 0, 0')
        overrides.append('\n'.join(ov) + '\n')

    tex_sections = []
    for ti, (h, src) in enumerate(sorted(_collect_textures(built).items())):
        name = f'RawTex{ti}{src.suffix}'
        try:
            shutil.copyfile(src, textures / name)
        except OSError:
            continue
        tex_sections.append(
            f'[ResourceRawTex{ti}]\nfilename = Textures/{name}\n\n'
            f'[TextureOverrideRawTex{ti}]\nhash = {h}\nmatch_priority = 0\n'
            f'this = ResourceRawTex{ti}\n')

    ini = ['; Raw-mesh mod (generated) - plain 3dmigoto per-component overrides',
           '; Each component overrides its own source draw (vb0 hash + index range).', '',
           '; --- Draw overrides ---', '']
    ini.extend(overrides)
    ini.append('; --- Mesh resources ---\n')
    ini.extend(resources)
    if tex_sections:
        ini.append('; --- Textures ---\n')
        ini.extend(tex_sections)
    (out / 'mod.ini').write_text('\n'.join(ini))

    return {'folder': str(out), 'components': len(built),
            'textures': len(tex_sections),
            'rebuilt': sum(1 for b in built if b['topo_changed'])}
