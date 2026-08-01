"""Shape key aggregation scanning and batch operations.

PropertyGroup `VELO_ShapeKeyAggItem` and the rename callback are defined in
properties.py (to centralize management with existing properties); this module
handles: scan/refresh, auto-listening (depsgraph), batch renaming.
"""

import json
from pathlib import Path

import bpy
from bpy.props import IntProperty, StringProperty

from .. import properties as props_mod
from .operators import is_real_mesh
from .shapekey_model import (
    aggregation_signature,
    build_rename_plan,
    deform_number,
    deform_number_is_locked,
    natural_key,
    remaining_deform_repair_boundary,
    remap_ui_state,
    sorted_shapekey_names,
    unique_temp_names,
    wwmi_native_deform_numbers,
)


# ---------------------------------------------------------------------------
# Shared: scan / signature / write list
# ---------------------------------------------------------------------------

def _scan_collection(coll):
    """Return deterministic names, counts, values, and contributing object names."""
    contributors = {}
    first_value = {}
    if coll is None:
        return [], {}, first_value, contributors
    objects = sorted(coll.all_objects, key=lambda obj: natural_key(obj.name))
    for obj in objects:
        if not is_real_mesh(obj):
            continue
        sk = obj.data.shape_keys
        if not sk:
            continue
        for kb in sk.key_blocks[1:]:
            if kb.name not in contributors:
                contributors[kb.name] = []
                first_value[kb.name] = kb.value
            contributors[kb.name].append(obj.name)
    order = sorted_shapekey_names(contributors)
    count = {name: len(contributors[name]) for name in order}
    return order, count, first_value, contributors


def _signature(order, contributors):
    """Include contributor identity so same-count membership changes refresh tooltips."""
    return aggregation_signature(order, contributors)


def update_shapekey_number_locks(settings):
    """Apply the active manual-repair boundary to every aggregate row."""
    unlock_from = int(getattr(settings, "shapekey_rename_unlock_from", -1))
    for item in settings.shapekey_items:
        name = item.original_name or item.name
        item.is_deform_numbered = deform_number_is_locked(
            deform_number(name),
            unlock_from,
        )


def _populate(settings, order, count, first_value, contributors, name_remap=None):
    name_remap = name_remap or {}
    selected_by_name = {
        (item.original_name or item.name): bool(item.selected)
        for item in settings.shapekey_items
    }
    order_hint_by_name = {
        (item.original_name or item.name): int(item.deform_rename_order)
        for item in settings.shapekey_items
    }
    order_hint_by_name = {
        name_remap.get(name, name): hint
        for name, hint in order_hint_by_name.items()
    }
    active_name = None
    if 0 <= settings.active_shapekey_index < len(settings.shapekey_items):
        active_item = settings.shapekey_items[settings.active_shapekey_index]
        old_active = active_item.original_name or active_item.name
        active_name = old_active
    selected, active_name = remap_ui_state(selected_by_name, active_name, name_remap)
    props_mod._suspend_shapekey_update = True
    try:
        settings.shapekey_items.clear()
        for name in order:
            it = settings.shapekey_items.add()
            it.name = name
            it.original_name = name
            it.selected = selected.get(name, False)
            it.count = count[name]
            it.contributor_names = "\n".join(contributors[name])
            number = deform_number(name)
            it.deform_rename_order = order_hint_by_name.get(
                name,
                number if number is not None else -1,
            )
            it.value = first_value.get(name, 0.0)
        update_shapekey_number_locks(settings)
        if active_name in order:
            settings.active_shapekey_index = order.index(active_name)
        elif order:
            settings.active_shapekey_index = min(settings.active_shapekey_index, len(order) - 1)
        else:
            settings.active_shapekey_index = 0
    finally:
        props_mod._suspend_shapekey_update = False


def refresh_shapekey_list(context, force=False, name_remap=None):
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

    order, count, first_value, contributors = _scan_collection(coll)
    sig = _signature(order, contributors)
    if not force and sig == _LAST_SIG[0]:
        return False
    _populate(s, order, count, first_value, contributors, name_remap=name_remap)
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


class VELO_OT_toggle_shapekey_rename_selection(bpy.types.Operator):
    bl_idname = "velo.toggle_shapekey_rename_selection"
    bl_label = "全选/全不选可重命名形态键"
    bl_description = "仅切换尚无 Deform 编号的条目"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        s = context.scene.velo_tools
        return any(not item.is_deform_numbered for item in s.shapekey_items)

    def execute(self, context):
        eligible = [
            item for item in context.scene.velo_tools.shapekey_items
            if not item.is_deform_numbered
        ]
        target = not all(item.selected for item in eligible)
        for item in eligible:
            item.selected = target
        return {'FINISHED'}


