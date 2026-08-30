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


def _on_mirror_mapping_update(self, context):
    settings = getattr(getattr(context, "scene", None), "velo_weight_tools", None)
    if settings is None:
        return
    sync_mirror_group(settings, context)
    sync_donor_preview(settings, context)


class VELO_WeightMirrorMapping(bpy.types.PropertyGroup):
    left_group: StringProperty(
        name='Left group',
        default="",
        description='Manually mirror the vertex group name on one side of the mapping',
        update=_on_mirror_mapping_update,
    )
    right_group: StringProperty(
        name='Right-side group',
        default="",
        description='Manually mirror the vertex group name on the other side of the mapping',
        update=_on_mirror_mapping_update,
    )


_DONOR_SLOT_PROPS = (
    "donor_slot_1",
    "donor_slot_2",
    "donor_slot_3",
    "donor_slot_4",
    "donor_slot_5",
    "donor_slot_6",
)


_MIRROR_DONOR_SLOT_PROPS = (
    "mirror_donor_slot_1",
    "mirror_donor_slot_2",
    "mirror_donor_slot_3",
    "mirror_donor_slot_4",
    "mirror_donor_slot_5",
    "mirror_donor_slot_6",
)


_DONOR_SLOT_MANUAL_PROPS = (
    "donor_slot_1_manual",
    "donor_slot_2_manual",
    "donor_slot_3_manual",
    "donor_slot_4_manual",
    "donor_slot_5_manual",
    "donor_slot_6_manual",
)


_SYNCING_DONOR_PREVIEW = False


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


def selected_donor_pairs(settings, count=None):
    limit = donor_count_value(settings) if count is None else max(0, min(int(count), len(_DONOR_SLOT_PROPS)))
    pairs = []
    seen = set()
    for donor_prop, mirror_prop, manual_prop in zip(
        _DONOR_SLOT_PROPS[:limit],
        _MIRROR_DONOR_SLOT_PROPS[:limit],
        _DONOR_SLOT_MANUAL_PROPS[:limit],
    ):
        if not bool(getattr(settings, manual_prop, False)):
            continue
        cleaned = (getattr(settings, donor_prop, "") or "").strip()
        if not cleaned or cleaned in seen:
            continue
        mirror = (getattr(settings, mirror_prop, "") or "").strip()
        pairs.append((cleaned, mirror))
        seen.add(cleaned)
    return pairs


def selected_donor_names(settings, count=None):
    return [donor for donor, _mirror in selected_donor_pairs(settings, count=count)]


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
    mirror_name = (
        (getattr(settings, "mirror_target_group_name", "") or "").strip()
        or (getattr(settings, "mirror_group", "") or "").strip()
    )
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
        if mirror_name and vg.name == mirror_name:
            continue
        item = coll.add()
        item.name = vg.name


def refresh_mirror_vg_names(settings):
    coll = settings.available_mirror_vgs
    coll.clear()
    obj = settings.source_object
    source_name = (getattr(settings, "source_group", "") or "").strip()
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
        if source_name and vg.name == source_name:
            continue
        item = coll.add()
        item.name = vg.name


def sync_mirror_group(settings, context):
    refresh_mirror_vg_names(settings)
    source_name = (getattr(settings, "source_group", "") or "").strip()
    obj = getattr(settings, "source_object", None)
    if not source_name or obj is None or obj.type != 'MESH':
        settings.mirror_group = ""
        settings.mirror_status = "未选择来源顶点组"
        sync_mirror_target_group_name(settings, context)
        return
    try:
        from . import algorithms as _algo
        resolution = _algo.resolve_mirror_group(context, settings, obj, source_name)
    except Exception as exc:
        settings.mirror_group = ""
        settings.mirror_status = str(exc)
        sync_mirror_target_group_name(settings, context)
        return
    if resolution.mirror_name:
        if getattr(settings, "mirror_group", "") != resolution.mirror_name:
            settings.mirror_group = resolution.mirror_name
        confidence = f"{resolution.confidence:.3f}" if resolution.confidence else "-"
        settings.mirror_status = f"{resolution.reason}: {source_name} ↔ {resolution.mirror_name} ({confidence})"
    else:
        if getattr(settings, "mirror_group", ""):
            settings.mirror_group = ""
        settings.mirror_status = resolution.reason or "未找到可信镜像顶点组"
    sync_mirror_target_group_name(settings, context)


