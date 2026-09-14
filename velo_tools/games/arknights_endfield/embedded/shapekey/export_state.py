"""Connect EFMI Deform naming to shared export-state preparation."""

import re

from .....core.export.shapekey_state import finalize_merger_shape_keys

from .detector import parse_deform_name


_NATIVE_SHAPE_RE = re.compile(r".*(?:deform|custom)[_ -]*(\d+).*$", re.IGNORECASE)


def _native_shape_name(name: str) -> bool:
    """Keep native channels, including bare names created by the importer."""
    return _NATIVE_SHAPE_RE.fullmatch(name or "") is not None


def _shape_id(name: str) -> int | None:
    parsed = parse_deform_name(name)
    return int(parsed[0]) if parsed is not None else None


def finalize_merger(merger) -> None:
    finalize_merger_shape_keys(
        merger, _shape_id, preserve_shape_key=_native_shape_name)
