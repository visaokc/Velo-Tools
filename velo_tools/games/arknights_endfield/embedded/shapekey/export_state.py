"""Connect EFMI Deform naming to shared export-state preparation."""

from .....core.export.shapekey_state import finalize_merger_shape_keys

from .detector import parse_deform_name


def _shape_id(name: str) -> int | None:
    parsed = parse_deform_name(name)
    return int(parsed[0]) if parsed is not None else None


def finalize_merger(merger) -> None:
    finalize_merger_shape_keys(merger, _shape_id)
