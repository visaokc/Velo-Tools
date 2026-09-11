"""Transactional selected-pair mirroring that never writes locked groups."""

from . import algorithms
from .symmetry import object_mirror_candidates


def group_mirror_map(context, settings, obj):
    """Resolve existing, reciprocal names without guessing from damaged weights."""
    import bpy

    proposed = {}
    skipped = []
    for group in obj.vertex_groups:
        name = group.name
        if group.lock_weight or algorithms.is_special_vg_name(name):
            continue
        manual = algorithms._manual_mirror_name(settings, name)
        alias = bpy.utils.flip_name(name)
        candidates = algorithms.mirror_name_candidates(name)
        if manual:
            other = manual
        else:
            other = next((candidate for candidate in (alias, *candidates)
                          if candidate and candidate != name and obj.vertex_groups.get(candidate)), '')
            if not other:
                other = algorithms._mmd_profile_mirror_name(context, obj, name)
            if not other and alias == name and not candidates and not name.isdigit():
                other = name
        target = obj.vertex_groups.get(other)
        if target is None or target.lock_weight or algorithms.is_special_vg_name(target.name):
            skipped.append(name)
        else:
            proposed[group.index] = target.index
    mapping = {source: target for source, target in proposed.items() if proposed.get(target) == source}
    skipped.extend(obj.vertex_groups[source].name for source in proposed if source not in mapping)
    return mapping, skipped


def apply_updates(obj, updates):
    """Apply only changed cells and roll back their exact original memberships."""
    import numpy as np

    if getattr(obj.data, 'users', 1) > 1:
        raise ValueError('Selected weight editing requires a single-user mesh')
    originals = {}
    for (row, column), value in updates.items():
        group = obj.vertex_groups[column]
        if group.lock_weight or algorithms.is_special_vg_name(group.name):
            raise ValueError('Locked or special vertex groups cannot be changed')
        if value is not None and (not np.isfinite(value) or not 0. <= value <= 1.):
            raise ValueError('Mirror weights must be finite and between zero and one')
        originals[row, column] = next((item.weight for item in obj.data.vertices[row].groups
                                      if item.group == column), None)
    changes = {key: value for key, value in updates.items() if value != originals[key]}

    def write(key, value):
        row, column = key
        group = obj.vertex_groups[column]
        if value is None:
            group.remove([row])
        else:
            group.add([row], float(value), 'REPLACE')

    try:
        for key, value in changes.items():
            write(key, value)
        for (row, column), value in changes.items():
            actual = next((item.weight for item in obj.data.vertices[row].groups if item.group == column), None)
            expected = None if value is None else float(np.float32(value))
            if actual != expected:
                raise RuntimeError('Selected weight write verification failed')
    except Exception:
        for key in changes:
            write(key, originals[key])
        raise
    return len(changes)


def mirror_selected(context, settings, obj, selected, direction):
    """Selection scopes pairs; direction chooses the read-only authoritative side.

    Either endpoint can be selected, so selecting damaged vertices and choosing
    the healthy-to-damaged direction also works. No normalization is performed.
    """
    if direction not in {'NEGATIVE_TO_POSITIVE', 'POSITIVE_TO_NEGATIVE'}:
        raise ValueError('Unknown weight mirror direction')
    np = algorithms._rwt.np_module()
    coordinates, candidates, tolerance = object_mirror_candidates(obj)
    selected = {int(row) for row in selected}
    source_sign = -1. if direction == 'NEGATIVE_TO_POSITIVE' else 1.
    center_epsilon = max(float(np.linalg.norm(np.ptp(coordinates, axis=0))) * 1e-8, 1e-9)
    source_side = coordinates[:, 0] * source_sign > center_epsilon
    target_side = coordinates[:, 0] * source_sign < -center_epsilon
    mapping, skipped_groups = group_mirror_map(context, settings, obj)
    if not mapping:
        return {'matched_vertices': 0, 'changed_weights': 0,
                'skipped_vertices': len(selected), 'ambiguous_vertices': 0,
                'skipped_groups': len(skipped_groups), 'tolerance': tolerance}
    target_columns = set(mapping.values())
    rows = [{int(item.group): float(item.weight) for item in vertex.groups}
            for vertex in obj.data.vertices]
    updates = {}
    matched = []
    covered = set()
    ambiguous = []
    for target, choices in enumerate(candidates):
        if not target_side[target]:
            continue
        choices = tuple(source for source in choices if source_side[source])
        if target not in selected and not selected.intersection(choices):
            continue
        if not choices:
            continue
        samples = [{mapping[column]: value for column, value in rows[source].items() if column in mapping}
                   for source in choices]
        if any(sample != samples[0] for sample in samples[1:]):
            ambiguous.append(target)
            continue
        wanted = samples[0]
        for column in (rows[target].keys() | wanted.keys()) & target_columns:
            value = wanted.get(column)
            if value != rows[target].get(column):
                updates[target, column] = value
        matched.append(target)
        covered.update(choices)
        covered.add(target)
    changed = apply_updates(obj, updates)
    return {
        'matched_vertices': len(matched),
        'changed_weights': changed,
        'skipped_vertices': len(selected - covered),
        'ambiguous_vertices': len(ambiguous),
        'skipped_groups': len(skipped_groups),
        'tolerance': tolerance,
    }
