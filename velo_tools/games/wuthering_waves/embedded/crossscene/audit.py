"""Static post-assembly checks for WWMI cross-scene INI routing."""
from __future__ import annotations

import re
import importlib.util
import sys
import types
from collections import Counter
from pathlib import Path

try:
    from ..slot_textures import constants as _slot_constants
    from ..slot_textures import dds_meta as _dds_meta
    from ..slot_textures import ps_resource_scope as _ps_resource_scope
except ImportError:  # pragma: no cover - direct import fallback for pure tests
    try:
        from velo_tools.games.wuthering_waves.embedded.slot_textures import constants as _slot_constants
        from velo_tools.games.wuthering_waves.embedded.slot_textures import dds_meta as _dds_meta
        from velo_tools.games.wuthering_waves.embedded.slot_textures import ps_resource_scope as _ps_resource_scope
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
            _spec = importlib.util.spec_from_file_location(
                _slot_pkg + ".ps_resource_scope", _slot_dir / "ps_resource_scope.py")
            _ps_resource_scope = importlib.util.module_from_spec(_spec)
            sys.modules[_slot_pkg + ".ps_resource_scope"] = _ps_resource_scope
            assert _spec and _spec.loader
            _spec.loader.exec_module(_ps_resource_scope)
        except Exception:
            _slot_constants = None
            _dds_meta = None
            _ps_resource_scope = None


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


