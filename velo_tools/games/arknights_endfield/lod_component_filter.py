"""Driver-layer filtering for duplicate lower-detail EFMI components."""

from __future__ import annotations

from dataclasses import dataclass

from ._efmi_core.migoto_io.migoto_model.migoto_mesh import (
    GeometryMatcher,
    GeometryMatcherConfig,
    GeometryMatcherMethod,
)
from ._efmi_core.migoto_io.migoto_model.types import ShaderType
from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object_builder import (
    MigotoObjectBuilder,
)


_INSTALLED = False
_ORIGINAL_BUILD_VG_MAP = None
_VOXEL_SIZE = 0.01
_SIMILARITY_THRESHOLD = 80.0


@dataclass(frozen=True)
class FilteredLODComponent:
    component_id: int
    matched_component_id: int
    face_count: int
    matched_face_count: int
    similarity: float


def _face_count(component) -> int:
    return int(component.mesh.format.index_count) // 3


def _has_pixel_texture_evidence(component) -> bool:
    return any(
        slot.shader_type == ShaderType.Pixel and bool(resources)
        for slot, resources in getattr(component, "textures", {}).items()
    )


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

    best_matches = {}
    for component_id, component in enumerate(components):
        for candidate_id in range(component_id + 1, len(components)):
            candidate = components[candidate_id]
            similarity = float(matcher.calculate_similarity(component.mesh, candidate.mesh))
            for source_id, target_id in ((component_id, candidate_id), (candidate_id, component_id)):
                current = best_matches.get(source_id)
                if current is None or similarity > current[1]:
                    best_matches[source_id] = (target_id, similarity)

    filtered = []
    paired_ids = set()
    for component_id in range(len(components)):
        if component_id in paired_ids or component_id not in best_matches:
            continue
        candidate_id, similarity = best_matches[component_id]
        reverse = best_matches.get(candidate_id)
        if (
            reverse is None
            or reverse[0] != component_id
            or similarity < similarity_threshold
        ):
            continue
        paired_ids.update((component_id, candidate_id))
        component_faces = _face_count(components[component_id])
        candidate_faces = _face_count(components[candidate_id])
        if component_faces == candidate_faces:
            component_has_textures = _has_pixel_texture_evidence(components[component_id])
            candidate_has_textures = _has_pixel_texture_evidence(components[candidate_id])
            if component_has_textures == candidate_has_textures:
                continue
            if component_has_textures:
                lower_id, higher_id = candidate_id, component_id
            else:
                lower_id, higher_id = component_id, candidate_id
            lower_faces = higher_faces = component_faces
        elif component_faces < candidate_faces:
            lower_id, higher_id = component_id, candidate_id
            lower_faces, higher_faces = component_faces, candidate_faces
        else:
            lower_id, higher_id = candidate_id, component_id
            lower_faces, higher_faces = candidate_faces, component_faces
        filtered.append(FilteredLODComponent(
            component_id=lower_id,
            matched_component_id=higher_id,
            face_count=lower_faces,
            matched_face_count=higher_faces,
            similarity=similarity,
        ))

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
            evidence_note = (
                "; equal-face pair resolved by one-sided PS texture bindings"
                if item.face_count == item.matched_face_count
                else ""
            )
            print(
                f"[LOD Filter] {migoto_object.id}: skipped Component {item.component_id} "
                f"({item.face_count} faces); voxel-matched Component "
                f"{item.matched_component_id} ({item.matched_face_count} faces) "
                f"at {item.similarity:.2f}%{evidence_note}"
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
