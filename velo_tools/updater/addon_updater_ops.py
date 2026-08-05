# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

"""Blender UI integrations for the addon updater.

Implements draw calls, popups, and operators that use the addon_updater.
"""

import os
import threading
import traceback
import bpy
from bpy.app.handlers import persistent
# Velo Tools host-level copy: pull bl_info from the top-level velo_tools package
# (this file lives under velo_tools/updater/), not from this subpackage.
from .. import bl_info

# Safely import the updater.
# Prevents popups for users with invalid python installs e.g. missing libraries
# and will replace with a fake class instead if it fails (so UI draws work).
try:
    from .addon_updater import Updater as updater
except Exception as e:
    print("ERROR INITIALIZING UPDATER")
    print(str(e))
    traceback.print_exc()

    class SingletonUpdaterNone(object):
        """Fake, bare minimum fields and functions for the updater object."""

        def __init__(self):
            self.invalid_updater = True  # Used to distinguish bad install.

            self.addon = None
            self.verbose = False
            self.use_print_traces = True
            self.error = None
            self.error_msg = None
            self.async_checking = None

        def clear_state(self):
            self.addon = None
            self.verbose = False
            self.invalid_updater = True
            self.error = None
            self.error_msg = None
            self.async_checking = None

        def run_update(self, force, callback, clean):
            pass

        def check_for_update(self, now):
            pass

    updater = SingletonUpdaterNone()
    updater.error = "Error initializing updater module"
    updater.error_msg = str(e)

# Must declare this before classes are loaded, otherwise the bl_idname's will
# not match and have errors. Must be all lowercase and no spaces! Should also
# be unique among any other addons that could exist (using this updater code),
# to avoid clashes in operator registration.
updater.addon = "velo_tools"

# The CGCookie engine derives its install/backup/data paths from
# os.path.dirname(__file__), assuming addon_updater.py sits at the addon root.
# This host copy is vendored one level deeper, under velo_tools/updater/, so the
# engine's __init__ computes _addon_root as ".../velo_tools/updater" instead of
# ".../velo_tools". Left uncorrected, an update merges the new release into
# velo_tools/updater/ -- the real addon (velo_tools/__init__.py) is never
# touched (the installed version never changes on restart) and the merge wipes
# the updater's own *.py and nests a stray copy. Re-base every path onto the
# true addon root: this file is velo_tools/updater/addon_updater_ops.py, so the
# addon root is its parent's parent.
if not getattr(updater, "invalid_updater", False):
    _velo_addon_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    updater._addon_root = _velo_addon_root
    updater._addon_package = "velo_tools"
    updater._updater_path = os.path.join(_velo_addon_root, "velo_tools_updater")


_update_job = None


def _poll_update_job():
    """Finish an update job on Blender's main thread after file work completes."""
    global _update_job
    job = _update_job
    if job is None:
        return None
    if job["thread"].is_alive():
        return 0.2

    _update_job = None
    result = job["result"]
    if result == 0:
        post_update_callback(updater.addon)
    else:
        post_update_callback(
            updater.addon,
            job["error"] or updater.error_msg or "更新失败",
        )
    return None


def _start_update_job(*, revert_tag=None, clean=False):
    """Run download/staging/merge off the Blender UI thread."""
    global _update_job
    if _update_job is not None and _update_job["thread"].is_alive():
        return False

    job = {"thread": None, "result": None, "error": None}

    def worker():
        try:
            job["result"] = updater.run_update(
                force=False,
                revert_tag=revert_tag,
                callback=None,
                clean=clean,
            )
        except Exception as exc:
            updater._error = "Error trying to run update"
            updater._error_msg = str(exc)
            job["error"] = str(exc)
            job["result"] = -1
            updater.print_trace()
        finally:
            updater.cleanup_staging()

    job["thread"] = threading.Thread(
        target=worker,
        name="velo-tools-updater",
        daemon=True,
    )
    _update_job = job
    job["thread"].start()
    bpy.app.timers.register(_poll_update_job, first_interval=0.2)
    return True


# -----------------------------------------------------------------------------
# Blender version utils
# -----------------------------------------------------------------------------
def make_annotations(cls):
    """Add annotation attribute to fields to avoid Blender 2.8+ warnings"""
    if not hasattr(bpy.app, "version") or bpy.app.version < (2, 80):
        return cls
    if bpy.app.version < (2, 93, 0):
        bl_props = {k: v for k, v in cls.__dict__.items()
                    if isinstance(v, tuple)}
    else:
        bl_props = {k: v for k, v in cls.__dict__.items()
                    if isinstance(v, bpy.props._PropertyDeferred)}
    if bl_props:
        if '__annotations__' not in cls.__dict__:
            setattr(cls, '__annotations__', {})
        annotations = cls.__dict__['__annotations__']
        for k, v in bl_props.items():
            annotations[k] = v
            delattr(cls, k)
    return cls


def layout_split(layout, factor=0.0, align=False):
    """Intermediate method for pre and post blender 2.8 split UI function"""
    if not hasattr(bpy.app, "version") or bpy.app.version < (2, 80):
        return layout.split(percentage=factor, align=align)
    return layout.split(factor=factor, align=align)


def get_user_preferences(context=None):
    """Intermediate method for pre and post blender 2.8 grabbing preferences"""
    if not context:
        context = bpy.context
    # Host-level copy: look up the top-level addon ("velo_tools") rather than
    # this subpackage's __name__, which would not be a registered addon.
    prefs = context.preferences.addons[updater.addon]
    if prefs.preferences:
        return prefs.preferences
    # To make the addon stable and non-exception prone, return None
    # raise Exception("Could not fetch user preferences")
    return None


# -----------------------------------------------------------------------------
# Updater operators
# -----------------------------------------------------------------------------


# Simple popup to prompt use to check for update & offer install if available.
class AddonUpdaterInstallPopup(bpy.types.Operator):
    """Check and install update if available"""
    bl_label = "更新 Velo Tools 插件"
    bl_idname = updater.addon + ".updater_install_popup"
    bl_description = "弹窗：检查并显示当前可用的更新"
    bl_options = {'REGISTER', 'INTERNAL'}

    # if true, run clean install - ie remove all files before adding new
    # equivalent to deleting the addon and reinstalling, except the
    # updater folder/backup folder remains
    clean_install: bpy.props.BoolProperty(
        name="清空后全新安装",
        description=("启用后，在安装新更新前会完全清空插件目录，"
                     "进行一次全新安装"),
        default=False,
        options={'HIDDEN'}
    )  #type: ignore

    ignore_enum: bpy.props.EnumProperty(
        name="处理更新",
        description="选择安装、忽略，或推迟这次更新",
        items=[
            ("install", "立即更新", "现在就安装更新"),
            ("ignore", "忽略", "忽略此次更新，避免以后再弹窗"),
            ("defer", "稍后", "推迟到下次 Blender 会话再决定")
        ],
        options={'HIDDEN'}
    )  #type: ignore

    def check(self, context):
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        if updater.invalid_updater:
            layout.label(text="更新器模块出错")
            return
        elif updater.update_ready:
            col = layout.column()
            col.scale_y = 0.7
            col.label(text="更新 {} 已就绪！".format(updater.update_version),
                      icon="LOOP_FORWARDS")
            col.label(text="选择「立即更新」并点 OK 安装，",
                      icon="BLANK1")
            col.label(text="或点击窗口外侧以稍后再说", icon="BLANK1")
            row = col.row()
            row.prop(self, "ignore_enum", expand=True)
            col.split()
        elif not updater.update_ready:
            col = layout.column()
            col.scale_y = 0.7
            col.label(text="没有可用更新")
            col.label(text="点 OK 关闭对话框")
            # add option to force install
        else:
            # Case: updater.update_ready = None
            # we have not yet checked for the update.
            layout.label(text="现在检查更新？")

        # Potentially in future, UI to 'check to select/revert to old version'.

    def execute(self, context):
        # In case of error importing updater.
        if updater.invalid_updater:
            return {'CANCELLED'}

        if updater.manual_only:
            bpy.ops.wm.url_open(url=updater.website)
        elif updater.update_ready:

            # Action based on enum selection.
            if self.ignore_enum == 'defer':
                return {'FINISHED'}
            elif self.ignore_enum == 'ignore':
                updater.ignore_update()
                return {'FINISHED'}

            res = updater.run_update(force=False,
                                     callback=post_update_callback,
                                     clean=self.clean_install)

            # Should return 0, if not something happened.
            if updater.verbose:
                if res == 0:
                    print("Updater returned successful")
                else:
                    print("Updater returned {}, error occurred".format(res))
        elif updater.update_ready is None:
            _ = updater.check_for_update(now=True)

            # Re-launch this dialog.
            atr = AddonUpdaterInstallPopup.bl_idname.split(".")
            getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')
        else:
            updater.print_verbose("Doing nothing, not ready for update")
        return {'FINISHED'}


# User preference check-now operator
class AddonUpdaterCheckNow(bpy.types.Operator):
    bl_label = "检查更新"
    bl_idname = updater.addon + ".updater_check_now"
    bl_description = "立即检查 Velo Tools 插件是否有更新"
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        if updater.invalid_updater:
            return {'CANCELLED'}

        if updater.async_checking and updater.error is None:
            # Check already happened.
            # Used here to just avoid constant applying settings below.
            # Ignoring if error, to prevent being stuck on the error screen.
            return {'CANCELLED'}

        # apply the UI settings
        settings = get_user_preferences(context)
        if not settings:
            updater.print_verbose(
                "Could not get {} preferences, update check skipped".format(
                    __package__))
            return {'CANCELLED'}

        updater.set_check_interval(
            enabled=settings.auto_check_update,
            months=settings.updater_interval_months,
            days=settings.updater_interval_days,
            hours=settings.updater_interval_hours,
            minutes=settings.updater_interval_minutes)

        # Input is an optional callback function. This function should take a
        # bool input. If true: update ready, if false: no update ready.
        updater.check_for_update_now(ui_refresh)

        return {'FINISHED'}


class AddonUpdaterUpdateNow(bpy.types.Operator):
    bl_label = "立即更新 Velo Tools 插件"
    bl_idname = updater.addon + ".updater_update_now"
    bl_description = "更新到 Velo Tools 插件的最新版本"
    bl_options = {'REGISTER', 'INTERNAL'}

    # If true, run clean install - ie remove all files before adding new
    # equivalent to deleting the addon and reinstalling, except the updater
    # folder/backup folder remains.
    clean_install: bpy.props.BoolProperty(
        name="清空后全新安装",
        description=("启用后，在安装新更新前会完全清空插件目录，"
                     "进行一次全新安装"),
        default=False,
        options={'HIDDEN'}
    )  #type: ignore

    def execute(self, context):

        # in case of error importing updater
        if updater.invalid_updater:
            return {'CANCELLED'}

        target_version = updater.version_tuple_from_text(
            updater.update_version)
        if updater.update_ready and target_version and \
                target_version <= updater.current_version:
            # Guard against stale cache surviving a manual addon install.
            updater.json_reset_restore()
            self.report({'INFO'}, "插件已是最新版本")
            return {'CANCELLED'}

        if updater.manual_only:
            bpy.ops.wm.url_open(url=updater.website)
        if updater.update_ready:
            if not _start_update_job(clean=self.clean_install):
                self.report({'INFO'}, "更新正在进行中")
                return {'CANCELLED'}
            self.report({'INFO'}, "更新已在后台开始，完成后会提示结果")
        elif updater.update_ready is None:
            (update_ready, version, link) = updater.check_for_update(now=True)
            # Re-launch this dialog.
            atr = AddonUpdaterInstallPopup.bl_idname.split(".")
            getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')

        elif not updater.update_ready:
            self.report({'INFO'}, "没有需要更新的内容")
            return {'CANCELLED'}
        else:
            self.report(
                {'ERROR'}, "尝试更新时遇到问题")
            return {'CANCELLED'}

        return {'FINISHED'}


