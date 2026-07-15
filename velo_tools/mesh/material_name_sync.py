"""Rename tracking state for automatic mesh/material name synchronization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Iterable


@dataclass
class _ScopeState:
    scope_key: Hashable
    names: dict[int, str]


class RenameTracker:
    """Track known object names independently for each Blender scene."""

    def __init__(self) -> None:
        self._states: dict[int, _ScopeState] = {}

    def prepare(
            self,
            scene_key: int,
            enabled: bool,
            scope_key: Hashable | None,
            seed: Iterable[tuple[int, str]],
    ) -> bool:
        """Return whether rename detection may run after seeding scope changes."""
        if not enabled or scope_key is None:
            self._states.pop(scene_key, None)
            return False
        state = self._states.get(scene_key)
        if state is None or state.scope_key != scope_key:
            self._states[scene_key] = _ScopeState(scope_key, dict(seed))
            return False
        return True

    def previous(self, scene_key: int, object_key: int) -> tuple[bool, str | None]:
        state = self._states.get(scene_key)
        if state is None or object_key not in state.names:
            return False, None
        return True, state.names[object_key]

    def record(self, scene_key: int, object_key: int, name: str) -> None:
        state = self._states.get(scene_key)
        if state is not None:
            state.names[object_key] = name

    def reset(self) -> None:
        self._states.clear()
