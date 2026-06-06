"""arknights_endfield - MMD mapping props (V0.1.3)"""
import bpy
from bpy.props import (
    StringProperty, BoolProperty, CollectionProperty, IntProperty, PointerProperty,
    EnumProperty, FloatVectorProperty,
)


_suspend_mmd_row_update = False


def _is_mesh_poll(self, obj):
    return obj is not None and obj.type == 'MESH' and _is_mesh_in_active_export_scope(self, obj)


def _collection_is_descendant_or_same(root, collection):
    if root is None or collection is None:
        return False
    if root == collection:
        return True
    try:
        return collection in getattr(root, "children_recursive", ())
    except Exception:
        return False


def _active_export_collection(settings):
    # 按 velo_tools.active_game 经游戏注册表取当前游戏的部件根集合（终末地→VTEF_settings，鸣潮→VTWW_settings）
    scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return None
    try:
        from .. import registry as _registry
        desc = _registry.get_active_descriptor(scene)
    except Exception:
        desc = None
    if desc is not None:
        return desc.component_collection(scene)
    cfg = getattr(scene, "VTEF_settings", None)
    return getattr(cfg, "component_collection", None) if cfg is not None else None


def _is_mesh_in_active_export_scope(settings, obj):
    root = _active_export_collection(settings)
    if root is None or obj is None or obj.type != 'MESH':
        return False
    return any(
        _collection_is_descendant_or_same(root, collection)
        for collection in getattr(obj, "users_collection", ())
    )


def _on_mmd_overlay_update(self, context):
    if not getattr(self, "show_overlay", False):
        try:
            from . import mmd_pick as _mp
            _mp.reset_state(context)
        except Exception:
            pass
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return
    gm = getattr(context.scene, "velo_general_mapping", None)
    if gm is not None and getattr(gm, "show_overlay", False):
        try:
            gm.show_overlay = False
        except Exception:
            pass
    # V0.1.6: 启动端点拾取/拖拽 modal
    try:
        from . import mmd_pick as _mp
        _mp.start_modal_if_needed()
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _refresh_available_src_vgs(settings):
    """重建 settings.available_src_vgs（按 mmd_source_object 的可用顶点组排序）。

    给 prop_search 用：键入即过滤；与 EnumProperty items callback 不同，
    它以 PropertyGroup 的 name 字段为搜索域，名称由 Python 端持有强引用，不会乱码。
    """
    coll = settings.available_src_vgs
    coll.clear()
    obj = settings.mmd_source_object
    names = set()
    if obj is None or obj.type != 'MESH':
        profile = getattr(settings, "mmd_profile", None)
        if profile is None:
            return
    else:
        try:
            from ...core.mapping.filters import usable_vertex_groups
            names.update(n for _i, n in usable_vertex_groups(obj))
        except Exception:
            names.update(vg.name for vg in obj.vertex_groups)

    profile = getattr(settings, "mmd_profile", None)
    if profile is not None:
        for row in profile.rows:
            mmd_name = (getattr(row, "mmd_name", "") or "").strip()
            current_name = (getattr(row, "current_source_name", "") or "").strip()
            if mmd_name:
                names.add(mmd_name)
            if current_name:
                names.add(current_name)

    for n in sorted(names):
        item = coll.add()
        item.name = n


def _on_active_mmd_text_update(self, context):
    """切换 MMD 映射表 Text 时：将 Text 内容载入 profile.rows。"""
    global _suspend_mmd_row_update
    prev = _suspend_mmd_row_update
    tb = self.active_mmd_text
    src = self.mmd_source_object
    if src is not None:
        try:
            src["velo_mmd_text"] = (tb.name if tb is not None else "")
        except Exception:
            pass
    if tb is None or self.mmd_profile is None:
        return
    try:
        from ...core.mapping import text_io as _tio
        from ...core.mapping import operators as _ops
        _suspend_mmd_row_update = True
        parsed = _tio.parse_mapping_text(tb.as_string())
        _tio.load_into_settings(self, parsed)
        _ops.apply_mmd_text_meta(self, tb)
        _ops._write_mmd_text_meta(tb, self)
        if _ops._mmd_source_is_unified(self):
            _ops._sync_mmd_source_from_table(self, save_backup=False)
    except Exception:
        pass
    finally:
        _suspend_mmd_row_update = prev


def _on_mmd_row_unified_update(self, context):
    global _suspend_mmd_row_update
    if _suspend_mmd_row_update:
        return
    settings = getattr(context.scene, "velo_endfield", None)
    if settings is None:
        return
    try:
        from ...core.mapping import operators as _ops
        if _ops._mmd_source_is_unified(settings):
            final_name, _bone_n = _ops._sync_mmd_source_row_incremental(settings, self, self.unified_name)
            if final_name and getattr(self, "current_source_name", "") != final_name:
                prev = _suspend_mmd_row_update
                _suspend_mmd_row_update = True
                try:
                    self.current_source_name = final_name
                finally:
                    _suspend_mmd_row_update = prev
        if not (self.unified_name or "").strip():
            self.has_unified_centroid = False
        _ops._capture_mmd_row_snapshot(settings, self)
        _ops._autosync_to_text(settings)
    except Exception:
        pass


