"""Driver-layer filtering for duplicate lower-detail EFMI components."""

from __future__ import annotations

from dataclasses import dataclass

from ._efmi_core.migoto_io.migoto_model.migoto_mesh import (
    GeometryMatcher,
    GeometryMatcherConfig,
    GeometryMatcherMethod,
)
from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object_builder import (
    MigotoObjectBuilder,
)


_INSTALLED = False
_ORIGINAL_BUILD_VG_MAP = None
_VOXEL_SIZE = 0.01
_SIMILARITY_THRESHOLD = 55.0
_FORWARD_DRAW_WINDOW = 12


@dataclass(frozen=True)
class FilteredLODComponent:
    component_id: int
    matched_component_id: int
    face_count: int
    matched_face_count: int
    similarity: float


def _face_count(component) -> int:
    return int(component.mesh.format.index_count) // 3


def _first_draw_id(component, fallback: int) -> int:
    shader_calls = getattr(getattr(component, "raw_data", None), "shader_calls", ())
    draw_ids = [int(call.id) for call in shader_calls if getattr(call, "id", None) is not None]
    return min(draw_ids, default=fallback)


def filter_lower_detail_components(
    migoto_object,
    *,
    matcher=None,
    similarity_threshold: float = _SIMILARITY_THRESHOLD,
) -> list[FilteredLODComponent]:
    """Remove lower-face-count components matching a retained component by voxels."""
    components = list(migoto_object.components)
    if len(components) < 2:
        return []

    if matcher is None:
        matcher = GeometryMatcher(
            GeometryMatcherConfig(
                method=GeometryMatcherMethod.Voxel,
                sensitivity=0.5,
                voxel_size=_VOXEL_SIZE,
            )
        )

    draw_order_ids = sorted(
        range(len(components)),
        key=lambda component_id: (_first_draw_id(components[component_id], component_id), component_id),
    )
    filtered = []

    for draw_position, component_id in enumerate(draw_order_ids):
        component = components[component_id]
        face_count = _face_count(component)
        best_match = None
        for candidate_id in draw_order_ids[
            draw_position + 1:draw_position + 1 + _FORWARD_DRAW_WINDOW
        ]:
            candidate = components[candidate_id]
            candidate_face_count = _face_count(candidate)
            if candidate_face_count <= face_count:
                continue
            similarity = float(matcher.calculate_similarity(component.mesh, candidate.mesh))
            if best_match is None or similarity > best_match.similarity:
                best_match = FilteredLODComponent(
                    component_id=component_id,
                    matched_component_id=candidate_id,
                    face_count=face_count,
                    matched_face_count=candidate_face_count,
                    similarity=similarity,
                )
        if best_match is not None and best_match.similarity >= similarity_threshold:
            filtered.append(best_match)

    if not filtered:
        return []

    filtered_ids = {item.component_id for item in filtered}
    migoto_object.components = [
        component
        for component_id, component in enumerate(components)
        if component_id not in filtered_ids
    ]
    migoto_object.build_metadata()
    return sorted(filtered, key=lambda item: item.component_id)


def _enabled_for_current_extraction() -> bool:
    try:
        import bpy  # type: ignore

        cfg = getattr(getattr(bpy.context, "scene", None), "VTEF_settings", None)
        if (
            cfg is None
            or getattr(cfg, "tool_mode", None) != "EXTRACT_FRAME_DATA"
            or not getattr(cfg, "auto_skip_lod_components", True)
        ):
            return False
        explicit_hash_filter = bool(
            getattr(cfg, "skip_object_resource_hashes_enabled", False)
            and str(getattr(cfg, "skip_object_resource_hashes", "") or "").strip()
        )
        return not explicit_hash_filter
    except Exception:
        return False


def _configured_similarity_threshold() -> float:
    try:
        import bpy  # type: ignore

        cfg = getattr(getattr(bpy.context, "scene", None), "VTEF_settings", None)
        return float(getattr(cfg, "auto_skip_lod_similarity_threshold", _SIMILARITY_THRESHOLD))
    except Exception:
        return _SIMILARITY_THRESHOLD


def _build_vg_map_after_lod_filter(self, migoto_object):
    if _enabled_for_current_extraction():
        filtered = filter_lower_detail_components(
            migoto_object,
            similarity_threshold=_configured_similarity_threshold(),
        )
        for item in filtered:
            print(
                f"[LOD Filter] {migoto_object.id}: skipped Component {item.component_id} "
                f"({item.face_count} faces); voxel-matched Component "
                f"{item.matched_component_id} ({item.matched_face_count} faces) "
                f"at {item.similarity:.2f}%"
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
