"""Shared export-scope and collection visibility policy."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, Tuple


_PROVIDER_PATCHES: dict[tuple[int, str], tuple[Any, Callable[..., Any]]] = {}


def data_identity(data: Any) -> tuple[str, int]:
    """Return a stable identity for Blender data and lightweight test doubles."""
    as_pointer = getattr(data, "as_pointer", None)
    if callable(as_pointer):
        return ("pointer", int(as_pointer()))
    return ("identity", id(data))


def _descendant_collection_keys(root_collection: Any) -> frozenset[tuple[str, int]]:
    keys = {data_identity(root_collection)}
    recursive = getattr(root_collection, "children_recursive", None)
    if recursive is not None:
        keys.update(data_identity(collection) for collection in recursive)
        return frozenset(keys)

    stack = list(getattr(root_collection, "children", ()) or ())
    while stack:
        collection = stack.pop()
        key = data_identity(collection)
        if key in keys:
            continue
        keys.add(key)
        stack.extend(getattr(collection, "children", ()) or ())
    return frozenset(keys)


def collection_is_descendant_or_same(root_collection: Any, collection: Any) -> bool:
    if root_collection is None or collection is None:
        return False
    return data_identity(collection) in _descendant_collection_keys(root_collection)


def effective_visible_collection_keys(
        context: Any,
        root_collection: Any,
) -> Optional[frozenset[tuple[str, int]]]:
    """Return collections under ``root_collection`` with one fully visible layer path."""
    view_layer = getattr(context, "view_layer", None)
    layer_root = getattr(view_layer, "layer_collection", None)
    if layer_root is None or root_collection is None:
        return None

    root_key = data_identity(root_collection)
    found_root = False
    visible_keys: set[tuple[str, int]] = set()

    def visit(layer: Any, parent_visible: bool, inside_root: bool) -> None:
        nonlocal found_root
        collection = getattr(layer, "collection", None)
        if collection is None:
            return
        own_visible = (
            not bool(getattr(layer, "exclude", False))
            and not bool(getattr(layer, "hide_viewport", False))
            and not bool(getattr(collection, "hide_viewport", False))
        )
        effective_visible = parent_visible and own_visible
        if data_identity(collection) == root_key:
            inside_root = True
            found_root = True
        if inside_root and effective_visible:
            visible_keys.add(data_identity(collection))
        for child in getattr(layer, "children", ()) or ():
            visit(child, effective_visible, inside_root)

    visit(layer_root, True, False)
    if not found_root:
        return frozenset()
    return frozenset(visible_keys)


def export_object_collections(
        context: Any,
        obj: Any,
        root_collection: Any,
        *,
        recursive: bool,
        skip_hidden_collections: bool,
) -> Tuple[Any, ...]:
    """Return in-scope object links that satisfy the shared visibility policy."""
    if root_collection is None or obj is None:
        return ()
    root_key = data_identity(root_collection)
    scope_keys = (_descendant_collection_keys(root_collection)
                  if recursive else frozenset((root_key,)))
    links = tuple(
        collection
        for collection in getattr(obj, "users_collection", ()) or ()
        if data_identity(collection) in scope_keys
    )
    if not links or not skip_hidden_collections:
        return links

    visible_keys = effective_visible_collection_keys(context, root_collection)
    if visible_keys is None:
        return links
    return tuple(
        collection for collection in links
        if data_identity(collection) in visible_keys
    )


def direct_collection_object_provider(
        collection: Any,
        *,
        recursive: bool,
        skip_hidden_collections: bool = False,
) -> Tuple[Any, ...]:
    """Read Blender collection ownership without applying visibility policy."""
    del skip_hidden_collections
    objects = (getattr(collection, "all_objects", ())
               if recursive else getattr(collection, "objects", ()))
    return tuple(objects or ())


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
    """Apply one collection and object visibility policy to any object provider."""
    visible_keys = None
    if skip_hidden_collections:
        visible_keys = effective_visible_collection_keys(context, collection)

    if not skip_hidden_collections or visible_keys is None:
        candidates = tuple(object_provider(
            collection,
            recursive=recursive,
            skip_hidden_collections=skip_hidden_collections,
        ))
    else:
        candidates = tuple(object_provider(
            collection,
            recursive=recursive,
            skip_hidden_collections=False,
        ))
        candidates = tuple(
            obj for obj in candidates
            if any(
                data_identity(user_collection) in visible_keys
                for user_collection in getattr(obj, "users_collection", ()) or ()
            )
        )

    if skip_hidden_objects and hidden_predicate is not None:
        candidates = tuple(
            obj for obj in candidates if not hidden_predicate(obj)
        )
    return candidates


def wrap_collection_object_provider(
        context_provider: Callable[[], Any],
        object_provider: Callable[..., Iterable[Any]],
        settings_provider: Optional[Callable[[], Any]] = None,
        hidden_predicate: Optional[Callable[[Any], bool]] = None,
) -> Callable[..., Tuple[Any, ...]]:
    """Adapt a native provider to the shared export selection policy."""
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


def install_collection_object_provider(
        owner: Any,
        *,
        context_provider: Callable[[], Any],
        settings_provider: Optional[Callable[[], Any]] = None,
        hidden_predicate: Optional[Callable[[Any], bool]] = None,
        attribute: str = "get_collection_objects",
) -> bool:
    """Patch one imported native provider binding without editing vendored code."""
    key = (id(owner), attribute)
    if key in _PROVIDER_PATCHES:
        return False
    original = getattr(owner, attribute)
    setattr(owner, attribute, wrap_collection_object_provider(
        context_provider,
        original,
        settings_provider=settings_provider,
        hidden_predicate=hidden_predicate,
    ))
    _PROVIDER_PATCHES[key] = (owner, original)
    return True


def remove_collection_object_provider(
        owner: Any,
        *,
        attribute: str = "get_collection_objects",
) -> bool:
    key = (id(owner), attribute)
    patch = _PROVIDER_PATCHES.pop(key, None)
    if patch is None:
        return False
    module, original = patch
    setattr(module, attribute, original)
    return True
