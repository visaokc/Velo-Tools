"""三段映射 - 用户算子（V0.1.1 重构）。

| bl_idname                       | 作用                                              |
|---------------------------------|---------------------------------------------------|
| velo.vg_apply_unified           | 把 mmd_source_object 的 VG 改名为统一编号           |
| velo.vg_stage_unified           | 把 mmd_source_object 上有权重 + 非特殊的 VG 补到表 |
| velo.vg_revert_to_mmd           | 把 mmd_source_object 上的 unified 名还原回 MMD     |
| velo.vg_match_to_mmd_table      | mmd_source_object → mmd_target_object 位置匹配      |
| velo.vg_table_import / export   | 文本 .txt 互转                                    |
| velo.vg_row_add / vg_row_remove | UIList 行 +/- 按钮                                |
| velo.vg_row_pick_source         | UIList 中点 mmd_name 弹「源 VG 选择」面板            |
| velo.vg_table_to_text / from_text | 与 .blend 内置 Text 双向同步                      |
"""

from __future__ import annotations

import json
import os

import bpy
from bpy.props import StringProperty, IntProperty, BoolProperty, EnumProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper

from . import algorithms as _algo
from . import text_io as _tio
from .filters import is_special_vg_name, collect_weighted_vg_indices, usable_vertex_groups


INTERNAL_TEXT_NAME = "velo_mmd_mapping.txt"
MMD_TEXT_META_KEY = "velo_mmd_overlay_meta"


# ============================================================
# 共用辅助
# ============================================================

def _get_settings(context):
    return getattr(context.scene, "velo_endfield", None)


def _mmd_source(context):
    s = _get_settings(context)
    if s is None:
        return None
    obj = s.mmd_source_object
    if obj and obj.type == 'MESH':
        return obj
    return None


def _mmd_target(context):
    s = _get_settings(context)
    if s is None:
        return None
    obj = s.mmd_target_object
    if obj and obj.type == 'MESH':
        return obj
    return None


def _ensure_mmd_text(settings):
    """确保 settings.active_mmd_text 有效；若没有则按当前源物体名创建并绑定。"""
    tb = settings.active_mmd_text
    if tb is not None:
        return tb
    src = settings.mmd_source_object
    base_part = (src.name if src is not None else "default")
    name = f"velo_mmd_{base_part}.txt"
    tb = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    settings.active_mmd_text = tb
    if src is not None:
        try:
            src["velo_mmd_text"] = tb.name
        except Exception:
            pass
    return tb


def _mmd_row_meta_key(mmd_name: str, unified_name: str) -> str:
    return f"{(mmd_name or '').strip()}\t{(unified_name or '').strip()}"


def _capture_mmd_row_snapshot(settings, row):
    from ...operators import vgroup_centroid_local

    src = settings.mmd_source_object
    tgt = settings.mmd_target_object
    mmd_name = (row.mmd_name or "").strip()
    unified_name = (row.unified_name or "").strip()
    current_source_name = (getattr(row, "current_source_name", "") or "").strip()

    if src is not None and mmd_name:
        svg = (src.vertex_groups.get(current_source_name)
               or src.vertex_groups.get(mmd_name)
               or src.vertex_groups.get(unified_name))
        if svg is not None:
            mc = vgroup_centroid_local(src, svg.index)
            if mc is not None:
                row.mmd_centroid_local = mc
                row.has_mmd_centroid = True

    if tgt is not None and unified_name:
        tvg = tgt.vertex_groups.get(unified_name) or tgt.vertex_groups.get(mmd_name)
        if tvg is not None:
            uc = vgroup_centroid_local(tgt, tvg.index)
            if uc is not None:
                row.unified_centroid_local = uc
                row.has_unified_centroid = True


def _write_mmd_text_meta(tb, settings):
    if tb is None:
        return
    profile = getattr(settings, "mmd_profile", None)
    if profile is None:
        return
    rows = []
    for row in profile.rows:
        rows.append({
            "mmd": (row.mmd_name or "").strip(),
            "unified": (row.unified_name or "").strip(),
            "mc": list(row.mmd_centroid_local) if getattr(row, "has_mmd_centroid", False) else None,
            "uc": list(row.unified_centroid_local) if getattr(row, "has_unified_centroid", False) else None,
        })
    try:
        tb[MMD_TEXT_META_KEY] = json.dumps({"rows": rows}, ensure_ascii=False)
    except Exception:
        pass


