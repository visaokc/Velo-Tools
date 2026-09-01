"""Build and consume Component-local Endfield bone-name mappings."""

from __future__ import annotations

import copy
import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy


MAPPING_FILE_NAME = "BoneNameMapping.json"
SKELETON_FILE_NAME = "BoneNameSkeleton.glb"
MAPPING_VERSION = 1


class NamedBoneMappingError(RuntimeError):
    pass


@dataclass
class SkinMesh:
    label: str
    bone_names: tuple[str, ...]
    _positions: numpy.ndarray
    _triangles: numpy.ndarray
    _blend_indices: numpy.ndarray
    _blend_weights: numpy.ndarray

    def positions(self):
        return self._positions

    def triangles(self):
        return self._triangles

    def blend_indices(self):
        return self._blend_indices

    def blend_weights(self):
        return self._blend_weights

    def get_data(self, semantic):
        name = str(semantic)
        if name == "POSITION":
            return self._positions
        if name == "INDEX":
            return self._triangles
        if name == "BLENDINDICES":
            return self._blend_indices
        if name in {"BLENDWEIGHT", "BLENDWEIGHTS"}:
            return self._blend_weights
        return None


@dataclass
class DumpMesh:
    _positions: numpy.ndarray
    _triangles: numpy.ndarray
    _blend_indices: numpy.ndarray
    _blend_weights: numpy.ndarray

    def positions(self):
        return self._positions

    def triangles(self):
        return self._triangles

    def blend_indices(self):
        return self._blend_indices

    def blend_weights(self):
        return self._blend_weights

    def get_data(self, semantic):
        name = str(semantic)
        if name == "POSITION":
            return self._positions
        if name == "INDEX":
            return self._triangles
        if name == "BLENDINDICES":
            return self._blend_indices
        if name in {"BLENDWEIGHT", "BLENDWEIGHTS"}:
            return self._blend_weights
        return None


@dataclass
class DumpComponent:
    index: int
    source_name: str
    meta: dict
    mesh: DumpMesh


_COMPONENT_DTYPES = {
    5120: numpy.dtype("i1"),
    5121: numpy.dtype("u1"),
    5122: numpy.dtype("<i2"),
    5123: numpy.dtype("<u2"),
    5125: numpy.dtype("<u4"),
    5126: numpy.dtype("<f4"),
}
_ACCESSOR_WIDTHS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}


def _read_glb(path: Path):
    data = Path(path).read_bytes()
    if len(data) < 12 or data[:4] != b"glTF":
        raise NamedBoneMappingError(f"{Path(path).name}: invalid GLB header")
    version, declared_size = struct.unpack_from("<II", data, 4)
    if version != 2 or declared_size > len(data):
        raise NamedBoneMappingError(f"{Path(path).name}: unsupported GLB version or size")
    document = None
    binary = None
    offset = 12
    while offset + 8 <= declared_size:
        size, chunk_type = struct.unpack_from("<I4s", data, offset)
        offset += 8
        chunk = data[offset:offset + size]
        offset += size
        if chunk_type == b"JSON":
            document = json.loads(chunk.rstrip(b" \0"))
        elif chunk_type == b"BIN\0":
            binary = chunk
    if not isinstance(document, dict) or binary is None:
        raise NamedBoneMappingError(f"{Path(path).name}: GLB JSON/BIN chunk is missing")
    return document, binary


def _accessor(document: dict, binary: bytes, accessor_id: int) -> numpy.ndarray:
    accessor = document["accessors"][accessor_id]
    if "sparse" in accessor or "bufferView" not in accessor:
        raise NamedBoneMappingError("Sparse or bufferless GLB accessors are not supported")
    view = document["bufferViews"][accessor["bufferView"]]
    try:
        dtype = _COMPONENT_DTYPES[accessor["componentType"]]
        width = _ACCESSOR_WIDTHS[accessor["type"]]
    except KeyError as exc:
        raise NamedBoneMappingError(f"Unsupported GLB accessor layout: {exc.args[0]}") from exc
    item_size = dtype.itemsize * width
    stride = int(view.get("byteStride", item_size))
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    end = start + max(0, int(accessor["count"]) - 1) * stride + item_size
    if start < 0 or end > len(binary):
        raise NamedBoneMappingError("GLB accessor exceeds BIN chunk")
    return numpy.ndarray(
        (int(accessor["count"]), width),
        dtype=dtype,
        buffer=binary,
        offset=start,
        strides=(stride, dtype.itemsize),
    ).copy()