class VELO_OT_shapekey_contributors_tooltip(bpy.types.Operator):
    bl_idname = "velo.shapekey_contributors_tooltip"
    bl_label = "形态键所在对象"
    bl_options = {'INTERNAL'}

    count: IntProperty(options={'HIDDEN'})
    object_names: StringProperty(options={'HIDDEN'})

    @classmethod
    def description(cls, _context, properties):
        names = properties.object_names or "（无）"
        return f"该形态键出现在 {properties.count} 个网格对象中：\n{names}"

    def execute(self, _context):
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Operator: auto-rename -> "Deform N <basename>"
# ---------------------------------------------------------------------------

def _reserved_deform_numbers(context):
    scene = context.scene
    host = getattr(scene, "velo_tools", None)
    if getattr(host, "active_game", "ENDFIELD") != "WUTHERING":
        return set()

    cfg = getattr(scene, "VTWW_settings", None)
    source = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
    source = bpy.path.abspath(source).strip() if source else ""
    if not source:
        raise ValueError("鸣潮模式自动编号前，请先在游戏区选择对象源文件夹")

    metadata_path = Path(source) / "Metadata.json"
    if not metadata_path.is_file():
        raise ValueError(f"对象源文件夹中缺少 Metadata.json：{metadata_path}")
    try:
        with metadata_path.open(encoding="utf-8") as stream:
            metadata = json.load(stream)
        return wwmi_native_deform_numbers(metadata)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 WWMI 原生 ShapeKey 编号：{exc}") from exc

class VELO_OT_rename_shapekeys_deform(bpy.types.Operator):
    bl_idname = "velo.rename_shapekeys_deform"
    bl_label = "自动重命名 (Deform N)"
    bl_description = (
        "为已勾选且可重命名的形态键分配最小可用编号; 鸣潮模式会从"
        "对象源 Metadata.json 保留原生编号，手工高编号不会抬高后续编号"
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

        order, _count, _first_value, _contributors = _scan_collection(coll)
        selected_names = {
            item.original_name or item.name
            for item in s.shapekey_items
            if item.selected
        }
        unlock_from = int(s.shapekey_rename_unlock_from)
        order_hints = {
            item.original_name or item.name: int(item.deform_rename_order)
            for item in s.shapekey_items
        }
        try:
            reserved_numbers = _reserved_deform_numbers(context)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        plan = build_rename_plan(
            order,
            selected_names,
            unlock_from=unlock_from,
            order_hints=order_hints,
            reserved_numbers=reserved_numbers,
        )
        skipped = len(selected_names) - len(plan)

        if not plan:
            self.report({'INFO'}, f"没有可重命名的已勾选条目（已选择 {len(selected_names)}，跳过 {skipped}）")
            return {'CANCELLED'}

        meshes = [o for o in coll.all_objects if is_real_mesh(o) and o.data.shape_keys]

        occupied_names = {
            kb.name
            for obj in meshes
            for kb in obj.data.shape_keys.key_blocks
        }
        temp_names = unique_temp_names(occupied_names, len(plan))
        rename_map = {}
        for (old, final), tmp in zip(plan, temp_names):
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

        final_by_old = dict(plan)
        if unlock_from >= 0:
            repaired_order, _count, _first_value, _contributors = _scan_collection(coll)
            next_boundary = remaining_deform_repair_boundary(
                repaired_order,
                unlock_from,
                s.shapekey_rename_unlock_end,
            )
            s.shapekey_rename_unlock_from = next_boundary
            if next_boundary < 0:
                s.shapekey_rename_unlock_end = -1
        refresh_shapekey_list(context, force=True, name_remap=final_by_old)

        self.report(
            {'INFO'},
            f"自动重命名完成：选择 {len(selected_names)} 种，修改 {len(plan)} 种/{renamed} 个形态键，跳过 {skipped} 种",
        )
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
        order, count, first_value, contributors = _scan_collection(coll)
        _populate(s, order, count, first_value, contributors)
        _LAST_SIG[0] = _signature(order, contributors)
        return

    # Same collection -> compare signatures
    order, count, first_value, contributors = _scan_collection(coll)
    sig = _signature(order, contributors)
    if sig == _LAST_SIG[0]:
        return
    _populate(s, order, count, first_value, contributors)
    _LAST_SIG[0] = sig


_classes = (
    VELO_OT_refresh_shapekey_list,
    VELO_OT_toggle_shapekey_rename_selection,
    VELO_OT_shapekey_contributors_tooltip,
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

