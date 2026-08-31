
from velo_tools.i18n import iface_
# UI for the WWMI slot-style texture layer (velo-owned, registered like
# embedded/lod/ui.py; nothing added to _wwmi_core's auto_load).
#
#   - VTWW_Settings gains `velo_slot_style_textures` (annotation injection,
#     EFMI-style; restored on unregister) shown in a "Velo 兼容选项" box
#     appended to the stock Export Mod menu (method wrap, original restored).
#   - A standalone helper sub-panel merges extra-form RAW frame dumps into
#     ShaderTextureUsage.json's component-local form_variants blocks and copies
#     the form's textures into the object folder (multi-form characters need one
#     dump per form; see ADR 0006/0007).

import traceback

import bpy

from velo_tools.core.component_selection import apply_bulk_selection

_MISSING = object()
_orig_slot_style_annotation = _MISSING
_orig_asset_name_annotation = _MISSING
_orig_skip_dirty_slot_annotation = _MISSING
_orig_auto_split_annotation = _MISSING
_orig_draw_menu_export_mod = None
_orig_draw_menu_extract_frame_data = None


# ------------------------------------------------------- settings injection --

def _enable_asset_name_export(self, _context):
    if getattr(self, "use_asset_name_matching", False):
        self.velo_slot_style_textures = False


def inject_settings():
    """Adds the export option to VTWW_Settings. Must run BEFORE the vendored
    settings class is registered (same constraint as _patch_tool_mode)."""
    global _orig_slot_style_annotation
    global _orig_asset_name_annotation
    global _orig_skip_dirty_slot_annotation, _orig_auto_split_annotation
    from ..._wwmi_core.addon import settings as _wsettings

    _orig_slot_style_annotation = _wsettings.VTWW_Settings.__annotations__.get(
        "velo_slot_style_textures", _MISSING)
    _orig_asset_name_annotation = (
        _wsettings.VTWW_Settings.__annotations__.get(
            "use_asset_name_matching", _MISSING))
    _orig_skip_dirty_slot_annotation = _wsettings.VTWW_Settings.__annotations__.get(
        "skip_slot_residual_textures", _MISSING)
    _orig_auto_split_annotation = _wsettings.VTWW_Settings.__annotations__.get(
        "velo_auto_split_by_material", _MISSING)
    _wsettings.VTWW_Settings.__annotations__["velo_slot_style_textures"] = bpy.props.BoolProperty(
        name='Slot style texture',
        description=(
            'Use slot rebinding to replace per-hash texture segments: textures are bound by slot within the component draw range, no longer matching the game texture hash, thus immune to texture streaming (from version 3.4 each mip level has an independent hash). Multi-form characters need to first merge dump data per form on the extraction page using "Merge Form Texture Data". If disabled, export follows the original hash style (identical byte-for-byte with un-upgraded versions).'
        ),
        default=False,
    )
    _wsettings.VTWW_Settings.__annotations__["use_asset_name_matching"] = (
        bpy.props.BoolProperty(
            name='Use asset name matching',
            description=(
                'Override the texture Hash in the exported INI with asset path evidence to match_asset_name; the asset path comes from F8 dump and is retained with ShaderTextureUsage.json, mutually exclusive with slot style textures.'
            ),
            default=False,
            update=_enable_asset_name_export,
        ))
    _wsettings.VTWW_Settings.__annotations__["skip_slot_residual_textures"] = bpy.props.BoolProperty(
        name='Texture filtering: skip Dirty Slot',
        description=(
            'Retain slots explicitly bound by PSSetShaderResources in the log.txt, as well as write/consume draws that are color passes, belong to different vb0s, and have the same role cb4 and cb5/cb6 material evidence as cross-object service slot inheritance; Component residues, depth-only, and main material slot inheritance within the same vb0 are still filtered by residue; If there is no available log evidence, legacy STU is retained and deletion is not speculated.'
        ),
        default=True,
    )
    _wsettings.VTWW_Settings.__annotations__["velo_auto_split_by_material"] = bpy.props.BoolProperty(
        name='Automatically split by material during export',
        description='When exporting temporary objects, automatically split according to the actual material with the prefix Component; it will not modify scene objects.',
        default=True,
    )


