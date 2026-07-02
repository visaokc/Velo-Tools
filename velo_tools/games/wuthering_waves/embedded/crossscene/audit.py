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


def _drawindexed_tuples(block):
    tuples = []
    for ln in (block or "").splitlines():
        m = re.match(r'\s*drawindexed = (\d+), (\d+), (-?\d+)', ln)
        if m:
            tuples.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return tuples


def _sections(text):
    for match in re.finditer(r'(^\[([^\]]+)\][^\[]*)', text, re.M):
        yield match.group(2), match.group(1)


def _draw_atom_tuple_map(text):
    atoms = {}
    for name, block in _sections(text):
        if not re.match(r'CommandListDrawAtomComponent\d+_\d+(?:_ib\d+)*$', name):
            continue
        entries = _draw_entries(block)
        if len(entries) == 1:
            cnt, off, _label = entries[0]
            atoms[name] = (cnt, off)
    return atoms


def _draw_atom_label_map(text):
    labels = {}
    for name, block in _sections(text):
        if not re.match(r'CommandListDrawAtomComponent\d+_\d+(?:_ib\d+)*$', name):
            continue
        entries = _draw_entries(block)
        if len(entries) == 1:
            _cnt, _off, label = entries[0]
            labels[name] = label
    return labels


def _run_draw_atoms(block):
    return re.findall(
        r'^\s*run\s*=\s*(CommandListDrawAtomComponent\d+_\d+(?:_ib\d+)*)\s*$',
        block or "",
        re.M)


def _run_draw_owners(block):
    return re.findall(
        r'^\s*run\s*=\s*(CommandListDrawOwnerComponent\d+(?:_ib\d+)*)\s*$',
        block or "",
        re.M)


def _draw_owner_atom_runs(text):
    return {
        name: _run_draw_atoms(block)
        for name, block in _sections(text)
        if re.match(r'CommandListDrawOwnerComponent\d+(?:_ib\d+)*$', name)
    }


def _draw_owner_skip_ordinals(block, component_id, ib_id):
    suffix = "(?:_ib0)?" if ib_id == 0 else "_ib%d" % ib_id
    pattern = r'\$xscene_skip_draw_c%d_(\d+)%s\s*=\s*1\b' % (
        int(component_id), suffix)
    return {int(raw) for raw in re.findall(pattern, block or "")}


def _owner_plan(text, owner_name, *, skip_ordinals=None):
    atoms = _draw_atom_tuple_map(text)
    labels = _draw_atom_label_map(text)
    runs = _draw_owner_atom_runs(text).get(owner_name, [])
    skip_ordinals = set(skip_ordinals or set())
    out = []
    for ordinal, atom in enumerate(runs):
        if ordinal in skip_ordinals or atom not in atoms:
            continue
        cnt, off = atoms[atom]
        out.append((cnt, off, labels.get(atom)))
    return out


def _audit_draw_owners(text):
    errors = []
    seen = {}
    for name, block in _sections(text):
        if re.match(r'TextureOverride_FoldHost_.*_LOD\d+(?:_ib\d+)?$', name):
            errors.append(
                "%s is a FoldHost LOD section and must not be emitted; "
                "LOD draw sections are not format-tag twins" % name)
        is_owner = re.match(r'CommandListDrawAtomComponent\d+_\d+(?:_ib\d+)*$', name) is not None
        for draw in _drawindexed_tuples(block):
            if not is_owner:
                errors.append("%s contains drawindexed outside draw-owner atom" % name)
                continue
            prev = seen.get(draw)
            if prev is not None:
                errors.append(
                    "drawindexed tuple %s duplicated in %s and %s"
                    % (draw, prev, name))
            else:
                seen[draw] = name
        if re.match(r'CommandListDrawAtomComponent\d+_\d+(?:_ib\d+)*$', name):
            continue
        if re.match(r'CommandListDrawOwnerComponent\d+(?:_ib\d+)*$', name):
            continue
        for atom in _run_draw_atoms(block):
            errors.append("%s directly runs draw atom %s outside canonical draw owner"
                          % (name, atom))
    return errors


def _audit_skip_vars_declared(text):
    errors = []
    declared = {
        match.group(1)
        for match in re.finditer(r'^\s*global\s+\$([A-Za-z0-9_]+)\b', text, re.M)
    }
    used = sorted({
        match.group(1)
        for match in re.finditer(r'\$(xscene_skip_draw_c\d+_\d+(?:_ib\d+)*)\b', text)
    })
    for name in used:
        if name not in declared:
            errors.append(
                "$%s is used as a FoldHost draw skip guard but is not declared global"
                % name)
    return errors


