from __future__ import annotations

import math

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)


def _is_mesh_poll(self, obj):
    return obj is not None and obj.type == 'MESH'


def _is_armature_poll(self, obj):
    return obj is not None and obj.type == 'ARMATURE'


class VELO_WeightVGName(bpy.types.PropertyGroup):
    pass


_DONOR_SLOT_PROPS = (
    "donor_slot_1",
    "donor_slot_2",
    "donor_slot_3",
    "donor_slot_4",
)


class _PreviewTargetGroup:
    __slots__ = ("name", "index")

    def __init__(self, name, index=-1):
        self.name = name
        self.index = index


def donor_count_value(settings):
    try:
        return max(1, min(int(getattr(settings, "donor_count", 1)), len(_DONOR_SLOT_PROPS)))
    except Exception:
        return 1


def selected_donor_names(settings, count=None):
    limit = donor_count_value(settings) if count is None else max(0, min(int(count), len(_DONOR_SLOT_PROPS)))
    names = []
    seen = set()
    for prop_name in _DONOR_SLOT_PROPS[:limit]:
        cleaned = (getattr(settings, prop_name, "") or "").strip()
        if not cleaned or cleaned in seen:
            continue
        names.append(cleaned)
        seen.add(cleaned)
    return names


def sync_target_group_name(settings, context):
    if getattr(settings, "manual_target_group_name", False):
        return
    try:
        from . import algorithms as _algo
        target_name = _algo.suggest_target_group_name(context, settings)
    except Exception:
        target_name = (getattr(settings, "source_group", "") or "").strip()
    if target_name and getattr(settings, "target_group_name", "") != target_name:
        settings.target_group_name = target_name


def refresh_source_vg_names(settings):
    coll = settings.available_source_vgs
    coll.clear()
    obj = settings.source_object
    if obj is None or obj.type != 'MESH':
        return
    try:
        from ..core.mapping.filters import is_special_vg_name
    except Exception:
        is_special_vg_name = lambda name: False
    for vg in obj.vertex_groups:
        if getattr(vg, "lock_weight", False):
            continue
        if is_special_vg_name(vg.name):
            continue
        item = coll.add()
        item.name = vg.name


def refresh_donor_vg_names(settings):
    coll = settings.available_donor_vgs
    coll.clear()
    obj = settings.target_object
    if obj is None or obj.type != 'MESH':
        return
    target_name = (getattr(settings, "target_group_name", "") or "").strip()
    try:
        from ..core.mapping.filters import is_special_vg_name
    except Exception:
        is_special_vg_name = lambda name: False
    for vg in obj.vertex_groups:
        if getattr(vg, "lock_weight", False):
            continue
        if is_special_vg_name(vg.name):
            continue
        if target_name and vg.name == target_name:
            continue
        item = coll.add()
        item.name = vg.name


def preview_donor_names(settings, context):
    if context is None:
        return []
    source_name = (getattr(settings, "source_group", "") or "").strip()
    target = getattr(settings, "target_object", None)
    if not source_name or target is None or target.type != 'MESH':
        return []
    try:
        from . import algorithms as _algo
        target_name = (getattr(settings, "target_group_name", "") or "").strip()
        if not target_name:
            target_name = _algo.suggest_target_group_name(context, settings)
        if not target_name:
            return []
        target_group = target.vertex_groups.get(target_name)
        if target_group is None:
            target_group = _PreviewTargetGroup(target_name)
            return _algo.semantic_auto_donor_names(
                context,
                settings,
                source_name,
                target_group,
                count=donor_count_value(settings),
            )
        semantic_names = _algo.semantic_auto_donor_names(
            context,
            settings,
            source_name,
            target_group,
            count=donor_count_value(settings),
        )
        donors = _algo.select_auto_donors(
            target,
            target_group,
            donor_count_value(settings),
            preferred_names=semantic_names,
            strict_preferred=False,
        )
        return [vg.name for vg in donors]
    except Exception:
        return []


def sync_donor_preview(settings, context):
    refresh_donor_vg_names(settings)
    preview_names = preview_donor_names(settings, context)
    for prop_name, donor_name in zip(_DONOR_SLOT_PROPS, preview_names):
        setattr(settings, prop_name, donor_name)
    for prop_name in _DONOR_SLOT_PROPS[len(preview_names):]:
        setattr(settings, prop_name, "")


