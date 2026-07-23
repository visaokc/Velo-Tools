from __future__ import annotations

import math
import sys
from contextlib import contextmanager
from pathlib import Path


_np = None
_sp = None
_igl = None
_robust_laplacian = None
_load_error = ""


def _bundled_site_packages():
    py_tag = f"Python{sys.version_info.major}{sys.version_info.minor}"
    return Path(__file__).resolve().parent / "_native_deps" / py_tag / "site-packages"


@contextmanager
def _temporary_sys_path(path):
    text = str(path)
    inserted = text not in sys.path
    if inserted:
        sys.path.insert(0, text)
    try:
        yield
    finally:
        if inserted:
            try:
                sys.path.remove(text)
            except ValueError:
                pass


def _import_dependency_modules():
    import numpy as np
    import scipy as sp
    import igl
    import robust_laplacian
    return np, sp, igl, robust_laplacian


def _load_dependency_modules():
    try:
        return _import_dependency_modules()
    except Exception:
        bundle_path = _bundled_site_packages()
        if not bundle_path.is_dir():
            raise
    with _temporary_sys_path(bundle_path):
        return _import_dependency_modules()


def ensure_available():
    global _np, _sp, _igl, _robust_laplacian, _load_error
    if _np is not None and _sp is not None and _igl is not None and _robust_laplacian is not None:
        return True, ""
    try:
        np, sp, igl, robust_laplacian = _load_dependency_modules()
    except Exception as exc:
        _load_error = str(exc)
        return False, _load_error
    _np = np
    _sp = sp
    _igl = igl
    _robust_laplacian = robust_laplacian
    _load_error = ""
    return True, ""


def np_module():
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    return _np


def find_closest_point_on_surface(points, verts, faces):
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    sqr_d, face_indices, closest = _igl.point_mesh_squared_distance(points, verts, faces)
    faces_closest = faces[face_indices, :]
    v1 = verts[faces_closest[:, 0], :]
    v2 = verts[faces_closest[:, 1], :]
    v3 = verts[faces_closest[:, 2], :]
    bary = _igl.barycentric_coordinates_tri(closest, v1, v2, v3)
    return sqr_d, face_indices, closest, bary


def interpolate_attribute_from_bary(attr, bary, face_indices, faces):
    faces_closest = faces[face_indices, :]
    a1 = attr[faces_closest[:, 0], :]
    a2 = attr[faces_closest[:, 1], :]
    a3 = attr[faces_closest[:, 2], :]
    b1 = bary[:, 0].reshape(-1, 1)
    b2 = bary[:, 1].reshape(-1, 1)
    b3 = bary[:, 2].reshape(-1, 1)
    return a1 * b1 + a2 * b2 + a3 * b3


def find_matches_closest_surface(source_verts, source_triangles, source_normals,
                                 target_verts, target_normals, source_weights,
                                 distance_threshold_sq, angle_threshold_degrees,
                                 flip_vertex_normal, *, return_stats=False):
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    sqr_d, face_indices, _closest, bary = find_closest_point_on_surface(
        target_verts, source_verts, source_triangles
    )
    weights = interpolate_attribute_from_bary(source_weights, bary, face_indices, source_triangles)
    n1 = interpolate_attribute_from_bary(source_normals, bary, face_indices, source_triangles)
    n1_norm = _np.linalg.norm(n1, axis=1, keepdims=True)
    n2_norm = _np.linalg.norm(target_normals, axis=1, keepdims=True)
    n1 = _np.divide(n1, n1_norm, out=_np.zeros_like(n1), where=n1_norm > 1e-12)
    n2 = _np.divide(target_normals, n2_norm, out=_np.zeros_like(target_normals), where=n2_norm > 1e-12)
    dot = _np.einsum('ij,ij->i', n1, n2)
    dot = _np.clip(dot, -1.0, 1.0)
    deg_angles = _np.degrees(_np.arccos(dot))
    is_distance = sqr_d <= distance_threshold_sq
    is_angle = deg_angles <= _np.full(deg_angles.shape, angle_threshold_degrees)
    if flip_vertex_normal:
        mirror_angles = 180.0 - deg_angles
        is_angle = _np.logical_or(is_angle, mirror_angles <= angle_threshold_degrees)
    matched = _np.logical_and(is_distance, is_angle)
    if return_stats:
        return matched, weights, {
            "target_vertices": int(target_verts.shape[0]),
            "distance_pass": int(_np.count_nonzero(is_distance)),
            "angle_pass": int(_np.count_nonzero(is_angle)),
            "matched": int(_np.count_nonzero(matched)),
        }
    return matched, weights


