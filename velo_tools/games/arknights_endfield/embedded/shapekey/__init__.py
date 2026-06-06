"""ShapeKey — Standalone Custom Shape Key export addon for EFMI Tools.

Parasitic plugin: hooks EFMI-Tools' export pipeline via monkey-patching to
add support for user-defined "Deform N Name" shape keys baked into compute
shader-driven position deltas. Disabling this addon fully restores original
EFMI behavior.

Architecture mirrors the sibling CrossIB plugin (see CrossIB/__init__.py).
"""
import bpy

bl_info = {
    "name": "ShapeKey (EFMI parasitic)",
    "version": (0, 3, 6),
    "blender": (3, 6, 0),
    "author": "Veloria",
    "location": "View3D > Sidebar > EFMI Tools > Advanced",
    "description": "Custom Deform-named shape key export for EFMI Tools (standalone).",
    "category": "Object",
}

from . import props, ui, patch


_classes = (
    props.ShapeKeyDetectedItem,
    props.ShapeKeyGroupState,
    props.ShapeKeySettings,
    ui.SHAPEKEY_OT_ToggleGroup,
    ui.SHAPEKEY_OT_RefreshDetected,
    ui.SHAPEKEY_UL_Detected,
)


def register():
    for cls in _classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
            bpy.utils.register_class(cls)
    bpy.types.Scene.shapekey_settings = bpy.props.PointerProperty(type=props.ShapeKeySettings)
    ui.install_ui_patch()
    ui.install_depsgraph_handler()
    patch.install_patches()


def unregister():
    patch.remove_patches()
    ui.remove_depsgraph_handler()
    ui.remove_ui_patch()
    if hasattr(bpy.types.Scene, "shapekey_settings"):
        del bpy.types.Scene.shapekey_settings
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
