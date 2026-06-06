"""属性与场景数据。"""

import json
import re

import bpy
from bpy.props import (
    StringProperty,
    FloatProperty,
    FloatVectorProperty,
    BoolProperty,
    IntProperty,
    EnumProperty,
    PointerProperty,
    CollectionProperty,
)


_suspend_updates = False
_suspend_history = False
_suspend_base_reload = False
_SUFFIX_RE = re.compile(r"\.\d{3}$")


# ============================================================
# 每对象映射表持久化（v0.3.0_R3fix 起）
#
# 在 base_object 上以自定义属性 "velo_local_rename_map" 存一份 JSON：
#   {
#     "rows": [
#       {"src_orig": str, "current": str, "tgt_idx": int, "distance": float}
#     ],
#     "target_object": str | "",
#   }
# 切换/吸管选择 base_object 时，会自动从该对象上重建 s.mappings；
# 改名 / 还原时同步回写。
# ============================================================

PER_OBJ_KEY = "velo_local_rename_map"


def load_per_object_map(obj):
    if obj is None:
        return None
    raw = obj.get(PER_OBJ_KEY)
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(str(raw))
    except Exception:
        return None


def save_per_object_map(obj, data: dict):
    if obj is None:
        return
    try:
        obj[PER_OBJ_KEY] = json.dumps(data or {}, ensure_ascii=False)
    except Exception:
        pass


def clear_per_object_map(obj):
    if obj is None:
        return
    if PER_OBJ_KEY in obj.keys():
        try:
            del obj[PER_OBJ_KEY]
        except Exception:
            pass


def _refresh_available_base_vgs(settings):
    coll = getattr(settings, "available_base_vgs", None)
    if coll is None:
        return
    coll.clear()
    names = set()
    obj = getattr(settings, "base_object", None)
    if obj is not None and obj.type == 'MESH':
        try:
            names.update(vg.name for vg in obj.vertex_groups)
        except Exception:
            pass
    for row in getattr(settings, "mappings", ()):
        for name in (
            (getattr(row, "original_name", "") or "").strip(),
            (getattr(row, "current_name", "") or "").strip(),
        ):
            if name:
                names.add(name)
    for name in sorted(names):
        item = coll.add()
        item.name = name


def _serialize_mapping_row(it) -> dict:
    return {
        "src_orig": it.original_name,
        "current_src": it.current_name,
        "target_name": it.target_name,
        "matched": bool(it.matched),
        "enabled": bool(getattr(it, "enabled", True)),
        "tgt_idx": int(it.target_vg_index),
        "distance": float(it.distance),
        # 缓存重心避免切换对象时重算（性能关键）
        "bc": list(it.base_centroid_local) if it.has_base_centroid else None,
        "tc": list(it.target_centroid_local) if it.has_target_centroid else None,
    }


def _legacy_target_name_from_row(row: dict, target):
    target_name = (row.get("target_name") or "").strip()
    if target_name:
        return target_name

    legacy_current = (row.get("current") or "").strip()
    tgt_idx = int(row.get("tgt_idx", -1))
    if target is not None and target.type == 'MESH':
        if legacy_current and target.vertex_groups.get(legacy_current) is not None:
            return legacy_current
        if 0 <= tgt_idx < len(target.vertex_groups):
            try:
                return target.vertex_groups[tgt_idx].name
            except Exception:
                pass
    return ""


def serialize_mappings_to_dict(settings) -> dict:
    rows = []
    for it in settings.mappings:
        rows.append(_serialize_mapping_row(it))
    target = settings.target_object
    base = settings.base_object
    # state: 默认 RENAMED（匹配后即处于改名后状态）；revert 时改 ORIGINAL
    existing = load_per_object_map(base)
    state = (existing or {}).get("state") or "RENAMED"
    return {
        "rows": rows,
        "target_object": target.name if target else "",
        "state": state,
    }


def sync_mappings_to_base_object(settings):
    """把当前 s.mappings 写回 base_object 上的自定义属性。"""
    base = settings.base_object
    if base is None:
        return
    save_per_object_map(base, serialize_mappings_to_dict(settings))