def inpaint(verts, faces, weights, matched, point_cloud):
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    if weights.shape[0] == 0 or weights.shape[1] == 0 or not _np.any(matched):
        return False, weights
    if point_cloud:
        laplacian, mass = _robust_laplacian.point_cloud_laplacian(verts)
    else:
        laplacian, mass = _robust_laplacian.mesh_laplacian(verts, faces)
    laplacian = -laplacian
    mass_inv = _sp.sparse.diags(1 / mass.diagonal())
    q2 = -laplacian + laplacian * mass_inv * laplacian
    q2 = q2.astype(_np.float64)
    aeq = _sp.sparse.csc_matrix((0, 0), dtype=_np.float64)
    beq = _np.array([], dtype=_np.float64)
    linear = _np.zeros(shape=(laplacian.shape[0], weights.shape[1]), dtype=_np.float64)
    fixed = _np.array(range(0, int(verts.shape[0])), dtype=_np.int64)
    fixed = fixed[matched]
    fixed_values = weights[matched, :].astype(_np.float64)
    result, painted = _igl.min_quad_with_fixed(q2, linear, fixed, fixed_values, aeq, beq, True)
    painted = painted.astype(_np.float32)
    if result:
        painted = painted.reshape(weights.shape)
    return result, painted


def limit_mask(weights, adjacency_matrix, dilation_repeat=5, limit_num=4):
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    if weights.shape[1] <= limit_num:
        return _np.zeros_like(weights)
    count = _np.count_nonzero(weights, axis=1)
    to_limit = count > limit_num
    kth = weights.shape[1] - limit_num
    weight_indices = _np.argpartition(weights, kth=kth, axis=1)[:, :kth]
    row_indices = _np.arange(weights.shape[0])[:, None]
    erode_mask = _np.zeros_like(weights, dtype=bool)
    erode_mask[row_indices, weight_indices] = True
    erode_mask = _np.logical_and(erode_mask, to_limit[:, _np.newaxis])
    erode_mask = _sp.sparse.csr_array(erode_mask).astype(_np.float32)
    degrees = _np.asarray(adjacency_matrix.sum(axis=1)).reshape(-1)
    inv = _np.divide(1.0, degrees, out=_np.zeros_like(degrees, dtype=_np.float32), where=degrees > 0)
    smooth_mat = _sp.sparse.diags(inv) @ adjacency_matrix
    for _ in range(dilation_repeat):
        avg_weights = smooth_mat @ erode_mask
        erode_mask = erode_mask.maximum(avg_weights)
    return erode_mask.toarray()


def smooth_weigths(verts, weights, matched, adjacency_matrix, adjacency_list,
                   num_smooth_iter_steps, smooth_alpha, distance_threshold):
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    not_matched = ~matched
    vids_to_smooth = _np.zeros(verts.shape[0], dtype=bool)

    def get_points_within_distance(vid):
        queue = [vid]
        while len(queue) != 0:
            current = queue.pop()
            if current < len(adjacency_list):
                for neighbor in adjacency_list[current]:
                    if (not vids_to_smooth[neighbor]
                            and _np.linalg.norm(verts[vid, :] - verts[neighbor, :]) < distance_threshold):
                        vids_to_smooth[neighbor] = True
                        if neighbor not in queue:
                            queue.append(neighbor)

    for i in range(verts.shape[0]):
        if not_matched[i]:
            get_points_within_distance(i)
    adj_mat = adjacency_matrix.astype(_np.float32)
    degrees = _np.asarray(adj_mat.sum(axis=1)).reshape(-1)
    inv = _np.divide(1.0, degrees, out=_np.zeros_like(degrees, dtype=_np.float32), where=degrees > 0)
    smooth_mat = _sp.sparse.diags(inv) @ adj_mat
    weights_smoothed = _sp.sparse.csr_array(weights)
    for _ in range(num_smooth_iter_steps):
        weights_smoothed = (1 - smooth_alpha) * weights_smoothed + smooth_alpha * (smooth_mat @ weights_smoothed)
        weights_smoothed[~vids_to_smooth] = weights[~vids_to_smooth]
    return _np.asarray(weights_smoothed.todense())


