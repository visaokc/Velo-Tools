from . import props as _props
from . import operators as _operators
from . import ui as _ui
from . import pick as _pick
from . import overlay as _overlay


def register():
    _props.register()
    _operators.register()
    _ui.register()
    _pick.register()
    _overlay.register()


def unregister():
    _overlay.unregister()
    _pick.unregister()
    _ui.unregister()
    _operators.unregister()
    _props.unregister()