def apply_mmd_text_meta(settings, tb):
    profile = getattr(settings, "mmd_profile", None)
    if profile is None:
        return
    meta_rows = {}
    if tb is not None:
        try:
            raw = tb.get(MMD_TEXT_META_KEY, "")
            payload = json.loads(str(raw)) if raw else {}
            for row in payload.get("rows", []):
                key = _mmd_row_meta_key(row.get("mmd", ""), row.get("unified", ""))
                if key:
                    meta_rows[key] = row
        except Exception:
            meta_rows = {}

    for row in profile.rows:
        row.has_mmd_centroid = False
        row.has_unified_centroid = False
        meta = meta_rows.get(_mmd_row_meta_key(row.mmd_name, row.unified_name))
        if meta is not None:
            mc = meta.get("mc")
            uc = meta.get("uc")
            if mc and len(mc) == 3:
                row.mmd_centroid_local = mc
                row.has_mmd_centroid = True
            if uc and len(uc) == 3:
                row.unified_centroid_local = uc
                row.has_unified_centroid = True
        if not (row.has_mmd_centroid and row.has_unified_centroid):
            _capture_mmd_row_snapshot(settings, row)


def _autosync_to_text(settings):
    """每次写表后自动同步到当前映射表 Text；若未绑定则自动创建。
    顺序必须是先 from_string 再 set active，避免 update 回调读取空 Text 清空 profile.rows。"""
    try:
        text = _tio.serialize_mapping(settings)
        tb = settings.active_mmd_text
        if tb is None:
            src = settings.mmd_source_object
            base_part = (src.name if src is not None else "default")
            name = f"velo_mmd_{base_part}.txt"
            tb = bpy.data.texts.get(name) or bpy.data.texts.new(name)
            tb.from_string(text)            # 先写入内容
            _write_mmd_text_meta(tb, settings)
            settings.active_mmd_text = tb   # 后 set active；回调 parse 出一致的 rows
            if src is not None:
                try:
                    src["velo_mmd_text"] = tb.name
                except Exception:
                    pass
        else:
            tb.from_string(text)
            _write_mmd_text_meta(tb, settings)
    except Exception:
        pass
    # 顺便刷新 prop_search 候选 + 让 overlay 缓存失效
    try:
        from ...games.arknights_endfield.props import _refresh_available_src_vgs
        _refresh_available_src_vgs(settings)
    except Exception:
        pass
    try:
        from ... import overlay as _ov
        if hasattr(_ov, "invalidate_mmd_cache"):
            _ov.invalidate_mmd_cache()
    except Exception:
        pass


def _mmd_source_is_unified(settings) -> bool:
    src = getattr(settings, "mmd_source_object", None)
    return bool(src is not None and _algo.has_vg_snapshot(src))


def _mmd_family_rows(settings, root: str):
    root = (root or "").strip()
    profile = getattr(settings, "mmd_profile", None)
    if not root or profile is None:
        return []
    rows = []
    for row in profile.rows:
        mmd_name = (row.mmd_name or "").strip()
        unified_name = (row.unified_name or "").strip()
        if mmd_name and unified_name == root:
            rows.append(row)
    return rows


def _detach_mmd_source_row(settings, row):
    obj = getattr(settings, "mmd_source_object", None)
    if obj is None or obj.type != 'MESH':
        return "", 0

    current_actual = (getattr(row, "current_source_name", "") or row.mmd_name or "").strip()
    desired_root = (row.mmd_name or current_actual).strip()
    fallback = [row.mmd_name, row.unified_name]
    arm = getattr(settings, "mmd_armature_object", None)
    extra_taken = []
    if arm is not None and arm.type == 'ARMATURE':
        extra_taken = [b.name for b in arm.data.bones if b.name != current_actual]

    rep = _algo.rename_single_vg_no_merge(
        obj,
        current_actual,
        desired_root,
        fallback_names=fallback,
        extra_taken_names=extra_taken,
    )
    final_name = rep.get("final_name", "") or current_actual or desired_root

    bone_n = 0
    if arm is not None and arm.type == 'ARMATURE':
        bone_n = _algo.rename_single_bone_no_merge(
            arm,
            current_actual,
            final_name,
            fallback_names=fallback,
        )

    return final_name, bone_n


def _sync_mmd_source_from_table(settings, *, save_backup: bool = False):
    """若 MMD 源当前处于统一编号态，则从原始快照重放当前表，并把数字组排到最前。"""
    obj = getattr(settings, "mmd_source_object", None)
    profile = getattr(settings, "mmd_profile", None)
    if obj is None or obj.type != 'MESH' or profile is None:
        return {"renamed": 0, "final_names": []}, 0

    ordered = _algo.build_ordered_mmd_pairs(profile)
    if not ordered:
        return {"renamed": 0, "final_names": []}, 0

    report = _algo.reapply_no_merge_with_suffix_from_snapshot(obj, ordered, save_backup=save_backup)
    _algo.reorder_numeric_vertex_groups_first(obj)

    bone_n = 0
    arm = getattr(settings, "mmd_armature_object", None)
    if arm is not None and arm.type == 'ARMATURE':
        bone_n = _algo.reapply_armature_bones_with_suffix_from_snapshot(arm, ordered, save_backup=save_backup)

    rows = [r for r in profile.rows if r.mmd_name and r.unified_name]
    from ...games.arknights_endfield import props as _mmd_props
    prev_row_suspend = _mmd_props._suspend_mmd_row_update
    _mmd_props._suspend_mmd_row_update = True
    try:
        for row in profile.rows:
            row.current_source_name = row.mmd_name or row.current_source_name
        for row, final_name in zip(rows, report.get("final_names", [])):
            row.current_source_name = final_name or row.current_source_name
    finally:
        _mmd_props._suspend_mmd_row_update = prev_row_suspend

    try:
        from ... import overlay as _ov
        if hasattr(_ov, "invalidate_mmd_cache"):
            _ov.invalidate_mmd_cache()
    except Exception:
        pass
    return report, bone_n


