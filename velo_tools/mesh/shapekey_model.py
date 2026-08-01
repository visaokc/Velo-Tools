"""Pure helpers for deterministic ShapeKey aggregation and rename planning."""

import re


_NATURAL_PART_RE = re.compile(r"(\d+)")
_DEFORM_NAME_RE = re.compile(r"^\s*deform\s*(\d+).*$", re.IGNORECASE)
_DEFORM_PREFIX_RE = re.compile(r"^\s*deform\s*\d+\s*", re.IGNORECASE)


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


def deform_number_is_locked(number, unlock_from=-1):
    """Return whether a numbered row remains protected before a repair boundary."""
    if number is None:
        return False
    return int(unlock_from) < 0 or int(number) < int(unlock_from)


def deform_basename(name):
    """Remove one existing Deform-number prefix before assigning a replacement."""
    return _DEFORM_PREFIX_RE.sub("", name or "", count=1).strip()


def remaining_deform_repair_boundary(names, unlock_from, unlock_end):
    """Return the first still-missing id in a tracked repair range, or -1."""
    unlock_from = int(unlock_from)
    unlock_end = int(unlock_end)
    if unlock_from < 0 or unlock_end < unlock_from:
        return -1
    occupied = {
        number for name in names
        if (number := deform_number(name)) is not None
    }
    for number in range(unlock_from, unlock_end + 1):
        if number not in occupied:
            return number
    return -1


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
        value_units=8.0, minimum_name_units=6.0):
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


def shapekey_count_label(count, minimum_digits=1):
    """Pad a contributor count with digit-width spaces for stable button sizing."""
    count_text = str(int(count))
    digits = max(int(minimum_digits), len(count_text))
    return "x" + "\u2007" * (digits - len(count_text)) + count_text


def wwmi_native_deform_numbers(metadata):
    """Return Metadata-owned WWMI Deform IDs, including deleted Blender keys."""
    shapes = (metadata or {}).get("shapekeys") or {}
    batches = shapes.get("batches") or []
    if batches:
        occupied = set()
        for batch_id, batch in enumerate(batches):
            count = int((batch or {}).get("shapekey_count", 0) or 0)
            if not 0 <= count <= 127:
                raise ValueError(
                    f"WWMI ShapeKey batch {batch_id} count is outside 0..127")
            start = batch_id * 127
            occupied.update(range(start, start + count))
        return occupied

    count = int(shapes.get("shapekey_count", 0) or 0)
    if count < 0:
        raise ValueError("WWMI ShapeKey count cannot be negative")
    return set(range(count))


def build_rename_plan(
        names, selected_names, unlock_from=-1, order_hints=None,
        reserved_numbers=None):
    """Build collision-free new names, optionally repairing an unlocked suffix."""
    ordered_names = sorted_shapekey_names(names)
    selected = set(selected_names)
    unlock_from = int(unlock_from)

    if unlock_from >= 0:
        order_hints = order_hints or {}

        def repair_key(name):
            number = deform_number(name)
            hint = int(order_hints.get(name, -1))
            if hint >= unlock_from:
                return (0, hint, natural_key(name))
            if number is not None and number >= unlock_from:
                return (0, number, natural_key(name))
            return (1, natural_key(name))

        candidates = [
            name for name in ordered_names
            if name in selected
            and not deform_number_is_locked(deform_number(name), unlock_from)
        ]
        candidates.sort(key=repair_key)
        occupied_numbers = {
            number for name in ordered_names
            if (number := deform_number(name)) is not None
            and name not in candidates
        }
        next_number = unlock_from
        plan = []
        for name in candidates:
            while next_number in occupied_numbers:
                next_number += 1
            basename = deform_basename(name) if deform_number(name) is not None else name
            final_name = f"Deform {next_number} {basename}".rstrip()
            if final_name != name:
                plan.append((name, final_name))
            occupied_numbers.add(next_number)
            next_number += 1
        return plan

    occupied_numbers = {
        number for name in ordered_names
        if (number := deform_number(name)) is not None
    }
    occupied_numbers.update(int(number) for number in (reserved_numbers or ()))
    next_number = 1
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
