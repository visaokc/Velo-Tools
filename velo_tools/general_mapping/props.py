from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)


_suspend_general_row_update = False


def _is_mesh_poll(self, obj):
    return obj is not None and obj.type == 'MESH'


def _settings_from_context(context):
    return getattr(context.scene, "velo_general_mapping", None)


def _refresh_available_source_vgs(settings):
    coll = settings.available_source_vgs
    coll.clear()
    obj = settings.source_object
    names = set()
    if obj is None or obj.type != 'MESH':
        profile = getattr(settings, "profile", None)
        if profile is None:
            return
    else:
        try:
            from ..core.mapping.filters import usable_vertex_groups
            names.update(name for _index, name in usable_vertex_groups(obj))
        except Exception:
            names.update(vg.name for vg in obj.vertex_groups)
    profile = getattr(settings, "profile", None)
    if profile is not None:
        for row in profile.rows:
            source_name = (getattr(row, "source_name", "") or "").strip()
            current_name = (getattr(row, "current_source_name", "") or "").strip()
            if source_name:
                names.add(source_name)
            if current_name:
                names.add(current_name)
    for name in sorted(names):
        item = coll.add()
        item.name = name


def _clear_profile(settings):
    profile = settings.profile
    if profile is None:
        return
    profile.rows.clear()
    profile.active_row_index = 0


def _on_general_overlay_update(self, context):
    if not getattr(self, "show_overlay", False):
        try:
            from . import pick as _pick
            _pick.reset_state(context)
        except Exception:
            pass
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return
    ef = getattr(context.scene, "velo_endfield", None)
    if ef is not None and getattr(ef, "show_overlay", False):
        try:
            ef.show_overlay = False
        except Exception:
            pass
    try:
        from . import pick as _pick
        _pick.start_modal_if_needed()
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _on_active_general_text_update(self, context):
    global _suspend_general_row_update
    prev = _suspend_general_row_update
    tb = self.active_general_text
    src = self.source_object
    if src is not None:
        try:
            src["velo_general_text"] = tb.name if tb is not None else ""
        except Exception:
            pass
    if tb is None or self.profile is None:
        return
    try:
        from . import operators as _ops
        _suspend_general_row_update = True
        _ops.load_general_text_into_settings(self, tb.as_string())
        _ops.apply_general_text_meta(self, tb)
        _ops.write_general_text_meta(tb, self)
        if _ops.general_source_is_target_named(self):
            _ops.sync_general_source_from_table(self, save_backup=False)
    except Exception:
        pass
    finally:
        _suspend_general_row_update = prev


def _on_general_row_target_update(self, context):
    global _suspend_general_row_update
    if _suspend_general_row_update:
        return
    settings = _settings_from_context(context)
    if settings is None:
        return
    try:
        from . import operators as _ops
        if _ops.general_source_is_target_named(settings):
            final_name, _bone_n = _ops.sync_general_source_row_incremental(
                settings,
                self,
                self.target_name,
            )
            if final_name and getattr(self, "current_source_name", "") != final_name:
                prev = _suspend_general_row_update
                _suspend_general_row_update = True
                try:
                    self.current_source_name = final_name
                finally:
                    _suspend_general_row_update = prev
        if not (self.target_name or "").strip():
            self.has_target_centroid = False
        _ops.capture_general_row_snapshot(settings, self)
        _ops.autosync_general_to_text(settings)
    except Exception:
        pass


def _on_source_object_update(self, context):
    global _suspend_general_row_update
    try:
        _refresh_available_source_vgs(self)
    except Exception:
        pass
    src = self.source_object
    if src is not None:
        try:
            bound = src.get("velo_general_text", "")
            if bound:
                tb = bpy.data.texts.get(bound)
                if tb is not None and self.active_general_text != tb:
                    self.active_general_text = tb
                elif tb is None:
                    _clear_profile(self)
                    prev = _suspend_general_row_update
                    _suspend_general_row_update = True
                    try:
                        self.active_general_text = None
                    finally:
                        _suspend_general_row_update = prev
            else:
                _clear_profile(self)
                prev = _suspend_general_row_update
                _suspend_general_row_update = True
                try:
                    self.active_general_text = None
                finally:
                    _suspend_general_row_update = prev
        except Exception:
            pass
    else:
        _clear_profile(self)
        prev = _suspend_general_row_update
        _suspend_general_row_update = True
        try:
            self.active_general_text = None
        finally:
            _suspend_general_row_update = prev
    try:
        from . import overlay as _overlay
        _overlay.invalidate_cache()
    except Exception:
        pass
    try:
        from . import pick as _pick
        _pick.reset_state(context)
        if getattr(self, "show_overlay", False):
            _pick.start_modal_if_needed()
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def _on_target_object_update(self, context):
    try:
        from . import overlay as _overlay
        _overlay.invalidate_cache()
    except Exception:
        pass
    try:
        from . import pick as _pick
        _pick.reset_state(context)
        if getattr(self, "show_overlay", False):
            _pick.start_modal_if_needed()
    except Exception:
        pass
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


class VELO_GM_VGName(bpy.types.PropertyGroup):
    pass


class VELO_GM_Row(bpy.types.PropertyGroup):
    source_name: StringProperty(name="源", default="")
    target_name: StringProperty(name="目标", default="", update=_on_general_row_target_update)
    current_source_name: StringProperty(name="CurrentSource", default="")
    enabled: BoolProperty(name="Enabled", default=True)
    source_centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    target_centroid_local: FloatVectorProperty(size=3, default=(0.0, 0.0, 0.0))
    has_source_centroid: BoolProperty(default=False)
    has_target_centroid: BoolProperty(default=False)


class VELO_GM_Profile(bpy.types.PropertyGroup):
    name: StringProperty(name="Name", default="default")
    rows: CollectionProperty(type=VELO_GM_Row)
    active_row_index: IntProperty(name="Row", default=0)


class VELO_GM_Settings(bpy.types.PropertyGroup):
    profile: PointerProperty(type=VELO_GM_Profile)
    active_general_text: PointerProperty(
        name="映射表",
        type=bpy.types.Text,
        description="当前使用的通用映射表内置文本",
        update=_on_active_general_text_update,
    )
    source_object: PointerProperty(
        name="源物体",
        type=bpy.types.Object,
        poll=_is_mesh_poll,
        description="通用映射的源物体",
        update=_on_source_object_update,
    )
    target_object: PointerProperty(
        name="目标物体",
        type=bpy.types.Object,
        poll=_is_mesh_poll,
        description="通用映射的目标物体",
        update=_on_target_object_update,
    )
    armature_object: PointerProperty(
        name="骨架",
        type=bpy.types.Object,
        poll=lambda self, obj: obj is not None and obj.type == 'ARMATURE',
        description="可选；选择后源物体改名会同步到骨架",
    )
    show_overlay: BoolProperty(
        name="启用通用映射可视化",
        default=False,
        description="开启或关闭通用映射可视化校对；开启时会自动关闭 MMD 映射可视化以避免两套端点叠加",
        update=_on_general_overlay_update,
    )
    available_source_vgs: CollectionProperty(type=VELO_GM_VGName)
    use_max_distance: BoolProperty(
        name="启用最大距离过滤",
        default=False,
        description="KDTree 匹配距离超过阈值时跳过写表",
    )
    max_match_distance: FloatProperty(
        name="最大匹配距离",
        default=0.5,
        min=0.0,
        soft_max=10.0,
        description="通用映射启用最大距离过滤时允许的最大匹配距离",
    )


_classes = (
    VELO_GM_VGName,
    VELO_GM_Row,
    VELO_GM_Profile,
    VELO_GM_Settings,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.velo_general_mapping = PointerProperty(type=VELO_GM_Settings)


def unregister():
    if hasattr(bpy.types.Scene, "velo_general_mapping"):
        try:
            del bpy.types.Scene.velo_general_mapping
        except Exception:
            pass
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass