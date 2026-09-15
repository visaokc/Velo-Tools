"""Shared export-activity guard for authoring-only reactive handlers."""

from __future__ import annotations

from contextlib import contextmanager


_SUSPEND_DEPTH = 0


def reactive_updates_suspended() -> bool:
    """Return whether disposable export mutations should stay invisible to UI handlers."""
    return _SUSPEND_DEPTH > 0


@contextmanager
def suspend_reactive_updates():
    """Suppress authoring UI/cache reactions while an export transaction mutates copies."""
    global _SUSPEND_DEPTH

    _SUSPEND_DEPTH += 1
    try:
        yield
    finally:
        _SUSPEND_DEPTH -= 1
