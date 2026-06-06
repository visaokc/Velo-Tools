"""形态键聚合扫描与批量操作。

PropertyGroup `VELO_ShapeKeyAggItem` 与 rename 回调定义在 properties.py
(为了与既有属性集中管理), 这里负责: 扫描刷新, 自动监听 (depsgraph), 批量改名。
"""

import re

import bpy

from .. import properties as props_mod
from .operators import is_real_mesh


# ---------------------------------------------------------------------------
# 公共: 扫描 / 签名 / 写入列表
# ---------------------------------------------------------------------------

# 形如 "Deform 12 ", "Deform12 ", "Deform 3", "deform  7 _" 等前缀
_DEFORM_PREFIX_RE = re.compile(r"^\s*[Dd]eform\s*\d+\s*", re.UNICODE)


def _strip_deform_prefix(name: str) -> str:
    if not name:
        return name
    return _DEFORM_PREFIX_RE.sub("", name).lstrip()


def _scan_collection(coll):
    """扫描集合, 返回 (order_list, count_dict, first_value_dict)。
    order_list 按首次遇到的顺序, 已跳过 Basis 与 .placeholder。
    """
    agg = {}
    order = []
    first_value = {}
    if coll is None:
        return order, agg, first_value
    for obj in coll.all_objects:
        if not is_real_mesh(obj):
            continue
        sk = obj.data.shape_keys
        if not sk:
            continue
        for kb in sk.key_blocks[1:]:
            if kb.name not in agg:
                agg[kb.name] = 0
                order.append(kb.name)
                first_value[kb.name] = kb.value
            agg[kb.name] += 1
    return order, agg, first_value


def _signature(order, count):
    """轻量签名: (name, count) 元组。仅在名字增删或 count 改变时触发刷新。"""
    return tuple((n, count[n]) for n in order)


def _populate(settings, order, count, first_value):
    props_mod._suspend_shapekey_update = True
    try:
        settings.shapekey_items.clear()
        for name in order:
            it = settings.shapekey_items.add()
            it.name = name
            it.original_name = name
            it.count = count[name]
            it.value = first_value.get(name, 0.0)
    finally:
        props_mod._suspend_shapekey_update = False


def refresh_shapekey_list(context, force=False):
    """供外部 (operator / 回调 / handler) 调用的统一刷新入口。
    返回 True 表示真的重建了列表。
    """
    s = getattr(context.scene, "velo_tools", None)
    if s is None:
        return False
    coll = s.target_collection
    if coll is None:
        # 集合被清空 -> 也清空列表
        if len(s.shapekey_items):
            props_mod._suspend_shapekey_update = True
            try:
                s.shapekey_items.clear()
            finally:
                props_mod._suspend_shapekey_update = False
            _LAST_SIG[0] = None
        return False

    order, count, first_value = _scan_collection(coll)
    sig = _signature(order, count)
    if not force and sig == _LAST_SIG[0]:
        return False
    _populate(s, order, count, first_value)
    _LAST_SIG[0] = sig
    return True


# 模块级单槽签名缓存 (仅服务于自动刷新; 手动 force=True 总是重建)
_LAST_SIG = [None]


# ---------------------------------------------------------------------------
# Operator: 手动刷新 (保留, 兼容旧 UI)
# ---------------------------------------------------------------------------

class VELO_OT_refresh_shapekey_list(bpy.types.Operator):
    bl_idname = "velo.refresh_shapekey_list"
    bl_label = "读取/刷新形态键"
    bl_description = "扫描目标集合下所有真实网格的形态键 (跳过 Basis), 按名称聚合"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return context.scene.velo_tools.target_collection is not None

    def execute(self, context):
        s = context.scene.velo_tools
        if s.target_collection is None:
            self.report({'WARNING'}, "请先选择目标集合")
            return {'CANCELLED'}
        refresh_shapekey_list(context, force=True)
        self.report({'INFO'}, f"形态键种类: {len(s.shapekey_items)}")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: 自动重命名 -> "Deform N <basename>"
# ---------------------------------------------------------------------------

