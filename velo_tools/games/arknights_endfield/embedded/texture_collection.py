"""EFMI adapter for the shared source-folder texture policy."""

from __future__ import annotations

from pathlib import Path

from ....core.export.texture_collection import (
    collect_managed_textures,
    managed_texture_hash,
)


_PATCHES = []


def get_managed_textures(object_source_folder: Path, exclude_hashes, *, texture_type=None):
    """Collect only exporter-owned textures and ignore arbitrary author files."""
    if texture_type is None:
        from .._efmi_core.blender_export.texture_collector import Texture
        texture_type = Texture
    return collect_managed_textures(
        object_source_folder,
        exclude_hashes,
        texture_type=texture_type,
    )


def install() -> None:
    """Patch imported EFMI collector bindings without modifying vendored core."""
    if _PATCHES:
        return
    from .._efmi_core.blender_export import blender_export, texture_collector

    for module in (texture_collector, blender_export):
        _PATCHES.append((module, module.get_textures))
        module.get_textures = get_managed_textures


def remove() -> None:
    while _PATCHES:
        module, original = _PATCHES.pop()
        module.get_textures = original
