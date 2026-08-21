"""Velo Tools - mesh tools submodule

Wholesale migration from v0.3.0 mesh_ops.py / mesh_ui.py / shapekey_ops.py;
per user request, business logic is unchanged, only the file location was moved.
"""

from . import operators
from . import machin3tools_patch
from . import octahedral_uv
from . import shapekey_ops
from . import ui

_modules = (operators, machin3tools_patch, octahedral_uv, shapekey_ops, ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        try:
            m.unregister()
        except Exception:
            pass