def set_per_object_state(obj, state: str):
    if obj is None:
        return
    data = load_per_object_map(obj) or {}
    data["state"] = state
    save_per_object_map(obj, data)


def get_per_object_state(obj):
    data = load_per_object_map(obj)
    if not data:
        return None
    return data.get("state")


def rebuild_mappings_from_per_object(settings, base):
    """从 base 对象上的自定义属性恢复 s.mappings 显示（不重跑算法、不扫顶点）。"""
    settings.mappings.clear()
    settings.unmatched_sources.clear()
    settings.unmatched_targets.clear()
    settings.rename_history.clear()
    settings.source_baselines.clear()
    settings.baseline_object_name = base.name if base else ""

    data = load_per_object_map(base) if base else None
    if not data or not isinstance(data, dict):
        return 0

    rows = data.get("rows") or []
    if not isinstance(rows, list):
        return 0

    target = settings.target_object

    global _suspend_updates
    for src_idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        orig = row.get("src_orig") or ""
        curr = (row.get("current_src") or row.get("current") or orig)
        target_name = _legacy_target_name_from_row(row, target)
        enabled = bool(row.get("enabled", True))
        tgt_idx = int(row.get("tgt_idx", -1))
        matched = bool(row.get("matched", tgt_idx >= 0))
        dist = float(row.get("distance", -1.0))
        bc = row.get("bc")
        tc = row.get("tc")
        if not orig:
            continue

        bl = settings.source_baselines.add()
        bl.source_index = src_idx
        bl.baseline_name = orig

        it = settings.mappings.add()
        it.enabled = enabled
        it.source_index = src_idx
        it.original_name = orig
        it.current_name = curr
        it.distance = dist
        it.matched = matched
        it.target_vg_index = tgt_idx
        _suspend_updates = True
        try:
            it.target_name = target_name
        finally:
            _suspend_updates = False

        if bc and len(bc) == 3:
            it.base_centroid_local = bc
            it.has_base_centroid = True
        if tc and len(tc) == 3:
            it.target_centroid_local = tc
            it.has_target_centroid = True

    return len(settings.mappings)


def _on_base_object_update(self, context):
    """切换原物体时自动重建匹配结果面板。"""
    global _suspend_base_reload
    if _suspend_base_reload:
        return
    s = context.scene.velo_tools
    base = s.base_object

    # 切走前先把当前未保存的编辑保存到上一个 base
    prev_name = s.baseline_object_name
    if prev_name and (base is None or base.name != prev_name):
        prev = bpy.data.objects.get(prev_name)
        if prev and len(s.mappings) > 0:
            try:
                # 临时把 base 指回去做序列化
                rows = []
                for it in s.mappings:
                    rows.append(_serialize_mapping_row(it))
                old = load_per_object_map(prev) or {}
                state = old.get("state") or "RENAMED"
                save_per_object_map(prev, {"rows": rows, "state": state})
            except Exception:
                pass

    if base is None:
        s.mappings.clear()
        s.unmatched_sources.clear()
        s.unmatched_targets.clear()
        s.baseline_object_name = ""
        return
    rebuild_mappings_from_per_object(s, base)
    # 任务4：基于对象绑定切换映射表 Text（材质分离/合并后每个部件保留各自映射）
    try:
        bound = base.get("velo_general_text", "")
        if bound:
            tb = bpy.data.texts.get(bound)
            if tb is not None and s.active_general_text != tb:
                s.active_general_text = tb  # update 回调会重建 mappings
    except Exception:
        pass
    try:
        from . import overlay as _ov
        _ov.invalidate_cache()
    except Exception:
        pass
    try:
        _refresh_available_base_vgs(s)
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _on_target_object_update(self, context):
    """切换目标物体时只触发重绘，不清空 s.mappings（保留用户未保存的编辑）。"""
    try:
        from . import overlay as _ov
        _ov.invalidate_cache()
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _on_match_show_overlay_update(self, context):
    """旧通用 overlay 钩子已停用；通用区已迁移到 general_mapping.props。"""
    return