def _restore_mmd_source_to_original(settings):
    obj = getattr(settings, "mmd_source_object", None)
    if obj is None or obj.type != 'MESH':
        return False, 0
    if not _algo.restore_vg_snapshot(obj):
        return False, 0

    bone_n = 0
    arm = getattr(settings, "mmd_armature_object", None)
    if arm is not None and arm.type == 'ARMATURE':
        if _algo.restore_armature_bone_snapshot(arm):
            bone_n = len(arm.data.bones)

    profile = getattr(settings, "mmd_profile", None)
    if profile is not None:
        from ...games.arknights_endfield import props as _mmd_props
        prev_row_suspend = _mmd_props._suspend_mmd_row_update
        _mmd_props._suspend_mmd_row_update = True
        try:
            for row in profile.rows:
                row.current_source_name = row.mmd_name or row.current_source_name
        finally:
            _mmd_props._suspend_mmd_row_update = prev_row_suspend

    try:
        from ... import overlay as _ov
        if hasattr(_ov, "invalidate_mmd_cache"):
            _ov.invalidate_mmd_cache()
    except Exception:
        pass
    return True, bone_n


def _sync_mmd_source_row_incremental(settings, row, new_root: str):
    """统一编号态下，只重排受影响 unified 家族的实际源 VG / 骨骼名。"""
    obj = getattr(settings, "mmd_source_object", None)
    if obj is None or obj.type != 'MESH':
        return "", 0

    current_actual = (getattr(row, "current_source_name", "") or row.mmd_name or "").strip()
    arm = getattr(settings, "mmd_armature_object", None)
    bone_n = 0
    final_name = current_actual
    detached_name = ""
    roots = []
    old_root = _algo.strip_dup_suffix(current_actual)
    if old_root:
        roots.append(old_root)
    new_root = (new_root or "").strip()
    if new_root and new_root not in roots:
        roots.append(new_root)

    from ...games.arknights_endfield import props as _mmd_props
    prev_row_suspend = _mmd_props._suspend_mmd_row_update
    _mmd_props._suspend_mmd_row_update = True
    try:
        if not new_root:
            detached_name, bone_delta = _detach_mmd_source_row(settings, row)
            bone_n += bone_delta
            if detached_name:
                row.current_source_name = detached_name
                final_name = detached_name
        for root in roots:
            family_rows = _mmd_family_rows(settings, root)
            if not family_rows:
                continue
            specs = []
            family_actual_names = set()
            for it in family_rows:
                actual_name = (getattr(it, "current_source_name", "") or it.mmd_name or it.unified_name or "").strip()
                if actual_name:
                    family_actual_names.add(actual_name)
                specs.append({
                    "current": actual_name,
                    "fallbacks": [it.mmd_name, it.unified_name],
                })

            if arm is not None and arm.type == 'ARMATURE':
                final_names, bone_delta = _algo.rename_synced_vg_bone_family_no_merge(
                    obj,
                    arm,
                    specs,
                    root,
                )
                bone_n += bone_delta
            else:
                final_names = _algo.rename_vg_family_no_merge(
                    obj,
                    specs,
                    root,
                )

            for it, assigned in zip(family_rows, final_names):
                if assigned:
                    it.current_source_name = assigned
                if it == row:
                    final_name = assigned or final_name
    finally:
        _mmd_props._suspend_mmd_row_update = prev_row_suspend

    if not new_root and detached_name:
        final_name = detached_name

    _algo.reorder_numeric_vertex_groups_first(obj)
    try:
        from ... import overlay as _ov
        if hasattr(_ov, "invalidate_mmd_cache"):
            _ov.invalidate_mmd_cache()
    except Exception:
        pass
    return final_name, bone_n


# ============================================================
# A. 实际改名（MMD → unified）
# ============================================================