def restore_settings():
    global _orig_slot_style_annotation
    global _orig_asset_name_annotation
    global _orig_skip_dirty_slot_annotation, _orig_auto_split_annotation
    from ..._wwmi_core.addon import settings as _wsettings

    if _orig_slot_style_annotation is _MISSING:
        _wsettings.VTWW_Settings.__annotations__.pop("velo_slot_style_textures", None)
    else:
        _wsettings.VTWW_Settings.__annotations__["velo_slot_style_textures"] = (
            _orig_slot_style_annotation)
    _orig_slot_style_annotation = _MISSING
    if _orig_asset_name_annotation is _MISSING:
        _wsettings.VTWW_Settings.__annotations__.pop(
            "use_asset_name_matching", None)
    else:
        _wsettings.VTWW_Settings.__annotations__[
            "use_asset_name_matching"
        ] = _orig_asset_name_annotation
    _orig_asset_name_annotation = _MISSING
    if _orig_skip_dirty_slot_annotation is _MISSING:
        _wsettings.VTWW_Settings.__annotations__.pop("skip_slot_residual_textures", None)
    else:
        _wsettings.VTWW_Settings.__annotations__["skip_slot_residual_textures"] = (
            _orig_skip_dirty_slot_annotation)
    _orig_skip_dirty_slot_annotation = _MISSING
    if _orig_auto_split_annotation is _MISSING:
        _wsettings.VTWW_Settings.__annotations__.pop("velo_auto_split_by_material", None)
    else:
        _wsettings.VTWW_Settings.__annotations__["velo_auto_split_by_material"] = (
            _orig_auto_split_annotation)
    _orig_auto_split_annotation = _MISSING


# --------------------------------------------------------- export menu wrap --

def _patch_export_menu():
    global _orig_draw_menu_export_mod
    from ..._wwmi_core.addon import ui as _wui

    if _orig_draw_menu_export_mod is not None:
        return
    _orig_draw_menu_export_mod = _wui.VTWW_PT_SIDEBAR.draw_menu_export_mod

    def draw_menu_export_mod(self, context):
        _orig_draw_menu_export_mod(self, context)
        cfg = context.scene.VTWW_settings
        layout = self.layout
        box = layout.box()
        box.label(text='Velo Compatibility Options', icon="TOOL_SETTINGS")
        if hasattr(cfg, "velo_auto_split_by_material"):
            box.prop(cfg, "velo_auto_split_by_material")
        if hasattr(cfg, "use_asset_name_matching"):
            box.prop(cfg, "use_asset_name_matching")
        if hasattr(cfg, "velo_slot_style_textures"):
            row = box.row()
            row.enabled = not bool(
                getattr(cfg, "use_asset_name_matching", False))
            row.prop(cfg, "velo_slot_style_textures")
            slot_cfg = getattr(context.scene, "vtww_slot_settings", None)
            if (slot_cfg is not None and cfg.velo_slot_style_textures):
                _draw_slot_components(box, slot_cfg)
                box.prop(slot_cfg, "formid_auxiliary_gate")
                if slot_cfg.formid_auxiliary_gate:
                    box.prop(slot_cfg, "form_anchors")
                    _draw_anchor_finder(box, slot_cfg, cfg)

    _wui.VTWW_PT_SIDEBAR.draw_menu_export_mod = draw_menu_export_mod


def _patch_extract_menu():
    global _orig_draw_menu_extract_frame_data
    from ..._wwmi_core.addon import ui as _wui

    if _orig_draw_menu_extract_frame_data is not None:
        return
    _orig_draw_menu_extract_frame_data = _wui.VTWW_PT_SIDEBAR.draw_menu_extract_frame_data

    def draw_menu_extract_frame_data(self, context):
        cfg = context.scene.VTWW_settings
        layout = self.layout

        layout.row()

        row = _wui.add_row_with_error_handler(layout, cfg, 'frame_dump_folder')
        row.prop(cfg, 'frame_dump_folder')

        layout.row().prop(cfg, 'extract_output_folder')

        layout.row()

        col = layout.column(align=True)
        grid = col.grid_flow(columns=2, align=True)
        grid.alignment = 'LEFT'
        grid.prop(cfg, 'skip_small_textures')
        if cfg.skip_small_textures:
            grid.prop(cfg, 'skip_small_textures_size')

        layout.row().prop(cfg, 'skip_jpg_textures')
        layout.row().prop(cfg, 'skip_known_cubemap_textures')
        layout.row().prop(cfg, 'skip_same_slot_hash_textures')
        if hasattr(cfg, "skip_slot_residual_textures"):
            layout.row().prop(cfg, 'skip_slot_residual_textures')

        layout.row()

        layout.row().operator(_wui.VTWW_ExtractFrameData.bl_idname)

    _wui.VTWW_PT_SIDEBAR.draw_menu_extract_frame_data = draw_menu_extract_frame_data