def _redraw_view3d(context=None):
    """重绘所有窗口的 VIEW_3D area（含 UI region）。

    用 window_manager 遍历，而非 context.screen——属性 update 回调里 context.screen
    有时为 None，会导致重绘根本没发生。返回重绘的 area 数，便于诊断。
    """
    n = 0
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return 0
    for win in wm.windows:
        screen = getattr(win, "screen", None)
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
                n += 1
                for region in area.regions:
                    if region.type == 'UI':
                        region.tag_redraw()
    return n


def _on_active_game_update(self, context):
    """切换当前游戏（终末地/鸣潮）时重绘 N 面板。

    问题 A 修复（A2，2026-06-01，经真实 Blender 4.4 GUI 实测确认）：两个游戏根面板的 poll 已改为
    只判 active_tab=='GAME'，进 GAME tab 时即「恒实例化」；切 active_game 只是在已实例化的面板上
    按 active_game 切内容（root.draw 早退 + 子面板 poll 门控，见 games/_a2_panels.py）。因此切游戏
    只需一个 plain redraw 即可立即生效，不再需要 0 延迟 timer 重注册根面板——1.1.1 的 timer 重注册
    经实测对本问题无效（根面板从未实例化时重注册也生不出实例），故连同 _rebuild_active_game_panels 删除。"""
    _redraw_view3d()


def _on_active_tab_update(self, context):
    if getattr(self, "active_tab", None) != 'WEIGHT':
        return
    try:
        from .weights import runtime as _weight_runtime
        _weight_runtime.reset_overlay_pick_runtime(context)
    except Exception:
        pass


def strip_dup_suffix(name: str) -> str:
    if not name:
        return name
    return _SUFFIX_RE.sub("", name)


def lookup_vgroup(obj, name):
    """先精确匹配, 失败则按剥后缀名再匹配一次。"""
    if not (obj and obj.type == 'MESH' and name):
        return None
    vg = obj.vertex_groups.get(name)
    if vg:
        return vg
    base = strip_dup_suffix(name)
    if base != name:
        return obj.vertex_groups.get(base)
    return None


def _drop_by_name(coll, *names):
    """从集合里移除指定 name 的所有项。"""
    targets = {n for n in names if n}
    for i in range(len(coll) - 1, -1, -1):
        if coll[i].name in targets:
            coll.remove(i)


def _add_unmatched_unique(coll, name, reason, obj):
    """若 name 不在 coll 中且 obj 上存在该顶点组, 则加入 (并缓存重心)。"""
    if not name or coll is None:
        return
    base_name = strip_dup_suffix(name)
    for it in coll:
        if it.name == name or strip_dup_suffix(it.name) == base_name:
            return
    if not (obj and obj.type == 'MESH'):
        return
    vg = obj.vertex_groups.get(name) or obj.vertex_groups.get(base_name)
    if not vg:
        return
    # 延迟引入避免循环
    from . import operators as _ops
    c = _ops.vgroup_centroid_local(obj, vg.index)
    item = coll.add()
    item.name = vg.name
    item.reason = reason
    if c is not None:
        item.centroid_local = c
        item.has_centroid = True


def _push_rename_history(settings, source_index, original_name, old_name, new_name):
    """写入本地改名历史, 供"仅撤销改名"使用。"""
    if not original_name or not old_name or not new_name:
        return
    if old_name == new_name:
        return
    item = settings.rename_history.add()
    item.source_index = source_index
    item.original_name = original_name
    item.old_name = old_name
    item.new_name = new_name
    # 防止历史无限增长
    if len(settings.rename_history) > 300:
        settings.rename_history.remove(0)


