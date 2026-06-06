"""Bone matrix-signature helpers for Velo VertexGroupMap generation."""

from __future__ import annotations

import logging
import math
import heapq
from dataclasses import dataclass
from typing import Optional

import numpy

log = logging.getLogger(__name__)


_DISCONTINUITY_RATIO = 10.0


@dataclass
class _BoneSignatureRecord:
    component_id: int
    component: "MeshComponent"
    local_bone: int
    global_id: int
    signature: bytes
    matrix_values: numpy.ndarray


@dataclass(frozen=True)
class _MatrixDistance:
    max_abs: float
    rms: float
    l1: float
    nonzero_count: int

    def sort_key(self) -> tuple[float, float, float, int]:
        return (self.max_abs, self.rms, self.l1, self.nonzero_count)


@dataclass(frozen=True)
class _NoiseEnvelope:
    max_abs: float
    sample_count: int
    gap_ratio: float
    next_max_abs: float | None


@dataclass
class _AtlasBone:
    global_id: int
    records: list[_BoneSignatureRecord]
    signatures: set[bytes]

    @classmethod
    def from_record(cls, global_id: int, record: _BoneSignatureRecord) -> "_AtlasBone":
        return cls(global_id=global_id, records=[record], signatures={record.signature})

    def add_record(self, record: _BoneSignatureRecord) -> None:
        self.records.append(record)
        self.signatures.add(record.signature)

    def has_exact_signature(self, signature: bytes) -> bool:
        return signature in self.signatures

    def best_distance_to(self, record: _BoneSignatureRecord) -> _MatrixDistance:
        return min(
            (_matrix_distance(record.matrix_values, member.matrix_values) for member in self.records),
            key=lambda distance: distance.sort_key(),
            default=_infinite_matrix_distance(),
        )


@dataclass(frozen=True)
class _AssignmentCandidate:
    local_index: int
    global_id: int
    distance: _MatrixDistance
    exact: bool

    def scalar_cost(self) -> float:
        distance = self.distance
        return (
            distance.max_abs
            + distance.rms * 1.0e-3
            + distance.l1 * 1.0e-6
            + float(distance.nonzero_count) * 1.0e-12
            + float(self.global_id) * 1.0e-18
        )

    def sort_key(self) -> tuple[float, float, float, int, int, int]:
        return (*self.distance.sort_key(), self.local_index, self.global_id)


def _compute_bone_count(vb: "NumpyBuffer") -> int:
    """Infer local bone count from the merged vertex buffer's blendindices field."""
    semantic_name = "BLENDINDICES"
    try:
        blendindices = vb.get_field(semantic_name)
    except Exception:
        return 0
    if blendindices is None or len(blendindices) == 0:
        return 0
    try:
        return int(blendindices.max()) + 1
    except Exception:
        return 0


def _signature_matrix_values(signature: bytes) -> numpy.ndarray:
    if len(signature) % 4 != 0:
        return numpy.zeros((0,), dtype=numpy.float32)
    return numpy.frombuffer(signature, dtype="<f4").astype(numpy.float32, copy=True)


def _matrix_max_abs_difference(a: numpy.ndarray, b: numpy.ndarray) -> float:
    return _matrix_distance(a, b).max_abs


def _infinite_matrix_distance() -> _MatrixDistance:
    return _MatrixDistance(math.inf, math.inf, math.inf, 0)


def _matrix_distance(a: numpy.ndarray, b: numpy.ndarray) -> _MatrixDistance:
    if a.shape != b.shape or a.size == 0:
        return _infinite_matrix_distance()
    diff = numpy.abs(a - b)
    if numpy.isnan(diff).any():
        return _infinite_matrix_distance()
    max_abs = float(diff.max(initial=0.0))
    l1 = float(diff.sum(initial=0.0))
    rms = float(math.sqrt(float(numpy.mean(numpy.square(diff, dtype=numpy.float64)))))
    nonzero_count = int(numpy.count_nonzero(diff))
    return _MatrixDistance(max_abs=max_abs, rms=rms, l1=l1, nonzero_count=nonzero_count)


