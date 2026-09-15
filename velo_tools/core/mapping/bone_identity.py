"""Resolve cross-component bone-name evidence for stable runtime identities."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Hashable, Iterable


class BoneIdentityConflict(ValueError):
    """Raised when component evidence cannot identify one runtime bone safely."""


@dataclass(frozen=True)
class BoneNameEvidence:
    runtime_id: int
    bone_name: str
    source: Hashable
    support: int = 0


def resolve_runtime_bone_names(
    evidence: Iterable[BoneNameEvidence],
) -> dict[int, str]:
    """Validate consistent names without turning majority votes into evidence.

    Identity is established by full-weight matching before this check. Neither
    source count nor point count may rewrite a conflicting local bone name.
    """
    by_runtime: dict[int, dict[Hashable, BoneNameEvidence]] = defaultdict(dict)
    for row in evidence:
        runtime_id = int(row.runtime_id)
        bone_name = str(row.bone_name).strip()
        if not bone_name:
            raise BoneIdentityConflict(
                f"Runtime bone {runtime_id} has an empty bone name"
            )
        current = by_runtime[runtime_id].get(row.source)
        normalized = BoneNameEvidence(
            runtime_id=runtime_id,
            bone_name=bone_name,
            source=row.source,
            support=max(0, int(row.support)),
        )
        if current is None:
            by_runtime[runtime_id][row.source] = normalized
            continue
        if current.bone_name != bone_name:
            raise BoneIdentityConflict(
                f"Runtime bone {runtime_id} has conflicting names from "
                f"source {row.source!r}: {current.bone_name!r}, {bone_name!r}"
            )
        by_runtime[runtime_id][row.source] = BoneNameEvidence(
            runtime_id=runtime_id,
            bone_name=bone_name,
            source=row.source,
            support=current.support + normalized.support,
        )

    resolved = {}
    for runtime_id, rows_by_source in sorted(by_runtime.items()):
        votes = Counter(row.bone_name for row in rows_by_source.values())
        if len(votes) == 1:
            resolved[runtime_id] = next(iter(votes))
            continue
        details = ", ".join(sorted(votes))
        raise BoneIdentityConflict(
            f"Runtime bone {runtime_id} has conflicting names: {details}. "
            "Regenerate the mapping from the original unpacked skin data."
        )
    return resolved
