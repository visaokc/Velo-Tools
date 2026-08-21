"""Prepare mixed ShapeKey state on disposable export objects."""

from __future__ import annotations

from array import array
from contextlib import contextmanager
import math
from typing import Callable, Iterable, Mapping


ShapeIdParser = Callable[[str], int | None]


def format_ini_float(value: float) -> str:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("ShapeKey default value must be finite")
    text = format(value, ".9g")
    if "." not in text and "e" not in text.casefold():
        text += ".0"
    return text


def merge_shape_key_defaults(
    mappings: Iterable[Mapping[int, float]],
    *,
    tolerance: float = 1e-6,
) -> dict[int, float]:
    result: dict[int, float] = {}
    for mapping in mappings:
        for shape_id, raw_value in mapping.items():
            shape_id = int(shape_id)
            value = float(raw_value)
            if not math.isfinite(value):
                raise ValueError(f"Deform {shape_id} has a non-finite Blender value")
            previous = result.get(shape_id)
            if previous is not None and not math.isclose(
                previous, value, rel_tol=0.0, abs_tol=tolerance
            ):
                raise ValueError(
                    f"Deform {shape_id} has inconsistent Blender values "
                    f"({format_ini_float(previous)} and {format_ini_float(value)}); "
                    "one runtime variable cannot preserve both defaults"
                )
            result[shape_id] = value
    return result


def object_shape_key_defaults(obj, parse_shape_id: ShapeIdParser) -> dict[int, float]:
    shape_keys = getattr(getattr(obj, "data", None), "shape_keys", None)
    blocks = tuple(getattr(shape_keys, "key_blocks", ()) or ())
    mappings = []
    for key in blocks:
        shape_id = parse_shape_id(str(getattr(key, "name", "")))
        if shape_id is not None:
            mappings.append({int(shape_id): float(getattr(key, "value", 0.0))})
    return merge_shape_key_defaults(mappings)


def _coordinates(key, vertex_count: int) -> array:
    result = array("f", [0.0]) * (vertex_count * 3)
    key.data.foreach_get("co", result)
    return result


def _update_mix(obj) -> None:
    try:
        obj.data.update()
    except Exception:
        pass
    try:
        import bpy

        bpy.context.view_layer.update()
    except Exception:
        pass


@contextmanager
def neutralized_shape_key_values(obj, key_blocks):
    """Temporarily evaluate selected ShapeKeys at zero during base export."""
    saved = [(key, float(getattr(key, "value", 0.0))) for key in key_blocks]
    try:
        for key, _value in saved:
            key.value = 0.0
        _update_mix(obj)
        yield
    finally:
        for key, value in saved:
            key.value = value
        _update_mix(obj)


def _capture_mix(obj, name: str, vertex_count: int):
    key = obj.shape_key_add(name=name, from_mix=True)
    return key, _coordinates(key, vertex_count)


def _set_if_present(target, name: str, value) -> None:
    if hasattr(target, name):
        setattr(target, name, value)


def collapse_nonexported_shape_key_mix(
    obj,
    parse_shape_id: ShapeIdParser,
) -> dict[int, float]:
    """Bake unmatched current values into Basis and retain matched Deform keys."""
    shape_keys = getattr(getattr(obj, "data", None), "shape_keys", None)
    blocks = list(getattr(shape_keys, "key_blocks", ()) or ())
    if len(blocks) <= 1:
        return {}

    protected = []
    unmatched = []
    for key in blocks[1:]:
        shape_id = parse_shape_id(str(getattr(key, "name", "")))
        if shape_id is None:
            unmatched.append(key)
        else:
            protected.append((int(shape_id), key))

    defaults = merge_shape_key_defaults(
        ({shape_id: float(getattr(key, "value", 0.0))}
         for shape_id, key in protected)
    )
    if not protected or not unmatched:
        return defaults
    if not bool(getattr(shape_keys, "use_relative", True)):
        raise ValueError(
            f"Object `{getattr(obj, 'name', '<unnamed>')}` mixes exported Deform keys "
            "with nonstandard absolute ShapeKeys; absolute keys have no per-key value "
            "that can be baked independently"
        )

    vertex_count = len(blocks[0].data)
    metadata = []
    for shape_id, key in protected:
        metadata.append({
            "shape_id": shape_id,
            "name": key.name,
            "value": float(key.value),
            "mute": bool(getattr(key, "mute", False)),
            "slider_min": float(getattr(key, "slider_min", 0.0)),
            "slider_max": float(getattr(key, "slider_max", 1.0)),
            "interpolation": getattr(key, "interpolation", None),
        })

    for _shape_id, key in protected:
        key.value = 0.0
    _update_mix(obj)
    captures = []
    base_key, base_coordinates = _capture_mix(
        obj, "__shape_export_mixed_basis__", vertex_count)
    captures.append(base_key)

    target_coordinates = []
    for index, (_shape_id, key) in enumerate(protected):
        original_mute = bool(getattr(key, "mute", False))
        original_max = float(getattr(key, "slider_max", 1.0))
        _set_if_present(key, "mute", False)
        _set_if_present(key, "slider_max", max(1.0, original_max))
        key.value = 1.0
        _update_mix(obj)
        capture, coordinates = _capture_mix(
            obj, f"__shape_export_target_{index}__", vertex_count)
        captures.append(capture)
        target_coordinates.append(coordinates)
        key.value = 0.0
        _set_if_present(key, "mute", original_mute)
        _set_if_present(key, "slider_max", original_max)

    for key in list(getattr(obj.data.shape_keys, "key_blocks", ()) or ()):
        obj.shape_key_remove(key)
    obj.data.vertices.foreach_set("co", base_coordinates)
    obj.data.update()

    obj.shape_key_add(name="Basis", from_mix=False)
    for item, coordinates in zip(metadata, target_coordinates):
        key = obj.shape_key_add(name=item["name"], from_mix=False)
        key.data.foreach_set("co", coordinates)
        _set_if_present(key, "slider_min", item["slider_min"])
        _set_if_present(key, "slider_max", item["slider_max"])
        _set_if_present(key, "interpolation", item["interpolation"])
        _set_if_present(key, "mute", item["mute"])
        _set_if_present(key, "vertex_group", "")
        key.value = item["value"]
    obj.data.update()
    return defaults


def finalize_merger_shape_keys(merger, parse_shape_id: ShapeIdParser) -> None:
    for component in getattr(merger, "components", ()) or ():
        for temp_object in getattr(component, "objects", ()) or ():
            obj = getattr(temp_object, "object", None)
            if obj is not None:
                collapse_nonexported_shape_key_mix(obj, parse_shape_id)
