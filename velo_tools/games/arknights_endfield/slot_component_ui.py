"""Per-Component controls for capability-based slot texture export."""

from __future__ import annotations

import traceback

import bpy

from velo_tools.i18n import iface_

from . import slot_texture_export


_SETTINGS_ATTR = "slot_texture_component_settings"


class SLOT_TEXTURE_PG_ComponentRule(bpy.types.PropertyGroup):
    component_id: bpy.props.IntProperty(default=0)
    use_slot: bpy.props.BoolProperty(
        name="Slot",
        description=(
            "Check = this component's texture uses slot style; uncheck = this "
            "component switches to hash style (textures deleted in advance are "
            "still managed by the game)"
        ),
        default=True,
    )
    texture_count: bpy.props.IntProperty(default=0)


class SLOT_TEXTURE_PG_ComponentSettings(bpy.types.PropertyGroup):
    rules: bpy.props.CollectionProperty(type=SLOT_TEXTURE_PG_ComponentRule)
    active_index: bpy.props.IntProperty(default=0)


class SLOT_TEXTURE_UL_Components(bpy.types.UIList):
    def draw_item(
            self, context, layout, data, item, icon,
            active_data, active_prop, index=0,
    ):
        row = layout.row(align=True)
        row.prop(item, "use_slot", text="")
        row.label(text=f"Component {item.component_id}")
        row.label(text=iface_("{0} Texture").format(item.texture_count))


class SLOT_TEXTURE_OT_ListComponents(bpy.types.Operator):
    bl_idname = "slot_texture.list_components"
    bl_label = "List components"
    bl_description = (
        "List all components from the ShaderTextureUsage.json in the object source "
        "folder for individual selection; all are selected by default (= all use "
        "slot style). Unchecked components switch to hash style, and textures "
        "deleted in advance are still taken over by the game. Please relist after "
        "changing the source folder."
    )

    def execute(self, context):
        cfg = context.scene.VTEF_settings
        settings = getattr(context.scene, _SETTINGS_ATTR)
        try:
            from ._efmi_core.migoto_io.blender_interface.utility import resolve_path

            if not cfg.object_source_folder.strip():
                raise ValueError(
                    iface_("Object source folder (object_source_folder) not set")
                )
            counts = slot_texture_export.component_texture_counts(
                resolve_path(cfg.object_source_folder)
            )
        except Exception as exc:
            traceback.print_exc()
            self.report(
                {"ERROR"},
                iface_("Failed to list components: {0}").format(exc),
            )
            return {"CANCELLED"}

        if not counts:
            self.report(
                {"WARNING"},
                iface_("No component texture record in ShaderTextureUsage.json"),
            )
            return {"CANCELLED"}

        previous = {
            item.component_id: item.use_slot
            for item in settings.rules
        }
        settings.rules.clear()
        for component_id, texture_count in counts.items():
            item = settings.rules.add()
            item.component_id = component_id
            item.use_slot = previous.get(component_id, True)
            item.texture_count = texture_count
        settings.active_index = min(
            settings.active_index,
            max(0, len(settings.rules) - 1),
        )
        self.report(
            {"INFO"},
            iface_(
                "List {0} components (default uses all slots, uncheck to use hash)"
            ).format(len(settings.rules)),
        )
        return {"FINISHED"}


def selected_components(context) -> set[int] | None:
    settings = getattr(context.scene, _SETTINGS_ATTR, None)
    if settings is None or not len(settings.rules):
        return None
    return {
        int(item.component_id)
        for item in settings.rules
        if item.use_slot
    }


def draw_component_selector(layout, context) -> None:
    settings = getattr(context.scene, _SETTINGS_ATTR, None)
    if settings is None:
        return
    sub = layout.box()
    sub.label(text=iface_("Select slot style by component"), icon="TEXTURE")
    row = sub.row()
    row.template_list(
        "SLOT_TEXTURE_UL_Components",
        "",
        settings,
        "rules",
        settings,
        "active_index",
        rows=4,
    )
    row.column(align=True).operator(
        SLOT_TEXTURE_OT_ListComponents.bl_idname,
        text="",
        icon="FILE_REFRESH",
    )
    if not len(settings.rules):
        sub.label(
            text=iface_(
                "Not listed: defaults to using all slots. After listing points "
                "from the source folder, components can be individually deselected"
            ),
            icon="INFO",
        )
    else:
        sub.label(
            text=iface_(
                "Unchecked components use hash; prematurely deleted textures "
                "belong to the game"
            ),
            icon="INFO",
        )


_CLASSES = (
    SLOT_TEXTURE_PG_ComponentRule,
    SLOT_TEXTURE_PG_ComponentSettings,
    SLOT_TEXTURE_UL_Components,
    SLOT_TEXTURE_OT_ListComponents,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    setattr(
        bpy.types.Scene,
        _SETTINGS_ATTR,
        bpy.props.PointerProperty(type=SLOT_TEXTURE_PG_ComponentSettings),
    )


def unregister() -> None:
    if hasattr(bpy.types.Scene, _SETTINGS_ATTR):
        delattr(bpy.types.Scene, _SETTINGS_ATTR)
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
