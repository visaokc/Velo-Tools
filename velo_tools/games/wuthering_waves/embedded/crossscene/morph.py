"""Canonical ShapeKey matching for self-contained cross-scene aggregates."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

import numpy as np


SIGNAL_EPS = 1e-8
RESIDUAL_MAX = 0.05
STRUCTURAL_SUPPORT_MIN = 8
POSITION_QUANTUM = 1e-6


@dataclass(frozen=True)
class MorphResolution:
    bindings: Dict[int, int]
    unmapped_ids: Tuple[int, ...]
    empty_ids: Tuple[int, ...]
    modal_offset: Optional[int]
    scale: float
    diagnostics: Dict[int, dict]


def _signal_rows(values: np.ndarray) -> np.ndarray:
    return np.any(np.abs(values) > SIGNAL_EPS, axis=1)


def _position_key(component_id: int, position: np.ndarray) -> tuple:
    quantized = np.rint(np.asarray(position) / POSITION_QUANTUM).astype(np.int64)
    return component_id, int(quantized[0]), int(quantized[1]), int(quantized[2])


def resolve_morph_bindings(
        base_deltas: Mapping[int, Mapping[int, np.ndarray]],
        source_deltas: Mapping[int, Mapping[int, np.ndarray]],
        correspondences: Sequence[Tuple[int, int, Sequence[Tuple[int, int]]]],
        base_positions: Optional[Mapping[int, np.ndarray]] = None) -> MorphResolution:
    """Resolve source-local ShapeKey ids to aggregate canonical ids.

    ``correspondences`` contains ``(base_component, source_component, pairs)``
    where each pair is ``(base_vertex_row, source_vertex_row)``. Dense keys
    establish the structural id offset, but sparse keys remain eligible when
    their exact structural candidate matches.
    """
    source_ids = sorted({
        int(key_id)
        for component in source_deltas.values()
        for key_id in component
    })
    base_ids = sorted({
        int(key_id)
        for component in base_deltas.values()
        for key_id in component
    })
    diagnostics: Dict[int, dict] = {}
    candidates: Dict[int, Dict[int, Tuple[float, float]]] = {}
    empty_ids = []

    for source_id in source_ids:
        source_rows = set()
        logical_positions = set()
        y_parts = []
        x_parts = {base_id: [] for base_id in base_ids}
        for base_component, source_component, pairs in correspondences:
            if not pairs:
                continue
            base_component_deltas = base_deltas.get(base_component, {})
            source_component_deltas = source_deltas.get(source_component, {})
            source_array = source_component_deltas.get(source_id)
            if source_array is None:
                source_array = np.zeros(
                    (max((source for _, source in pairs), default=-1) + 1, 3),
                    dtype=np.float32,
                )
            base_rows = np.asarray([int(base) for base, _ in pairs], dtype=np.int64)
            source_vertex_rows = np.asarray(
                [int(source) for _, source in pairs], dtype=np.int64)
            y = np.asarray(source_array[source_vertex_rows, :3], dtype=np.float64)
            y_parts.append(y)
            signal = _signal_rows(y)
            for pair_index in np.flatnonzero(signal):
                source_row = int(source_vertex_rows[pair_index])
                source_rows.add((int(source_component), source_row))
                if base_positions is not None and base_component in base_positions:
                    logical_positions.add(_position_key(
                        int(base_component),
                        base_positions[base_component][int(base_rows[pair_index])],
                    ))
            for base_id in base_ids:
                base_array = base_component_deltas.get(base_id)
                if base_array is None:
                    x_parts[base_id].append(np.zeros_like(y))
                else:
                    x_parts[base_id].append(np.asarray(
                        base_array[base_rows, :3], dtype=np.float64))

        if not y_parts:
            empty_ids.append(source_id)
            diagnostics[source_id] = {
                "raw_rows": 0,
                "unique_positions": 0,
                "classification": "empty",
            }
            continue
        y = np.concatenate(y_parts, axis=0)
        energy = float(np.sum(y * y))
        raw_rows = len(source_rows)
        if energy <= SIGNAL_EPS * SIGNAL_EPS:
            empty_ids.append(source_id)
            diagnostics[source_id] = {
                "raw_rows": raw_rows,
                "unique_positions": len(logical_positions),
                "classification": "empty",
            }
            continue
        candidate_scores: Dict[int, Tuple[float, float]] = {}
        for base_id in base_ids:
            x = np.concatenate(x_parts[base_id], axis=0)
            denominator = float(np.sum(x * x))
            if denominator <= 1e-20:
                continue
            scale = float(np.sum(x * y) / denominator)
            residual = float(np.sum((scale * x - y) ** 2) / energy)
            if np.isfinite(scale) and np.isfinite(residual):
                candidate_scores[base_id] = (residual, scale)
        candidates[source_id] = candidate_scores
        ordered = sorted(candidate_scores.items(), key=lambda item: (item[1][0], item[0]))
        diagnostics[source_id] = {
            "raw_rows": raw_rows,
            "unique_positions": len(logical_positions),
            "best_candidate": ordered[0][0] if ordered else None,
            "best_residual": round(ordered[0][1][0], 12) if ordered else None,
            "best_scale": round(ordered[0][1][1], 9) if ordered else None,
            "qualifying_candidates": [
                base_id for base_id, (residual, _scale) in ordered
                if residual < RESIDUAL_MAX
            ],
        }

    dense_offsets = Counter()
    for source_id, scores in candidates.items():
        diag = diagnostics[source_id]
        if diag["raw_rows"] < STRUCTURAL_SUPPORT_MIN:
            continue
        qualifying = [
            (base_id, score)
            for base_id, score in scores.items()
            if score[0] < RESIDUAL_MAX
        ]
        if not qualifying:
            continue
        best_id, _ = min(qualifying, key=lambda item: (item[1][0], item[0]))
        dense_offsets[best_id - source_id] += max(1, int(diag["raw_rows"]))
    modal_offset = (
        min(dense_offsets, key=lambda offset: (-dense_offsets[offset], offset))
        if dense_offsets else None
    )

    bindings: Dict[int, int] = {}
    scales = []
    for source_id, scores in candidates.items():
        qualifying = {
            base_id: score
            for base_id, score in scores.items()
            if score[0] < RESIDUAL_MAX
        }
        selected = None
        classification = "unmapped"
        if modal_offset is not None:
            structural = source_id + modal_offset
            if structural in qualifying:
                selected = structural
                classification = "structural"
        if selected is None and len(qualifying) == 1:
            selected = next(iter(qualifying))
            classification = "unique"
        if selected is not None:
            bindings[source_id] = selected
            residual, scale = qualifying[selected]
            scales.append(scale)
            diagnostics[source_id].update({
                "canonical_id": selected,
                "residual": round(residual, 12),
                "scale": round(scale, 9),
                "classification": classification,
            })
        else:
            diagnostics[source_id]["classification"] = classification

    unmapped_ids = tuple(sorted(set(candidates) - set(bindings)))
    scale = float(np.median(scales)) if scales else 1.0
    return MorphResolution(
        bindings=bindings,
        unmapped_ids=unmapped_ids,
        empty_ids=tuple(sorted(empty_ids)),
        modal_offset=modal_offset,
        scale=scale,
        diagnostics=diagnostics,
    )


def reproject_unique_key(
        source_key_id: int,
        source_deltas: Mapping[int, Mapping[int, np.ndarray]],
        correspondences: Sequence[Tuple[int, int, Sequence[Tuple[int, int]]]],
        scale: float = 1.0) -> Dict[int, np.ndarray]:
    """Project an IB-local key into aggregate component row space.

    Every non-zero source row must be represented by at least one aggregate
    row. This is the fail-closed guarantee that removes pristine export-time
    fallback payloads.
    """
    if abs(float(scale)) < 1e-12:
        raise ValueError(f"ShapeKey {source_key_id} has an invalid zero scale")
    output: Dict[int, np.ndarray] = {}
    covered_source_rows = set()
    required_source_rows = set()

    for source_component, keys in source_deltas.items():
        values = keys.get(source_key_id)
        if values is None:
            continue
        for row in np.flatnonzero(_signal_rows(np.asarray(values)[:, :3])):
            required_source_rows.add((int(source_component), int(row)))

    for base_component, source_component, pairs in correspondences:
        source_values = source_deltas.get(source_component, {}).get(source_key_id)
        if source_values is None or not pairs:
            continue
        size = max((int(base) for base, _ in pairs), default=-1) + 1
        projected = output.setdefault(
            int(base_component), np.zeros((size, 3), dtype=np.float32))
        if len(projected) < size:
            grown = np.zeros((size, 3), dtype=np.float32)
            grown[:len(projected)] = projected
            projected = output[int(base_component)] = grown
        for base_row, source_row in pairs:
            delta = np.asarray(source_values[int(source_row), :3], dtype=np.float32)
            if np.any(np.abs(delta) > SIGNAL_EPS):
                covered_source_rows.add((int(source_component), int(source_row)))
            existing = projected[int(base_row)]
            if (np.any(np.abs(existing) > SIGNAL_EPS)
                    and not np.allclose(existing, delta / float(scale),
                                        rtol=1e-5, atol=1e-7)):
                raise ValueError(
                    f"ShapeKey {source_key_id} maps conflicting source rows "
                    f"onto aggregate Component {base_component} vertex {base_row}")
            projected[int(base_row)] = delta / float(scale)

    missing = sorted(required_source_rows - covered_source_rows)
    if missing:
        preview = ", ".join(f"C{component}:v{row}" for component, row in missing[:8])
        suffix = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
        raise ValueError(
            f"ShapeKey {source_key_id} has non-zero source rows outside fold "
            f"correspondence: {preview}{suffix}")
    return output
