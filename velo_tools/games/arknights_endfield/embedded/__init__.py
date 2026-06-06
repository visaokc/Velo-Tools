"""CrossIB / ShapeKey embedded registration (V0.1.6).

Since V0.1.6: removed the V0.1.5 placeholder, forwards register/unregister to
embedded.crossib and embedded.shapekey. Each module handles on its own: registering
classes + attaching a Scene PointerProperty + monkey-patch (CrossIB modifies IniMaker;
ShapeKey modifies VTEF_PT_SidePanelAdvancedExport).

Call-order constraint: must be called after vendored EFMI `_al.register()`, otherwise
VTEF_PT_INI_TOGGLES / VTEF_PT_SidePanelAdvancedExport cannot be found.
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