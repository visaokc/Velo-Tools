"""Shared, preflighted weight allocation for surface and Robust transfers."""

import numpy as np


EPSILON = 1e-8
TOLERANCE = 1e-5


def plan_weights(current, proposed, authority, locked, *, labels=None,
                 max_groups=None, normalize=True, secondary_hints=None):
    current = np.asarray(current)
    proposed = np.asarray(proposed, dtype=np.float64).copy()
    authority = np.asarray(authority, dtype=int)
    locked = np.asarray(locked, dtype=bool)
    if not np.isfinite(current).all() or not np.isfinite(proposed).all():
        raise ValueError('Transfer contains non-finite weights')
    if np.any(locked[authority]):
        raise ValueError('Receiving groups must be unlocked')
    proposed = np.clip(proposed, 0., 1.)
    proposed[proposed < EPSILON] = 0.
    requested = proposed.copy()
    affected = np.any(current[:, authority] > 0., axis=1) | np.any(proposed > 0., axis=1)
    rows = np.flatnonzero(affected)
    secondary = np.flatnonzero(~locked & ~np.isin(np.arange(current.shape[1]), authority))
    locked_sum = current[:, locked].sum(axis=1, dtype=np.float64)
    if normalize and np.any(locked_sum[rows] > 1. + TOLERANCE):
        raise ValueError('Locked weights already exceed one; unlock the conflicting groups first')
    available = np.maximum(0., 1. - locked_sum)
    labels = np.arange(proposed.size).reshape(proposed.shape) if labels is None else np.asarray(labels)
    scale = np.ones(int(labels.max()) + 1)
    slots = np.full(len(current), current.shape[1], dtype=int)
    if max_groups is not None:
        if np.any(np.count_nonzero(current[rows][:, locked] > EPSILON, axis=1) > max_groups):
            raise ValueError('Locked groups already exceed the group limit')
        slots = np.maximum(0, int(max_groups) - np.count_nonzero(current[:, locked] > EPSILON, axis=1))
        for row in rows:
            active = np.flatnonzero(proposed[row] > 0.)
            if len(active) > slots[row]:
                order = sorted(active, key=lambda col: (-proposed[row, col], labels[row, col]))
                scale[labels[row, order[slots[row]:]]] = 0.
    proposed *= scale[labels]
    if normalize and max_groups is not None:
        totals = proposed.sum(axis=1)
        full = affected & (slots > 0) & (np.count_nonzero(proposed > 0., axis=1) == slots)
        full &= (totals > 0.) & (available > totals + TOLERANCE)
        expansion = np.ones(len(scale))
        for row in np.flatnonzero(full):
            active = proposed[row] > 0.
            np.maximum.at(expansion, labels[row, active], available[row] / totals[row])
        proposed *= expansion[labels]
    if normalize:
        totals = proposed.sum(axis=1)
        ratios = np.minimum(1., np.divide(available, totals, out=np.ones_like(totals), where=totals > 0.))
        # A shared label is one mirrored value. Apply the tightest budget to
        # every occurrence, never independently normalize the two sides.
        np.minimum.at(scale, labels.ravel(), np.repeat(ratios, proposed.shape[1]))
        proposed *= scale[labels]
    proposed[proposed < EPSILON] = 0.
    result = current.copy()
    result[:, authority] = proposed
    inferred = 0
    for row in rows:
        remainder = max(0., available[row] - proposed[row].sum())
        values = np.asarray(current[row, secondary], dtype=np.float64).copy()
        values[values < EPSILON] = 0.
        if normalize and remainder > TOLERANCE and not np.any(values):
            if secondary_hints is not None:
                values = np.asarray(secondary_hints[row], dtype=np.float64).copy()
                values[values < EPSILON] = 0.
            if not np.any(values):
                raise ValueError('No unlocked remainder evidence is available; choose an unlocked donor')
            inferred += 1
        if max_groups is not None:
            keep = max(0, slots[row] - np.count_nonzero(proposed[row] > 0.))
            order = np.argsort(-values, kind='stable')
            values[order[keep:]] = 0.
        if normalize:
            total = values.sum()
            if remainder > TOLERANCE and total <= 0.:
                raise ValueError('Locked groups and the group limit leave no slot for the remaining weight')
            values *= remainder / total if total > 0. else 0.
        result[row, secondary] = values
    if normalize and np.any(np.abs(result[rows].sum(axis=1, dtype=np.float64) - 1.) > TOLERANCE):
        raise ValueError('Transfer normalization verification failed')
    return result, affected, {
        'capacity_adjusted': int(np.count_nonzero(np.any(np.abs(proposed - requested) > TOLERANCE, axis=1))),
        'inferred_remainder': inferred,
        'limited_vertices': int(np.count_nonzero(np.count_nonzero(result[rows] > EPSILON, axis=1)
                                                < np.count_nonzero(current[rows] > EPSILON, axis=1))) if max_groups is not None else 0,
    }