def load_glb_lod0_meshes(path: Path) -> list[SkinMesh]:
    """Load weighted LOD0 mesh primitives and normalize them to EFMI axes."""
    document, binary = _read_glb(path)
    nodes = document.get("nodes", [])
    skins = document.get("skins", [])
    meshes = document.get("meshes", [])
    result = []
    for node in nodes:
        mesh_id = node.get("mesh")
        skin_id = node.get("skin")
        if mesh_id is None or skin_id is None:
            continue
        mesh = meshes[int(mesh_id)]
        mesh_name = str(mesh.get("name") or node.get("name") or f"Mesh {mesh_id}")
        if "lod0" not in mesh_name.lower():
            continue
        skin = skins[int(skin_id)]
        bone_names = tuple(str(nodes[int(joint)].get("name") or f"Bone_{joint}") for joint in skin["joints"])
        for primitive_id, primitive in enumerate(mesh.get("primitives", [])):
            attributes = primitive.get("attributes", {})
            required = {"POSITION", "JOINTS_0", "WEIGHTS_0"}
            if not required.issubset(attributes):
                continue
            positions = _accessor(document, binary, attributes["POSITION"]).astype(numpy.float32)
            positions = positions[:, (0, 2, 1)]
            positions[:, 0] *= -1.0
            positions[:, 1] *= -1.0
            blend_indices = _accessor(document, binary, attributes["JOINTS_0"]).astype(numpy.int32)
            blend_weights = _accessor(document, binary, attributes["WEIGHTS_0"]).astype(numpy.float32)
            if primitive.get("indices") is None:
                flat_indices = numpy.arange(len(positions), dtype=numpy.int64)
            else:
                flat_indices = _accessor(document, binary, primitive["indices"]).reshape(-1).astype(numpy.int64)
            if len(flat_indices) < 3 or len(flat_indices) % 3:
                continue
            label = mesh_name if len(mesh.get("primitives", [])) == 1 else f"{mesh_name}: primitive {primitive_id}"
            result.append(SkinMesh(
                label=label,
                bone_names=bone_names,
                _positions=positions,
                _triangles=flat_indices.reshape(-1, 3),
                _blend_indices=blend_indices,
                _blend_weights=blend_weights,
            ))
    if not result:
        raise NamedBoneMappingError(f"{Path(path).name}: no weighted LOD0 mesh primitives found")
    return result


def _load_dump_components(source_folder: Path):
    from ._efmi_core.migoto_io.data_model.byte_buffer import NumpyBuffer, Semantic
    from ._efmi_core.migoto_io.migoto_model.migoto_format import MigotoFormat

    source_folder = Path(source_folder)
    metadata_path = source_folder / "Metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NamedBoneMappingError(f"Cannot read Metadata.json: {exc}") from exc
    components = []
    for component_id, component_meta in enumerate(metadata.get("components", [])):
        name = str(component_meta.get("mesh_name") or f"Component {component_id}")
        fmt_path = source_folder / f"Component {component_id}.fmt"
        vb_path = source_folder / f"Component {component_id}.vb"
        ib_path = source_folder / f"Component {component_id}.ib"
        if not all(path.is_file() for path in (fmt_path, vb_path, ib_path)):
            raise NamedBoneMappingError(f"Object source folder is missing Component {component_id} buffers")
        fmt = MigotoFormat.from_fmt_text(fmt_path.read_text(encoding="utf-8"))
        vb = NumpyBuffer(fmt.vb_layout)
        ib = NumpyBuffer(fmt.ib_layout)
        vb.import_raw_data(vb_path.read_bytes())
        ib.import_raw_data(ib_path.read_bytes())
        positions = vb.get_field(Semantic.Position).astype(numpy.float32, copy=False)
        blend_indices = vb.get_field(Semantic.Blendindices)
        blend_weights = vb.get_field(Semantic.Blendweights)
        if blend_indices is None:
            if not bool(component_meta.get("cpu_posed", False)):
                raise NamedBoneMappingError(f"Component {component_id} has no blend indices")
            blend_indices = numpy.zeros((len(positions), 1), dtype=numpy.int32)
            blend_weights = numpy.zeros((len(positions), 1), dtype=numpy.float32)
        else:
            blend_indices = blend_indices.astype(numpy.int32, copy=False)
            if blend_weights is None:
                blend_weights = numpy.zeros(blend_indices.shape, dtype=numpy.float32)
                blend_weights[:, 0] = 1.0
            else:
                blend_weights = blend_weights.astype(numpy.float32, copy=False)
        triangles = ib.get_field(Semantic.Index).reshape(-1, 3).astype(numpy.int64, copy=False)
        components.append(DumpComponent(
            index=component_id,
            source_name=name,
            meta=component_meta,
            mesh=DumpMesh(positions, triangles, blend_indices, blend_weights),
        ))
    if not components:
        raise NamedBoneMappingError("Metadata.json contains no Components")
    return metadata, components


