"""Scene-local suspension for expensive material-route refreshes."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator


def _scene_key(scene: Any) -> int:
    try:
        return int(scene.as_pointer())
    except Exception:
        return id(scene)


class SceneRefreshGate:
    """Collapse nested dirty notifications into one refresh per scene."""

    def __init__(self) -> None:
        self._depths: dict[int, int] = {}
        self._dirty: set[int] = set()

    def is_suspended(self, scene: Any) -> bool:
        return self._depths.get(_scene_key(scene), 0) > 0

    def mark_dirty(self, scene: Any) -> bool:
        key = _scene_key(scene)
        if self._depths.get(key, 0) <= 0:
            return False
        self._dirty.add(key)
        return True

    @contextmanager
    def suspend(
            self,
            scene: Any,
            refresh: Callable[[Any], None],
    ) -> Iterator[None]:
        key = _scene_key(scene)
        self._depths[key] = self._depths.get(key, 0) + 1
        try:
            yield
        finally:
            depth = self._depths.get(key, 1) - 1
            if depth > 0:
                self._depths[key] = depth
                return
            self._depths.pop(key, None)
            dirty = key in self._dirty
            self._dirty.discard(key)
            if dirty:
                refresh(scene)