def sync_mirror_target_group_name(settings, context):
    if getattr(settings, "manual_mirror_target_group_name", False):
        return
    mirror_name = (getattr(settings, "mirror_group", "") or "").strip()
    target = getattr(settings, "target_object", None)
    target_name = (getattr(settings, "target_group_name", "") or "").strip()
    if not mirror_name or target is None or target.type != 'MESH' or not target_name:
        if getattr(settings, "mirror_target_group_name", ""):
            settings.mirror_target_group_name = ""
        return
    try:
        from . import algorithms as _algo
        resolved = _algo.resolve_transfer_mirror_group_name(
            context,
            settings,
            target,
            target_name,
            mirror_name,
        )
    except Exception:
        resolved = mirror_name
    if resolved != getattr(settings, "mirror_target_group_name", ""):
        settings.mirror_target_group_name = resolved


def _set_donor_slot_value(settings, prop_name, value):
    if getattr(settings, prop_name, "") != value:
        setattr(settings, prop_name, value)


def _set_donor_slot_manual(settings, slot_index, value):
    if slot_index is None or slot_index < 0 or slot_index >= len(_DONOR_SLOT_MANUAL_PROPS):
        return
    prop_name = _DONOR_SLOT_MANUAL_PROPS[slot_index]
    if getattr(settings, prop_name, False) != bool(value):
        setattr(settings, prop_name, bool(value))


def _append_mirror_donor_status(settings, message):
    if not message:
        return
    current = (getattr(settings, "mirror_donor_status", "") or "").strip()
    settings.mirror_donor_status = f"{current}；{message}" if current else message


def _clear_donor_slot_manual_flags(settings):
    for prop_name in _DONOR_SLOT_MANUAL_PROPS:
        if getattr(settings, prop_name, False):
            setattr(settings, prop_name, False)


def _refresh_mirror_donor_status(settings, context, donor_names=None):
    settings.mirror_donor_status = ""
    target = getattr(settings, "target_object", None)
    if target is None or target.type != 'MESH':
        return
    if donor_names is None:
        pairs = selected_donor_pairs(settings)
    else:
        pairs = [
            (donor_name, (getattr(settings, _MIRROR_DONOR_SLOT_PROPS[index], "") or "").strip())
            for index, donor_name in enumerate(donor_names)
        ]
    if not pairs:
        return
    try:
        missing = []
        locked = []
        for donor_name, mirror_name in pairs:
            if not mirror_name:
                missing.append(donor_name)
                continue
            group = target.vertex_groups.get(mirror_name)
            if group is None:
                missing.append(mirror_name)
                continue
            if group is not None and getattr(group, "lock_weight", False):
                locked.append(mirror_name)
        if locked:
            settings.mirror_donor_status = "镜像供体已锁定: " + ", ".join(locked)
        elif missing:
            settings.mirror_donor_status = "未找到镜像供体: " + ", ".join(missing)
        elif pairs:
            settings.mirror_donor_status = "镜像供体已匹配"
    except Exception as exc:
        settings.mirror_donor_status = str(exc)


def sync_mirror_donor_preview(settings, context, base_donor_names=None, skipped_locked_pairs=None):
    global _SYNCING_DONOR_PREVIEW
    previous = _SYNCING_DONOR_PREVIEW
    _SYNCING_DONOR_PREVIEW = True
    try:
        for prop_name in _MIRROR_DONOR_SLOT_PROPS:
            _set_donor_slot_value(settings, prop_name, "")
        settings.mirror_donor_status = ""
        target = getattr(settings, "target_object", None)
        if target is None or target.type != 'MESH':
            return
        donor_names = list(base_donor_names) if base_donor_names is not None else selected_donor_names(settings)
        if not donor_names:
            if skipped_locked_pairs:
                _append_mirror_donor_status(
                    settings,
                    "已跳过锁定供体对: " + ", ".join(skipped_locked_pairs),
                )
            return
        try:
            from . import algorithms as _algo
            mirrored = []
            for donor_name in donor_names:
                resolution = _algo.resolve_mirror_group(context, settings, target, donor_name)
                mirrored.append(resolution.mirror_name or "")
            for prop_name, donor_name in zip(_MIRROR_DONOR_SLOT_PROPS, mirrored):
                _set_donor_slot_value(settings, prop_name, donor_name)
        except Exception as exc:
            settings.mirror_donor_status = str(exc)
        else:
            _refresh_mirror_donor_status(settings, context, donor_names)
            if skipped_locked_pairs:
                _append_mirror_donor_status(
                    settings,
                    "已跳过锁定供体对: " + ", ".join(skipped_locked_pairs),
                )
    finally:
        _SYNCING_DONOR_PREVIEW = previous


