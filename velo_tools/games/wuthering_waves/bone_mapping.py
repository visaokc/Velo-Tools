"""Evidence-based WWMI global vertex-group to UE bone-name mapping."""

from __future__ import annotations

import io
import hashlib
import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy

from .embedded.lod.matcher import GeometryMatcher, GeometryMatcherConfig, ChamferMixin
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


@dataclass(frozen=True)
class UEBone:
    name: str
    parent: int
    position: tuple[float, float, float]
    rotation: tuple[float, float, float, float]


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


def load_uemodel_skeleton(path: Path) -> tuple[UEBone, ...]:
    """Read the named bone hierarchy and bind-pose transforms from a UEMODEL."""
    reader = _Reader(Path(path).read_bytes())
    if reader.read(8) != b"UEFORMAT" or reader.string() != "UEMODEL":
        raise BoneMappingError(f"{path.name}: not a UEMODEL file")
    reader.unpack("B")
    reader.string()
    if reader.unpack("?"):
        raise BoneMappingError(f"{path.name}: compressed UEMODEL is not supported")
    root_chunks = {name: payload for name, _count, payload in _data_chunks(reader.read(reader.remaining()))}
    skeleton = root_chunks.get("SKELETON")
    if skeleton is None:
        raise BoneMappingError(f"{path.name}: SKELETON data is missing")
    skeleton_chunks = {name: payload for name, _count, payload in _data_chunks(skeleton)}
    bone_data = skeleton_chunks.get("BONES")
    if bone_data is None:
        raise BoneMappingError(f"{path.name}: BONES chunk is missing")
    bone_reader = _Reader(bone_data)
    # Re-read the chunk header to retain its element count.
    skeleton_reader = _Reader(skeleton)
    bone_count = None
    while skeleton_reader.remaining():
        name = skeleton_reader.string()
        count = skeleton_reader.unpack("i")
        size = skeleton_reader.unpack("i")
        payload = skeleton_reader.read(size)
        if name == "BONES":
            bone_count = count
            break
    if bone_count is None:
        raise BoneMappingError(f"{path.name}: BONES chunk is missing")
    bones = []
    for _ in range(bone_count):
        name = bone_reader.string()
        parent = bone_reader.unpack("i")
        position = tuple(float(value) for value in bone_reader.unpack("3f"))
        rotation = tuple(float(value) for value in bone_reader.unpack("4f"))
        bones.append(UEBone(name, parent, position, rotation))
    if bone_reader.remaining():
        raise BoneMappingError(f"{path.name}: malformed BONES chunk")
    return tuple(bones)


def _global_id(component_meta: dict, local_id: int) -> int:
    vg_map = component_meta.get("vg_map") or {}
    value = vg_map.get(str(local_id), vg_map.get(local_id))
    if value is None:
        raise BoneMappingError(f"Metadata.json has no global vg_map entry for local VG {local_id}")
    return int(value)


def _group_clouds(mesh, max_points=128):
    positions = mesh.positions()
    indices = mesh.blend_indices().astype(numpy.int32, copy=False)
    weights = mesh.blend_weights()
    if weights is None:
        weights = numpy.zeros(indices.shape, dtype=numpy.float32)
        weights[:, 0] = 1.0
    active = weights > 0
    group_ids = sorted(int(value) for value in numpy.unique(indices[active]))
    clouds = {}
    for group_id in group_ids:
        points = positions[numpy.any((indices == group_id) & active, axis=1)].astype(numpy.float32)
        if len(points) > max_points:
            keep = numpy.linspace(0, len(points) - 1, max_points, dtype=numpy.int64)
            points = points[keep]
        clouds[group_id] = points
    return clouds


def _match_vertex_groups_unique_with_cost(component_mesh, source_mesh, candidates_count=6):
    """Map local VGs to source bones and return their mean point-cloud error."""
    target_clouds = _group_clouds(component_mesh)
    source_clouds = _group_clouds(source_mesh)
    if len(target_clouds) > len(source_clouds):
        raise BoneMappingError(
            f"Component has {len(target_clouds)} weighted VGs but source section has only "
            f"{len(source_clouds)} weighted bones")
    source_ids = list(source_clouds)
    source_centroids = numpy.array([source_clouds[group_id].mean(axis=0) for group_id in source_ids])
    edge_cache = {}

    def edge(target_id, source_index):
        key = (target_id, source_index)
        if key not in edge_cache:
            edge_cache[key] = ChamferMixin.calculate_linear_chamfer_distance(
                target_clouds[target_id], source_clouds[source_ids[source_index]])
        return edge_cache[key]

    candidate_width = min(max(1, candidates_count), len(source_ids))
    while True:
        edges = []
        for target_id, points in target_clouds.items():
            centroid = points.mean(axis=0)
            order = numpy.argsort(numpy.linalg.norm(source_centroids - centroid, axis=1))[:candidate_width]
            edges.extend((edge(target_id, int(index)), target_id, int(index)) for index in order)
        matched_targets = set()
        matched_sources = set()
        mapping = {}
        matched_costs = []
        for cost, target_id, source_index in sorted(edges):
            if target_id in matched_targets or source_index in matched_sources:
                continue
            mapping[target_id] = source_ids[source_index]
            matched_costs.append(cost)
            matched_targets.add(target_id)
            matched_sources.add(source_index)
        if len(mapping) == len(target_clouds):
            return dict(sorted(mapping.items())), float(numpy.mean(matched_costs))
        if candidate_width == len(source_ids):
            missing = sorted(set(target_clouds) - set(mapping))
            raise BoneMappingError(f"无法为 Component local VG 建立一对一骨骼匹配：{missing[:8]}")
        candidate_width = min(len(source_ids), candidate_width * 2)


