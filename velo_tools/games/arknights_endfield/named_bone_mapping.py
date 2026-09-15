"""Build and consume Component-local Endfield bone-name mappings."""

from __future__ import annotations

import copy
import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy

from ...core.mapping.bone_identity import (
    BoneIdentityConflict,
    BoneNameEvidence,
    resolve_runtime_bone_names,
)


MAPPING_FILE_NAME = "BoneNameMapping.json"
SKELETON_FILE_NAME = "BoneNameSkeleton.glb"
MAPPING_VERSION = 2
MATCHING_PROOF_VERSION = 1
MATCHING_METHOD = "complete_skin_weights"


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
    implicit_weights: bool = False

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
    if stride < item_size or int(accessor["count"]) < 0 or start < 0 or end > len(binary):
        raise NamedBoneMappingError("GLB accessor exceeds BIN chunk")
    values = numpy.ndarray(
        (int(accessor["count"]), width),
        dtype=dtype,
        buffer=binary,
        offset=start,
        strides=(stride, dtype.itemsize),
    ).copy()
    if accessor.get("normalized", False):
        if dtype.kind not in {'u', 'i'}:
            raise NamedBoneMappingError("GLB normalized accessor requires integer storage")
        values = values.astype(numpy.float32) / numpy.iinfo(dtype).max
        if dtype.kind == 'i':
            values = numpy.maximum(values, -1.0)
    return values


def _skin_attributes(document, binary, attributes, vertex_count):
    """Decode every paired glTF skin set, including normalized integer weights."""
    joints = {int(key[7:]) for key in attributes if key.startswith("JOINTS_")}
    weights = {int(key[8:]) for key in attributes if key.startswith("WEIGHTS_")}
    if not joints or joints != weights or joints != set(range(max(joints) + 1)):
        raise NamedBoneMappingError("GLB joint and weight sets are incomplete")
    index_arrays, weight_arrays = [], []
    for index in sorted(joints):
        joint_id, weight_id = attributes[f"JOINTS_{index}"], attributes[f"WEIGHTS_{index}"]
        joint_meta = document["accessors"][joint_id]
        weight_meta = document["accessors"][weight_id]
        if joint_meta.get("normalized", False) or joint_meta["componentType"] not in {5121, 5123}:
            raise NamedBoneMappingError("GLB joints require unnormalized unsigned integer storage")
        if weight_meta["componentType"] not in {5126, 5121, 5123} or (
                weight_meta["componentType"] != 5126 and not weight_meta.get("normalized", False)):
            raise NamedBoneMappingError("GLB weights require float or normalized unsigned integer storage")
        ids = _accessor(document, binary, joint_id)
        values = _accessor(document, binary, weight_id)
        if ids.shape != (vertex_count, 4) or values.shape != ids.shape:
            raise NamedBoneMappingError("GLB skin attributes have incompatible vertex counts or widths")
        index_arrays.append(ids.astype(numpy.int32))
        weight_arrays.append(values.astype(numpy.float32))
    return numpy.concatenate(index_arrays, axis=1), numpy.concatenate(weight_arrays, axis=1)


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
            blend_indices, blend_weights = _skin_attributes(document, binary, attributes, len(positions))
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


def load_glb_bone_names(path: Path) -> tuple[str, ...]:
    document, _binary = _read_glb(path)
    nodes = document.get("nodes", [])
    joint_ids = {
        int(joint_id)
        for skin in document.get("skins", [])
        for joint_id in skin.get("joints", [])
    }
    return tuple(
        str(nodes[joint_id].get("name") or f"Bone_{joint_id}")
        for joint_id in sorted(joint_ids)
    )


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
        implicit_weights = blend_weights is None
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
                # Preserve UNORM storage precision for full-weight matching.
                if numpy.issubdtype(blend_weights.dtype, numpy.unsignedinteger):
                    denominator = numpy.iinfo(blend_weights.dtype).max
                    blend_weights = blend_weights.astype(numpy.float32) / denominator
                else:
                    blend_weights = blend_weights.astype(numpy.float32, copy=False)
        triangles = ib.get_field(Semantic.Index).reshape(-1, 3).astype(numpy.int64, copy=False)
        components.append(DumpComponent(
            index=component_id,
            source_name=name,
            meta=component_meta,
            mesh=DumpMesh(positions, triangles, blend_indices, blend_weights, implicit_weights),
        ))
    if not components:
        raise NamedBoneMappingError("Metadata.json contains no Components")
    return metadata, components


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


