"""Planning and validation for EFMI v1.4 runtime MergedSkeleton output."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import vgmap


EFMI_MERGED_MIN_VERSION = (1, 4, 0)


class MergedRuntimeError(ValueError):
    """Raised when source metadata cannot express a safe merged runtime."""


@dataclass(frozen=True)
class LodRemap:
    lod_id: int
    values: tuple[int, ...]


@dataclass(frozen=True)
class ComponentPlan:
    component_id: int
    vg_offset: int
    vg_count: int
    global_ids: tuple[int, ...]
    global_by_local: tuple[int, ...]
    lod_remaps: tuple[LodRemap, ...]


@dataclass(frozen=True)
class MergedRuntimePlan:
    bones_count: int
    component_count: int
    instance_count: int
    components: tuple[ComponentPlan, ...]

    @property
    def component_offsets(self) -> tuple[int, ...]:
        return tuple(item.vg_offset for item in self.components)

    @property
    def component_counts(self) -> tuple[int, ...]:
        return tuple(item.vg_count for item in self.components)

    @property
    def global_to_runtime(self) -> tuple[int, ...]:
        """Translate authoring global VG ids to EFMI's contiguous runtime slots."""
        result = {}
        for component in self.components:
            for local_id, global_id in enumerate(component.global_by_local):
                result.setdefault(global_id, component.vg_offset + local_id)
        if not result:
            return ()
        return tuple(result.get(index, -1) for index in range(max(result) + 1))


def _int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise MergedRuntimeError(f"Invalid {label}: {value!r}") from exc
    if result < 0:
        raise MergedRuntimeError(f"Invalid {label}: {result}")
    return result


def _local_to_global(component: Any) -> dict[int, int]:
    raw = getattr(component, "vg_map", {}) or {}
    result: dict[int, int] = {}
    for local, global_id in raw.items():
        local_id = _int(local, "local vertex-group id")
        global_id = _int(global_id, "global vertex-group id")
        result[local_id] = global_id
    return result


def _lod_values(component: Any, local_to_global: dict[int, int], lod: Any, lod_id: int) -> LodRemap:
    if lod_id >= 4:
        raise MergedRuntimeError(
            f"Component has LOD {lod_id + 1}; EFMI MergedSkeleton supports at most 4 LOD levels"
        )
    raw = getattr(lod, "vg_map", {}) or {}
    if raw:
        values: dict[int, int] = {}
        for lod_local, component_local in raw.items():
            lod_local_id = _int(lod_local, f"LOD {lod_id + 1} local vertex-group id")
            component_local_id = _int(component_local, f"LOD {lod_id + 1} component vertex-group id")
            if component_local_id not in local_to_global:
                raise MergedRuntimeError(
                    f"LOD {lod_id + 1} references Component local VG {component_local_id} "
                    "which is absent from VertexGroupMap.json"
                )
            # EFMI's importer reads this as a source index in the current
            # Component's local skeleton, not as an authoring global VG id.
            values[lod_local_id] = component_local_id
    else:
        values = {local_id: local_id for local_id in local_to_global}

    if not values:
        raise MergedRuntimeError(f"Component LOD {lod_id + 1} has no vertex-group remap")
    max_local = max(values)
    expected = set(range(max_local + 1))
    if set(values) != expected:
        raise MergedRuntimeError(
            f"Component LOD {lod_id + 1} has incomplete local VG remap; "
            f"expected ids 0..{max_local}"
        )
    return LodRemap(lod_id=lod_id, values=tuple(values[index] for index in range(max_local + 1)))


def build_plan(metadata: Any, vertex_group_map: vgmap.VertexGroupMap, instance_count: int = 8) -> MergedRuntimePlan:
    components = list(getattr(metadata, "components", []) or [])
    if len(components) != len(vertex_group_map.components):
        raise MergedRuntimeError(
            f"VertexGroupMap.json component count {len(vertex_group_map.components)} "
            f"does not match Metadata.json component count {len(components)}"
        )
    plans: list[ComponentPlan] = []
    global_ids: set[int] = set()
    runtime_offset = 0
    for component_id, (component, entry) in enumerate(zip(components, vertex_group_map.components)):
        local_to_global = _local_to_global(component)
        sidecar_map = {int(key): int(value) for key, value in entry.vg_map.items()}
        if local_to_global != sidecar_map:
            raise MergedRuntimeError(
                f"Component {component_id} Metadata vg_map differs from VertexGroupMap.json; "
                "reload the sidecar before MergedSkeleton export"
            )
        if not local_to_global and not getattr(component, "cpu_posed", False):
            raise MergedRuntimeError(f"Component {component_id} has no vertex-group map")
        metadata_vg_count = _int(getattr(component, "vg_count", 0), f"Component {component_id} vg_count")
        # Older/merged authoring sources may leave the component-local palette
        # fields at zero while the sidecar and LOD metadata carry the complete
        # local palette. Derive that count from the authoritative local map.
        vg_count = metadata_vg_count or (max(local_to_global, default=-1) + 1)
        if local_to_global and vg_count <= max(local_to_global):
            raise MergedRuntimeError(
                f"Component {component_id} vg_count={vg_count} does not cover local VG {max(local_to_global)}"
            )
        if local_to_global and set(local_to_global) != set(range(vg_count)):
            raise MergedRuntimeError(
                f"Component {component_id} has incomplete local VG remap; expected ids 0..{vg_count - 1}"
            )
        global_ids.update(local_to_global.values())
        lod_remaps = tuple(
            _lod_values(component, local_to_global, lod, lod_id)
            for lod_id, lod in enumerate(getattr(component, "lods", []) or [])
        )
        plans.append(
            ComponentPlan(
                component_id=component_id,
                vg_offset=runtime_offset,
                vg_count=vg_count,
                global_ids=tuple(sorted(local_to_global.values())),
                global_by_local=tuple(local_to_global[index] for index in range(vg_count)),
                lod_remaps=lod_remaps,
            )
        )
        runtime_offset += vg_count
    bones_count = runtime_offset
    if bones_count <= 0 and any(not getattr(item, "cpu_posed", False) for item in components):
        raise MergedRuntimeError("MergedSkeleton requires at least one GPU-posed global vertex group")
    for component in plans:
        for remap in component.lod_remaps:
            invalid = [value for value in remap.values if value >= bones_count]
            if invalid:
                raise MergedRuntimeError(
                    f"Component {component.component_id} LOD {remap.lod_id + 1} remap "
                    f"contains global VG {invalid[0]} outside bones_count={bones_count}"
                )
    return MergedRuntimePlan(
        bones_count=bones_count,
        component_count=len(components),
        instance_count=_int(instance_count, "instance count"),
        components=tuple(plans),
    )


def effective_required_version(version: Any) -> tuple[int, int, int]:
    """Return a semver tuple clamped to the EFMI v1.4 MergedSkeleton minimum."""
    try:
        parts = tuple(int(part) for part in str(version).split("."))
    except (TypeError, ValueError) as exc:
        raise MergedRuntimeError(f"Invalid EFMI version: {version!r}") from exc
    parts = (parts + (0, 0, 0))[:3]
    return max(parts, EFMI_MERGED_MIN_VERSION)
