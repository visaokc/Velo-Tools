"""Driver-layer keep and skip filters for extracted EFMI components."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module


_INSTALLED = False
_ORIGINAL_PARSE_COMPONENT_FILTER = None
_ORIGINAL_FILTER_COMPONENTS_CONTIGUOUSLY = None
_EXTRACT_MODULE = None


@dataclass(frozen=True)
class CombinedComponentFilter:
    keep: set[int] | None
    skip: set[int]


def apply_component_filters(
    migoto_object,
    keep: set[int] | None,
    skip: set[int],
) -> None:
    """Apply keep then skip semantics to current component indices."""
    migoto_object.components = [
        component
        for component_id, component in enumerate(migoto_object.components)
        if (keep is None or component_id in keep) and component_id not in skip
    ]
    migoto_object.build_metadata()


def _parse_component_filter_with_skip(spec: str):
    keep = _ORIGINAL_PARSE_COMPONENT_FILTER(spec)
    try:
        import bpy  # type: ignore

        cfg = getattr(getattr(bpy.context, "scene", None), "VTEF_settings", None)
        skip_spec = getattr(cfg, "extract_components_skip_filter", "") if cfg else ""
    except Exception:
        skip_spec = ""
    skip = _ORIGINAL_PARSE_COMPONENT_FILTER(skip_spec or "")
    if skip is None:
        return keep
    return CombinedComponentFilter(keep=keep, skip=skip)


def _filter_components_with_skip(migoto_object, component_filter) -> None:
    if not isinstance(component_filter, CombinedComponentFilter):
        _ORIGINAL_FILTER_COMPONENTS_CONTIGUOUSLY(migoto_object, component_filter)
        return
    apply_component_filters(migoto_object, component_filter.keep, component_filter.skip)


def install() -> None:
    global _INSTALLED
    global _ORIGINAL_PARSE_COMPONENT_FILTER
    global _ORIGINAL_FILTER_COMPONENTS_CONTIGUOUSLY
    global _EXTRACT_MODULE
    if _INSTALLED:
        return
    _EXTRACT_MODULE = import_module(
        f"{__package__}._efmi_core.extract_frame_data.extract_frame_data"
    )
    _ORIGINAL_PARSE_COMPONENT_FILTER = _EXTRACT_MODULE._parse_component_filter
    _ORIGINAL_FILTER_COMPONENTS_CONTIGUOUSLY = _EXTRACT_MODULE._filter_components_contiguously
    _EXTRACT_MODULE._parse_component_filter = _parse_component_filter_with_skip
    _EXTRACT_MODULE._filter_components_contiguously = _filter_components_with_skip
    _INSTALLED = True


def remove() -> None:
    global _INSTALLED
    global _ORIGINAL_PARSE_COMPONENT_FILTER
    global _ORIGINAL_FILTER_COMPONENTS_CONTIGUOUSLY
    global _EXTRACT_MODULE
    if not _INSTALLED:
        return
    if _EXTRACT_MODULE._parse_component_filter is _parse_component_filter_with_skip:
        _EXTRACT_MODULE._parse_component_filter = _ORIGINAL_PARSE_COMPONENT_FILTER
    if _EXTRACT_MODULE._filter_components_contiguously is _filter_components_with_skip:
        _EXTRACT_MODULE._filter_components_contiguously = _ORIGINAL_FILTER_COMPONENTS_CONTIGUOUSLY
    _ORIGINAL_PARSE_COMPONENT_FILTER = None
    _ORIGINAL_FILTER_COMPONENTS_CONTIGUOUSLY = None
    _EXTRACT_MODULE = None
    _INSTALLED = False
