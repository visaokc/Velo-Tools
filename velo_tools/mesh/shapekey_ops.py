"""Shape key aggregation scanning and batch operations.

PropertyGroup `VELO_ShapeKeyAggItem` and the rename callback are defined in
properties.py (to centralize management with existing properties); this module
handles: scan/refresh, auto-listening (depsgraph), batch renaming.
"""

import re

import bpy

from .. import properties as props_mod
from .operators import is_real_mesh


# ---------------------------------------------------------------------------
# Shared: scan / signature / write list
# ---------------------------------------------------------------------------

# Prefixes like "Deform 12 ", "Deform12 ", "Deform 3", "deform  7 _", etc.
_DEFORM_PREFIX_RE = re.compile(r"^\s*[Dd]eform\s*\d+\s*", re.UNICODE)


def _strip_deform_prefix(name: str) -> str:
    if not name:
        return name
    return _DEFORM_PREFIX_RE.sub("", name).lstrip()


def _scan_collection(coll):
    """Scan the collection, returning (order_list, count_dict, first_value_dict).
    order_list follows first-encountered order, having skipped Basis and .placeholder.
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
    """Lightweight signature: tuple of (name, count). Triggers a refresh only when names are added/removed or count changes."""
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
    """Unified refresh entry point called by external code (operator / callback / handler).
    Returns True if the list was actually rebuilt.
    """
    s = getattr(context.scene, "velo_tools", None)
    if s is None:
        return False
    coll = s.target_collection
    if coll is None:
        # Collection was cleared -> clear the list too
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


# Module-level single-slot signature cache (serves auto-refresh only; manual force=True always rebuilds)
_LAST_SIG = [None]


# ---------------------------------------------------------------------------
# Operator: manual refresh (kept for backward compatibility with old UI)
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
# Operator: auto-rename -> "Deform N <basename>"
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

        # Current list order = first-encountered order; numbering follows this order
        items = list(s.shapekey_items)
        plan = []  # [(old_name, final_name), ...]
        used_finals = set()
        for idx, it in enumerate(items, start=1):
            old = it.name
            if not old:
                continue
            base = _strip_deform_prefix(old)
            if not base:
                base = old  # Edge case where the whole name is a Deform prefix; fall back to original name
            final = f"Deform {idx} {base}"
            # Guard against the edge case: same base appearing twice with the same index -> impossible (idx is unique)
            used_finals.add(final)
            if final != old:
                plan.append((old, final))

        if not plan:
            self.report({'INFO'}, "已经全部符合 Deform 命名规则")
            return {'CANCELLED'}

        meshes = [o for o in coll.all_objects if is_real_mesh(o) and o.data.shape_keys]

        # Two-pass rename to avoid mid-way name collisions (a new name may equal another existing shape key's name)
        # Pass 1: old -> __velo_tmp_<i>__
        tmp_names = []  # [(mesh -> kb_ref, tmp_name, final_name)]
        # We locate by mesh+old_name, then after renaming to tmp record tmp -> final
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

        # Force-refresh the list (order follows first appearance of new names, i.e. Deform 1, 2, ...)
        refresh_shapekey_list(context, force=True)

        self.report({'INFO'}, f"自动重命名完成: 处理 {len(plan)} 种, 共改 {renamed} 个形态键")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# depsgraph handler: auto-listen for shape key add/remove / native-panel renames
# ---------------------------------------------------------------------------

_LAST_COLL_NAME = [None]


def _depsgraph_handler(scene, _depsgraph):
    """Lightweight auto-refresh:
    - Works only when target_collection exists
    - Compares (name, count) signatures; no rebuild if nothing changed
    - Covers: native-panel add/delete/rename of shape keys, meshes added to/removed from the collection
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

    # Collection switched -> force rebuild
    if coll.name != _LAST_COLL_NAME[0]:
        _LAST_COLL_NAME[0] = coll.name
        order, count, first_value = _scan_collection(coll)
        _populate(s, order, count, first_value)
        _LAST_SIG[0] = _signature(order, count)
        return

    # Same collection -> compare signatures
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

