"""Three-stage mapping core (R3-phase implementation).

File layout:
- algorithms.py : MMD/unified/native three-stage mapping algorithms + rename + weight merge
- text_io.py    : mapping text format parse / serialize
- operators.py  : 5 user-facing operators (PLAN section 2.3)
- ui.py         : minimal UI (sub-panel under the Endfield tab)
"""

from . import operators as _ops
from . import ui as _ui


def register():
    _ops.register()
    _ui.register()


def unregister():
    _ui.unregister()
    _ops.unregister()
