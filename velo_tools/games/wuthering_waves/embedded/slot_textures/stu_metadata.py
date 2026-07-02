"""Helpers for user-editable ShaderTextureUsage metadata."""

import json
import re
from copy import deepcopy
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


def _is_component_key(key):
    return re.match(r'^Component\s+\d+$', str(key)) is not None


def _component_metadata_keys():
    return {
        constants.FORM_COMPONENT_MODE_KEY,
        constants.COMPONENT_SOURCES_KEY,
        constants.FORM_VARIANTS_KEY,
        *constants.LEGACY_FORM_COMPONENT_MODE_KEYS,
    }


def _variant_metadata_keys():
    return {
        'label',
        'source',
        'matched_by',
        'vb0_hash',
        constants.FORM_ANCHOR_LABEL_KEY,
        constants.FORM_ANCHOR_VB0_KEY,
        constants.FORM_ANCHOR_SOURCE_KEY,
        constants.FORM_ANCHOR_RANK_KEY,
        constants.COMPONENT_SOURCES_KEY,
    }


def _normalize_sources(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _merge_source_list(block, values):
    values = _normalize_sources(values)
    if not isinstance(block, dict) or not values:
        return
    bucket = _normalize_sources(block.get(constants.COMPONENT_SOURCES_KEY))
    for value in values:
        if value not in bucket:
            bucket.append(value)
    if bucket:
        block[constants.COMPONENT_SOURCES_KEY] = bucket


def _merge_component_block(dst, src):
    if not isinstance(dst, dict) or not isinstance(src, dict):
        return
    for key, value in src.items():
        if key in _variant_metadata_keys() or key in _component_metadata_keys():
            continue
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _merge_component_block(dst[key], value)
        else:
            dst[key] = deepcopy(value)


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
    for comp_name in component_ids_in_usage(usage):
        block = usage.get(component_key(comp_name))
        if not isinstance(block, dict):
            continue
        variants = block.get(constants.FORM_VARIANTS_KEY)
        if not isinstance(variants, dict):
            continue
        for label, variant in variants.items():
            if not isinstance(variant, dict):
                continue
            add(label, variant.get(constants.FORM_ANCHOR_VB0_KEY))
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
        if _is_component_key(key):
            ids.append(_component_sort_key(key))
    return sorted(set(ids))


def _variant_record(label, entry, component_block):
    variant = OrderedDict()
    label = str(label or '').strip().lower()
    if label:
        variant['label'] = label
    for key in ('source', 'matched_by', 'vb0_hash'):
        value = entry.get(key) if isinstance(entry, dict) else None
        if value not in (None, ''):
            variant[key] = value
    for key in (
            constants.FORM_ANCHOR_LABEL_KEY,
            constants.FORM_ANCHOR_VB0_KEY,
            constants.FORM_ANCHOR_SOURCE_KEY,
            constants.FORM_ANCHOR_RANK_KEY):
        value = entry.get(key) if isinstance(entry, dict) else None
        if value not in (None, ''):
            variant[key] = value
    if isinstance(entry, dict):
        sources = (entry.get(constants.COMPONENT_SOURCES_KEY)
                   or entry.get(constants.LOCAL_COMPONENT_SOURCES_KEY))
        if isinstance(sources, dict):
            comp_sources = sources.get(component_block[0])
            if comp_sources:
                variant[constants.COMPONENT_SOURCES_KEY] = _normalize_sources(
                    comp_sources)
        elif isinstance(component_block[1], dict):
            comp_sources = component_block[1].get(constants.COMPONENT_SOURCES_KEY)
            if comp_sources:
                variant[constants.COMPONENT_SOURCES_KEY] = _normalize_sources(
                    comp_sources)
    _merge_component_block(variant, component_block[1])
    return variant


def migrate_extra_forms_to_component_variants(usage):
    if not isinstance(usage, dict):
        return usage
    forms = usage.get(constants.EXTRA_FORMS_KEY)
    if not isinstance(forms, list):
        return usage
    for index, entry in enumerate(forms):
        if not isinstance(entry, dict):
            continue
        components = entry.get('components')
        if not isinstance(components, dict):
            continue
        label = _entry_label(entry) or f'form{index + 2}'
        for comp_name, comp_block in components.items():
            if not _is_component_key(comp_name) or not isinstance(comp_block, dict):
                continue
            target = usage.setdefault(comp_name, OrderedDict())
            if not isinstance(target, dict):
                continue
            variants = target.setdefault(constants.FORM_VARIANTS_KEY, OrderedDict())
            if not isinstance(variants, dict):
                variants = OrderedDict()
                target[constants.FORM_VARIANTS_KEY] = variants
            variant = variants.get(label)
            if not isinstance(variant, dict):
                variant = OrderedDict()
                variants[label] = variant
            _merge_component_block(
                variant, _variant_record(label, entry, (comp_name, comp_block)))
            for key in ('label', 'source', 'matched_by', 'vb0_hash',
                        constants.FORM_ANCHOR_LABEL_KEY,
                        constants.FORM_ANCHOR_VB0_KEY,
                        constants.FORM_ANCHOR_SOURCE_KEY,
                        constants.FORM_ANCHOR_RANK_KEY,
                        constants.COMPONENT_SOURCES_KEY):
                value = _variant_record(label, entry, (comp_name, comp_block)).get(key)
                if value not in (None, '', []):
                    variant[key] = value
    return usage


def migrate_local_component_sources(usage):
    if not isinstance(usage, dict):
        return usage
    sources = usage.get(constants.LOCAL_COMPONENT_SOURCES_KEY)
    if not isinstance(sources, dict):
        return usage
    for comp_name, values in sources.items():
        if not _is_component_key(comp_name):
            continue
        block = usage.get(comp_name)
        if isinstance(block, dict):
            _merge_source_list(block, values)
    return usage


def canonicalize_lean_usage(usage):
    if not isinstance(usage, dict):
        return usage
    sync_form_anchors_field(usage)
    migrate_local_component_sources(usage)
    migrate_extra_forms_to_component_variants(usage)
    sync_form_component_modes(usage)
    for key in (
            constants.EXTRA_FORMS_KEY,
            constants.LOCAL_FORM_DISCRIMINATOR_KEY,
            constants.LOCAL_COMPONENT_SOURCES_KEY,
            constants.FORM_ANCHOR_LABEL_KEY,
            constants.FORM_ANCHOR_VB0_KEY,
            constants.FORM_ANCHOR_SOURCE_KEY,
            constants.FORM_ANCHOR_RANK_KEY,
            'form_component_modes'):
        usage.pop(key, None)
    for comp_id in component_ids_in_usage(usage):
        block = usage.get(component_key(comp_id))
        if not isinstance(block, dict):
            continue
        for legacy_key in constants.LEGACY_FORM_COMPONENT_MODE_KEYS:
            block.pop(legacy_key, None)
        variants = block.get(constants.FORM_VARIANTS_KEY)
        if block.get(constants.FORM_COMPONENT_MODE_KEY) != 'multi':
            block.pop(constants.FORM_VARIANTS_KEY, None)
            continue
        if isinstance(variants, dict):
            for label, variant in list(variants.items()):
                if not isinstance(variant, dict):
                    variants.pop(label, None)
                    continue
                variant.pop(constants.FORM_ANCHOR_LABEL_KEY, None)
                variant.pop(constants.FORM_ANCHOR_VB0_KEY, None)
                variant.pop(constants.FORM_ANCHOR_SOURCE_KEY, None)
                variant.pop(constants.FORM_ANCHOR_RANK_KEY, None)
                if 'label' in variant:
                    variant.pop('label', None)
            if not variants:
                block.pop(constants.FORM_VARIANTS_KEY, None)
    return usage


def _form_entry_by_label(entries, label):
    label = str(label or '').strip().lower()
    for entry in entries:
        if _entry_label(entry) == label:
            return entry
    entry = OrderedDict((('label', label), ('components', OrderedDict())))
    entries.append(entry)
    return entry


def form_entries(usage):
    """Return legacy-shaped form entries from component-local variants.

    Old top-level extra_forms are folded in for read compatibility, but callers
    should write through canonicalize_lean_usage/write_usage.
    """
    entries = []
    seen_components = set()
    if not isinstance(usage, dict):
        return entries
    for comp_id in component_ids_in_usage(usage):
        comp_name = component_key(comp_id)
        block = usage.get(comp_name)
        if not isinstance(block, dict):
            continue
        variants = block.get(constants.FORM_VARIANTS_KEY)
        if not isinstance(variants, dict):
            continue
        for label, variant in variants.items():
            if not isinstance(variant, dict):
                continue
            label = str(label or '').strip().lower()
            if not label:
                continue
            entry = _form_entry_by_label(entries, label)
            for key in ('source', 'matched_by', 'vb0_hash'):
                if key not in entry and variant.get(key) not in (None, ''):
                    entry[key] = variant.get(key)
            entry['components'][comp_name] = variant
            seen_components.add((label, comp_name))
    forms = usage.get(constants.EXTRA_FORMS_KEY)
    if isinstance(forms, list):
        for old_entry in forms:
            if not isinstance(old_entry, dict):
                continue
            label = _entry_label(old_entry) or f'form{len(entries) + 2}'
            entry = _form_entry_by_label(entries, label)
            for key, value in old_entry.items():
                if key != 'components' and key not in entry:
                    entry[key] = value
            components = old_entry.get('components')
            if not isinstance(components, dict):
                continue
            for comp_name, block in components.items():
                if (label, comp_name) not in seen_components:
                    entry['components'][comp_name] = block
    return entries


def _ordered_component_block(block):
    if not isinstance(block, dict):
        return block
    ordered = OrderedDict()
    if constants.FORM_COMPONENT_MODE_KEY in block:
        ordered[constants.FORM_COMPONENT_MODE_KEY] = block[
            constants.FORM_COMPONENT_MODE_KEY]
    if constants.COMPONENT_SOURCES_KEY in block:
        ordered[constants.COMPONENT_SOURCES_KEY] = block[
            constants.COMPONENT_SOURCES_KEY]
    for key, value in block.items():
        if key == constants.FORM_VARIANTS_KEY:
            continue
        if key not in ordered:
            ordered[key] = value
    if constants.FORM_VARIANTS_KEY in block:
        variants = block[constants.FORM_VARIANTS_KEY]
        if isinstance(variants, dict) and variants:
            ordered[constants.FORM_VARIANTS_KEY] = OrderedDict(
                (label, _ordered_variant_block(variant))
                for label, variant in sorted(variants.items()))
    return ordered


def _ordered_variant_block(block):
    if not isinstance(block, dict):
        return block
    ordered = OrderedDict()
    for key in ('source', 'matched_by', 'vb0_hash',
                constants.COMPONENT_SOURCES_KEY):
        if key in block:
            ordered[key] = block[key]
    for key, value in block.items():
        if key not in ordered and key not in _variant_metadata_keys():
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
    for comp_id in component_ids_in_usage(usage):
        block = usage.get(component_key(comp_id))
        if not isinstance(block, dict):
            continue
        variants = block.get(constants.FORM_VARIANTS_KEY)
        if isinstance(variants, dict) and variants:
            inferred_multi.add(comp_id)
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
    canonicalize_lean_usage(usage)
    ordered = OrderedDict()
    for key in (
            'version',
            constants.FORM_ANCHORS_KEY):
        if key in usage:
            ordered[key] = usage[key]
    for key in sorted(
            (key for key in usage if _is_component_key(key)),
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
