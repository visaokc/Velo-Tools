"""Weight transfer tab for Velo Tools."""

from . import props as _props
from . import native_dependencies as _native_dependencies
from . import operators as _operators
from . import ui as _ui
from . import runtime as _runtime


def register():
    _native_dependencies.cleanup_stale_caches()
    _props.register()
    _operators.register()
    _ui.register()
    _runtime.register()


def unregister():
    _runtime.unregister()
    _ui.unregister()
    _operators.unregister()
    _props.unregister()