def _restore_extract_menu():
    global _orig_draw_menu_extract_frame_data
    from ..._wwmi_core.addon import ui as _wui

    if _orig_draw_menu_extract_frame_data is not None:
        _wui.VTWW_PT_SIDEBAR.draw_menu_extract_frame_data = _orig_draw_menu_extract_frame_data
        _orig_draw_menu_extract_frame_data = None


def _restore_export_menu():
    global _orig_draw_menu_export_mod
    from ..._wwmi_core.addon import ui as _wui

    if _orig_draw_menu_export_mod is not None:
        _wui.VTWW_PT_SIDEBAR.draw_menu_export_mod = _orig_draw_menu_export_mod
        _orig_draw_menu_export_mod = None


# ------------------------------------------------------- form merge UI ------

class VTWW_AnchorCandidateItem(bpy.types.PropertyGroup):
    """One recommended form-anchor candidate (display only)."""
    vb0: bpy.props.StringProperty(default='')
    form_label: bpy.props.StringProperty(default='')
    shared_textures: bpy.props.IntProperty(default=0)
    shares_character_ps: bpy.props.BoolProperty(default=False)
    min_call_distance: bpy.props.IntProperty(default=0)
    skinned: bpy.props.BoolProperty(default=False)
    index_count: bpy.props.IntProperty(default=0)
    hits: bpy.props.StringProperty(default='')


class VTWW_AnchorFormDump(bpy.types.PropertyGroup):
    """One extra-form dump row of the anchor finder (the extraction blend
    and the mod-making blend are different files, so the finder carries its
    own dump paths instead of relying on the extraction page)."""
    dump_folder: bpy.props.StringProperty(
        name='Form Dump',
        description='The original frame dump directory of this form',
        default='',
        subtype="DIR_PATH",
    )
    form_label: bpy.props.StringProperty(
        name='Tags',
        description='The label of this form (leave blank to automatically use line order form2/form3...; if using a candidate, this label is written to the form anchor field)',
        default='',
    )


class VTWW_SlotComponentRule(bpy.types.PropertyGroup):
    """One component's slot-style eligibility (per-component opt-out). Default checked =
    that component's textures go slot-style; unchecked = it falls back to hash. Populated
    from the source folder's ShaderTextureUsage.json by VTWW_OT_slot_populate_components."""
    component_id: bpy.props.IntProperty(default=0)
    use_slot: bpy.props.BoolProperty(
        name='Slot',
        description="Check = this component's texture uses slot style; uncheck = this component switches to hash style (textures deleted in advance are still managed by the game)",
        default=True,
    )
    texture_count: bpy.props.IntProperty(default=0)


class VTWW_SlotTextureSettings(bpy.types.PropertyGroup):

    form_dump_folder: bpy.props.StringProperty(
        name='Form Frame Dump',
        description='The original frame dump directory of another form (no need to extract the character folder again). Capture frames at close range in this form to ensure full binding of material textures.',
        default='',
        subtype="DIR_PATH",
    )

    form_label: bpy.props.StringProperty(
        name='Morph Tag',
        description="Optional: display name of the form in the merged data (leave blank for automatic numbering). Merging dumps of the same label at different distances will collect other streaming-level hashes of that form's texture (reduces detection delay for form switching); fill in base to collect into base extraction data.",
        default='',
    )

    anchor_candidates: bpy.props.CollectionProperty(type=VTWW_AnchorCandidateItem)
    anchor_status: bpy.props.StringProperty(default='')
    # Base-form dump has NO property of its own: the finder renders the
    # extraction page's frame_dump_folder directly (one fact, one property -
    # the same pattern as object_source_folder on the import/export pages).
    anchor_form_dumps: bpy.props.CollectionProperty(type=VTWW_AnchorFormDump)

    form_anchors: bpy.props.StringProperty(
        name='Morph Anchor',
        description='Optional: manually specify exclusive anchor point hash for a form, format hash:form label, separated by comma/space (e.g., 358cdfe4:base). Base form label is fixed as base, others use labels created during merging. Only two types of values can be used: 8-digit = vb0 hash (value of vb0 in dump file name; ib value is not involved in WWMI matching, invalid), 16-digit = ps hash (vs has the same invalidation risk, do not use). If only one form is left without an anchor (any number of forms), per-frame watchdog is automatically enabled: hit = corresponding form, no hit in full frame = use exclusion method to determine form without anchor, zero-delay switching in all directions; if anchor becomes invalid due to version update, it will automatically revert to texture latching (with data streaming delay).',
        default='',
    )

    formid_auxiliary_gate: bpy.props.BoolProperty(
        name='formid Auxiliary Criterion',
        description=(
            'Optional: append $form_id condition at the end of multi-form branches that can already be safely distinguished by local ps-t slot-layout. Disabled by default to maintain a pure 0hash slot; this option cannot salvage components with identical slot-layout like C0.'
        ),
        default=False,
    )

    # Per-component slot eligibility (UI opt-out). Empty = never populated = all components
    # slot-style (backward compatible). Populate from the source folder, then uncheck the
    # components you want exported hash-style instead.
    slot_component_rules: bpy.props.CollectionProperty(type=VTWW_SlotComponentRule)
    slot_component_rules_index: bpy.props.IntProperty(default=0)


