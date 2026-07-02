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

_MISSING = object()
_orig_slot_style_annotation = _MISSING
_orig_skip_dirty_slot_annotation = _MISSING
_orig_draw_menu_export_mod = None
_orig_draw_menu_extract_frame_data = None


# ------------------------------------------------------- settings injection --

def inject_settings():
    """Adds the export option to VTWW_Settings. Must run BEFORE the vendored
    settings class is registered (same constraint as _patch_tool_mode)."""
    global _orig_slot_style_annotation, _orig_skip_dirty_slot_annotation
    from ..._wwmi_core.addon import settings as _wsettings

    _orig_slot_style_annotation = _wsettings.VTWW_Settings.__annotations__.get(
        "velo_slot_style_textures", _MISSING)
    _orig_skip_dirty_slot_annotation = _wsettings.VTWW_Settings.__annotations__.get(
        "skip_slot_residual_textures", _MISSING)
    _wsettings.VTWW_Settings.__annotations__["velo_slot_style_textures"] = bpy.props.BoolProperty(
        name="插槽风格贴图",
        description=(
            "用槽位重绑替代逐 hash 贴图段：贴图在组件 draw 范围内按槽位绑定，"
            "不再匹配游戏贴图 hash，因此对纹理流送（3.4 起每个 mip 级独立 hash）免疫。"
            "多形态角色需先在提取页用「合并形态贴图数据」逐形态合并 dump。"
            "关闭则按原版 hash 风格导出（与未升级版本逐字节一致）"
        ),
        default=False,
    )
    _wsettings.VTWW_Settings.__annotations__["skip_slot_residual_textures"] = bpy.props.BoolProperty(
        name="贴图过滤：跳过 Dirty Slot",
        description=(
            "仅保留 log.txt 中由 PSSetShaderResources 明确绑定过的 ps-t 槽位；"
            "没有可用 log 证据时保持 legacy STU，不猜测删除。"
        ),
        default=True,
    )


def restore_settings():
    global _orig_slot_style_annotation, _orig_skip_dirty_slot_annotation
    from ..._wwmi_core.addon import settings as _wsettings

    if _orig_slot_style_annotation is _MISSING:
        _wsettings.VTWW_Settings.__annotations__.pop("velo_slot_style_textures", None)
    else:
        _wsettings.VTWW_Settings.__annotations__["velo_slot_style_textures"] = (
            _orig_slot_style_annotation)
    _orig_slot_style_annotation = _MISSING
    if _orig_skip_dirty_slot_annotation is _MISSING:
        _wsettings.VTWW_Settings.__annotations__.pop("skip_slot_residual_textures", None)
    else:
        _wsettings.VTWW_Settings.__annotations__["skip_slot_residual_textures"] = (
            _orig_skip_dirty_slot_annotation)
    _orig_skip_dirty_slot_annotation = _MISSING


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
        box.label(text="Velo 兼容选项", icon="TOOL_SETTINGS")
        if hasattr(cfg, "velo_slot_style_textures"):
            box.prop(cfg, "velo_slot_style_textures")
            slot_cfg = getattr(context.scene, "vtww_slot_settings", None)
            if (slot_cfg is not None and cfg.velo_slot_style_textures):
                box.prop(slot_cfg, "slot_audit_dump_folder")
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
        name="形态 Dump",
        description="该形态的原始帧转储目录",
        default='',
        subtype="DIR_PATH",
    )
    form_label: bpy.props.StringProperty(
        name="标签",
        description="该形态的标签（留空按行序自动 form2/form3...；"
                    "采用候选时写入形态锚点字段的就是这个标签）",
        default='',
    )


class VTWW_SlotComponentRule(bpy.types.PropertyGroup):
    """One component's slot-style eligibility (per-component opt-out). Default checked =
    that component's textures go slot-style; unchecked = it falls back to hash. Populated
    from the source folder's ShaderTextureUsage.json by VTWW_OT_slot_populate_components."""
    component_id: bpy.props.IntProperty(default=0)
    use_slot: bpy.props.BoolProperty(
        name="插槽",
        description="勾选=该组件贴图走插槽风格；取消=该组件改走 hash 风格"
                    "（提前删掉的贴图仍由游戏接管）",
        default=True,
    )
    texture_count: bpy.props.IntProperty(default=0)


