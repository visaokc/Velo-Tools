"""Static post-assembly checks for WWMI cross-scene INI routing."""
from __future__ import annotations

import re
from pathlib import Path


def _section(text, header):
    m = re.search(r'(^\[' + re.escape(header) + r'\][^\[]*)', text, re.M)
    return m.group(1) if m else None


def _draw_entries(block):
    entries = []
    label = None
    for ln in (block or "").splitlines():
        s = ln.strip()
        if s.startswith("; Draw "):
            label = s.split("; Draw ", 1)[1].strip()
            continue
        m = re.match(r'drawindexed = (\d+), (\d+)', s)
        if m:
            entries.append((int(m.group(1)), int(m.group(2)), label))
            label = None
    return entries


def _body_draw_entries(text, component_id):
    cmd = _section(text, "CommandListDrawComponent%d_ib0" % component_id)
    if cmd:
        return _draw_entries(cmd)
    ovr = _section(text, "TextureOverrideComponent%d_ib0" % component_id)
    return _draw_entries(ovr)


def _select_draws(entries, excluded_labels):
    entries = list(entries or [])
    excluded = {str(label) for label in (excluded_labels or set())}
    if not entries or not excluded:
        return entries
    if not any(label in excluded for _cnt, _off, label in entries):
        return entries[:1]
    return [entry for entry in entries if entry[2] not in excluded]


def _pairs(entries):
    return [(cnt, off) for cnt, off, _label in entries]


def audit_cross_scene_ini(mod_ini_path, routing, roles, *, own_excluded=None, draw_excludes=None):
    """Return a dict with routing errors found in the final namespace-merged INI."""
    path = Path(mod_ini_path)
    if not path.is_file():
        return {"skipped": True, "reason": "mod.ini not written", "errors": []}
    text = path.read_text(encoding="utf-8")
    roles = list(roles or [])
    own_excluded = dict(own_excluded or {})
    draw_excludes = {int(k): set(v or set()) for k, v in (draw_excludes or {}).items()}
    errors = []

    for tag, label in sorted(own_excluded.items()):
        if tag not in roles:
            errors.append("hidden own-buffer %s (%s) missing from assembled roles" % (tag, label))
            continue
        ib = roles.index(tag)
        matched = False
        for m in re.finditer(r'(^\[TextureOverrideComponent\d+(?:LOD\d+)?_ib%d\][^\[]*)' % ib,
                             text, re.M):
            block = m.group(1)
            if not re.search(r'^\s*hash\s*=\s*%s\s*$' % re.escape(tag), block, re.M):
                continue
            matched = True
            if "handling = skip" not in block:
                errors.append("hidden own-buffer %s (%s) does not skip" % (tag, label))
            if "drawindexed" in block:
                errors.append("hidden own-buffer %s (%s) still draws" % (tag, label))
            for run in re.findall(r'^\s*run\s*=\s*(CommandListDrawComponent\d+_ib%d)\s*$' % ib,
                                  block, re.M):
                target = _section(text, run)
                if target and "drawindexed" in target:
                    errors.append("hidden own-buffer %s (%s) still draws via %s" % (tag, label, run))
        if not matched:
            errors.append("hidden own-buffer %s (%s) has no matching skip section" % (tag, label))

    for scene in routing.get("scene_ibs") or []:
        if not scene.get("foldable"):
            continue
        tag = scene.get("ib_hash")
        comp_map = {
            int(k): int(v)
            for k, v in ((scene.get("fold") or {}).get("comp_map") or {}).items()
        }
        for fc, bc in sorted(comp_map.items()):
            header = "TextureOverride_FoldHost_%s_C%d_ib0" % (tag, fc)
            block = _section(text, header)
            body_draws = _body_draw_entries(text, bc)
            if not body_draws:
                if not block:
                    errors.append("%s missing empty skip for excluded base Component %d" % (header, bc))
                    continue
                if "handling = skip" not in block:
                    errors.append("%s does not skip excluded base Component %d" % (header, bc))
                if "drawindexed" in block:
                    errors.append("%s draws even though base Component %d has no draw" % (header, bc))
                continue
            if not block:
                errors.append("%s missing for visible base Component %d" % (header, bc))
                continue
            expected = _pairs(_select_draws(body_draws, draw_excludes.get(bc)))
            actual = _pairs(_draw_entries(block))
            if actual != expected:
                errors.append("%s draw plan mismatch: expected %s, got %s" % (header, expected, actual))

    return {"skipped": False, "errors": errors}
