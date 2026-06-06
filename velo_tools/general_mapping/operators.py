from __future__ import annotations

import json
import os

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from ..operators import compute_all_centroids_local
from ..core.mapping.filters import usable_vertex_groups
from ..core.mapping import algorithms as _algo


GENERAL_TEXT_META_KEY = "velo_general_overlay_meta"


def get_settings(context):
    return getattr(context.scene, "velo_general_mapping", None)


def general_source(context):
    settings = get_settings(context)
    if settings is None:
        return None
    obj = settings.source_object
    if obj and obj.type == 'MESH':
        return obj
    return None


def general_target(context):
    settings = get_settings(context)
    if settings is None:
        return None
    obj = settings.target_object
    if obj and obj.type == 'MESH':
        return obj
    return None


def _tag_redraw_all_view3d(context=None):
    ctx = context or bpy.context
    try:
        screen = getattr(ctx, "screen", None)
        if screen is None:
            return
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    except Exception:
        pass


def refresh_general_runtime(settings, context=None, *, reset_pick=False):
    try:
        from .props import _refresh_available_source_vgs
        _refresh_available_source_vgs(settings)
    except Exception:
        pass
    try:
        from . import overlay as _overlay
        _overlay.invalidate_cache()
    except Exception:
        pass
    try:
        from . import pick as _pick
        if reset_pick:
            _pick.reset_state(context)
        if getattr(settings, "show_overlay", False):
            _pick.start_modal_if_needed()
    except Exception:
        pass
    _tag_redraw_all_view3d(context)


def general_row_meta_key(source_name: str, target_name: str) -> str:
    return f"{(source_name or '').strip()}\t{(target_name or '').strip()}"


def serialize_general_text(settings) -> str:
    profile = getattr(settings, "profile", None)
    if profile is None:
        return ""
    lines = []
    for row in profile.rows:
        source_name = (row.source_name or "").strip()
        target_name = (row.target_name or "").strip()
        if not source_name:
            continue
        lines.append(f"{source_name}\t{target_name}")
    return "\n".join(lines)


def parse_general_text(raw: str):
    pairs = []
    for line in (raw or "").splitlines():
        text = line.split('#', 1)[0].strip()
        if not text:
            continue
        if text.startswith('@'):
            continue
        if '\t' in text:
            parts = text.split('\t', 1)
        else:
            parts = text.split(None, 1)
        if len(parts) != 2:
            continue
        source_name, target_name = parts[0].strip(), parts[1].strip()
        if source_name:
            pairs.append((source_name, target_name))
    return pairs


def ensure_general_text(settings):
    tb = settings.active_general_text
    if tb is not None:
        return tb
    src = settings.source_object
    base_part = src.name if src is not None else "default"
    name = f"velo_general_{base_part}.txt"
    tb = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    if src is not None:
        try:
            src["velo_general_text"] = tb.name
        except Exception:
            pass
    return tb


def capture_general_row_snapshot(settings, row):
    src = settings.source_object
    tgt = settings.target_object
    source_name = (row.source_name or "").strip()
    target_name = (row.target_name or "").strip()
    current_source_name = (getattr(row, "current_source_name", "") or "").strip()

    if src is not None and source_name:
        svg = (
            src.vertex_groups.get(current_source_name)
            or src.vertex_groups.get(source_name)
            or src.vertex_groups.get(target_name)
        )
        if svg is not None:
            centroid = compute_all_centroids_local(src, only_indices={svg.index}).get(svg.index)
            if centroid is not None:
                row.source_centroid_local = centroid
                row.has_source_centroid = True

    if tgt is not None and target_name:
        tvg = tgt.vertex_groups.get(target_name) or tgt.vertex_groups.get(source_name)
        if tvg is not None:
            centroid = compute_all_centroids_local(tgt, only_indices={tvg.index}).get(tvg.index)
            if centroid is not None:
                row.target_centroid_local = centroid
                row.has_target_centroid = True