class VELO_OT_vg_apply_unified(bpy.types.Operator):
    bl_idname = "velo.vg_apply_unified"
    bl_label = "将源物体改名为统一编号"
    bl_description = "按 MMD 映射表把 mmd_source_object 的顶点组改名为统一编号；若选择了 MMD 骨架则同步骨骼名（lossless 备份原始权重供还原）"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None or settings.mmd_profile is None:
            self.report({'ERROR'}, "未找到终末地映射数据")
            return {'CANCELLED'}
        obj = _mmd_source(context)
        if obj is None:
            self.report({'ERROR'}, "请先在面板顶部指定『MMD 源物体』")
            return {'CANCELLED'}
        ordered = _algo.build_ordered_mmd_pairs(settings.mmd_profile)
        if not ordered:
            self.report({'ERROR'}, "MMD 映射表为空")
            return {'CANCELLED'}
        r, bone_n = _sync_mmd_source_from_table(settings, save_backup=True)
        self.report({'INFO'}, f"完成: 改名 {r['renamed']} (未合并; 骨骼同步 {bone_n})")
        return {'FINISHED'}


# ============================================================
# B. 仅记录映射（不改名）
# ============================================================

class VELO_OT_vg_stage_unified(bpy.types.Operator):
    bl_idname = "velo.vg_stage_unified"
    bl_label = "从源物体补行"
    bl_description = (
        "把 mmd_source_object 当前实际有权重的顶点组追加为新行；"
        "自动跳过 mmd_edge_scale / mmd_vertex_order / UV_* 等特殊组以及完全无权重的组"
    )
    bl_options = {'REGISTER', 'UNDO'}

    prune_invalid: BoolProperty(
        name="同时清理表里 mmd 列已不存在的行",
        default=True,
    )

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None or settings.mmd_profile is None:
            self.report({'ERROR'}, "未找到终末地映射数据")
            return {'CANCELLED'}
        obj = _mmd_source(context)
        if obj is None:
            self.report({'ERROR'}, "请先在面板顶部指定『MMD 源物体』")
            return {'CANCELLED'}

        profile = settings.mmd_profile
        usable = list(usable_vertex_groups(obj))
        usable_names = {n for _, n in usable}

        existing = {r.mmd_name: i for i, r in enumerate(profile.rows)}

        added = 0
        for _idx, name in usable:
            if name in existing:
                continue
            row = profile.rows.add()
            row.mmd_name = name
            row.unified_name = ""
            row.enabled = True
            existing[name] = len(profile.rows) - 1
            added += 1

        pruned = 0
        if self.prune_invalid:
            # 逆序删除 mmd_name 不在源物体可用 VG 中的行
            for i in range(len(profile.rows) - 1, -1, -1):
                r = profile.rows[i]
                if r.mmd_name and r.mmd_name not in usable_names:
                    profile.rows.remove(i)
                    pruned += 1

        _autosync_to_text(settings)
        self.report({'INFO'}, f"补行: 新增 {added}, 清理 {pruned} (源={obj.name})")
        return {'FINISHED'}


# ============================================================
# C. 还原（unified → MMD 原名）
# ============================================================

class VELO_OT_vg_revert_to_mmd(bpy.types.Operator):
    bl_idname = "velo.vg_revert_to_mmd"
    bl_label = "将源物体还原为MMD名字"
    bl_description = "使用快照把 mmd_source_object 完整还原到改名前的 VG 名/顺序/权重；若选择了 MMD 骨架则同步骨骼名"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None or settings.mmd_profile is None:
            self.report({'ERROR'}, "未找到终末地映射数据")
            return {'CANCELLED'}
        obj = _mmd_source(context)
        if obj is None:
            self.report({'ERROR'}, "请先在面板顶部指定『MMD 源物体』")
            return {'CANCELLED'}
        ok, bone_n = _restore_mmd_source_to_original(settings)
        if not ok:
            self.report({'ERROR'}, "未找到 VG 快照，无法无损还原")
            return {'CANCELLED'}
        self.report({'INFO'}, f"还原: 已用快照无损还原 (源={obj.name}; 骨骼 {bone_n})")
        return {'FINISHED'}


# ============================================================
# C2. 目标物体改名（unified <-> MMD）— V0.1.6 任务 3
# ============================================================

class VELO_OT_vg_target_to_mmd(bpy.types.Operator):
    bl_idname = "velo.vg_target_to_mmd"
    bl_label = "目标物体改名为MMD名字"
    bl_description = "按映射表把『目标 Component』的统一编号顶点组改名为 MMD 名字，并按映射表顺序重排 VG"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None or settings.mmd_profile is None:
            self.report({'ERROR'}, "未找到终末地映射数据")
            return {'CANCELLED'}
        obj = _mmd_target(context)
        if obj is None:
            self.report({'ERROR'}, "请先在面板顶部指定『目标 Component』")
            return {'CANCELLED'}
        # 按 profile 顺序取首次出现的 (unified -> first_mmd)
        seen = set()
        ordered = []
        for row in settings.mmd_profile.rows:
            if not row.mmd_name or not row.unified_name:
                continue
            if row.unified_name in seen:
                continue
            seen.add(row.unified_name)
            ordered.append((row.unified_name, row.mmd_name))
        if not ordered:
            self.report({'ERROR'}, "MMD 映射表为空")
            return {'CANCELLED'}
        # 保存快照 + 不合并改名
        r = _algo.rename_no_merge_with_suffix(obj, ordered, save_backup=True)
        # 按首次出现的 mmd_name 顺序重排
        desired = [m for _u, m in ordered]
        _algo.reorder_vertex_groups_by_order(obj, desired)
        self.report({'INFO'}, f"目标→MMD 名: 改名 {r['renamed']} (目标={obj.name})")
        return {'FINISHED'}


