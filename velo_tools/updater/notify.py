"""Host-level "update available" notification banner.

Velo Tools surfaces new releases as a calm in-panel banner at the top of the
``VELO_PT_main`` N-panel instead of the self-popping modal dialog. This module
owns that banner: a self-driving throttled background check, the draw helper,
and the two banner-only operators ("skip this session" / "stop notifying").

Kept separate from the vendored ``addon_updater_ops`` so the upstream fork stays
clean and easy to re-sync. Reuses the engine bits from there (the ``updater``
singleton, ``ui_refresh``, ``get_user_preferences``, ``AddonUpdaterUpdateNow``).
"""

import bpy

from . import addon_updater_ops

# Session guards, reset on unregister() so a Blender restart / addon reload
# starts fresh (mirrors the global resets at the bottom of addon_updater_ops).
_ran_notify_check = False
_skip_session = False


def _tag_redraw():
    # Reuse the updater's all-areas redraw helper (ignores its argument).
    addon_updater_ops.ui_refresh(None)


def maybe_check(prefs):
    """Self-driving throttled background check that feeds the banner.

    Independent of ``auto_check_update``: having ``auto_update_notify`` on is
    enough to drive this. We force the interval check ``enabled`` and let the
    engine's ``check_for_update(now=False)`` honor the interval (default 1 day),
    so opening the panel only hits GitHub once per elapsed interval, once per
    session. ``show_popups`` is False at the host level, so the async callback
    here is ``ui_refresh`` (redraw only) and no modal is ever armed.
    """
    global _ran_notify_check
    updater = addon_updater_ops.updater
    if updater.invalid_updater:
        return
    if _ran_notify_check:
        return
    if updater.update_ready is not None or updater.async_checking:
        # Already checked (or checking) this session.
        return

    updater.set_check_interval(
        enabled=True,
        months=prefs.updater_interval_months,
        days=prefs.updater_interval_days,
        hours=prefs.updater_interval_hours,
        minutes=prefs.updater_interval_minutes)
    updater.check_for_update_async(addon_updater_ops.ui_refresh)
    _ran_notify_check = True


def draw_update_banner(panel, context):
    """Draw the top-of-panel update banner; call first in VELO_PT_main.draw().

    Kicks the throttled self-driving check, then draws a notice box with three
    actions when an update is ready and the user has not skipped / disabled it.
    """
    updater = addon_updater_ops.updater
    if updater.invalid_updater:
        return
    prefs = addon_updater_ops.get_user_preferences(context)
    if not prefs or not getattr(prefs, "auto_update_notify", True):
        return

    maybe_check(prefs)

    if _skip_session:
        return
    if not updater.update_ready:
        return

    version = updater.update_version
    if isinstance(version, (tuple, list)):
        version = "v" + ".".join(str(part) for part in version)

    layout = panel.layout
    box = layout.box()
    col = box.column(align=True)
    title = col.row()
    title.alert = True
    title.label(text="发现新版本 {}".format(version), icon="ERROR")
    row = col.row(align=True)
    row.scale_y = 1.3
    row.operator(addon_updater_ops.AddonUpdaterUpdateNow.bl_idname,
                 text="立即更新", icon="LOOP_FORWARDS")
    row.operator(VELO_OT_UpdaterSkipSession.bl_idname, text="本次跳过")
    row.operator(VELO_OT_UpdaterDisableNotify.bl_idname, text="不再自动提醒")


class VELO_OT_UpdaterSkipSession(bpy.types.Operator):
    """Temporarily dismiss the update banner for this session.

    Distinct from AddonUpdaterIgnore (ignore_update(), permanent for this
    version): this only sets a session flag, so the next session / next elapsed
    interval reminds the user again.
    """
    bl_label = "本次跳过"
    bl_idname = "velo_tools.updater_skip_session"
    bl_description = "关闭本次更新提示；下个检测周期仍会再提醒"
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        global _skip_session
        _skip_session = True
        _tag_redraw()
        return {'FINISHED'}


class VELO_OT_UpdaterDisableNotify(bpy.types.Operator):
    """Turn off the host auto update-notification banner (auto_update_notify)."""
    bl_label = "不再自动提醒"
    bl_idname = "velo_tools.updater_disable_notify"
    bl_description = "关闭面板顶部的自动更新提示；可在偏好设置中重新开启"
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        prefs = addon_updater_ops.get_user_preferences(context)
        if prefs:
            prefs.auto_update_notify = False
        _tag_redraw()
        return {'FINISHED'}


classes = (
    VELO_OT_UpdaterSkipSession,
    VELO_OT_UpdaterDisableNotify,
)


def reset_state():
    """Reset session guards; called from the updater package unregister()."""
    global _ran_notify_check, _skip_session
    _ran_notify_check = False
    _skip_session = False