class VTWW_OT_merge_form_textures(bpy.types.Operator):
    bl_idname = "vtww.merge_form_textures"
    bl_label = 'Merge Form Texture Data'
    bl_description = ('Parse the raw frame dump of another form, merge its (component x shader pair x slot) texture table into the form_variants field of the ShaderTextureUsage.json component in the model folder (supports any number of forms), for slot style export to generate per-form branches. Duplicate dumps with the same name will overwrite the old entry')

    def execute(self, context):
        cfg = context.scene.VTWW_settings
        slot_cfg = context.scene.vtww_slot_settings
        try:
            from ..._wwmi_core.migoto_io.blender_interface.utility import resolve_path
            from . import form_merge
            summary = form_merge.merge_form_dump(
                resolve_path(cfg.object_source_folder),
                resolve_path(slot_cfg.form_dump_folder),
                slot_cfg.form_label,
                texture_filter=form_merge.texture_filter_from_cfg(cfg),
            )
        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, iface_('Form merge failed: {0}').format(exc))
            return {'CANCELLED'}

        if summary.get('mode') == 'cross_scene':
            # Source folder is a cross-scene merged root: lifted the form's per-IB textures to
            # the root (hash-style; no per-object match / extra_forms). Re-export to emit them.
            self.report(
                {'INFO'},
                iface_('Cross-scene mode: The texture of the form "{0}" has been lifted into the merge root (overwriting IB {1}, copying in {2} files). Re-export Mod for it to take effect.').format(summary['form_label'], ', '.join(summary['lifted_ibs']) or iface_('(No match)'), summary['textures_copied']))
            return {'FINISHED'}

        action = "覆盖" if summary['replaced'] else "新增"
        harvest = (f"，收割 {summary['variants_added']} 个残留度变体"
                   if summary.get('variants_added') else "")
        self.report(
            {'INFO'},
            iface_('The form "{1}" has been {0} (matched by {2}, {3} component / {4} shader pair, copied {5} maps of this form {6}), currently there are {7} forms in total.').format(action, summary['label'], summary['matched_by'], summary['components'], summary['pairs'], summary['textures_copied'], harvest, summary['total_forms'])
        )
        return {'FINISHED'}


class VTWW_OT_anchor_form_dump_add(bpy.types.Operator):
    bl_idname = "vtww.anchor_form_dump_add"
    bl_label = 'Add Form Dump Row'
    bl_description = 'Add another line of form dump (one dump for each third/fourth form character)'

    def execute(self, context):
        context.scene.vtww_slot_settings.anchor_form_dumps.add()
        return {'FINISHED'}


class VTWW_OT_anchor_form_dump_remove(bpy.types.Operator):
    bl_idname = "vtww.anchor_form_dump_remove"
    bl_label = 'Remove'
    bl_description = 'Remove this line shape dump'

    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        slot_cfg = context.scene.vtww_slot_settings
        if 0 <= self.index < len(slot_cfg.anchor_form_dumps):
            slot_cfg.anchor_form_dumps.remove(self.index)
        return {'FINISHED'}


class VTWW_OT_find_form_anchors(bpy.types.Operator):
    bl_idname = "vtww.find_form_anchors"
    bl_label = 'Find Form Anchors'
    bl_description = ('Compare base shape dump with all completed shape dump rows (exclusive intersection across all shapes), provide top 5 exclusive shape vb0 anchor candidates: sorted by character texture affinity (weighted by fresh binding evidence). Click "Adopt" at the line end to automatically fill the shape anchor field on the export page. "Bone" in the row = skinned piece (more like character-specific piece, more reliable than props/effects), "Scale" = draw index count (mesh size reference)')

    def execute(self, context):
        cfg = context.scene.VTWW_settings
        slot_cfg = context.scene.vtww_slot_settings

        # The base dump IS the extraction page's frame_dump_folder (shared
        # property, rendered in the finder box too). Form rows seed from the
        # merge panel when blank (operator-time, never in draw - writing
        # properties there is forbidden by Blender).
        if not slot_cfg.anchor_form_dumps:
            slot_cfg.anchor_form_dumps.add()
        first = slot_cfg.anchor_form_dumps[0]
        if not first.dump_folder.strip() and slot_cfg.form_dump_folder.strip():
            first.dump_folder = slot_cfg.form_dump_folder
            if not first.form_label.strip():
                first.form_label = slot_cfg.form_label.strip()

        try:
            import json
            from ..._wwmi_core.migoto_io.blender_interface.utility import resolve_path
            from . import anchors, form_merge

            if not cfg.frame_dump_folder.strip():
                raise ValueError("基础形态 dump 未指定——就是提取页的 "
                                 "Frame Dump 目录（与查找区第一个框同值，"
                                 "两处任填一处）")
            form_rows = anchors.resolve_form_rows(
                [(row.dump_folder, row.form_label)
                 for row in slot_cfg.anchor_form_dumps])

            dumps_by_form = {}
            form_labels = {1: 'base'}
            try:
                dumps_by_form[1] = [anchors.load_dump_calls(
                    resolve_path(cfg.frame_dump_folder))]
            except ValueError as exc:
                raise ValueError(f"基础形态 dump：{exc}")
            for offset, (label, folder) in enumerate(form_rows, start=2):
                try:
                    dumps_by_form[offset] = [anchors.load_dump_calls(
                        resolve_path(folder))]
                except ValueError as exc:
                    raise ValueError(f"形态「{label}」dump：{exc}")
                form_labels[offset] = label

            # Character object: ordinary single-IB exports can use Metadata.vb0_hash directly.
            # Cross-scene merged roots cannot: root metadata points at the showcase object,
            # while alternate-form raw dumps may share a routed scene IB instead.
            object_hash = ''
            detect_note = ''
            all_loaded = [d for dumps in dumps_by_form.values()
                          for d in dumps]
            if cfg.object_source_folder.strip():
                source_folder = resolve_path(cfg.object_source_folder)
                manifest_path = source_folder / 'CrossSceneManifest.json'
                legacy_path = source_folder / 'CrossSceneRouting.json'
                if manifest_path.is_file():
                    detected = anchors.detect_object_hash(all_loaded)
                    if not detected:
                        raise ValueError(
                            "dump 里找不到跨全部形态出现的蒙皮多组件对象——"
                            "确认各 dump 抓帧时角色都在画面内（或在对象文件夹"
                            "字段指定提取文件夹精确指定）")
                    from ..crossscene.manifest import load_manifest
                    routing = load_manifest(source_folder)
                    object_hash, note = anchors.choose_cross_scene_object_hash(
                        detected, routing)
                    detect_note = (
                        f"跨场景识别角色: {object_hash}"
                        f"（候选 {len(detected)} 个）") if object_hash else note
                elif legacy_path.is_file():
                    raise ValueError(
                        "检测到旧版 CrossSceneRouting.json schema v2；"
                        "请先重新执行“合并跨场景”")
                else:
                    meta_path = source_folder / 'Metadata.json'
                    if meta_path.is_file():
                        with open(meta_path, encoding='utf-8') as f:
                            object_hash = json.load(f).get('vb0_hash') or ''
            if not object_hash:
                detected = anchors.detect_object_hash(all_loaded)
                if not detected:
                    raise ValueError(
                        "dump 里找不到跨全部形态出现的蒙皮多组件对象——"
                        "确认各 dump 抓帧时角色都在画面内（或在对象文件夹"
                        "字段指定提取文件夹精确指定）")
                top = detected[0]
                object_hash = top.vb0
                detect_note = (f"自动识别角色: {top.vb0}"
                               f"（{top.components} 组件/{top.draws} draws"
                               f"，候选 {len(detected)} 个）")

            candidates = anchors.recommend_anchors(
                dumps_by_form, form_labels, object_hash, top_n=12)
            trusted_written = []
            if cfg.object_source_folder.strip() and candidates:
                source_folder = resolve_path(cfg.object_source_folder)
                top = candidates[0]
                trusted_written.extend(form_merge.write_trusted_form_anchor(
                    source_folder, top.form_label, top.vb0, rank=1))
        except Exception as exc:
            traceback.print_exc()
            slot_cfg.anchor_status = f"查找失败：{exc}"
            self.report({'ERROR'}, iface_('Anchor point not found: {0}').format(exc))
            return {'CANCELLED'}

        slot_cfg.anchor_candidates.clear()
        for cand in candidates:
            item = slot_cfg.anchor_candidates.add()
            item.vb0 = cand.vb0
            item.form_label = cand.form_label
            item.shared_textures = cand.shared_textures
            item.shares_character_ps = cand.shares_character_ps
            item.min_call_distance = cand.min_call_distance
            item.skinned = cand.skinned
            item.index_count = cand.index_count
            # Compact row text; the full per-dump evidence goes to the
            # console (the row would carry whole dump folder names).
            item.hits = f"{sum(cand.hits.values())} draws"
            print(f"[SlotTextures] anchor candidate {cand.vb0} "
                  f"({cand.form_label}): "
                  + ", ".join(f"{n}: {c}" for n, c in cand.hits.items()))
        prefix = f"{detect_note}；" if detect_note else ""
        if candidates:
            trusted_note = (
                f"；已写入 {len(set(trusted_written))} 个 STU 锚点元数据"
                if trusted_written else "")
            slot_cfg.anchor_status = (
                f"{prefix}已确认 {len(candidates)} 个角色形态件（cb 与主体比对）；"
                f"特效/UI 不自动列出，如需作锚点请在游戏确认 vb0 后手动填入"
                f"{trusted_note}")
            self.report({'INFO'}, iface_('Confirm {0} character form anchor points').format(len(candidates)))
        else:
            slot_cfg.anchor_status = (
                f"{prefix}没有 cb 确认的角色形态件；特效/UI 不自动推荐，"
                f"请在游戏确认 vb0 后手动填入形态锚点字段（确认各 dump 分属"
                f"不同形态、角色都在画面内）")
            self.report({'WARNING'}, iface_('No CB-confirmed character shape anchors'))
        return {'FINISHED'}


class VTWW_OT_anchor_candidates_reset(bpy.types.Operator):
    bl_idname = "vtww.anchor_candidates_reset"
    bl_label = 'Reset candidates'
    bl_description = 'Clear Morph Anchor Candidate Results, Do Not Clear Dump Paths or Already Filled Morph Anchors'

    def execute(self, context):
        slot_cfg = context.scene.vtww_slot_settings
        slot_cfg.anchor_candidates.clear()
        slot_cfg.anchor_status = ''
        self.report({'INFO'}, iface_('Morph anchor candidates cleared'))
        return {'FINISHED'}


class VTWW_OT_apply_form_anchor(bpy.types.Operator):
    bl_idname = "vtww.apply_form_anchor"
    bl_label = 'Apply'
    bl_description = "Append the candidate to the export page's morphology anchor field with hash:morphology tag"

    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        slot_cfg = context.scene.vtww_slot_settings
        if not (0 <= self.index < len(slot_cfg.anchor_candidates)):
            self.report({'ERROR'}, iface_('Candidate indexes are invalid; please search again'))
            return {'CANCELLED'}
        item = slot_cfg.anchor_candidates[self.index]
        token = f"{item.vb0}:{item.form_label}"
        existing = slot_cfg.form_anchors.strip()
        if item.vb0 in existing:
            self.report({'WARNING'}, iface_('{0} Already in Morph Anchor Field').format(item.vb0))
            return {'CANCELLED'}
        slot_cfg.form_anchors = f"{existing}, {token}" if existing else token
        self.report({'INFO'}, iface_('Morph anchor {0} adopted').format(token))
        return {'FINISHED'}