def _on_source_object_update(self, context):
    refresh_source_vg_names(self)
    if not self.source_group and len(self.available_source_vgs) > 0:
        self.source_group = self.available_source_vgs[0].name
    sync_target_group_name(self, context)
    sync_donor_preview(self, context)


def _on_target_object_update(self, context):
    sync_target_group_name(self, context)
    sync_donor_preview(self, context)


def _on_source_group_update(self, context):
    sync_target_group_name(self, context)
    sync_donor_preview(self, context)


def _on_manual_target_group_update(self, context):
    if not getattr(self, "manual_target_group_name", False):
        sync_target_group_name(self, context)
    sync_donor_preview(self, context)


def _on_target_group_name_update(self, context):
    sync_donor_preview(self, context)


def _on_donor_count_update(self, context):
    sync_donor_preview(self, context)


class VELO_WeightSettings(bpy.types.PropertyGroup):
    source_object: PointerProperty(
        name="来源网格",
        type=bpy.types.Object,
        poll=_is_mesh_poll,
        description="提供权重来源的网格；来源顶点组从该物体读取",
        update=_on_source_object_update,
    )
    target_object: PointerProperty(
        name="目标网格",
        type=bpy.types.Object,
        poll=_is_mesh_poll,
        description="接收新权重的网格；承接组会在该物体上复用或创建",
        update=_on_target_object_update,
    )
    armature_object: PointerProperty(
        name="目标骨架",
        type=bpy.types.Object,
        poll=_is_armature_poll,
        description="可选；承接组不存在且允许创建新骨时，会在该骨架里创建同名 deform 骨骼。若留空，会尝试从目标网格的父级/Armature 修改器自动推断。",
    )
    merge_source_group: StringProperty(
        name="转移来源组",
        default="",
        description="当前选中网格上要把权重转出的顶点组",
    )
    merge_target_group: StringProperty(
        name="转移目标组",
        default="",
        description="当前选中网格上要接收额外权重的顶点组",
    )
    available_source_vgs: CollectionProperty(
        type=VELO_WeightVGName,
        description="来源网格上可作为权重供体的非锁定、非特殊顶点组候选",
    )
    available_donor_vgs: CollectionProperty(
        type=VELO_WeightVGName,
        description="目标网格上可手动选择的供体组候选；只显示未锁定、非特殊顶点组",
    )
    source_group: StringProperty(
        name="来源顶点组",
        default="",
        description="要从来源网格读取的顶点组；会自动根据 MMD 映射表推断承接组",
        update=_on_source_group_update,
    )
    engine: EnumProperty(
        name="传递引擎",
        items=[
            ('ROBUST', "Robust", "robust_weight_transfer 最近表面匹配 + inpaint"),
            ('DATA_TRANSFER_SURFACE', "面插值传递", "Blender Data Transfer 数据传递修改器的 POLYINTERP_NEAREST 表面面插值"),
        ],
        default='ROBUST',
        description="选择权重传递核心：Robust 表面匹配/inpaint，或 Blender Data Transfer 数据修改器的 POLYINTERP_NEAREST 表面面插值传递",
    )
    manual_target_group_name: BoolProperty(
        name="手动指定承接组",
        default=False,
        description="关闭时按 MMD 映射表自动识别承接组；开启后使用下面填写的承接组名作为覆盖",
        update=_on_manual_target_group_update,
    )
    target_group_name: StringProperty(
        name="承接组名",
        default="",
        description="自动推断或手动指定的目标顶点组名；自动模式会优先使用 MMD 映射表认领的对应组",
        update=_on_target_group_name_update,
    )
    reuse_existing_group: BoolProperty(
        name="复用已有承接组",
        default=True,
        description="保留项：当前流程会自动复用目标网格上存在且未锁定的承接组",
    )
    clear_before_transfer: BoolProperty(
        name="传递前清空承接组",
        default=True,
        description="保留项：当前执行会在写入前清空承接组，避免旧权重残留",
    )
    create_bone_if_missing: BoolProperty(
        name="无承接组时创建新骨",
        default=True,
        description="承接组不存在且选择了目标骨架时，同时创建同名 deform 骨骼",
    )
    donor_count: IntProperty(
        name="自动供体数",
        description="规格化时最多使用的供体组数量；当前固定为 1 到 4 档",
        default=3,
        min=1,
        soft_max=4,
        max=4,
        update=_on_donor_count_update,
    )
    donor_slot_1: StringProperty(
        name="供体 1",
        default="",
        description="规格化时优先使用的第 1 个供体；来源组切换时会自动预填",
    )
    donor_slot_2: StringProperty(
        name="供体 2",
        default="",
        description="规格化时优先使用的第 2 个供体；来源组切换时会自动预填",
    )
    donor_slot_3: StringProperty(
        name="供体 3",
        default="",
        description="规格化时优先使用的第 3 个供体；来源组切换时会自动预填",
    )
    donor_slot_4: StringProperty(
        name="供体 4",
        default="",
        description="规格化时优先使用的第 4 个供体；来源组切换时会自动预填",
    )
    smoothing_enable: BoolProperty(
        name="启用平滑",
        default=True,
        description="传递后对新承接组执行 seam-safe 平滑；会阻断 UV seam 边以避免接缝串权重",
    )
    smoothing_repeat: IntProperty(
        name="平滑次数",
        default=4,
        min=0,
        soft_max=20,
        description="平滑迭代次数；次数越高越柔和，但也更容易扩散细节",
    )
    smoothing_factor: FloatProperty(
        name="平滑强度",
        default=0.2,
        min=0.0,
        max=1.0,
        description="每次平滑混合邻域权重的比例；0 不改变，1 完全采用邻域平均",
    )
    limit_groups_enable: BoolProperty(
        name="限制每顶点组数量",
        default=True,
        description="传递后限制每个顶点参与的可编辑骨骼权重数量；锁定组和 Velo 特殊组不会参与",
    )
    max_groups_per_vertex: IntProperty(
        name="最大组数量",
        default=4,
        min=1,
        soft_max=8,
        description="每个顶点最多保留的可编辑权重组数量",
    )
    normalize_after: BoolProperty(
        name="执行后规格化",
        default=True,
        description="传递后用自动供体组一起规格化；当前实现会优先保留新传递组的权重，只压缩供体组去适配剩余权重空间。同对象不同组传递时会自动跳过，避免改写来源组。",
    )
    show_advanced: BoolProperty(
        name="高级参数",
        default=False,
        description="保留项：高级参数面板目前作为独立折叠面板显示",
    )
    robust_max_distance: FloatProperty(
        name="Robust 最大距离",
        description="Robust 直接匹配允许的最大世界空间距离；超过该距离的目标顶点不会作为 direct match",
        default=0.05,
        min=0.0,
        soft_max=1.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )
    robust_normal_angle: FloatProperty(
        name="Robust 法线角",
        description="Robust 直接匹配时，来源面插值法线与目标顶点法线允许的最大夹角。超出阈值的顶点不会作为 direct match；若全部超出，Robust 会失败。开启法线翻转时，也会接受接近 180 度的反向法线。",
        default=math.radians(60.0),
        min=0.0,
        max=math.pi,
        subtype='ANGLE',
        unit='ROTATION',
    )
    robust_flip_normals: BoolProperty(
        name="允许法线翻转",
        description="允许把接近 180 度的反向法线也视为可匹配；当法线角达到 90 度及以上时，这会基本关闭角度过滤。",
        default=True,
    )
    robust_point_cloud_inpaint: BoolProperty(
        name="Point inpaint",
        default=True,
        description="使用点云 Laplacian 做 inpaint；关闭后使用网格面 Laplacian",
    )
    use_deformed_source: BoolProperty(
        name="使用来源变形结果",
        default=True,
        description="开启时在来源网格的 evaluated/deformed 结果上计算表面和法线",
    )
    use_deformed_target: BoolProperty(
        name="使用目标变形结果",
        default=True,
        description="开启时在目标网格的 evaluated/deformed 结果上匹配；目标拓扑必须与原网格一致",
    )
    limit_dilation_repeat: IntProperty(
        name="Limit dilation",
        default=4,
        min=0,
        soft_max=12,
        description="限制每顶点组数量时，对被剔除候选做邻域扩张保护的次数",
    )
    last_report: StringProperty(
        name="结果",
        default="",
        description="最近一次权重传递的结果或错误信息",
    )


_classes = (
    VELO_WeightVGName,
    VELO_WeightSettings,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.velo_weight_tools = PointerProperty(type=VELO_WeightSettings)


def unregister():
    if hasattr(bpy.types.Scene, "velo_weight_tools"):
        try:
            del bpy.types.Scene.velo_weight_tools
        except Exception:
            pass
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