def _match_vertex_groups_unique(component_mesh, source_mesh, candidates_count=6):
    """Map Component-local VGs to source bones with a one-to-one assignment."""
    mapping, _cost = _match_vertex_groups_unique_with_cost(
        component_mesh, source_mesh, candidates_count)
    return mapping

def mapping_from_assignments(assignments, *, vg_candidates=6):
    """Merge repeated global-id/name occurrences while retaining source Components."""
    rows = []
    evidence = []
    for component, model, score in assignments:
        local_to_source = _match_vertex_groups_unique(component.mesh, model, vg_candidates)
        support = {group_id: len(points) for group_id, points in _group_clouds(component.mesh).items()}
        for local_id, source_id in local_to_source.items():
            if source_id >= len(model.bone_names):
                raise BoneMappingError(f"{model.label}: bone index {source_id} is out of range")
            global_id = _global_id(component.meta, local_id)
            bone_name = model.bone_names[source_id]
            rows.append((global_id, bone_name, component.source_name, support.get(local_id, 0)))
        evidence.append((component.source_name, model.label, score, len(local_to_source)))
    if not rows:
        raise BoneMappingError("体素匹配完成，但没有得到任何骨骼编号映射")
    merged = {}
    for global_id, bone_name, component_name, support in rows:
        key = (global_id, bone_name)
        current = merged.setdefault(key, [set(), 0])
        current[0].add(component_name)
        current[1] += support
    result = [
        (global_id, bone_name, _format_component_sources(component_names), support)
        for (global_id, bone_name), (component_names, support) in merged.items()
    ]
    return sorted(result, key=lambda item: (item[0], item[2], item[1])), evidence


def _format_component_sources(names):
    """Format integer Component sources as ranges, retaining dotted suffixes."""
    values = []
    for name in names:
        match = re.search(r'(?:Component\s+|C)(\d+)(\.\d+)?$', name)
        if not match:
            values.append((None, name))
        elif match.group(2):
            values.append((None, f"C{match.group(1)}{match.group(2)}"))
        else:
            values.append((int(match.group(1)), None))
    integer_ids = sorted(value for value, _ in values if value is not None)
    tokens = [value for _, value in values if value is not None]
    index = 0
    while index < len(integer_ids):
        start = end = integer_ids[index]
        while index + 1 < len(integer_ids) and integer_ids[index + 1] == end + 1:
            index += 1
            end = integer_ids[index]
        tokens.append(f"C{start}" if start == end else f"C{start}-C{end}")
        index += 1
    return ",".join(sorted(tokens, key=lambda token: (int(re.search(r'\d+', token).group()), token)))


def generate_mapping(unpack_folder: Path, object_source_folder: Path, *, voxel_size=0.05,
                     similarity_threshold=55.0, vg_candidates=6):
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
    unique_sections = []
    seen_signatures = set()
    for section in sections:
        points = geometry.voxel_sample_mesh(section, voxel_size=voxel_size)
        signature = hashlib.sha1(
            points.tobytes()
            + section.blend_indices().tobytes()
            + section.blend_weights().tobytes()
        ).digest()
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        unique_sections.append(section)
    sections = unique_sections
    assignments = []
    for component in full_object.components:
        scores = sorted(
            ((geometry.calculate_similarity(section, component.mesh), index, section)
             for index, section in enumerate(sections)),
            reverse=True, key=lambda item: item[0],
        )
        best_score, best_index, best_section = scores[0]
        if best_score < similarity_threshold:
            raise BoneMappingError(
                f"Component {component.index} 最佳体素匹配仅 {best_score:.2f}%（{best_section.label}）")

        viable = [item for item in scores if item[0] >= similarity_threshold]
        skin_candidates = []
        first_compatible = None
        for score, index, section in viable:
            try:
                mapping, skin_cost = _match_vertex_groups_unique_with_cost(
                    component.mesh, section, vg_candidates)
            except BoneMappingError:
                continue
            first_compatible = (skin_cost, -score, section.label, mapping, score, index, section)
            skin_candidates.append(first_compatible)
            break
        if first_compatible is not None and first_compatible[0] > 0.001:
            for score, index, section in viable:
                if index == first_compatible[5]:
                    continue
                try:
                    mapping, skin_cost = _match_vertex_groups_unique_with_cost(
                        component.mesh, section, vg_candidates)
                except BoneMappingError:
                    continue
                skin_candidates.append((skin_cost, -score, section.label, mapping, score, index, section))
        if not skin_candidates:
            raise BoneMappingError(
                f"Component {component.index} 没有骨骼通道兼容的 section 候选")
        skin_candidates.sort(key=lambda item: item[:3])
        best_cost, _negative_score, _label, best_mapping, best_score, best_index, best_section = skin_candidates[0]
        tied = [item for item in skin_candidates if abs(item[0] - best_cost) < 1e-6]
        if any(item[3] != best_mapping for item in tied[1:]):
            details = "，".join(
                f"skin={item[0]:.6f} geometry={item[4]:.2f}% {item[6].label}"
                for item in tied[:3])
            raise BoneMappingError(f"Component {component.index} 蒙皮同分候选产生不同骨骼映射：{details}")
        assignments.append((component, best_section, best_score))

    return mapping_from_assignments(assignments, vg_candidates=vg_candidates)