def _learn_matrix_noise_envelope(
    records: list[_BoneSignatureRecord],
    object_id: Optional[str],
) -> _NoiseEnvelope | None:
    if len(records) < 3:
        return None

    nearest_by_index: dict[int, tuple[int, _MatrixDistance]] = {}
    for left_index, left in enumerate(records):
        best_index: int | None = None
        best_distance = _infinite_matrix_distance()
        for right_index, right in enumerate(records):
            if left_index == right_index or left.component_id == right.component_id:
                continue
            distance = _matrix_distance(left.matrix_values, right.matrix_values)
            if distance.sort_key() < best_distance.sort_key():
                best_index = right_index
                best_distance = distance
        if best_index is not None and math.isfinite(best_distance.max_abs):
            nearest_by_index[left_index] = (best_index, best_distance)

    nearest_pairs: dict[tuple[int, int], tuple[_MatrixDistance, bool]] = {}
    for left_index, (right_index, distance) in nearest_by_index.items():
        pair_key = (min(left_index, right_index), max(left_index, right_index))
        reciprocal = nearest_by_index.get(right_index, (None, None))[0] == left_index
        current = nearest_pairs.get(pair_key)
        if current is None:
            nearest_pairs[pair_key] = (distance, reciprocal)
        elif distance.sort_key() < current[0].sort_key():
            nearest_pairs[pair_key] = (distance, reciprocal or current[1])
        elif reciprocal and not current[1]:
            nearest_pairs[pair_key] = (current[0], True)

    all_candidates = [
        (distance, reciprocal)
        for distance, reciprocal in nearest_pairs.values()
        if distance.max_abs > 0.0 and math.isfinite(distance.max_abs)
    ]
    reciprocal_candidates = [
        (distance, reciprocal)
        for distance, reciprocal in all_candidates
        if reciprocal
    ]

    envelopes = [
        envelope
        for envelope in (
            _learn_envelope_from_candidates(reciprocal_candidates, object_id, "reciprocal"),
            _learn_envelope_from_candidates(all_candidates, object_id, "nearest"),
        )
        if envelope is not None
    ]
    if envelopes:
        return max(envelopes, key=lambda item: item.max_abs)

    log.info(
        "No clear VG matrix noise discontinuity for %s; non-exact matrix aliases disabled",
        object_id,
    )
    return None


def _learn_envelope_from_candidates(
    candidates: list[tuple[_MatrixDistance, bool]],
    object_id: Optional[str],
    source_label: str,
) -> _NoiseEnvelope | None:
    if len(candidates) < 2:
        return None

    candidates = sorted(candidates, key=lambda item: item[0].sort_key())
    best_split: int | None = None
    best_ratio = 0.0
    for index in range(len(candidates) - 1):
        left = candidates[index][0].max_abs
        right = candidates[index + 1][0].max_abs
        if left <= 0.0:
            continue
        ratio = right / left
        if ratio > best_ratio:
            best_ratio = ratio
            best_split = index

    if best_split is None or best_ratio < _DISCONTINUITY_RATIO:
        return None

    samples = candidates[: best_split + 1]
    if not any(reciprocal for _distance, reciprocal in samples):
        return None

    max_abs = max(distance.max_abs for distance, _reciprocal in samples)
    next_max_abs = candidates[best_split + 1][0].max_abs
    envelope = _NoiseEnvelope(
        max_abs=max_abs,
        sample_count=len(samples),
        gap_ratio=best_ratio,
        next_max_abs=next_max_abs,
    )
    log.info(
        "Learned VG matrix noise envelope for %s: max_abs <= %.12g from %s %s samples; "
        "next %.12g (gap %.3g)",
        object_id,
        envelope.max_abs,
        envelope.sample_count,
        source_label,
        envelope.next_max_abs,
        envelope.gap_ratio,
    )
    return envelope


def _compact_component_vg_maps(components: list["MeshComponent"]) -> bool:
    unique_components: list["MeshComponent"] = []
    seen_component_ids = set()
    for component in components:
        component_identity = id(component)
        if component_identity in seen_component_ids:
            continue
        seen_component_ids.add(component_identity)
        unique_components.append(component)

    used_global_ids = sorted(
        {
            int(global_id)
            for component in unique_components
            for global_id in (component.vg_map or {}).values()
        }
    )
    if not used_global_ids:
        return False
    compact_id_by_global_id = {global_id: index for index, global_id in enumerate(used_global_ids)}
    if all(global_id == index for index, global_id in enumerate(used_global_ids)):
        return False
    for component in unique_components:
        component.vg_map = {
            local_bone: compact_id_by_global_id[int(global_id)]
            for local_bone, global_id in (component.vg_map or {}).items()
        }
    return True