class VELO_OT_vg_target_to_unified(bpy.types.Operator):
    bl_idname = "velo.vg_target_to_unified"
    bl_label = "将目标物体改名为统一编号"
    bl_description = "按映射表把『目标 Component』上的 MMD 名字顶点组改回统一编号；保持当前 VG 顺序不变"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None or settings.mmd_profile is None:
            self.report({'ERROR'}, "未找到终末地映射数据")
            return {'CANCELLED'}
        obj = _mmd_target(context)
        if obj is None:
            self.report({'ERROR'}, "请先在面板顶部指定『目标 Component』")
            return {'CANCELLED'}
        # 优先用快照还原（无损）
        if _algo.restore_vg_snapshot(obj):
            self.report({'INFO'}, f"目标→统一编号: 已用快照无损还原 (目标={obj.name})")
            return {'FINISHED'}
        # 无快照时：按 (mmd 首次出现 -> unified) 改名
        seen = set()
        ordered = []
        for row in settings.mmd_profile.rows:
            if not row.mmd_name or not row.unified_name:
                continue
            if row.unified_name in seen:
                continue
            seen.add(row.unified_name)
            ordered.append((row.mmd_name, row.unified_name))
        if not ordered:
            self.report({'ERROR'}, "MMD 映射表为空且无快照可还原")
            return {'CANCELLED'}
        r = _algo.rename_no_merge_with_suffix(obj, ordered, save_backup=False)
        self.report({'INFO'}, f"目标→统一编号: 改名 {r['renamed']} (目标={obj.name})")
        return {'FINISHED'}


# ============================================================
# D. 文本 import / export（外部文件）
# ============================================================

class VELO_OT_vg_table_import(bpy.types.Operator, ImportHelper):
    bl_idname = "velo.vg_table_import"
    bl_label = "导入映射文本"
    bl_description = "从 .txt 文件读取 MMD 映射表"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".txt"
    filter_glob: StringProperty(default="*.txt", options={'HIDDEN'})

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None:
            self.report({'ERROR'}, "未找到终末地映射数据")
            return {'CANCELLED'}
        if not self.filepath or not os.path.exists(self.filepath):
            self.report({'ERROR'}, "未选择有效文件")
            return {'CANCELLED'}
        try:
            with open(self.filepath, 'r', encoding='utf-8') as fh:
                text = fh.read()
        except Exception as e:
            self.report({'ERROR'}, f"读取失败: {e}")
            return {'CANCELLED'}
        parsed = _tio.parse_mapping_text(text)
        from ...games.arknights_endfield import props as _mmd_props
        prev_row_suspend = _mmd_props._suspend_mmd_row_update
        _mmd_props._suspend_mmd_row_update = True
        try:
            report = _tio.load_into_settings(settings, parsed)
        finally:
            _mmd_props._suspend_mmd_row_update = prev_row_suspend
        if _mmd_source_is_unified(settings):
            _sync_mmd_source_from_table(settings, save_backup=False)
        _autosync_to_text(settings)
        self.report({'INFO'}, f"导入完成: rows={report.get('rows', 0)}")
        return {'FINISHED'}