def write_general_text_meta(tb, settings):
    if tb is None:
        return
    profile = getattr(settings, "profile", None)
    if profile is None:
        return
    rows = []
    for row in profile.rows:
        rows.append({
            "src": (row.source_name or "").strip(),
            "tgt": (row.target_name or "").strip(),
            "sc": list(row.source_centroid_local) if getattr(row, "has_source_centroid", False) else None,
            "tc": list(row.target_centroid_local) if getattr(row, "has_target_centroid", False) else None,
        })
    try:
        tb[GENERAL_TEXT_META_KEY] = json.dumps({"rows": rows}, ensure_ascii=False)
    except Exception:
        pass


def apply_general_text_meta(settings, tb):
    profile = getattr(settings, "profile", None)
    if profile is None:
        return
    meta_rows = {}
    if tb is not None:
        try:
            raw = tb.get(GENERAL_TEXT_META_KEY, "")
            payload = json.loads(str(raw)) if raw else {}
            for row in payload.get("rows", []):
                key = general_row_meta_key(row.get("src", ""), row.get("tgt", ""))
                if key:
                    meta_rows[key] = row
        except Exception:
            meta_rows = {}
    for row in profile.rows:
        row.has_source_centroid = False
        row.has_target_centroid = False
        meta = meta_rows.get(general_row_meta_key(row.source_name, row.target_name))
        if meta is not None:
            source_centroid = meta.get("sc")
            target_centroid = meta.get("tc")
            if source_centroid and len(source_centroid) == 3:
                row.source_centroid_local = source_centroid
                row.has_source_centroid = True
            if target_centroid and len(target_centroid) == 3:
                row.target_centroid_local = target_centroid
                row.has_target_centroid = True
        if not (row.has_source_centroid and row.has_target_centroid):
            capture_general_row_snapshot(settings, row)


def autosync_general_to_text(settings):
    try:
        text = serialize_general_text(settings)
        tb = settings.active_general_text
        if tb is None:
            src = settings.source_object
            base_part = src.name if src is not None else "default"
            name = f"velo_general_{base_part}.txt"
            tb = bpy.data.texts.get(name) or bpy.data.texts.new(name)
            tb.from_string(text)
            write_general_text_meta(tb, settings)
            settings.active_general_text = tb
            if src is not None:
                try:
                    src["velo_general_text"] = tb.name
                except Exception:
                    pass
        else:
            tb.from_string(text)
            write_general_text_meta(tb, settings)
    except Exception:
        pass
    refresh_general_runtime(settings)


def load_general_text_into_settings(settings, raw: str):
    profile = settings.profile
    if profile is None:
        return
    pairs = parse_general_text(raw)
    from . import props as _props
    prev = _props._suspend_general_row_update
    _props._suspend_general_row_update = True
    try:
        profile.rows.clear()
        for source_name, target_name in pairs:
            row = profile.rows.add()
            row.enabled = True
            row.source_name = source_name
            row.current_source_name = source_name
            row.target_name = target_name
        profile.active_row_index = 0
    finally:
        _props._suspend_general_row_update = prev
    try:
        apply_general_text_meta(settings, settings.active_general_text)
        if general_source_is_target_named(settings):
            sync_general_source_from_table(settings, save_backup=False)
        write_general_text_meta(settings.active_general_text, settings)
    except Exception:
        pass


def general_source_is_target_named(settings) -> bool:
    src = getattr(settings, "source_object", None)
    return bool(src is not None and _algo.has_vg_snapshot(src))


def matched_general_rows(settings):
    profile = getattr(settings, "profile", None)
    if profile is None:
        return []
    rows = []
    for row in profile.rows:
        source_name = (row.source_name or "").strip()
        target_name = (row.target_name or "").strip()
        if source_name and target_name:
            rows.append(row)
    return rows


def general_family_rows(settings, root: str):
    profile = getattr(settings, "profile", None)
    if profile is None:
        return []
    root = (root or "").strip()
    if not root:
        return []
    rows = []
    for row in profile.rows:
        source_name = (row.source_name or "").strip()
        target_name = (row.target_name or "").strip()
        if source_name and target_name == root:
            rows.append(row)
    return rows


