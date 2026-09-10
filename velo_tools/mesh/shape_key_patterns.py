"""Conservative, reference-free planning for repeated material-piece offsets.

Equality is evidence of a repeated field, not proof of authoring intent. Only
complete material regions are matched. A distinct, self-contained source must
survive every accepted repair; ambiguous patterns never produce edits.
"""

from dataclasses import dataclass
from hashlib import blake2b

import numpy as np


@dataclass
class CleanupPlan:
    edits: dict
    sources: tuple
    repeated_patterns: int
    skipped_patterns: int

    @property
    def vertex_edits(self):
        return sum(len(indices) for indices in self.edits.values())


def material_regions(vertex_count, loop_vertices, loop_materials):
    """Return disjoint full-material regions, coalescing shared boundaries.

    Splitting a shared boundary into smaller voting regions would weaken the
    whole-piece evidence. Merge its materials instead, even if detection then
    becomes impossible. Loose vertices form a separate region.
    """
    vertices = np.asarray(loop_vertices, dtype=np.int64)
    materials = np.asarray(loop_materials, dtype=np.int64)
    if vertices.ndim != 1 or vertices.shape != materials.shape:
        raise ValueError("Invalid material incidence arrays")
    if vertex_count < 0 or np.any(vertices < 0) or np.any(vertices >= vertex_count):
        raise ValueError("Invalid material vertex index")
    groups = [np.unique(vertices[materials == m]) for m in np.unique(materials)]
    parents = list(range(len(groups)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    owners = np.full(vertex_count, -1, dtype=np.int64)
    for index, indices in enumerate(groups):
        for previous in np.unique(owners[indices]):
            if previous >= 0:
                parents[root(int(previous))] = root(index)
        owners[indices] = index
    labels = np.full(vertex_count, -1, dtype=np.int64)
    for index, indices in enumerate(groups):
        labels[indices] = root(index)
    return tuple(np.flatnonzero(labels == label) for label in np.unique(labels))


def _digest(coordinates):
    return blake2b(coordinates.tobytes(), digest_size=20).digest()


def _source_matches(delta, pattern, epsilon, relative_error):
    """Use near-equality only to PROTECT a slightly edited source, never to erase."""
    delta = np.asarray(delta, dtype=np.float64)
    pattern = np.asarray(pattern, dtype=np.float64)
    if not np.isfinite(delta).all() or not np.isfinite(pattern).all():
        return False
    moved = np.any(np.abs(pattern) > epsilon, axis=1)
    if np.any(np.abs(delta[~moved]) > epsilon):
        return False
    error = np.linalg.norm((delta - pattern).ravel())
    return bool(error <= relative_error * np.linalg.norm(pattern.ravel()))


def plan_cleanup(coordinates, regions, *, epsilon=1e-6,
                 minimum_repeats=3, source_relative_error=0.05):
    """Plan exact Basis resets, retaining all source candidates unchanged.

    A source has no independent displacement outside repeated fields. Every
    nonzero region of that key must match a repeated field (or its small edited
    variant). Multiple distinct sources for one field are ambiguous. Identical
    duplicate sources are all protected, and duplicate targets do not inflate
    the required number of independently shaped recipients.

    Intentional, identical reuse of a complete source field in otherwise distinct
    composite keys cannot be distinguished from missing-key fill without author
    knowledge. This conservative heuristic is not a universal corruption test.
    """
    coordinates = np.asarray(coordinates)
    if (coordinates.ndim != 3 or coordinates.shape[2] != 3
            or not np.issubdtype(coordinates.dtype, np.floating)
            or not np.isfinite(coordinates).all()):
        raise ValueError("Expected finite floating-point ShapeKey coordinates")
    if epsilon <= 0 or minimum_repeats < 3 or not 0 <= source_relative_error <= 0.05:
        raise ValueError("Invalid conservative detection limits")
    key_count, vertex_count, _ = coordinates.shape
    regions = tuple(np.asarray(ids, dtype=np.int64) for ids in regions)
    coverage = np.zeros(vertex_count, dtype=np.int32)
    for ids in regions:
        if ids.ndim != 1 or len(np.unique(ids)) != len(ids):
            raise ValueError("Region vertices must be unique")
        if np.any(ids < 0) or np.any(ids >= vertex_count):
            raise ValueError("Invalid region vertex index")
        coverage[ids] += 1
    if np.any(coverage != 1):
        raise ValueError("Regions must partition every vertex exactly once")
    if key_count < 4 or vertex_count == 0:
        return CleanupPlan({}, (), 0, 0)

    identities = [_digest(key) for key in coordinates]
    patterns = []
    by_region = []
    nonzero = np.zeros((key_count, len(regions)), dtype=bool)
    for region_index, ids in enumerate(regions):
        base = coordinates[0, ids]
        groups = {}
        for key in range(1, key_count):
            field = coordinates[key, ids]
            nonzero[key, region_index] = bool(np.any(np.abs(field - base) > epsilon))
            if nonzero[key, region_index]:
                groups.setdefault(_digest(field), []).append(key)
        region_patterns = []
        for members in groups.values():
            if len({identities[key] for key in members}) < minimum_repeats:
                continue
            exemplar = coordinates[members[0], ids]
            # Hashing only indexes candidates; mutation eligibility uses equality.
            members = [key for key in members
                       if np.array_equal(coordinates[key, ids], exemplar)]
            if len({identities[key] for key in members}) < minimum_repeats:
                continue
            region_patterns.append(len(patterns))
            patterns.append({"region": region_index, "members": members,
                             "delta": exemplar - base, "owners": []})
        by_region.append(region_patterns)

    sources = set()
    for key in range(1, key_count):
        owned = []
        for region_index in np.flatnonzero(nonzero[key]):
            ids = regions[region_index]
            delta = coordinates[key, ids] - coordinates[0, ids]
            matches = [index for index in by_region[region_index]
                       if _source_matches(delta, patterns[index]["delta"],
                                          epsilon, source_relative_error)]
            if len(matches) != 1:
                break
            owned.extend(matches)
        else:
            if owned:
                sources.add(key)
                for index in owned:
                    patterns[index]["owners"].append(key)

    edits = {}
    skipped = 0
    for pattern in patterns:
        owners = pattern["owners"]
        if len({identities[key] for key in owners}) != 1:
            skipped += 1
            continue
        targets = [key for key in pattern["members"] if key not in sources]
        if len({identities[key] for key in targets}) < 2:
            skipped += 1
            continue
        ids = regions[pattern["region"]]
        for key in targets:
            changed = np.any(coordinates[key, ids] != coordinates[0, ids], axis=1)
            edits.setdefault(key, []).append(ids[changed])
    edits = {key: np.unique(np.concatenate(chunks)) for key, chunks in edits.items()}
    return CleanupPlan(edits, tuple(sorted(sources)), len(patterns), skipped)