def _on_target_name_update(self, context):
    """二次编辑目标名称时:
    - 同步重命名顶点组与骨骼
    - 刷新缓存重心 (后缀回退)
    - 若成功定位到目标重心 -> 标记为 matched, 清掉相应未匹配项
    """
    global _suspend_updates, _suspend_history
    if _suspend_updates:
        return

    new_name = (self.target_name or "").strip()

    s = context.scene.velo_tools
    base = s.base_object
    target = s.target_object
    arm = s.armature_object
    old = self.current_name or self.original_name

    from . import operators as _ops
    from . import overlay as _ov
    from .core.mapping import algorithms as _algo
    from mathutils import Vector

    if not new_name:
        old_target_vg_index = self.target_vg_index
        old_target_vg_name = ""
        if (
            old_target_vg_index >= 0
            and target
            and target.type == 'MESH'
            and old_target_vg_index < len(target.vertex_groups)
        ):
            old_target_vg_name = target.vertex_groups[old_target_vg_index].name

        if base and base.type == 'MESH' and _algo.has_vg_snapshot(base):
            final_name, _bone_n = _ops._sync_general_source_row_incremental(s, self, "")
            base_lookup_name = (final_name or self.current_name or self.original_name or "").strip()
            bg = None
            if base_lookup_name:
                bg = (base.vertex_groups.get(base_lookup_name)
                      or base.vertex_groups.get(self.current_name)
                      or base.vertex_groups.get(self.original_name))
            if bg:
                c = _ops.vgroup_centroid_local(base, bg.index)
                if c is not None:
                    self.base_centroid_local = c
                    self.has_base_centroid = True
        self.has_target_centroid = False
        self.target_vg_index = -1
        self.matched = False
        if self.original_name:
            _add_unmatched_unique(
                s.unmatched_sources,
                self.original_name,
                "手动断开映射",
                base,
            )
        if old_target_vg_index >= 0 and old_target_vg_name:
            still_claimed = False
            for it in s.mappings:
                if it == self:
                    continue
                if it.matched and it.target_vg_index == old_target_vg_index:
                    still_claimed = True
                    break
            if not still_claimed:
                _add_unmatched_unique(
                    s.unmatched_targets,
                    old_target_vg_name,
                    "原匹配被断开",
                    target,
                )
        _ops._general_autosync_to_text(s)
        _ov.invalidate_cache()
        _refresh_available_base_vgs(s)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return

    # 若源物体当前已经切到统一编号态，则后续映射改动必须：
    # 只更新当前这一行对应的实际名字；重复 unified（如 36 / 36.001）
    # 用不合并后缀分配即可，无需整表重放。
    if base and base.type == 'MESH' and _algo.has_vg_snapshot(base):
        final_name, _bone_n = _ops._sync_general_source_row_incremental(s, self, new_name)
        base_lookup_name = (final_name or self.current_name or self.original_name or new_name).strip()
        bg = None
        if base_lookup_name:
            bg = (base.vertex_groups.get(base_lookup_name)
                  or base.vertex_groups.get(self.current_name)
                  or base.vertex_groups.get(self.original_name)
                  or base.vertex_groups.get(new_name))
        if bg:
            c = _ops.vgroup_centroid_local(base, bg.index)
            if c is not None:
                self.base_centroid_local = c
                self.has_base_centroid = True

        found_target = False
        new_target_vg_index = -1
        new_target_vg_name = ""
        if target and target.type == 'MESH':
            tg = lookup_vgroup(target, new_name)
            if tg:
                c = _ops.vgroup_centroid_local(target, tg.index)
                if c is not None:
                    self.target_centroid_local = c
                    self.has_target_centroid = True
                    found_target = True
                    new_target_vg_index = tg.index
                    new_target_vg_name = tg.name
            if not found_target:
                self.has_target_centroid = False

        if self.has_base_centroid and self.has_target_centroid and base and target:
            bw = base.matrix_world @ Vector(self.base_centroid_local)
            tw = target.matrix_world @ Vector(self.target_centroid_local)
            self.distance = (bw - tw).length

        old_target_vg_index = self.target_vg_index
        old_target_vg_name = ""
        if (
            old_target_vg_index >= 0
            and target
            and target.type == 'MESH'
            and old_target_vg_index < len(target.vertex_groups)
        ):
            old_target_vg_name = target.vertex_groups[old_target_vg_index].name

        self.target_vg_index = new_target_vg_index
        self.matched = bool(found_target)
        if found_target:
            _drop_by_name(s.unmatched_sources, self.original_name)
            _drop_by_name(
                s.unmatched_targets,
                new_target_vg_name,
                strip_dup_suffix(new_target_vg_name),
            )
        elif self.original_name:
            _add_unmatched_unique(
                s.unmatched_sources,
                self.original_name,
                "手动改名后失配",
                base,
            )

        if (
            old_target_vg_index >= 0
            and old_target_vg_index != new_target_vg_index
            and old_target_vg_name
        ):
            still_claimed = False
            for it in s.mappings:
                if it == self:
                    continue
                if it.matched and it.target_vg_index == old_target_vg_index:
                    still_claimed = True
                    break
            if not still_claimed:
                _add_unmatched_unique(
                    s.unmatched_targets,
                    old_target_vg_name,
                    "原匹配被改走",
                    target,
                )

        _ops._general_autosync_to_text(s)
        _ov.invalidate_cache()
        _refresh_available_base_vgs(s)
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return

    if new_name != old:
        old_name_for_history = old
        final_name = new_name
        if arm and arm.type == 'ARMATURE':
            bone = arm.data.bones.get(old)
            if bone:
                bone.name = new_name
                final_name = bone.name

        if base and base.type == 'MESH':
            vg = base.vertex_groups.get(old) or base.vertex_groups.get(final_name)
            if vg and vg.name != final_name:
                vg.name = final_name
                final_name = vg.name

        _suspend_updates = True
        try:
            self.current_name = final_name
            if self.target_name != final_name:
                self.target_name = final_name
        finally:
            _suspend_updates = False

        if (not _suspend_history) and final_name != old_name_for_history:
            _push_rename_history(
                s,
                self.source_index,
                self.original_name,
                old_name_for_history,
                final_name,
            )
    else:
        final_name = old

    # 基物体重心
    if base and base.type == 'MESH':
        bg = base.vertex_groups.get(final_name)
        if bg:
            c = _ops.vgroup_centroid_local(base, bg.index)
            if c is not None:
                self.base_centroid_local = c
                self.has_base_centroid = True

    # 目标重心 (后缀回退)
    found_target = False
    new_target_vg_index = -1
    new_target_vg_name = ""
    if target and target.type == 'MESH':
        tg = lookup_vgroup(target, final_name)
        if tg:
            c = _ops.vgroup_centroid_local(target, tg.index)
            if c is not None:
                self.target_centroid_local = c
                self.has_target_centroid = True
                found_target = True
                new_target_vg_index = tg.index
                new_target_vg_name = tg.name
        if not found_target:
            self.has_target_centroid = False

    # 距离
    if self.has_base_centroid and self.has_target_centroid and base and target:
        bw = base.matrix_world @ Vector(self.base_centroid_local)
        tw = target.matrix_world @ Vector(self.target_centroid_local)
        self.distance = (bw - tw).length

    # 旧目标 vgroup 索引 (编辑前的真实目标), 用来判断是否变孤儿
    old_target_vg_index = self.target_vg_index
    old_target_vg_name = ""
    if (
        old_target_vg_index >= 0
        and target
        and target.type == 'MESH'
        and old_target_vg_index < len(target.vertex_groups)
    ):
        old_target_vg_name = target.vertex_groups[old_target_vg_index].name

    # 写回新的目标索引
    self.target_vg_index = new_target_vg_index

    # 标记匹配状态 + 同步未匹配集合
    if found_target:
        self.matched = True
        _drop_by_name(s.unmatched_sources, self.original_name)
        _drop_by_name(
            s.unmatched_targets,
            new_target_vg_name,
            strip_dup_suffix(new_target_vg_name),
        )
    else:
        self.matched = False
        if self.original_name:
            _add_unmatched_unique(
                s.unmatched_sources,
                self.original_name,
                "手动改名后失配",
                base,
            )

    # 旧目标若变孤儿 (没有任何其它已匹配行的 target_vg_index 等于它), 加回未匹配目标
    if (
        old_target_vg_index >= 0
        and old_target_vg_index != new_target_vg_index
        and old_target_vg_name
    ):
        still_claimed = False
        for it in s.mappings:
            if it == self:
                continue
            if it.matched and it.target_vg_index == old_target_vg_index:
                still_claimed = True
                break
        if not still_claimed:
            _add_unmatched_unique(
                s.unmatched_targets,
                old_target_vg_name,
                "原匹配被改走",
                target,
            )

    _ov.invalidate_cache()
    _refresh_available_base_vgs(s)
    # 注意：per-object 持久化只在匹配结束 / 显式还原 / 切换对象时同步，
    # 不在每次按键改名时全量序列化（性能关键，避免 200 行 × 每次按键的 JSON）
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


class VELO_CandidateItem(bpy.types.PropertyGroup):
    """单个匹配候选 (供 UI 下拉切换)。"""
    target_vg_index: IntProperty(default=-1)
    target_name: StringProperty()
    score: FloatProperty(default=-1.0)


class VELO_AvailableVGName(bpy.types.PropertyGroup):
    pass


_suspend_shapekey_update = False


def _is_real_mesh_simple(obj):
    """与 mesh_ops.is_real_mesh 等价的轻量判定 (避免循环导入)。"""
    if obj is None or obj.type != 'MESH':
        return False
    if ".placeholder" in (obj.name or "").lower():
        return False
    if obj.data is not None and ".placeholder" in (obj.data.name or "").lower():
        return False
    return True


def _on_target_collection_update(self, context):
    """指定/切换目标集合时立刻扫描一次 (无需手动点刷新)。"""
    # 延迟导入避免循环
    try:
        from .mesh import shapekey_ops as _sk
    except Exception:
        return
    try:
        _sk.refresh_shapekey_list(context, force=True)
    except Exception:
        pass


def _on_shapekey_agg_name_update(self, context):
    """聚合面板里改名时, 把目标集合下所有同名形态键一起改掉。"""
    global _suspend_shapekey_update
    if _suspend_shapekey_update:
        return
    new_name = (self.name or "").strip()
    old_name = self.original_name
    if not new_name or not old_name or new_name == old_name:
        return

    s = context.scene.velo_tools
    coll = s.target_collection
    if coll is None:
        return

    renamed = 0
    for obj in coll.all_objects:
        if not _is_real_mesh_simple(obj):
            continue
        sk = obj.data.shape_keys
        if not sk:
            continue
        kb = sk.key_blocks.get(old_name)
        if kb is None:
            continue
        kb.name = new_name
        renamed += 1

    _suspend_shapekey_update = True
    try:
        # Blender 自动后缀化时同步显示真实名 (取最后一次 rename 的结果)
        self.original_name = new_name
        if self.name != new_name:
            self.name = new_name
    finally:
        _suspend_shapekey_update = False


