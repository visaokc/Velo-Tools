"""EFMI adapter for the shared export selection policy."""

from __future__ import annotations

from ....core.export.selection import (
    install_collection_object_provider,
    remove_collection_object_provider,
)


_PATCHED_PROVIDER = None


def install() -> None:
    """Route stock EFMI collection reads through the shared host policy."""
    global _PATCHED_PROVIDER
    if _PATCHED_PROVIDER is not None:
        return

    import bpy

    from .._efmi_core.blender_export import object_merger
    from .._efmi_core.migoto_io.blender_interface.objects import object_is_hidden

    install_collection_object_provider(
        object_merger,
        context_provider=lambda: bpy.context,
        settings_provider=lambda: getattr(
            bpy.context.scene, "VTEF_settings", None),
        hidden_predicate=object_is_hidden,
    )
    _PATCHED_PROVIDER = object_merger
    print("[velo.export-selection] patched stock EFMI collection provider")


def remove() -> None:
    """Restore the vendored EFMI provider binding."""
    global _PATCHED_PROVIDER
    if _PATCHED_PROVIDER is None:
        return
    remove_collection_object_provider(_PATCHED_PROVIDER)
    _PATCHED_PROVIDER = None