def _uses_mirror_donor_pairs(settings):
    return bool(
        (getattr(settings, "mirror_group", "") or "").strip()
        or (getattr(settings, "mirror_target_group_name", "") or "").strip()
    )


def preview_donor_selection(settings, context):
    if context is None:
        return [], []
    source_name = (getattr(settings, "source_group", "") or "").strip()
    target = getattr(settings, "target_object", None)
    if not source_name or target is None or target.type != 'MESH':
        return [], []
    try:
        from . import algorithms as _algo
        target_name = (getattr(settings, "target_group_name", "") or "").strip()
        if not target_name:
            target_name = _algo.suggest_target_group_name(context, settings)
        if not target_name:
            return [], []
        target_group = target.vertex_groups.get(target_name)
        try:
            focus_weights = _algo.source_group_target_focus_weights(context, settings, source_name)
        except Exception:
            focus_weights = None
        if target_group is None:
            target_group = _PreviewTargetGroup(target_name)
        semantic_names = _algo.semantic_auto_donor_names(
            context,
            settings,
            source_name,
            target_group,
            count=donor_count_value(settings),
        )
        semantic_groups = [target.vertex_groups.get(name) for name in semantic_names]
        semantic_groups = [group for group in semantic_groups if group is not None]
        preferred_side = _algo.infer_donor_side(
            target,
            target_group,
            focus_weights=focus_weights,
            candidate_groups=semantic_groups,
        )
        mirror_target_name = (
            (getattr(settings, "mirror_target_group_name", "") or "").strip()
            or (getattr(settings, "mirror_group", "") or "").strip()
        )
        exclude_group_names = [mirror_target_name]
        uses_mirror_pairs = _uses_mirror_donor_pairs(settings)
        donors = _algo.select_auto_donors(
            target,
            target_group,
            donor_count_value(settings),
            exclude_group_names=exclude_group_names,
            preferred_names=semantic_names,
            strict_preferred=False,
            focus_weights=focus_weights,
            preferred_side=preferred_side,
            rank_all=uses_mirror_pairs,
        )
        skipped_locked_pairs = []
        if uses_mirror_pairs:
            eligibility = _algo.auto_donor_pair_eligibility(
                context,
                settings,
                target,
                donors,
                exclude_names=[target_name, mirror_target_name],
                max_pairs=donor_count_value(settings),
            )
            donors = eligibility.donors
            skipped_locked_pairs = list(eligibility.skipped_locked_pairs)
            diagnostic_donors = _algo.select_auto_donors(
                target,
                target_group,
                donor_count_value(settings),
                exclude_group_names=exclude_group_names,
                preferred_names=semantic_names,
                strict_preferred=False,
                focus_weights=focus_weights,
                preferred_side=preferred_side,
                include_locked_candidates=True,
                rank_all=True,
            )
            diagnostic = _algo.auto_donor_pair_eligibility(
                context,
                settings,
                target,
                diagnostic_donors,
                exclude_names=[target_name, mirror_target_name],
                max_pairs=donor_count_value(settings),
            )
            for label in diagnostic.skipped_locked_pairs:
                if label not in skipped_locked_pairs:
                    skipped_locked_pairs.append(label)
        return [vg.name for vg in donors], skipped_locked_pairs
    except Exception:
        return [], []


def preview_donor_names(settings, context):
    try:
        donor_names, _skipped_locked_pairs = preview_donor_selection(settings, context)
        return donor_names
    except Exception:
        return []


