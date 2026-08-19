"""Evidence-based WWMI global vertex-group to UE bone-name mapping."""

from __future__ import annotations

import io
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy

from .embedded.lod.matcher import GeometryMatcher, GeometryMatcherConfig, VertexGroupsMatcher
from .embedded.lod.model import load_full_object


class BoneMappingError(RuntimeError):
    pass


class _Reader:
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)

    def read(self, size: int) -> bytes:
        data = self.stream.read(size)
        if len(data) != size:
            raise BoneMappingError("Unexpected end of .uemodel data")
        return data

    def unpack(self, fmt: str):
        size = struct.calcsize("<" + fmt)
        values = struct.unpack("<" + fmt, self.read(size))
        return values[0] if len(values) == 1 else values

    def string(self) -> str:
        size = self.unpack("i")
        if size < 0 or size > 16 * 1024 * 1024:
            raise BoneMappingError(f"Invalid .uemodel string size: {size}")
        return self.read(size).decode("utf-8")

    def remaining(self) -> int:
        return len(self.stream.getbuffer()) - self.stream.tell()


def _data_chunks(data: bytes):
    reader = _Reader(data)
    while reader.remaining():
        name = reader.string()
        count = reader.unpack("i")
        size = reader.unpack("i")
        if count < 0 or size < 0 or size > reader.remaining():
            raise BoneMappingError(f"Invalid {name} chunk header")
        yield name, count, reader.read(size)


@dataclass
class _SkinMesh:
    label: str
    model_name: str
    _positions: numpy.ndarray
    _triangles: numpy.ndarray
    _blend_indices: numpy.ndarray
    _blend_weights: numpy.ndarray
    bone_names: tuple[str, ...]

    def positions(self):
        return self._positions

    def triangles(self):
        return self._triangles

    def blend_indices(self):
        return self._blend_indices

    def blend_weights(self):
        return self._blend_weights


def _parse_lod(data: bytes, bone_names: tuple[str, ...], model_name: str):
    chunks = {name: (count, payload) for name, count, payload in _data_chunks(data)}
    try:
        vertex_count, vertex_data = chunks["VERTICES"]
        _, index_data = chunks["INDICES"]
        weight_count, weight_data = chunks["WEIGHTS"]
    except KeyError as exc:
        raise BoneMappingError(f"{model_name}: LOD0 is missing {exc.args[0]}") from exc

    positions = numpy.frombuffer(vertex_data, dtype="<f4").reshape(vertex_count, 3).copy()
    indices = numpy.frombuffer(index_data, dtype="<u4").astype(numpy.int64, copy=False)
    influences = [[] for _ in range(vertex_count)]
    weight_reader = _Reader(weight_data)
    for _ in range(weight_count):
        bone_id, vertex_id, weight = weight_reader.unpack("Hif")
        if vertex_id < 0 or vertex_id >= vertex_count or bone_id >= len(bone_names):
            raise BoneMappingError(f"{model_name}: invalid skin influence")
        if weight > 0:
            influences[vertex_id].append((int(bone_id), float(weight)))
    if weight_reader.remaining():
        raise BoneMappingError(f"{model_name}: malformed WEIGHTS chunk")

    sections = []
    material_chunk = chunks.get("MATERIALS")
    if material_chunk is not None:
        material_count, material_data = material_chunk
        material_reader = _Reader(material_data)
        for section_id in range(material_count):
            material_name = material_reader.string()
            material_reader.string()
            first_index, face_count = material_reader.unpack("II")
            sections.append((section_id, material_name, first_index, face_count * 3))
        if material_reader.remaining():
            raise BoneMappingError(f"{model_name}: malformed MATERIALS chunk")
    else:
        sections.append((0, "", 0, len(indices)))

    meshes = []
    for section_id, material_name, first_index, index_count in sections:
        section_indices = indices[first_index:first_index + index_count]
        if len(section_indices) < 3 or len(section_indices) % 3:
            continue
        referenced = numpy.unique(section_indices)
        remap = numpy.full(vertex_count, -1, dtype=numpy.int64)
        remap[referenced] = numpy.arange(len(referenced), dtype=numpy.int64)
        triangles = remap[section_indices].reshape(-1, 3)
        section_influences = [influences[int(index)] for index in referenced]
        width = max((len(items) for items in section_influences), default=0)
        if width == 0:
            continue
        blend_indices = numpy.zeros((len(referenced), width), dtype=numpy.int32)
        blend_weights = numpy.zeros((len(referenced), width), dtype=numpy.float32)
        for vertex_id, items in enumerate(section_influences):
            for slot, (bone_id, weight) in enumerate(items):
                blend_indices[vertex_id, slot] = bone_id
                blend_weights[vertex_id, slot] = weight
        label = f"{model_name}: section {section_id}"
        if material_name:
            label += f" ({material_name})"
        meshes.append(_SkinMesh(
            label=label,
            model_name=model_name,
            _positions=positions[referenced],
            _triangles=triangles,
            _blend_indices=blend_indices,
            _blend_weights=blend_weights,
            bone_names=bone_names,
        ))
    return meshes