def get_obj_arrs_world(obj):
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    mesh = obj.data
    mesh.calc_loop_triangles()
    vertices = _np.empty((len(mesh.vertices), 3), dtype=_np.float32)
    indices = _np.empty((len(mesh.loop_triangles), 3), dtype=_np.int64)
    normals = _np.empty((len(mesh.vertices), 3), dtype=_np.float32)
    mesh.vertices.foreach_get("co", vertices.reshape(-1))
    mesh.loop_triangles.foreach_get("vertices", indices.reshape(-1))
    mesh.vertices.foreach_get("normal", normals.reshape(-1))
    world_matrix = _np.array(obj.matrix_world)
    ones = _np.ones((vertices.shape[0], 1))
    vertices_4d = _np.hstack((vertices, ones))
    world_vertices_4d = (world_matrix @ vertices_4d.T).T
    world_vertices_4d = _np.ascontiguousarray(world_vertices_4d, dtype=_np.float32)
    world_vertices = world_vertices_4d[:, :3] / world_vertices_4d[:, 3][:, _np.newaxis]
    world_normals = (_np.linalg.inv(world_matrix[:3, :3]).T @ normals.T).T
    world_normals = _np.ascontiguousarray(world_normals, dtype=_np.float32)
    return world_vertices, indices, world_normals


def get_group_arr(obj, group_name):
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    group = obj.vertex_groups.get(group_name)
    if group is None:
        raise ValueError(f"vertex group not found: {group_name}")
    arr = _np.zeros((len(obj.data.vertices), 1), dtype=_np.float32)
    group_index = group.index
    for vertex in obj.data.vertices:
        for elem in vertex.groups:
            if elem.group == group_index:
                arr[vertex.index, 0] = elem.weight
                break
    return arr


def get_groups_arr(obj, group_indices):
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    lookup = {group_index: col for col, group_index in enumerate(group_indices)}
    arr = _np.zeros((len(obj.data.vertices), len(group_indices)), dtype=_np.float32)
    for vertex in obj.data.vertices:
        for elem in vertex.groups:
            col = lookup.get(elem.group)
            if col is not None:
                arr[vertex.index, col] = elem.weight
    return arr


def get_group_weights_by_index(obj, group_index):
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    arr = _np.zeros(len(obj.data.vertices), dtype=_np.float32)
    for vertex in obj.data.vertices:
        for elem in vertex.groups:
            if elem.group == group_index:
                arr[vertex.index] = elem.weight
                break
    return arr


def get_mesh_adjacency_matrix_sparse(mesh, *, include_self=False, blocked_edges=None):
    ok, error = ensure_available()
    if not ok:
        raise RuntimeError(error)
    blocked_edges = blocked_edges or set()
    edges = []
    for edge in mesh.edges:
        key = tuple(sorted(edge.vertices))
        if key in blocked_edges:
            continue
        edges.append(tuple(edge.vertices))
    num_verts = len(mesh.vertices)
    if edges:
        edge_data = _np.array(edges, dtype=int)
        rows = _np.hstack([edge_data[:, 0], edge_data[:, 1]])
        cols = _np.hstack([edge_data[:, 1], edge_data[:, 0]])
        data = _np.ones(len(rows), dtype=int)
        matrix = _sp.sparse.csr_array((data, (rows, cols)), shape=(num_verts, num_verts))
    else:
        matrix = _sp.sparse.csr_array((num_verts, num_verts), dtype=int)
    if include_self:
        matrix.setdiag(1)
    return matrix


def get_mesh_adjacency_list(mesh, *, blocked_edges=None):
    blocked_edges = blocked_edges or set()
    adj_list = [[] for _ in range(len(mesh.vertices))]
    for edge in mesh.edges:
        key = tuple(sorted(edge.vertices))
        if key in blocked_edges:
            continue
        a, b = edge.vertices
        adj_list[a].append(b)
        adj_list[b].append(a)
    return adj_list


def degrees(value):
    return math.degrees(value)
