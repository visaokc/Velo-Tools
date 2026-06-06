"""Compatibility patch for MACHIN3Tools collection purge.

MACHIN3Tools removes empty collections purely by checking whether they contain
objects / non-empty child collections. Velo's material-routing tree needs some
real collections to stay around even when they are temporarily empty, so we
exclude collections tagged with `velo_preserve_empty_collection`.
"""

from __future__ import annotations

import importlib


_PRESERVE_KEY = "velo_preserve_empty_collection"
_PATCHES = {}
_TARGETS = (
    ("MACHIN3tools.utils.collection", "get_removable_collections"),
    ("MACHIN3tools.ui.operators.collection", "get_removable_collections"),
)


def _should_keep_collection(collection):
    return bool(collection is not None and getattr(collection, "get", None) and collection.get(_PRESERVE_KEY))


def _wrap_get_removable_collections(func):
    def wrapped(context, *args, **kwargs):
        removable = func(context, *args, **kwargs)
        if not removable:
            return removable
        return [collection for collection in removable if not _should_keep_collection(collection)]

    return wrapped


def install():
    for module_name, attr_name in _TARGETS:
        key = (module_name, attr_name)
        if key in _PATCHES:
            continue
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        original = getattr(module, attr_name, None)
        if not callable(original):
            continue
        setattr(module, attr_name, _wrap_get_removable_collections(original))
        _PATCHES[key] = (module, original)


def remove():
    for (module_name, attr_name), (module, original) in list(_PATCHES.items()):
        try:
            setattr(module, attr_name, original)
        except Exception:
            pass
    _PATCHES.clear()


def register():
    install()


def unregister():
    remove()