def detach_general_source_row(settings, row):
    obj = settings.source_object
    if obj is None or obj.type != 'MESH':
        return "", 0

    current_actual = (getattr(row, "current_source_name", "") or row.source_name or "").strip()
    desired_root = (row.source_name or current_actual).strip()
    fallback = [row.source_name, row.target_name]
    arm = settings.armature_object
    extra_taken = []
    if arm is not None and arm.type == 'ARMATURE':
        extra_taken = [bone.name for bone in arm.data.bones if bone.name != current_actual]

    report = _algo.rename_single_vg_no_merge(
        obj,
        current_actual,
        desired_root,
        fallback_names=fallback,
        extra_taken_names=extra_taken,
    )
    final_name = report.get("final_name", "") or current_actual or desired_root

    bone_count = 0
    if arm is not None and arm.type == 'ARMATURE':
        bone_count = _algo.rename_single_bone_no_merge(
            arm,
            current_actual,
            final_name,
            fallback_names=fallback,
        )
    return final_name, bone_count


def sync_general_source_from_table(settings, *, save_backup: bool = False):
    obj = settings.source_object
    if obj is None or obj.type != 'MESH':
        return {"renamed": 0, "final_names": []}, 0

    rows = matched_general_rows(settings)
    ordered = [(row.source_name, row.target_name) for row in rows]
    if not ordered:
        return {"renamed": 0, "final_names": []}, 0

    report = _algo.reapply_no_merge_with_suffix_from_snapshot(obj, ordered, save_backup=save_backup)
    _algo.reorder_numeric_vertex_groups_first(obj)

    bone_count = 0
    arm = settings.armature_object
    if arm is not None and arm.type == 'ARMATURE':
        bone_count = _algo.reapply_armature_bones_with_suffix_from_snapshot(
            arm,
            ordered,
            save_backup=save_backup,
        )

    from . import props as _props
    prev = _props._suspend_general_row_update
    _props._suspend_general_row_update = True
    try:
        for row in settings.profile.rows:
            row.current_source_name = row.source_name or row.current_source_name
        for row, final_name in zip(rows, report.get("final_names", [])):
            row.current_source_name = final_name or row.current_source_name
    finally:
        _props._suspend_general_row_update = prev

    refresh_general_runtime(settings)
    return report, bone_count


def sync_general_source_row_incremental(settings, row, new_root: str):
    obj = settings.source_object
    if obj is None or obj.type != 'MESH':
        return "", 0

    current_actual = (getattr(row, "current_source_name", "") or row.source_name or "").strip()
    arm = settings.armature_object
    bone_count = 0
    final_name = current_actual
    detached_name = ""
    roots = []
    old_root = _algo.strip_dup_suffix(current_actual)
    if old_root:
        roots.append(old_root)
    new_root = (new_root or "").strip()
    if new_root and new_root not in roots:
        roots.append(new_root)

    from . import props as _props
    prev = _props._suspend_general_row_update
    _props._suspend_general_row_update = True
    try:
        if not new_root:
            detached_name, bone_delta = detach_general_source_row(settings, row)
            bone_count += bone_delta
            if detached_name:
                row.current_source_name = detached_name
                final_name = detached_name
        for root in roots:
            family_rows = general_family_rows(settings, root)
            if not family_rows:
                continue
            specs = []
            for family_row in family_rows:
                actual_name = (
                    getattr(family_row, "current_source_name", "")
                    or family_row.source_name
                    or family_row.target_name
                    or ""
                ).strip()
                specs.append({
                    "current": actual_name,
                    "fallbacks": [family_row.source_name, family_row.target_name],
                })

            if arm is not None and arm.type == 'ARMATURE':
                final_names, bone_delta = _algo.rename_synced_vg_bone_family_no_merge(
                    obj,
                    arm,
                    specs,
                    root,
                )
                bone_count += bone_delta
            else:
                final_names = _algo.rename_vg_family_no_merge(obj, specs, root)

            for family_row, assigned in zip(family_rows, final_names):
                if assigned:
                    family_row.current_source_name = assigned
                if family_row == row:
                    final_name = assigned or final_name
    finally:
        _props._suspend_general_row_update = prev

    if not new_root and detached_name:
        final_name = detached_name

    _algo.reorder_numeric_vertex_groups_first(obj)
    refresh_general_runtime(settings)
    return final_name, bone_count