def _on_shapekey_agg_value_update(self, context):
    """聚合面板里改 value 时, 把目标集合下所有同名形态键 value 一起设置。"""
    global _suspend_shapekey_update
    if _suspend_shapekey_update:
        return
    s = context.scene.velo_tools
    coll = s.target_collection
    if coll is None:
        return
    name = self.original_name or self.name
    if not name:
        return
    v = float(self.value)
    for obj in coll.all_objects:
        if not _is_real_mesh_simple(obj):
            continue
        sk = obj.data.shape_keys
        if not sk:
            continue
        kb = sk.key_blocks.get(name)
        if kb is None:
            continue
        if kb.value != v:
            kb.value = v


class VELO_ShapeKeyAggItem(bpy.types.PropertyGroup):
    """目标集合里出现过的形态键聚合条目 (仅 mesh.shape_keys, 即 MMD 顶点变形)。"""
    name: StringProperty(
        name="形态键名",
        description="编辑名称将同步重命名集合里所有同名形态键",
        update=_on_shapekey_agg_name_update,
    )
    original_name: StringProperty()
    count: IntProperty(default=0, description="该名称在多少个网格上出现")
    value: FloatProperty(
        name="值",
        default=0.0,
        min=-10.0,
        max=10.0,
        soft_min=0.0,
        soft_max=1.0,
        precision=3,
        description="同步设置集合里所有同名形态键的 value",
        update=_on_shapekey_agg_value_update,
    )


class VELO_MatchMappingItem(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="启用", default=True)
    source_index: IntProperty(name="源索引", default=-1)
    target_vg_index: IntProperty(name="目标顶点组索引", default=-1)
    original_name: StringProperty(name="原始名称")
    current_name: StringProperty(name="当前名称")
    target_name: StringProperty(
        name="目标名称",
        description="目标顶点组名; 直接编辑可二次修正, 同步顶点组与骨骼",
        update=_on_target_name_update,
    )
    distance: FloatProperty(name="距离", default=-1.0)
    matched: BoolProperty(default=True)

    base_centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    target_centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    has_base_centroid: BoolProperty(default=False)
    has_target_centroid: BoolProperty(default=False)

    # 完整候选排名 (按 score 升序, 含当前选中); UI 下拉时实时过滤掉当前再取前 N
    candidates: CollectionProperty(type=VELO_CandidateItem)


