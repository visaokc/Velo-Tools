# WWMI data adapter for the LOD matcher.
#
# Both the full object (extracted folder on disk) and the LOD candidates
# (in-memory extraction result) are normalized into LodObject/LodComponent/
# LodMesh, parsed through the same _wwmi_core path the stock importer uses
# (MigotoFmt + NumpyBuffer), so content hashes are comparable across both
# sources. _wwmi_core is only called, never modified.

import io
import json
import hashlib
import re

import numpy

from dataclasses import dataclass, field
from pathlib import Path

from ..._wwmi_core.migoto_io.data_model.byte_buffer import MigotoFormat, NumpyBuffer, Semantic


@dataclass
class LodMesh:
    """Lazy buffer-backed mesh implementing the matcher's 4-method protocol."""
    fmt_text: str
    vb_bytes: bytes
    ib_bytes: bytes

    _vb: NumpyBuffer = field(init=False, default=None, repr=False)
    _ib: NumpyBuffer = field(init=False, default=None, repr=False)

    def _ensure_parsed(self):
        if self._vb is not None:
            return
        fmt = MigotoFormat.from_fmt_text(self.fmt_text)
        self._ib = NumpyBuffer(fmt.ib_layout)
        self._ib.import_raw_data(self.ib_bytes)
        self._vb = NumpyBuffer(fmt.vb_layout)
        # Some WWMI dumps declare blend fields as R8 while reserving eight bytes
        # between adjacent semantics. Preserve the on-disk offsets and spans.
        semantics = sorted(fmt.vb_layout.semantics, key=lambda item: item.offset)
        fields = []
        offsets = []
        for index, semantic in enumerate(semantics):
            next_offset = (
                semantics[index + 1].offset
                if index + 1 < len(semantics)
                else fmt.stride
            )
            span = next_offset - semantic.offset
            fields.append((semantic.get_name(), semantic.format.get_numpy_type(span)))
            offsets.append(semantic.offset)
        dtype = numpy.dtype({
            'names': [name for name, _ in fields],
            'formats': [field_type for _, field_type in fields],
            'offsets': offsets,
            'itemsize': fmt.stride,
        })
        self._vb.data = numpy.frombuffer(self.vb_bytes, dtype=dtype)

    def positions(self) -> numpy.ndarray:
        self._ensure_parsed()
        return self._vb.get_field(Semantic.Position)

    def triangles(self) -> numpy.ndarray:
        self._ensure_parsed()
        indices = self._ib.get_field(Semantic.Index)
        # Normalize flat per-index layouts to (T, 3) triangles.
        return indices.reshape(-1, 3)

    def blend_indices(self):
        self._ensure_parsed()
        return self._vb.get_field(Semantic.Blendindices)

    def blend_weights(self):
        self._ensure_parsed()
        return self._vb.get_field(Semantic.Blendweight)


# eq=False keeps identity hashing: the matcher uses components/objects as dict keys.
@dataclass(eq=False)
class LodComponent:
    index: int
    mesh: LodMesh
    content_hash: str  # sha1 over ib+vb bytes; WWMI has no per-component IB hash
    meta: dict         # component entry from Metadata.json (offsets, counts, vg_map)
    mesh_name: str     # mutable diagnostics label; matcher writes "Skipped ..." markers
    source_name: str = ""

    @property
    def vertex_count(self) -> int:
        return self.meta['vertex_count']


@dataclass(eq=False)
class LodObject:
    id: str            # vb0_hash (the hash mod.ini overrides; also the dump folder name)
    meta: dict         # full Metadata.json as a plain dict (never dataclass round-tripped)
    components: list


def _content_hash(ib_bytes: bytes, vb_bytes: bytes) -> str:
    digest = hashlib.sha1()
    digest.update(bytes(ib_bytes))
    digest.update(bytes(vb_bytes))
    return digest.hexdigest()


def _make_component(index: int, fmt_text: str, vb_bytes: bytes, ib_bytes: bytes, meta: dict,
                    source_name: str = "") -> LodComponent:
    return LodComponent(
        index=index,
        mesh=LodMesh(fmt_text=fmt_text, vb_bytes=vb_bytes, ib_bytes=ib_bytes),
        content_hash=_content_hash(ib_bytes, vb_bytes),
        meta=meta,
        mesh_name=source_name or f"Component {index}",
        source_name=source_name or f"Component {index}",
    )


def load_full_object(source_folder: Path) -> LodObject:
    """Loads an extracted object folder (Metadata.json + Component N.fmt/.vb/.ib)."""
    source_folder = Path(source_folder)
    metadata_path = source_folder / 'Metadata.json'
    with open(metadata_path, encoding='utf-8') as f:
        meta = json.load(f)

    components = []
    file_groups = {}
    for fmt_path in source_folder.glob('Component *.fmt'):
        match = re.fullmatch(r'Component (\d+)(?:\.(\d+))?', fmt_path.stem)
        if match:
            file_groups[fmt_path.stem] = (int(match.group(1)), int(match.group(2) or 0))
    for source_name, (index, _suffix) in sorted(file_groups.items(), key=lambda item: (item[1], item[0])):
        if index >= len(meta['components']):
            raise FileNotFoundError(f'Object source folder has no Metadata.json entry for {source_name}')
        component_meta = meta['components'][index]
        # Do not use Path.with_suffix here: it truncates the ``.001`` suffix
        # in names such as ``Component 5.001`` back to ``Component 5``.
        fmt_path = source_folder / f'{source_name}.fmt'
        vb_path = source_folder / f'{source_name}.vb'
        ib_path = source_folder / f'{source_name}.ib'
        for path in (fmt_path, vb_path, ib_path):
            if not path.is_file():
                raise FileNotFoundError(f'Object source folder is missing {path.name}!')
        components.append(_make_component(
            index=index,
            fmt_text=fmt_path.read_text(),
            vb_bytes=vb_path.read_bytes(),
            ib_bytes=ib_path.read_bytes(),
            meta=component_meta,
            source_name=source_name,
        ))

    return LodObject(
        id=source_folder.name,
        meta=meta,
        components=components,
    )


def from_output_object(vb0_hash: str, object_data) -> LodObject:
    """Wraps one in-memory extraction result (output_builder.ObjectData)."""
    meta = json.loads(object_data.metadata)

    components = []
    for index, (component_data, component_meta) in enumerate(zip(object_data.components, meta['components'])):
        components.append(_make_component(
            index=index,
            fmt_text=component_data.fmt,
            vb_bytes=component_data.vb,
            ib_bytes=component_data.ib,
            meta=component_meta,
        ))

    return LodObject(
        id=vb0_hash,
        meta=meta,
        components=components,
    )
