"""Cross-scene merge UI -- the "base + each IB (with role) + output -> merge" panel + operators.

The merge operator calls the producer ``xscene_merge.build_cross_scene_merge`` to produce an
editable merged folder + ``CrossSceneRouting.json``; afterwards the user imports that folder with
the existing import operator, edits one mesh, and clicks the regular "Export Mod" -- the hook in
``patch.py`` then automatically folds and merges out a mod that works across all scenes.

There are only two roles: ``Fold`` (fold into base) and ``Editable`` (independently editable, as a new component).
Registration is handled by this module's register()/unregister() (new classes + Scene properties), not via _wwmi_core's auto_load.
"""
import traceback
from pathlib import Path

import bpy

_ROLES = [
    ('fold', '折入基底',
     '该 IB 折入基底：编辑基底即覆盖它（格式兼容则重定向到基底 buffer，骨数等不兼容则出独立 buffer，自动判定）'),
    ('editable', '独立可编辑',
     '独立可编辑：几何不属于基底（如另一形态），作为新 component 单独导入编辑、单独导出'),
]


class VTWW_XSceneIB(bpy.types.PropertyGroup):
    folder: bpy.props.StringProperty(name="IB 文件夹", subtype='DIR_PATH',
                                     description="某 IB 的提取文件夹（文件夹名即其 hash）")
    role: bpy.props.EnumProperty(name="角色", items=_ROLES, default='fold')


class VTWW_UL_xscene_ibs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_prop, index=0):
        row = layout.row(align=True)
        row.prop(item, "role", text="")
        row.prop(item, "folder", text="")


class VTWW_OT_xscene_ib_add(bpy.types.Operator):
    bl_idname = "vtww.xscene_ib_add"
    bl_label = "添加 IB"
    bl_description = "新增一条 IB"

    def execute(self, context):
        s = context.scene
        s.vtww_xscene_ibs.add()
        s.vtww_xscene_ib_index = len(s.vtww_xscene_ibs) - 1
        return {'FINISHED'}


class VTWW_OT_xscene_ib_remove(bpy.types.Operator):
    bl_idname = "vtww.xscene_ib_remove"
    bl_label = "移除 IB"
    bl_description = "移除选中的 IB"

    def execute(self, context):
        s = context.scene
        i = s.vtww_xscene_ib_index
        if 0 <= i < len(s.vtww_xscene_ibs):
            s.vtww_xscene_ibs.remove(i)
            s.vtww_xscene_ib_index = max(0, i - 1)
        return {'FINISHED'}


class VTWW_OT_xscene_merge(bpy.types.Operator):
    bl_idname = "vtww.xscene_merge"
    bl_label = "合并跨场景"
    bl_description = "合并基底 + 各 IB → 一份可编辑提取文件夹 + CrossSceneRouting.json"

    def execute(self, context):
        s = context.scene
        base = bpy.path.abspath(s.vtww_xscene_base) if s.vtww_xscene_base else ""
        out = bpy.path.abspath(s.vtww_xscene_out) if s.vtww_xscene_out else ""
        if not base or not Path(base).is_dir():
            self.report({'ERROR'}, "请设置有效的基底文件夹（整角色基底提取）")
            return {'CANCELLED'}
        if not out:
            self.report({'ERROR'}, "请设置输出文件夹")
            return {'CANCELLED'}
        specs, editable = [], []
        for ib in s.vtww_xscene_ibs:
            f = bpy.path.abspath(ib.folder) if ib.folder else ""
            if not f or not Path(f).is_dir():
                continue
            rec = {"hash": Path(f).name, "folder": f, "role": ib.role}
            (editable if ib.role == 'editable' else specs).append(rec)
        if not specs and not editable:
            self.report({'ERROR'}, "请至少添加一条有效的 IB")
            return {'CANCELLED'}
        try:
            from ... import xscene_merge
            rep = xscene_merge.build_cross_scene_merge(base, specs, out, editable_ibs=editable or None)
        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, "合并失败：%s（详见系统控制台）" % exc)
            return {'CANCELLED'}
        # Sanity hints: producer detected IBs that "look like an independent form but were not set to Editable" -> emit one WARNING each.
        for w in rep.get("warnings", []):
            self.report({'WARNING'}, w)
        pruned = rep.get("root_textures_pruned") or []
        msg = "合并完成 → %s | components=%d splits=%d fold=%d editable=%d" % (
            out, rep.get("base_components", 0), len(rep.get("splits", [])),
            len(specs), len(editable))
        if pruned:
            msg += " | pruned %d redundant root texture(s)" % len(pruned)
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class VELO_PT_xscene(bpy.types.Panel):
    bl_idname = "VELO_PT_xscene"
    bl_label = "跨场景折叠合并"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Velo Tools"
    bl_parent_id = "VELO_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        # Only shown when on the "Game" tab and the active game is switched to Wuthering Waves (WWMI) -- cross-scene folding is a WWMI-exclusive feature.
        vt = getattr(context.scene, "velo_tools", None)
        return (vt is not None
                and getattr(vt, "active_tab", "") == 'GAME'
                and getattr(vt, "active_game", "") == 'WUTHERING'
                and hasattr(context.scene, "VTWW_settings"))

    def draw(self, context):
        s = context.scene
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="1) 合并：基底 + IB → 合并文件夹")
        col.prop(s, "vtww_xscene_base", text="基底")
        box = layout.box()
        box.label(text="IB 列表（含角色）")
        row = box.row()
        row.template_list("VTWW_UL_xscene_ibs", "", s, "vtww_xscene_ibs", s, "vtww_xscene_ib_index", rows=4)
        bcol = row.column(align=True)
        bcol.operator("vtww.xscene_ib_add", text="", icon='ADD')
        bcol.operator("vtww.xscene_ib_remove", text="", icon='REMOVE')
        layout.prop(s, "vtww_xscene_out", text="输出")
        layout.operator("vtww.xscene_merge", icon='AUTOMERGE_ON')
        layout.separator()
        col2 = layout.column(align=True)
        col2.label(text="2) 用上方「导入对象」导入合并文件夹")
        col2.label(text="3) 编辑一份网格")
        col2.label(text="4) 点「导出 Mod」→ 自动折叠出多场景 mod")


_CLASSES = (
    VTWW_XSceneIB,
    VTWW_UL_xscene_ibs,
    VTWW_OT_xscene_ib_add,
    VTWW_OT_xscene_ib_remove,
    VTWW_OT_xscene_merge,
    VELO_PT_xscene,
)


def register():
    for cls in _CLASSES:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            traceback.print_exc()
    bpy.types.Scene.vtww_xscene_base = bpy.props.StringProperty(
        name="基底文件夹", subtype='DIR_PATH',
        description="整角色基底的提取文件夹（单 IB）")
    bpy.types.Scene.vtww_xscene_out = bpy.props.StringProperty(
        name="输出文件夹", subtype='DIR_PATH',
        description="合并提取文件夹的输出位置")
    bpy.types.Scene.vtww_xscene_ibs = bpy.props.CollectionProperty(type=VTWW_XSceneIB)
    bpy.types.Scene.vtww_xscene_ib_index = bpy.props.IntProperty(default=0)


def unregister():
    for attr in ("vtww_xscene_ib_index", "vtww_xscene_ibs", "vtww_xscene_out", "vtww_xscene_base"):
        if hasattr(bpy.types.Scene, attr):
            try:
                delattr(bpy.types.Scene, attr)
            except Exception:
                pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