class AddonUpdaterUpdateTarget(bpy.types.Operator):
    bl_label = "Velo Tools 指定版本"
    bl_idname = updater.addon + ".updater_update_target"
    bl_description = "安装 Velo Tools 插件的指定版本"
    bl_options = {'REGISTER', 'INTERNAL'}

    def target_version(self, context):
        # In case of error importing updater.
        if updater.invalid_updater:
            ret = []

        ret = []
        i = 0
        for tag in updater.tags:
            ret.append((tag, tag, "选择安装 " + tag))
            i += 1
        return ret

    target: bpy.props.EnumProperty(
        name="要安装的目标版本",
        description="选择要安装的版本",
        items=target_version
    )  #type: ignore

    # If true, run clean install - ie remove all files before adding new
    # equivalent to deleting the addon and reinstalling, except the
    # updater folder/backup folder remains.
    clean_install: bpy.props.BoolProperty(
        name="清空后全新安装",
        description=("启用后，在安装新更新前会完全清空插件目录，"
                     "进行一次全新安装"),
        default=False,
        options={'HIDDEN'}
    )  #type: ignore

    @classmethod
    def poll(cls, context):
        if updater.invalid_updater:
            return False
        return updater.update_ready is not None and len(updater.tags) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        if updater.invalid_updater:
            layout.label(text="更新器出错")
            return
        split = layout_split(layout, factor=0.5)
        sub_col = split.column()
        sub_col.label(text="选择安装版本")
        sub_col = split.column()
        sub_col.prop(self, "target", text="")

    def execute(self, context):
        # In case of error importing updater.
        if updater.invalid_updater:
            return {'CANCELLED'}

        if not _start_update_job(
                revert_tag=self.target,
                clean=self.clean_install):
            self.report({'INFO'}, "更新正在进行中")
            return {'CANCELLED'}
        self.report({'INFO'}, "指定版本已在后台开始安装，完成后会提示结果")
        return {'FINISHED'}


class AddonUpdaterInstallManually(bpy.types.Operator):
    """As a fallback, direct the user to download the addon manually"""
    bl_label = "手动安装更新"
    bl_idname = updater.addon + ".updater_install_manually"
    bl_description = "继续手动安装更新"
    bl_options = {'REGISTER', 'INTERNAL'}

    error: bpy.props.StringProperty(
        name="发生错误",
        default="",
        options={'HIDDEN'}
    )  #type: ignore

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self)

    def draw(self, context):
        layout = self.layout

        if updater.invalid_updater:
            layout.label(text="更新器出错")
            return

        # Display error if a prior autoamted install failed.
        if self.error != "":
            col = layout.column()
            col.scale_y = 0.7
            col.label(text="自动安装时出现问题",
                      icon="ERROR")
            col.label(text="点击下方的下载按钮，然后",
                      icon="BLANK1")
            col.label(text="像普通插件那样安装这个 zip 文件。", icon="BLANK1")
        else:
            col = layout.column()
            col.scale_y = 0.7
            col.label(text="手动安装本插件")
            col.label(text="点击下方的下载按钮，然后")
            col.label(text="像普通插件那样安装这个 zip 文件。")

        # If check hasn't happened, i.e. accidentally called this menu,
        # allow to check here.

        row = layout.row()

        if updater.update_link is not None:
            row.operator(
                "wm.url_open",
                text="直接下载").url = updater.update_link
        else:
            row.operator(
                "wm.url_open",
                text="（获取直接下载链接失败）")
            row.enabled = False

            if updater.website is not None:
                row = layout.row()
                ops = row.operator("wm.url_open", text="打开网站")
                ops.url = updater.website
            else:
                row = layout.row()
                row.label(text="请到源站网站下载更新")

    def execute(self, context):
        return {'FINISHED'}


class AddonUpdaterUpdatedSuccessful(bpy.types.Operator):
    """Addon in place, popup telling user it completed or what went wrong"""
    bl_label = "安装报告"
    bl_idname = updater.addon + ".updater_update_successful"
    bl_description = "更新安装结果"
    bl_options = {'REGISTER', 'INTERNAL', 'UNDO'}

    error: bpy.props.StringProperty(
        name="发生错误",
        default="",
        options={'HIDDEN'}
    )  #type: ignore

    def invoke(self, context, event):
        # Make sure error is correctly accessed via instance property
        return context.window_manager.invoke_props_popup(self, event)

    def draw(self, context):
        layout = self.layout

        if updater.invalid_updater:
            layout.label(text="更新器出错")
            return

        saved = updater.json

        if self.error:
            col = layout.column()
            col.scale_y = 0.7
            col.label(text="发生错误，未安装", icon="ERROR")
            msg = updater.error_msg if updater.error_msg else self.error
            col.label(text=str(msg), icon="BLANK1")
            rw = col.row()
            rw.scale_y = 2
            rw.operator(
                "wm.url_open",
                text="点击进行手动下载。",
                icon="BLANK1").url = updater.website
        elif not updater.auto_reload_post_update:
            # Tell user to restart blender after an update/restore!
            if "just_restored" in saved and saved["just_restored"]:
                col = layout.column()
                col.label(text="插件已还原", icon="RECOVER_LAST")
                alert_row = col.row()
                alert_row.alert = True
                alert_row.operator(
                    "wm.quit_blender",
                    text="重启 Blender 以重新加载",
                    icon="BLANK1")
                updater.json_reset_restore()
            else:
                col = layout.column()
                col.label(
                    text="插件安装成功", icon="FILE_TICK")
                alert_row = col.row()
                alert_row.alert = True
                alert_row.operator(
                    "wm.quit_blender",
                    text="重启 Blender 以重新加载",
                    icon="BLANK1")

        else:
            # reload addon, but still recommend they restart blender
            if "just_restored" in saved and saved["just_restored"]:
                col = layout.column()
                col.scale_y = 0.7
                col.label(text="插件已还原", icon="RECOVER_LAST")
                col.label(
                    text="建议重启 Blender 以完全重新加载。",
                    icon="BLANK1")
                updater.json_reset_restore()
            else:
                col = layout.column()
                col.scale_y = 0.7
                col.label(
                    text="插件安装成功", icon="FILE_TICK")
                col.label(
                    text="建议重启 Blender 以完全重新加载。",
                    icon="BLANK1")

    def execute(self, context):
        return {'FINISHED'}


class AddonUpdaterRestoreBackup(bpy.types.Operator):
    """Restore addon from backup"""
    bl_label = "还原备份"
    bl_idname = updater.addon + ".updater_restore_backup"
    bl_description = "从备份还原插件"
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        try:
            return os.path.isdir(os.path.join(updater.stage_path, "backup"))
        except:
            return False

    def execute(self, context):
        # in case of error importing updater
        if updater.invalid_updater:
            return {'CANCELLED'}
        updater.restore_backup()
        return {'FINISHED'}


class AddonUpdaterIgnore(bpy.types.Operator):
    """Ignore update to prevent future popups"""
    bl_label = "忽略更新"
    bl_idname = updater.addon + ".updater_ignore"
    bl_description = "忽略更新，避免以后再弹窗"
    bl_options = {'REGISTER', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        if updater.invalid_updater:
            return False
        elif updater.update_ready:
            return True
        else:
            return False

    def execute(self, context):
        # in case of error importing updater
        if updater.invalid_updater:
            return {'CANCELLED'}
        updater.ignore_update()
        self.report({"INFO"}, "在插件首选项中查看更新器选项")
        return {'FINISHED'}


class AddonUpdaterEndBackground(bpy.types.Operator):
    """Stop checking for update in the background"""
    bl_label = "停止后台检查"
    bl_idname = updater.addon + ".end_background_check"
    bl_description = "停止在后台检查更新"
    bl_options = {'REGISTER', 'INTERNAL'}

    def execute(self, context):
        # in case of error importing updater
        if updater.invalid_updater:
            return {'CANCELLED'}
        updater.stop_async_check_update()
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Handler related, to create popups
# -----------------------------------------------------------------------------


# global vars used to prevent duplicate popup handlers
ran_auto_check_install_popup = False
ran_update_success_popup = False

# global var for preventing successive calls
ran_background_check = False


@persistent
def updater_run_success_popup_handler(scene):
    global ran_update_success_popup
    ran_update_success_popup = True

    # in case of error importing updater
    if updater.invalid_updater:
        return

    try:
        if "scene_update_post" in dir(bpy.app.handlers):
            bpy.app.handlers.scene_update_post.remove(
                updater_run_success_popup_handler)
        else:
            bpy.app.handlers.depsgraph_update_post.remove(
                updater_run_success_popup_handler)
    except:
        pass

    atr = AddonUpdaterUpdatedSuccessful.bl_idname.split(".")
    getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')


@persistent
def updater_run_install_popup_handler(scene):
    global ran_auto_check_install_popup
    ran_auto_check_install_popup = True
    updater.print_verbose("Running the install popup handler.")

    # in case of error importing updater
    if updater.invalid_updater:
        return

    try:
        if "scene_update_post" in dir(bpy.app.handlers):
            bpy.app.handlers.scene_update_post.remove(
                updater_run_install_popup_handler)
        else:
            bpy.app.handlers.depsgraph_update_post.remove(
                updater_run_install_popup_handler)
    except:
        pass

    if "ignore" in updater.json and updater.json["ignore"]:
        return  # Don't do popup if ignore pressed.
    elif "version_text" in updater.json and updater.json["version_text"].get("version"):
        version = updater.json["version_text"]["version"]
        ver_tuple = updater.version_tuple_from_text(version)

        if ver_tuple < updater.current_version:
            # User probably manually installed to get the up to date addon
            # in here. Clear out the update flag using this function.
            updater.print_verbose(
                "{} updater: appears user updated, clearing flag".format(
                    updater.addon))
            updater.json_reset_restore()
            return
    atr = AddonUpdaterInstallPopup.bl_idname.split(".")
    getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')


def background_update_callback(update_ready):
    """Passed into the updater, background thread updater"""
    global ran_auto_check_install_popup
    updater.print_verbose("Running background update callback")

    # In case of error importing updater.
    if updater.invalid_updater:
        return
    if not updater.show_popups:
        return
    if not update_ready:
        return

    # See if we need add to the update handler to trigger the popup.
    handlers = []
    if "scene_update_post" in dir(bpy.app.handlers):  # 2.7x
        handlers = bpy.app.handlers.scene_update_post
    else:  # 2.8+
        handlers = bpy.app.handlers.depsgraph_update_post
    in_handles = updater_run_install_popup_handler in handlers

    if in_handles or ran_auto_check_install_popup:
        return

    if "scene_update_post" in dir(bpy.app.handlers):  # 2.7x
        bpy.app.handlers.scene_update_post.append(
            updater_run_install_popup_handler)
    else:  # 2.8+
        bpy.app.handlers.depsgraph_update_post.append(
            updater_run_install_popup_handler)
    ran_auto_check_install_popup = True
    updater.print_verbose("Attempted popup prompt")


def post_update_callback(module_name, res=None):
    """Callback for once the run_update function has completed.

    Only makes sense to use this if "auto_reload_post_update" == False,
    i.e. don't auto-restart the addon.

    Arguments:
        module_name: returns the module name from updater, but unused here.
        res: If an error occurred, this is the detail string.
    """

    # In case of error importing updater.
    if updater.invalid_updater:
        return

    if res is None:
        # This is the same code as in conditional at the end of the register
        # function, ie if "auto_reload_post_update" == True, skip code.
        updater.print_verbose(
            "{} updater: Running post update callback".format(updater.addon))

        atr = AddonUpdaterUpdatedSuccessful.bl_idname.split(".")
        getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')
        global ran_update_success_popup
        ran_update_success_popup = True
    else:
        # Some kind of error occurred and it was unable to install, offer
        # manual download instead.
        atr = AddonUpdaterUpdatedSuccessful.bl_idname.split(".")
        getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT', error=res)
    return


def ui_refresh(update_status):
    """Redraw the ui once an async thread has completed"""
    for windowManager in bpy.data.window_managers:
        for window in windowManager.windows:
            for area in window.screen.areas:
                area.tag_redraw()


def check_for_update_background():
    """Function for asynchronous background check.

    *Could* be called on register, but would be bad practice as the bare
    minimum code should run at the moment of registration (addon ticked).
    """
    if updater.invalid_updater:
        return
    global ran_background_check
    if ran_background_check:
        # Global var ensures check only happens once.
        return
    elif updater.update_ready is not None or updater.async_checking:
        # Check already happened.
        # Used here to just avoid constant applying settings below.
        return

    # Apply the UI settings.
    settings = get_user_preferences(bpy.context)
    if not settings:
        return
    updater.set_check_interval(enabled=settings.auto_check_update,
                               months=settings.updater_interval_months,
                               days=settings.updater_interval_days,
                               hours=settings.updater_interval_hours,
                               minutes=settings.updater_interval_minutes)

    # Input is an optional callback function. This function should take a bool
    # input, if true: update ready, if false: no update ready.
    updater.check_for_update_async(background_update_callback)
    ran_background_check = True


def check_for_update_nonthreaded(self, context):
    """Can be placed in front of other operators to launch when pressed"""
    if updater.invalid_updater:
        return

    # Only check if it's ready, ie after the time interval specified should
    # be the async wrapper call here.
    settings = get_user_preferences(bpy.context)
    if not settings:
        if updater.verbose:
            print("Could not get {} preferences, update check skipped".format(
                __package__))
        return
    updater.set_check_interval(enabled=settings.auto_check_update,
                               months=settings.updater_interval_months,
                               days=settings.updater_interval_days,
                               hours=settings.updater_interval_hours,
                               minutes=settings.updater_interval_minutes)

    (update_ready, version, link) = updater.check_for_update(now=False)
    if update_ready:
        atr = AddonUpdaterInstallPopup.bl_idname.split(".")
        getattr(getattr(bpy.ops, atr[0]), atr[1])('INVOKE_DEFAULT')
    else:
        updater.print_verbose("No update ready")
        self.report({'INFO'}, "No update ready")


def show_reload_popup():
    """For use in register only, to show popup after re-enabling the addon.

    Must be enabled by developer.
    """
    if updater.invalid_updater:
        return
    saved_state = updater.json
    global ran_update_success_popup

    has_state = saved_state is not None
    just_updated = "just_updated" in saved_state
    updated_info = saved_state["just_updated"]

    if not (has_state and just_updated and updated_info):
        return

    updater.json_reset_postupdate()  # So this only runs once.

    # No handlers in this case.
    if not updater.auto_reload_post_update:
        return

    # See if we need add to the update handler to trigger the popup.
    handlers = []
    if "scene_update_post" in dir(bpy.app.handlers):  # 2.7x
        handlers = bpy.app.handlers.scene_update_post
    else:  # 2.8+
        handlers = bpy.app.handlers.depsgraph_update_post
    in_handles = updater_run_success_popup_handler in handlers

    if in_handles or ran_update_success_popup:
        return

    if "scene_update_post" in dir(bpy.app.handlers):  # 2.7x
        bpy.app.handlers.scene_update_post.append(
            updater_run_success_popup_handler)
    else:  # 2.8+
        bpy.app.handlers.depsgraph_update_post.append(
            updater_run_success_popup_handler)
    ran_update_success_popup = True


# -----------------------------------------------------------------------------
# Example UI integrations
# -----------------------------------------------------------------------------
def update_notice_box_ui(self, context):
    """Update notice draw, to add to the end or beginning of a panel.

    After a check for update has occurred, this function will draw a box
    saying an update is ready, and give a button for: update now, open website,
    or ignore popup. Ideal to be placed at the end / beginning of a panel.
    """

    if updater.invalid_updater:
        return

    saved_state = updater.json
    if not updater.auto_reload_post_update:
        if "just_updated" in saved_state and saved_state["just_updated"]:
            layout = self.layout
            box = layout.box()
            col = box.column()
            alert_row = col.row()
            alert_row.alert = True
            alert_row.operator(
                "wm.quit_blender",
                text="Restart blender",
                icon="ERROR")
            col.label(text="to complete update")
            return

    # If user pressed ignore, don't draw the box.
    if "ignore" in updater.json and updater.json["ignore"]:
        return
    if not updater.update_ready:
        return

    layout = self.layout
    box = layout.box()
    col = box.column(align=True)
    col.alert = True
    col.label(text="Update ready!", icon="ERROR")
    col.alert = False
    col.separator()
    row = col.row(align=True)
    split = row.split(align=True)
    colL = split.column(align=True)
    colL.scale_y = 1.5
    colL.operator(AddonUpdaterIgnore.bl_idname, icon="X", text="Ignore")
    colR = split.column(align=True)
    colR.scale_y = 1.5
    if not updater.manual_only:
        colR.operator(AddonUpdaterUpdateNow.bl_idname,
                      text="Update", icon="LOOP_FORWARDS")
        col.operator("wm.url_open", text="Open website").url = updater.website
        # ops = col.operator("wm.url_open",text="Direct download")
        # ops.url=updater.update_link
        col.operator(AddonUpdaterInstallManually.bl_idname,
                     text="Install manually")
    else:
        # ops = col.operator("wm.url_open", text="Direct download")
        # ops.url=updater.update_link
        col.operator("wm.url_open", text="Get it now").url = updater.website


def update_settings_ui(self, context, element=None):
    """Preferences - for drawing with full width inside user preferences

    A function that can be run inside user preferences panel for prefs UI.
    Place inside UI draw using:
        addon_updater_ops.update_settings_ui(self, context)
    or by:
        addon_updater_ops.update_settings_ui(context)
    """

    # Element is a UI element, such as layout, a row, column, or box.
    if element is None:
        element = self.layout
    box = element.box()

    # In case of error importing updater.
    if updater.invalid_updater:
        box.label(text="更新器代码初始化出错：")
        box.label(text=updater.error_msg)
        return
    settings = get_user_preferences(context)
    if not settings:
        box.label(text="获取更新器首选项出错", icon='ERROR')
        return

    # auto-update settings
    # box.label(text="Updater Settings")
    row = box.row()

    # special case to tell user to restart blender, if set that way
    if not updater.auto_reload_post_update:
        saved_state = updater.json
        if "just_updated" in saved_state and saved_state["just_updated"]:
            row.alert = True
            row.operator("wm.quit_blender",
                         text="重启 Blender 以完成更新",
                         icon="ERROR")
            return

    # Checking / managing updates.
    row = box.row()
    col = row.column()
    if updater.error is not None:
        sub_col = col.row(align=True)
        sub_col.scale_y = 1
        split = sub_col.split(align=True)
        split.scale_y = 2
        if "ssl" in updater.error_msg.lower():
            split.enabled = True
            split.operator(AddonUpdaterInstallManually.bl_idname,
                           text=updater.error)
        else:
            split.enabled = False
            split.operator(AddonUpdaterCheckNow.bl_idname,
                           text=updater.error)
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname,
                       text="", icon="FILE_REFRESH")

    elif updater.update_ready is None and not updater.async_checking:
        col.scale_y = 2
        col.operator(AddonUpdaterCheckNow.bl_idname)
    elif updater.update_ready is None:  # async is running
        sub_col = col.row(align=True)
        sub_col.scale_y = 1
        split = sub_col.split(align=True)
        split.enabled = False
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname, text="检查中…")
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterEndBackground.bl_idname, text="", icon="X")

    elif updater.include_branches and \
            len(updater.tags) == len(updater.include_branch_list) and not \
            updater.manual_only:
        # No releases found, but still show the appropriate branch.
        sub_col = col.row(align=True)
        sub_col.scale_y = 1
        split = sub_col.split(align=True)
        split.scale_y = 2
        update_now_txt = "直接更新到 {}".format(
            updater.include_branch_list[0])
        split.operator(AddonUpdaterUpdateNow.bl_idname, text=update_now_txt)
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname,
                       text="", icon="FILE_REFRESH")

    elif updater.update_ready and not updater.manual_only:
        sub_col = col.row(align=True)
        sub_col.scale_y = 1
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterUpdateNow.bl_idname,
                       text="立即更新到 " + str(updater.update_version))
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname,
                       text="", icon="FILE_REFRESH")

    elif updater.update_ready and updater.manual_only:
        col.scale_y = 2
        dl_now_txt = "下载 " + str(updater.update_version)
        col.operator("wm.url_open",
                     text=dl_now_txt).url = updater.website
    else:  # i.e. that updater.update_ready == False.
        sub_col = col.row(align=True)
        sub_col.scale_y = 1
        split = sub_col.split(align=True)
        split.enabled = False
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname,
                       text="插件已是最新")
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname,
                       text="", icon="FILE_REFRESH")

    if not updater.manual_only:
        col = row.column(align=True)
        if updater.include_branches and len(updater.include_branch_list) > 0:
            branch = updater.include_branch_list[0]
            col.operator(AddonUpdaterUpdateTarget.bl_idname,
                         text="安装 {} / 旧版本".format(branch))
        else:
            col.operator(AddonUpdaterUpdateTarget.bl_idname,
                         text="（重新）安装插件版本")
        last_date = "无"
        backup_path = os.path.join(updater.stage_path, "backup")
        if "backup_date" in updater.json and os.path.isdir(backup_path):
            if updater.json["backup_date"] == "":
                last_date = "未找到日期"
            else:
                last_date = updater.json["backup_date"]
        backup_text = "还原插件备份（{}）".format(last_date)
        col.operator(AddonUpdaterRestoreBackup.bl_idname, text=backup_text)

    row = box.row()
    
    split = layout_split(row, factor=0.4)
    sub_col = split.column()
    sub_col.prop(settings, "auto_check_update")
    sub_col = split.column()

    if not settings.auto_check_update:
        sub_col.enabled = False
    sub_row = sub_col.row()
    sub_row.label(text="检查间隔")
    sub_row = sub_col.row(align=True)
    check_col = sub_row.column(align=True)
    check_col.prop(settings, "updater_interval_months")
    check_col = sub_row.column(align=True)
    check_col.prop(settings, "updater_interval_days")
    check_col = sub_row.column(align=True)

    # Consider un-commenting for local dev (e.g. to set shorter intervals)
    # check_col.prop(settings,"updater_interval_hours")
    # check_col = sub_row.column(align=True)
    # check_col.prop(settings,"updater_interval_minutes")

    row = box.row()
    row.scale_y = 0.7
    last_check = updater.json["last_check"]
    if updater.error is not None and updater.error_msg is not None:
        row.label(text=updater.error_msg)
    elif last_check:
        last_check = last_check[0: last_check.index(".")]
        row.label(text="上次检查更新：" + last_check)
    else:
        row.label(text="上次检查更新：从未")


def update_settings_ui_condensed(self, context, element=None):
    """Preferences - Condensed drawing within preferences.

    Alternate draw for user preferences or other places, does not draw a box.
    """

    # Element is a UI element, such as layout, a row, column, or box.
    if element is None:
        element = self.layout
    row = element.row()

    # In case of error importing updater.
    if updater.invalid_updater:
        row.label(text="Error initializing updater code:")
        row.label(text=updater.error_msg)
        return
    settings = get_user_preferences(context)
    if not settings:
        row.label(text="Error getting updater preferences", icon='ERROR')
        return

    # Special case to tell user to restart blender, if set that way.
    if not updater.auto_reload_post_update:
        saved_state = updater.json
        if "just_updated" in saved_state and saved_state["just_updated"]:
            row.alert = True  # mark red
            row.operator(
                "wm.quit_blender",
                text="Restart blender to complete update",
                icon="ERROR")
            return

    col = row.column()
    if updater.error is not None:
        sub_col = col.row(align=True)
        sub_col.scale_y = 1
        split = sub_col.split(align=True)
        split.scale_y = 2
        if "ssl" in updater.error_msg.lower():
            split.enabled = True
            split.operator(AddonUpdaterInstallManually.bl_idname,
                           text=updater.error)
        else:
            split.enabled = False
            split.operator(AddonUpdaterCheckNow.bl_idname,
                           text=updater.error)
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname,
                       text="", icon="FILE_REFRESH")

    elif updater.update_ready is None and not updater.async_checking:
        col.scale_y = 2
        col.operator(AddonUpdaterCheckNow.bl_idname)
    elif updater.update_ready is None:  # Async is running.
        sub_col = col.row(align=True)
        sub_col.scale_y = 1
        split = sub_col.split(align=True)
        split.enabled = False
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname, text="检查中…")
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterEndBackground.bl_idname, text="", icon="X")

    elif updater.include_branches and \
            len(updater.tags) == len(updater.include_branch_list) and not \
            updater.manual_only:
        # No releases found, but still show the appropriate branch.
        sub_col = col.row(align=True)
        sub_col.scale_y = 1
        split = sub_col.split(align=True)
        split.scale_y = 2
        now_txt = "Update directly to " + str(updater.include_branch_list[0])
        split.operator(AddonUpdaterUpdateNow.bl_idname, text=now_txt)
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname,
                       text="", icon="FILE_REFRESH")

    elif updater.update_ready and not updater.manual_only:
        sub_col = col.row(align=True)
        sub_col.scale_y = 1
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterUpdateNow.bl_idname,
                       text="立即更新到 " + str(updater.update_version))
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname,
                       text="", icon="FILE_REFRESH")

    elif updater.update_ready and updater.manual_only:
        col.scale_y = 2
        dl_txt = "Download " + str(updater.update_version)
        col.operator("wm.url_open", text=dl_txt).url = updater.website
    else:  # i.e. that updater.update_ready == False.
        sub_col = col.row(align=True)
        sub_col.scale_y = 1
        split = sub_col.split(align=True)
        split.enabled = False
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname,
                       text="插件已是最新")
        split = sub_col.split(align=True)
        split.scale_y = 2
        split.operator(AddonUpdaterCheckNow.bl_idname,
                       text="", icon="FILE_REFRESH")

    row = element.row()
    row.prop(settings, "auto_check_update")

    row = element.row()
    row.scale_y = 0.7
    last_check = updater.json["last_check"]
    if updater.error is not None and updater.error_msg is not None:
        row.label(text=updater.error_msg)
    elif last_check != "" and last_check is not None:
        last_check = last_check[0: last_check.index(".")]
        row.label(text="Last check: " + last_check)
    else:
        row.label(text="Last check: Never")