def _apply_guarded_near_signature_aliases(records: list[_BoneSignatureRecord], object_id: Optional[str]) -> int:
    if not records:
        return 0

    records_by_component: dict[int, list[_BoneSignatureRecord]] = {}
    for record in records:
        records_by_component.setdefault(record.component_id, []).append(record)
    for component_records in records_by_component.values():
        component_records.sort(key=lambda item: item.local_bone)

    seed_component_id = 0 if records_by_component.get(0) else min(records_by_component)
    if seed_component_id != 0:
        log.warning(
            "Component_0 has no VG matrix signatures for %s; seeding atlas from Component_%s",
            object_id,
            seed_component_id,
        )

    envelope = _learn_matrix_noise_envelope(records, object_id)
    atlas_by_global_id: dict[int, _AtlasBone] = {}
    next_global_id = 0
    aliases_applied = 0

    for component_id in sorted(records_by_component):
        component_records = records_by_component[component_id]
        if component_id == seed_component_id and not atlas_by_global_id:
            for record in component_records:
                assigned_global = next_global_id
                next_global_id += 1
                record.component.vg_map[record.local_bone] = assigned_global
                atlas_by_global_id[assigned_global] = _AtlasBone.from_record(assigned_global, record)
                if assigned_global != int(record.global_id):
                    aliases_applied += 1
            continue

        candidates = _build_atlas_assignment_candidates(component_records, atlas_by_global_id, envelope)
        assignments = _solve_one_to_one_assignments(candidates, len(component_records))

        for local_index, record in enumerate(component_records):
            candidate = assignments.get(local_index)
            if candidate is None:
                assigned_global = next_global_id
                next_global_id += 1
                atlas_by_global_id[assigned_global] = _AtlasBone.from_record(assigned_global, record)
                record.component.vg_map[record.local_bone] = assigned_global
                continue

            assigned_global = candidate.global_id
            atlas_by_global_id[assigned_global].add_record(record)
            record.component.vg_map[record.local_bone] = assigned_global
            if int(record.global_id) != int(assigned_global):
                aliases_applied += 1
            if not candidate.exact:
                log.info(
                    "Applied learned-matrix VG alias for %s: Component_%s:%s -> G%s "
                    "(max %.12g rms %.12g l1 %.12g nz %s)",
                    object_id,
                    record.component_id,
                    record.local_bone,
                    assigned_global,
                    candidate.distance.max_abs,
                    candidate.distance.rms,
                    candidate.distance.l1,
                    candidate.distance.nonzero_count,
                )

    for component_id, component_records in records_by_component.items():
        global_ids = [int(record.component.vg_map[record.local_bone]) for record in component_records]
        if len(global_ids) != len(set(global_ids)):
            log.warning(
                "Velo VG atlas produced duplicate globals inside Component_%s of %s; keeping local ids distinct",
                component_id,
                object_id,
            )
            _split_duplicate_component_globals(component_records, atlas_by_global_id, next_global_id)
            next_global_id = max(atlas_by_global_id) + 1

    if _compact_component_vg_maps([record.component for record in records]):
        log.info("Compacted learned-matrix VG atlas into dense global ids (object %s)", object_id)
    return aliases_applied


def _build_atlas_assignment_candidates(
    component_records: list[_BoneSignatureRecord],
    atlas_by_global_id: dict[int, _AtlasBone],
    envelope: _NoiseEnvelope | None,
) -> list[_AssignmentCandidate]:
    candidates: list[_AssignmentCandidate] = []
    for local_index, record in enumerate(component_records):
        for global_id, atlas_bone in sorted(atlas_by_global_id.items()):
            if atlas_bone.has_exact_signature(record.signature):
                candidates.append(
                    _AssignmentCandidate(
                        local_index=local_index,
                        global_id=global_id,
                        distance=_MatrixDistance(0.0, 0.0, 0.0, 0),
                        exact=True,
                    )
                )
                continue
            if envelope is None:
                continue
            distance = atlas_bone.best_distance_to(record)
            if distance.max_abs <= envelope.max_abs:
                candidates.append(
                    _AssignmentCandidate(
                        local_index=local_index,
                        global_id=global_id,
                        distance=distance,
                        exact=False,
                    )
                )
    candidates.sort(key=lambda item: item.sort_key())
    return candidates