def _draw_anchor_finder(layout, slot_cfg, wwmi_cfg):
    """Anchor finder UI, drawn inside the export page's Velo compatibility
    box right under the form-anchors field (anchors are filled right before
    exporting - keeping the whole flow in one place). The base-form dump
    field renders the extraction page's frame_dump_folder DIRECTLY (one
    fact, one property - the object_source_folder pattern), so both pages
    always show the same value with zero sync code."""
    box = layout.box()
    header = box.row(align=True)
    header.label(text='Find Morph Anchor', icon='VIEWZOOM')
    header.operator(VTWW_OT_anchor_candidates_reset.bl_idname,
                    text='', icon='FILE_REFRESH')
    box.row().prop(wwmi_cfg, 'frame_dump_folder', text='Basic form Dump')
    for index, row_item in enumerate(slot_cfg.anchor_form_dumps):
        split = box.row(align=True).split(factor=0.62, align=True)
        split.prop(row_item, 'dump_folder', text='',
                   placeholder="形态 Dump 目录")
        sub = split.row(align=True)
        sub.prop(row_item, 'form_label', text='', placeholder="标签")
        op = sub.operator(VTWW_OT_anchor_form_dump_remove.bl_idname,
                          text='', icon='X')
        op.index = index
    if (not any(r.dump_folder.strip() for r in slot_cfg.anchor_form_dumps)
            and slot_cfg.form_dump_folder.strip()):
        box.row().label(text=iface_('Form-free → Automatically use the merged page form Dump: {0}').format(slot_cfg.form_dump_folder),
                        icon='INFO')
    box.row().operator(VTWW_OT_anchor_form_dump_add.bl_idname,
                       text='Add Form Dump Row', icon='ADD')
    box.row().operator(VTWW_OT_find_form_anchors.bl_idname, icon='VIEWZOOM')
    box.row().label(text='Only list the role form parts confirmed with the main body by cb; special effects/UI need to be manually filled in the above field after vb0 is confirmed in the game.', icon='INFO')
    if slot_cfg.anchor_status:
        box.row().label(text=slot_cfg.anchor_status)
    for index, item in enumerate(slot_cfg.anchor_candidates):
        row = box.row(align=True)
        ps_mark = " ps" if item.shares_character_ps else ""
        skin_mark = " 骨" if item.skinned else ""
        row.label(text=(iface_('{0}  {1}  Texture ×{2}{3}{4}  Distance {5}  Scale {6}  {7}').format(item.vb0, item.form_label, item.shared_textures, ps_mark, skin_mark, item.min_call_distance, item.index_count, item.hits)))
        # Fixed-width apply button: the label gets every extra pixel when
        # the sidebar is widened (an evenly-split row let the button grow
        # without bound while the data got truncated).
        btn = row.row(align=True)
        btn.ui_units_x = 4
        op = btn.operator(VTWW_OT_apply_form_anchor.bl_idname,
                          text='Apply', icon='IMPORT')
        op.index = index


class VELO_PT_wwmi_slot_forms(bpy.types.Panel):
    bl_idname = "VELO_PT_wwmi_slot_forms"
    bl_label = 'Form Texture Merge'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Velo Tools"
    bl_parent_id = "VELO_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        # Same gate as the other velo-owned WWMI helper panels. The anchor
        # finder lives on the EXPORT page's Velo compatibility box.
        vt = getattr(context.scene, "velo_tools", None)
        if (vt is None
                or getattr(vt, "active_tab", "") != 'GAME'
                or getattr(vt, "active_game", "") != 'WUTHERING'):
            return False
        wwmi_cfg = getattr(context.scene, "VTWW_settings", None)
        return wwmi_cfg is not None

    def draw(self, context):
        layout = self.layout
        cfg = context.scene.vtww_slot_settings
        wwmi_cfg = context.scene.VTWW_settings

        layout.row().prop(cfg, 'form_dump_folder')
        layout.row().prop(wwmi_cfg, 'object_source_folder')
        layout.row().prop(cfg, 'form_label')

        layout.separator()
        layout.row().operator(VTWW_OT_merge_form_textures.bl_idname, icon='TEXTURE')


class VTWW_UL_slot_components(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_prop, index=0):
        row = layout.row(align=True)
        row.prop(item, "use_slot", text="")
        row.label(text=f"Component {item.component_id}")
        row.label(text=iface_('{0} Texture').format(item.texture_count))