def _group_clouds(mesh, max_points=128):
    positions = mesh.positions()
    indices = mesh.blend_indices().astype(numpy.int32, copy=False)
    weights = mesh.blend_weights()
    active = weights > 1.0e-6
    clouds = {}
    for group_id in sorted(int(value) for value in numpy.unique(indices[active])):
        points = positions[numpy.any((indices == group_id) & active, axis=1)].astype(numpy.float32)
        if len(points) > max_points:
            keep = numpy.linspace(0, len(points) - 1, max_points, dtype=numpy.int64)
            points = points[keep]
        clouds[group_id] = points
    return clouds


def _calculate_min_distances(points_a, points_b, chunk_size=1024):
    points_a = numpy.asarray(points_a, dtype=numpy.float32)
    points_b = numpy.asarray(points_b, dtype=numpy.float32)
    max_chunk_elements = 16 * 1024 * 1024
    chunk_size = min(chunk_size, max(1, max_chunk_elements // max(1, len(points_b))))
    squared_b = numpy.einsum("ij,ij->i", points_b, points_b)
    result = []
    for start in range(0, len(points_a), chunk_size):
        chunk = points_a[start:start + chunk_size]
        squared = -2.0 * (chunk @ points_b.T)
        squared += numpy.einsum("ij,ij->i", chunk, chunk)[:, None]
        squared += squared_b[None, :]
        minimum = numpy.min(squared, axis=1)
        result.append(numpy.sqrt(numpy.maximum(minimum, 0.0)))
    return numpy.concatenate(result)


def _linear_chamfer_distance(points_a, points_b):
    return float(
        _calculate_min_distances(points_a, points_b).mean()
        + _calculate_min_distances(points_b, points_a).mean()
    )


def _match_vertex_groups(
    component_mesh,
    source_mesh,
    candidates_count=6,
    *,
    target_clouds=None,
    source_clouds=None,
):
    target_clouds = target_clouds if target_clouds is not None else _group_clouds(component_mesh)
    source_clouds = source_clouds if source_clouds is not None else _group_clouds(source_mesh)
    if len(target_clouds) > len(source_clouds):
        raise NamedBoneMappingError(
            f"Component uses {len(target_clouds)} weighted local groups but {source_mesh.label} has only "
            f"{len(source_clouds)} weighted bones"
        )
    source_ids = list(source_clouds)
    source_centroids = numpy.array([source_clouds[group_id].mean(axis=0) for group_id in source_ids])
    edge_cache = {}

    def edge(target_id, source_index):
        key = (target_id, source_index)
        if key not in edge_cache:
            edge_cache[key] = _linear_chamfer_distance(
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
        costs = []
        for cost, target_id, source_index in sorted(edges):
            if target_id in matched_targets or source_index in matched_sources:
                continue
            mapping[target_id] = source_ids[source_index]
            costs.append(cost)
            matched_targets.add(target_id)
            matched_sources.add(source_index)
        if len(mapping) == len(target_clouds):
            return dict(sorted(mapping.items())), float(numpy.mean(costs))
        if candidate_width == len(source_ids):
            missing = sorted(set(target_clouds) - set(mapping))
            raise NamedBoneMappingError(f"Cannot uniquely map local groups: {missing[:8]}")
        candidate_width = min(len(source_ids), candidate_width * 2)


def uses_asset_input(unpack_path: Path) -> bool:
    unpack_path = Path(unpack_path)
    return not (unpack_path.is_file() and unpack_path.suffix.lower() == ".glb")


def _find_glb(unpack_path: Path) -> Path:
    unpack_path = Path(unpack_path)
    if unpack_path.is_file() and unpack_path.suffix.lower() == ".glb":
        return unpack_path
    raise NamedBoneMappingError("A GLB input must be selected as the GLB file itself")


def generate_mapping(unpack_path: Path, source_folder: Path, *, voxel_size=0.01,
                     similarity_threshold=55.0, vg_candidates=6):
    """Match each dump Component to a GLB mesh and write local-to-name mappings."""
    from ._efmi_core.migoto_io.migoto_model.migoto_mesh import GeometryMatcher, GeometryMatcherConfig

    unpack_path = Path(unpack_path)
    if uses_asset_input(unpack_path):
        from .asset_model import load_asset_model

        asset_model = load_asset_model(unpack_path)
        source_path = asset_model.root
        source_meshes = [
            SkinMesh(
                label=mesh.label,
                bone_names=mesh.bone_names,
                _positions=mesh.positions,
                _triangles=mesh.triangles,
                _blend_indices=mesh.blend_indices,
                _blend_weights=mesh.blend_weights,
            )
            for mesh in asset_model.meshes
        ]
    else:
        source_path = _find_glb(unpack_path)
        source_meshes = load_glb_lod0_meshes(source_path)
    metadata, components = _load_dump_components(Path(source_folder))
    class CachedGeometryMatcher(GeometryMatcher):
        def __init__(self, cfg, point_limit=512):
            super().__init__(cfg)
            self.point_limit = point_limit
            self.point_cache = {}

        def voxel_sample_mesh(self, mesh, voxel_size=0.05):
            key = (id(mesh), float(voxel_size))
            points = self.point_cache.get(key)
            if points is None:
                points = super().voxel_sample_mesh(mesh, voxel_size=voxel_size)
                if self.point_limit and len(points) > self.point_limit:
                    voxels = numpy.rint(points / float(voxel_size)).astype(numpy.int32)
                    order = numpy.lexsort((voxels[:, 2], voxels[:, 1], voxels[:, 0]))
                    points = points[order]
                    keep = numpy.linspace(0, len(points) - 1, self.point_limit, dtype=numpy.int64)
                    points = points[keep]
                self.point_cache[key] = points
            return points

        calculate_min_distances = staticmethod(_calculate_min_distances)

    geometry_prefilter = CachedGeometryMatcher(
        GeometryMatcherConfig(voxel_size=voxel_size, sensitivity=0.5),
        point_limit=512,
    )
    geometry = CachedGeometryMatcher(
        GeometryMatcherConfig(voxel_size=voxel_size, sensitivity=0.5),
        point_limit=0,
    )
    unique_meshes = []
    seen_signatures = set()
    for source_mesh in source_meshes:
        points = geometry_prefilter.voxel_sample_mesh(source_mesh, voxel_size=voxel_size)
        signature = hashlib.sha1(
            points.tobytes()
            + source_mesh.positions().tobytes()
            + source_mesh.triangles().tobytes()
            + source_mesh.blend_indices().tobytes()
            + source_mesh.blend_weights().tobytes()
            + "\0".join(source_mesh.bone_names).encode("utf-8")
        ).digest()
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        unique_meshes.append(source_mesh)
    source_meshes = unique_meshes
    source_cloud_cache = {
        id(source_mesh): _group_clouds(source_mesh)
        for source_mesh in source_meshes
    }
    component_maps = {}
    evidence = []
    for component in components:
        if bool(component.meta.get("cpu_posed", False)):
            component_maps[component.index] = {}
            evidence.append((component.index, "CPU-posed", 100.0, 0.0, 0))
            continue
        prefilter_scores = sorted(
            ((geometry_prefilter.calculate_similarity(source_mesh, component.mesh), source_mesh.label, source_mesh)
             for source_mesh in source_meshes),
            reverse=True,
            key=lambda item: (item[0], item[1]),
        )
        shortlist = prefilter_scores[:min(3, len(prefilter_scores))]
        scores = sorted(
            ((geometry.calculate_similarity(source_mesh, component.mesh), label, source_mesh)
             for _prefilter_score, label, source_mesh in shortlist),
            reverse=True,
            key=lambda item: (item[0], item[1]),
        )
        viable = [item for item in scores if item[0] >= similarity_threshold]
        if not viable and len(shortlist) < len(prefilter_scores):
            scores = sorted(
                ((geometry.calculate_similarity(source_mesh, component.mesh), label, source_mesh)
                 for _prefilter_score, label, source_mesh in prefilter_scores),
                reverse=True,
                key=lambda item: (item[0], item[1]),
            )
            viable = [item for item in scores if item[0] >= similarity_threshold]
        if not viable:
            best_score, best_label, _mesh = scores[0]
            raise NamedBoneMappingError(
                f"Component {component.index} best voxel similarity is only {best_score:.2f}% ({best_label})"
            )
        target_clouds = _group_clouds(component.mesh)
        candidates = []
        first_compatible = None
        for score, label, source_mesh in viable:
            try:
                local_to_source, skin_cost = _match_vertex_groups(
                    component.mesh,
                    source_mesh,
                    vg_candidates,
                    target_clouds=target_clouds,
                    source_clouds=source_cloud_cache[id(source_mesh)],
                )
            except NamedBoneMappingError:
                continue
            first_compatible = (skin_cost, -score, label, local_to_source, source_mesh)
            candidates.append(first_compatible)
            break
        geometry_score = -first_compatible[1] if first_compatible is not None else 0.0
        competing_score = max(
            (score for score, _label, source_mesh in viable
             if first_compatible is None or source_mesh is not first_compatible[4]),
            default=-1.0,
        )
        geometry_is_decisive = geometry_score >= 99.0 and geometry_score - competing_score > 0.01
        if (
            first_compatible is not None
            and first_compatible[0] > 0.001
            and not geometry_is_decisive
        ):
            for score, label, source_mesh in viable:
                if source_mesh is first_compatible[4]:
                    continue
                try:
                    local_to_source, skin_cost = _match_vertex_groups(
                        component.mesh,
                        source_mesh,
                        vg_candidates,
                        target_clouds=target_clouds,
                        source_clouds=source_cloud_cache[id(source_mesh)],
                    )
                except NamedBoneMappingError:
                    continue
                candidates.append((skin_cost, -score, label, local_to_source, source_mesh))
        if not candidates:
            raise NamedBoneMappingError(f"Component {component.index} has no skin-compatible GLB mesh candidate")
        candidates.sort(key=lambda item: item[:3])
        skin_cost, negative_score, label, local_to_source, source_mesh = candidates[0]
        tied = [item for item in candidates if abs(item[0] - skin_cost) < 1.0e-6]
        if any(item[3] != local_to_source for item in tied[1:]):
            raise NamedBoneMappingError(
                f"Component {component.index} has tied GLB candidates with different bone assignments"
            )
        local_to_name = {}
        for local_id, source_id in local_to_source.items():
            if source_id < 0 or source_id >= len(source_mesh.bone_names):
                raise NamedBoneMappingError(f"{label}: joint {source_id} is out of range")
            local_to_name[int(local_id)] = source_mesh.bone_names[source_id]
        runtime_map = {int(local): int(runtime) for local, runtime in (component.meta.get("runtime_vg_map") or {}).items()}
        if set(local_to_name) != set(runtime_map):
            missing = sorted(set(runtime_map) - set(local_to_name))
            extra = sorted(set(local_to_name) - set(runtime_map))
            raise NamedBoneMappingError(
                f"Component {component.index} local bone coverage differs from runtime_vg_map "
                f"(missing={missing[:8]}, extra={extra[:8]})"
            )
        component_maps[component.index] = local_to_name
        evidence.append((component.index, label, -negative_score, skin_cost, len(local_to_name)))
    return source_path, metadata, component_maps, evidence


def write_mapping(source_folder: Path, _source_path: Path, metadata: dict, component_maps: dict) -> Path:
    payload = copy.deepcopy(metadata)
    components = payload.get("components", [])
    if len(components) != len(component_maps):
        raise NamedBoneMappingError("Component mapping count does not match Metadata.json")
    for component_id, component in enumerate(components):
        component["vg_map"] = {
            str(local): name for local, name in sorted(component_maps[component_id].items())
        }
    payload["bone_name_mapping_version"] = MAPPING_VERSION
    payload["skeleton_file"] = SKELETON_FILE_NAME
    payload["source_glb"] = SKELETON_FILE_NAME
    target = Path(source_folder) / MAPPING_FILE_NAME
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def load_mapping(source_folder: Path):
    path = Path(source_folder) / MAPPING_FILE_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NamedBoneMappingError(f"Cannot read {MAPPING_FILE_NAME}: {exc}") from exc
    if payload.get("bone_name_mapping_version") != MAPPING_VERSION:
        raise NamedBoneMappingError(f"Unsupported {MAPPING_FILE_NAME} version")
    return payload


def component_name_maps(payload: dict, component_id: int):
    try:
        component = payload["components"][component_id]
    except (KeyError, IndexError, TypeError) as exc:
        raise NamedBoneMappingError(f"{MAPPING_FILE_NAME} has no Component {component_id}") from exc
    local_to_name = {int(local): str(name) for local, name in (component.get("vg_map") or {}).items()}
    name_to_local = {}
    ambiguous = set()
    for local_id, name in local_to_name.items():
        previous = name_to_local.get(name)
        if previous is not None and previous != local_id:
            ambiguous.add(name)
        else:
            name_to_local[name] = local_id
    for name in ambiguous:
        name_to_local.pop(name, None)
    return local_to_name, name_to_local, ambiguous
