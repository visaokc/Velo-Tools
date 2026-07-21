"""Immutable WWMI export selection shared by every cross-scene export unit."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional, Tuple


_COMPONENT_RE = re.compile(r"component[ _-]*(\d+)", re.I)


def component_id(name: object) -> Optional[int]:
    match = _COMPONENT_RE.search(str(name or ""))
    return int(match.group(1)) if match else None


def _data_identity(data: Any) -> tuple[str, int]:
    as_pointer = getattr(data, "as_pointer", None)
    if callable(as_pointer):
        return ("pointer", int(as_pointer()))
    return ("identity", id(data))


def _effective_visible_collection_keys(
        context: Any, root_collection: Any,
) -> Optional[frozenset[tuple[str, int]]]:
    view_layer = getattr(context, "view_layer", None)
    layer_root = getattr(view_layer, "layer_collection", None)
    if layer_root is None:
        return None

    root_key = _data_identity(root_collection)
    found_root = False
    visible_keys = set()

    def visit(layer: Any, parent_visible: bool, inside_root: bool) -> None:
        nonlocal found_root
        collection = layer.collection
        own_visible = (
            not bool(getattr(layer, "exclude", False))
            and not bool(getattr(layer, "hide_viewport", False))
            and not bool(getattr(collection, "hide_viewport", False))
        )
        effective_visible = parent_visible and own_visible
        if _data_identity(collection) == root_key:
            inside_root = True
            found_root = True
        if inside_root and effective_visible:
            visible_keys.add(_data_identity(collection))
        for child in layer.children:
            visit(child, effective_visible, inside_root)

    visit(layer_root, True, False)
    if not found_root:
        return None
    return frozenset(visible_keys)


def get_export_collection_objects(
        context: Any,
        collection: Any,
        *,
        recursive: bool,
        skip_hidden_collections: bool,
        object_provider: Callable[..., Iterable[Any]],
) -> Tuple[Any, ...]:
    """Collect objects while honoring effective parent collection visibility."""
    if not recursive or not skip_hidden_collections:
        return tuple(object_provider(
            collection,
            recursive=recursive,
            skip_hidden_collections=skip_hidden_collections,
        ))

    visible_keys = _effective_visible_collection_keys(context, collection)
    if visible_keys is None:
        return tuple(object_provider(
            collection,
            recursive=recursive,
            skip_hidden_collections=True,
        ))

    candidates = object_provider(
        collection,
        recursive=recursive,
        skip_hidden_collections=False,
    )
    return tuple(
        obj for obj in candidates
        if any(
            _data_identity(user_collection) in visible_keys
            for user_collection in getattr(obj, "users_collection", ())
        )
    )


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
        if ignore_hidden_objects and hidden_predicate(obj):
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
