"""Pure canonical LOD bone-map construction."""

from dataclasses import dataclass
from typing import Mapping, Optional


class LodMappingError(Exception):
    pass


@dataclass(frozen=True)
class CanonicalLodMap:
    component_id: int
    level: int
    buffer_name: str
    destination_offset: int
    sources: tuple[int, ...]

    @property
    def bone_count(self) -> int:
        return len(self.sources)


def _int_map(value: Optional[Mapping]) -> Optional[dict[int, int]]:
    if value is None:
        return None
    result = {}
    for key, target in value.items():
        if target is None:
            continue
        result[int(key)] = int(target)
    return result


def build_canonical_lod_map(
        component_id: int,
        level: int,
        component_vg_offset: int,
        component_vg_count: int,
        lod_entry: Mapping,
        *,
        merged: bool,
        max_source_bones: int = 256,
        max_canonical_bones: int = 512,
) -> CanonicalLodMap:
    """Build an EFMI-style canonical-local to native-LOD source dictionary."""
    remap = _int_map(lod_entry.get("vg_map"))
    sources = []
    missing = []

    for canonical_local in range(int(component_vg_count)):
        source = canonical_local if remap is None else remap.get(canonical_local)
        if source is None:
            missing.append(canonical_local)
            continue
        if source < 0 or source >= max_source_bones:
            raise LodMappingError(
                f"Component {component_id} LOD{level} source bone {source} is "
                f"outside the native palette range 0..{max_source_bones - 1}")
        destination = (
            int(component_vg_offset) + canonical_local
            if merged else canonical_local
        )
        if destination < 0 or destination >= max_canonical_bones:
            raise LodMappingError(
                f"Component {component_id} LOD{level} canonical bone "
                f"{destination} exceeds the runtime limit "
                f"0..{max_canonical_bones - 1}")
        sources.append(source)

    if missing:
        preview = ", ".join(str(value) for value in missing[:12])
        suffix = "..." if len(missing) > 12 else ""
        raise LodMappingError(
            f"Component {component_id} LOD{level} has no native source for "
            f"canonical local bones {preview}{suffix}")

    buffer_name = f"CanonicalLodMapC{component_id}L{level}"
    return CanonicalLodMap(
        component_id=int(component_id),
        level=int(level),
        buffer_name=buffer_name,
        destination_offset=(int(component_vg_offset) if merged else 0),
        sources=tuple(sources),
    )