class VELO_OT_vg_table_export(bpy.types.Operator, ExportHelper):
    bl_idname = "velo.vg_table_export"
    bl_label = "导出映射文本"
    bl_description = "把当前 MMD 映射表写出为 .txt"
    bl_options = {'REGISTER'}

    filename_ext = ".txt"
    filter_glob: StringProperty(default="*.txt", options={'HIDDEN'})

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None:
            self.report({'ERROR'}, "未找到终末地映射数据")
            return {'CANCELLED'}
        try:
            text = _tio.serialize_mapping(settings)
            with open(self.filepath, 'w', encoding='utf-8') as fh:
                fh.write(text)
        except Exception as e:
            self.report({'ERROR'}, f"写入失败: {e}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"已导出到 {self.filepath}")
        return {'FINISHED'}


# ============================================================
# E. 位置匹配 → 写入 MMD 映射表（kdtree 加速）
# ============================================================

def _compute_centroids_world(obj, only_indices=None):
    """单遍扫描，返回 {vg_index: world_pos Vector}。

    - 跳过特殊命名 + 无权重的 VG
    - 若提供 only_indices（set/iterable），则只累加这些 vg index（性能优化：
      源侧通常只关心已存在于 profile.rows 里的 mmd_name 对应的 vg）
    """
    from mathutils import Vector
    out = {}
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return out
    me = obj.data
    mw = obj.matrix_world

    skip = set()
    for vg in obj.vertex_groups:
        if is_special_vg_name(vg.name):
            skip.add(vg.index)
    keep = None
    if only_indices is not None:
        keep = set(only_indices) - skip

    sums = {}
    weights = {}
    sums_get = sums.get  # 局部变量缓存属性查找
    for v in me.vertices:
        co = v.co
        cox, coy, coz = co.x, co.y, co.z
        for g in v.groups:
            w = g.weight
            if w <= 0.0:
                continue
            gi = g.group
            if gi in skip:
                continue
            if keep is not None and gi not in keep:
                continue
            s = sums_get(gi)
            if s is None:
                sums[gi] = Vector((cox * w, coy * w, coz * w))
                weights[gi] = w
            else:
                s.x += cox * w
                s.y += coy * w
                s.z += coz * w
                weights[gi] += w
    for gi, total in weights.items():
        if total > 0.0:
            out[gi] = mw @ (sums[gi] / total)
    return out


class VELO_OT_vg_match_to_mmd_table(bpy.types.Operator):
    bl_idname = "velo.vg_match_to_mmd_table"
    bl_label = "按位置匹配 → 写入本表"
    bl_description = (
        "用 mmd_source_object（MMD 网格）和 mmd_target_object（终末地 Component）"
        "做一次顶点组重心位置匹配，结果写入本表的 unified 列；不会改名顶点组。"
        "使用 KDTree 加速：单遍扫描两边网格 + O(N log M) 最近邻"
    )
    bl_options = {'REGISTER', 'UNDO'}

    overwrite_existing: BoolProperty(name="覆盖已有 unified", default=True)
    use_max_distance: BoolProperty(name="启用最大距离过滤", default=False)
    max_match_distance: bpy.props.FloatProperty(name="最大匹配距离", default=0.5, min=0.0, soft_max=10.0)
    only_existing_rows: BoolProperty(
        name="只匹配表内已有的 mmd 行",
        default=True,
        description="开启后只对 profile.rows 里 mmd_name 已存在的源 VG 计算重心，"
                    "可显著加快速度（建议先点『从源物体补行』再来匹配）",
    )

    def execute(self, context):
        from mathutils.kdtree import KDTree

        settings = _get_settings(context)
        if settings is None or settings.mmd_profile is None:
            self.report({'ERROR'}, "未找到终末地映射数据")
            return {'CANCELLED'}

        base = _mmd_source(context)
        target = _mmd_target(context)
        if base is None or target is None:
            self.report({'ERROR'}, "请先在面板顶部指定 MMD 源物体 + 目标物体")
            return {'CANCELLED'}

        profile = settings.mmd_profile

        # 1) 单遍预计算两边的重心（源侧可选只算 profile 里出现过的 mmd_name）
        only_src_indices = None
        if self.only_existing_rows and len(profile.rows) > 0:
            wanted_names = {r.mmd_name for r in profile.rows if r.mmd_name}
            only_src_indices = {
                vg.index for vg in base.vertex_groups if vg.name in wanted_names
            }
            if not only_src_indices:
                # 表里所有 mmd_name 在源物体都不存在，回退到全量
                only_src_indices = None

        src_centroids = _compute_centroids_world(base, only_indices=only_src_indices)
        tgt_centroids = _compute_centroids_world(target)
        if not src_centroids:
            self.report({'ERROR'}, f"源物体 {base.name} 没有可用的有权重顶点组")
            return {'CANCELLED'}
        if not tgt_centroids:
            self.report({'ERROR'}, f"目标物体 {target.name} 没有可用的有权重顶点组")
            return {'CANCELLED'}

        # V0.1.6 修复：同时计算局部重心供 overlay 快照（不受 VG 名变动影响）
        from ...operators import compute_all_centroids_local
        src_local = compute_all_centroids_local(base, only_indices=only_src_indices)
        tgt_local = compute_all_centroids_local(target)

        # 2) 用 KDTree 索引 target 重心
        tgt_items = list(tgt_centroids.items())   # [(vg_idx, world), ...]
        kd = KDTree(len(tgt_items))
        for i, (_gi, w) in enumerate(tgt_items):
            kd.insert(w, i)
        kd.balance()

        # 3) 源物体 vg index -> name
        src_name_by_idx = {vg.index: vg.name for vg in base.vertex_groups}
        tgt_name_by_idx = {vg.index: vg.name for vg in target.vertex_groups}

        existing = {r.mmd_name: r for r in profile.rows}

        # V0.1.4: 按 base.vertex_groups 出现顺序遍历，新增行的追加顺序
        # 与「从源物体补行」一致；之前用 src_centroids.items() 顺序由网格
        # 顶点遍历副作用决定，导致用户两条流程出来的表行序对不上。
        added = updated = skipped = 0
        from ...games.arknights_endfield import props as _mmd_props
        prev_row_suspend = _mmd_props._suspend_mmd_row_update
        _mmd_props._suspend_mmd_row_update = True
        try:
            for vg in base.vertex_groups:
                src_gi = vg.index
                src_w = src_centroids.get(src_gi)
                if src_w is None:
                    continue
                src_name = src_name_by_idx.get(src_gi, "")
                if not src_name:
                    continue
                co, idx, dist = kd.find(src_w)
                if idx is None:
                    skipped += 1
                    continue
                if self.use_max_distance and dist > self.max_match_distance:
                    skipped += 1
                    continue
                tgt_gi, _ = tgt_items[idx]
                best_name = tgt_name_by_idx.get(tgt_gi, "")
                if not best_name:
                    skipped += 1
                    continue
                row = existing.get(src_name)
                if row is None:
                    row = profile.rows.add()
                    row.mmd_name = src_name
                    row.unified_name = best_name
                    row.enabled = True
                    existing[src_name] = row
                    added += 1
                else:
                    if row.unified_name and not self.overwrite_existing:
                        skipped += 1
                        continue
                    if row.unified_name != best_name:
                        row.unified_name = best_name
                        updated += 1
                # V0.1.6 修复：同步写入 overlay 快照（局部重心）
                mc = src_local.get(src_gi)
                if mc is not None:
                    row.mmd_centroid_local = mc
                    row.has_mmd_centroid = True
                uc = tgt_local.get(tgt_gi)
                if uc is not None:
                    row.unified_centroid_local = uc
                    row.has_unified_centroid = True
        finally:
            _mmd_props._suspend_mmd_row_update = prev_row_suspend

        if _mmd_source_is_unified(settings):
            _sync_mmd_source_from_table(settings, save_backup=False)

        _autosync_to_text(settings)
        self.report({'INFO'}, f"匹配(KDTree): 新增 {added}, 更新 {updated}, 跳过 {skipped}")
        return {'FINISHED'}


# ============================================================
# F. UIList 行 +/- 与源 VG 选择弹窗
# ============================================================

class VELO_OT_mmd_row_add(bpy.types.Operator):
    bl_idname = "velo.vg_row_add"
    bl_label = "新增空行"
    bl_options = {'REGISTER', 'UNDO'}

    after_index: IntProperty(default=-1)

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None or settings.mmd_profile is None:
            return {'CANCELLED'}
        profile = settings.mmd_profile
        row = profile.rows.add()
        row.enabled = True
        # 移到指定位置之后
        new_idx = len(profile.rows) - 1
        if 0 <= self.after_index < new_idx:
            profile.rows.move(new_idx, self.after_index + 1)
            profile.active_row_index = self.after_index + 1
        else:
            profile.active_row_index = new_idx
        _autosync_to_text(settings)
        return {'FINISHED'}


class VELO_OT_mmd_row_remove(bpy.types.Operator):
    bl_idname = "velo.vg_row_remove"
    bl_label = "删除该行"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1)

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None or settings.mmd_profile is None:
            return {'CANCELLED'}
        profile = settings.mmd_profile
        idx = self.index
        if idx < 0:
            idx = profile.active_row_index
        if 0 <= idx < len(profile.rows):
            profile.rows.remove(idx)
            profile.active_row_index = max(0, min(idx, len(profile.rows) - 1))
        _autosync_to_text(settings)
        return {'FINISHED'}