class VTWW_OT_slot_populate_components(bpy.types.Operator):
    bl_idname = "vtww.slot_populate_components"
    bl_label = 'List components'
    bl_description = ('List all components from the ShaderTextureUsage.json in the object source folder for individual selection; all are selected by default (= all use slot style). Unchecked components switch to hash style, and textures deleted in advance are still taken over by the game. Please relist after changing the source folder.')

    def execute(self, context):
        cfg = context.scene.VTWW_settings
        slot_cfg = context.scene.vtww_slot_settings
        try:
            from ..._wwmi_core.migoto_io.blender_interface.utility import resolve_path
            from . import generator
            if not cfg.object_source_folder.strip():
                raise ValueError("未指定对象源文件夹")
            forms, _info, _warn = generator.load_forms(
                resolve_path(cfg.object_source_folder))
        except Exception as exc:
            traceback.print_exc()
            self.report({'ERROR'}, iface_('Failed to list components: {0}').format(exc))
            return {'CANCELLED'}

        # distinct texture hashes per component, across base + extra forms
        per_comp = {}
        for _label, form_data in forms:
            for comp_id, comp_pairs in form_data.items():
                bucket = per_comp.setdefault(comp_id, set())
                for pair_map in comp_pairs.values():
                    for h in pair_map.values():
                        if h:
                            bucket.add(h)
        if not per_comp:
            self.report({'WARNING'}, iface_('No component texture record in ShaderTextureUsage.json'))
            return {'CANCELLED'}

        # Preserve the user's prior checked state on refresh; new components default checked.
        prev = {r.component_id: r.use_slot for r in slot_cfg.slot_component_rules}
        slot_cfg.slot_component_rules.clear()
        for comp_id in sorted(per_comp):
            item = slot_cfg.slot_component_rules.add()
            item.component_id = comp_id
            item.use_slot = prev.get(comp_id, True)
            item.texture_count = len(per_comp[comp_id])
        self.report({'INFO'},
                    iface_('List {0} components (default uses all slots, uncheck to use hash)').format(len(per_comp)))
        return {'FINISHED'}


class VTWW_OT_slot_select_components(bpy.types.Operator):
    bl_idname = "vtww.slot_select_components"
    bl_label = "Change Component selection"
    bl_description = "Apply a bulk selection operation to the listed Components"

    action: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        slot_cfg = context.scene.vtww_slot_settings
        if not apply_bulk_selection(slot_cfg.slot_component_rules, self.action):
            self.report(
                {'WARNING'},
                iface_('Check at least two Components to define the range endpoints'),
            )
            return {'CANCELLED'}
        return {'FINISHED'}


def _draw_slot_components(box, slot_cfg):
    sub = box.box()
    sub.label(text='Select slot style by component', icon="TEXTURE")
    row = sub.row()
    row.template_list("VTWW_UL_slot_components", "", slot_cfg,
                      "slot_component_rules", slot_cfg,
                      "slot_component_rules_index", rows=4)
    col = row.column(align=True)
    col.operator("vtww.slot_populate_components", text="", icon='FILE_REFRESH')
    controls = sub.row(align=True)
    controls.enabled = bool(len(slot_cfg.slot_component_rules))
    for text, action in (
        ('Select All', 'SELECT_ALL'),
        ('Select None', 'SELECT_NONE'),
        ('Invert Selection', 'INVERT'),
        ('Fill Range', 'FILL_RANGE'),
    ):
        operator = controls.operator(
            VTWW_OT_slot_select_components.bl_idname,
            text=iface_(text),
        )
        operator.action = action
    if not len(slot_cfg.slot_component_rules):
        sub.label(text='Not listed: defaults to using all slots. After listing points from the source folder, components can be individually deselected',
                  icon='INFO')
    else:
        sub.label(text='Unchecked components use hash; prematurely deleted textures belong to the game', icon='INFO')


_CLASSES = (
    VTWW_AnchorCandidateItem,
    VTWW_AnchorFormDump,
    VTWW_SlotComponentRule,
    VTWW_SlotTextureSettings,
    VTWW_OT_merge_form_textures,
    VTWW_OT_anchor_form_dump_add,
    VTWW_OT_anchor_form_dump_remove,
    VTWW_OT_find_form_anchors,
    VTWW_OT_anchor_candidates_reset,
    VTWW_OT_apply_form_anchor,
    VTWW_UL_slot_components,
    VTWW_OT_slot_populate_components,
    VTWW_OT_slot_select_components,
    VELO_PT_wwmi_slot_forms,
)


def register():
    for cls in _CLASSES:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            traceback.print_exc()
    bpy.types.Scene.vtww_slot_settings = bpy.props.PointerProperty(type=VTWW_SlotTextureSettings)
    _patch_export_menu()
    _patch_extract_menu()


def unregister():
    _restore_extract_menu()
    _restore_export_menu()
    if hasattr(bpy.types.Scene, "vtww_slot_settings"):
        try:
            del bpy.types.Scene.vtww_slot_settings
        except Exception:
            pass
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