def _body_draw_entries(text, component_id):
    atoms = _draw_atom_tuple_map(text)
    cmd = _section(text, "CommandListDrawComponent%d_ib0" % component_id)
    if cmd:
        owner_runs = _run_draw_owners(cmd)
        if owner_runs:
            out = _owner_plan(text, owner_runs[-1])
            if out:
                return out
        out = []
        label = None
        for ln in cmd.splitlines():
            s = ln.strip()
            if s.startswith("; Draw "):
                label = s.split("; Draw ", 1)[1].strip()
                continue
            m = re.match(r'run = (CommandListDrawAtomComponent\d+_\d+(?:_ib\d+)*)$', s)
            if m and m.group(1) in atoms:
                cnt, off = atoms[m.group(1)]
                out.append((cnt, off, label))
                label = None
        if out:
            return out
        return _draw_entries(cmd)
    ovr = _section(text, "TextureOverrideComponent%d_ib0" % component_id)
    owner_runs = _run_draw_owners(ovr)
    if owner_runs:
        out = _owner_plan(text, owner_runs[-1])
        if out:
            return out
    out = []
    label = None
    for ln in (ovr or "").splitlines():
        s = ln.strip()
        if s.startswith("; Draw "):
            label = s.split("; Draw ", 1)[1].strip()
            continue
        m = re.match(r'run = (CommandListDrawAtomComponent\d+_\d+(?:_ib\d+)*)$', s)
        if m and m.group(1) in atoms:
            cnt, off = atoms[m.group(1)]
            out.append((cnt, off, label))
            label = None
    if out:
        return out
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
        filename = resources.get(tex_hash, "")
        normalized = filename.replace("\\", "/")
        if not re.search(r'(^|/)Textures/Components-\d+\s+t=[0-9a-fA-F]{8}\.dds$',
                         normalized):
            continue
        block = match.group(1)
        scoped = re.findall(r'\$component_hash_fallback_c\d+_ib\d+\s*==\s*1', block)
        tagged = 'component_scoped_hash_fallback' in block.lower()
        if tagged and scoped and "$object_detected_ib0" not in block:
            continue
        if "$object_detected_ib0" not in block and "$object_detected_ib" in block and not scoped:
            continue
        errors.append(
            "body slot-owned texture %s is emitted as unscoped hash fallback %s; "
            "slot-style export requires ps-t assignment, explicit component-scoped "
            "excluded-component fallback, or fail-closed"
            % (tex_hash, filename))
    return errors


def _audit_no_slot_markers(text):
    errors = []
    forbidden = (
        ('TextureOverrideSlotMarkerComponent',
         'slot marker TextureOverride sections are not allowed in pure 0hash slot mode'),
        ('CommandListTriggerSlotMarkersComponent',
         'scoped slot marker trigger command lists are not allowed in pure 0hash slot mode'),
        ('$slot_tex_c',
         'slot marker variables are not allowed in pure 0hash slot mode'),
        ('marker_mode = hash-marker + slot-write',
         'hash-marker + slot-write mode is disabled for pure 0hash slot export'),
    )
    for needle, message in forbidden:
        if needle in text:
            errors.append(message)
    return errors


def _audit_residual_sensitive_conditions(text):
    errors = []
    for match in re.finditer(
            r'(^\[(CommandListSetTexturesComponent[^\]]*)\][^\[]*)',
            text, re.M):
        block = match.group(1)
        header = match.group(2)
        assigned_slots = {
            int(slot)
            for slot in re.findall(
                r'^\s*ps-t(\d+)\s*=\s*ref\s+ResourceTexture[0-9A-Za-z_]+\b',
                block, re.M)
        }
        if not assigned_slots:
            continue
        reported = set()
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("if ", "else if ")):
                continue
            for slot_raw, expected in re.findall(
                    r'\bps-t(\d+)\s*!=\s*([0-9.]+)', stripped):
                slot = int(slot_raw)
                if slot not in assigned_slots or slot in reported:
                    continue
                reported.add(slot)
                errors.append(
                    "%s uses residual-sensitive ps-t%d != %s after the same "
                    "command list assigns ps-t%d; require a fresh positive "
                    "slot condition or exclude the component from the slot layer"
                    % (header, slot, expected, slot))
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
    errors.extend(_audit_no_slot_markers(text))
    errors.extend(_audit_residual_sensitive_conditions(text))
    errors.extend(_audit_skip_vars_declared(text))
    errors.extend(_audit_draw_owners(text))
    draw_atoms = _draw_atom_tuple_map(text)
    draw_owners = _draw_owner_atom_runs(text)

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
                if target and (_draw_entries(target) or _run_draw_atoms(target)):
                    errors.append("hidden own-buffer %s (%s) still draws via %s" % (tag, label, run))
                for owner in _run_draw_owners(target):
                    if draw_owners.get(owner):
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
                if _run_draw_atoms(block):
                    errors.append("%s runs draw-owner atoms even though base Component %d has no draw"
                                  % (header, bc))
                if _run_draw_owners(block):
                    errors.append("%s runs draw owner even though base Component %d has no draw"
                                  % (header, bc))
                continue
            if not block:
                errors.append("%s missing for visible base Component %d" % (header, bc))
                continue
            expected = _pairs(_select_draws(body_draws, draw_excludes.get(bc)))
            if _run_draw_atoms(block):
                errors.append("%s directly runs draw-owner atom(s); run canonical draw owner instead"
                              % header)
            owner_runs = _run_draw_owners(block)
            missing_owners = [name for name in owner_runs if name not in draw_owners]
            if missing_owners:
                errors.append("%s references missing draw owner(s): %s" % (header, missing_owners))
            if len(owner_runs) != 1:
                errors.append("%s must run exactly one canonical draw owner, got %s"
                              % (header, owner_runs))
                actual = []
            else:
                skip = _draw_owner_skip_ordinals(block, bc, 0)
                actual = _pairs(_owner_plan(text, owner_runs[0], skip_ordinals=skip))
            if actual != expected:
                errors.append("%s draw plan mismatch: expected %s, got %s" % (header, expected, actual))

    return {"skipped": False, "errors": errors}
