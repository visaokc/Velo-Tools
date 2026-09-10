"""Write-free planning for source-faithful, unnormalized transfer staging."""

import numpy as np


EPSILON = 1e-8


def plan_weights(current, proposed, authority, locked, *, labels=None):
    """Replace only receiving columns; totals and influence counts are irrelevant.

    Locked recipients are rejected, but other locked groups impose no budget.
    Normalization and influence limiting belong to explicit user cleanup, not
    to staging. Every non-receiving membership remains owned by the user.
    """
    current = np.asarray(current)
    requested = np.asarray(proposed, dtype=np.float64)
    indices = np.asarray(authority)
    locked = np.asarray(locked, dtype=bool)
    if current.ndim != 2 or not np.issubdtype(current.dtype, np.floating):
        raise ValueError('Current weights must be a floating-point matrix')
    if (indices.ndim != 1 or not len(indices)
            or not np.issubdtype(indices.dtype, np.integer)
            or len(np.unique(indices)) != len(indices)
            or np.any(indices < 0) or np.any(indices >= current.shape[1])):
        raise ValueError('Receiving group indices must be unique and valid')
    if requested.shape != (len(current), len(indices)) or locked.shape != (current.shape[1],):
        raise ValueError('Transfer matrix dimensions do not match')
    if not np.isfinite(current).all() or not np.isfinite(requested).all():
        raise ValueError('Transfer contains non-finite weights')
    if np.any(locked[indices]):
        raise ValueError('Receiving groups must be unlocked')
    # Engine output is bounded per influence, never by the other groups' sum.
    receiving = np.clip(requested, 0., 1.)
    if labels is not None:
        labels = np.asarray(labels)
        if labels.shape != receiving.shape or not np.issubdtype(labels.dtype, np.integer):
            raise ValueError('Mirror labels must match the receiving matrix')
        _, inverse = np.unique(labels, return_inverse=True)
        if receiving.size:
            low = np.full(int(inverse.max()) + 1, np.inf)
            high = np.full(len(low), -np.inf)
            np.minimum.at(low, inverse.ravel(), receiving.ravel())
            np.maximum.at(high, inverse.ravel(), receiving.ravel())
            if np.any(high != low):
                raise ValueError('A shared mirror label must have one receiving value')
    result = current.copy()
    result[:, indices] = receiving
    affected = np.any(current[:, indices] != result[:, indices], axis=1)
    return result, affected, {
        'changed_vertices': int(np.count_nonzero(affected)),
        'receiving_vertices': int(np.count_nonzero(np.any(receiving > 0., axis=1))),
    }
