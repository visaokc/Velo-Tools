"""Shared collection visibility policy for every WWMI export route."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, Tuple


_PATCHED_PROVIDER = None


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
        skip_hidden_objects: bool = False,
        hidden_predicate: Optional[Callable[[Any], bool]] = None,
) -> Tuple[Any, ...]:
    """Apply the shared collection and object visibility policy."""
    if not recursive or not skip_hidden_collections:
        candidates = tuple(object_provider(
            collection,
            recursive=recursive,
            skip_hidden_collections=skip_hidden_collections,
        ))
    else:
        visible_keys = _effective_visible_collection_keys(context, collection)
        if visible_keys is None:
            candidates = tuple(object_provider(
                collection,
                recursive=recursive,
                skip_hidden_collections=True,
            ))
        else:
            candidates = tuple(
                obj for obj in object_provider(
                    collection,
                    recursive=recursive,
                    skip_hidden_collections=False,
                )
                if any(
                    _data_identity(user_collection) in visible_keys
                    for user_collection in getattr(obj, "users_collection", ())
                )
            )
    if not skip_hidden_objects or hidden_predicate is None:
        return candidates
    return tuple(obj for obj in candidates if not hidden_predicate(obj))


def wrap_collection_object_provider(
        context_provider: Callable[[], Any],
        object_provider: Callable[..., Iterable[Any]],
        settings_provider: Optional[Callable[[], Any]] = None,
        hidden_predicate: Optional[Callable[[Any], bool]] = None,
) -> Callable[..., Tuple[Any, ...]]:
    """Adapt a native provider to the shared Velo export visibility policy."""
    def wrapped(
            collection: Any,
            recursive: bool = False,
            skip_hidden_collections: bool = True,
    ) -> Tuple[Any, ...]:
        cfg = settings_provider() if settings_provider is not None else None
        return get_export_collection_objects(
            context_provider(),
            collection,
            recursive=recursive,
            skip_hidden_collections=skip_hidden_collections,
            object_provider=object_provider,
            skip_hidden_objects=bool(
                getattr(cfg, "ignore_hidden_objects", False)),
            hidden_predicate=hidden_predicate,
        )

    return wrapped


def install() -> None:
    """Route stock single-IB ObjectMerger collection reads through this policy."""
    global _PATCHED_PROVIDER
    if _PATCHED_PROVIDER is not None:
        return

    import bpy

    from .._wwmi_core.blender_export import object_merger
    from .._wwmi_core.migoto_io.blender_interface.objects import (
        object_is_hidden,
    )

    original = object_merger.get_collection_objects
    object_merger.get_collection_objects = wrap_collection_object_provider(
        lambda: bpy.context,
        original,
        settings_provider=lambda: getattr(
            bpy.context.scene, "VTWW_settings", None),
        hidden_predicate=object_is_hidden,
    )
    _PATCHED_PROVIDER = (object_merger, original)
    print("[velo.export-selection] patched stock WWMI collection provider")


def remove() -> None:
    """Restore the vendored stock provider binding."""
    global _PATCHED_PROVIDER
    if _PATCHED_PROVIDER is None:
        return
    module, original = _PATCHED_PROVIDER
    module.get_collection_objects = original
    _PATCHED_PROVIDER = None