class VTWW_SlotTextureSettings(bpy.types.PropertyGroup):

    form_dump_folder: bpy.props.StringProperty(
        name="形态 Frame Dump",
        description="另一形态的原始帧转储目录（无需二次提取角色文件夹）。"
                    "在该形态下近距离抓帧，保证材质贴图全量绑定",
        default='',
        subtype="DIR_PATH",
    )

    form_label: bpy.props.StringProperty(
        name="形态标签",
        description="可选：该形态在合并数据里的显示名（留空自动编号）。"
                    "同一标签再合并不同距离的 dump 会收割该形态贴图的其它流送级 "
                    "hash（缩短形态切换的检测延迟）；填 base 表示收割进基础提取数据",
        default='',
    )

    anchor_candidates: bpy.props.CollectionProperty(type=VTWW_AnchorCandidateItem)
    anchor_status: bpy.props.StringProperty(default='')
    # Base-form dump has NO property of its own: the finder renders the
    # extraction page's frame_dump_folder directly (one fact, one property -
    # the same pattern as object_source_folder on the import/export pages).
    anchor_form_dumps: bpy.props.CollectionProperty(type=VTWW_AnchorFormDump)

    form_anchors: bpy.props.StringProperty(
        name="形态锚点",
        description="可选：手动指定形态独占的锚点 hash，格式 hash:形态标签，"
                    "逗号/空格分隔（如 358cdfe4:base）。基础形态标签固定为 base，"
                    "其余用合并时起的标签。只能用两类值：8 位 = vb0 hash"
                    "（dump 文件名里 vb0= 的值；ib 值不参与 WWMI 匹配、无效），"
                    "16 位 = ps hash（vs 有同样失效风险，勿用）。恰好只剩一个"
                    "形态没有锚点时（任意形态数）自动启用每帧看门狗：命中=对应"
                    "形态，整帧无命中=排除法判定无锚形态，全方向零延迟切换；"
                    "锚点因版本更新失效后自动退回贴图锁存（有流送延迟）",
        default='',
    )

    formid_auxiliary_gate: bpy.props.BoolProperty(
        name="formid 辅助判据",
        description=(
            "可选：在已经能用本地 ps-t slot-layout 安全区分的多形态分支末尾追加 $form_id 条件。"
            "默认关闭以保持纯 0hash slot；该选项不能挽救 C0 这类 slot-layout 完全相同的组件。"
        ),
        default=False,
    )

    slot_audit_dump_folder: bpy.props.StringProperty(
        name="Raw 审计 Dump",
        description=(
            "可选：指定一份真实 FrameAnalysis raw dump。跨场景 slot-style 导出会确认该 dump "
            "里 fresh 绑定过的 pass 已被当前 ShaderTextureUsage.json 覆盖；未覆盖则阻断导出。"
        ),
        subtype='DIR_PATH',
        default='',
    )

    # Per-component slot eligibility (UI opt-out). Empty = never populated = all components
    # slot-style (backward compatible). Populate from the source folder, then uncheck the
    # components you want exported hash-style instead.
    slot_component_rules: bpy.props.CollectionProperty(type=VTWW_SlotComponentRule)
    slot_component_rules_index: bpy.props.IntProperty(default=0)


class VTWW_OT_merge_form_textures(bpy.types.Operator):
    bl_idname = "vtww.merge_form_textures"
    bl_label = "合并形态贴图数据"
    bl_description = ("解析另一形态的原始帧转储，把它的 (组件 x 着色器对 x 槽位) 贴图表"
                      "合并进模型文件夹 ShaderTextureUsage.json 的组件 form_variants 字段"
                      "（支持任意数量形态），供插槽风格导出生成 per-form 分支。"
                      "同名 dump 重复合并会覆盖旧条目")

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
            self.report({'ERROR'}, f"形态合并失败：{exc}")
            return {'CANCELLED'}

        if summary.get('mode') == 'cross_scene':
            # Source folder is a cross-scene merged root: lifted the form's per-IB textures to
            # the root (hash-style; no per-object match / extra_forms). Re-export to emit them.
            self.report(
                {'INFO'},
                f"跨场景模式：已把形态「{summary['form_label']}」的贴图抬入合并根"
                f"（覆盖 IB {', '.join(summary['lifted_ibs']) or '（无匹配）'}，"
                f"拷入 {summary['textures_copied']} 张）。重新导出 Mod 即生效。")
            return {'FINISHED'}

        action = "覆盖" if summary['replaced'] else "新增"
        harvest = (f"，收割 {summary['variants_added']} 个残留度变体"
                   if summary.get('variants_added') else "")
        self.report(
            {'INFO'},
            f"已{action}形态「{summary['label']}」（按 {summary['matched_by']} 匹配，"
            f"{summary['components']} 组件 / {summary['pairs']} 着色器对，"
            f"拷入 {summary['textures_copied']} 张该形态贴图{harvest}），"
            f"当前共 {summary['total_forms']} 个形态。"
        )
        return {'FINISHED'}