def _match_vertex_groups(
    component_mesh, source_mesh, candidates_count=6, *,
    target_clouds=None, source_clouds=None,
):
    """Use complete weight evidence; retain the old call signature for callers."""
    from ...core.mapping.skin_weight_match import SkinMatchError, match_skin_weights

    try:
        result = match_skin_weights(component_mesh, source_mesh)
    except SkinMatchError as exc:
        raise NamedBoneMappingError(str(exc)) from exc
    return result.mapping, result.weight_error


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
    from ...core.mapping.skin_weight_match import SkinMatchError, select_skin_match

    class VerifiedMaps(dict):
        pass

    component_maps = VerifiedMaps()
    component_maps.match_evidence = {}
    match_cache = {}
    evidence = []
    for component in components:
        if bool(component.meta.get("cpu_posed", False)):
            component_maps[component.index] = {}
            evidence.append((component.index, "CPU-posed", 100.0, 0.0, 0))
            continue
        # Voxel scores order candidates, but never certify bone identity or hide
        # an alternative full-weight match behind a fixed shortlist.
        ranked = sorted(source_meshes, key=lambda mesh:
            geometry_prefilter.calculate_similarity(mesh, component.mesh), reverse=True)
        try:
            source_mesh, match = select_skin_match(component.mesh, ranked, cache=match_cache)
        except SkinMatchError as exc:
            raise NamedBoneMappingError(
                f"Component {component.index}: {exc}") from exc
        geometry_score = geometry.calculate_similarity(source_mesh, component.mesh)
        if geometry_score < similarity_threshold:
            raise NamedBoneMappingError(
                f"Component {component.index} verified source is below the geometry threshold")
        local_to_name = {local: source_mesh.bone_names[joint]
                         for local, joint in match.mapping.items()}
        runtime_map = {int(local): int(runtime) for local, runtime
                       in (component.meta.get("runtime_vg_map") or {}).items()}
        if set(local_to_name) != set(runtime_map):
            raise NamedBoneMappingError(
                f"Component {component.index} full-weight mapping does not cover runtime_vg_map")
        if len(set(local_to_name.values())) != len(local_to_name):
            raise NamedBoneMappingError(
                f"Component {component.index} source skeleton has ambiguous duplicate bone names")
        component_maps[component.index] = local_to_name
        component_maps.match_evidence[component.index] = {
            "method": match.method,
            "source_mesh": source_mesh.label,
            "vertices_checked": len(match.vertex_witnesses),
            "max_position_error": match.position_error,
            "max_weight_error": match.weight_error,
            "position_tolerance": match.position_tolerance,
            "weight_tolerance": match.weight_tolerance,
            "translation": match.translation,
            "source_joint_indices": match.mapping,
            "implicit_source_signatures": match.implicit_signatures,
        }
        evidence.append((component.index, source_mesh.label, geometry_score,
                         match.weight_error, len(local_to_name)))
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
    payload, _corrections = normalize_runtime_bone_names(payload)
    proof = getattr(component_maps, "match_evidence", None)
    if not isinstance(proof, dict):
        raise NamedBoneMappingError(
            "Bone-name mappings must be generated from complete skin-weight evidence"
        )
    payload["bone_name_matching"] = {
        "version": MATCHING_PROOF_VERSION,
        "method": MATCHING_METHOD,
        "components": proof,
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
        raise NamedBoneMappingError(
            f"{MAPPING_FILE_NAME} was generated by an obsolete bone matcher; "
            "regenerate it from the original unpacked model"
        )
    _validate_matching_proof(payload)
    payload, corrections = normalize_runtime_bone_names(payload)
    if corrections:
        print(
            f"[bone-name-mapping] normalized {len(corrections)} "
            "component-local bone names by runtime identity"
        )
    return payload


def _validate_matching_proof(payload: dict):
    proof = payload.get("bone_name_matching")
    if not isinstance(proof, dict):
        raise NamedBoneMappingError(
            f"{MAPPING_FILE_NAME} has no complete skin-weight matching proof"
        )
    if (proof.get("version") != MATCHING_PROOF_VERSION
            or proof.get("method") != MATCHING_METHOD):
        raise NamedBoneMappingError(
            f"{MAPPING_FILE_NAME} uses an unsupported bone matching proof"
        )
    proof_components = proof.get("components")
    components = payload.get("components")
    if not isinstance(proof_components, dict) or not isinstance(components, list):
        raise NamedBoneMappingError(
            f"{MAPPING_FILE_NAME} has invalid bone matching proof data"
        )
    expected = {
        component_id
        for component_id, component in enumerate(components)
        if isinstance(component, dict) and not bool(component.get("cpu_posed", False))
    }
    try:
        actual = {int(component_id) for component_id in proof_components}
    except (TypeError, ValueError) as exc:
        raise NamedBoneMappingError(
            f"{MAPPING_FILE_NAME} has invalid proof Component IDs"
        ) from exc
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise NamedBoneMappingError(
            f"{MAPPING_FILE_NAME} proof Component coverage differs "
            f"(missing={missing[:8]}, extra={extra[:8]})"
        )
    for component_id in sorted(expected):
        row = proof_components.get(str(component_id), proof_components.get(component_id))
        if not isinstance(row, dict):
            raise NamedBoneMappingError(
                f"{MAPPING_FILE_NAME} Component {component_id} proof is invalid"
            )
        if row.get("method") not in {"full_weights", "constant_skin_signatures"}:
            raise NamedBoneMappingError(
                f"{MAPPING_FILE_NAME} Component {component_id} proof method is invalid"
            )
        if int(row.get("vertices_checked") or 0) <= 0:
            raise NamedBoneMappingError(
                f"{MAPPING_FILE_NAME} Component {component_id} proof has no vertex witnesses"
            )
        source_indices = row.get("source_joint_indices")
        if not isinstance(source_indices, dict):
            raise NamedBoneMappingError(
                f"{MAPPING_FILE_NAME} Component {component_id} proof has no joint map"
            )
        local_ids = {int(local) for local in (components[component_id].get("vg_map") or {})}
        try:
            proof_ids = {int(local) for local in source_indices}
        except (TypeError, ValueError) as exc:
            raise NamedBoneMappingError(
                f"{MAPPING_FILE_NAME} Component {component_id} proof has invalid local IDs"
            ) from exc
        if proof_ids != local_ids:
            raise NamedBoneMappingError(
                f"{MAPPING_FILE_NAME} Component {component_id} proof does not cover every local bone"
            )


def normalize_runtime_bone_names(payload: dict):
    """Make component-local names agree for each authoritative runtime bone."""
    normalized = copy.deepcopy(payload)
    components = normalized.get("components")
    if not isinstance(components, list):
        raise NamedBoneMappingError(f"{MAPPING_FILE_NAME} has no Component list")

    evidence = []
    locations = []
    for component_id, component in enumerate(components):
        if not isinstance(component, dict):
            raise NamedBoneMappingError(
                f"{MAPPING_FILE_NAME} Component {component_id} is invalid"
            )
        local_to_name = {
            int(local): str(name)
            for local, name in (component.get("vg_map") or {}).items()
        }
        runtime_map = {
            int(local): int(runtime)
            for local, runtime in (component.get("runtime_vg_map") or {}).items()
        }
        if set(local_to_name) != set(runtime_map):
            missing = sorted(set(runtime_map) - set(local_to_name))
            extra = sorted(set(local_to_name) - set(runtime_map))
            raise NamedBoneMappingError(
                f"{MAPPING_FILE_NAME} Component {component_id} bone-name and "
                f"runtime_vg_map local keys differ "
                f"(missing={missing[:8]}, extra={extra[:8]})"
            )
        for local_id, bone_name in sorted(local_to_name.items()):
            runtime_id = runtime_map[local_id]
            evidence.append(BoneNameEvidence(
                runtime_id=runtime_id,
                bone_name=bone_name,
                source=component_id,
            ))
            locations.append((component_id, local_id, runtime_id, bone_name))

    try:
        canonical = resolve_runtime_bone_names(evidence)
    except BoneIdentityConflict as exc:
        raise NamedBoneMappingError(
            f"{MAPPING_FILE_NAME} has ambiguous runtime bone names: {exc}"
        ) from exc

    corrections = []
    for component_id, local_id, runtime_id, old_name in locations:
        new_name = canonical[runtime_id]
        normalized["components"][component_id].setdefault("vg_map", {})[
            str(local_id)
        ] = new_name
        if new_name != old_name:
            corrections.append(
                (component_id, local_id, runtime_id, old_name, new_name)
            )
    return normalized, tuple(corrections)


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