def _solve_one_to_one_assignments(
    candidates: list[_AssignmentCandidate],
    local_count: int,
) -> dict[int, _AssignmentCandidate]:
    del local_count
    if not candidates:
        return {}

    globals_sorted = sorted({candidate.global_id for candidate in candidates})
    global_index_by_id = {global_id: index for index, global_id in enumerate(globals_sorted)}
    local_indices = sorted({candidate.local_index for candidate in candidates})

    source = 0
    local_offset = 1
    global_offset = local_offset + len(local_indices)
    sink = global_offset + len(globals_sorted)
    graph: list[list[_FlowEdge]] = [[] for _ in range(sink + 1)]
    local_node_by_index = {local_index: local_offset + index for index, local_index in enumerate(local_indices)}
    local_index_by_node = {node: local_index for local_index, node in local_node_by_index.items()}
    global_id_by_node = {
        global_offset + index: global_id
        for global_id, index in global_index_by_id.items()
    }

    for local_index in local_indices:
        _add_flow_edge(graph, source, local_node_by_index[local_index], 1, 0.0, None)
    for global_id, global_index in global_index_by_id.items():
        _add_flow_edge(graph, global_offset + global_index, sink, 1, 0.0, None)
    for candidate in sorted(candidates, key=lambda item: item.sort_key()):
        _add_flow_edge(
            graph,
            local_node_by_index[candidate.local_index],
            global_offset + global_index_by_id[candidate.global_id],
            1,
            candidate.scalar_cost(),
            candidate,
        )

    potentials = [0.0 for _ in graph]
    while True:
        dist = [math.inf for _ in graph]
        previous_node = [-1 for _ in graph]
        previous_edge = [-1 for _ in graph]
        dist[source] = 0.0
        queue: list[tuple[float, int]] = [(0.0, source)]
        while queue:
            current_dist, node = heapq.heappop(queue)
            if current_dist > dist[node]:
                continue
            for edge_index, edge in enumerate(graph[node]):
                if edge.capacity <= 0:
                    continue
                reduced_cost = edge.cost + potentials[node] - potentials[edge.to_node]
                if -1.0e-12 < reduced_cost < 0.0:
                    reduced_cost = 0.0
                next_dist = current_dist + reduced_cost
                if next_dist < dist[edge.to_node]:
                    dist[edge.to_node] = next_dist
                    previous_node[edge.to_node] = node
                    previous_edge[edge.to_node] = edge_index
                    heapq.heappush(queue, (next_dist, edge.to_node))

        if not math.isfinite(dist[sink]):
            break
        for node, node_dist in enumerate(dist):
            if math.isfinite(node_dist):
                potentials[node] += node_dist

        node = sink
        while node != source:
            parent = previous_node[node]
            edge_index = previous_edge[node]
            edge = graph[parent][edge_index]
            edge.capacity -= 1
            graph[node][edge.reverse_index].capacity += 1
            node = parent

    assignments: dict[int, _AssignmentCandidate] = {}
    for local_node, local_index in local_index_by_node.items():
        for edge in graph[local_node]:
            if edge.candidate is None or edge.to_node not in global_id_by_node:
                continue
            if edge.capacity == 0:
                assignments[local_index] = edge.candidate
                break
    return assignments


@dataclass
class _FlowEdge:
    to_node: int
    reverse_index: int
    capacity: int
    cost: float
    candidate: _AssignmentCandidate | None


def _add_flow_edge(
    graph: list[list[_FlowEdge]],
    from_node: int,
    to_node: int,
    capacity: int,
    cost: float,
    candidate: _AssignmentCandidate | None,
) -> None:
    forward = _FlowEdge(to_node, len(graph[to_node]), capacity, cost, candidate)
    reverse = _FlowEdge(from_node, len(graph[from_node]), 0, -cost, None)
    graph[from_node].append(forward)
    graph[to_node].append(reverse)


def _split_duplicate_component_globals(
    component_records: list[_BoneSignatureRecord],
    atlas_by_global_id: dict[int, _AtlasBone],
    next_global_id: int,
) -> None:
    seen: set[int] = set()
    for record in sorted(component_records, key=lambda item: item.local_bone):
        global_id = int(record.component.vg_map[record.local_bone])
        if global_id not in seen:
            seen.add(global_id)
            continue
        new_global_id = next_global_id
        next_global_id += 1
        record.component.vg_map[record.local_bone] = new_global_id
        atlas_by_global_id[new_global_id] = _AtlasBone.from_record(new_global_id, record)