class VTWW_OT_anchor_form_dump_add(bpy.types.Operator):
    bl_idname = "vtww.anchor_form_dump_add"
    bl_label = "添加形态 Dump 行"
    bl_description = "再加一行形态 dump（第三/第四形态角色逐形态各一份 dump）"

    def execute(self, context):
        context.scene.vtww_slot_settings.anchor_form_dumps.add()
        return {'FINISHED'}


class VTWW_OT_anchor_form_dump_remove(bpy.types.Operator):
    bl_idname = "vtww.anchor_form_dump_remove"
    bl_label = "移除"
    bl_description = "移除这一行形态 dump"

    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        slot_cfg = context.scene.vtww_slot_settings
        if 0 <= self.index < len(slot_cfg.anchor_form_dumps):
            slot_cfg.anchor_form_dumps.remove(self.index)
        return {'FINISHED'}


class VTWW_OT_find_form_anchors(bpy.types.Operator):
    bl_idname = "vtww.find_form_anchors"
    bl_label = "查找形态锚点"
    bl_description = ("对比基础形态 dump 与全部已填的形态 dump 行（跨全部形态做"
                      "独占交集），给出 top5 形态独占 vb0 锚点候选：按角色贴图"
                      "亲和度排序（新鲜绑定证据加权），点行尾「采用」自动填入"
                      "导出页的形态锚点字段。行内「骨」=skinned 蒙皮件（更像"
                      "角色专属件、比场景道具/特效更可靠）、「规模」=draw 索引数"
                      "（网格大小参考）")

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
                routing_path = source_folder / 'CrossSceneRouting.json'
                if routing_path.is_file():
                    detected = anchors.detect_object_hash(all_loaded)
                    if not detected:
                        raise ValueError(
                            "dump 里找不到跨全部形态出现的蒙皮多组件对象——"
                            "确认各 dump 抓帧时角色都在画面内（或在对象文件夹"
                            "字段指定提取文件夹精确指定）")
                    with open(routing_path, encoding='utf-8') as f:
                        routing = json.load(f)
                    object_hash, note = anchors.choose_cross_scene_object_hash(
                        detected, routing)
                    detect_note = (
                        f"跨场景识别角色: {object_hash}"
                        f"（候选 {len(detected)} 个）") if object_hash else note
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
            self.report({'ERROR'}, f"锚点查找失败：{exc}")
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
            self.report({'INFO'}, f"确认 {len(candidates)} 个角色形态锚点")
        else:
            slot_cfg.anchor_status = (
                f"{prefix}没有 cb 确认的角色形态件；特效/UI 不自动推荐，"
                f"请在游戏确认 vb0 后手动填入形态锚点字段（确认各 dump 分属"
                f"不同形态、角色都在画面内）")
            self.report({'WARNING'}, "没有 cb 确认的角色形态锚点")
        return {'FINISHED'}


class VTWW_OT_anchor_candidates_reset(bpy.types.Operator):
    bl_idname = "vtww.anchor_candidates_reset"
    bl_label = "重置候选"
    bl_description = "清空形态锚点候选结果，不清空 Dump 路径或已填写的形态锚点"

    def execute(self, context):
        slot_cfg = context.scene.vtww_slot_settings
        slot_cfg.anchor_candidates.clear()
        slot_cfg.anchor_status = ''
        self.report({'INFO'}, "已清空形态锚点候选")
        return {'FINISHED'}


