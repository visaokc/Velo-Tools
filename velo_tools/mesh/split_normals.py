"""Preserve per-corner normals across destructive mesh separation."""

from __future__ import annotations

import numpy as np


_ATTRIBUTE_BASE = "__velo_split_corner_normal"


def capture_split_corner_normals(mesh) -> str:
    """Store the current loop normals in a temporary corner attribute."""
    name = _ATTRIBUTE_BASE
    suffix = 1
    while mesh.attributes.get(name) is not None:
        name = f"{_ATTRIBUTE_BASE}.{suffix:03d}"
        suffix += 1

    attribute = mesh.attributes.new(name, "FLOAT_VECTOR", "CORNER")
    normals = np.empty(len(mesh.loops) * 3, dtype=np.float32)
    mesh.loops.foreach_get("normal", normals)
    attribute.data.foreach_set("vector", normals)
    return attribute.name


def restore_split_corner_normals(objects, attribute_name: str) -> None:
    """Restore and remove a propagated corner-normal attribute on every mesh."""
    meshes = []
    seen = set()
    for obj in objects:
        mesh = getattr(obj, "data", None)
        if getattr(obj, "type", None) != "MESH" or mesh is None:
            continue
        pointer = int(mesh.as_pointer())
        if pointer not in seen:
            seen.add(pointer)
            meshes.append(mesh)

    snapshots = []
    try:
        for mesh in meshes:
            attribute = mesh.attributes.get(attribute_name)
            if attribute is None:
                raise RuntimeError(
                    f"Material split did not preserve temporary corner normals on mesh `{mesh.name}`."
                )
            if attribute.domain != "CORNER" or attribute.data_type != "FLOAT_VECTOR":
                raise RuntimeError(
                    f"Temporary corner normals on mesh `{mesh.name}` have an invalid type."
                )
            if len(attribute.data) != len(mesh.loops):
                raise RuntimeError(
                    f"Temporary corner normals on mesh `{mesh.name}` do not match its loop count."
                )
            normals = np.empty(len(mesh.loops) * 3, dtype=np.float32)
            attribute.data.foreach_get("vector", normals)
            snapshots.append((mesh, normals.reshape((-1, 3))))
    finally:
        for mesh in meshes:
            attribute = mesh.attributes.get(attribute_name)
            if attribute is not None:
                mesh.attributes.remove(attribute)

    for mesh, normals in snapshots:
        mesh.normals_split_custom_set(normals)
