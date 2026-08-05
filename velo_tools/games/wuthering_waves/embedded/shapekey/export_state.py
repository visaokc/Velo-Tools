"""Connect WWMI ShapeKey naming to shared export-state preparation."""

import re

from .....core.export.shapekey_state import finalize_merger_shape_keys


_SHAPE_ID_RE = re.compile(r".*(?:deform|custom)[_ -]*(\d+).*$", re.IGNORECASE)


def shape_id(name: str) -> int | None:
    match = _SHAPE_ID_RE.fullmatch(name or "")
    return int(match.group(1)) if match is not None else None


def finalize_merger(merger) -> None:
    finalize_merger_shape_keys(merger, shape_id)
