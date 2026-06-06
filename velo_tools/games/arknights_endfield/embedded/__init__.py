"""CrossIB / ShapeKey 嵌入注册（V0.1.6）。

V0.1.6 起：删除 V0.1.5 占位，转发 register/unregister 到 embedded.crossib
与 embedded.shapekey。两个模块各自处理：注册类 + 挂 Scene PointerProperty +
monkey-patch（CrossIB 修改 IniMaker；ShapeKey 修改 VTEF_PT_SidePanelAdvancedExport）。

调用顺序约束：必须在 vendored EFMI `_al.register()` 之后调用，否则找不到
VTEF_PT_INI_TOGGLES / VTEF_PT_SidePanelAdvancedExport。
"""
import traceback

from . import crossib, shapekey


def register():
    try:
        crossib.register()
    except Exception:
        traceback.print_exc()
    try:
        shapekey.register()
    except Exception:
        traceback.print_exc()


def unregister():
    try:
        shapekey.unregister()
    except Exception:
        traceback.print_exc()
    try:
        crossib.unregister()
    except Exception:
        traceback.print_exc()