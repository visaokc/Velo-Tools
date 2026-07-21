"""Immutable WWMI export selection shared by every cross-scene export unit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Tuple

from ..export_selection import get_export_collection_objects


_COMPONENT_RE = re.compile(r"component[ _-]*(\d+)", re.I)


def component_id(name: object) -> Optional[int]:
    match = _COMPONENT_RE.search(str(name or ""))
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class SelectedObject:
    object: Any
    name: str
    component_id: int


@dataclass(frozen=True)
class ExportSelection:
    objects: Tuple[SelectedObject, ...]
    slot_eligible_components: Optional[frozenset[int]]
    ignore_nested_collections: bool
    ignore_hidden_collections: bool
    ignore_hidden_objects: bool
    ignore_muted_shape_keys: bool
    apply_modifiers: bool
    fill_missing_mesh_data: bool
    add_missing_vertex_groups: bool

    def for_components(self, component_ids: Iterable[int]) -> Tuple[SelectedObject, ...]:
        wanted = frozenset(int(value) for value in component_ids)
        return tuple(item for item in self.objects if item.component_id in wanted)

    def by_name(self, name: str) -> Optional[SelectedObject]:
        for item in self.objects:
            if item.name == name:
                return item
        return None

    @property
    def selected_component_ids(self) -> frozenset[int]:
        return frozenset(item.component_id for item in self.objects)


def capture_export_selection(
        context: Any,
        cfg: Any,
        collection: Any,
        *,
        slot_eligible_components: Optional[Iterable[int]] = None,
        object_provider: Optional[Callable[..., Iterable[Any]]] = None,
        hidden_predicate: Optional[Callable[[Any], bool]] = None,
) -> ExportSelection:
    """Run the native WWMI collection/visibility selection exactly once."""
    if object_provider is None or hidden_predicate is None:
        from ..._wwmi_core.migoto_io.blender_interface.collections import (
            get_collection_objects,
        )
        from ..._wwmi_core.migoto_io.blender_interface.objects import (
            object_is_hidden,
        )
        object_provider = object_provider or get_collection_objects
        hidden_predicate = hidden_predicate or object_is_hidden

    ignore_nested = bool(getattr(cfg, "ignore_nested_collections", False))
    ignore_hidden_collections = bool(
        getattr(cfg, "ignore_hidden_collections", False))
    ignore_hidden_objects = bool(getattr(cfg, "ignore_hidden_objects", False))

    candidates = get_export_collection_objects(
        context,
        collection,
        recursive=not ignore_nested,
        skip_hidden_collections=ignore_hidden_collections,
        object_provider=object_provider,
        skip_hidden_objects=ignore_hidden_objects,
        hidden_predicate=hidden_predicate,
    )
    selected = []
    for obj in candidates:
        if getattr(obj, "type", None) != "MESH":
            continue
        name = str(getattr(obj, "name", ""))
        if name.startswith("TEMP_"):
            continue
        cid = component_id(name)
        if cid is None:
            continue
        selected.append(SelectedObject(obj, name, cid))
    selected.sort(key=lambda item: item.name)

    eligible = None
    if slot_eligible_components is not None:
        eligible = frozenset(int(value) for value in slot_eligible_components)
    return ExportSelection(
        objects=tuple(selected),
        slot_eligible_components=eligible,
        ignore_nested_collections=ignore_nested,
        ignore_hidden_collections=ignore_hidden_collections,
        ignore_hidden_objects=ignore_hidden_objects,
        ignore_muted_shape_keys=bool(
            getattr(cfg, "ignore_muted_shape_keys", False)),
        apply_modifiers=bool(getattr(cfg, "apply_all_modifiers", False)),
        fill_missing_mesh_data=bool(
            getattr(cfg, "fill_missing_mesh_data", False)),
        add_missing_vertex_groups=bool(
            getattr(cfg, "add_missing_vertex_groups", False)),
    )