def _on_mmd_source_update(self, context):
    """切源物体时刷新自动补全候选 + 让 overlay 缓存失效；同时按对象绑定切换映射表 Text。"""
    try:
        _refresh_available_src_vgs(self)
    except Exception:
        pass
    src = self.mmd_source_object
    if src is not None:
        try:
            bound = src.get("velo_mmd_text", "")
            if bound:
                tb = bpy.data.texts.get(bound)
                if tb is not None and self.active_mmd_text != tb:
                    self.active_mmd_text = tb
        except Exception:
            pass
    try:
        from ... import overlay as _ov
        if hasattr(_ov, "invalidate_mmd_cache"):
            _ov.invalidate_mmd_cache()
    except Exception:
        pass
    try:
        from . import mmd_pick as _mp
        _mp.reset_state(context)
        if getattr(self, "show_overlay", False):
            _mp.start_modal_if_needed()
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _on_mmd_target_update(self, context):
    try:
        from ... import overlay as _ov
        if hasattr(_ov, "invalidate_mmd_cache"):
            _ov.invalidate_mmd_cache()
    except Exception:
        pass
    try:
        from . import mmd_pick as _mp
        _mp.reset_state(context)
        if getattr(self, "show_overlay", False):
            _mp.start_modal_if_needed()
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


class VELO_EF_VGName(bpy.types.PropertyGroup):
    """单纯持有一个 name 字段，给 prop_search 当候选项。"""
    pass  # 自动有 name: StringProperty


class VELO_EF_MMDRow(bpy.types.PropertyGroup):
    mmd_name: StringProperty(name="MMD", default="")
    unified_name: StringProperty(name="Unified", default="", update=_on_mmd_row_unified_update)
    current_source_name: StringProperty(name="CurrentSource", default="")
    enabled: BoolProperty(name="Enabled", default=True)
    # V0.1.6 修复：overlay 快照重心（局部坐标）。
    # 仅记录「匹配下发生时」的 VG 重心，overlay 只读这里，
    # 不再跟随顶点组 / 骨骼的实际名变动。
    mmd_centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    unified_centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    has_mmd_centroid: BoolProperty(default=False)
    has_unified_centroid: BoolProperty(default=False)


class VELO_EF_MMDProfile(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", default="default")
    rows: CollectionProperty(type=VELO_EF_MMDRow)
    active_row_index: IntProperty(name="Row", default=0)


class VELO_EF_Settings(bpy.types.PropertyGroup):
    mmd_profile: PointerProperty(type=VELO_EF_MMDProfile)
    # 任务4：当前映射表 Text（template_ID 选择器）
    active_mmd_text: PointerProperty(
        name="映射表",
        type=bpy.types.Text,
        description="当前使用的 MMD 映射表内置文本",
        update=_on_active_mmd_text_update,
    )
    mmd_source_object: PointerProperty(
        name="MMD 源物体",
        type=bpy.types.Object,
        poll=_is_mesh_poll,
        description="MMD 模型网格（profile 行的 mmd_name 来自它）",
        update=lambda self, ctx: _on_mmd_source_update(self, ctx),
    )
    mmd_target_object: PointerProperty(
        name="目标物体",
        type=bpy.types.Object,
        poll=_is_mesh_poll,
        description="终末地 Component 网格（profile 行的 unified_name 来自它）",
        update=lambda self, ctx: _on_mmd_target_update(self, ctx),
    )
    mmd_armature_object: PointerProperty(
        name="MMD 骨架",
        type=bpy.types.Object,
        poll=lambda self, obj: obj is not None and obj.type == 'ARMATURE',
        description="可选；选择后『改名为统一编号 / 还原为 MMD 名字』将同步更新骨骼名",
    )
    show_overlay: BoolProperty(
        name="启用 MMD 映射可视化",
        default=False,
        description="开启或关闭 MMD 映射可视化校对；开启时会自动关闭通用映射可视化以避免两套端点叠加",
        update=lambda self, context: _on_mmd_overlay_update(self, context),
    )
    # prop_search 候选集合（运行时重建，不参与持久化）
    available_src_vgs: CollectionProperty(type=VELO_EF_VGName)
    # 1.0.8: 旧的 export_adapter 下拉已移除，导出目标统一由 velo_tools.active_game 决定


_classes = (VELO_EF_VGName, VELO_EF_MMDRow, VELO_EF_MMDProfile, VELO_EF_Settings)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.velo_endfield = PointerProperty(type=VELO_EF_Settings)


def unregister():
    if hasattr(bpy.types.Scene, 'velo_endfield'):
        try:
            del bpy.types.Scene.velo_endfield
        except Exception:
            pass
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass