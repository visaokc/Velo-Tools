"""Weight transfer tab for Velo Tools."""

from . import props as _props
from . import operators as _operators
from . import ui as _ui
from . import runtime as _runtime


def register():
    _props.register()
    _operators.register()
    _ui.register()
    _runtime.register()


def unregister():
    _runtime.unregister()
    _ui.unregister()
    _operators.unregister()
    _props.unregister()
