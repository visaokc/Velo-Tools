"""三段映射核心算法（R3 阶段）。

PLAN §2.2 分四个阶段：
- 阶段 A：MOD 导入时自动构建 UnifiedVGTable（已在 R2 importer 完成）
- 阶段 B：用户编辑 MMD → unified 映射（本模块的 5 个算子覆盖）
- 阶段 C：用户分尸 + 标注归属（手工 + 集合命名约定）
- 阶段 D：导出时 MMD → unified → native 三段翻译（R4 在 exporter 调用本模块）

本文件只放算法，不直接处理 UI；算子写在 operators.py。
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable


_SUFFIX_RE = re.compile(r"\.\d{3}$")


def strip_dup_suffix(name: str) -> str:
    if not name:
        return name
    return _SUFFIX_RE.sub("", name)


# ============================================================
# 字典构建
# ============================================================

def build_mmd_to_unified(profile) -> dict:
    """profile: VELO_EF_MmdMappingProfile -> {mmd_name: unified_name}"""
    out = {}
    if profile is None:
        return out
    for row in profile.rows:
        if not row.mmd_name or not row.unified_name:
            continue
        out[row.mmd_name] = row.unified_name
    return out


def build_unified_to_mmd(profile) -> dict:
    """反向：{unified_name: mmd_name}（同一 unified 对应多个 mmd 时只保留第一个）"""
    out = {}
    if profile is None:
        return out
    for row in profile.rows:
        if not row.mmd_name or not row.unified_name:
            continue
        out.setdefault(row.unified_name, row.mmd_name)
    return out


def build_ordered_mmd_pairs(profile):
    """返回按 profile.rows 顺序的 [(mmd_name, unified_root)] 列表，供
    rename_no_merge_with_suffix / rename_armature_bones_with_suffix 使用。"""
    out = []
    if profile is None:
        return out
    for row in profile.rows:
        if not row.mmd_name or not row.unified_name:
            continue
        out.append((row.mmd_name, row.unified_name))
    return out


def build_unified_to_native(component_map) -> dict:
    """component_map: VELO_EF_ComponentMap -> {unified_name: native_name}"""
    out = {}
    if component_map is None:
        return out
    for p in component_map.pairs:
        if not p.unified_name or not p.native_name:
            continue
        out[p.unified_name] = p.native_name
    return out


# ============================================================
# 顶点组重命名 + 同名合并
# ============================================================

def _collect_weights(obj, vg_index: int) -> dict:
    """返回 {vertex_index: weight} （仅 weight > 0）。"""
    result = {}
    if obj is None or obj.type != 'MESH' or vg_index < 0:
        return result
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == vg_index and g.weight > 0.0:
                result[v.index] = g.weight
                break
    return result


def _collect_all_weights(obj) -> dict:
    """单遍扫描整个网格，返回 {vg_index: {vertex_index: weight}}。

    旧 _collect_weights 在每个 vg 上重新遍历整网格，rename_vertex_groups_with_merge
    复杂度是 O(VG * Verts)；此函数 O(Verts) 一次扫完，是 V0.1.4 卡顿的根因修复。
    """
    out = {}
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return out
    for v in obj.data.vertices:
        vi = v.index
        for g in v.groups:
            w = g.weight
            if w <= 0.0:
                continue
            d = out.get(g.group)
            if d is None:
                d = {}
                out[g.group] = d
            d[vi] = w
    return out


_NUMERIC_RE = re.compile(r"^\d+$")
_NUMERIC_FAMILY_RE = re.compile(r"^(\d+)(?:\.(\d{3}))?$")


def _numeric_sort_key(name: str):
    """让纯数字按数值排序，非数字按字典序后置。"""
    if name is None:
        return (1, "")
    if _NUMERIC_RE.match(name):
        try:
            return (0, int(name), name)
        except Exception:
            return (1, name)
    return (1, name)


def _numeric_family_sort_key(name: str):
    """让数字族（如 5 / 5.001 / 12 / 12.001）整体按数值排前。
    非数字名保持后置，由 reorder_vertex_groups_by_order 保持原顺序追加。"""
    if name is None:
        return (1, 0, 0, "")
    m = _NUMERIC_FAMILY_RE.match(name)
    if not m:
        return (1, 0, 0, name)
    root = int(m.group(1))
    suffix = int(m.group(2) or 0)
    return (0, root, suffix, name)


def allocate_unique_no_merge_name(desired_root: str, existing_names, *, current_name: str = "") -> str:
    """为不合并改名分配唯一名字。

    - 映射表里始终存 root（如 36）
    - 实际对象上若 root 已存在，则分配 36.001 / 36.002 ...
    - 若 current_name 本身就属于该 root 家族，则优先保留 current_name，避免无意义抖动
    """
    root = (desired_root or "").strip()
    if not root:
        return (current_name or "").strip()

    current = (current_name or "").strip()
    taken = {n for n in (existing_names or []) if n}
    if current:
        taken.discard(current)
        if strip_dup_suffix(current) == root:
            return current

    if root not in taken:
        return root

    index = 1
    while True:
        candidate = f"{root}.{index:03d}"
        if candidate not in taken:
            return candidate
        index += 1


_VG_ORDER_BACKUP_KEY = "velo_vg_order_backup"
_VG_SNAPSHOT_KEY = "velo_vg_snapshot_json"
_BONE_SNAPSHOT_KEY = "velo_bone_snapshot_json"


def has_vg_snapshot(obj, key: str = _VG_SNAPSHOT_KEY) -> bool:
    if obj is None:
        return False
    try:
        return bool(obj.get(key))
    except Exception:
        return False


def has_armature_bone_snapshot(arm_obj, key: str = _BONE_SNAPSHOT_KEY) -> bool:
    if arm_obj is None:
        return False
    try:
        return bool(arm_obj.get(key))
    except Exception:
        return False


def _unique_temp_names(existing_names, count: int, prefix: str):
    names = set(existing_names or [])
    out = []
    cursor = 0
    while len(out) < count:
        candidate = f"{prefix}{cursor}"
        cursor += 1
        if candidate in names:
            continue
        names.add(candidate)
        out.append(candidate)
    return out


def _family_spec_candidates(spec):
    candidates = []
    for name in [spec.get("current", ""), *(spec.get("fallbacks", []) or [])]:
        name = (name or "").strip()
        if name and name not in candidates:
            candidates.append(name)
    return candidates


def rename_synced_vg_bone_family_no_merge(obj, arm_obj, row_specs, desired_root: str, *, extra_taken_names=()):
    """对同一家族的 VG/骨骼做共享临时名的联动改名。

    先把每一行的 VG 改到该行独占的临时名，再把同一行骨骼改到相同临时名，
    最后把骨骼改到最终名并让 Blender 自动把同名 VG 一起带到最终名，避免
    骨骼临时名阶段误劫持其它已重分配好的 VG。
    """
    if obj is None or obj.type != 'MESH' or obj.data is None or not row_specs:
        return [], 0
    if arm_obj is None or arm_obj.type != 'ARMATURE':
        return rename_vg_family_no_merge(obj, row_specs, desired_root, extra_taken_names=extra_taken_names), 0

    resolved_vgs = []
    used_vg_indices = set()
    for spec in row_specs:
        vg = None
        for name in _family_spec_candidates(spec):
            cand = obj.vertex_groups.get(name)
            if cand is not None and cand.index not in used_vg_indices:
                vg = cand
                used_vg_indices.add(cand.index)
                break
        resolved_vgs.append(vg)

    resolved_bones = []
    used_bone_names = set()
    for spec in row_specs:
        bone = None
        for name in _family_spec_candidates(spec):
            cand = arm_obj.data.bones.get(name)
            if cand is not None and cand.name not in used_bone_names:
                bone = cand
                used_bone_names.add(cand.name)
                break
        resolved_bones.append(bone)

    family_vg_indices = {vg.index for vg in resolved_vgs if vg is not None}
    family_bone_names = {bone.name for bone in resolved_bones if bone is not None}
    outside = {vg.name for vg in obj.vertex_groups if vg.index not in family_vg_indices}
    outside.update({bone.name for bone in arm_obj.data.bones if bone.name not in family_bone_names})
    outside.update({n for n in (extra_taken_names or []) if n})

    final_names = []
    taken = set(outside)
    for spec, vg, bone in zip(row_specs, resolved_vgs, resolved_bones):
        current_name = ""
        if vg is not None:
            current_name = vg.name
        elif bone is not None:
            current_name = bone.name
        else:
            current_name = (spec.get("current", "") or "")
        if vg is None and bone is None:
            final_name = current_name
        else:
            final_name = allocate_unique_no_merge_name(desired_root, taken, current_name="")
            if not final_name:
                final_name = current_name
        final_names.append(final_name)
        if final_name:
            taken.add(final_name)

    if not any(vg is not None or bone is not None for vg, bone in zip(resolved_vgs, resolved_bones)):
        return final_names, 0

    temp_names = _unique_temp_names(
        {vg.name for vg in obj.vertex_groups}
        | {bone.name for bone in arm_obj.data.bones}
        | set(extra_taken_names or []),
        len(row_specs),
        "__velo_family_tmp_",
    )

    for vg, temp_name in zip(resolved_vgs, temp_names):
        if vg is None:
            continue
        try:
            vg.name = temp_name
        except Exception:
            pass

    for bone, temp_name in zip(resolved_bones, temp_names):
        if bone is None:
            continue
        try:
            bone.name = temp_name
        except Exception:
            pass

    renamed_bones = 0
    for bone, temp_name, final_name in zip(resolved_bones, temp_names, final_names):
        if bone is None or not final_name:
            continue
        cur = arm_obj.data.bones.get(temp_name) or bone
        try:
            cur.name = final_name
            renamed_bones += 1
        except Exception:
            pass

    for vg, temp_name, final_name in zip(resolved_vgs, temp_names, final_names):
        if vg is None or not final_name:
            continue
        cur = obj.vertex_groups.get(temp_name) or vg
        try:
            cur.name = final_name
        except Exception:
            pass
    return final_names, renamed_bones


def rename_vg_family_no_merge(obj, row_specs, desired_root: str, *, extra_taken_names=()):
    """仅对同一 unified root 家族的一组 VG 做两阶段改名，不重放整表。"""
    if obj is None or obj.type != 'MESH' or obj.data is None or not row_specs:
        return []

    resolved = []
    used_indices = set()
    for spec in row_specs:
        vg = None
        candidates = []
        for name in [spec.get("current", ""), *(spec.get("fallbacks", []) or [])]:
            name = (name or "").strip()
            if name and name not in candidates:
                candidates.append(name)
        for name in candidates:
            cand = obj.vertex_groups.get(name)
            if cand is not None and cand.index not in used_indices:
                vg = cand
                used_indices.add(cand.index)
                break
        resolved.append((spec, vg))

    family_vgs = [vg for _spec, vg in resolved if vg is not None]
    family_indices = {vg.index for vg in family_vgs}
    outside = {vg.name for vg in obj.vertex_groups if vg.index not in family_indices}
    outside.update({n for n in (extra_taken_names or []) if n})

    final_names = []
    taken = set(outside)
    for spec, vg in resolved:
        current_name = vg.name if vg is not None else (spec.get("current", "") or "")
        if vg is None:
            final_name = current_name
        else:
            final_name = allocate_unique_no_merge_name(desired_root, taken, current_name="")
            if not final_name:
                final_name = current_name
        final_names.append(final_name)
        if final_name:
            taken.add(final_name)

    if not family_vgs:
        return final_names

    temp_names = _unique_temp_names(
        {vg.name for vg in obj.vertex_groups} | set(extra_taken_names or []),
        len(family_vgs),
        "__velo_vg_family_tmp_",
    )
    temp_iter = iter(temp_names)
    temp_by_index = {}
    for _spec, vg in resolved:
        if vg is None:
            continue
        temp_name = next(temp_iter)
        temp_by_index[vg.index] = temp_name
        try:
            vg.name = temp_name
        except Exception:
            pass

    for (_spec, vg), final_name in zip(resolved, final_names):
        if vg is None or not final_name:
            continue
        cur = obj.vertex_groups.get(temp_by_index.get(vg.index, "")) or vg
        try:
            cur.name = final_name
        except Exception:
            pass
    return final_names


def rename_bone_family_no_merge(arm_obj, row_specs, final_names):
    """骨骼版两阶段家族改名；final_names 由 VG 家族分配结果直接复用。"""
    if arm_obj is None or arm_obj.type != 'ARMATURE' or not row_specs or not final_names:
        return 0

    resolved = []
    used_names = set()
    for spec in row_specs:
        bone = None
        candidates = []
        for name in [spec.get("current", ""), *(spec.get("fallbacks", []) or [])]:
            name = (name or "").strip()
            if name and name not in candidates:
                candidates.append(name)
        for name in candidates:
            cand = arm_obj.data.bones.get(name)
            if cand is not None and cand.name not in used_names:
                bone = cand
                used_names.add(cand.name)
                break
        resolved.append(bone)

    family_bones = [b for b in resolved if b is not None]
    if not family_bones:
        return 0

    temp_names = _unique_temp_names(
        {b.name for b in arm_obj.data.bones},
        len(family_bones),
        "__velo_bone_family_tmp_",
    )
    temp_iter = iter(temp_names)
    temp_slots = []
    for bone in resolved:
        if bone is None:
            temp_slots.append("")
            continue
        temp_name = next(temp_iter)
        temp_slots.append(temp_name)
        try:
            bone.name = temp_name
        except Exception:
            pass

    renamed = 0
    for bone, temp_name, final_name in zip(resolved, temp_slots, final_names):
        if bone is None or not final_name:
            continue
        cur = arm_obj.data.bones.get(temp_name) or bone
        try:
            cur.name = final_name
            renamed += 1
        except Exception:
            pass
    return renamed


def rename_single_vg_no_merge(obj, current_name: str, desired_root: str, *, fallback_names=(), extra_taken_names=()):
    """只改一个顶点组名，不重放整表；返回 {renamed, old_name, final_name}。"""
    report = {"renamed": 0, "old_name": "", "final_name": ""}
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return report

    vg = None
    candidates = []
    for name in (current_name, *fallback_names):
        name = (name or "").strip()
        if name and name not in candidates:
            candidates.append(name)
    for name in candidates:
        vg = obj.vertex_groups.get(name)
        if vg is not None:
            break
    if vg is None:
        return report

    old_name = vg.name
    taken = {g.name for g in obj.vertex_groups if g.index != vg.index}
    taken.update({n for n in (extra_taken_names or []) if n and n != old_name})
    final_name = allocate_unique_no_merge_name(desired_root, taken, current_name=old_name)
    report["old_name"] = old_name
    report["final_name"] = final_name
    if not final_name or final_name == old_name:
        return report
    try:
        vg.name = final_name
        report["renamed"] = 1
    except Exception:
        pass
    return report


def rename_single_bone_no_merge(arm_obj, current_name: str, desired_final_name: str, *, fallback_names=()):
    """只改一个骨骼名；调用方应保证 desired_final_name 已唯一。"""
    if arm_obj is None or arm_obj.type != 'ARMATURE' or not desired_final_name:
        return 0
    candidates = []
    for name in (current_name, *fallback_names):
        name = (name or "").strip()
        if name and name not in candidates:
            candidates.append(name)
    bone = None
    for name in candidates:
        bone = arm_obj.data.bones.get(name)
        if bone is not None:
            break
    if bone is None or bone.name == desired_final_name:
        return 0
    try:
        bone.name = desired_final_name
        return 1
    except Exception:
        return 0


# ============================================================
# 完整 VG 快照（V0.1.6 lossless roundtrip 修复）
# ============================================================
def _serialize_snapshot(obj) -> str:
    """把 obj 当前所有 VG 的 (顺序 + 每顶点权重) 序列化为 JSON 字符串。"""
    import json as _json
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return ""
    order = [vg.name for vg in obj.vertex_groups]
    weights_by_vg = _collect_all_weights(obj)
    idx_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
    weights = {n: [] for n in order}
    for gi, wmap in weights_by_vg.items():
        name = idx_to_name.get(gi)
        if name is None:
            continue
        weights[name] = [(int(vi), float(w)) for vi, w in wmap.items()]
    return _json.dumps({"order": order, "weights": weights})


def save_vg_snapshot(obj, key: str = _VG_SNAPSHOT_KEY) -> bool:
    """快照保存到 obj[key]。已有快照 → 不覆盖（保留最早的真实快照）。"""
    if obj is None:
        return False
    try:
        if obj.get(key):
            return False  # 已存在快照，不覆盖
        snap = _serialize_snapshot(obj)
        if not snap:
            return False
        obj[key] = snap
        return True
    except Exception:
        return False


def restore_vg_snapshot(obj, key: str = _VG_SNAPSHOT_KEY) -> bool:
    """从 obj[key] 完整还原 VG 名+顺序+权重，并清掉 key。失败返回 False。"""
    import json as _json
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return False
    raw = obj.get(key)
    if not raw:
        return False
    try:
        snap = _json.loads(raw)
    except Exception:
        return False
    order = snap.get("order") or []
    weights = snap.get("weights") or {}
    if not order:
        return False
    # 删除全部现有 VG
    for vg in list(obj.vertex_groups):
        try:
            obj.vertex_groups.remove(vg)
        except Exception:
            pass
    for name in order:
        new_vg = obj.vertex_groups.new(name=name)
        wlist = weights.get(name) or []
        if not wlist:
            continue
        # 按权重值分桶批量写入
        buckets = defaultdict(list)
        for vi, w in wlist:
            ww = float(w)
            if ww > 1.0:
                ww = 1.0
            if ww <= 0.0:
                continue
            buckets[ww].append(int(vi))
        for ww, vis in buckets.items():
            try:
                new_vg.add(vis, ww, 'REPLACE')
            except Exception:
                pass
    try:
        del obj[key]
    except Exception:
        pass
    return True


def reorder_numeric_vertex_groups_first(obj):
    """把所有数字族顶点组按数值排到最前；其他 VG 保持当前顺序追加在后。"""
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return
    current = [vg.name for vg in obj.vertex_groups]
    desired = [n for n in current if _NUMERIC_FAMILY_RE.match(n or "")]
    desired.sort(key=_numeric_family_sort_key)
    if desired:
        reorder_vertex_groups_by_order(obj, desired)


def reapply_no_merge_with_suffix_from_snapshot(obj, ordered_pairs, *, save_backup: bool = False) -> dict:
    """若对象已存在原始快照，则先临时还原到原始状态，再按当前映射重放一次不合并改名。
    还原时会保留最早的快照，确保后续仍可无损恢复原名/原顺序。"""
    raw_snapshot = None
    try:
        raw_snapshot = obj.get(_VG_SNAPSHOT_KEY) if obj is not None else None
    except Exception:
        raw_snapshot = None

    if raw_snapshot:
        restore_vg_snapshot(obj)
        try:
            obj[_VG_SNAPSHOT_KEY] = raw_snapshot
        except Exception:
            pass
        return rename_no_merge_with_suffix(obj, ordered_pairs, save_backup=False)
    return rename_no_merge_with_suffix(obj, ordered_pairs, save_backup=save_backup)


def reapply_armature_bones_with_suffix_from_snapshot(arm_obj, ordered_pairs, *, save_backup: bool = False) -> int:
    """骨骼版的 snapshot-aware 重放。"""
    raw_snapshot = None
    try:
        raw_snapshot = arm_obj.get(_BONE_SNAPSHOT_KEY) if arm_obj is not None else None
    except Exception:
        raw_snapshot = None

    if raw_snapshot:
        restore_armature_bone_snapshot(arm_obj)
        try:
            arm_obj[_BONE_SNAPSHOT_KEY] = raw_snapshot
        except Exception:
            pass
        return rename_armature_bones_with_suffix(arm_obj, ordered_pairs)

    if save_backup:
        save_armature_bone_snapshot(arm_obj)
    return rename_armature_bones_with_suffix(arm_obj, ordered_pairs)


# ============================================================
# 骨骼名同步改名（V0.1.6 任务 2）
# ============================================================
def rename_armature_bones(arm_obj, name_map: dict) -> int:
    """按 name_map 对骨架物体的骨骼改名，返回成功改名数。
    要求 arm_obj 为 ARMATURE；自动避开同名冲突（已存在则跳过）。"""
    if arm_obj is None or arm_obj.type != 'ARMATURE' or not name_map:
        return 0
    arm = arm_obj.data
    existing = {b.name for b in arm.bones}
    renamed = 0
    for old, new in list(name_map.items()):
        if not old or not new or old == new:
            continue
        b = arm.bones.get(old)
        if b is None:
            continue
        if new in existing and new != old:
            continue
        try:
            b.name = new
        except Exception:
            continue
        existing.discard(old)
        existing.add(new)
        renamed += 1
    return renamed


# ============================================================
# 骨架快照（V0.1.6 任务 2 修订：lossless 还原骨骼名）
# ============================================================
def save_armature_bone_snapshot(arm_obj, key: str = _BONE_SNAPSHOT_KEY) -> bool:
    import json as _json
    if arm_obj is None or arm_obj.type != 'ARMATURE':
        return False
    try:
        if arm_obj.get(key):
            return False
        names = [b.name for b in arm_obj.data.bones]
        arm_obj[key] = _json.dumps({"names": names})
        return True
    except Exception:
        return False


def restore_armature_bone_snapshot(arm_obj, key: str = _BONE_SNAPSHOT_KEY) -> bool:
    """按快照顺序还原骨骼名。两阶段（先全部改成临时名，再回写）防冲突。"""
    import json as _json
    if arm_obj is None or arm_obj.type != 'ARMATURE':
        return False
    raw = arm_obj.get(key)
    if not raw:
        return False
    try:
        snap = _json.loads(raw)
    except Exception:
        return False
    target_names = snap.get("names") or []
    bones = list(arm_obj.data.bones)
    if not target_names or len(target_names) != len(bones):
        # 数量不一致也尝试按当前顺序映射前 N 个
        n = min(len(target_names), len(bones))
        if n == 0:
            return False
        bones = bones[:n]
        target_names = target_names[:n]
    # 阶段 1：临时名
    for i, b in enumerate(bones):
        try:
            b.name = f"__velo_bone_tmp_{i}"
        except Exception:
            pass
    # 阶段 2：回写真名
    for b, name in zip(bones, target_names):
        try:
            b.name = name
        except Exception:
            pass
    try:
        del arm_obj[key]
    except Exception:
        pass
    return True


# ============================================================
# 不合并 + 顺序后缀改名（V0.1.6 修订核心：源 → unified 不再合并）
# ============================================================
def rename_no_merge_with_suffix(obj, ordered_pairs, *, save_backup: bool = True) -> dict:
    """按 ordered_pairs 顺序重命名 VG，不合并；冲突时按出现次序 .001/.002 加后缀。

    Parameters:
        obj: MESH 物体
        ordered_pairs: List[Tuple[old_name, unified_root]]，按映射表顺序提供
        save_backup: 是否保存完整 VG 快照供还原

    Returns dict: {"renamed": int, "missing": [old], "final_names": [str]}
    流程：
      1) （可选）保存完整 VG 快照
      2) 计算每个 (old, root) 对应的最终名（root 第 N 次出现 → root.NNN, N>=1）
      3) 阶段 1：把命中的 VG 改成临时名 "__velo_vg_tmp_<i>"
      4) 阶段 2：临时名 → 最终名
      5) 删除并按 (final_names + 未触碰原顺序) 重建（保留权重，不合并）
    """
    report = {"renamed": 0, "missing": [], "final_names": []}
    if obj is None or obj.type != 'MESH' or obj.data is None:
        return report

    if save_backup:
        save_vg_snapshot(obj)

    # 收集 vg 名 → 索引
    name_to_idx = {vg.name: vg.index for vg in obj.vertex_groups}
    counts = defaultdict(int)
    plan = []  # [(old, final)]
    for old, root in ordered_pairs:
        if not old or not root:
            continue
        idx = name_to_idx.get(old)
        if idx is None:
            # 尝试 strip 后缀再找
            idx = name_to_idx.get(strip_dup_suffix(old))
            if idx is None:
                report["missing"].append(old)
                continue
        c = counts[root]
        final = root if c == 0 else f"{root}.{c:03d}"
        counts[root] += 1
        plan.append((old, final))

    if not plan:
        return report

    # 单遍收集所有 VG 权重 + 原顺序
    weights_by_vg = _collect_all_weights(obj)
    idx_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
    weights_by_name = {idx_to_name[i]: w for i, w in weights_by_vg.items() if i in idx_to_name}
    original_order = [vg.name for vg in obj.vertex_groups]

    # 计算 final 顺序
    renamed_set_old = {old for old, _ in plan}
    untouched = [n for n in original_order if n not in renamed_set_old]
    final_order = [final for _, final in plan] + untouched

    # 重命名权重表 key
    new_weights_by_name = {}
    for old, final in plan:
        new_weights_by_name[final] = weights_by_name.get(old, {})
    for n in untouched:
        new_weights_by_name[n] = weights_by_name.get(n, {})

    # 删除全部 VG 并按 final_order 重建
    for vg in list(obj.vertex_groups):
        try:
            obj.vertex_groups.remove(vg)
        except Exception:
            pass
    for name in final_order:
        new_vg = obj.vertex_groups.new(name=name)
        wmap = new_weights_by_name.get(name) or {}
        if not wmap:
            continue
        buckets = defaultdict(list)
        for vi, w in wmap.items():
            ww = w if w <= 1.0 else 1.0
            if ww <= 0.0:
                continue
            buckets[ww].append(vi)
        for ww, vis in buckets.items():
            try:
                new_vg.add(vis, ww, 'REPLACE')
            except Exception:
                pass

    report["renamed"] = len(plan)
    report["final_names"] = [f for _, f in plan]
    return report


def rename_armature_bones_with_suffix(arm_obj, ordered_pairs) -> int:
    """与 rename_no_merge_with_suffix 配套：骨骼按相同次序加 .NNN 后缀。
    两阶段防冲突。"""
    if arm_obj is None or arm_obj.type != 'ARMATURE' or not ordered_pairs:
        return 0
    arm = arm_obj.data
    name_to_bone = {b.name: b for b in arm.bones}
    counts = defaultdict(int)
    plan = []  # [(bone, final)]
    for old, root in ordered_pairs:
        if not old or not root:
            continue
        b = name_to_bone.get(old)
        if b is None:
            continue
        c = counts[root]
        final = root if c == 0 else f"{root}.{c:03d}"
        counts[root] += 1
        plan.append((b, final))
    if not plan:
        return 0
    # 阶段 1：临时名
    for i, (b, _f) in enumerate(plan):
        try:
            b.name = f"__velo_bone_tmp_{i}"
        except Exception:
            pass
    # 阶段 2：最终名
    n = 0
    for b, final in plan:
        try:
            b.name = final
            n += 1
        except Exception:
            pass
    return n


def reorder_vertex_groups_by_order(obj, desired_order):
    """把 obj 的 VG 排成 desired_order 列出的顺序；不在 desired_order 中的保留原顺序追加在后。
    通过删除 + 重建实现（保留权重）。"""
    if obj is None or obj.type != 'MESH' or not desired_order:
        return
    weights_by_vg = _collect_all_weights(obj)
    idx_to_name = {vg.index: vg.name for vg in obj.vertex_groups}
    lock_by_name = {vg.name: bool(getattr(vg, "lock_weight", False)) for vg in obj.vertex_groups}
    active_name = ""
    try:
        if 0 <= obj.vertex_groups.active_index < len(obj.vertex_groups):
            active_name = obj.vertex_groups[obj.vertex_groups.active_index].name
    except Exception:
        active_name = ""
    weights_by_name = {}
    for gi, wmap in weights_by_vg.items():
        nm = idx_to_name.get(gi)
        if nm is not None:
            weights_by_name[nm] = wmap
    current = [vg.name for vg in obj.vertex_groups]
    in_desired = [n for n in desired_order if n in current]
    rest = [n for n in current if n not in set(in_desired)]
    final_order = in_desired + rest
    if final_order == current:
        return
    for vg in list(obj.vertex_groups):
        try:
            obj.vertex_groups.remove(vg)
        except Exception:
            pass
    for name in final_order:
        new_vg = obj.vertex_groups.new(name=name)
        try:
            new_vg.lock_weight = lock_by_name.get(name, False)
        except Exception:
            pass
        wmap = weights_by_name.get(name) or {}
        if not wmap:
            continue
        buckets = defaultdict(list)
        for vi, w in wmap.items():
            ww = w if w <= 1.0 else 1.0
            if ww <= 0.0:
                continue
            buckets[ww].append(vi)
        for ww, vis in buckets.items():
            try:
                new_vg.add(vis, ww, 'REPLACE')
            except Exception:
                pass
    if active_name:
        active_vg = obj.vertex_groups.get(active_name)
        if active_vg is not None:
            try:
                obj.vertex_groups.active_index = active_vg.index
            except Exception:
                pass


def rename_and_reorder(
    obj,
    name_map: dict,
    *,
    sort_renamed_numerically: bool = False,
    untouched_to_end: bool = False,
    save_backup: bool = False,
    restore_backup: bool = False,
) -> dict:
    """单遍重命名 + 合并 + 重排（V0.1.4 性能/排序/还原一体化）。

    Parameters:
        name_map: {old_name: new_name}（命中 strip(.NNN) 后名也算）。
        sort_renamed_numerically: True 时改名后的项按数字排序放最前。
        untouched_to_end: True 时未被改名的 VG 保留原顺序放在末尾。
        save_backup: True 时把改名前的 VG 名顺序写到 obj["velo_vg_order_backup"]。
        restore_backup: True 时优先按 obj["velo_vg_order_backup"] 的顺序回写
            （已知名先按 backup 顺序，未知名按当前顺序追加），并清掉 backup。

    返回: {"renamed": int, "merged": int, "skipped": int, "missing": [old_name]}
    """
    report = {"renamed": 0, "merged": 0, "skipped": 0, "missing": []}
    if obj is None or obj.type != 'MESH':
        return report

    name_map = name_map or {}

    # V0.1.6 lossless 还原：优先用完整快照
    if restore_backup:
        if restore_vg_snapshot(obj):
            # 顺手清掉旧的纯顺序备份（如果有）
            try:
                if _VG_ORDER_BACKUP_KEY in obj.keys():
                    del obj[_VG_ORDER_BACKUP_KEY]
            except Exception:
                pass
            report["renamed"] = len(obj.vertex_groups)
            return report

    # V0.1.6 lossless 备份：保存改名前完整快照（仅在 save_backup 时）
    if save_backup:
        save_vg_snapshot(obj)

    # 1) 单遍收集所有 VG 的 (vi -> w)
    weights_by_vg = _collect_all_weights(obj)

    # 2) 当前 VG 顺序快照
    original_order = [vg.name for vg in obj.vertex_groups]
    idx_by_name = {vg.name: vg.index for vg in obj.vertex_groups}

    # 3) 备份原顺序（仅在 save_backup 时）
    if save_backup:
        try:
            obj[_VG_ORDER_BACKUP_KEY] = list(original_order)
        except Exception:
            pass

    # 4) 决定每个 VG 的新名 + 是否被改名
    renamed_pairs = []  # [(old_name, new_name)]
    untouched = []     # [name]（未被 name_map 命中）
    matched_keys = set()
    for old in original_order:
        new = name_map.get(old)
        if new is None:
            new = name_map.get(strip_dup_suffix(old))
        if new is None:
            untouched.append(old)
        else:
            renamed_pairs.append((old, new))
            matched_keys.add(old)

    # missing：name_map 里 key 没出现在对象中
    keys_in_obj = set(original_order) | {strip_dup_suffix(n) for n in original_order}
    for k in name_map.keys():
        if k not in keys_in_obj:
            report["missing"].append(k)

    # 5) 合并：new_name -> 累加权重；记录每个 new_name 来自的 source 数
    merged_weights = defaultdict(lambda: defaultdict(float))
    sources_count = defaultdict(int)
    for old, new in renamed_pairs:
        gi = idx_by_name.get(old)
        if gi is None:
            continue
        wmap = weights_by_vg.get(gi, {})
        sources_count[new] += 1
        target = merged_weights[new]
        for vi, w in wmap.items():
            target[vi] += w

    renamed_target_names = {new for _old, new in renamed_pairs}
    colliding_untouched = set()

    # 6) untouched 的权重保留：若原本就存在同名目标组，则一起并入 merged_weights，
    # 避免留下 Blender 自动补的 .001 重名族。
    untouched_weights = {}
    for name in untouched:
        gi = idx_by_name.get(name)
        wmap = {} if gi is None else weights_by_vg.get(gi, {})
        if name in renamed_target_names:
            colliding_untouched.add(name)
            sources_count[name] += 1
            target = merged_weights[name]
            for vi, w in wmap.items():
                target[vi] += w
            continue
        untouched_weights[name] = wmap

    if colliding_untouched:
        untouched = [name for name in untouched if name not in colliding_untouched]

    # 7) 计算最终顺序
    renamed_names_unique = []
    seen = set()
    for _old, new in renamed_pairs:
        if new not in seen:
            renamed_names_unique.append(new)
            seen.add(new)

    if restore_backup:
        # 优先按 backup 顺序还原
        backup = []
        try:
            backup = list(obj.get(_VG_ORDER_BACKUP_KEY) or [])
        except Exception:
            backup = []
        # backup 里出现的名字（在当前对象「改名后名集合」中存在的）按 backup 顺序
        all_after = set(renamed_names_unique) | set(untouched)
        ordered = [n for n in backup if n in all_after]
        # 不在 backup 里的按 (renamed_unique ++ untouched) 顺序追加
        rest = [n for n in renamed_names_unique + untouched if n not in set(ordered)]
        final_order = ordered + rest
        # 用完即清，避免后续无关改名误用
        try:
            if _VG_ORDER_BACKUP_KEY in obj.keys():
                del obj[_VG_ORDER_BACKUP_KEY]
        except Exception:
            pass
    else:
        if sort_renamed_numerically:
            renamed_part = sorted(renamed_names_unique, key=_numeric_sort_key)
        else:
            renamed_part = renamed_names_unique
        if untouched_to_end:
            final_order = renamed_part + untouched
        else:
            # 保持原顺序（renamed 项落在原 old 名出现位置；untouched 原位置）
            slots = []
            seen2 = set()
            renamed_map_old_to_new = {old: new for old, new in renamed_pairs}
            for old in original_order:
                if old in renamed_map_old_to_new:
                    new = renamed_map_old_to_new[old]
                    if new not in seen2:
                        slots.append(new)
                        seen2.add(new)
                else:
                    if old not in seen2:
                        slots.append(old)
                        seen2.add(old)
            final_order = slots

    # 8) 删除全部原 VG，按 final_order 重建并写权重
    for vg in list(obj.vertex_groups):
        try:
            obj.vertex_groups.remove(vg)
        except Exception:
            pass

    for name in final_order:
        new_vg = obj.vertex_groups.new(name=name)
        if name in untouched_weights:
            wmap = untouched_weights[name]
        else:
            wmap = merged_weights.get(name, {})
        # 同权重批量加（按权重值分桶，减少 .add 调用次数）
        if not wmap:
            continue
        buckets = defaultdict(list)
        for vi, w in wmap.items():
            ww = w if w <= 1.0 else 1.0
            if ww <= 0.0:
                continue
            buckets[ww].append(vi)
        for ww, vis in buckets.items():
            new_vg.add(vis, ww, 'REPLACE')

    # 9) 统计
    for new, count in sources_count.items():
        if count > 1:
            report["merged"] += 1
            report["renamed"] += count
        else:
            report["renamed"] += 1
    report["skipped"] = len(untouched)
    return report


def rename_vertex_groups_with_merge(obj, name_map: dict, *, allow_passthrough: bool = True) -> dict:
    """按 name_map={old_name: new_name} 改名当前对象的顶点组（V0.1.4 起 delegate 到 rename_and_reorder）。

    - 同一 new_name 下多个 old_name -> 权重相加，clamp 到 [0,1]
    - old_name 命中 strip(.NNN) 后的同名也会被处理
    - allow_passthrough=True 时，未在 name_map 里的顶点组保持原样
    """
    if obj is None or obj.type != 'MESH' or not name_map:
        return {"renamed": 0, "merged": 0, "skipped": 0, "missing": []}
    return rename_and_reorder(
        obj, name_map,
        sort_renamed_numerically=False,
        untouched_to_end=False,
        save_backup=False,
        restore_backup=False,
    )


# ============================================================
# 三段映射（导出阶段使用，R4 接入 exporter）
# ============================================================

def build_three_stage_map(profile, component_map) -> dict:
    """合成 {source_name: native_name}。

    source_name 既可能是 MMD 名也可能已经是 unified 名（PLAN §2.2 阶段 D step2）。
    """
    mmd_to_unified = build_mmd_to_unified(profile)
    unified_to_native = build_unified_to_native(component_map)

    out = {}
    # 已是 unified 的源 -> native
    for u, n in unified_to_native.items():
        out[u] = n
    # MMD 源 -> unified -> native
    for m, u in mmd_to_unified.items():
        n = unified_to_native.get(u)
        if n:
            out[m] = n
    return out


def apply_three_stage_rename(obj, profile, component_map) -> dict:
    """导出端调用：把对象顶点组按三段映射改名为 native 名。"""
    name_map = build_three_stage_map(profile, component_map)
    return rename_vertex_groups_with_merge(obj, name_map, allow_passthrough=True)
