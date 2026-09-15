"""Recover bone permutations from complete, spatially corresponding skin weights.

Geometry only proposes vertex witnesses. Every accepted bone edge must satisfy
all vertex weights, and the resulting bipartite matching must be unique. This
module has no Blender, game, file-system, or mutable scene dependencies.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from itertools import product

import numpy as np


class SkinMatchError(ValueError):
    """The supplied meshes do not prove a complete skin correspondence."""


class AmbiguousSkinMatch(SkinMatchError):
    """More than one bone assignment satisfies the available evidence."""


@dataclass
class SkinMatch:
    mapping: dict[int, int]
    vertex_witnesses: np.ndarray
    position_error: float
    weight_error: float
    position_tolerance: float
    weight_tolerance: float
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    method: str = "full_weights"
    implicit_signatures: dict[int, dict[int, float]] | None = None


@dataclass
class _Skin:
    positions: np.ndarray
    group_ids: np.ndarray
    weights: np.ndarray
    sorted_weights: np.ndarray
    quantum: float
    grids: dict


def _weight_quantum(weights):
    """Recognize normalized integer streams; never discard small influences."""
    active = weights[(weights > 0.0) & (weights < 1.0)]
    if not len(active):
        return 0.0
    for denominator in (255, 65535):
        if np.max(np.abs(active * denominator - np.rint(active * denominator))) < 0.006:
            return 1.0 / denominator
    return 0.0


def _prepare(mesh):
    positions = np.asarray(mesh.positions(), dtype=np.float64)
    raw_indices = np.asarray(mesh.blend_indices())
    raw_weights = np.asarray(mesh.blend_weights())
    weights = raw_weights.astype(np.float64)
    explicit_quantum = 0.0
    if raw_weights.dtype.kind == 'u':
        explicit_quantum = 1.0 / np.iinfo(raw_weights.dtype).max
        weights *= explicit_quantum
    if positions.ndim != 2 or positions.shape[1] != 3 or not len(positions):
        raise SkinMatchError("Skin positions must be a nonempty N by 3 array")
    if raw_indices.ndim != 2 or weights.shape != raw_indices.shape or len(weights) != len(positions):
        raise SkinMatchError("Skin index, weight and position counts differ")
    if not np.isfinite(positions).all() or not np.isfinite(weights).all():
        raise SkinMatchError("Skin data contains non-finite values")
    if not np.isfinite(raw_indices).all() or np.any(raw_indices != np.floor(raw_indices)):
        raise SkinMatchError("Skin indices must be finite integers")
    if np.any(raw_indices < 0) or np.any(weights < 0.0) or np.any(weights > 1.00001):
        raise SkinMatchError("Skin indices or weights are out of range")
    indices = raw_indices.astype(np.int64)
    names = getattr(mesh, 'bone_names', None)
    if names is not None and np.any(indices[weights > 0.0] >= len(names)):
        raise SkinMatchError("Skin joint index exceeds the source skeleton")
    quantum = explicit_quantum or _weight_quantum(weights)
    totals = weights.sum(axis=1)
    active_rows = totals > 0.0
    if np.any(np.abs(totals[active_rows] - 1.0) > max(0.0001, quantum * weights.shape[1] + 1e-6)):
        raise SkinMatchError("Skin weights are not a normalized stream")
    weights = weights / np.where(active_rows, totals, 1.0)[:, None]
    group_ids = np.unique(indices[weights > 0.0])
    if not len(group_ids):
        raise SkinMatchError("Skin contains no weighted bones")
    dense = np.zeros((len(weights), len(group_ids)), dtype=np.float32)
    rows, slots = np.nonzero(weights > 0.0)
    np.add.at(dense, (rows, np.searchsorted(group_ids, indices[rows, slots])), weights[rows, slots])
    # Sorting is used only for vertex candidacy, never for bone identity.
    width = int(np.max(np.count_nonzero(dense, axis=1)))
    sorted_weights = np.sort(dense, axis=1)[:, ::-1][:, :width].copy()
    return _Skin(positions.copy(), group_ids, dense, sorted_weights, quantum, {})


def _full_matching(graph, banned=None):
    """Find a saturating matching by augmenting paths, not greedy edge costs."""
    owner = {}
    matched = {}
    for root in sorted(graph, key=lambda key: (len(graph[key]), key)):
        queue = deque([root])
        seen = {root}
        parent = {}
        free = None
        while queue and free is None:
            left = queue.popleft()
            for right in graph[left]:
                if (left, right) == banned or right in parent:
                    continue
                parent[right] = left
                if right not in owner:
                    free = right
                    break
                next_left = owner[right]
                if next_left not in seen:
                    seen.add(next_left)
                    queue.append(next_left)
        if free is None:
            return None
        right = free
        while True:
            left = parent[right]
            previous = matched.get(left)
            owner[right] = left
            matched[left] = right
            if previous is None:
                break
            right = previous
    return matched


def unique_bone_matching(graph):
    """Prove uniqueness by forbidding each chosen edge and resolving globally."""
    graph = {int(key): tuple(sorted(set(map(int, values)))) for key, values in graph.items()}
    result = _full_matching(graph)
    if result is None:
        raise SkinMatchError("No complete bone assignment satisfies the weight evidence")
    for left, right in sorted(result.items()):
        if len(graph[left]) > 1 and _full_matching(graph, (left, right)) is not None:
            raise AmbiguousSkinMatch(
                "Multiple bone assignments satisfy the weight evidence; additional source evidence is required")
    return dict(sorted(result.items()))


def _vertex_candidates(target, source, position_tolerance, weight_tolerance, *, check_weights=True):
    grid = source.grids.get(position_tolerance)
    if grid is None:
        grid = {}
        cells = np.floor(source.positions / position_tolerance).astype(np.int64)
        for index, cell in enumerate(cells):
            grid.setdefault(tuple(cell), []).append(index)
        source.grids[position_tolerance] = grid
    width = max(target.sorted_weights.shape[1], source.sorted_weights.shape[1])
    target_sorted = np.pad(target.sorted_weights, ((0, 0), (0, width - target.sorted_weights.shape[1])))
    source_sorted = np.pad(source.sorted_weights, ((0, 0), (0, width - source.sorted_weights.shape[1])))
    offsets = tuple(product((-1, 0, 1), repeat=3))
    rows = []
    missing_geometry = 0
    missing_skin = 0
    for index, point in enumerate(target.positions):
        cell = np.floor(point / position_tolerance).astype(np.int64)
        possible = []
        for dx, dy, dz in offsets:
            possible.extend(grid.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ()))
        if possible:
            possible = np.asarray(possible, dtype=np.int64)
            error = np.linalg.norm(source.positions[possible] - point, axis=1)
            possible = possible[error <= position_tolerance]
        else:
            possible = np.empty(0, dtype=np.int64)
        if not len(possible):
            missing_geometry += 1
        elif check_weights:
            possible = possible[np.max(np.abs(source_sorted[possible] - target_sorted[index]), axis=1) <= weight_tolerance]
            if not len(possible):
                missing_skin += 1
        rows.append(possible)
    if missing_geometry:
        raise SkinMatchError("Some target vertices have no position witness in the source mesh")
    if missing_skin:
        raise SkinMatchError("Corresponding vertices have incompatible full weight distributions")
    return rows


def select_skin_match(target_mesh, source_meshes, *, cache=None):
    """Accept only a single name assignment across every compatible source mesh."""
    cache = {} if cache is None else cache
    accepted = []
    ambiguous = []
    for source_mesh in source_meshes:
        try:
            result = match_skin_weights(target_mesh, source_mesh, cache=cache)
        except AmbiguousSkinMatch:
            ambiguous.append(str(getattr(source_mesh, 'label', '')))
            continue
        except SkinMatchError:
            continue
        names = tuple(sorted((local, source_mesh.bone_names[joint])
                             for local, joint in result.mapping.items()))
        accepted.append((names, result, source_mesh))
    if ambiguous:
        raise AmbiguousSkinMatch("A spatially compatible source mesh has ambiguous bone identities")
    if not accepted:
        raise SkinMatchError("No source mesh satisfies every vertex and skin-weight constraint")
    if len({row[0] for row in accepted}) != 1:
        raise AmbiguousSkinMatch("Compatible source meshes disagree on the bone-name assignment")
    accepted.sort(key=lambda row: (row[1].weight_error, row[1].position_error,
                                   str(getattr(row[2], 'label', ''))))
    _names, result, source_mesh = accepted[0]
    return source_mesh, result


def _constant_signature_mapping(target, source, candidates, tolerance):
    """Verify constant source mixtures for an explicitly implicit-weight stream.

    Such runtime slots can represent preblended matrices, not pure source bones.
    Keep their complete source mixtures as evidence and require a unique support
    assignment. Never apply this rule to ordinary explicit-weight meshes.
    """
    if np.any(np.count_nonzero(target.weights, axis=1) != 1):
        raise SkinMatchError("Implicit skin stream is not a rigid local-slot partition")
    signatures = {}
    expected = np.zeros((len(target.positions), len(source.group_ids)), dtype=np.float32)
    graph = {}
    for column, group_id in enumerate(target.group_ids):
        vertices = np.flatnonzero(target.weights[:, column] > 0.0)
        options = np.unique(source.weights[candidates[int(vertices[0])]], axis=0)
        passing = []
        for signature in options:
            if all(np.any(np.max(np.abs(source.weights[candidates[int(vertex)]] - signature), axis=1) <= tolerance) for vertex in vertices):
                if not any(np.max(np.abs(signature - previous)) <= tolerance for previous in passing):
                    passing.append(signature)
        if not passing:
            raise SkinMatchError("Implicit local slot has no constant full source-weight signature")
        if len(passing) != 1:
            raise AmbiguousSkinMatch("Implicit local slot has multiple source-weight signatures")
        signature = passing[0]
        active = np.flatnonzero(signature > 0.0)
        graph[int(group_id)] = [int(source.group_ids[index]) for index in active]
        signatures[int(group_id)] = {int(source.group_ids[index]): float(signature[index]) for index in active}
        expected[vertices] = signature
    return unique_bone_matching(graph), expected, signatures


def match_skin_weights(target_mesh, source_mesh, *, position_tolerance=None,
                       weight_tolerance=None, cache=None):
    """Return a unique local-bone permutation supported by every target vertex.

    UV seam duplicates and reordering are allowed. Different coincident skin
    records remain candidates until full row verification; nearest-point order
    is never used to silently choose between distinct bone identities.
    """
    cache = {} if cache is None else cache
    for mesh in (target_mesh, source_mesh):
        if id(mesh) not in cache:
            cache[id(mesh)] = _prepare(mesh)
    target, source = cache[id(target_mesh)], cache[id(source_mesh)]
    scale = float(np.linalg.norm(np.ptp(target.positions, axis=0)))
    if position_tolerance is None:
        position_tolerance = max(1.0e-7, scale * 2.0e-5)
    if weight_tolerance is None:
        # Nearest-value quantization contributes at most half a storage step
        # from each stream. Keep only a small floating-point allowance beyond
        # that representational bound.
        weight_tolerance = max(
            3e-6,
            0.5 * (target.quantum + source.quantum) + 3e-6,
        )
    if not np.isfinite(position_tolerance) or position_tolerance <= 0.0:
        raise SkinMatchError("Position tolerance must be finite and positive")
    if not np.isfinite(weight_tolerance) or not 0.0 <= weight_tolerance < 0.05:
        raise SkinMatchError("Weight tolerance must be finite and less than 0.05")
    translation = np.zeros(3, dtype=np.float64)
    target_min, target_max = target.positions.min(axis=0), target.positions.max(axis=0)
    source_min, source_max = source.positions.min(axis=0), source.positions.max(axis=0)
    # Complete near-identical meshes may carry a different object translation.
    # Propose one rigid translation only when all bounding extents agree; every
    # vertex must still pass the position and full-weight witness checks.
    if np.all(np.abs((target_max - target_min) - (source_max - source_min)) <= position_tolerance):
        translation = (target_min + target_max - source_min - source_max) * 0.5
        target = replace(target, positions=target.positions - translation)
    if np.any(np.max(target.positions, axis=0) > np.max(source.positions, axis=0) + position_tolerance) or np.any(np.min(target.positions, axis=0) < np.min(source.positions, axis=0) - position_tolerance):
        raise SkinMatchError("Target mesh is outside the source coordinate bounds")
    implicit = bool(getattr(target_mesh, 'implicit_weights', False))
    candidates = _vertex_candidates(target, source, position_tolerance, weight_tolerance,
                                    check_weights=not implicit)
    first = np.asarray([row[0] for row in candidates], dtype=np.int64)
    lower = source.weights[first].copy()
    upper = lower.copy()
    for index, rows in enumerate(candidates):
        if len(rows) > 1:
            lower[index] = source.weights[rows].min(axis=0)
            upper[index] = source.weights[rows].max(axis=0)
    signatures = None
    if implicit:
        mapping, expected, signatures = _constant_signature_mapping(
            target, source, candidates, weight_tolerance)
    else:
        graph = {}
        for column, group_id in enumerate(target.group_ids):
            values = target.weights[:, column]
            anchor = int(np.argmax(values))
            possible = np.flatnonzero((values[anchor] >= lower[anchor] - weight_tolerance) & (values[anchor] <= upper[anchor] + weight_tolerance))
            accepted = []
            for source_column in possible:
                error = np.maximum(lower[:, source_column] - values, values - upper[:, source_column])
                if np.max(error) <= weight_tolerance:
                    accepted.append(int(source.group_ids[source_column]))
            if not accepted:
                raise SkinMatchError("A target bone has no source weight-vector match")
            graph[int(group_id)] = accepted
        mapping = unique_bone_matching(graph)
        source_columns = np.asarray([np.searchsorted(source.group_ids, mapping[int(group_id)]) for group_id in target.group_ids])
        expected = np.zeros_like(lower)
        expected[:, source_columns] = target.weights
    # A set of per-bone bounds can overapproximate coincident vertex choices.
    # Require one actual source row to satisfy the entire skin simultaneously.
    witnesses = first.copy()
    max_weight_error = 0.0
    max_position_error = 0.0
    for index, rows in enumerate(candidates):
        errors = np.max(np.abs(source.weights[rows] - expected[index]), axis=1)
        passing = np.flatnonzero(errors <= weight_tolerance)
        if not len(passing):
            raise SkinMatchError("Bone matches do not share a consistent full vertex witness")
        selected = int(passing[np.argmin(errors[passing])])
        witness = int(rows[selected])
        witnesses[index] = witness
        max_weight_error = max(max_weight_error, float(errors[selected]))
        max_position_error = max(max_position_error, float(np.linalg.norm(target.positions[index] - source.positions[witness])))
    return SkinMatch(mapping, witnesses, max_position_error, max_weight_error,
                     float(position_tolerance), float(weight_tolerance),
                     tuple(float(value) for value in translation),
                     "constant_skin_signatures" if implicit else "full_weights",
                     signatures)
