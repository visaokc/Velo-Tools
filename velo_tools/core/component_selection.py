"""Game-agnostic bulk selection helpers for Component rule lists."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class ComponentRule(Protocol):
    component_id: int
    use_slot: bool


def apply_bulk_selection(rules: Iterable[ComponentRule], action: str) -> bool:
    """Apply a bulk selection action and return False for an incomplete range."""
    items = list(rules)
    if action == "SELECT_ALL":
        for item in items:
            item.use_slot = True
        return True
    if action == "SELECT_NONE":
        for item in items:
            item.use_slot = False
        return True
    if action == "INVERT":
        for item in items:
            item.use_slot = not item.use_slot
        return True
    if action == "FILL_RANGE":
        endpoints = [int(item.component_id) for item in items if item.use_slot]
        if len(endpoints) < 2:
            return False
        first, last = min(endpoints), max(endpoints)
        for item in items:
            if first <= int(item.component_id) <= last:
                item.use_slot = True
        return True
    raise ValueError(f"Unknown bulk selection action: {action}")
