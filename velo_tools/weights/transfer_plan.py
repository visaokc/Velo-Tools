"""Blender adapter for sampling-independent, receiving-first allocation."""

import numpy as np

from .allocation import EPSILON, plan_weights


def _mesh_graph(obj):
    from scipy import sparse
    edges = np.empty((len(obj.data.edges), 2), dtype=np.int32)
    obj.data.edges.foreach_get('vertices', edges.ravel())
    return sparse.csr_matrix(
        (np.ones(len(edges)), (edges[:, 0], edges[:, 1])),
        shape=(len(obj.data.vertices), len(obj.data.vertices)),
    )


def supported_field(obj, weights, matched, seeds):
    """Reject positive extrapolation components with no positive source seed."""
    from scipy import sparse
    values = np.clip(np.asarray(weights).reshape(-1), 0., 1.)
    positive = np.flatnonzero(values >= EPSILON)
    if not len(positive):
        return values
    graph = _mesh_graph(obj)
    count, labels = sparse.csgraph.connected_components(graph[positive][:, positive], directed=False)
    trusted = np.asarray(matched)[positive] & (np.asarray(seeds).reshape(-1)[positive] >= EPSILON)
    anchored = np.zeros(count, dtype=bool)
    anchored[labels[trusted]] = True
    values[positive[~anchored[labels]]] = 0.
    return values


def mirrored_field(obj, weights):
    """Share one primary value across reciprocal, coincident mirror buckets."""
    from mathutils import Vector, kdtree
    from . import algorithms
    tolerance = max(algorithms._bbox_diagonal_local(obj) * .0001, .000001)
    buckets = list(algorithms._bucket_vertices_by_coordinate(obj, tolerance).values())
    centers = [sum((obj.data.vertices[i].co for i in bucket), Vector()) / len(bucket) for bucket in buckets]
    values = [float(np.mean(np.asarray(weights)[bucket])) for bucket in buckets]
    tree = kdtree.KDTree(len(buckets))
    for i, center in enumerate(centers):
        tree.insert(center, i)
    tree.balance()
    partners = np.full(len(buckets), -1, dtype=int)
    for i, center in enumerate(centers):
        _, partner, distance = tree.find(Vector((-center.x, center.y, center.z)))
        if partner is not None and distance <= tolerance:
            partners[i] = partner
    proposed = np.zeros((len(weights), 2))
    labels = np.arange(len(weights) * 2).reshape(-1, 2) + len(buckets)
    matched_vertices = 0
    coincident_buckets = 0
    unmatched_vertices = 0
    for i, bucket in enumerate(buckets):
        proposed[bucket, 0] = values[i]
        labels[bucket, 0] = i
        partner = partners[i]
        if partner >= 0 and partners[partner] == i:
            proposed[bucket, 1] = values[partner]
            labels[bucket, 1] = partner
            if values[partner] >= EPSILON:
                matched_vertices += len(bucket)
                coincident_buckets += int(len(bucket) > 1)
        elif values[i] >= EPSILON:
            unmatched_vertices += len(bucket)
    return proposed, labels, {
        'matched_vertices': matched_vertices,
        'coincident_buckets': coincident_buckets,
        'unmatched_vertices': unmatched_vertices,
    }


def commit_transfer(obj, groups, proposed, settings, *, labels=None):
    """Preflight all rows, apply only the diff, then verify the complete result."""
    from . import algorithms, rwt_bridge as bridge
    ordinary = [g for g in obj.vertex_groups if not algorithms.is_special_vg_name(g.name)]
    current = bridge.get_groups_arr(obj, [g.index for g in ordinary])
    authority = [ordinary.index(g) for g in groups]
    locked = np.array([g.lock_weight for g in ordinary])
    planned, affected, info = plan_weights(current, proposed, authority, locked, labels=labels)
    # Do not materialize or erase unchanged zero memberships. The surrounding
    # operator owns exact membership rollback, including any created groups.
    for column, group in enumerate(ordinary):
        changed = np.flatnonzero(planned[:, column] != current[:, column])
        if not len(changed):
            continue
        if group.lock_weight:
            raise ValueError('Receiving groups must be unlocked')
        for start in range(0, len(changed), 10000):
            group.remove(changed[start:start + 10000].tolist())
        for row in changed:
            value = float(planned[row, column])
            if value > 0.:
                group.add([int(row)], value, 'REPLACE')
    actual = bridge.get_groups_arr(obj, [g.index for g in ordinary])
    if not np.array_equal(actual, planned):
        raise ValueError('Committed weights do not match the complete transfer plan')
    if not np.array_equal(actual[:, locked], current[:, locked]):
        raise ValueError('Locked weight verification failed')
    if not np.array_equal(actual[~affected], current[~affected]):
        raise ValueError('Untouched weight verification failed')
    return info