def load_uemodel_sections(path: Path):
    reader = _Reader(Path(path).read_bytes())
    if reader.read(8) != b"UEFORMAT":
        raise BoneMappingError(f"{path.name}: invalid UEFormat magic")
    if reader.string() != "UEMODEL":
        raise BoneMappingError(f"{path.name}: not a UEMODEL file")
    version = reader.unpack("B")
    reader.string()
    if reader.unpack("?"):
        raise BoneMappingError(f"{path.name}: compressed UEMODEL is not supported")

    root_chunks = {name: (count, payload) for name, count, payload in _data_chunks(reader.read(reader.remaining()))}
    skeleton = root_chunks.get("SKELETON")
    lods = root_chunks.get("LODS")
    if skeleton is None or lods is None:
        raise BoneMappingError(f"{path.name}: skeletal LODS/SKELETON data is missing")

    skeleton_chunks = {name: (count, payload) for name, count, payload in _data_chunks(skeleton[1])}
    try:
        bone_count, bone_data = skeleton_chunks["BONES"]
    except KeyError as exc:
        raise BoneMappingError(f"{path.name}: BONES chunk is missing") from exc
    bone_reader = _Reader(bone_data)
    bone_names = []
    for _ in range(bone_count):
        bone_names.append(bone_reader.string())
        bone_reader.read(4 + 12 + 16)
    if bone_reader.remaining():
        raise BoneMappingError(f"{path.name}: malformed BONES chunk")

    lod_reader = _Reader(lods[1])
    if lods[0] < 1:
        raise BoneMappingError(f"{path.name}: no exported LOD")
    lod_name = lod_reader.string()
    lod_size = lod_reader.unpack("i")
    if lod_name != "LOD0" or lod_size < 0 or lod_size > lod_reader.remaining():
        raise BoneMappingError(f"{path.name}: LOD0 is missing")
    return _parse_lod(lod_reader.read(lod_size), tuple(bone_names), path.name)


def _global_id(component_meta: dict, local_id: int) -> int:
    vg_map = component_meta.get("vg_map") or {}
    value = vg_map.get(str(local_id), vg_map.get(local_id))
    if value is None:
        raise BoneMappingError(f"Metadata.json has no global vg_map entry for local VG {local_id}")
    return int(value)


def mapping_from_assignments(assignments, *, vg_candidates=3):
    """Translate proven Component/model matches into global-id/name pairs."""
    vg_matcher = VertexGroupsMatcher(candidates_count=vg_candidates)
    result = {}
    evidence = []
    for component, model, score in assignments:
        source_to_local = vg_matcher.match_vertex_groups(model, component.mesh)
        for source_id, local_id in source_to_local.items():
            if source_id >= len(model.bone_names):
                raise BoneMappingError(f"{model.label}: bone index {source_id} is out of range")
            global_id = _global_id(component.meta, local_id)
            bone_name = model.bone_names[source_id]
            existing = result.get(global_id)
            if existing is not None and existing != bone_name:
                raise BoneMappingError(
                    f"全局编号 {global_id} 同时匹配到 {existing} 与 {bone_name}")
            result[global_id] = bone_name
        evidence.append((component.index, model.label, score, len(source_to_local)))
    if not result:
        raise BoneMappingError("体素匹配完成，但没有得到任何骨骼编号映射")
    return dict(sorted(result.items())), evidence


def generate_mapping(unpack_folder: Path, object_source_folder: Path, *, voxel_size=0.05,
                     similarity_threshold=55.0, ambiguity_margin=2.0, vg_candidates=3):
    unpack_folder = Path(unpack_folder)
    model_paths = sorted(unpack_folder.rglob("*.uemodel")) if unpack_folder.is_dir() else []
    if not model_paths:
        raise BoneMappingError("解包路径中没有 .uemodel 文件")
    sections = []
    failures = []
    for path in model_paths:
        try:
            parsed = load_uemodel_sections(path)
            if not parsed:
                raise BoneMappingError(f"{path.name}: no weighted material section")
            sections.extend(parsed)
        except BoneMappingError as exc:
            failures.append(str(exc))
    if failures:
        raise BoneMappingError("部分 .uemodel 无法参与匹配：" + "; ".join(failures[:3]))
    if not sections:
        raise BoneMappingError("没有可用于匹配的 skeletal LOD0 section")

    try:
        full_object = load_full_object(Path(object_source_folder))
    except Exception as exc:
        raise BoneMappingError(f"对象源目录读取失败：{exc}") from exc

    geometry = GeometryMatcher(GeometryMatcherConfig(voxel_size=voxel_size, sensitivity=0.5))
    assignments = []
    used_sections = set()
    for component in full_object.components:
        scores = sorted(
            ((geometry.calculate_similarity(section, component.mesh), index, section)
             for index, section in enumerate(sections)),
            reverse=True, key=lambda item: item[0],
        )
        best_score, best_index, best_section = scores[0]
        second_score = scores[1][0] if len(scores) > 1 else 0.0
        if best_score < similarity_threshold:
            raise BoneMappingError(
                f"Component {component.index} 最佳体素匹配仅 {best_score:.2f}%（{best_section.label}）")
        if best_score - second_score < ambiguity_margin:
            raise BoneMappingError(
                f"Component {component.index} 体素匹配有歧义：{best_score:.2f}% 与 {second_score:.2f}%")
        if best_index in used_sections:
            raise BoneMappingError(f"{best_section.label} 同时匹配多个 Component，无法建立唯一证据链")
        used_sections.add(best_index)
        assignments.append((component, best_section, best_score))

    matched_models = {section.model_name for _, section, _ in assignments}
    missing_models = [path.name for path in model_paths if path.name not in matched_models]
    if missing_models:
        raise BoneMappingError("以下 .uemodel 没有任何 section 匹配到 Component：" + ", ".join(missing_models))

    return mapping_from_assignments(assignments, vg_candidates=vg_candidates)
