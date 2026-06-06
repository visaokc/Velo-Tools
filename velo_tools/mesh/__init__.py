"""Velo Tools - 网格工具子模块

来自 v0.3.0 mesh_ops.py / mesh_ui.py / shapekey_ops.py 的整体迁移，
按用户要求保持业务逻辑不变，只迁移文件位置。
"""

from . import operators
from . import machin3tools_patch
from . import shapekey_ops
from . import ui

_modules = (operators, machin3tools_patch, shapekey_ops, ui)


def register():
    for m in _modules:
        m.register()


def unregister():
    for m in reversed(_modules):
        try:
            m.unregister()
        except Exception:
            pass