class VELO_OT_rename_shapekeys_deform(bpy.types.Operator):
    bl_idname = "velo.rename_shapekeys_deform"
    bl_label = "自动重命名 (Deform N)"
    bl_description = (
        "按当前列表顺序为集合里所有形态键重命名为 'Deform N <原名>'; "
        "已有 'DeformXX'/'Deform XX ' 前缀会被覆盖"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        s = context.scene.velo_tools
        return s.target_collection is not None and len(s.shapekey_items) > 0

    def execute(self, context):
        s = context.scene.velo_tools
        coll = s.target_collection
        if coll is None or len(s.shapekey_items) == 0:
            self.report({'WARNING'}, "请先选择集合并刷新形态键")
            return {'CANCELLED'}

        # 当前列表顺序 = 首次遇到顺序; 这里以此顺序编号
        items = list(s.shapekey_items)
        plan = []  # [(old_name, final_name), ...]
        used_finals = set()
        for idx, it in enumerate(items, start=1):
            old = it.name
            if not old:
                continue
            base = _strip_deform_prefix(old)
            if not base:
                base = old  # 整个名字就是 Deform 前缀的极端情况, 退回原名
            final = f"Deform {idx} {base}"
            # 防止极端: 同 base 出现两次又同序号 -> 已不可能 (idx 唯一)
            used_finals.add(final)
            if final != old:
                plan.append((old, final))

        if not plan:
            self.report({'INFO'}, "已经全部符合 Deform 命名规则")
            return {'CANCELLED'}

        meshes = [o for o in coll.all_objects if is_real_mesh(o) and o.data.shape_keys]

        # 两遍重命名, 避开中途名字冲突 (新名可能等于另一个现存形态键的名字)
        # Pass 1: old -> __velo_tmp_<i>__
        tmp_names = []  # [(mesh -> kb_ref, tmp_name, final_name)]
        # 我们用 mesh+old_name 定位, 改成 tmp 后记录 tmp -> final
        rename_map = {}  # mesh_id -> list of (tmp, final)
        for i, (old, final) in enumerate(plan):
            tmp = f"__velo_tmp_sk_{i}__"
            for o in meshes:
                kb = o.data.shape_keys.key_blocks.get(old)
                if kb is None:
                    continue
                kb.name = tmp
                rename_map.setdefault(id(o), (o, []))[1].append((tmp, final))

        # Pass 2: tmp -> final
        renamed = 0
        for _o_id, (o, pairs) in rename_map.items():
            sk = o.data.shape_keys
            if not sk:
                continue
            for tmp, final in pairs:
                kb = sk.key_blocks.get(tmp)
                if kb is None:
                    continue
                kb.name = final
                renamed += 1

        # 强制刷新列表 (顺序按新名字首次出现, 即 Deform 1, 2, ...)
        refresh_shapekey_list(context, force=True)

        self.report({'INFO'}, f"自动重命名完成: 处理 {len(plan)} 种, 共改 {renamed} 个形态键")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# depsgraph handler: 自动监听形态键增删/原生面板改名
# ---------------------------------------------------------------------------

_LAST_COLL_NAME = [None]


def _depsgraph_handler(scene, _depsgraph):
    """轻量自动刷新:
    - 仅当 target_collection 存在时工作
    - 通过 (name, count) 签名比对, 无变化不重建
    - 涵盖: 原生面板新增/删除/改名形态键, 网格被加入/移出集合
    """
    s = getattr(scene, "velo_tools", None)
    if s is None:
        return
    coll = s.target_collection
    if coll is None:
        if _LAST_COLL_NAME[0] is not None:
            _LAST_COLL_NAME[0] = None
            _LAST_SIG[0] = None
        return

    # 集合切换 -> 强制重建
    if coll.name != _LAST_COLL_NAME[0]:
        _LAST_COLL_NAME[0] = coll.name
        order, count, first_value = _scan_collection(coll)
        _populate(s, order, count, first_value)
        _LAST_SIG[0] = _signature(order, count)
        return

    # 同集合 -> 签名比对
    order, count, first_value = _scan_collection(coll)
    sig = _signature(order, count)
    if sig == _LAST_SIG[0]:
        return
    _populate(s, order, count, first_value)
    _LAST_SIG[0] = sig


_classes = (
    VELO_OT_refresh_shapekey_list,
    VELO_OT_rename_shapekeys_deform,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    if _depsgraph_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph_handler)


def unregister():
    if _depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
        try:
            bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_handler)
        except ValueError:
            pass
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass

