"""CrossIB — Standalone Cross Index Buffer addon for EFMI Tools.

Hooks into the existing EFMI-Tools export pipeline via monkey-patching, so the
base addon stays untouched. Disabling this addon fully restores original
behavior.
"""
import bpy

bl_info = {
    "name": "CrossIB",
    "version": (1, 1, 0),
    "blender": (3, 6, 0),
    "author": "Veloria",
    "location": "View3D > Sidebar > EFMI Tools > Cross Index Buffer",
    "description": "Cross Index Buffer support for EFMI Tools (standalone, non-invasive).",
    "category": "Object",
}

from . import props, ui, patch


_classes = (
    props.CrossIBMapping,
    props.CrossIBSettings,
    ui.CROSSIB_PT_Panel,
    ui.CROSSIB_OT_AddMapping,
    ui.CROSSIB_OT_RemoveMapping,
    ui.CROSSIB_OT_GenerateSidecars,
)


def register():
    for cls in _classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            # Already registered (e.g. residue from a previous failed install).
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
            bpy.utils.register_class(cls)
    bpy.types.Scene.crossib_settings = bpy.props.PointerProperty(type=props.CrossIBSettings)
    patch.install_patches()


def unregister():
    patch.remove_patches()
    if hasattr(bpy.types.Scene, "crossib_settings"):
        del bpy.types.Scene.crossib_settings
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
