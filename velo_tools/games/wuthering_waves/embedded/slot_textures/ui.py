# UI for the WWMI slot-style texture layer (velo-owned, registered like
# embedded/lod/ui.py; nothing added to _wwmi_core's auto_load).
#
#   - VTWW_Settings gains `velo_slot_style_textures` (annotation injection,
#     EFMI-style; restored on unregister) shown in a "Velo 兼容选项" box
#     appended to the stock Export Mod menu (method wrap, original restored).
#   - A standalone sub-panel in EXTRACT_FRAME_DATA mode merges extra-form RAW
#     frame dumps into ShaderTextureUsage.json's "extra_forms" key and copies
#     the form's textures into the object folder (multi-form characters need
#     one dump per form; see ADR 0006/0007).

import traceback

import bpy

_MISSING = object()
_orig_slot_style_annotation = _MISSING
_orig_draw_menu_export_mod = None


# ------------------------------------------------------- settings injection --

def inject_settings():
    """Adds the export option to VTWW_Settings. Must run BEFORE the vendored
    settings class is registered (same constraint as _patch_tool_mode)."""
    global _orig_slot_style_annotation
    from ..._wwmi_core.addon import settings as _wsettings

    _orig_slot_style_annotation = _wsettings.VTWW_Settings.__annotations__.get(
        "velo_slot_style_textures", _MISSING)
    _wsettings.VTWW_Settings.__annotations__["velo_slot_style_textures"] = bpy.props.BoolProperty(
        name="插槽风格贴图 (Slot-Style Textures)",
        description=(
            "用槽位重绑替代逐 hash 贴图段：贴图在组件 draw 范围内按槽位绑定，"
            "不再匹配游戏贴图 hash，因此对纹理流送（3.4 起每个 mip 级独立 hash）免疫。"
            "多形态角色需先在提取页用「合并形态贴图数据」逐形态合并 dump。"
            "关闭则按原版 hash 风格导出（与未升级版本逐字节一致）"
        ),
        default=True,
    )


def restore_settings():
    global _orig_slot_style_annotation
    from ..._wwmi_core.addon import settings as _wsettings

    if _orig_slot_style_annotation is _MISSING:
        _wsettings.VTWW_Settings.__annotations__.pop("velo_slot_style_textures", None)
    else:
        _wsettings.VTWW_Settings.__annotations__["velo_slot_style_textures"] = (
            _orig_slot_style_annotation)
    _orig_slot_style_annotation = _MISSING


# --------------------------------------------------------- export menu wrap --

def _patch_export_menu():
    global _orig_draw_menu_export_mod
    from ..._wwmi_core.addon import ui as _wui

    if _orig_draw_menu_export_mod is not None:
        return
    _orig_draw_menu_export_mod = _wui.VTWW_PT_SIDEBAR.draw_menu_export_mod

    def draw_menu_export_mod(self, context):
        _orig_draw_menu_export_mod(self, context)
        cfg = context.scene.VTWW_settings
        layout = self.layout
        box = layout.box()
        box.label(text="Velo 兼容选项", icon="TOOL_SETTINGS")
        if hasattr(cfg, "velo_slot_style_textures"):
            box.prop(cfg, "velo_slot_style_textures")
            slot_cfg = getattr(context.scene, "vtww_slot_settings", None)
            if (slot_cfg is not None and cfg.velo_slot_style_textures):
                box.prop(slot_cfg, "form_anchors")

    _wui.VTWW_PT_SIDEBAR.draw_menu_export_mod = draw_menu_export_mod


def _restore_export_menu():
    global _orig_draw_menu_export_mod
    from ..._wwmi_core.addon import ui as _wui

    if _orig_draw_menu_export_mod is not None:
        _wui.VTWW_PT_SIDEBAR.draw_menu_export_mod = _orig_draw_menu_export_mod
        _orig_draw_menu_export_mod = None


# ------------------------------------------------------- form merge UI ------

class VTWW_SlotTextureSettings(bpy.types.PropertyGroup):

    form_dump_folder: bpy.props.StringProperty(
        name="形态 Frame Dump",
        description="另一形态的原始帧转储目录（无需二次提取角色文件夹）。"
                    "在该形态下近距离抓帧，保证材质贴图全量绑定",
        default='',
        subtype="DIR_PATH",
    )

    form_label: bpy.props.StringProperty(
        name="形态标签",
        description="可选：该形态在合并数据里的显示名（留空自动编号）。"
                    "同一标签再合并不同距离的 dump 会收割该形态贴图的其它流送级 "
                    "hash（缩短形态切换的检测延迟）；填 base 表示收割进基础提取数据",
        default='',
    )

    form_anchors: bpy.props.StringProperty(
        name="形态锚点 (Form Anchors)",
        description="可选：手动指定形态独占的锚点 hash，格式 hash:形态标签，"
                    "逗号/空格分隔（如 ce56ef1a:base）。基础形态标签固定为 base，"
                    "其余用合并时起的标签。命中即零延迟锁存形态；版本更新失效后"
                    "自动退回贴图锁存（有流送延迟）。推荐用形态独占的 ib/vb"
                    "（即时且较稳）；16 位 shader hash 也可（即时但版本必变）；"
                    "贴图 hash 可用但有流送延迟与任意绑定触发风险",
        default='',
    )


class VTWW_OT_merge_form_textures(bpy.types.Operator):
    bl_idname = "vtww.merge_form_textures"
    bl_label = "合并形态贴图数据 (Merge Form Dump)"
    bl_description = ("解析另一形态的原始帧转储，把它的 (组件 x 着色器对 x 槽位) 贴图表"
                      "合并进模型文件夹 ShaderTextureUsage.json 的 extra_forms 键（支持任意"
                      "数量形态），供插槽风格导出生成形态检测与 per-form 分支。"
                      "同名 dump 重复合并会覆盖旧条目")

    def execute(self, context):
        cfg = context.scene.VTWW_settings
        slot_cfg = context.scene.vtww_slot_settings
        try:
            from ..._wwmi_core.migoto_io.blender_interface.utility import resolve_path
            from . import form_merge
            summary = form_merge.merge_form_dump(
                resolve_path(cfg.object_source_folder),
                resolve_path(slot_cfg.form_dump_folder),
                slot_cfg.form_label,
                texture_filter=form_merge.texture_filter_from_cfg(cfg),
            )
        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, f"形态合并失败：{exc}")
            return {'CANCELLED'}

        action = "覆盖" if summary['replaced'] else "新增"
        harvest = (f"，收割 {summary['variants_added']} 个残留度变体"
                   if summary.get('variants_added') else "")
        self.report(
            {'INFO'},
            f"已{action}形态「{summary['label']}」（按 {summary['matched_by']} 匹配，"
            f"{summary['components']} 组件 / {summary['pairs']} 着色器对，"
            f"拷入 {summary['textures_copied']} 张该形态贴图{harvest}），"
            f"当前共 {summary['total_forms']} 个形态。"
        )
        return {'FINISHED'}


class VELO_PT_wwmi_slot_forms(bpy.types.Panel):
    bl_idname = "VELO_PT_wwmi_slot_forms"
    bl_label = "形态贴图合并 (Merge Form Textures)"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Velo Tools"
    bl_parent_id = "VELO_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        # Same gating as the LOD extraction panel: WWMI game tab, extraction
        # mode (reads as the next step of the extraction workflow).
        vt = getattr(context.scene, "velo_tools", None)
        if (vt is None
                or getattr(vt, "active_tab", "") != 'GAME'
                or getattr(vt, "active_game", "") != 'WUTHERING'):
            return False
        wwmi_cfg = getattr(context.scene, "VTWW_settings", None)
        return wwmi_cfg is not None and wwmi_cfg.tool_mode == 'EXTRACT_FRAME_DATA'

    def draw(self, context):
        layout = self.layout
        cfg = context.scene.vtww_slot_settings
        wwmi_cfg = context.scene.VTWW_settings

        layout.row().prop(cfg, 'form_dump_folder')
        layout.row().prop(wwmi_cfg, 'object_source_folder')
        layout.row().prop(cfg, 'form_label')

        layout.separator()
        layout.row().operator(VTWW_OT_merge_form_textures.bl_idname, icon='TEXTURE')


_CLASSES = (
    VTWW_SlotTextureSettings,
    VTWW_OT_merge_form_textures,
    VELO_PT_wwmi_slot_forms,
)


def register():
    for cls in _CLASSES:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            traceback.print_exc()
    bpy.types.Scene.vtww_slot_settings = bpy.props.PointerProperty(type=VTWW_SlotTextureSettings)
    _patch_export_menu()


def unregister():
    _restore_export_menu()
    if hasattr(bpy.types.Scene, "vtww_slot_settings"):
        try:
            del bpy.types.Scene.vtww_slot_settings
        except Exception:
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
