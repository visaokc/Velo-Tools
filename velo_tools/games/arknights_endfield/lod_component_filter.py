"""Driver-layer filtering for inactive EFMI LOD component draws."""

from __future__ import annotations

from dataclasses import dataclass

from ._efmi_core.migoto_io.migoto_model.types import ShaderType
from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object_builder import (
    MigotoObjectBuilder,
)


_INSTALLED = False
_ORIGINAL_BUILD_VG_MAP = None


@dataclass(frozen=True)
class FilteredLODComponent:
    component_id: int
    face_count: int


def _face_count(component) -> int:
    return int(component.mesh.format.index_count) // 3


def _has_pixel_texture_evidence(component) -> bool:
    return any(
        slot.shader_type == ShaderType.Pixel and bool(resources)
        for slot, resources in getattr(component, "textures", {}).items()
    )


def filter_components_without_pixel_textures(migoto_object) -> list[FilteredLODComponent]:
    """Remove components whose raw draw contains no PS texture bindings."""
    components = list(migoto_object.components)
    filtered = [
        FilteredLODComponent(component_id, _face_count(component))
        for component_id, component in enumerate(components)
        if not _has_pixel_texture_evidence(component)
    ]
    if not filtered:
        return []

    filtered_ids = {item.component_id for item in filtered}
    migoto_object.components = [
        component
        for component_id, component in enumerate(components)
        if component_id not in filtered_ids
    ]
    migoto_object.build_metadata()
    return filtered


def _enabled_for_current_extraction() -> bool:
    try:
        import bpy  # type: ignore

        cfg = getattr(getattr(bpy.context, "scene", None), "VTEF_settings", None)
        if (
            cfg is None
            or getattr(cfg, "tool_mode", None) != "EXTRACT_FRAME_DATA"
            or not getattr(cfg, "auto_skip_lod_components", False)
        ):
            return False
        explicit_hash_filter = bool(
            getattr(cfg, "skip_object_resource_hashes_enabled", False)
            and str(getattr(cfg, "skip_object_resource_hashes", "") or "").strip()
        )
        return not explicit_hash_filter
    except Exception:
        return False


def _build_vg_map_after_lod_filter(self, migoto_object):
    if _enabled_for_current_extraction():
        filtered = filter_components_without_pixel_textures(migoto_object)
        for item in filtered:
            print(
                f"[LOD Filter] {migoto_object.id}: skipped Component {item.component_id} "
                f"({item.face_count} faces); raw draw has no PS texture bindings"
            )
    return _ORIGINAL_BUILD_VG_MAP(self, migoto_object)


def install() -> None:
    global _INSTALLED, _ORIGINAL_BUILD_VG_MAP
    if _INSTALLED:
        return
    _ORIGINAL_BUILD_VG_MAP = MigotoObjectBuilder.build_vg_map
    MigotoObjectBuilder.build_vg_map = _build_vg_map_after_lod_filter
    _INSTALLED = True


def remove() -> None:
    global _INSTALLED, _ORIGINAL_BUILD_VG_MAP
    if not _INSTALLED:
        return
    if MigotoObjectBuilder.build_vg_map is _build_vg_map_after_lod_filter:
        MigotoObjectBuilder.build_vg_map = _ORIGINAL_BUILD_VG_MAP
    _ORIGINAL_BUILD_VG_MAP = None
    _INSTALLED = False