def restore_general_source_to_original(settings):
    obj = settings.source_object
    if obj is None or obj.type != 'MESH':
        return False, 0
    if not _algo.restore_vg_snapshot(obj):
        return False, 0

    bone_count = 0
    arm = settings.armature_object
    if arm is not None and arm.type == 'ARMATURE':
        if _algo.restore_armature_bone_snapshot(arm):
            bone_count = len(arm.data.bones)

    from . import props as _props
    prev = _props._suspend_general_row_update
    _props._suspend_general_row_update = True
    try:
        for row in settings.profile.rows:
            row.current_source_name = row.source_name or row.current_source_name
    finally:
        _props._suspend_general_row_update = prev

    refresh_general_runtime(settings)
    return True, bone_count


class VELO_OT_match_row_add(bpy.types.Operator):
    bl_idname = "velo.match_row_add"
    bl_label = "新增空行"
    bl_options = {'REGISTER', 'UNDO'}

    after_index: IntProperty(default=-1)

    def execute(self, context):
        settings = get_settings(context)
        if settings is None or settings.profile is None:
            return {'CANCELLED'}
        profile = settings.profile
        row = profile.rows.add()
        row.enabled = True
        new_idx = len(profile.rows) - 1
        if 0 <= self.after_index < new_idx:
            profile.rows.move(new_idx, self.after_index + 1)
            profile.active_row_index = self.after_index + 1
        else:
            profile.active_row_index = new_idx
        autosync_general_to_text(settings)
        return {'FINISHED'}


class VELO_OT_match_row_remove(bpy.types.Operator):
    bl_idname = "velo.match_row_remove"
    bl_label = "删除当前行"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1)

    def execute(self, context):
        settings = get_settings(context)
        if settings is None or settings.profile is None:
            return {'CANCELLED'}
        profile = settings.profile
        idx = self.index if self.index >= 0 else profile.active_row_index
        if not (0 <= idx < len(profile.rows)):
            self.report({'ERROR'}, "无有效行可删除")
            return {'CANCELLED'}
        profile.rows.remove(idx)
        profile.active_row_index = max(0, min(idx, len(profile.rows) - 1))
        autosync_general_to_text(settings)
        return {'FINISHED'}


class VELO_OT_clear_mappings(bpy.types.Operator):
    bl_idname = "velo.clear_mappings"
    bl_label = "清空映射表"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = get_settings(context)
        if settings is None or settings.profile is None:
            return {'CANCELLED'}
        settings.profile.rows.clear()
        settings.profile.active_row_index = 0
        autosync_general_to_text(settings)
        refresh_general_runtime(settings, context, reset_pick=True)
        return {'FINISHED'}


class VELO_OT_match_stage_from_source(bpy.types.Operator):
    bl_idname = "velo.match_stage_from_source"
    bl_label = "从源物体补行"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = get_settings(context)
        obj = general_source(context)
        if settings is None or settings.profile is None or obj is None:
            self.report({'ERROR'}, "未指定源物体")
            return {'CANCELLED'}
        profile = settings.profile
        existing = {(row.source_name or "") for row in profile.rows}
        added = 0
        from . import props as _props
        prev = _props._suspend_general_row_update
        _props._suspend_general_row_update = True
        try:
            for _index, name in usable_vertex_groups(obj):
                if name in existing:
                    continue
                row = profile.rows.add()
                row.enabled = True
                row.source_name = name
                row.current_source_name = name
                existing.add(name)
                added += 1
        finally:
            _props._suspend_general_row_update = prev
        autosync_general_to_text(settings)
        self.report({'INFO'}, f"补行: 新增 {added} (源={obj.name})")
        return {'FINISHED'}


