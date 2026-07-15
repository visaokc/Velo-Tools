"""Pure helpers for deterministic ShapeKey aggregation and rename planning."""

import re


_NATURAL_PART_RE = re.compile(r"(\d+)")
_DEFORM_NAME_RE = re.compile(r"^\s*deform\s*(\d+).*$", re.IGNORECASE)


def natural_key(value):
    """Return a case-insensitive natural-sort key."""
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in _NATURAL_PART_RE.split(value or "")
        if part != ""
    )


def deform_number(name):
    """Return the numeric Deform id, or None when the whole name is unnumbered."""
    match = _DEFORM_NAME_RE.fullmatch(name or "")
    return int(match.group(1)) if match else None


def shapekey_sort_key(name):
    """Sort numbered Deform names first, then all other names naturally."""
    number = deform_number(name)
    if number is not None:
        return (0, number, natural_key(name))
    return (1, natural_key(name))


def sorted_shapekey_names(names):
    return sorted(names, key=shapekey_sort_key)


def aggregation_signature(order, contributors):
    """Return a signature that changes when contributing object identity changes."""
    return tuple((name, tuple(contributors[name])) for name in order)


def remap_ui_state(selected_by_name, active_name, name_remap=None):
    """Move selection and active-row identity through a batch rename."""
    name_remap = name_remap or {}
    selected = {
        name_remap.get(name, name): state
        for name, state in selected_by_name.items()
    }
    active = name_remap.get(active_name, active_name) if active_name is not None else None
    return selected, active


def shapekey_column_units(
        total_units, checkbox_units=1.6, count_units=3.0,
        value_units=10.0, minimum_name_units=6.0):
    """Keep utility columns fixed once the name column reaches its minimum width."""
    total_units = max(float(total_units), 1.0)
    minimum_total = checkbox_units + count_units + value_units + minimum_name_units
    if total_units < minimum_total:
        scale = total_units / minimum_total
        return (
            checkbox_units * scale,
            minimum_name_units * scale,
            count_units * scale,
            value_units * scale,
        )
    return (
        checkbox_units,
        total_units - checkbox_units - count_units - value_units,
        count_units,
        value_units,
    )


def build_rename_plan(names, selected_names):
    """Build collision-free final names for selected, unnumbered aggregate entries."""
    ordered_names = sorted_shapekey_names(names)
    occupied_numbers = {
        number for name in ordered_names
        if (number := deform_number(name)) is not None
    }
    next_number = max(occupied_numbers) + 1 if occupied_numbers else 1
    selected = set(selected_names)
    plan = []
    for name in ordered_names:
        if name not in selected or deform_number(name) is not None:
            continue
        while next_number in occupied_numbers:
            next_number += 1
        plan.append((name, f"Deform {next_number} {name}"))
        occupied_numbers.add(next_number)
        next_number += 1
    return plan


def unique_temp_names(occupied_names, count):
    """Return deterministic temporary names that do not collide with existing names."""
    occupied = set(occupied_names)
    result = []
    candidate = 0
    while len(result) < count:
        name = f"__velo_tmp_sk_{candidate}__"
        candidate += 1
        if name in occupied:
            continue
        occupied.add(name)
        result.append(name)
    return result
