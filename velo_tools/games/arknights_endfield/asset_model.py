"""Read standard Endfield Unity YAML assets without relying on a sibling GLB."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy


_LOD_PATTERN = re.compile(r"_lod(\d+)(?:_|\.|$)", re.IGNORECASE)
_FLOAT = r"[-+0-9.eE]+"
_FORMAT_DTYPES = {
    0: numpy.dtype("<f4"),
    1: numpy.dtype("<f2"),
    2: numpy.dtype("u1"),
    3: numpy.dtype("i1"),
    4: numpy.dtype("<u2"),
    5: numpy.dtype("<i2"),
    6: numpy.dtype("u1"),
    7: numpy.dtype("i1"),
    8: numpy.dtype("<u2"),
    9: numpy.dtype("<i2"),
    10: numpy.dtype("<u4"),
    11: numpy.dtype("<i4"),
}


@dataclass(frozen=True)
class AssetMeshData:
    label: str
    bone_names: tuple[str, ...]
    positions: numpy.ndarray
    triangles: numpy.ndarray
    blend_indices: numpy.ndarray
    blend_weights: numpy.ndarray


@dataclass(frozen=True)
class AssetSkeletonBone:
    name: str
    parent: str | None
    head: tuple[float, float, float]
    armature: str


@dataclass(frozen=True)
class AssetModelData:
    root: Path
    meshes: tuple[AssetMeshData, ...]
    bones: tuple[AssetSkeletonBone, ...]
    armature_matrices: dict[str, tuple[tuple[float, ...], ...]]


def _required_match(pattern, text, label):
    match = re.search(pattern, text, re.MULTILINE)
    if match is None:
        raise ValueError(f"Missing {label}")
    return match


def _asset_inventory(path: Path):
    path = Path(path)
    if not path.exists():
        raise ValueError("The asset path does not exist")
    if path.is_file():
        raise ValueError("Select the complete unpacked asset directory, not one .asset file")
    root = path
    mesh_files = []
    for candidate in root.rglob("*.asset"):
        if "otherlods" in {part.lower() for part in candidate.relative_to(root).parts}:
            continue
        match = _LOD_PATTERN.search(candidate.stem)
        if match is None or int(match.group(1)) != 0:
            continue
        mesh_files.append(candidate)
    if not mesh_files:
        raise ValueError("No LOD0 mesh .asset files were found inside the selected directory")
    avatar_files = sorted(root.rglob("*Avatar.asset"))
    if len(avatar_files) != 1:
        raise ValueError(
            "Select the unpacked character root containing both one Avatar.asset and the LOD0 assets; "
            f"the selected directory contains {len(avatar_files)} Avatar.asset files"
        )
    prefab_files = sorted(root.rglob("*.prefab"))
    postmodels = [candidate for candidate in prefab_files if "postmodel" in candidate.stem.lower()]
    if len(postmodels) == 1:
        prefab_files = postmodels
    if len(prefab_files) != 1:
        raise ValueError(f"Expected one character prefab inside the selected directory, found {len(prefab_files)}")
    return root, tuple(sorted(mesh_files)), avatar_files[0], prefab_files[0]


def _avatar_paths(text: str):
    return {
        int(key): value.strip()
        for key, value in re.findall(r"^    (\d+): (.+)$", text, re.MULTILINE)
    }


def _channels(text: str):
    block = _required_match(
        r"^    m_Channels:\n(?P<body>[\s\S]*?)(?=^    m_DataSize:)", text, "m_Channels"
    ).group("body")
    channels = []
    pattern = re.compile(
        r"^    - stream: (\d+)\n"
        r"      offset: (\d+)\n"
        r"      format: (\d+)\n"
        r"      dimension: (\d+)$",
        re.MULTILINE,
    )
    for match in pattern.finditer(block):
        channels.append(tuple(int(value) for value in match.groups()))
    if len(channels) < 14:
        raise ValueError("The LOD0 mesh has an incomplete vertex channel table")
    return channels


def _stream_layout(channels, vertex_count):
    strides = {}
    for stream, offset, data_format, dimension in channels:
        if dimension == 0:
            continue
        dtype = _FORMAT_DTYPES.get(data_format)
        if dtype is None:
            raise ValueError(f"Unsupported Unity vertex format {data_format}")
        strides[stream] = max(strides.get(stream, 0), offset + dtype.itemsize * dimension)
    starts = {}
    cursor = 0
    for stream in sorted(strides):
        cursor = (cursor + 15) & ~15
        starts[stream] = cursor
        cursor += strides[stream] * vertex_count
    return starts, strides, cursor


def _decode_channel(data, vertex_count, channel, starts, strides):
    stream, offset, data_format, dimension = channel
    dtype = _FORMAT_DTYPES[data_format]
    array = numpy.ndarray(
        (vertex_count, dimension),
        dtype=dtype,
        buffer=data,
        offset=starts[stream] + offset,
        strides=(strides[stream], dtype.itemsize),
    ).copy()
    if data_format == 2:
        return array.astype(numpy.float32) / 255.0
    if data_format == 3:
        return numpy.maximum(array.astype(numpy.float32) / 127.0, -1.0)
    if data_format == 4:
        return array.astype(numpy.float32) / 65535.0
    if data_format == 5:
        return numpy.maximum(array.astype(numpy.float32) / 32767.0, -1.0)
    return array


def _triangles(text: str):
    index_format = int(_required_match(r"^  m_IndexFormat: (\d+)$", text, "m_IndexFormat").group(1))
    index_hex = _required_match(r"^  m_IndexBuffer: ([0-9a-fA-F]+)$", text, "m_IndexBuffer").group(1)
    dtype = numpy.dtype("<u4" if index_format else "<u2")
    raw = bytes.fromhex(index_hex)
    indices = numpy.frombuffer(raw, dtype=dtype)
    submesh_block = _required_match(
        r"^  m_SubMeshes:\n(?P<body>[\s\S]*?)(?=^  m_Shapes:)", text, "m_SubMeshes"
    ).group("body")
    parts = []
    pattern = re.compile(
        r"^  - serializedVersion: \d+\n"
        r"    firstByte: (\d+)\n"
        r"    indexCount: (\d+)\n"
        r"    topology: (\d+)\n"
        r"    baseVertex: (\d+)",
        re.MULTILINE,
    )
    for match in pattern.finditer(submesh_block):
        first_byte, count, topology, base_vertex = (int(value) for value in match.groups())
        if topology != 0:
            continue
        first = first_byte // dtype.itemsize
        part = indices[first:first + count].astype(numpy.int64) + base_vertex
        if len(part) % 3 == 0:
            parts.append(part.reshape(-1, 3))
    if not parts:
        raise ValueError("The LOD0 mesh has no triangle submesh")
    return numpy.concatenate(parts)


def _mesh_data(path: Path, tos):
    text = path.read_text(encoding="utf-8")
    label = _required_match(r"^  m_Name: (.+)$", text, "m_Name").group(1).strip()
    bone_hex = _required_match(
        r"^  m_BoneNameHashes: ([0-9a-fA-F]+)$", text, "m_BoneNameHashes"
    ).group(1)
    bone_hashes = struct.unpack(f"<{len(bone_hex) // 8}I", bytes.fromhex(bone_hex))
    try:
        bone_paths = tuple(tos[value] for value in bone_hashes)
    except KeyError as exc:
        raise ValueError(f"Avatar TOS has no path for bone hash {exc.args[0]}") from exc
    bone_names = tuple(value.rsplit("/", 1)[-1] for value in bone_paths)
    vertex_count = int(_required_match(r"^    m_VertexCount: (\d+)$", text, "m_VertexCount").group(1))
    channels = _channels(text)
    starts, strides, expected_size = _stream_layout(channels, vertex_count)
    data_hex = _required_match(r"^    _typelessdata: ([0-9a-fA-F]+)$", text, "_typelessdata").group(1)
    data = bytes.fromhex(data_hex)
    if len(data) < expected_size:
        raise ValueError(f"Vertex data is truncated in {path.name}")
    positions = _decode_channel(data, vertex_count, channels[0], starts, strides).astype(numpy.float32)
    weights = _decode_channel(data, vertex_count, channels[12], starts, strides).astype(numpy.float32)
    indices = _decode_channel(data, vertex_count, channels[13], starts, strides).astype(numpy.int32)
    if indices.shape[1] == 0:
        indices = numpy.zeros((vertex_count, 1), dtype=numpy.int32)
    if weights.shape[1] == 0:
        weights = numpy.zeros(indices.shape, dtype=numpy.float32)
        weights[:, 0] = 1.0
    bind_block = _required_match(
        r"^  m_BindPose:\n(?P<body>[\s\S]*?)(?=^  m_BoneNameHashes:)", text, "m_BindPose"
    ).group("body")
    bind_matrices = []
    for match in re.finditer(r"^  - e00: [\s\S]*?(?=^  - e00:|\Z)", bind_block, re.MULTILINE):
        values = {
            key: float(value)
            for key, value in re.findall(r"\b(e\d\d): ([^\n]+)", match.group(0))
        }
        bind_matrices.append(numpy.array(
            [[values[f"e{row}{column}"] for column in range(4)] for row in range(4)],
            dtype=numpy.float64,
        ))
    if len(bind_matrices) != len(bone_paths):
        raise ValueError(f"Bind pose count differs from bone hash count in {path.name}")
    bind_heads = []
    for matrix in bind_matrices:
        position = numpy.linalg.inv(matrix)[:3, 3]
        bind_heads.append((-float(position[0]), float(position[1]), float(position[2])))
    mesh = AssetMeshData(label, bone_names, positions, _triangles(text), indices, weights)
    return mesh, bone_paths, tuple(bind_heads)


def _unity_matrix(position, rotation, scale):
    x, y, z, w = rotation
    length = (x * x + y * y + z * z + w * w) ** 0.5
    if length == 0.0:
        raise ValueError("Prefab contains a zero-length Transform quaternion")
    x, y, z, w = (value / length for value in (x, y, z, w))
    sx, sy, sz = scale
    matrix = numpy.array((
        ((1 - 2 * (y * y + z * z)) * sx, (2 * (x * y - z * w)) * sy, (2 * (x * z + y * w)) * sz, position[0]),
        ((2 * (x * y + z * w)) * sx, (1 - 2 * (x * x + z * z)) * sy, (2 * (y * z - x * w)) * sz, position[1]),
        ((2 * (x * z - y * w)) * sx, (2 * (y * z + x * w)) * sy, (1 - 2 * (x * x + y * y)) * sz, position[2]),
        (0.0, 0.0, 0.0, 1.0),
    ), dtype=numpy.float64)
    return matrix


def _vector(body, field, width):
    suffix = r", w: (" + _FLOAT + r")" if width == 4 else ""
    match = _required_match(
        rf"^  {field}: \{{x: ({_FLOAT}), y: ({_FLOAT}), z: ({_FLOAT}){suffix}\}}$",
        body,
        field,
    )
    return tuple(float(value) for value in match.groups())


def _prefab_transforms(text: str):
    pieces = re.split(r"(?m)^--- !u!(\d+) &(\d+)\n", text)[1:]
    game_objects = {}
    raw_transforms = {}
    for index in range(0, len(pieces), 3):
        class_id, file_id, body = pieces[index], int(pieces[index + 1]), pieces[index + 2]
        if class_id == "1":
            name = _required_match(r"^  m_Name: (.*)$", body, "GameObject m_Name").group(1).strip("'\"")
            game_objects[file_id] = name
        elif class_id == "4":
            game_object = int(_required_match(
                r"^  m_GameObject: \{fileID: (\d+)\}$", body, "Transform m_GameObject"
            ).group(1))
            parent = int(_required_match(
                r"^  m_Father: \{fileID: (\d+)\}$", body, "Transform m_Father"
            ).group(1))
            raw_transforms[file_id] = (
                game_object,
                parent,
                _vector(body, "m_LocalPosition", 3),
                _vector(body, "m_LocalRotation", 4),
                _vector(body, "m_LocalScale", 3),
            )
    world_cache = {}
    path_cache = {}

    def world(file_id):
        cached = world_cache.get(file_id)
        if cached is not None:
            return cached
        game_object, parent, position, rotation, scale = raw_transforms[file_id]
        local = _unity_matrix(position, rotation, scale)
        result = world(parent) @ local if parent in raw_transforms else local
        world_cache[file_id] = result
        return result

    def full_path(file_id):
        cached = path_cache.get(file_id)
        if cached is not None:
            return cached
        game_object, parent, *_unused = raw_transforms[file_id]
        name = game_objects.get(game_object, str(game_object))
        result = f"{full_path(parent)}/{name}" if parent in raw_transforms else name
        path_cache[file_id] = result
        return result

    result = {}
    for file_id in raw_transforms:
        parts = full_path(file_id).split("/")
        if "Root" not in parts:
            continue
        canonical = "/".join(parts[parts.index("Root"):])
        if canonical in result:
            raise ValueError(f"Prefab contains duplicate skeleton path {canonical}")
        result[canonical] = world(file_id)
    return result


def _skeleton(prefab_text, weighted_paths, bind_heads):
    transforms = _prefab_transforms(prefab_text)
    selected = set()
    for path in weighted_paths:
        parts = path.split("/")
        for length in range(1, len(parts) + 1):
            ancestor = "/".join(parts[:length])
            if ancestor in transforms:
                selected.add(ancestor)
    containers = {"Root"}
    if "Root/Bip001" in selected:
        containers.add("Root/Bip001")
    bone_paths = selected - containers
    names = [path.rsplit("/", 1)[-1] for path in bone_paths]
    if len(names) != len(set(names)):
        raise ValueError("Skeleton contains duplicate bone names")
    name_by_path = {path: path.rsplit("/", 1)[-1] for path in bone_paths}

    def root_path(path):
        current = path
        parent = current.rsplit("/", 1)[0] if "/" in current else ""
        while parent in bone_paths:
            current = parent
            parent = current.rsplit("/", 1)[0] if "/" in current else ""
        return current

    root_offsets = {}
    for path in bone_paths:
        root = root_path(path)
        if root in root_offsets or not bind_heads.get(root):
            continue
        unity = transforms[root][:3, 3]
        prefab_head = numpy.array((-float(unity[0]), -float(unity[2]), float(unity[1])))
        bind_head = numpy.median(bind_heads[root], axis=0)
        root_offsets[root] = prefab_head - bind_head
    positions = {}
    for path in bone_paths:
        candidates = bind_heads.get(path)
        if candidates:
            position = numpy.median(candidates, axis=0) + root_offsets.get(root_path(path), 0.0)
        else:
            unity = transforms[path][:3, 3]
            position = numpy.array((-float(unity[0]), -float(unity[2]), float(unity[1])))
        positions[path] = position
    for path in bone_paths:
        if bind_heads.get(path):
            continue
        children = [candidate for candidate in bone_paths if candidate.rsplit("/", 1)[0] == path]
        if len(children) == 1 and bind_heads.get(children[0]):
            child_position = positions[children[0]]
            if numpy.linalg.norm(positions[path] - child_position) < 0.001:
                positions[path] = child_position.copy()

    bones = []
    container_by_root = {}
    for path in sorted(bone_paths, key=lambda value: (value.count("/"), value)):
        parent_path = path.rsplit("/", 1)[0] if "/" in path else ""
        while parent_path and parent_path not in name_by_path:
            parent_path = parent_path.rsplit("/", 1)[0] if "/" in parent_path else ""
        head = tuple(float(value) for value in positions[path])
        root = root_path(path)
        container = root.rsplit("/", 1)[0]
        container_by_root[root] = container
        bones.append(AssetSkeletonBone(
            name_by_path[path], name_by_path.get(parent_path), head, container.rsplit("/", 1)[-1]
        ))
    conversion = numpy.array((
        (-1.0, 0.0, 0.0, 0.0),
        (0.0, 0.0, -1.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ))
    armature_matrices = {}
    for container in sorted(set(container_by_root.values()), key=lambda value: (value != "Root", value)):
        matrix = conversion @ transforms[container] @ conversion.T
        armature_matrices[container.rsplit("/", 1)[-1]] = tuple(
            tuple(float(value) for value in row) for row in matrix
        )
    return tuple(bones), armature_matrices


def load_asset_model(path: Path) -> AssetModelData:
    root, mesh_files, avatar_file, prefab_file = _asset_inventory(path)
    avatar_text = avatar_file.read_text(encoding="utf-8")
    tos = _avatar_paths(avatar_text)
    parsed_meshes = tuple(_mesh_data(mesh_file, tos) for mesh_file in mesh_files)
    meshes = tuple(result[0] for result in parsed_meshes)
    bind_heads = {}
    for _mesh, bone_paths, heads in parsed_meshes:
        for bone_path, head in zip(bone_paths, heads):
            bind_heads.setdefault(bone_path, []).append(head)
    weighted_paths = set()
    for mesh_file in mesh_files:
        mesh_text = mesh_file.read_text(encoding="utf-8")
        bone_hex = _required_match(
            r"^  m_BoneNameHashes: ([0-9a-fA-F]+)$", mesh_text, "m_BoneNameHashes"
        ).group(1)
        for value in struct.unpack(f"<{len(bone_hex) // 8}I", bytes.fromhex(bone_hex)):
            path_value = tos.get(value)
            if path_value is not None:
                weighted_paths.add(path_value)
    bones, armature_matrices = _skeleton(
        prefab_file.read_text(encoding="utf-8"), weighted_paths, bind_heads
    )
    if not bones:
        raise ValueError("No LOD0 skeleton bones were reconstructed from the selected assets")
    return AssetModelData(root, meshes, bones, armature_matrices)
