# Export hook: wraps IniMaker.build_from_template (same idempotent
# install()/remove() pattern as embedded/lod/export_hook.py; _wwmi_core
# untouched). Installed AFTER the LOD hook so this wrapper is outermost and
# post-processes whatever template the inner layers rendered (stock merged,
# stock per-component or the velo LOD fork - transform.py is anchor-based and
# template-agnostic).
#
# The inner render runs with with_checksum=False; the checksum stamp is
# re-applied here after the transformation so IniMaker.is_ini_edited keeps
# protecting user-edited mod.ini files from silent overwrites.
#
# Degrade policy: SlotStyleDegrade aborts the export with a clear explanation.
# The concise slot layer must not silently fall back to hash-style output,
# because that can re-enable stale slot pollution.

import json
import re
import traceback

from ..._wwmi_core.blender_export import ini_maker as _im_module
from ..._wwmi_core.migoto_io.blender_interface.utility import resolve_path

from . import dds_meta
from . import generator
from . import transform

_INSTALLED = False
_ORIG_BUILD_FROM_TEMPLATE = None

# Last export's report lines (operators may surface them to the UI).
last_report = []


def install():
    global _INSTALLED, _ORIG_BUILD_FROM_TEMPLATE
    if _INSTALLED:
        return

    _ORIG_BUILD_FROM_TEMPLATE = _im_module.IniMaker.build_from_template

    def _wrapped_build_from_template(self, context, cfg, template_string=None, with_checksum=False):
        if (not getattr(cfg, 'velo_slot_style_textures', False)
                or getattr(cfg, 'use_custom_template', False)
                or getattr(cfg, 'custom_template_live_update', False)):
            return _ORIG_BUILD_FROM_TEMPLATE(self, context, cfg,
                                             template_string=template_string,
                                             with_checksum=with_checksum)

        result = _ORIG_BUILD_FROM_TEMPLATE(self, context, cfg,
                                           template_string=template_string,
                                           with_checksum=False)
        del last_report[:]
        try:
            source_folder = resolve_path(cfg.object_source_folder)
            # Cross-scene (CrossSceneRouting.json present) is now supported: the body export reads the
            # merged root STU (orchestrator trims it to the base components first) and each sub-IB
            # exports its own slot layer; the assembler keeps them per-IB. No degrade here anymore.
            form_freshness = []
            forms, texture_info, load_warnings = generator.load_forms(
                source_folder, freshness_out=form_freshness)
            textures = [(texture.hash, f'ResourceTexture{index}')
                        for index, texture in enumerate(self.textures)]
            if not texture_info:
                # Legacy-schema json (no recorded formats): best-effort DDS
                # read of the model-folder files. Risk (reported): the author
                # may have re-saved a texture in a different format than the
                # game original the conditions must match.
                for texture in self.textures:
                    meta = dds_meta.read_dds_meta(texture.path)
                    if meta is not None and meta.format:
                        texture_info[texture.hash] = {
                            'format': meta.format, 'width': meta.width,
                            'height': meta.height}
                load_warnings.append(
                    'legacy ShaderTextureUsage.json without recorded formats - '
                    'formats were read from the model-folder files instead; '
                    're-extract to record the original game formats')
            manual_anchors = _parse_form_anchors(context, forms, load_warnings)
            manual_anchors = _auto_form_anchors_from_stu(
                source_folder, forms, manual_anchors, load_warnings)
            plan = generator.build_plan(
                forms, textures, texture_info, load_warnings,
                component_ranges=transform.extract_component_ranges(result),
                lod_ranges=_read_lod_ranges(source_folder),
                manual_anchors=manual_anchors,
                freshness=form_freshness,
                slot_eligible_components=_read_slot_eligible(context))
            result = transform.apply(result, plan)
            for anchor_hash, form_id in manual_anchors:
                kind = 'shader (ps)' if len(anchor_hash) == 16 else 'resource (vb0)'
                _report(f'[SlotTextures] form anchor {anchor_hash} ({kind}) -> '
                        f'form "{forms[form_id - 1][0]}"')
            if plan.stats.get('anchor_watchdog'):
                _report(f'[SlotTextures] anchor watchdog active: a frame without '
                        f'an anchor hit commits form '
                        f'"{forms[plan.default_form_id - 1][0]}" - both switch '
                        f'directions are zero-latency')
            for warning in plan.warnings:
                _report(f'[SlotTextures] WARNING: {warning}')
            for tex_hash, section in plan.blind_zone:
                _report(f'[SlotTextures] WARNING: texture {tex_hash} not present in any '
                        f'form map - stock hash section [{section}] kept as fallback')
            for tex_hash, section in plan.phantom_suppressed:
                _report(f'[SlotTextures] WARNING: texture {tex_hash} only appears in '
                        f'stale-inherited phantom pairs - stock hash section '
                        f'[{section}] suppressed')
            if plan.format_diagnostics:
                fmt = plan.format_diagnostics
                _report(
                    '[SlotTextures] Format tag sections: '
                    f'raw={fmt.get("format_sections_raw", 0)}, '
                    f'unique={fmt.get("format_sections_unique", 0)}, '
                    f'removed={fmt.get("format_sections_removed", 0)} '
                    '(single emitted format member kept; remaining copies are '
                    'component/LOD/FoldHost range scope)')
                _report('[SlotTextures] Format tag summary: ' + json.dumps(
                    fmt.get('format_sections_summary', {}),
                    sort_keys=True))
            _report(f'[SlotTextures] Slot-style texture layer applied: {plan.stats}')
        except generator.SlotStyleDegrade as e:
            _report(f'[SlotTextures] ERROR: slot-style export aborted: {e}')
            raise
        except Exception:
            traceback.print_exc()
            _report('[SlotTextures] ERROR: unexpected slot-style export failure.')
            raise

        if with_checksum:
            result = _im_module.IniMaker.with_checksum(result)
        self.ini_string = result
        return result

    _wrapped_build_from_template._velo_slot_hook = True
    _im_module.IniMaker.build_from_template = _wrapped_build_from_template
    _INSTALLED = True