class VELO_UnmatchedItem(bpy.types.PropertyGroup):
    name: StringProperty()
    reason: StringProperty()
    centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    has_centroid: BoolProperty(default=False)


class VELO_RenameHistoryItem(bpy.types.PropertyGroup):
    source_index: IntProperty(default=-1)
    original_name: StringProperty()
    old_name: StringProperty()
    new_name: StringProperty()


class VELO_SourceBaselineItem(bpy.types.PropertyGroup):
    source_index: IntProperty(default=-1)
    baseline_name: StringProperty()


def _on_active_general_text_update(self, context):
    """切换映射表 Text 时：把该 Text 内容加载到 mappings。"""
    if _suspend_updates:
        return
    s = context.scene.velo_tools
    tb = s.active_general_text
    base = s.base_object
    if base is not None:
        try:
            base["velo_general_text"] = (tb.name if tb is not None else "")
        except Exception:
            pass
    if tb is None:
        return
    try:
        from . import operators as _ops
        _ops._load_general_text_into_mappings(s, tb.as_string())
        try:
            from .core.mapping import algorithms as _algo
            if base is not None and _algo.has_vg_snapshot(base):
                _ops._sync_general_source_from_table(s, save_backup=False)
        except Exception:
            pass
        _refresh_available_base_vgs(s)
    except Exception:
        pass


