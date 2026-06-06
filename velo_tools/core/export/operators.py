"""Operators for export preprocessing + handing off to the adapter export (V0.1.5)."""
from __future__ import annotations

import bpy
from bpy.props import BoolProperty, StringProperty

from . import preexport, adapters


def _get_settings(context):
    return getattr(context.scene, "velo_endfield", None)


def _get_source(context):
    s = _get_settings(context)
    if s is None:
        return None
    obj = s.mmd_source_object
    if obj and obj.type == 'MESH':
        return obj
    return None


class VELO_OT_mmd_pre_export(bpy.types.Operator):
    bl_idname = "velo.mmd_pre_export"
    bl_label = "执行 MMD 导出预处理"
    bl_description = (
        "对 MMD 源物体执行：按映射改名为统一编号 → 删除特殊命名 VG → 删除无权重 VG。"
        "用于在调起 EFMI / WWMI 导出之前清理出符合导出器约束的网格。"
    )
    bl_options = {'REGISTER', 'UNDO'}

    drop_special: BoolProperty(name="删除特殊命名 VG", default=True)
    drop_empty: BoolProperty(name="删除无权重 VG", default=True)
    rename_to_unified: BoolProperty(name="改名为统一编号", default=True)

    def execute(self, context):
        s = _get_settings(context)
        obj = _get_source(context)
        if s is None or obj is None:
            self.report({'ERROR'}, "请先在 MMD↔统一编号映射 面板顶部指定 MMD 源物体")
            return {'CANCELLED'}
        profile = s.mmd_profile if s else None
        r = preexport.apply_mmd_pre_export(
            obj, profile,
            drop_special=self.drop_special,
            drop_empty=self.drop_empty,
            rename_to_unified=self.rename_to_unified,
        )
        try:
            from .. import overlay as _ov  # this file lives in core/export/, so go back up to the velo_tools package
        except Exception:
            _ov = None
        try:
            from ... import overlay as _ov2
            if hasattr(_ov2, "invalidate_mmd_cache"):
                _ov2.invalidate_mmd_cache()
        except Exception:
            pass
        self.report(
            {'INFO'},
            f"预处理完成: 改名 {r['renamed']}, 合并 {r['merged']}, "
            f"删特殊 {r['dropped_special']}, 删空 {r['dropped_empty']}",
        )
        return {'FINISHED'}


class VELO_OT_invoke_game_export(bpy.types.Operator):
    bl_idname = "velo.invoke_game_export"
    bl_label = "预处理 + 转交目标导出器"
    bl_description = (
        "先对 MMD 源物体执行导出预处理，然后调起当前选定的导出适配器（EFMI / WWMI）"
    )
    bl_options = {'REGISTER'}

    do_preprocess: BoolProperty(name="先执行预处理", default=True)

    def execute(self, context):
        s = _get_settings(context)
        if s is None:
            self.report({'ERROR'}, "未找到终末地映射数据")
            return {'CANCELLED'}

        obj = _get_source(context)
        if self.do_preprocess:
            if obj is None:
                self.report({'ERROR'}, "请先指定 MMD 源物体（或取消「先执行预处理」）")
                return {'CANCELLED'}
            preexport.apply_mmd_pre_export(obj, s.mmd_profile)

        # single active-game switch: derive the adapter key from velo_tools.active_game via the game registry
        from ...games import registry as _registry
        desc = _registry.get_active_descriptor(context.scene)
        adapter_key = desc.adapter_key if desc is not None else "EFMI"
        is_avail, invoke = adapters.get_adapter(adapter_key)
        if not is_avail():
            self.report(
                {'ERROR'},
                f"目标导出器 {adapter_key} 未检测到，请安装/启用对应插件后重试",
            )
            return {'CANCELLED'}
        result = invoke(context)
        if not result.get("ok"):
            self.report({'ERROR'}, result.get("msg", "调用导出失败"))
            return {'CANCELLED'}
        self.report({'INFO'}, result.get("msg", "已调起导出"))
        return {'FINISHED'}


_classes = (VELO_OT_mmd_pre_export, VELO_OT_invoke_game_export)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
