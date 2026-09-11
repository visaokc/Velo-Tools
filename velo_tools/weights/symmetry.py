"""Layer-preserving local-X correspondence, independent of deform weights."""

import numpy as np


def mirror_candidates(coordinates, edges=(), *, tolerance=None):
    """Return reciprocal nearest candidates without quantizing nearby layers.

    Exact duplicate coordinates remain separate vertices. Topology may resolve
    their identity; otherwise the caller must require equal source values and
    never average different fields or pick a layer by vertex index.
    """
    from scipy.spatial import cKDTree

    coordinates = np.asarray(coordinates, dtype=float).reshape(-1, 3)
    if not np.isfinite(coordinates).all():
        raise ValueError('Mirror coordinates must be finite')
    count = len(coordinates)
    if not count:
        return [], 0.0
    diagonal = float(np.linalg.norm(np.ptp(coordinates, axis=0)))
    tolerance = max(diagonal * .0001, .000001) if tolerance is None else float(tolerance)
    if not np.isfinite(tolerance) or tolerance <= 0.:
        raise ValueError('Mirror tolerance must be positive and finite')
    # Only bit-identical positions share a search entry, never a weight value.
    centers, inverse = np.unique(coordinates, axis=0, return_inverse=True)
    buckets = [[] for _ in centers]
    for row, bucket in enumerate(inverse):
        buckets[bucket].append(row)
    reflected = centers * (-1., 1., 1.)
    tree = cKDTree(centers)
    distances, nearest = tree.query(reflected, k=2)
    tie = max(diagonal * 1e-9, 1e-12)
    partners = []
    for row, (distance, other) in enumerate(distances):
        if distance > tolerance:
            partners.append(())
        elif other <= min(tolerance, distance + tie):
            partners.append(tuple(tree.query_ball_point(reflected[row], min(tolerance, distance + tie))))
        else:
            partners.append((int(nearest[row, 0]),))
    candidates = [tuple(vertex for partner in partners[bucket]
                        if bucket in partners[partner]
                        for vertex in buckets[partner]) for bucket in inverse]

    # Distinguish coincident layers with different edge neighborhoods. The
    # fallback remains ambiguous rather than using weights as matching input.
    ambiguous = [row for row, values in enumerate(candidates) if len(values) > 1]
    if ambiguous and len(edges):
        adjacency = [[] for _ in coordinates]
        for left, right in edges:
            adjacency[int(left)].append(int(right))
            adjacency[int(right)].append(int(left))
        original = candidates.copy()
        for row in ambiguous:
            neighbors = adjacency[row]
            if not neighbors:
                continue
            wanted = (coordinates[neighbors] - coordinates[row]) * (-1., 1., 1.)
            compatible = []
            for source in original[row]:
                other = adjacency[source]
                if len(other) != len(neighbors):
                    continue
                offsets = coordinates[other] - coordinates[source]
                delta = np.linalg.norm(wanted[:, None, :] - offsets[None, :, :], axis=2)
                if max(float(delta.min(axis=0).max()), float(delta.min(axis=1).max())) <= tolerance:
                    compatible.append(source)
            if compatible:
                candidates[row] = tuple(compatible)
        candidates = [tuple(source for source in values if row in candidates[source])
                      for row, values in enumerate(candidates)]
    return candidates, tolerance


def object_mirror_candidates(obj, *, tolerance=None):
    """Match undeformed mesh coordinates; poses and ShapeKeys never move pairs."""
    from .rwt_bridge import np_module
    np_module()
    reference = getattr(getattr(obj.data, 'shape_keys', None), 'reference_key', None)
    vertices = reference.data if reference is not None else obj.data.vertices
    coordinates = np.array([tuple(vertex.co) for vertex in vertices], dtype=float)
    edges = [tuple(edge.vertices) for edge in getattr(obj.data, 'edges', ())]
    candidates, tolerance = mirror_candidates(coordinates, edges, tolerance=tolerance)
    return coordinates, candidates, tolerance


def mirrored_values(values, candidates, *, fallback=None):
    """Copy equal candidate values exactly and preserve unresolved destinations."""
    values = np.asarray(values)
    result = np.zeros_like(values) if fallback is None else np.array(fallback, dtype=values.dtype, copy=True)
    sources = np.full(len(values), -1, dtype=int)
    ambiguous = []
    for row, choices in enumerate(candidates):
        if not choices:
            continue
        samples = values[list(choices)]
        if not np.all(samples == samples[0]):
            ambiguous.append(row)
            continue
        result[row] = samples[0]
        sources[row] = choices[0]
    return result, sources, ambiguous
