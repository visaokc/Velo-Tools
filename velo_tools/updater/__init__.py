"""Velo Tools host-level auto-updater (wiring layer).

This subpackage vendors the CGCookie/SpectrumQT ``addon_updater`` (GPL) at the
host level so the whole ``velo_tools`` addon can self-update from the
``visaokc/Velo-Tools`` GitHub releases. The per-game WWMI / EFMI updaters stay
neutralized and must never point at SpectrumQT upstream (see ADR 0002).

``addon_updater_ops.register()`` only configures the updater singleton; it does
NOT register the operator classes (upstream relies on auto_load for that). The
host does not auto_load this subpackage, so the operator classes and the host
AddonPreferences are registered explicitly here.
"""

import bpy

from . import addon_updater_ops


class VELO_AddonUpdaterPreferences(bpy.types.AddonPreferences):
    """Host-level addon preferences carrying the updater settings UI."""

    # Must equal the top-level addon module name ("velo_tools") so Blender
    # attaches these preferences to the addon; addon_updater_ops looks them up
    # via updater.addon == "velo_tools".
    bl_idname = "velo_tools"

    auto_check_update: bpy.props.BoolProperty(
        name="自动检查更新",
        description="启用后，按下方间隔在后台自动检查更新",
        default=True,
    )  # type: ignore
    updater_interval_months: bpy.props.IntProperty(
        name="月", description="检查更新的间隔月数", default=0, min=0,
    )  # type: ignore
    updater_interval_days: bpy.props.IntProperty(
        name="天", description="检查更新的间隔天数", default=1, min=0, max=31,
    )  # type: ignore
    updater_interval_hours: bpy.props.IntProperty(
        name="时", description="检查更新的间隔小时数", default=0, min=0, max=23,
    )  # type: ignore
    updater_interval_minutes: bpy.props.IntProperty(
        name="分", description="检查更新的间隔分钟数", default=0, min=0, max=59,
    )  # type: ignore
    receive_prereleases: bpy.props.BoolProperty(
        name="接收预发布版本",
        description=("启用后，更新检查会包含 GitHub 上标记为 pre-release 的版本；"
                     "默认只接收稳定的正式版本"),
        default=False,
    )  # type: ignore

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "receive_prereleases")
        # Kick off a throttled background check when the prefs are opened, then
        # draw the full updater UI (check button, interval, restore backup...).
        addon_updater_ops.check_for_update_background()
        addon_updater_ops.update_settings_ui(self, context)


def register():
    for cls in addon_updater_ops.classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_class(VELO_AddonUpdaterPreferences)
    addon_updater_ops.register()


def unregister():
    addon_updater_ops.unregister()
    bpy.utils.unregister_class(VELO_AddonUpdaterPreferences)
    for cls in reversed(addon_updater_ops.classes):
        bpy.utils.unregister_class(cls)