def _audit_section_control_flow(text):
    errors = []
    for name, block in _sections(text):
        stack = []
        for lineno, line in enumerate(block.splitlines()[1:], 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(";"):
                continue
            if stripped == "endif":
                if not stack:
                    errors.append(
                        "%s has unmatched endif at section line %d"
                        % (name, lineno))
                    continue
                stack.pop()
                continue
            if stripped.startswith("if "):
                stack.append(lineno)
                continue
            if stripped.startswith(("else if ", "elif ", "else")):
                if not stack:
                    errors.append(
                        "%s has %s without matching if at section line %d"
                        % (name, stripped.split(None, 1)[0], lineno))
        for start in stack:
            errors.append(
                "%s has unterminated if starting at section line %d"
                % (name, start))
    return errors


def _run_draw_geometries(block):
    return re.findall(
        r'^\s*run\s*=\s*(CommandListDrawGeometryComponent\d+(?:_ib\d+)*)\s*$',
        block or "", re.M)


def _geometry_plan(text, geometry_name, *, skip_ordinals=None):
    entries = _draw_entries(_section(text, geometry_name))
    skipped = {int(ordinal) for ordinal in (skip_ordinals or set())}
    return [
        entry for ordinal, entry in enumerate(entries)
        if ordinal not in skipped
    ]


def _geometry_guard_targets(block, component_id, ib_id):
    pattern = re.compile(
        r'if\s+\$xscene_skip_draw_c%d_(\d+)_ib%d\s*!=\s*1\s*'
        % (int(component_id), int(ib_id)))
    stack = []
    targets = {}
    draw_ordinal = 0
    for line in (block or "").splitlines()[1:]:
        stripped = line.strip()
        if stripped.startswith("if "):
            match = pattern.fullmatch(stripped)
            stack.append(int(match.group(1)) if match else None)
            continue
        if stripped == "endif":
            if stack:
                stack.pop()
            continue
        if re.fullmatch(
                r'drawindexed\s*=\s*\d+\s*,\s*\d+\s*,\s*-?\d+', stripped):
            for guard_ordinal in stack:
                if guard_ordinal is not None:
                    targets.setdefault(guard_ordinal, set()).add(draw_ordinal)
            draw_ordinal += 1
    return targets


def _geometry_skip_ordinals(text, block, geometry_name, component_id, ib_id,
                            errors, caller):
    lines = (block or "").splitlines()
    runs = [
        index for index, line in enumerate(lines)
        if re.fullmatch(
            r'\s*run\s*=\s*%s\s*' % re.escape(geometry_name), line)
    ]
    if len(runs) != 1:
        return set()
    run_index = runs[0]
    pattern = re.compile(
        r'\s*\$xscene_skip_draw_c%d_(\d+)_ib%d\s*=\s*([01])\s*'
        % (int(component_id), int(ib_id)))
    before = {}
    after = {}
    for index, line in enumerate(lines):
        match = pattern.fullmatch(line)
        if not match:
            continue
        ordinal = int(match.group(1))
        value = int(match.group(2))
        if index < run_index and value == 1:
            before.setdefault(ordinal, []).append(index)
        elif index > run_index and value == 0:
            after.setdefault(ordinal, []).append(index)
        else:
            errors.append(
                "%s has out-of-order geometry skip assignment %s"
                % (caller, line.strip()))
    for ordinal in sorted(set(before) | set(after)):
        if len(before.get(ordinal, [])) != 1 or len(after.get(ordinal, [])) != 1:
            errors.append(
                "%s must set $xscene_skip_draw_c%d_%d_ib%d to 1 before %s "
                "and clear it to 0 after the run"
                % (caller, component_id, ordinal, ib_id, geometry_name))
    complete = {
        ordinal for ordinal in before
        if len(before[ordinal]) == 1 and len(after.get(ordinal, [])) == 1
    }
    geometry_block = _section(text, geometry_name) or ""
    draw_count = len(_draw_entries(geometry_block))
    guard_targets = _geometry_guard_targets(
        geometry_block, component_id, ib_id)
    for ordinal in sorted(complete):
        skip_var = "$xscene_skip_draw_c%d_%d_ib%d" % (
            component_id, ordinal, ib_id)
        if ordinal >= draw_count:
            errors.append(
                "%s skips missing draw ordinal %d in %s"
                % (caller, ordinal, geometry_name))
            continue
        if guard_targets.get(ordinal) != {ordinal}:
            errors.append(
                "%s skips draw ordinal %d but %s does not guard that draw "
                "with %s != 1"
                % (caller, ordinal, geometry_name, skip_var))
    return complete


def _audit_draw_geometry(text):
    errors = []
    sections = list(_sections(text))
    section_names = {name for name, _block in sections}
    geometry_names = {
        name for name in section_names
        if re.fullmatch(
            r'CommandListDrawGeometryComponent\d+(?:_ib\d+)*', name)
    }
    geometry_callers = {}
    for caller, block in sections:
        for target in _run_draw_geometries(block):
            geometry_callers.setdefault(target, []).append(caller)

    for name, block in sections:
        if re.match(r'TextureOverride_FoldHost_.*_LOD\d+(?:_ib\d+)?$', name):
            errors.append(
                "%s is a FoldHost LOD section and must not be emitted; "
                "LOD draw sections are not format-tag twins" % name)
        if re.fullmatch(
                r'CommandListDraw(?:OwnerComponent\d+|AtomComponent\d+_\d+)'
                r'(?:_ib\d+)*', name):
            errors.append("legacy draw Owner/Atom section remains: %s" % name)
        is_geometry = name in geometry_names
        draws = _drawindexed_tuples(block)
        inline = re.fullmatch(
            r'(?:CommandListDrawComponent|TextureOverrideComponent)'
            r'(\d+)((?:_ib\d+)*)', name)
        if draws and not is_geometry and not inline:
            errors.append("%s contains drawindexed outside a draw caller" % name)
        if draws and inline:
            geometry = "CommandListDrawGeometryComponent%s%s" % (
                inline.group(1), inline.group(2) or "")
            if geometry in geometry_names or geometry_callers.get(geometry):
                errors.append(
                    "%s keeps inline drawindexed while shared Geometry %s exists"
                    % (name, geometry))
        if is_geometry:
            if not draws:
                errors.append("%s contains no drawindexed" % name)
            callers = set(geometry_callers.get(name, []))
            if len(callers) < 2:
                errors.append(
                    "%s is not reused by at least two draw transactions"
                    % name)
            for line in block.splitlines()[1:]:
                stripped = line.strip()
                if (not stripped or stripped.startswith(";")
                        or re.fullmatch(
                            r'drawindexed\s*=\s*\d+\s*,\s*\d+\s*,\s*-?\d+',
                            stripped)
                        or stripped == "endif" or stripped == "else"
                        or stripped.startswith(("if ", "else if ", "elif "))):
                    continue
                errors.append(
                    "%s contains non-geometry side effect: %s"
                    % (name, stripped))
        for target in _run_draw_geometries(block):
            if target not in geometry_names:
                errors.append("%s references missing draw Geometry %s" % (name, target))
        for legacy in re.findall(
                r'^\s*run\s*=\s*(CommandListDraw(?:OwnerComponent\d+|'
                r'AtomComponent\d+_\d+)(?:_ib\d+)*)\s*$', block, re.M):
            errors.append("%s runs legacy draw route %s" % (name, legacy))
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


def _audit_no_legacy_component_hash_gates(text):
    if re.search(r'\$component_hash_fallback_c\d+(?:_ib\d+)*\b', text):
        return [
            "legacy $component_hash_fallback gate remains; slot opt-outs must "
            "use the owning IB's native $object_detected gate"
        ]
    return []


def _body_draw_entries(text, component_id):
    cmd = _section(text, "CommandListDrawComponent%d_ib0" % component_id)
    if cmd:
        geometry_runs = _run_draw_geometries(cmd)
        if geometry_runs:
            return _geometry_plan(text, geometry_runs[-1])
        return _draw_entries(cmd)
    ovr = _section(text, "TextureOverrideComponent%d_ib0" % component_id)
    geometry_runs = _run_draw_geometries(ovr)
    if geometry_runs:
        return _geometry_plan(text, geometry_runs[-1])
    return _draw_entries(ovr)


def _select_draws(entries, excluded_labels):
    entries = list(entries or [])
    excluded = {str(label) for label in (excluded_labels or set())}
    if not entries or not excluded:
        return entries
    if not any(label in excluded for _cnt, _off, label in entries):
        return entries[:1]
    return [entry for entry in entries if entry[2] not in excluded]


def _strict_selected_draw_ordinals(entries, excluded_labels):
    entries = list(entries or [])
    excluded = {str(label) for label in (excluded_labels or set())}
    excluded_ordinals = set()
    for label in sorted(excluded):
        matches = [
            ordinal for ordinal, entry in enumerate(entries)
            if entry[2] == label
        ]
        if len(matches) != 1:
            raise ValueError(
                "split draw exclusion %r must resolve to exactly one draw "
                "ordinal, got %s" % (label, matches))
        excluded_ordinals.add(matches[0])
    return tuple(
        ordinal for ordinal in range(len(entries))
        if ordinal not in excluded_ordinals)


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


def _positive_object_gate(conditions):
    object_conditions = [
        condition for condition in conditions
        if re.search(r'\$object_detected_ib\d+\b', condition)
    ]
    object_ibs = {
        int(ib)
        for condition in object_conditions
        for ib in re.findall(r'\$object_detected_ib(\d+)\b', condition)
    }
    if len(conditions) != 1 or len(object_conditions) != 1:
        return object_ibs, False
    terms = [term.strip() for term in object_conditions[0].split("||")]
    matches = [
        re.fullmatch(
            r'\$object_detected_ib(\d+)(?:\s*==\s*1)?', term)
        for term in terms
    ]
    if not terms or not all(matches):
        return object_ibs, False
    return {int(match.group(1)) for match in matches}, True


def _resource_assignment_gate_evidence(block, resource):
    """Return object-gate evidence for each exact resource assignment."""
    conditions = []
    assignments = []
    assignment = re.compile(
        r'this\s*=\s*%s(?:\s*;.*)?' % re.escape(resource), re.I)
    for line in block.splitlines()[1:]:
        stripped = line.strip()
        if stripped.startswith("if "):
            conditions.append(stripped[3:])
            continue
        if stripped.startswith("else if "):
            if conditions:
                conditions[-1] = "__else_if__ " + stripped[8:]
            continue
        if stripped.startswith("elif "):
            if conditions:
                conditions[-1] = "__elif__ " + stripped[5:]
            continue
        if stripped == "else":
            if conditions:
                conditions[-1] = ""
            continue
        if stripped == "endif":
            if conditions:
                conditions.pop()
            continue
        if assignment.fullmatch(stripped):
            assignments.append(_positive_object_gate(conditions))
    return assignments


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
        if not re.search(
                r'(^|/)Textures/Components-\d+(?:-\d+)*\s+'
                r't=[0-9a-fA-F]{8}\.dds$',
                normalized):
            continue
        block = match.group(1)
        tagged = re.search(
            r'^\s*;\s*slot_opt_out_hash_fallback\s*=\s*1\s*$',
            block, re.M | re.I) is not None
        scope_lines = re.findall(
            r'^\s*;\s*opt_out_component_scope\s*=\s*(.*?)\s*$',
            block, re.M | re.I)
        scopes = {
            (int(comp), int(ib))
            for scope_line in scope_lines
            for comp, ib in re.findall(r'c(\d+)_ib(\d+)', scope_line, re.I)
        }
        if tagged and scopes:
            owning_ibs = {ib for _comp, ib in scopes}
            assignment_gates = _resource_assignment_gate_evidence(
                block, "Resource_Texture_%s" % tex_hash)
            if len(assignment_gates) != 1:
                errors.append(
                    "body slot opt-out texture %s must assign its hash "
                    "resource exactly once, found %d assignments"
                    % (tex_hash, len(assignment_gates)))
                continue
            object_gate_ibs, positive_gate = assignment_gates[0]
            if not object_gate_ibs:
                errors.append(
                    "body slot opt-out texture %s resource assignment is not "
                    "enclosed by its owning IB object gate(s)"
                    % tex_hash)
                continue
            if not positive_gate:
                errors.append(
                    "body slot opt-out texture %s resource assignment must be "
                    "enclosed by one positive owning-IB OR expression"
                    % tex_hash)
                continue
            if owning_ibs == object_gate_ibs:
                continue
            errors.append(
                "body slot opt-out texture %s must use exactly the owning IB "
                "object gates; expected %s, found %s"
                % (tex_hash,
                   ", ".join("$object_detected_ib%d" % ib
                             for ib in sorted(owning_ibs)),
                   ", ".join("$object_detected_ib%d" % ib
                             for ib in sorted(object_gate_ibs)) or "none"))
            continue
        errors.append(
            "body slot-owned texture %s is emitted as unscoped hash fallback %s; "
            "slot-style export requires ps-t assignment, explicit slot opt-out "
            "metadata with an owning-IB object gate, or fail-closed"
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
            r'(^\[(CommandListSetTexturesComponent\d+'
            r'(?:Route(?:Base|[0-9a-fA-F]{8}))?_ib\d+[^\]]*)\][^\[]*)',
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


def _audit_readable_slot_resource_names(text):
    errors = []
    for match in re.finditer(
            r'(^\[(CommandListSetTexturesComponent[^\]]*)\][^\[]*)',
            text, re.M):
        header = match.group(2)
        block = match.group(1)
        for resource in re.findall(
                r'\bps-t\d+\s*=\s*ref\s+(ResourceTexture\d+(?:_ib\d+)*)\b',
                block):
            errors.append(
                "%s references numeric slot resource %s; use "
                "ResourceTexture_C{component}_{hash}_ibN naming"
                % (header, resource))
        setter = re.fullmatch(
            r'CommandListSetTexturesComponent(\d+)'
            r'(?:Route(?:Base|[0-9a-fA-F]{8}))?_ib(\d+)', header)
        if not setter:
            continue
        setter_component = int(setter.group(1))
        setter_ib = int(setter.group(2))
        for resource_match in re.finditer(
                r'\bps-t\d+\s*=\s*ref\s+'
                r'(ResourceTexture_C(\d+)_[0-9a-fA-F]{8}_ib(\d+))\b',
                block):
            resource = resource_match.group(1)
            resource_component = int(resource_match.group(2))
            resource_ib = int(resource_match.group(3))
            if resource_component != setter_component:
                errors.append(
                    "%s references component C%d resource %s; expected C%d"
                    % (header, resource_component, resource,
                       setter_component))
            if resource_ib != setter_ib:
                errors.append(
                    "%s references IB%d resource %s; expected IB%d"
                    % (header, resource_ib, resource, setter_ib))
    return errors


def _slot_branch_signature_from_condition(line):
    positive = []
    for slot_raw, expected in re.findall(
            r'\bps-t(\d+)\s*==\s*([0-9.]+)', line):
        positive.append((int(slot_raw), expected))
    negative = []
    for slot_raw, expected in re.findall(
            r'\bps-t(\d+)\s*!=\s*([0-9.]+)', line):
        negative.append((int(slot_raw), expected))
    return tuple(sorted(positive)), tuple(sorted(negative))


def _normalise_slot_branch_expectations(slot_branch_expectations):
    out = {}
    for key, values in (slot_branch_expectations or {}).items():
        if isinstance(key, str):
            normalised_key = key.casefold()
        else:
            try:
                comp_id, ib = key
                normalised_key = (int(comp_id), int(ib))
            except (TypeError, ValueError):
                continue
        branches = []
        for value in values or []:
            if (not isinstance(value, (list, tuple)) or len(value) != 3
                    or value[0] != "branch"):
                continue
            raw_signature = value[1]
            try:
                assign_slots = tuple(sorted(int(slot) for slot in value[2]))
            except (TypeError, ValueError):
                continue
            signature = []
            for item in raw_signature or []:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    signature = []
                    break
                try:
                    signature.append((int(item[0]), str(item[1])))
                except (TypeError, ValueError):
                    signature = []
                    break
            if signature and assign_slots:
                branches.append((tuple(sorted(signature)), assign_slots))
        if branches:
            out[normalised_key] = tuple(branches)
    return out


def _slot_branch_lines(block):
    current = None
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith(("if ", "else if ")):
            if current is not None:
                yield current
            current = [stripped, []]
            continue
        if stripped.startswith(("else ", "elif ", "endif")):
            if current is not None:
                yield current
                current = None
            continue
        if current is None:
            continue
        match = re.search(
            r'\bps-t(\d+)\s*=\s*ref\s+ResourceTexture[0-9A-Za-z_]+\b',
            stripped)
        if match:
            current[1].append(int(match.group(1)))
    if current is not None:
        yield current


def _audit_slot_branches_match_stu_primary_pass(text, slot_branch_expectations):
    expected = _normalise_slot_branch_expectations(slot_branch_expectations)
    if not expected:
        return []
    errors = []
    seen_expectation_keys = set()
    for match in re.finditer(
            r'(^\[(CommandListSetTexturesComponent(\d+)'
            r'(?:Route(?:Base|[0-9a-fA-F]{8}))?_ib(\d+)[^\]]*)\][^\[]*)',
            text, re.M):
        block = match.group(1)
        header = match.group(2)
        comp_id = int(match.group(3))
        ib = int(match.group(4))
        expected_sigs = (
            expected.get(header.casefold())
            or expected.get((comp_id, ib)))
        if not expected_sigs:
            continue
        seen_expectation_keys.add(header.casefold())
        seen_expectation_keys.add((comp_id, ib))
        actual = []
        for stripped, assigned_slots in _slot_branch_lines(block):
            if "ps-t" not in stripped:
                continue
            positive, negative = _slot_branch_signature_from_condition(stripped)
            if negative:
                errors.append(
                    "%s uses negative slot condition %s; slot branches must "
                    "use stable positive STU primary-pass subsets only"
                    % (header, ", ".join(
                        "ps-t%d != %s" % item for item in negative)))
            if len(assigned_slots) != len(set(assigned_slots)):
                errors.append(
                    "%s has duplicate assignment slot(s) in condition %s"
                    % (header, stripped))
            positive_slots = {slot for slot, _value in positive}
            for slot in sorted(set(assigned_slots) - positive_slots):
                errors.append(
                    "%s assignment ps-t%d has no positive condition in %s"
                    % (header, slot, stripped))
            actual.append((positive, tuple(sorted(assigned_slots))))
        expected_counter = Counter(expected_sigs)
        actual_counter = Counter(actual)
        for branch, count in sorted(actual_counter.items(), key=str):
            if count > 1:
                errors.append(
                    "%s has duplicate slot branch %s (%d copies)"
                    % (header, _format_slot_branch(branch), count))
        missing = expected_counter - actual_counter
        extra = actual_counter - expected_counter
        if missing or extra:
            details = []
            if missing:
                details.append("missing " + ", ".join(
                    "%s x%d" % (_format_slot_branch(branch), count)
                    for branch, count in sorted(missing.items(), key=str)))
            if extra:
                details.append("extra " + ", ".join(
                    "%s x%d" % (_format_slot_branch(branch), count)
                    for branch, count in sorted(extra.items(), key=str)))
            errors.append(
                "%s slot branch multiset mismatch: %s"
                % (header, "; ".join(details)))
    for key in expected:
        if key not in seen_expectation_keys:
            errors.append("missing slot setter section for branch contract %r" % (key,))
    return errors


def _format_slot_branch(branch):
    signature, assignments = branch
    condition = " && ".join(
        "ps-t%d == %s" % item for item in signature) or "<no positive signature>"
    assigned = ",".join("ps-t%d" % slot for slot in assignments) or "<none>"
    return "{%s -> %s}" % (condition, assigned)


def _route_setter_runs(block):
    return re.findall(
        r'^\s*run\s*=\s*(CommandListSetTexturesComponent\d+'
        r'Route(?:Base|[0-9a-fA-F]{8})_ib\d+)\s*$',
        block or "", re.M | re.I)


def _audit_component_route_setters(text, routing, component_route_lists):
    mappings = dict(component_route_lists or {})
    if not mappings:
        return []
    errors = []
    for raw_key, mapping in mappings.items():
        try:
            if isinstance(raw_key, str):
                key_match = re.fullmatch(r'c(\d+)_ib(\d+)', raw_key, re.I)
                if not key_match:
                    raise ValueError
                component_id, ib_id = map(int, key_match.groups())
            else:
                component_id, ib_id = int(raw_key[0]), int(raw_key[1])
        except (TypeError, ValueError, IndexError):
            errors.append("invalid component route key %r" % (raw_key,))
            continue
        if not isinstance(mapping, dict):
            errors.append("invalid route mapping for C%d/ib%d" % (
                component_id, ib_id))
            continue
        base_setter = mapping.get("base")
        base = _section(text, "CommandListDrawComponent%d_ib%d" % (
            component_id, ib_id))
        if base is None:
            base = _section(text, "TextureOverrideComponent%d_ib%d" % (
                component_id, ib_id))
        base_runs = _route_setter_runs(base)
        if base_runs.count(base_setter) != 1 or len(base_runs) != 1:
            errors.append(
                "base C%d/ib%d must run exactly %s" % (
                    component_id, ib_id, base_setter))
        if ib_id != 0:
            continue
        for scene in routing.get("scene_ibs") or []:
            if not scene.get("foldable"):
                continue
            route = str(scene.get("ib_hash") or "").lower()
            comp_map = {
                int(fc): int(bc)
                for fc, bc in ((scene.get("fold") or {}).get("comp_map") or {}).items()
            }
            for fold_component, base_component in comp_map.items():
                if base_component != component_id:
                    continue
                expected = mapping.get(route)
                header = "TextureOverride_FoldHost_%s_C%d_ib0" % (
                    route, fold_component)
                block = _section(text, header)
                runs = _route_setter_runs(block)
                if expected is None:
                    errors.append("%s has no route setter contract" % header)
                elif runs.count(expected) != 1 or len(runs) != 1:
                    errors.append("%s must run exactly %s" % (header, expected))
    return errors


def audit_cross_scene_ini(mod_ini_path, routing, roles, *, own_excluded=None, draw_excludes=None,
                           allowed_body_hash_fallbacks=None,
                           slot_branch_expectations=None,
                           slot_restore_contract=None,
                           slot_component_route_lists=None,
                           slot_style=False):
    """Return a dict with routing errors found in the final namespace-merged INI."""
    path = Path(mod_ini_path)
    if not path.is_file():
        return {
            "skipped": False,
            "reason": "expected mod.ini is missing",
            "errors": ["expected mod.ini is missing: %s" % path],
        }
    text = path.read_text(encoding="utf-8")
    roles = list(roles or [])
    own_excluded = dict(own_excluded or {})
    draw_excludes = {int(k): set(v or set()) for k, v in (draw_excludes or {}).items()}
    errors = []
    missing_capabilities = []
    if _slot_constants is None:
        missing_capabilities.append("slot constants")
    if _dds_meta is None:
        missing_capabilities.append("DDS metadata")
    if _ps_resource_scope is None:
        missing_capabilities.append("PS resource scope")
    if missing_capabilities:
        errors.append(
            "cross-scene audit capability unavailable: "
            + ", ".join(missing_capabilities))
    errors.extend(_audit_section_control_flow(text))
    errors.extend(_audit_slot_resources(text, path.parent))
    if slot_style:
        errors.extend(_audit_body_hash_fallbacks(
            text, allowed_body_hash_fallbacks))
    errors.extend(_audit_no_slot_markers(text))
    errors.extend(_audit_residual_sensitive_conditions(text))
    errors.extend(_audit_readable_slot_resource_names(text))
    errors.extend(_audit_slot_branches_match_stu_primary_pass(
        text, slot_branch_expectations))
    errors.extend(_audit_component_route_setters(
        text, routing, slot_component_route_lists))
    if _ps_resource_scope is not None:
        errors.extend(_ps_resource_scope.audit_ps_resource_scope(
            text, slot_restore_contract))
    errors.extend(_audit_skip_vars_declared(text))
    errors.extend(_audit_no_legacy_component_hash_gates(text))
    errors.extend(_audit_draw_geometry(text))

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
                if target and _draw_entries(target):
                    errors.append("hidden own-buffer %s (%s) still draws via %s" % (tag, label, run))
                for geometry in _run_draw_geometries(target):
                    if _geometry_plan(text, geometry):
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
                if _run_draw_geometries(block):
                    errors.append("%s runs Geometry even though base Component %d has no draw"
                                  % (header, bc))
                continue
            if not block:
                errors.append("%s missing for visible base Component %d" % (header, bc))
                continue
            try:
                selected_ordinals = _strict_selected_draw_ordinals(
                    body_draws, draw_excludes.get(bc))
            except ValueError as exc:
                errors.append("%s: %s" % (header, exc))
                selected_ordinals = ()
            expected = _pairs([
                entry for ordinal, entry in enumerate(body_draws)
                if ordinal in set(selected_ordinals)
            ])
            geometry_runs = _run_draw_geometries(block)
            expected_geometry = "CommandListDrawGeometryComponent%d_ib0" % bc
            if len(geometry_runs) != 1:
                errors.append("%s must run exactly one canonical Geometry, got %s"
                              % (header, geometry_runs))
                actual = []
            elif geometry_runs[0] != expected_geometry:
                errors.append(
                    "%s must run %s, got %s"
                    % (header, expected_geometry, geometry_runs[0]))
                actual = []
            else:
                skip = _geometry_skip_ordinals(
                    text, block, geometry_runs[0], bc, 0, errors, header)
                actual = _pairs(_geometry_plan(
                    text, geometry_runs[0], skip_ordinals=skip))
            if actual != expected:
                errors.append("%s draw plan mismatch: expected %s, got %s" % (header, expected, actual))

    return {"skipped": False, "errors": errors}
