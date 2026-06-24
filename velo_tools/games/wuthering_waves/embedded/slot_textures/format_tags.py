"""Format-family tag diagnostics and safe dedupe helpers."""

import re

from typing import Dict, List, Optional, Tuple

_SECTION_RE = re.compile(r'^\[([^\]]+)\]\s*$')
_BASE_RE = re.compile(
    r'^TextureOverrideComponent(?P<component>\d+)(?P<format>[A-Z0-9_]+)'
    r'(?P<suffix>(?:_ib\d+)*)$')
_LOD_RE = re.compile(
    r'^TextureOverrideLod(?P<level>\d+)Component(?P<component>\d+)'
    r'(?P<format>[A-Z0-9_]+)(?P<suffix>(?:_ib\d+)*)$')
_FOLD_RE = re.compile(
    r'^TextureOverride_FoldHost_[0-9a-fA-F]+_C(?P<component>\d+)_'
    r'(?P<format>[A-Z0-9_]+)(?P<suffix>(?:_ib\d+)*)$')
_FIELD_RE = re.compile(
    r'^\s*(match_first_index|match_index_count|match_priority|match_format|filter_index)'
    r'\s*=\s*(.*?)\s*$',
    re.I)
_FIELD_ORDER = (
    'match_first_index',
    'match_index_count',
    'match_priority',
    'match_format',
    'filter_index',
)


def _section_spans(lines: List[str]) -> List[Tuple[str, int, int]]:
    headers = [(m.group(1), i) for i, line in enumerate(lines)
               if (m := _SECTION_RE.match(line))]
    spans = []
    for pos, (name, start) in enumerate(headers):
        end = headers[pos + 1][1] if pos + 1 < len(headers) else len(lines)
        spans.append((name, start, end))
    return spans


def _parse_format_header(name: str) -> Optional[Dict[str, str]]:
    for kind, rx in (('lod', _LOD_RE), ('base', _BASE_RE), ('foldhost', _FOLD_RE)):
        m = rx.match(name)
        if not m:
            continue
        suffix = m.group('suffix') or ''
        ibs = re.findall(r'_ib(\d+)', suffix)
        return {
            'kind': kind,
            'component': 'C' + m.group('component'),
            'header_format': m.group('format'),
            'ib': 'ib' + ibs[-1] if ibs else 'local',
        }
    return None


def _body_key(body: List[str], header_format: str) -> Optional[Tuple[str, ...]]:
    values: Dict[str, str] = {}
    for line in body:
        stripped = line.strip()
        if not stripped or stripped.startswith(';'):
            continue
        m = _FIELD_RE.match(line)
        if not m:
            return None
        key = m.group(1).lower()
        if key in values:
            return None
        values[key] = m.group(2)
    if any(key not in values for key in _FIELD_ORDER):
        return None
    if values['match_format'].upper() != header_format.upper():
        return None
    return tuple(values[key] for key in _FIELD_ORDER)


def _bump(summary: Dict[str, Dict[str, Dict[str, int]]],
          bucket: str,
          value: str,
          field: str) -> None:
    entry = summary.setdefault(bucket, {}).setdefault(
        value, {'raw': 0, 'unique': 0, 'removed': 0})
    entry[field] += 1


def _finalize_summary(summary: Dict[str, Dict[str, Dict[str, int]]]) -> None:
    for bucket in summary.values():
        for entry in bucket.values():
            entry['unique'] = entry['raw'] - entry['removed']


def dedupe_format_tag_sections(ini_text: str) -> Tuple[str, Dict[str, object]]:
    """Remove duplicate generated format marker sections with identical keys."""
    lines = ini_text.split('\n')
    seen = set()
    deleted = set()
    raw = 0
    removed = 0
    summary: Dict[str, Dict[str, Dict[str, int]]] = {
        'by_ib': {},
        'by_component': {},
        'by_family': {},
        'by_kind': {},
    }

    for name, start, end in _section_spans(lines):
        meta = _parse_format_header(name.strip())
        if meta is None:
            continue
        key = _body_key(lines[start + 1:end], meta['header_format'])
        if key is None:
            continue
        raw += 1
        family = key[3]
        for bucket, value in (
                ('by_ib', meta['ib']),
                ('by_component', meta['component']),
                ('by_family', family),
                ('by_kind', meta['kind'])):
            _bump(summary, bucket, value, 'raw')

        if key in seen:
            removed += 1
            deleted.update(range(start, end))
            for bucket, value in (
                    ('by_ib', meta['ib']),
                    ('by_component', meta['component']),
                    ('by_family', family),
                    ('by_kind', meta['kind'])):
                _bump(summary, bucket, value, 'removed')
            continue
        seen.add(key)

    _finalize_summary(summary)
    stats = {
        'format_sections_raw': raw,
        'format_sections_unique': raw - removed,
        'format_sections_removed': removed,
        'format_sections_summary': summary,
    }
    if not deleted:
        return ini_text, stats

    out = [line for i, line in enumerate(lines) if i not in deleted]
    result = '\n'.join(out)
    if ini_text.endswith('\n') and not result.endswith('\n'):
        result += '\n'
    return result, stats