def supported_field(obj, weights, matched, seeds):
    """Reject positive extrapolation components with no positive source seed."""
    from scipy import sparse
    values = np.clip(np.asarray(weights).reshape(-1), 0., 1.)
    positive = np.flatnonzero(values >= EPSILON)
    if not len(positive):
        return values
    edges = np.empty((len(obj.data.edges), 2), dtype=np.int32)
    obj.data.edges.foreach_get('vertices', edges.ravel())
    graph = sparse.csr_matrix(
        (np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(len(values), len(values)),
    )
    count, labels = sparse.csgraph.connected_components(graph[positive][:, positive], directed=False)
    trusted = np.asarray(matched)[positive] & (np.asarray(seeds).reshape(-1)[positive] >= EPSILON)
    anchored = np.zeros(count, dtype=bool)
    anchored[labels[trusted]] = True
    values[positive[~anchored[labels]]] = 0.
    return values


def mirrored_field(obj, weights):
    from mathutils import Vector, kdtree
    from . import algorithms as algorithms
    tolerance = max(algorithms._bbox_diagonal_local(obj) * .0001, .000001)
    buckets = list(algorithms._bucket_vertices_by_coordinate(obj, tolerance).values())
    centers = [sum((obj.data.vertices[i].co for i in bucket), Vector()) / len(bucket) for bucket in buckets]
    values = [float(np.mean(np.asarray(weights)[bucket])) for bucket in buckets]
    tree = kdtree.KDTree(len(buckets))
    for i, center in enumerate(centers):
        tree.insert(center, i)
    tree.balance()
    proposed = np.zeros((len(weights), 2))
    labels = np.arange(len(weights) * 2).reshape(-1, 2) + len(buckets)
    matched_vertices = 0
    coincident_buckets = 0
    for i, bucket in enumerate(buckets):
        proposed[bucket, 0] = values[i]
        labels[bucket, 0] = i
        center = centers[i]
        _, partner, distance = tree.find(Vector((-center.x, center.y, center.z)))
        if partner is not None and distance <= tolerance:
            proposed[bucket, 1] = values[partner]
            labels[bucket, 1] = partner
            if values[partner] >= EPSILON:
                matched_vertices += len(bucket)
                coincident_buckets += int(len(bucket) > 1)
    return proposed, labels, {'matched_vertices': matched_vertices, 'coincident_buckets': coincident_buckets}


def commit_transfer(obj, groups, proposed, settings, *, labels=None, normalize=True, limit=True):
    from . import algorithms as algorithms, rwt_bridge as bridge, props
    ordinary = [g for g in obj.vertex_groups if not algorithms.is_special_vg_name(g.name)]
    current = bridge.get_groups_arr(obj, [g.index for g in ordinary])
    authority = [ordinary.index(g) for g in groups]
    locked = np.array([g.lock_weight for g in ordinary])
    secondary = np.flatnonzero(~locked & ~np.isin(np.arange(len(ordinary)), authority))
    hints = None
    if normalize and len(secondary):
        evidence = np.any(current[:, secondary] >= EPSILON, axis=1)
        affected = np.any(current[:, authority] > 0., axis=1) | np.any(proposed > 0., axis=1)
        missing = affected & ~evidence
        if np.any(missing) and np.any(evidence):
            from mathutils import kdtree
            preferred_names = {name for pair in props.selected_donor_pairs(settings) for name in pair if name}
            preferred = [i for i in secondary if ordinary[i].name in preferred_names]
            candidates = np.any(current[:, preferred] >= EPSILON, axis=1) if preferred else evidence
            if not np.any(candidates):
                candidates = evidence
            tree = kdtree.KDTree(int(candidates.sum()))
            for i in np.flatnonzero(candidates):
                tree.insert(obj.data.vertices[int(i)].co, int(i))
            tree.balance()
            hints = current[:, secondary].copy()
            for i in np.flatnonzero(missing):
                _, nearest, _ = tree.find(obj.data.vertices[int(i)].co)
                hints[i] = current[nearest, secondary]
    maximum = settings.max_groups_per_vertex if limit and settings.limit_groups_enable else None
    planned, affected, info = plan_weights(
        current, proposed, authority, locked, labels=labels, max_groups=maximum,
        normalize=normalize, secondary_hints=hints,
    )
    # Preflight is complete. Only write changed rows, keeping untouched and
    # locked memberships byte-for-byte intact. The operator owns rollback.
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
    if not np.array_equal(actual[:, locked], current[:, locked]):
        raise ValueError('Locked weight verification failed')
    if not np.array_equal(actual[:, authority], planned[:, authority]):
        raise ValueError('Receiving weight verification failed')
    if not np.array_equal(actual[~affected], current[~affected]):
        raise ValueError('Untouched weight verification failed')
    if normalize and np.any(np.abs(actual[affected].sum(axis=1, dtype=np.float64) - 1.) > TOLERANCE):
        raise ValueError('Transfer normalization verification failed')
    if maximum is not None and np.any(np.count_nonzero(actual[affected] > EPSILON, axis=1) > maximum):
        raise ValueError('Locked groups already exceed the group limit')
    return info