class VELO_OT_match_to_table_lite(bpy.types.Operator):
    bl_idname = "velo.match_to_table_lite"
    bl_label = "按位置匹配 → 写入本表"
    bl_options = {'REGISTER', 'UNDO'}

    overwrite_existing: BoolProperty(name="覆盖已有目标名", default=True)

    def execute(self, context):
        from mathutils.kdtree import KDTree
        from ..core.mapping.operators import _compute_centroids_world

        settings = get_settings(context)
        if settings is None or settings.profile is None:
            self.report({'ERROR'}, "未找到通用映射数据")
            return {'CANCELLED'}
        src = general_source(context)
        tgt = general_target(context)
        if src is None or tgt is None:
            self.report({'ERROR'}, "请先指定源物体与目标物体")
            return {'CANCELLED'}

        profile = settings.profile
        src_centroids = _compute_centroids_world(src)
        tgt_centroids = _compute_centroids_world(tgt)
        if not src_centroids:
            self.report({'ERROR'}, f"源物体 {src.name} 没有可用的有权重顶点组")
            return {'CANCELLED'}
        if not tgt_centroids:
            self.report({'ERROR'}, f"目标物体 {tgt.name} 没有可用的有权重顶点组")
            return {'CANCELLED'}

        src_local = compute_all_centroids_local(src)
        tgt_local = compute_all_centroids_local(tgt)

        tgt_items = list(tgt_centroids.items())
        kd = KDTree(len(tgt_items))
        for idx, (_group_index, world) in enumerate(tgt_items):
            kd.insert(world, idx)
        kd.balance()

        tgt_name_by_idx = {vg.index: vg.name for vg in tgt.vertex_groups}
        existing = {row.source_name: row for row in profile.rows if row.source_name}

        added = 0
        updated = 0
        skipped = 0
        from . import props as _props
        prev = _props._suspend_general_row_update
        _props._suspend_general_row_update = True
        try:
            for vg in src.vertex_groups:
                src_world = src_centroids.get(vg.index)
                if src_world is None:
                    continue
                src_name = vg.name
                if not src_name:
                    continue
                _co, kd_index, distance = kd.find(src_world)
                if kd_index is None:
                    skipped += 1
                    continue
                if settings.use_max_distance and distance > settings.max_match_distance:
                    skipped += 1
                    continue
                tgt_group_index, _tgt_world = tgt_items[kd_index]
                best_name = tgt_name_by_idx.get(tgt_group_index, "")
                if not best_name:
                    skipped += 1
                    continue
                row = existing.get(src_name)
                if row is None:
                    row = profile.rows.add()
                    row.enabled = True
                    row.source_name = src_name
                    row.current_source_name = src_name
                    row.target_name = best_name
                    existing[src_name] = row
                    added += 1
                else:
                    if row.target_name and not self.overwrite_existing:
                        skipped += 1
                        continue
                    if row.target_name != best_name:
                        row.target_name = best_name
                        updated += 1
                source_centroid = src_local.get(vg.index)
                if source_centroid is not None:
                    row.source_centroid_local = source_centroid
                    row.has_source_centroid = True
                target_centroid = tgt_local.get(tgt_group_index)
                if target_centroid is not None:
                    row.target_centroid_local = target_centroid
                    row.has_target_centroid = True
        finally:
            _props._suspend_general_row_update = prev

        if general_source_is_target_named(settings):
            sync_general_source_from_table(settings, save_backup=False)

        autosync_general_to_text(settings)
        self.report({'INFO'}, f"匹配(KDTree): 新增 {added}, 更新 {updated}, 跳过 {skipped}")
        return {'FINISHED'}


class VELO_OT_match_table_to_text(bpy.types.Operator):
    bl_idname = "velo.match_table_to_text"
    bl_label = "→ 内置文本"
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = get_settings(context)
        if settings is None:
            return {'CANCELLED'}
        autosync_general_to_text(settings)
        tb = settings.active_general_text
        self.report({'INFO'}, f"已写入 Text『{tb.name if tb else '?'}』")
        return {'FINISHED'}


class VELO_OT_match_table_from_text(bpy.types.Operator):
    bl_idname = "velo.match_table_from_text"
    bl_label = "← 内置文本"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = get_settings(context)
        if settings is None:
            return {'CANCELLED'}
        tb = settings.active_general_text
        if tb is None:
            self.report({'ERROR'}, "未选中映射表 Text")
            return {'CANCELLED'}
        text = tb.as_string()
        load_general_text_into_settings(settings, text)
        autosync_general_to_text(settings)
        self.report({'INFO'}, f"从 Text 同步: rows={len(settings.profile.rows) if settings.profile else 0}")
        return {'FINISHED'}