def remove():
    global _INSTALLED, _ORIG_BUILD_FROM_TEMPLATE
    if not _INSTALLED:
        return
    _im_module.IniMaker.build_from_template = _ORIG_BUILD_FROM_TEMPLATE
    _ORIG_BUILD_FROM_TEMPLATE = None
    _INSTALLED = False


def _parse_form_anchors(context, forms, warnings):
    """USER-SPECIFIED form anchors from the slot settings: "hash:formlabel"
    tokens (comma/space separated). Labels: 'base' for the base extraction,
    else the labels given in the form-merge panel. Returns
    [(hash, form_id)]; malformed tokens are skipped with a warning."""
    try:
        spec = (context.scene.vtww_slot_settings.form_anchors or '').strip()
    except Exception:
        return []
    if not spec:
        return []
    labels = {label.strip().lower(): form_id
              for form_id, (label, _) in enumerate(forms, start=1)}
    anchors = []
    for token in re.split(r'[\s,;]+', spec):
        if not token:
            continue
        if ':' not in token:
            warnings.append(
                f'form anchor "{token}" skipped (expected hash:formlabel)')
            continue
        anchor_hash, label = token.rsplit(':', 1)
        form_id = labels.get(label.strip().lower())
        if form_id is None:
            known = ', '.join(label for label, _ in forms)
            warnings.append(
                f'form anchor "{token}" skipped (unknown form label '
                f'"{label}"; known labels: {known})')
            continue
        anchors.append((anchor_hash.strip().lower(), form_id))
    return anchors


def _valid_resource_anchor(value):
    return re.fullmatch(r'[0-9a-f]{8}', str(value or '').strip().lower()) is not None


