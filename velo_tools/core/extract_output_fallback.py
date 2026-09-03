"""Use the Frame Dump directory when extraction output is left blank."""

from __future__ import annotations

from functools import wraps


_ORIGINAL_ATTR = "_extract_output_fallback_original"


def wrap_execute(original_execute, settings_attr: str):
    @wraps(original_execute)
    def wrapped(operator, context):
        cfg = getattr(context.scene, settings_attr)
        original_output = getattr(cfg, "extract_output_folder", "")
        dump_folder = getattr(cfg, "frame_dump_folder", "")
        if str(original_output).strip() or not str(dump_folder).strip():
            return original_execute(operator, context)
        cfg.extract_output_folder = dump_folder
        try:
            return original_execute(operator, context)
        finally:
            cfg.extract_output_folder = original_output

    return wrapped


def install(operator_cls, settings_attr: str) -> None:
    current = operator_cls.execute
    if getattr(current, _ORIGINAL_ATTR, None) is not None:
        return
    wrapped = wrap_execute(current, settings_attr)
    setattr(wrapped, _ORIGINAL_ATTR, current)
    operator_cls.execute = wrapped


def remove(operator_cls) -> None:
    current = operator_cls.execute
    original = getattr(current, _ORIGINAL_ATTR, None)
    if original is None:
        return
    operator_cls.execute = original