class VELO_ToolsSettings(bpy.types.PropertyGroup):
    base_object: PointerProperty(
        name="源物体",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
        update=lambda self, context: _on_base_object_update(self, context),
    )
    target_object: PointerProperty(
        name="目标物体",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
        update=lambda self, context: _on_target_object_update(self, context),
    )
    armature_object: PointerProperty(
        name="骨架",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE',
        description="可选; 选择后改名将同步到骨骼",
    )

    mappings: CollectionProperty(type=VELO_MatchMappingItem)
    active_mapping_index: IntProperty(default=0)
    available_base_vgs: CollectionProperty(type=VELO_AvailableVGName)

    unmatched_sources: CollectionProperty(type=VELO_UnmatchedItem)
    unmatched_targets: CollectionProperty(type=VELO_UnmatchedItem)
    active_unmatched_source_index: IntProperty(default=0)
    active_unmatched_target_index: IntProperty(default=0)

    rename_history: CollectionProperty(type=VELO_RenameHistoryItem)
    source_baselines: CollectionProperty(type=VELO_SourceBaselineItem)
    baseline_object_name: StringProperty(default="")

    # 任务4：当前映射表 Text 选择器（template_ID 样式，与 MMD 区一致）
    active_general_text: PointerProperty(
        name="映射表",
        type=bpy.types.Text,
        description="当前使用的映射表内置文本；点击三角小图标在多个映射表之间切换",
        update=_on_active_general_text_update,
    )

    # 匹配选项
    match_method: EnumProperty(
        name="匹配算法",
        items=[
            ('SAMPLES', "Top-K 加权样本", "每个顶点组取权重最高 K 个顶点做加权点云距离 (V0.0.5 算法)"),
            ('CENTROID', "权重重心", "仅比较权重中心 (旧算法, 速度最快, 区分度低)"),
        ],
        default='SAMPLES',
    )
    sample_count: IntProperty(
        name="采样数 K",
        default=8,
        min=1,
        max=64,
        description="每个顶点组用权重最高的 K 个顶点做匹配",
    )
    use_max_distance: BoolProperty(
        name="启用最大距离过滤",
        default=False,
        description="得分(距离)超过阈值的匹配视为失败, 保留原名",
    )
    max_match_distance: FloatProperty(
        name="最大匹配距离",
        default=0.5,
        min=0.0,
        soft_max=10.0,
    )

    # 可视化
    show_overlay: BoolProperty(
        name="启用可视化校对",
        default=False,
        description="旧通用映射可视化开关；当前通用映射已迁移到独立面板，此项不再自动关闭任何其它可视化功能",
        update=lambda self, context: _on_match_show_overlay_update(self, context),
    )
    only_show_active: BoolProperty(
        name="只显示当前选中行",
        default=False,
        description="仅显示当前选中映射行的可视化连线和端点",
    )
    overlay_max_distance: FloatProperty(
        name="距离阈值(可视化)",
        default=0.1,
        min=0.0,
        soft_max=2.0,
        description="可视化中判断匹配距离是否正常的阈值；超过阈值会显示为异常颜色",
    )
    show_labels: BoolProperty(
        name="显示名称标签",
        default=True,
        description="在可视化端点旁显示顶点组名称标签",
    )
    show_unmatched_targets: BoolProperty(
        name="显示未匹配的目标顶点组",
        default=True,
        description="显示目标网格上尚未被映射表认领的可用顶点组端点",
    )
    show_unmatched_sources: BoolProperty(
        name="显示未匹配的源顶点组",
        default=False,
        description="显示源网格上尚未被映射表认领的可用顶点组端点",
    )

    # ============================================================
    # 网格/形态键功能 (单独 N 面板 tab "Velo 网格")
    # ============================================================
    target_collection: PointerProperty(
        name="目标集合",
        type=bpy.types.Collection,
        description="形态键聚合面板的扫描范围",
        update=lambda self, context: _on_target_collection_update(self, context),
    )
    shapekey_items: CollectionProperty(type=VELO_ShapeKeyAggItem)
    active_shapekey_index: IntProperty(default=0)

    # 按材质拆分: 形态键"接近零位移"清理阈值
    # 单位 = 物体局部坐标 (米)。骨骼烘焙到形态键时常残留 1e-4 ~ 1e-3 量级的位移,
    # Blender 自带"清理"只删完全为 0 的, 这里给出可调阈值兜底。
    shapekey_cleanup_threshold: FloatProperty(
        name="形态键清理阈值",
        description=(
            "按材质拆分后, 若某形态键在该子网格上所有顶点的最大位移 ≤ 此阈值, "
            "则视为无效并删除 (单位: 米, 物体局部坐标)"
        ),
        default=1e-4,
        min=0.0,
        soft_max=1e-2,
        precision=6,
        step=0.01,
    )
    mesh_component_prefix_id: IntProperty(
        name="Component 编号",
        description="网格工具中为选中物体添加 Component 前缀时使用的编号",
        default=0,
        min=0,
        soft_max=999,
    )

    # 主面板顶部 tab 切换
    active_tab: EnumProperty(
        name="功能区",
        items=[
            ('MATCH', "顶点组工具", "顶点组名称匹配 / MMD 映射 / 顶点组操作"),
            ('MESH', "网格工具", "材质 / 拆分合并 / 形态键聚合 / 多物体雕刻"),
            ('WEIGHT', "权重工具", "权重传递 / 平滑 / 限制组数量"),
            ('GAME', "游戏", "游戏 MOD 工作流：终末地(EFMI) / 鸣潮(WWMI)"),
        ],
        default='MATCH',
        update=_on_active_tab_update,
    )

    # “游戏” tab 内的游戏选择器（下拉）：终末地(EFMI) / 鸣潮(WWMI)
    active_game: EnumProperty(
        name="游戏",
        items=[
            ('ENDFIELD', "终末地", "明日方舟：终末地 MOD 工作流（EFMITools）"),
            ('WUTHERING', "鸣潮", "鸣潮 MOD 工作流（WWMITools）"),
        ],
        default='ENDFIELD',
        update=_on_active_game_update,
    )


_classes = (
    VELO_CandidateItem,
    VELO_AvailableVGName,
    VELO_ShapeKeyAggItem,
    VELO_MatchMappingItem,
    VELO_UnmatchedItem,
    VELO_RenameHistoryItem,
    VELO_SourceBaselineItem,
    VELO_ToolsSettings,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.velo_tools = PointerProperty(type=VELO_ToolsSettings)


def unregister():
    if hasattr(bpy.types.Scene, "velo_tools"):
        del bpy.types.Scene.velo_tools
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
