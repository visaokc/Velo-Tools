"""Helpers for user-editable ShaderTextureUsage metadata."""

import json
import re
from collections import OrderedDict
from pathlib import Path

from . import constants

_ANCHOR_TOKEN_RE = re.compile(
    r'(?P<hash>[0-9a-fA-F]{8})\s*:\s*(?P<label>[^,\s;]+)')


def _valid_hash8(value):
    text = str(value or '').strip().lower()
    return len(text) == 8 and all(c in '0123456789abcdef' for c in text)


def normalize_form_anchors(value):
    pairs = []
    seen = set()
    for match in _ANCHOR_TOKEN_RE.finditer(str(value or '')):
        anchor_hash = match.group('hash').lower()
        label = match.group('label').strip().lower()
        if not label:
            continue
        key = (anchor_hash, label)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return ', '.join(f'{anchor_hash}:{label}' for anchor_hash, label in pairs)


def parse_form_anchors(value):
    return [(label, anchor_hash)
            for anchor_hash, label in (
                (h, l) for h, l in _ANCHOR_TOKEN_RE.findall(str(value or '')))
            if _valid_hash8(anchor_hash)]


def _entry_label(entry):
    return str((entry or {}).get('label') or (entry or {}).get('source')
               or (entry or {}).get('form_label') or '').strip().lower()


def collect_anchor_pairs(usage):
    pairs = []
    seen = set()

    def add(label, anchor_hash):
        label = str(label or '').strip().lower()
        anchor_hash = str(anchor_hash or '').strip().lower()
        if not label or not _valid_hash8(anchor_hash):
            return
        key = (label, anchor_hash)
        if key in seen:
            return
        seen.add(key)
        pairs.append(key)

    if not isinstance(usage, dict):
        return pairs
    for label, anchor_hash in parse_form_anchors(
            usage.get(constants.FORM_ANCHORS_KEY)):
        add(label, anchor_hash)
    label = str(usage.get(constants.FORM_ANCHOR_LABEL_KEY) or '').strip().lower()
    anchor_hash = str(usage.get(constants.FORM_ANCHOR_VB0_KEY) or '').strip().lower()
    if not label and _valid_hash8(anchor_hash):
        label = 'base'
    add(label, anchor_hash)
    forms = usage.get(constants.EXTRA_FORMS_KEY)
    if isinstance(forms, list):
        for entry in forms:
            if not isinstance(entry, dict):
                continue
            add(_entry_label(entry), entry.get(constants.FORM_ANCHOR_VB0_KEY))
    return pairs


def sync_form_anchors_field(usage):
    if not isinstance(usage, dict):
        return usage
    existing = normalize_form_anchors(usage.get(constants.FORM_ANCHORS_KEY))
    pairs = collect_anchor_pairs(usage)
    if existing:
        for label, anchor_hash in parse_form_anchors(existing):
            key = (label, anchor_hash)
            if key not in pairs:
                pairs.append(key)
    if pairs:
        usage[constants.FORM_ANCHORS_KEY] = ', '.join(
            f'{anchor_hash}:{label}' for label, anchor_hash in pairs)
    else:
        usage.pop(constants.FORM_ANCHORS_KEY, None)
    return usage


def component_key(component_id):
    return f'Component {int(component_id)}'


def _component_sort_key(key):
    return int(str(key).rsplit(' ', 1)[-1])


def component_ids_in_usage(usage):
    if not isinstance(usage, dict):
        return []
    ids = []
    for key in usage:
        if re.match(r'^Component\s+\d+$', str(key)):
            ids.append(_component_sort_key(key))
    return sorted(set(ids))


def _ordered_component_block(block):
    if not isinstance(block, dict):
        return block
    ordered = OrderedDict()
    if constants.FORM_COMPONENT_MODE_KEY in block:
        ordered[constants.FORM_COMPONENT_MODE_KEY] = block[
            constants.FORM_COMPONENT_MODE_KEY]
    for key, value in block.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def sync_form_component_modes(usage, multi_components=None):
    if not isinstance(usage, dict):
        return usage
    multi_components = {int(c) for c in (multi_components or [])}
    inferred_multi = set()
    forms = usage.get(constants.EXTRA_FORMS_KEY)
    if isinstance(forms, list):
        for entry in forms:
            if not isinstance(entry, dict):
                continue
            components = entry.get('components')
            if isinstance(components, dict):
                inferred_multi.update(component_ids_in_usage(components))
    legacy_modes = usage.pop('form_component_modes', None)
    if not isinstance(legacy_modes, dict):
        legacy_modes = {}
    normalized = {}
    for comp_id in component_ids_in_usage(usage):
        key = component_key(comp_id)
        block = usage.get(key)
        if not isinstance(block, dict):
            continue
        legacy_value = None
        for legacy_key in constants.LEGACY_FORM_COMPONENT_MODE_KEYS:
            if legacy_key in block:
                legacy_value = block.pop(legacy_key)
        explicit = (
            constants.FORM_COMPONENT_MODE_KEY in block
            or legacy_value is not None
            or key in legacy_modes)
        current = str(block.get(constants.FORM_COMPONENT_MODE_KEY)
                      if constants.FORM_COMPONENT_MODE_KEY in block
                      else legacy_value
                      if legacy_value is not None
                      else legacy_modes.get(key) or '').strip().lower()
        normalized[key] = 'multi' if (
            comp_id in multi_components
            or current == 'multi'
            or (not explicit and comp_id in inferred_multi)
        ) else 'single'
    for key, mode in normalized.items():
        block = usage.get(key)
        if isinstance(block, dict):
            block[constants.FORM_COMPONENT_MODE_KEY] = mode
    forms = usage.get(constants.EXTRA_FORMS_KEY)
    if isinstance(forms, list):
        for entry in forms:
            if not isinstance(entry, dict):
                continue
            components = entry.get('components')
            if not isinstance(components, dict):
                continue
            for comp_id in component_ids_in_usage(components):
                key = component_key(comp_id)
                block = components.get(key)
                if isinstance(block, dict):
                    for legacy_key in constants.LEGACY_FORM_COMPONENT_MODE_KEYS:
                        block.pop(legacy_key, None)
                    block[constants.FORM_COMPONENT_MODE_KEY] = (
                        normalized.get(key)
                        or ('multi' if comp_id in multi_components else 'single'))
    return usage


def ordered_usage(usage):
    if not isinstance(usage, dict):
        return usage
    sync_form_anchors_field(usage)
    sync_form_component_modes(usage)
    ordered = OrderedDict()
    for key in (
            'version',
            constants.FORM_ANCHORS_KEY,
            constants.FORM_ANCHOR_LABEL_KEY,
            constants.FORM_ANCHOR_VB0_KEY,
            constants.FORM_ANCHOR_SOURCE_KEY,
            constants.FORM_ANCHOR_RANK_KEY,
            constants.LOCAL_COMPONENT_SOURCES_KEY,
            constants.LOCAL_FORM_DISCRIMINATOR_KEY):
        if key in usage:
            ordered[key] = usage[key]
    for key in sorted(
            (key for key in usage if re.match(r'^Component\s+\d+$', str(key))),
            key=_component_sort_key):
        ordered[key] = _ordered_component_block(usage[key])
    for key, value in usage.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def dumps_usage(usage, *, indent=4):
    return json.dumps(ordered_usage(usage), indent=indent, ensure_ascii=False)


def write_usage(path, usage, *, indent=4):
    Path(path).write_text(dumps_usage(usage, indent=indent), encoding='utf-8')