def _form_label(value):
    return str(value or '').strip().lower()


def _crossscene_editable_vb0_hash(source_folder, rec):
    anchor_hash = str((rec or {}).get('vb0_hash') or '').strip().lower()
    if _valid_resource_anchor(anchor_hash):
        return anchor_hash
    rel = str((rec or {}).get('source_folder') or '').strip()
    if not rel:
        return ''
    meta_path = source_folder / rel / 'Metadata.json'
    if not meta_path.is_file():
        return ''
    try:
        with open(meta_path, encoding='utf-8') as f:
            meta = json.load(f) or {}
    except Exception:
        return ''
    anchor_hash = str(meta.get('vb0_hash') or '').strip().lower()
    return anchor_hash if _valid_resource_anchor(anchor_hash) else ''


def _auto_form_anchors_from_crossscene_editable(source_folder, forms, anchors, warnings):
    routing_path = source_folder / 'CrossSceneRouting.json'
    if not routing_path.is_file():
        return None
    try:
        with open(routing_path, encoding='utf-8') as f:
            routing = json.load(f) or {}
    except Exception as exc:
        warnings.append(f'cross-scene routing unreadable; automatic form anchors skipped: {exc}')
        return list(anchors)

    editable = []
    editable_count = 0
    for rec in routing.get('editable_ibs') or []:
        if not isinstance(rec, dict):
            continue
        editable_count += 1
        anchor_hash = _crossscene_editable_vb0_hash(source_folder, rec)
        if anchor_hash:
            editable.append((rec, anchor_hash))
    if not editable_count:
        warnings.append(
            'cross-scene routing has no editable IB; automatic form anchors skipped')
        return list(anchors)
    if not editable:
        warnings.append(
            'cross-scene editable IBs have no VB0 hash evidence; automatic form anchors skipped')
        return list(anchors)

    covered_forms = {form_id for _anchor_hash, form_id in anchors}
    seen = set(anchors)
    out = list(anchors)
    form_ids = {
        label.strip().lower(): form_id
        for form_id, (label, _) in enumerate(forms, start=1)
    }
    unused = []
    for rec, anchor_hash in editable:
        label = _form_label(rec.get('label') or rec.get('source') or rec.get('form_label'))
        form_id = form_ids.get(label)
        if form_id in covered_forms or form_id == 1:
            continue
        if form_id is None:
            unused.append((rec, anchor_hash))
            continue
        pair = (anchor_hash, form_id)
        if pair not in seen:
            out.append(pair)
            seen.add(pair)
            covered_forms.add(form_id)
            warnings.append(
                f'auto form anchor {anchor_hash}:{forms[form_id - 1][0]} '
                'from CrossSceneRouting editable IB')

    remaining = [
        form_id for form_id in range(2, len(forms) + 1)
        if form_id not in covered_forms
    ]
    if len(remaining) == 1 and unused:
        form_id = remaining[0]
        anchor_hash = unused[0][1]
        pair = (anchor_hash, form_id)
        if pair not in seen:
            out.append(pair)
            warnings.append(
                f'auto form anchor {anchor_hash}:{forms[form_id - 1][0]} '
                'from CrossSceneRouting editable IB')
    elif len(remaining) == len(unused):
        for form_id, (_rec, anchor_hash) in zip(remaining, unused):
            pair = (anchor_hash, form_id)
            if pair in seen:
                continue
            out.append(pair)
            seen.add(pair)
            warnings.append(
                f'auto form anchor {anchor_hash}:{forms[form_id - 1][0]} '
                'from CrossSceneRouting editable IB')
    elif remaining:
        warnings.append(
            'cross-scene editable IB count does not unambiguously match uncovered forms; '
            'manual form anchors are required')
    return out


def _auto_form_anchors_from_stu(source_folder, forms, anchors, warnings):
    """Add one STU-recorded vb0 anchor for extra forms not covered manually."""
    if len(forms) <= 1:
        return anchors
    routed = _auto_form_anchors_from_crossscene_editable(
        source_folder, forms, anchors, warnings)
    if routed is not None:
        return routed
    labels = {label.strip().lower(): form_id
              for form_id, (label, _) in enumerate(forms, start=1)}
    covered_forms = {form_id for _anchor_hash, form_id in anchors}
    seen = set(anchors)
    try:
        with open(source_folder / generator.constants.BASE_USAGE_FILENAME,
                  encoding='utf-8') as f:
            raw = json.load(f)
    except Exception:
        return anchors
    out = list(anchors)
    for entry in raw.get(generator.constants.EXTRA_FORMS_KEY) or []:
        if not isinstance(entry, dict):
            continue
        label = entry.get('label') or entry.get('source')
        form_id = labels.get(str(label or '').strip().lower())
        if form_id is None or form_id in covered_forms:
            continue
        anchor_hash = str(entry.get('vb0_hash') or '').strip().lower()
        if not re.fullmatch(r'[0-9a-f]{8}', anchor_hash):
            continue
        pair = (anchor_hash, form_id)
        if pair in seen:
            continue
        out.append(pair)
        seen.add(pair)
        covered_forms.add(form_id)
        warnings.append(
            f'auto form anchor {anchor_hash}:{forms[form_id - 1][0]} '
            f'from {generator.constants.BASE_USAGE_FILENAME}')
    return out


def _read_lod_ranges(source_folder):
    """lod level -> comp_id -> (index_offset, index_count) from the velo lods
    metadata. LOD draws use the LOD object's component index ranges; the
    generator emits a fuzzy format-tag twin per level so the slot conditions
    keep working at LOD distance. Empty when the object has no lods data."""
    lod_ranges = {}
    meta_path = source_folder / 'Metadata.json'
    if not meta_path.is_file():
        return lod_ranges
    try:
        with open(meta_path, encoding='utf-8') as f:
            metadata = json.load(f)
        for comp_id, component in enumerate(metadata.get('components') or []):
            for level, entry in enumerate(component.get('lods') or [], start=1):
                first = entry.get('index_offset')
                count = entry.get('index_count')
                if first is not None and count is not None:
                    lod_ranges.setdefault(level, {})[comp_id] = (first, count)
    except Exception:
        traceback.print_exc()
    return lod_ranges


def _report(message: str):
    print(message)
    last_report.append(message)


# Per-export slot-eligibility override (cross-scene). The cross-scene orchestrator runs N sub-exports,
# each with a DIFFERENT local component numbering, so the global UI rules (chosen in MERGED numbering)
# must be translated and injected per sub-export. cfg is a Blender PropertyGroup (no arbitrary
# attributes), so the channel is module-level: set_eligible_override(value) makes the next
# build_from_template use `value` verbatim (a set of eligible LOCAL component ids, or None = all
# eligible); clear_eligible_override() restores the global-UI-rules behavior.
_eligible_override = None
_eligible_override_active = False


def set_eligible_override(value):
    global _eligible_override, _eligible_override_active
    _eligible_override, _eligible_override_active = value, True


def clear_eligible_override():
    global _eligible_override_active
    _eligible_override_active = False


def read_global_eligible(context):
    """Per-component slot eligibility from the UI rules (MERGED numbering under cross-scene). Empty
    list (never populated by the user) -> None = all components eligible (backward compatible /
    默认全选). Otherwise the set of component ids the user left checked; unchecked -> hash fallback."""
    try:
        rules = context.scene.vtww_slot_settings.slot_component_rules
    except Exception:
        return None
    if not len(rules):
        return None
    return {r.component_id for r in rules if r.use_slot}


def _read_slot_eligible(context):
    """The eligibility the generator should use for THIS export: the per-export override the
    cross-scene orchestrator set (if any), else the global UI rules."""
    if _eligible_override_active:
        return _eligible_override
    return read_global_eligible(context)
