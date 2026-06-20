"""Extract orchestration + self-contained consolidated-folder writer for the
Velo raw-mesh tool. Pure file I/O (no bpy), so it is headless-testable.

For each resolved component it reads the per-draw IB (.txt) and slices each
input slot's raw vertex bytes from the .buf (faithful, no decode). It writes a
consolidated folder whose components and textures follow input order, with:

    Component {i}.ib            rebased index buffer (0-based)
    Component {i}.fmt           informational layout
    Component {i} vb{slot}.buf  raw per-slot vertex bytes
    Components-{ids} t={hash}.* deduped textures (stock naming)
    Metadata.json               stock-valid + velo_raw_mesh block
    TextureUsage.json
    ShaderTextureUsage.json

Nothing here couples to the stock write_objects / slot-textures patch; it never
modifies _wwmi_core (only reuses its parsers read-only).
"""

import json
import shutil
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..._wwmi_core.migoto_io.dump_parser.dump_parser import Dump
from ..._wwmi_core.migoto_io.dump_parser.filename_parser import ShaderType
from ..._wwmi_core.blender_import.buffers import IndexBuffer

from ..slot_textures import dds_meta as _dds_meta

from . import scan
from . import layout as _layout
from . import schema


class RawMeshExtractError(Exception):
    pass


@dataclass
class _BuiltComponent:
    source: scan.ResolvedComponent
    layout: _layout.ComponentLayout
    ib_format: str
    index_bytes: bytes
    slot_bytes: Dict[int, bytes]
    fmt: str
    vertex_count: int
    index_count: int


def _shader_keys(desc):
    vs = next((s.raw for s in desc.shaders if s.type is ShaderType.Vertex), 'vs=?')
    ps = next((s.raw for s in desc.shaders if s.type is ShaderType.Pixel), 'ps=?')
    return vs, ps


def _texture_record(desc, filename='') -> OrderedDict:
    meta = _dds_meta.read_dds_meta(desc.path)
    return OrderedDict((
        ('filename', filename),
        ('hash', desc.hash),
        ('format', meta.format if meta else ''),
        ('width', meta.width if meta else 0),
        ('height', meta.height if meta else 0),
    ))


def _build_component(comp: scan.ResolvedComponent, position_override: str) -> _BuiltComponent:
    faces, ib_format, vertex_offset, vertex_count = _layout.read_ib(comp.ib_path)
    clayout = _layout.parse_layout(comp.vb_paths, position_override or None)
    base = comp.base_vertex

    slot_bytes: Dict[int, bytes] = {}
    for s in clayout.slots:
        raw = Path(comp.vb_paths[s.slot]).read_bytes()
        start = (vertex_offset + base) * s.stride
        end = start + vertex_count * s.stride
        if end > len(raw):
            raise RawMeshExtractError(
                f'Call {comp.call_id} vb{s.slot}: needs bytes [{start}:{end}] but the '
                f'dumped buffer is only {len(raw)} bytes (stride {s.stride}, '
                f'{vertex_count} verts at offset {vertex_offset + base}).')
        slot_bytes[s.slot] = raw[start:end]

    rebased = [tuple(i - vertex_offset for i in face) for face in faces]
    ib_out = IndexBuffer(ib_format)
    ib_out.faces = rebased
    index_bytes = bytes(ib_out.encode(comp.ib_hash))

    return _BuiltComponent(
        source=comp, layout=clayout, ib_format=ib_format,
        index_bytes=index_bytes, slot_bytes=slot_bytes,
        fmt=_layout.build_fmt(clayout, ib_format),
        vertex_count=vertex_count, index_count=len(faces) * 3,
    )


def _clean_existing(folder: Path):
    """Remove only our own product files, so re-extract does not leave stale
    components (never touches author edits with other names)."""
    for pat in ('Component *.ib', 'Component *.fmt', 'Component * vb*.buf',
                'Components-* t=*', 'Metadata.json', 'TextureUsage.json',
                'ShaderTextureUsage.json'):
        for p in folder.glob(pat):
            try:
                p.unlink()
            except OSError:
                pass


def _write_textures(folder: Path, built: List[_BuiltComponent],
                    skip_jpg: bool, min_size: int):
    tex_files: Dict[str, dict] = {}
    record_cache: Dict[str, OrderedDict] = {}
    texture_usage = OrderedDict()
    shader_usage = OrderedDict()

    for i, bc in enumerate(built):
        cname = f'Component {i}'
        tu = OrderedDict()
        su = OrderedDict()
        for desc in bc.source.textures:
            if skip_jpg and desc.ext == 'jpg':
                continue
            if min_size and Path(desc.path).stat().st_size < min_size:
                continue
            h = desc.hash
            slot = desc.get_slot()
            vs, ps = _shader_keys(desc)
            shaders = '-'.join(s.raw for s in desc.shaders)
            tu.setdefault(slot, []).append(f'{h}-{shaders}')
            if h not in record_cache:
                record_cache[h] = _texture_record(desc)
            su.setdefault(vs, OrderedDict()).setdefault(ps, OrderedDict())[slot] = record_cache[h]
            entry = tex_files.setdefault(
                h, {'path': desc.path, 'suffix': Path(desc.path).suffix, 'comp_ids': set()})
            entry['comp_ids'].add(str(i))
        texture_usage[cname] = OrderedDict(sorted(tu.items()))
        shader_usage[cname] = su

    for h, info in tex_files.items():
        ids = '-'.join(sorted(info['comp_ids'], key=int))
        name = f'Components-{ids} t={h}{info["suffix"]}'
        try:
            shutil.copyfile(info['path'], folder / name)
        except OSError:
            pass
        if h in record_cache:
            record_cache[h]['filename'] = name

    (folder / 'TextureUsage.json').write_text(json.dumps(texture_usage, indent=4))
    (folder / 'ShaderTextureUsage.json').write_text(json.dumps(shader_usage, indent=4))
    return len(tex_files)


def extract(dump_folder: str, output_folder: str, hashes_text: str,
            position_override: str = '', folder_name: Optional[str] = None,
            skip_jpg: bool = False, skip_small: bool = False,
            skip_small_kb: int = 256) -> dict:
    """Extract VFX/scene meshes by hash into one consolidated folder.

    Returns a summary dict {folder, components, textures}.
    """
    dump_path = Path(dump_folder)
    if not (dump_path / 'log.txt').is_file():
        raise RawMeshExtractError('Selected dump folder has no log.txt (not a frame dump).')

    dump = Dump(dump_directory=dump_path)
    index = scan.build_index(dump)
    components = scan.resolve_hashes(index, hashes_text)

    built = [_build_component(c, position_override) for c in components]

    if not folder_name:
        folder_name = f'RawMesh_{built[0].source.vb0_hash}'
    folder = Path(output_folder) / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    _clean_existing(folder)

    for i, bc in enumerate(built):
        (folder / f'Component {i}.ib').write_bytes(bc.index_bytes)
        (folder / f'Component {i}.fmt').write_text(bc.fmt)
        for slot, data in bc.slot_bytes.items():
            (folder / f'Component {i} vb{slot}.buf').write_bytes(data)

    min_size = skip_small_kb * 1024 if skip_small else 0
    n_textures = _write_textures(folder, built, skip_jpg, min_size)

    components_meta = [{
        'vertex_count': bc.vertex_count,
        'index_count': bc.index_count,
        'source_vb0_hash': bc.source.vb0_hash,
        'source_ib_hash': bc.source.ib_hash,
        'source_call_id': bc.source.call_id,
        'source_start_index': bc.source.start_index,
        'source_index_count': bc.source.index_count,
        'source_base_vertex': bc.source.base_vertex,
        'position_element': bc.layout.position.name,
        'ib_format': bc.ib_format,
        'input_slots': bc.layout.serialize_slots(),
    } for bc in built]
    (folder / 'Metadata.json').write_text(schema.build_metadata_json(components_meta))

    return {'folder': str(folder), 'components': len(built), 'textures': n_textures}