def skip_tag_function(self, tag):
    """A global function for tag skipping.

    A way to filter which tags are displayed, e.g. to limit downgrading too
    long ago.

    Args:
        self: The instance of the singleton addon update.
        tag: the text content of a tag from the repo, e.g. "v1.2.3".

    Returns:
        bool: True to skip this tag name (ie don't allow for downloading this
            version), or False if the tag is allowed.
    """

    # In case of error importing updater.
    if self.invalid_updater:
        return False

    # ---- Velo Tools custom filter ---- #
    # Hide GitHub pre-releases from regular users. With use_releases=True each
    # tag is a release dict carrying a "prerelease" flag; only show pre-releases
    # when the user opted in via the "receive_prereleases" preference. See ADR 0002.
    if isinstance(tag, dict) and tag.get("prerelease"):
        prefs = get_user_preferences(bpy.context)
        if not (prefs and getattr(prefs, "receive_prereleases", False)):
            return True
    # ---- end Velo Tools custom filter ---- #

    if self.include_branches:
        for branch in self.include_branch_list:
            if tag["name"].lower() == branch:
                return False

    # Function converting string to tuple, ignoring e.g. leading 'v'.
    # Be aware that this strips out other text that you might otherwise
    # want to be kept and accounted for when checking tags (e.g. v1.1a vs 1.1b)
    tupled = self.version_tuple_from_text(tag["name"])
    if not isinstance(tupled, tuple):
        return True

    # Select the min tag version - change tuple accordingly.
    if self.version_min_update is not None:
        if tupled < self.version_min_update:
            return True  # Skip if current version below this.

    # Select the max tag version.
    if self.version_max_update is not None:
        if tupled >= self.version_max_update:
            return True  # Skip if current version at or above this.

    # In all other cases, allow showing the tag for updating/reverting.
    # To simply and always show all tags, this return False could be moved
    # to the start of the function definition so all tags are allowed.
    return False


def select_link_function(self, tag):
    """Only customize if trying to leverage "attachments" in *GitHub* releases.

    A way to select from one or multiple attached downloadable files from the
    server, instead of downloading the default release/tag source code.
    """

    # Velo Tools publishes its installable zip as a GitHub release ASSET
    # (built by pack.ps1; single top-level folder "velo_tools/"). Always prefer
    # that asset. Returning None when a release has no attached .zip asset makes
    # the updater degrade to the manual-install popup instead of mis-installing
    # the source zipball, whose folder layout differs from the asset. See ADR 0002.
    for asset in (tag.get("assets") or []):
        url = asset.get("browser_download_url", "")
        if url.endswith(".zip"):
            return url
    return None


# -----------------------------------------------------------------------------
# Register, should be run in the register module itself
# -----------------------------------------------------------------------------
classes = (
    AddonUpdaterInstallPopup,
    AddonUpdaterCheckNow,
    AddonUpdaterUpdateNow,
    AddonUpdaterUpdateTarget,
    AddonUpdaterInstallManually,
    AddonUpdaterUpdatedSuccessful,
    AddonUpdaterRestoreBackup,
    AddonUpdaterIgnore,
    AddonUpdaterEndBackground
)


def register():
    """Registering the operators in this module"""
    # Safer failure in case of issue loading module.
    if updater.error:
        print("Exiting updater registration, " + updater.error)
        return
    updater.clear_state()  # Clear internal vars, avoids reloading oddities.
    updater.engine = "Github"
    updater.user = "visaokc"
    updater.repo = "Velo-Tools"
    # updater.addon = # define at top of module, MUST be done first
    updater.website = "https://github.com/visaokc/Velo-Tools/releases"
    # Velo Tools ships its install zip as a GitHub release asset (built by
    # pack.ps1); that asset's single top-level folder is "velo_tools/" and the
    # updater auto-descends into it, so no subfolder is needed. See ADR 0002.
    updater.subfolder_path = ""
    updater.current_version = bl_info["version"]
    updater.verbose = False  # make False for production default
    updater.backup_current = True  # True by default
    updater.backup_ignore_patterns = ["__pycache__", "velo_tools_updater"]
    updater.overwrite_patterns = ["*.png", "*.jpg", "README.md", "LICENSE.txt", "*.j2"]
    updater.remove_pre_update_patterns = ["*.py", "*.pyc"]
    # Only published GitHub Releases drive updates -- never branch tips. This
    # keeps ordinary dev pushes to main invisible to users (see ADR 0002).
    # include_branch_list is left at its engine default: with include_branches
    # off it is never consulted, and its setter rejects an empty list.
    updater.include_branches = False
    updater.use_releases = True
    updater.manual_only = False

    # Used for development only, "pretend" to install an update to test
    # reloading conditions.
    updater.fake_install = False  # Set to true to test callback/reloading.
    updater.show_popups = True
    updater.version_min_update = (0, 0, 0)
    updater.version_max_update = None  # None or default for no max.
    updater.skip_tag = skip_tag_function  # min and max used in this function
    updater.select_link = select_link_function
    updater.auto_reload_post_update = False
    updater.cleanup_staging()
    updater.cleanup_legacy_dependencies()
    # Special situation: we just updated the addon, show a popup to tell the
    # user it worked. Could enclosed in try/catch in case other issues arise.
    show_reload_popup()


def unregister():
    # Clear global vars since they may persist if not restarting blender.
    updater.clear_state()  # Clear internal vars, avoids reloading oddities.

    global ran_auto_check_install_popup
    ran_auto_check_install_popup = False

    global ran_update_success_popup
    ran_update_success_popup = False

    global ran_background_check
    ran_background_check = False
