"""Export-local batching for redundant vendor mode requests."""

from __future__ import annotations

from contextlib import contextmanager
import sys
import traceback


@contextmanager
def batch_export_context(context):
    """Skip redundant vendor requests to enter an already-active Object mode."""
    from ...games.arknights_endfield._efmi_core.migoto_io.blender_interface import objects as efmi_objects
    from ...games.wuthering_waves._wwmi_core.migoto_io.blender_interface import objects as wwmi_objects

    modules = (efmi_objects, wwmi_objects)
    saved = []
    skipped = False

    def make_wrapper(original):
        def set_mode(context, mode):
            nonlocal skipped
            if mode == 'OBJECT' and getattr(context, 'mode', None) == 'OBJECT':
                active = context.view_layer.objects.active
                if active is not None and active.mode == 'OBJECT':
                    skipped = True
                    return None
            return original(context, mode)

        return set_mode

    try:
        for module in modules:
            original = module.set_mode
            saved.append((module, original))
            module.set_mode = make_wrapper(original)
        from .component_batching import batch_component_operations
        with batch_component_operations():
            yield
    finally:
        active_exception = sys.exc_info()[0] is not None
        for module, original in reversed(saved):
            module.set_mode = original
        if skipped:
            if active_exception:
                try:
                    context.view_layer.update()
                except Exception:
                    traceback.print_exc()
            else:
                context.view_layer.update()