class VTWW_OT_apply_form_anchor(bpy.types.Operator):
    bl_idname = "vtww.apply_form_anchor"
    bl_label = "采用"
    bl_description = "把该候选以 hash:形态标签 追加进导出页的形态锚点字段"

    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        slot_cfg = context.scene.vtww_slot_settings
        if not (0 <= self.index < len(slot_cfg.anchor_candidates)):
            self.report({'ERROR'}, "候选索引无效，请重新查找")
            return {'CANCELLED'}
        item = slot_cfg.anchor_candidates[self.index]
        token = f"{item.vb0}:{item.form_label}"
        existing = slot_cfg.form_anchors.strip()
        if item.vb0 in existing:
            self.report({'WARNING'}, f"{item.vb0} 已在形态锚点字段里")
            return {'CANCELLED'}
        slot_cfg.form_anchors = f"{existing}, {token}" if existing else token
        self.report({'INFO'}, f"已采用形态锚点 {token}")
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
    header.label(text="形态锚点查找", icon='VIEWZOOM')
    header.operator(VTWW_OT_anchor_candidates_reset.bl_idname,
                    text='', icon='FILE_REFRESH')
    box.row().prop(wwmi_cfg, 'frame_dump_folder', text="基础形态 Dump")
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
        box.row().label(text=f"形态行空 → 自动用合并页形态 Dump: "
                             f"{slot_cfg.form_dump_folder}",
                        icon='INFO')
    box.row().operator(VTWW_OT_anchor_form_dump_add.bl_idname,
                       text="添加形态 Dump 行", icon='ADD')
    box.row().operator(VTWW_OT_find_form_anchors.bl_idname, icon='VIEWZOOM')
    box.row().label(text="只列出 cb 与主体确认的角色形态件；特效/UI 需"
                         "在游戏确认 vb0 后手动填入上方字段", icon='INFO')
    if slot_cfg.anchor_status:
        box.row().label(text=slot_cfg.anchor_status)
    for index, item in enumerate(slot_cfg.anchor_candidates):
        row = box.row(align=True)
        ps_mark = " ps" if item.shares_character_ps else ""
        skin_mark = " 骨" if item.skinned else ""
        row.label(text=(f"{item.vb0}  {item.form_label}  "
                        f"贴图×{item.shared_textures}{ps_mark}{skin_mark}  "
                        f"距{item.min_call_distance}  规模{item.index_count}  "
                        f"{item.hits}"))
        # Fixed-width apply button: the label gets every extra pixel when
        # the sidebar is widened (an evenly-split row let the button grow
        # without bound while the data got truncated).
        btn = row.row(align=True)
        btn.ui_units_x = 4
        op = btn.operator(VTWW_OT_apply_form_anchor.bl_idname,
                          text="采用", icon='IMPORT')
        op.index = index


class VELO_PT_wwmi_slot_forms(bpy.types.Panel):
    bl_idname = "VELO_PT_wwmi_slot_forms"
    bl_label = "形态贴图合并"
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
        row.label(text=f"{item.texture_count} 贴图")


class VTWW_OT_slot_populate_components(bpy.types.Operator):
    bl_idname = "vtww.slot_populate_components"
    bl_label = "列出组件"
    bl_description = ("从对象源文件夹的 ShaderTextureUsage.json 列出所有组件供逐个勾选；"
                      "默认全部勾选（= 全部走插槽风格）。取消勾选的组件改走 hash 风格，"
                      "提前删掉的贴图仍由游戏接管。改了源文件夹后请重新列出")

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
            self.report({'ERROR'}, f"列出组件失败：{exc}")
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
            self.report({'WARNING'}, "ShaderTextureUsage.json 里没有组件贴图记录")
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
                    f"列出 {len(per_comp)} 个组件（默认全部走插槽，取消勾选改走 hash）")
        return {'FINISHED'}


def _draw_slot_components(box, slot_cfg):
    sub = box.box()
    sub.label(text="按组件选插槽风格", icon="TEXTURE")
    row = sub.row()
    row.template_list("VTWW_UL_slot_components", "", slot_cfg,
                      "slot_component_rules", slot_cfg,
                      "slot_component_rules_index", rows=4)
    col = row.column(align=True)
    col.operator("vtww.slot_populate_components", text="", icon='FILE_REFRESH')
    if not len(slot_cfg.slot_component_rules):
        sub.label(text="未列出：默认全部走插槽。点刷新从源文件夹列出后可逐组件取消",
                  icon='INFO')
    else:
        sub.label(text="取消勾选的组件改走 hash；提前删掉的贴图归游戏", icon='INFO')


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
