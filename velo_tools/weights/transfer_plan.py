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


def mirrored_field(obj, weights, *, mirror_weights=None):
    """Preserve the primary field and copy only resolved, layer-safe mirrors."""
    from .symmetry import object_mirror_candidates, mirrored_values
    weights = np.asarray(weights).reshape(-1)
    _, candidates, _ = object_mirror_candidates(obj)
    mirrored, sources, ambiguous = mirrored_values(weights, candidates, fallback=mirror_weights)
    proposed = np.column_stack((weights, mirrored))
    labels = np.column_stack((np.arange(len(weights)), np.arange(len(weights), 2 * len(weights))))
    resolved = sources >= 0
    labels[resolved, 1] = sources[resolved]
    return proposed, labels, {
        'matched_vertices': int(np.count_nonzero(resolved & (mirrored >= EPSILON))),
        'coincident_buckets': 0,
        'unmatched_vertices': int(np.count_nonzero(~resolved & (weights >= EPSILON))),
        'ambiguous_vertices': len(ambiguous),
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