def sync_donor_preview(settings, context):
    global _SYNCING_DONOR_PREVIEW
    previous = _SYNCING_DONOR_PREVIEW
    _SYNCING_DONOR_PREVIEW = True
    try:
        _clear_donor_slot_manual_flags(settings)
        refresh_donor_vg_names(settings)
        preview_names, skipped_locked_pairs = preview_donor_selection(settings, context)
        for prop_name, donor_name in zip(_DONOR_SLOT_PROPS, preview_names):
            _set_donor_slot_value(settings, prop_name, donor_name)
        for prop_name in _DONOR_SLOT_PROPS[len(preview_names):]:
            _set_donor_slot_value(settings, prop_name, "")
        sync_mirror_donor_preview(settings, context, preview_names, skipped_locked_pairs=skipped_locked_pairs)
    finally:
        _SYNCING_DONOR_PREVIEW = previous


def _on_source_object_update(self, context):
    refresh_source_vg_names(self)
    if not self.source_group and len(self.available_source_vgs) > 0:
        self.source_group = self.available_source_vgs[0].name
    sync_mirror_group(self, context)
    sync_target_group_name(self, context)
    sync_donor_preview(self, context)


def _on_target_object_update(self, context):
    sync_mirror_group(self, context)
    sync_target_group_name(self, context)
    sync_donor_preview(self, context)


def _on_source_group_update(self, context):
    sync_mirror_group(self, context)
    sync_target_group_name(self, context)
    sync_donor_preview(self, context)


def _on_manual_target_group_update(self, context):
    if not getattr(self, "manual_target_group_name", False):
        sync_target_group_name(self, context)
    sync_mirror_target_group_name(self, context)
    sync_donor_preview(self, context)


def _on_target_group_name_update(self, context):
    sync_mirror_target_group_name(self, context)
    sync_donor_preview(self, context)


def _on_mirror_group_update(self, context):
    sync_mirror_target_group_name(self, context)
    sync_mirror_donor_preview(self, context)


def _on_mirror_target_group_update(self, context):
    sync_donor_preview(self, context)


def _on_donor_count_update(self, context):
    sync_donor_preview(self, context)


def _on_donor_slot_update(self, context, slot_index=None):
    if _SYNCING_DONOR_PREVIEW:
        return
    _set_donor_slot_manual(self, slot_index, True)
    sync_mirror_donor_preview(self, context)


def _on_mirror_donor_slot_update(self, context, slot_index=None):
    if _SYNCING_DONOR_PREVIEW:
        return
    _set_donor_slot_manual(self, slot_index, True)
    _refresh_mirror_donor_status(self, context)


def _on_donor_slot_1_update(self, context):
    _on_donor_slot_update(self, context, 0)


def _on_donor_slot_2_update(self, context):
    _on_donor_slot_update(self, context, 1)


def _on_donor_slot_3_update(self, context):
    _on_donor_slot_update(self, context, 2)


def _on_donor_slot_4_update(self, context):
    _on_donor_slot_update(self, context, 3)


def _on_donor_slot_5_update(self, context):
    _on_donor_slot_update(self, context, 4)


def _on_donor_slot_6_update(self, context):
    _on_donor_slot_update(self, context, 5)


def _on_mirror_donor_slot_1_update(self, context):
    _on_mirror_donor_slot_update(self, context, 0)


def _on_mirror_donor_slot_2_update(self, context):
    _on_mirror_donor_slot_update(self, context, 1)


def _on_mirror_donor_slot_3_update(self, context):
    _on_mirror_donor_slot_update(self, context, 2)


def _on_mirror_donor_slot_4_update(self, context):
    _on_mirror_donor_slot_update(self, context, 3)


def _on_mirror_donor_slot_5_update(self, context):
    _on_mirror_donor_slot_update(self, context, 4)


def _on_mirror_donor_slot_6_update(self, context):
    _on_mirror_donor_slot_update(self, context, 5)


def _on_manual_mirror_target_group_update(self, context):
    if not getattr(self, "manual_mirror_target_group_name", False):
        sync_mirror_target_group_name(self, context)


class VELO_WeightSettings(bpy.types.PropertyGroup):
    source_object: PointerProperty(
        name='Source Mesh',
        type=bpy.types.Object,
        poll=_is_mesh_poll,
        description='Provide a mesh for weight sources; the source vertex group is read from this object',
        update=_on_source_object_update,
    )
    target_object: PointerProperty(
        name='Target mesh',
        type=bpy.types.Object,
        poll=_is_mesh_poll,
        description='Receive mesh with new weights; the associated group will be reused or created on this object',
        update=_on_target_object_update,
    )
    armature_object: PointerProperty(
        name='Target skeleton',
        type=bpy.types.Object,
        poll=_is_armature_poll,
        description='Optional; if the target group does not exist and creating new bones is allowed, a deform bone with the same name will be created in this armature. If left blank, it will try to automatically infer from the parent / Armature modifier of the target mesh.',
    )
    merge_source_group: StringProperty(
        name='Transfer source group',
        default="",
        description='Vertex group on the currently selected mesh to transfer weights from',
    )
    merge_target_group: StringProperty(
        name='Transfer target group',
        default="",
        description='Vertex group on the currently selected mesh to receive additional weights',
    )
    available_source_vgs: CollectionProperty(
        type=VELO_WeightVGName,
        description='Candidates for weight donors on the source mesh that are non-locked and non-special vertex groups',
    )
    available_donor_vgs: CollectionProperty(
        type=VELO_WeightVGName,
        description='Candidate donor groups manually selectable on the target mesh; only unlocked, non-special vertex groups are displayed',
    )
    available_mirror_vgs: CollectionProperty(
        type=VELO_WeightVGName,
        description='Candidates for mirror vertex groups on the source mesh',
    )
    mirror_mappings: CollectionProperty(
        type=VELO_WeightMirrorMapping,
        description='Scene-level manual mirror vertex group mapping; mappings with the same name will apply to Component objects within the current Velo takeover range',
    )
    active_mirror_mapping_index: IntProperty(
        name='Mirror Mapping Index',
        default=0,
        min=0,
    )
    source_group: StringProperty(
        name='Source Vertex Group',
        default="",
        description='Vertex groups to be read from the source mesh; binding groups will be automatically inferred according to the MMD mapping table.',
        update=_on_source_group_update,
    )
    mirror_group: StringProperty(
        name='Mirror Vertex Group',
        default="",
        description='Mirror group of the source vertex group; automatically rematches when the source vertex group changes, can also be manually reselected',
        update=_on_mirror_group_update,
    )
    mirror_status: StringProperty(
        name='Mirror Status',
        default="",
        description='The most recent automatic matching result of the mirror vertex group',
    )
    mirror_donor_status: StringProperty(
        name='Mirror Donor Status',
        default="",
        description='The most recent precomputation result of the mirror donor',
    )
    engine: EnumProperty(
        name='Transfer engine',
        items=[
            ('ROBUST', "Robust", 'robust_weight_transfer Recent Surface Matching + Inpaint'),
            ('DATA_TRANSFER_SURFACE', 'Face interpolation pass', 'Blender Data Transfer modifier POLYINTERP_NEAREST surface interpolation'),
        ],
        default='ROBUST',
        description='Select weight transfer core: Robust surface matching/inpaint, or POLYINTERP_NEAREST surface interpolation transfer of Blender Data Transfer modifier',
    )
    manual_target_group_name: BoolProperty(
        name='Manually specify receiving group',
        default=False,
        description='When off, automatically recognize the undertaking group according to the MMD mapping table; when on, use the undertaking group name filled in below as an override',
        update=_on_manual_target_group_update,
    )
    target_group_name: StringProperty(
        name='Parent group name',
        default="",
        description='Automatically infer or manually specify the target vertex group name; in automatic mode, the corresponding group claimed by the MMD mapping table will be prioritized',
        update=_on_target_group_name_update,
    )
    manual_mirror_target_group_name: BoolProperty(
        name='Manually specify mirrored receiving group',
        default=False,
        description='When off, automatically recognize the mirror undertaking group according to the current image transmission rules; when on, use the mirror undertaking group name filled in below as an override',
        update=_on_manual_mirror_target_group_update,
    )
    mirror_target_group_name: StringProperty(
        name='Mirror Receiving Group Name',
        default="",
        description='The name of the target vertex group written during mirror transfer; can manually override the automatically parsed result',
        update=_on_mirror_target_group_update,
    )
    reuse_existing_group: BoolProperty(
        name='Reuse already has an acceptance group',
        default=True,
        description='Holded: The current process will automatically reuse any unlocked successor groups on the target mesh',
    )
    clear_before_transfer: BoolProperty(
        name='Clear the receiving group before transfer',
        default=True,
        description='Reserved item: The current execution will clear the acceptance group before writing to avoid retaining old weights',
    )
    create_bone_if_missing: BoolProperty(
        name='Create New Bone When No Connected Group',
        default=True,
        description='If the parent group does not exist and the target skeleton is selected, simultaneously create a deform bone with the same name',
    )
    auto_lock_target_groups: BoolProperty(
        name='Automatically lock receiving group after transfer',
        default=True,
        description='After the weight transfer is successful, lock the recipient group for this write; during mirror transfer, the mirror recipient group will also be locked to prevent subsequent restrictions or normalization rewrites from affecting the completed group.',
    )
    donor_count: IntProperty(
        name='Automatic donor count',
        description='The maximum number of donor groups used during normalization; currently fixed at 1 to 6 levels, automatic selection will not forcibly add weakly related groups to reach the limit',
        default=4,
        min=1,
        soft_max=6,
        max=6,
        update=_on_donor_count_update,
    )
    donor_slot_1: StringProperty(
        name='Donor 1',
        default="",
        description='First donor to prioritize when normalizing; will be automatically pre-filled when switching source groups',
        update=_on_donor_slot_1_update,
    )
    donor_slot_2: StringProperty(
        name='Donor 2',
        default="",
        description='Second donor to prioritize when normalizing; will be automatically pre-filled when switching source groups',
        update=_on_donor_slot_2_update,
    )
    donor_slot_3: StringProperty(
        name='Donor 3',
        default="",
        description='The 3rd donor preferred during normalization; automatically prefilled when switching source groups',
        update=_on_donor_slot_3_update,
    )
    donor_slot_4: StringProperty(
        name='Donor 4',
        default="",
        description='The 4th donor preferred during normalization; automatically prefilled when switching source groups',
        update=_on_donor_slot_4_update,
    )
    donor_slot_5: StringProperty(
        name='Donor 5',
        default="",
        description='The 5th donor preferred during normalization; automatically prefilled when switching source groups',
        update=_on_donor_slot_5_update,
    )
    donor_slot_6: StringProperty(
        name='Donor 6',
        default="",
        description='The 6th donor preferred during normalization; automatically prefilled when switching source groups',
        update=_on_donor_slot_6_update,
    )
    donor_slot_1_manual: BoolProperty(
        default=False,
        options={'HIDDEN'},
    )
    donor_slot_2_manual: BoolProperty(
        default=False,
        options={'HIDDEN'},
    )
    donor_slot_3_manual: BoolProperty(
        default=False,
        options={'HIDDEN'},
    )
    donor_slot_4_manual: BoolProperty(
        default=False,
        options={'HIDDEN'},
    )
    donor_slot_5_manual: BoolProperty(
        default=False,
        options={'HIDDEN'},
    )
    donor_slot_6_manual: BoolProperty(
        default=False,
        options={'HIDDEN'},
    )
    mirror_donor_slot_1: StringProperty(
        name='Mirror Donor 1',
        default="",
        description='Mirror donor for donor 1; will auto-fill, can also override manually',
        update=_on_mirror_donor_slot_1_update,
    )
    mirror_donor_slot_2: StringProperty(
        name='Mirror Donor 2',
        default="",
        description='Mirror donor for donor 2; will auto-fill, can also override manually',
        update=_on_mirror_donor_slot_2_update,
    )
    mirror_donor_slot_3: StringProperty(
        name='Mirror Donor 3',
        default="",
        description='Mirror donor for donor 3; will auto-fill, can also override manually',
        update=_on_mirror_donor_slot_3_update,
    )
    mirror_donor_slot_4: StringProperty(
        name='Mirror Donor 4',
        default="",
        description="Donor 4's mirror donor; Will be automatically prefilled or manually overwritten",
        update=_on_mirror_donor_slot_4_update,
    )
    mirror_donor_slot_5: StringProperty(
        name='Mirror Donor 5',
        default="",
        description="Donor 5's mirror donor; It will be automatically prefilled or manually overwritten",
        update=_on_mirror_donor_slot_5_update,
    )
    mirror_donor_slot_6: StringProperty(
        name='Mirror Donor 6',
        default="",
        description="Donor 6's mirror donor; It will be automatically prefilled or manually overwritten",
        update=_on_mirror_donor_slot_6_update,
    )
    smoothing_enable: BoolProperty(
        name='Enable smoothing.',
        default=True,
        description='After transfer, perform seam-safe smoothing on the new receiving group; UV seam edges will be blocked to prevent seams from affecting weights',
    )
    smoothing_repeat: IntProperty(
        name='Smoothing repetitions',
        default=4,
        min=0,
        soft_max=20,
        description='Number of smoothing iterations; the more iterations, the smoother, but details are also more likely to diffuse',
    )
    smoothing_factor: FloatProperty(
        name='Smoothing strength',
        default=0.2,
        min=0.0,
        max=1.0,
        description='The proportion to smooth and blend neighborhood weights each time; 0 means no change, 1 means fully adopt neighborhood average',
    )
    limit_groups_enable: BoolProperty(
        name='Limit the number of vertex groups',
        default=True,
        description='After transfer, limit the number of editable bone weights each vertex can participate in; locked groups and Velo special groups will not be involved',
    )
    max_groups_per_vertex: IntProperty(
        name='Maximum number of groups',
        default=4,
        min=1,
        soft_max=8,
        description='Maximum number of editable weight groups that can be retained per vertex',
    )
    normalize_after: BoolProperty(
        name='Perform post-normalization',
        default=True,
        description='After transfer, normalize together with the automatic donor group; the current implementation prioritizes keeping the weights of the newly transferred group and only compresses the donor group to fit the remaining weight space. When transferring different groups of the same object, it will automatically skip to avoid overwriting the source group.',
    )
    show_advanced: BoolProperty(
        name='Advanced Parameters',
        default=False,
        description='Reserved item: The advanced parameter panel is currently displayed as a standalone folding panel',
    )
    robust_max_distance: FloatProperty(
        name='Robust Maximum Distance',
        description='Robust direct match allows maximum world-space distance; target vertices beyond this distance will not be included as direct matches',
        default=0.05,
        min=0.0,
        soft_max=1.0,
        subtype='DISTANCE',
        unit='LENGTH',
    )
    robust_normal_angle: FloatProperty(
        name='Robust Normal Angle',
        description='For Robust direct match, the maximum allowed angle between the interpolated normal of the source face and the target vertex normal. Vertices exceeding this threshold will not be direct matches; if all exceed, Robust will fail. When normal flipping is enabled, nearly 180-degree opposite normals are also accepted.',
        default=math.radians(60.0),
        min=0.0,
        max=math.pi,
        subtype='ANGLE',
        unit='ROTATION',
    )
    robust_flip_normals: BoolProperty(
        name='Allow normal flipping',
        description='Allow nearly 180-degree opposite normals to be considered a match; this effectively disables angle filtering when normal angles reach 90 degrees or more.',
        default=True,
    )
    robust_point_cloud_inpaint: BoolProperty(
        name="Point inpaint",
        default=True,
        description='Use point cloud Laplacian for inpainting; when turned off, use mesh face Laplacian',
    )
    use_deformed_source: BoolProperty(
        name='Use the source deformation result',
        default=True,
        description='When enabled, calculate surface and normals on the evaluated/deformed result of the source mesh',
    )
    use_deformed_target: BoolProperty(
        name='Use the target deformation result',
        default=True,
        description='When enabled, match on the evaluated/deformed result of the target mesh; target topology must match the original mesh',
    )
    limit_dilation_repeat: IntProperty(
        name="Limit dilation",
        default=4,
        min=0,
        soft_max=12,
        description='Number of neighborhood expansion protections for pruned candidates when limiting the number of vertex groups',
    )
    last_report: StringProperty(
        name='Result',
        default="",
        description='The result or error message of the most recent weight transfer',
    )


_classes = (
    VELO_WeightVGName,
    VELO_WeightMirrorMapping,
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