class VELO_OT_mmd_table_clear(bpy.types.Operator):
    bl_idname = "velo.vg_table_clear"
    bl_label = "清空映射表"
    bl_description = "清空当前 MMD 映射表所有行（不可撤销请谨慎）"
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None or settings.mmd_profile is None:
            return {'CANCELLED'}
        profile = settings.mmd_profile
        n = len(profile.rows)
        profile.rows.clear()
        profile.active_row_index = 0
        _autosync_to_text(settings)
        self.report({'INFO'}, f"已清空 {n} 行")
        return {'FINISHED'}


def _enum_source_vgs(self, context):
    """弹窗下拉项：源物体里所有「有权重 + 非特殊」的 VG 名。"""
    items = []
    obj = _mmd_source(context)
    if obj is None:
        return [('', '<未指定 MMD 源物体>', '')]
    seen = set()
    for _gi, name in usable_vertex_groups(obj):
        if name in seen:
            continue
        seen.add(name)
        items.append((name, name, ""))
    if not items:
        items.append(('', '<源物体无可用顶点组>', ''))
    return items


class VELO_OT_mmd_row_pick_source(bpy.types.Operator):
    bl_idname = "velo.vg_row_pick_source"
    bl_label = "选择源顶点组"
    bl_description = "从 MMD 源物体的顶点组中挑一个填入本行的 mmd_name"
    bl_options = {'REGISTER', 'UNDO'}
    bl_property = "choice"

    index: IntProperty(default=-1)
    choice: EnumProperty(name="顶点组", items=_enum_source_vgs)

    def invoke(self, context, event):
        wm = context.window_manager
        wm.invoke_search_popup(self)
        return {'FINISHED'}

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None or settings.mmd_profile is None:
            return {'CANCELLED'}
        profile = settings.mmd_profile
        idx = self.index
        if idx < 0:
            idx = profile.active_row_index
        if not (0 <= idx < len(profile.rows)):
            self.report({'WARNING'}, "无效行")
            return {'CANCELLED'}
        if not self.choice:
            return {'CANCELLED'}
        profile.rows[idx].mmd_name = self.choice
        _autosync_to_text(settings)
        return {'FINISHED'}


# ============================================================
# G. 与 .blend 内置 Text 的双向同步
# ============================================================

class VELO_OT_vg_table_to_text(bpy.types.Operator):
    bl_idname = "velo.vg_table_to_text"
    bl_label = "→ 内置文本"
    bl_description = "把当前映射表写入当前选中的映射表 Text（未选中会自动按源物体名创建）"
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None:
            return {'CANCELLED'}
        _autosync_to_text(settings)
        tb = settings.active_mmd_text
        self.report({'INFO'}, f"已写入 Text『{tb.name if tb else '?'}』")
        return {'FINISHED'}


class VELO_OT_vg_table_from_text(bpy.types.Operator):
    bl_idname = "velo.vg_table_from_text"
    bl_label = "← 内置文本"
    bl_description = "从当前选中的映射表 Text 重新加载映射表"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _get_settings(context)
        if settings is None:
            return {'CANCELLED'}
        tb = settings.active_mmd_text
        if tb is None:
            self.report({'ERROR'}, "未选中映射表 Text")
            return {'CANCELLED'}
        try:
            text = tb.as_string()
        except Exception as e:
            self.report({'ERROR'}, f"读取 Text 失败: {e}")
            return {'CANCELLED'}
        parsed = _tio.parse_mapping_text(text)
        from ...games.arknights_endfield import props as _mmd_props
        prev_row_suspend = _mmd_props._suspend_mmd_row_update
        _mmd_props._suspend_mmd_row_update = True
        try:
            report = _tio.load_into_settings(settings, parsed)
        finally:
            _mmd_props._suspend_mmd_row_update = prev_row_suspend
        if _mmd_source_is_unified(settings):
            _sync_mmd_source_from_table(settings, save_backup=False)
        _autosync_to_text(settings)
        self.report({'INFO'}, f"从 Text 同步: rows={report.get('rows', 0)}")
        return {'FINISHED'}


# ============================================================
# 任务4: MMD 区"映射表"槽位 CRUD（替代旧"表名"单字段）
# ============================================================

import json as _json_mmd


class VELO_OT_mmd_text_new(bpy.types.Operator):
    """新建一个空 MMD 映射表 Text 并绑定到当前源物体；当前 profile.rows 会被清空。"""
    bl_idname = "velo.mmd_text_new"
    bl_label = "新建映射表"
    bl_options = {'REGISTER', 'UNDO'}

    name: bpy.props.StringProperty(name="名称", default="")

    def invoke(self, context, event):
        ef = _get_settings(context)
        src = ef.mmd_source_object if ef else None
        default = f"velo_mmd_{src.name}.txt" if src is not None else "velo_mmd_new.txt"
        i = 1
        candidate = default
        while bpy.data.texts.get(candidate) is not None:
            i += 1
            stem = default[:-4] if default.endswith(".txt") else default
            candidate = f"{stem}.{i:03d}.txt"
        self.name = candidate
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        ef = _get_settings(context)
        if ef is None or ef.mmd_profile is None:
            return {'CANCELLED'}
        nm = (self.name or "").strip() or "velo_mmd_new.txt"
        if not nm.endswith(".txt"):
            nm += ".txt"
        tb = bpy.data.texts.get(nm) or bpy.data.texts.new(nm)
        tb.from_string("# velo_tools MMD 映射表\n# 格式: <mmd_name>\\t<unified_name>\n")
        ef.mmd_profile.rows.clear()
        ef.active_mmd_text = tb
        src = ef.mmd_source_object
        if src is not None:
            try:
                src["velo_mmd_text"] = tb.name
            except Exception:
                pass
        self.report({'INFO'}, f"已新建并切换到映射表: {tb.name}")
        return {'FINISHED'}


_classes = (
    VELO_OT_vg_apply_unified,
    VELO_OT_vg_stage_unified,
    VELO_OT_vg_revert_to_mmd,
    VELO_OT_vg_target_to_mmd,
    VELO_OT_vg_target_to_unified,
    VELO_OT_vg_table_import,
    VELO_OT_vg_table_export,
    VELO_OT_vg_match_to_mmd_table,
    VELO_OT_mmd_row_add,
    VELO_OT_mmd_row_remove,
    VELO_OT_mmd_table_clear,
    VELO_OT_mmd_row_pick_source,
    VELO_OT_vg_table_to_text,
    VELO_OT_vg_table_from_text,
    VELO_OT_mmd_text_new,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass