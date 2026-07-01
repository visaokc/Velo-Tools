"""Static post-assembly checks for WWMI cross-scene INI routing."""
from __future__ import annotations

import re
import importlib.util
import sys
import types
from pathlib import Path

try:
    from ..slot_textures import constants as _slot_constants
    from ..slot_textures import dds_meta as _dds_meta
except ImportError:  # pragma: no cover - direct import fallback for pure tests
    try:
        from velo_tools.games.wuthering_waves.embedded.slot_textures import constants as _slot_constants
        from velo_tools.games.wuthering_waves.embedded.slot_textures import dds_meta as _dds_meta
    except ImportError:
        _slot_pkg = "_velo_audit_slot_textures"
        _slot_dir = Path(__file__).resolve().parents[1] / "slot_textures"
        _pkg = types.ModuleType(_slot_pkg)
        _pkg.__path__ = [str(_slot_dir)]
        sys.modules.setdefault(_slot_pkg, _pkg)
        try:
            _spec = importlib.util.spec_from_file_location(
                _slot_pkg + ".constants", _slot_dir / "constants.py")
            _slot_constants = importlib.util.module_from_spec(_spec)
            sys.modules[_slot_pkg + ".constants"] = _slot_constants
            assert _spec and _spec.loader
            _spec.loader.exec_module(_slot_constants)
            _spec = importlib.util.spec_from_file_location(
                _slot_pkg + ".dds_meta", _slot_dir / "dds_meta.py")
            _dds_meta = importlib.util.module_from_spec(_spec)
            sys.modules[_slot_pkg + ".dds_meta"] = _dds_meta
            assert _spec and _spec.loader
            _spec.loader.exec_module(_dds_meta)
        except Exception:
            _slot_constants = None
            _dds_meta = None


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


def _resource_filenames(text):
    resources = {}
    for m in re.finditer(r'(^\[(ResourceTexture[^\]]*)\][^\[]*)', text, re.M):
        name = m.group(2)
        fm = re.search(r'^\s*filename\s*=\s*(.+?)\s*$', m.group(1), re.M)
        if fm:
            resources[name] = fm.group(1).strip()
    return resources


def _condition_families(lines, index):
    families = {}
    for prev in range(index - 1, -1, -1):
        stripped = lines[prev].strip()
        if stripped.startswith("["):
            break
        if not stripped.startswith(("if ", "else if ")):
            continue
        for slot, value in re.findall(
                r'\bps-t(\d+)\s*==\s*([0-9.]+)', stripped):
            try:
                families[int(slot)] = float(value)
            except ValueError:
                pass
        break
    return families


def _audit_slot_resources(text, mod_dir):
    errors = []
    resources = _resource_filenames(text)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.search(
            r'\bps-t(\d+)\s*=\s*ref\s+(ResourceTexture[0-9A-Za-z_]+)\b',
            line)
        if not match:
            continue
        slot = int(match.group(1))
        resource = match.group(2)
        filename = resources.get(resource)
        if filename is None:
            errors.append(
                "slot assignment ps-t%d references %s without Resource section"
                % (slot, resource))
            continue
        path = Path(mod_dir) / filename
        if not path.is_file():
            errors.append(
                "slot assignment ps-t%d references %s missing file %s"
                % (slot, resource, filename))
            continue
        if _slot_constants is None or _dds_meta is None:
            continue
        expected = _condition_families(lines, index).get(slot)
        if expected is None:
            continue
        meta = _dds_meta.read_dds_meta(path)
        if meta is None or not meta.format:
            errors.append(
                "slot assignment ps-t%d references %s with unreadable DDS format %s"
                % (slot, resource, filename))
            continue
        actual = _slot_constants.format_filter_index(meta.format)
        if abs(float(expected) - float(actual)) > 0.0000001:
            errors.append(
                "slot assignment ps-t%d references %s format %s (%s) but "
                "condition expects %s"
                % (slot, resource, meta.format, actual, expected))
    return errors


def _audit_body_hash_fallbacks(text, allowed_body_hash_fallbacks=None):
    errors = []
    allowed = {str(h).lower() for h in (allowed_body_hash_fallbacks or set())}
    resources = {}
    for match in re.finditer(
            r'(^\[(Resource_Texture_([0-9a-fA-F]{8}))\][^\[]*)',
            text, re.M):
        filename = re.search(
            r'^\s*filename\s*=\s*(.+?)\s*$',
            match.group(1), re.M)
        if filename:
            resources[match.group(3).lower()] = filename.group(1).strip()
    for match in re.finditer(
            r'(^\[TextureOverride_Texture_([0-9a-fA-F]{8})\][^\[]*)',
            text, re.M):
        tex_hash = match.group(2).lower()
        if tex_hash in allowed:
            continue
        block = match.group(1)
        if "$object_detected_ib0" not in block:
            continue
        filename = resources.get(tex_hash, "")
        normalized = filename.replace("\\", "/")
        if re.search(r'(^|/)Textures/Components-\d+\s+t=[0-9a-fA-F]{8}\.dds$',
                     normalized):
            errors.append(
                "body slot-owned texture %s is emitted as hash fallback %s; "
                "slot-style export requires ps-t assignment or fail-closed"
                % (tex_hash, filename))
    return errors


def audit_cross_scene_ini(mod_ini_path, routing, roles, *, own_excluded=None, draw_excludes=None,
                          allowed_body_hash_fallbacks=None):
    """Return a dict with routing errors found in the final namespace-merged INI."""
    path = Path(mod_ini_path)
    if not path.is_file():
        return {"skipped": True, "reason": "mod.ini not written", "errors": []}
    text = path.read_text(encoding="utf-8")
    roles = list(roles or [])
    own_excluded = dict(own_excluded or {})
    draw_excludes = {int(k): set(v or set()) for k, v in (draw_excludes or {}).items()}
    errors = []
    errors.extend(_audit_slot_resources(text, path.parent))
    errors.extend(_audit_body_hash_fallbacks(text, allowed_body_hash_fallbacks))

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