class VELO_OT_match_table_export(bpy.types.Operator):
    bl_idname = "velo.match_table_export"
    bl_label = "导出映射表"
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH')
    filename_ext = ".txt"
    filter_glob: StringProperty(default="*.txt", options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = "velo_general_mapping.txt"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        settings = get_settings(context)
        if settings is None or settings.profile is None:
            return {'CANCELLED'}
        try:
            with open(self.filepath, 'w', encoding='utf-8') as handle:
                handle.write("# velo_tools 通用映射表\n")
                handle.write("# 格式: <源名>\t<目标名>\n")
                handle.write(serialize_general_text(settings))
                handle.write("\n")
        except Exception as exc:
            self.report({'ERROR'}, f"写入失败: {exc}")
            return {'CANCELLED'}
        self.report({'INFO'}, f"已导出到 {self.filepath}")
        return {'FINISHED'}


class VELO_OT_match_table_import(bpy.types.Operator):
    bl_idname = "velo.match_table_import"
    bl_label = "导入映射表"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')
    filename_ext = ".txt"
    filter_glob: StringProperty(default="*.txt", options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath or not os.path.exists(self.filepath):
            self.report({'ERROR'}, "未选择有效文件")
            return {'CANCELLED'}
        settings = get_settings(context)
        if settings is None or settings.profile is None:
            return {'CANCELLED'}
        try:
            with open(self.filepath, 'r', encoding='utf-8') as handle:
                raw = handle.read()
        except Exception as exc:
            self.report({'ERROR'}, f"读取失败: {exc}")
            return {'CANCELLED'}
        load_general_text_into_settings(settings, raw)
        autosync_general_to_text(settings)
        self.report({'INFO'}, f"导入完成: rows={len(settings.profile.rows)}")
        return {'FINISHED'}


def build_general_ordered_pairs(settings):
    src_to_tgt = []
    seen_target = set()
    tgt_to_src = []
    src_to_tgt_unique = []
    for row in matched_general_rows(settings):
        src_to_tgt.append((row.source_name, row.target_name))
        if row.target_name in seen_target:
            continue
        seen_target.add(row.target_name)
        tgt_to_src.append((row.target_name, row.source_name))
        src_to_tgt_unique.append((row.source_name, row.target_name))
    return src_to_tgt, tgt_to_src, src_to_tgt_unique


class VELO_OT_general_source_to_unified(bpy.types.Operator):
    bl_idname = "velo.general_source_to_unified"
    bl_label = "将源物体改名为目标名"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = get_settings(context)
        obj = general_source(context)
        if settings is None or obj is None:
            self.report({'ERROR'}, "未指定源物体")
            return {'CANCELLED'}
        rows = matched_general_rows(settings)
        if not rows:
            self.report({'ERROR'}, "映射表为空，请先补行并匹配")
            return {'CANCELLED'}
        report, bone_count = sync_general_source_from_table(settings, save_backup=True)
        refresh_general_runtime(settings, context)
        self.report({'INFO'}, f"源→目标名: 改名 {report['renamed']} (骨骼 {bone_count})")
        return {'FINISHED'}


class VELO_OT_general_source_to_original(bpy.types.Operator):
    bl_idname = "velo.general_source_to_original"
    bl_label = "将源物体还原为原名字"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = get_settings(context)
        obj = general_source(context)
        if settings is None or obj is None:
            self.report({'ERROR'}, "未指定源物体")
            return {'CANCELLED'}
        ok, bone_count = restore_general_source_to_original(settings)
        if not ok:
            self.report({'ERROR'}, "未找到 VG 快照，无法无损还原")
            return {'CANCELLED'}
        refresh_general_runtime(settings, context)
        self.report({'INFO'}, f"源→原名字: 已用快照无损还原 (骨骼 {bone_count})")
        return {'FINISHED'}


class VELO_OT_general_target_to_mmd(bpy.types.Operator):
    bl_idname = "velo.general_target_to_mmd"
    bl_label = "目标物体改名成源物体名字"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = get_settings(context)
        obj = general_target(context)
        if settings is None or obj is None:
            self.report({'ERROR'}, "未指定目标物体")
            return {'CANCELLED'}
        _src_to_tgt, tgt_to_src, _src_to_tgt_unique = build_general_ordered_pairs(settings)
        if not tgt_to_src:
            self.report({'ERROR'}, "映射表为空，请先补行并匹配")
            return {'CANCELLED'}
        report = _algo.rename_no_merge_with_suffix(obj, tgt_to_src, save_backup=True)
        desired = [source_name for _target_name, source_name in tgt_to_src]
        _algo.reorder_vertex_groups_by_order(obj, desired)
        refresh_general_runtime(settings, context)
        self.report({'INFO'}, f"目标→源名: 改名 {report['renamed']} (目标={obj.name})")
        return {'FINISHED'}


class VELO_OT_general_target_to_unified(bpy.types.Operator):
    bl_idname = "velo.general_target_to_unified"
    bl_label = "将目标物体改名为目标名"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = get_settings(context)
        obj = general_target(context)
        if settings is None or obj is None:
            self.report({'ERROR'}, "未指定目标物体")
            return {'CANCELLED'}
        if _algo.restore_vg_snapshot(obj):
            refresh_general_runtime(settings, context)
            self.report({'INFO'}, f"目标→目标名: 已用快照无损还原 (目标={obj.name})")
            return {'FINISHED'}
        _src_to_tgt, _tgt_to_src, src_to_tgt_unique = build_general_ordered_pairs(settings)
        ordered = src_to_tgt_unique
        if not ordered:
            self.report({'ERROR'}, "映射表为空且无快照可还原")
            return {'CANCELLED'}
        report = _algo.rename_no_merge_with_suffix(obj, ordered, save_backup=False)
        refresh_general_runtime(settings, context)
        self.report({'INFO'}, f"目标→目标名: 改名 {report['renamed']} (目标={obj.name})")
        return {'FINISHED'}


class VELO_OT_general_text_new(bpy.types.Operator):
    bl_idname = "velo.general_text_new"
    bl_label = "新建映射表"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="名称", default="")

    def invoke(self, context, event):
        settings = get_settings(context)
        src = settings.source_object if settings else None
        default = f"velo_general_{src.name}.txt" if src is not None else "velo_general_new.txt"
        idx = 1
        candidate = default
        while bpy.data.texts.get(candidate) is not None:
            idx += 1
            stem = default[:-4] if default.endswith('.txt') else default
            candidate = f"{stem}.{idx:03d}.txt"
        self.name = candidate
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        settings = get_settings(context)
        if settings is None or settings.profile is None:
            return {'CANCELLED'}
        name = (self.name or "").strip() or "velo_general_new.txt"
        if not name.endswith('.txt'):
            name += '.txt'
        tb = bpy.data.texts.get(name) or bpy.data.texts.new(name)
        tb.from_string("# velo_tools 通用映射表\n# 格式: <源名>\\t<目标名>\n")
        settings.profile.rows.clear()
        settings.active_general_text = tb
        src = settings.source_object
        if src is not None:
            try:
                src["velo_general_text"] = tb.name
            except Exception:
                pass
        self.report({'INFO'}, f"已新建并切换到映射表: {tb.name}")
        return {'FINISHED'}


_classes = (
    VELO_OT_match_row_add,
    VELO_OT_match_row_remove,
    VELO_OT_clear_mappings,
    VELO_OT_match_stage_from_source,
    VELO_OT_match_to_table_lite,
    VELO_OT_match_table_to_text,
    VELO_OT_match_table_from_text,
    VELO_OT_match_table_export,
    VELO_OT_match_table_import,
    VELO_OT_general_source_to_unified,
    VELO_OT_general_source_to_original,
    VELO_OT_general_target_to_mmd,
    VELO_OT_general_target_to_unified,
    VELO_OT_general_text_new,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass