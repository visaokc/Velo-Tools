"""Namespace-merge N independent per-IB WWMI mods -> one mod + textures deduped by hash + self-check.

Ported from the standalone, game-verified prototype ``merge_inis.py`` (in-process, no subprocess):
for each IB k, non-texture sections become ``[X]->[X_ibK]``, ``$v->$v_ibK``, ``Resource*/CommandList*->*_ibK``
(the ``\\WWMIv1\\`` framework references are preserved), ``Meshes/->Meshes/ibK_`` (flattened).

Textures take one of two paths per IB:
  * Slot-style (velo): textures rebound inside the component draw scope as ``ps-t{n} = ref
    ResourceTexture{i}`` are kept PER-IB (``[ResourceTexture{i}]`` -> ``[ResourceTexture{i}_ibK]``,
    filename normalised to the deduped ``Textures/Components-* t=<hash>.dds``), so the namespaced
    slot command lists resolve. They carry NO texture-hash matching -> immune to streaming.
  * Hash-style (stock / slot opt-out fallback): ``[TextureOverrideTexture{i}]`` hash overrides
    collapse into one global ``[Resource_Texture_<hash>]``/``[TextureOverride_Texture_<hash>]`` per
    unique hash (gate = OR over each owning IB's ``$object_detected``), as before. Explicit slot
    opt-outs retain metadata naming the component that requested the native hash path.

``[Constants]``/``[Present]`` each merged into one section. Returns a self-check report (the
``tex_blindzone`` field lists residual hash-style textures -> empty == a pure 0-texture-hash mod).
"""
import os
import re
import shutil
import importlib.util
import hashlib
import json
import sys


def _load_format_tags():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "slot_textures", "format_tags.py")
    spec = importlib.util.spec_from_file_location("_velo_wwmi_format_tags", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_ps_resource_scope():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "slot_textures", "ps_resource_scope.py")
    spec = importlib.util.spec_from_file_location("_velo_wwmi_ps_resource_scope", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_crossscene_helper(module_name):
    path = os.path.join(os.path.dirname(__file__), module_name + ".py")
    spec = importlib.util.spec_from_file_location(
        "_velo_wwmi_crossscene_" + module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_format_tags = _load_format_tags()
_ps_resource_scope = _load_ps_resource_scope()
_ini_document = _load_crossscene_helper("ini_document")
_texture_delivery = _load_crossscene_helper("texture_delivery")

_RE_GLOBAL = re.compile(r'\$([A-Za-z]\w*)')
_RE_RESCMD = re.compile(r'\b(Resource[A-Za-z0-9_]+|CommandList[A-Za-z0-9_]+)\b')
_RE_RESTEX = re.compile(r'ResourceTexture(?:\d+|_C\d+_[0-9a-fA-F]+)(?:_ib\d+)*$')
_RE_OVRTEX = re.compile(r'TextureOverrideTexture\d+(?:_ib\d+)*$')
_RE_TEXHASH = re.compile(r't=([0-9a-fA-F]+)')
# Slot-style binds: `ps-t{n} = ref ResourceTexture{i}` (the trailing \b keeps ResourceTextureBackupT*
# -- the per-slot backup resources -- out; suffixes cover fold-merged resources that are already
# namespaced once before the final assembler pass).
_RE_PST_REF = re.compile(r'ps-t\d+\s*=\s*ref\s+(ResourceTexture(?:\d+|_C\d+_[0-9a-fA-F]+)(?:_ib\d+)*)\b')
_RE_TEXTURE_REF = re.compile(r'\s*this\s*=\s*(ResourceTexture(?:\d+|_C\d+_[0-9a-fA-F]+)(?:_ib\d+)*)\b', re.I)
_SHARED_BODY_GLOBALS = {"form_id"}
_SLOT_CONTRACT_FILENAME = ".velo_slot_contract.json"


def _alias_for_component(namespace_aliases, k, component_id):
    try:
        raw = (namespace_aliases or {}).get(k, {}).get("component_map", {})
    except AttributeError:
        return component_id
    return int(raw.get(component_id, component_id))


def _alias_enabled(namespace_aliases, k):
    return namespace_aliases is not None and k in namespace_aliases


def _alias_global_var_name(name, k, namespace_aliases):
    if not _alias_enabled(namespace_aliases, k):
        return name
    match = re.fullmatch(r'xscene_skip_draw_c(\d+)_(\d+)', name)
    if match:
        comp = _alias_for_component(namespace_aliases, k, int(match.group(1)))
        return f'xscene_skip_draw_c{comp}_{match.group(2)}'
    return name


def _resource_alias_name(name, k, resource_hashes, resource_components,
                         namespace_aliases, resource_component_override=None):
    hv = resource_hashes.get(name)
    if not hv:
        return f'{name}_ib{k}'
    component_id = resource_component_override
    if component_id is None:
        component_id = (resource_components or {}).get(name)
        if isinstance(component_id, (set, list, tuple)):
            component_id = next(iter(sorted(component_id))) if component_id else None
    if component_id is None:
        try:
            raw = (namespace_aliases or {}).get(k, {}).get("component_map", {})
            if _alias_enabled(namespace_aliases, k) and len(raw) == 1:
                component_id = int(next(iter(raw.values())))
        except (AttributeError, StopIteration, TypeError, ValueError):
            component_id = None
    if component_id is None:
        return f'{name}_ib{k}'
    return f'ResourceTexture_C{component_id}_{hv}_ib{k}'


def _section_alias_name(name, k, resource_hashes=None, resource_components=None,
                        namespace_aliases=None, resource_component_override=None):
    resource_hashes = resource_hashes or {}
    route_setter = re.fullmatch(
        r'(CommandListSetTexturesComponent)(\d+)'
        r'(Route(?:Base|[0-9a-fA-F]{8}))', name)
    if route_setter:
        comp = _alias_for_component(
            namespace_aliases, k, int(route_setter.group(2)))
        return (f'{route_setter.group(1)}{comp}{route_setter.group(3)}'
                f'_ib{k}')
    comp_match = re.fullmatch(r'(CommandListSetTexturesComponent|CommandListProbeComponent|'
                              r'CommandListDrawComponent|CommandListDrawGeometryComponent|'
                              r'CommandListDrawOwnerComponent|'
                              r'TextureOverrideComponent)(\d+)', name)
    if comp_match:
        comp = _alias_for_component(namespace_aliases, k, int(comp_match.group(2)))
        return f'{comp_match.group(1)}{comp}_ib{k}'
    atom_match = re.fullmatch(r'CommandListDrawAtomComponent(\d+)_(\d+)', name)
    if atom_match:
        comp = _alias_for_component(namespace_aliases, k, int(atom_match.group(1)))
        return f'CommandListDrawAtomComponent{comp}_{atom_match.group(2)}_ib{k}'
    fmt_match = re.fullmatch(r'(TextureOverride(?:Lod\d+)?Component)(\d+)(.*)', name)
    if fmt_match:
        comp = _alias_for_component(namespace_aliases, k, int(fmt_match.group(2)))
        return f'{fmt_match.group(1)}{comp}{fmt_match.group(3)}_ib{k}'
    if _RE_RESTEX.match(name):
        return _resource_alias_name(
            name, k, resource_hashes, resource_components, namespace_aliases,
            resource_component_override)
    return f'{name}_ib{k}'


def _normalise_restore_policy(value):
    full = {"mode": "full"}
    if not isinstance(value, dict):
        return full, False
    mode = str(value.get("mode") or "").strip().lower()
    if mode == "full":
        return full, True
    if mode != "except":
        return full, False
    try:
        slot = int(value.get("persistent_slot"))
    except (TypeError, ValueError):
        return full, False
    if not 0 <= slot <= 8:
        return full, False
    return {"mode": "except", "persistent_slot": slot}, True


def _branch_expectation(contract, final_setter):
    match = re.fullmatch(
        r'CommandListSetTexturesComponent(\d+)'
        r'(?:Route(?:Base|[0-9a-f]{8}))?_ib(\d+)', final_setter, re.I)
    if not match or not isinstance(contract, dict):
        return None, [f"{final_setter}: branch contract is not an object"], False
    branches = contract.get("branches")
    if not isinstance(branches, list):
        return None, [f"{final_setter}: branch contract has no branches list"], False
    if not branches:
        return None, [f"{final_setter}: branch contract has no branches"], False
    values = []
    errors = []
    for ordinal, branch in enumerate(branches):
        if not isinstance(branch, dict):
            errors.append(
                f"{final_setter}: branch {ordinal} is not an object")
            continue
        raw_signature = branch.get("positive_signature")
        signature = []
        if not isinstance(raw_signature, list) or not raw_signature:
            errors.append(
                f"{final_setter}: branch {ordinal} has no positive signature")
            continue
        for item in raw_signature:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                signature = []
                break
            try:
                slot = int(item[0])
                value = str(item[1]).strip()
            except (TypeError, ValueError):
                signature = []
                break
            if not 0 <= slot <= 8 or not value:
                signature = []
                break
            signature.append((slot, value))
        raw_assignments = branch.get("assignment_slots")
        try:
            assignment_slots = tuple(sorted(int(slot) for slot in raw_assignments))
        except (TypeError, ValueError):
            assignment_slots = ()
        signature_slots = [slot for slot, _value in signature]
        if (not signature or not assignment_slots
                or any(not 0 <= slot <= 8 for slot in assignment_slots)
                or len(signature_slots) != len(set(signature_slots))
                or len(assignment_slots) != len(set(assignment_slots))):
            errors.append(
                f"{final_setter}: branch {ordinal} has an incomplete signature")
            continue
        missing_conditions = sorted(set(assignment_slots) - set(signature_slots))
        if missing_conditions:
            errors.append(
                f"{final_setter}: branch {ordinal} condition does not cover "
                "assignment slot(s) "
                + ", ".join(f"ps-t{slot}" for slot in missing_conditions))
            continue
        if branch.get("negative_signature"):
            errors.append(
                f"{final_setter}: branch {ordinal} has a negative signature")
            continue
        values.append((
            "branch", tuple(sorted(signature)), assignment_slots))
    if len(values) != len(set(values)):
        errors.append(f"{final_setter}: branch contract contains duplicate branches")
    valid = not errors and len(values) == len(branches)
    return ((final_setter, tuple(values)) if values else None), errors, valid


def _collect_slot_contracts(mods, namespace_aliases):
    restore_contract = {}
    branch_expectations = {}
    missing = []
    degraded = []
    conflicts = []
    component_route_lists = {}
    sidecars = 0
    for k, mod in enumerate(mods):
        path = os.path.join(mod, _SLOT_CONTRACT_FILENAME)
        if not os.path.isfile(path):
            missing.append(k)
            continue
        sidecars += 1
        try:
            payload = json.loads(open(path, encoding="utf-8").read())
        except Exception as exc:
            raise ValueError(
                f"invalid slot contract for ib{k}: {exc}") from exc
        if (not isinstance(payload, dict)
                or payload.get("version") not in {1, 2}):
            raise ValueError(f"unsupported slot contract for ib{k}")
        raw_restore = payload.get("restore_contract")
        raw_branches = payload.get("branch_contract")
        if not isinstance(raw_restore, dict) or not isinstance(raw_branches, dict):
            raise ValueError(f"incomplete slot contract for ib{k}")
        source_keys = set(raw_restore) | set(raw_branches)
        source_route_setters = {}
        for source_setter in sorted(source_keys, key=str.casefold):
            if not re.fullmatch(
                    r'CommandListSetTexturesComponent\d+'
                    r'(?:Route(?:Base|[0-9a-fA-F]{8}))?',
                    str(source_setter), re.I):
                degraded.append(f"ib{k}:{source_setter}")
                continue
            if re.search(r'Route(?:Base|[0-9a-fA-F]{8})$', str(source_setter), re.I):
                source_route_setters[str(source_setter).casefold()] = str(source_setter)
            final_setter = _section_alias_name(
                str(source_setter), k, namespace_aliases=namespace_aliases)
            complete = (source_setter in raw_restore
                        and source_setter in raw_branches)
            policy, valid = _normalise_restore_policy(
                raw_restore.get(source_setter))
            expectation, errors, branch_valid = _branch_expectation(
                raw_branches.get(source_setter), final_setter)
            degraded.extend(errors)
            valid = valid and complete and branch_valid
            if not valid:
                policy = {"mode": "full"}
                degraded.append(final_setter)
            existing = restore_contract.get(final_setter)
            if existing is not None and existing != policy:
                restore_contract[final_setter] = {"mode": "full"}
                conflicts.append(final_setter)
            else:
                restore_contract[final_setter] = policy
            if expectation is not None and branch_valid:
                key, values = expectation
                branch_expectations.setdefault(key, []).extend(values)
        raw_routes = payload.get("component_route_lists") or {}
        if not isinstance(raw_routes, dict):
            raise ValueError(f"invalid component route lists for ib{k}")
        mapped_source_setters = set()
        for raw_component, raw_mapping in raw_routes.items():
            try:
                source_component = int(raw_component)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid route component {raw_component!r} for ib{k}") from exc
            if not isinstance(raw_mapping, dict) or not raw_mapping:
                raise ValueError(
                    f"empty route mapping for component {source_component} ib{k}")
            final_component = _alias_for_component(
                namespace_aliases, k, source_component)
            final_mapping = {}
            for raw_route, source_setter in raw_mapping.items():
                route = str(raw_route or "").strip().lower()
                if route != "base" and not re.fullmatch(r'[0-9a-f]{8}', route):
                    raise ValueError(
                        f"invalid route {raw_route!r} for component "
                        f"{source_component} ib{k}")
                expected = (
                    f"CommandListSetTexturesComponent{source_component}"
                    + ("RouteBase" if route == "base"
                       else f"Route{route}"))
                if str(source_setter).lower() != expected.lower():
                    raise ValueError(
                        f"route {route} for component {source_component} ib{k} "
                        f"points at unexpected setter {source_setter}")
                mapped_source_setters.add(str(source_setter).casefold())
                final_setter = _section_alias_name(
                    str(source_setter), k, namespace_aliases=namespace_aliases)
                if final_setter not in restore_contract:
                    raise ValueError(
                        f"route setter {final_setter} has no restore contract")
                final_mapping[route] = final_setter
            if "base" not in final_mapping:
                raise ValueError(
                    f"component {source_component} ib{k} has no base route setter")
            route_key = (final_component, k)
            if route_key in component_route_lists:
                raise ValueError(
                    f"duplicate route mapping for C{final_component}/ib{k}")
            component_route_lists[route_key] = final_mapping
        orphan_contracts = sorted(
            (source_route_setters[key]
             for key in set(source_route_setters) - mapped_source_setters),
            key=str.casefold)
        if orphan_contracts:
            raise ValueError(
                f"orphan route contract for ib{k}: "
                + ", ".join(orphan_contracts))
    return {
        "restore_contract": restore_contract,
        "branch_expectations": branch_expectations,
        "missing": missing,
        "degraded": sorted(set(degraded)),
        "conflicts": sorted(set(conflicts)),
        "component_route_lists": component_route_lists,
        "sidecars": sidecars,
    }


def _ns_line(line, k, *, shared_globals=None, resource_hashes=None,
             resource_components=None, namespace_aliases=None,
             resource_component_override=None):
    shared_globals = shared_globals or set()
    resource_hashes = resource_hashes or {}

    def _sub_global(m):
        name = m.group(1)
        if name in shared_globals:
            return f'${name}'
        name = _alias_global_var_name(name, k, namespace_aliases)
        return f'${name}_ib{k}'

    def _sub_rescmd(m):
        return _section_alias_name(
            m.group(1), k, resource_hashes, resource_components,
            namespace_aliases, resource_component_override)

    line = _RE_GLOBAL.sub(_sub_global, line)
    line = _RE_RESCMD.sub(_sub_rescmd, line)
    line = line.replace('Meshes/', f'Meshes/ib{k}_')
    return line


def _parse_sections(text):
    header, body, sections = None, [], []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith('[') and s.endswith(']'):
            if header is not None:
                sections.append((header, body))
            header, body = s[1:-1], []
        elif header is not None:
            body.append(ln)
    if header is not None:
        sections.append((header, body))
    return sections


def _texture_filename(path):
    return os.path.basename(str(path).replace("\\", "/"))


def _slot_opt_out_components(body):
    components = set()
    tagged = False
    for line in body:
        stripped = line.strip().lower()
        if not stripped.startswith(';'):
            continue
        if re.fullmatch(r';\s*slot_opt_out_hash_fallback\s*=\s*1', stripped):
            tagged = True
            continue
        match = re.fullmatch(r';\s*opt_out_component\s*=\s*(.+)', stripped)
        if match:
            components.update(int(value) for value in re.findall(r'\d+', match.group(1)))
    return components if tagged and components else set()


def _collect_local_section_references(text):
    references = set()
    for raw_line in text.splitlines():
        line = raw_line.split(";", 1)[0]
        for match in re.finditer(
                r'\bref\s+(Resource[A-Za-z0-9_]+)\b', line, re.I):
            references.add(match.group(1))
        binding = re.match(
            r'^\s*[A-Za-z][A-Za-z0-9_-]*\s*=\s*'
            r'(?:ref\s+)?(Resource[A-Za-z0-9_]+)\b', line, re.I)
        if binding:
            references.add(binding.group(1))
        if re.match(r'^\s*(?:if|else\s+if|elif)\b', line, re.I):
            references.update(re.findall(
                r'\bResource[A-Za-z0-9_]+\b', line, re.I))
        run = re.match(
            r'^\s*run\s*=\s*'
            r'((?:CommandList|CustomShader)[^\s;]+)', line, re.I)
        if run:
            references.add(run.group(1))
    return references


def _is_framework_external_reference(name):
    return "\\" in name or "/" in name


def _slot_resource_components(sections, k, namespace_aliases):
    out = {}
    for header, body in sections:
        match = re.fullmatch(
            r'CommandListSetTexturesComponent(\d+)'
            r'(?:Route(?:Base|[0-9a-fA-F]{8}))?(?:_ib\d+)*', header)
        if not match:
            continue
        comp = _alias_for_component(namespace_aliases, k, int(match.group(1)))
        for line in body:
            for resource in _RE_PST_REF.findall(line):
                out.setdefault(resource, set()).add(comp)
    return out


def _draw_geometry_name(component_id, suffix):
    return f"CommandListDrawGeometryComponent{component_id}{suffix or ''}"


def _skip_var_name(component_id, ordinal, suffix):
    return f"$xscene_skip_draw_c{component_id}_{ordinal}{suffix or ''}"


def _is_geometry_line(line):
    stripped = line.strip()
    if not stripped or stripped.startswith(";"):
        return True
    if re.fullmatch(r'drawindexed\s*=\s*\d+\s*,\s*\d+\s*,\s*-?\d+', stripped):
        return True
    if stripped == "endif" or stripped == "else":
        return True
    return stripped.startswith(("if ", "else if ", "elif "))


def _geometry_control_is_balanced(lines):
    depth = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("if "):
            depth += 1
        elif stripped.startswith(("else if ", "elif ")) or stripped == "else":
            if depth <= 0:
                return False
        elif stripped == "endif":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _draw_comment_indices(body):
    labels = {}
    pending = None
    for index, line in enumerate(body):
        stripped = line.strip()
        if stripped.startswith("; Draw "):
            pending = index
            continue
        if re.fullmatch(r'drawindexed\s*=\s*\d+\s*,\s*\d+\s*,\s*-?\d+', stripped):
            labels[index] = pending
            pending = None
    return labels


def _geometry_region(body, draw_indices, header):
    comments = _draw_comment_indices(body)
    required_start = comments.get(draw_indices[0])
    if required_start is None:
        required_start = draw_indices[0]
    last_draw = draw_indices[-1]
    candidates = []
    for start in range(required_start, -1, -1):
        for end in range(last_draw + 1, len(body) + 1):
            region = body[start:end]
            if not all(_is_geometry_line(line) for line in region):
                continue
            if not _geometry_control_is_balanced(region):
                continue
            candidates.append((end - start, -start, end, start))
    if not candidates:
        raise ValueError(
            "%s has a discontinuous or side-effectful draw region; "
            "cannot build one canonical geometry list" % header)
    _size, _neg_start, end, start = min(candidates)
    return start, end


def _dedent_geometry(lines):
    indents = [
        len(line) - len(line.lstrip())
        for line in lines if line.strip()
    ]
    amount = min(indents) if indents else 0
    if amount <= 0:
        return list(lines), ""
    return [
        line[amount:] if line.strip() else ""
        for line in lines
    ], " " * amount


def _guard_geometry_draws(lines, component_id, suffix, guarded_skips):
    out = []
    ordinal = 0
    for line in lines:
        stripped = line.strip()
        if not re.fullmatch(
                r'drawindexed\s*=\s*\d+\s*,\s*\d+\s*,\s*-?\d+', stripped):
            out.append(line)
            continue
        indent = line[:len(line) - len(line.lstrip())]
        if (component_id, ordinal, suffix) in guarded_skips:
            skip_var = _skip_var_name(component_id, ordinal, suffix)
            out.append(f"{indent}if {skip_var} != 1")
            out.append(f"{indent}    {stripped}")
            out.append(f"{indent}endif")
        else:
            out.append(line)
        ordinal += 1
    return out


def _validate_geometry_layout(text):
    sections = _parse_sections(text)
    section_names = {name for name, _body in sections}
    geometry_run_counts = {}
    for _name, body in sections:
        for line in body:
            match = re.fullmatch(
                r'\s*run\s*=\s*'
                r'(CommandListDrawGeometryComponent\d+(?:_ib\d+)*)\s*',
                line)
            if match:
                target = match.group(1)
                geometry_run_counts[target] = (
                    geometry_run_counts.get(target, 0) + 1)

    for name, body in sections:
        is_geometry = re.fullmatch(
            r'CommandListDrawGeometryComponent\d+(?:_ib\d+)*', name)
        draws = [
            line for line in body
            if re.fullmatch(
                r'drawindexed\s*=\s*\d+\s*,\s*\d+\s*,\s*-?\d+',
                line.strip())
        ]
        inline = re.fullmatch(
            r'(?:CommandListDrawComponent|TextureOverrideComponent)'
            r'(\d+)((?:_ib\d+)*)', name)
        if draws and not is_geometry and not inline:
            raise ValueError("%s contains drawindexed outside a draw caller" % name)
        if draws and inline:
            geometry = _draw_geometry_name(
                int(inline.group(1)), inline.group(2) or "")
            if geometry in section_names or geometry_run_counts.get(geometry):
                raise ValueError(
                    "%s keeps inline drawindexed while shared Geometry %s exists"
                    % (name, geometry))
        if not is_geometry:
            continue
        if not draws:
            raise ValueError("%s contains no drawindexed" % name)
        if geometry_run_counts.get(name, 0) < 2:
            raise ValueError(
                "%s is not reused by at least two draw transactions" % name)
        side_effects = [
            line.strip() for line in body
            if not _is_geometry_line(line)
        ]
        if side_effects:
            raise ValueError(
                "%s contains non-geometry side effect: %s"
                % (name, side_effects[0]))


_ROUTE_SETTER_RE = re.compile(
    r'CommandListSetTexturesComponent(\d+)'
    r'(Route(?:Base|[0-9a-fA-F]{8}))?_ib(\d+)$', re.I)


def _apply_component_route_setters(text, component_route_lists):
    """Bind route-specific setters to base and FoldHost transactions."""
    if not component_route_lists:
        return text, []
    section_names = {name for name, _body in _parse_sections(text)}
    for mapping in component_route_lists.values():
        for setter in mapping.values():
            if setter not in section_names:
                raise ValueError(f"route contract references missing setter {setter}")
    applications = []
    output = []
    header = None
    body = []

    def flush():
        if header is None:
            return
        trigger_indices = [
            index for index, line in enumerate(body)
            if re.fullmatch(
                r'\s*run\s*=\s*CommandListTriggerResourceOverrides_ib\d+\s*',
                line, re.I)
        ]
        route = None
        target = None
        fold_match = re.fullmatch(
            r'TextureOverride_FoldHost_([0-9a-fA-F]{8})_C\d+_ib(\d+)',
            header)
        if fold_match:
            ib_index = int(fold_match.group(2))
            candidates = set()
            for line in body:
                for comp, _route_suffix, ib in re.findall(
                        r'CommandListSetTexturesComponent(\d+)'
                        r'(Route(?:Base|[0-9a-fA-F]{8}))?_ib(\d+)', line, re.I):
                    key = (int(comp), int(ib))
                    if key in component_route_lists:
                        candidates.add(key)
                for comp, ib in re.findall(
                        r'CommandListDraw(?:Geometry)?Component(\d+)_ib(\d+)\b',
                        line, re.I):
                    key = (int(comp), int(ib))
                    if key in component_route_lists:
                        candidates.add(key)
            candidates = {key for key in candidates if key[1] == ib_index}
            if len(candidates) > 1:
                raise ValueError(
                    f"{header} maps to multiple route-bound components: "
                    + ", ".join(f"C{comp}/ib{ib}" for comp, ib in sorted(candidates)))
            if candidates:
                target = next(iter(candidates))
                route = fold_match.group(1).lower()
        else:
            base_match = re.fullmatch(
                r'(?:CommandListDrawComponent|TextureOverrideComponent)'
                r'(\d+)_ib(\d+)', header)
            if base_match:
                candidate = (int(base_match.group(1)), int(base_match.group(2)))
                if candidate in component_route_lists:
                    target = candidate
                    route = "base"

        if target is None:
            output.append((header, list(body)))
            return
        if not trigger_indices and fold_match is None:
            has_transaction_fragment = any(
                re.search(
                    r'CommandListSetTexturesComponent\d+'
                    r'(?:Route(?:Base|[0-9a-fA-F]{8}))?_ib\d+',
                    line, re.I)
                or re.fullmatch(
                    r'\s*run\s*=\s*CommandListCleanupSharedResources_ib\d+\s*',
                    line, re.I)
                for line in body
            )
            if not has_transaction_fragment:
                output.append((header, list(body)))
                return
        mapping = component_route_lists[target]
        desired = mapping.get(route)
        if desired is None:
            raise ValueError(f"{header} has no exact route setter for {route}")
        if desired not in section_names:
            raise ValueError(f"{header} references missing route setter {desired}")
        if not trigger_indices:
            raise ValueError(f"{header} route transaction has no trigger")
        if len(trigger_indices) != 1:
            raise ValueError(
                f"{header} has {len(trigger_indices)} resource-override triggers")
        trigger = trigger_indices[0]
        cleanup = next((
            index for index in range(trigger + 1, len(body))
            if re.fullmatch(
                r'\s*run\s*=\s*CommandListCleanupSharedResources_ib\d+\s*',
                body[index], re.I)), None)
        if cleanup is None:
            raise ValueError(f"{header} route transaction has no cleanup")

        direct_indices = []
        for index in range(trigger + 1, cleanup):
            match = re.fullmatch(
                r'(\s*)run\s*=\s*'
                r'(CommandListSetTexturesComponent\d+'
                r'(?:Route(?:Base|[0-9a-fA-F]{8}))?_ib\d+)\s*',
                body[index], re.I)
            if not match:
                continue
            setter_match = _ROUTE_SETTER_RE.fullmatch(match.group(2))
            if setter_match and (
                    int(setter_match.group(1)), int(setter_match.group(3))) == target:
                direct_indices.append((index, match.group(1), match.group(2)))
        if len(direct_indices) > 1:
            raise ValueError(f"{header} has multiple route setters before cleanup")
        new_body = list(body)
        if direct_indices:
            index, indent, _old = direct_indices[0]
            new_body[index] = f"{indent}run = {desired}"
        else:
            indent = body[trigger][
                :len(body[trigger]) - len(body[trigger].lstrip())]
            new_body.insert(trigger + 1, f"{indent}run = {desired}")
        output.append((header, new_body))
        applications.append({
            "section": header,
            "component": target[0],
            "ib": target[1],
            "route": route,
            "setter": desired,
        })

    preamble = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            flush()
            header = stripped[1:-1]
            body = []
        elif header is None:
            preamble.append(line)
        else:
            body.append(line)
    flush()
    for target, mapping in sorted(component_route_lists.items()):
        component_id, ib_id = target
        base_setter = mapping["base"]
        base_apps = [
            item for item in applications
            if (item["component"], item["ib"], item["route"], item["setter"])
            == (component_id, ib_id, "base", base_setter)
        ]
        if len(base_apps) != 1:
            raise ValueError(
                f"base route contract for C{component_id}/ib{ib_id} must be "
                f"applied exactly once, got {len(base_apps)}")
        for route, setter in sorted(mapping.items()):
            if route == "base":
                continue
            route_apps = [
                item for item in applications
                if (item["component"], item["ib"], item["route"], item["setter"])
                == (component_id, ib_id, route, setter)
            ]
            if not route_apps:
                raise ValueError(
                    f"orphan route contract for C{component_id}/ib{ib_id} "
                    f"route {route}: {setter}")
    parts = list(preamble)
    for section, lines in output:
        parts.append(f"[{section}]")
        parts.extend(lines)
    return "\n".join(parts).rstrip() + "\n", applications


def _canonicalize_draw_geometry_sections(text):
    """Share draw geometry only when another transaction already reuses it."""
    out = []
    geometries = []
    header = None
    body = []
    section_names = {
        name
        for name, _body in _parse_sections(text)
    }
    guarded_skips = {
        (int(comp), int(ordinal), suffix or "")
        for comp, ordinal, suffix in re.findall(
            r'\$xscene_skip_draw_c(\d+)_(\d+)((?:_ib\d+)*)\b',
            text)
    }
    legacy_sections = sorted(
        name for name in section_names
        if re.fullmatch(
            r'CommandListDraw(?:OwnerComponent\d+|AtomComponent\d+_\d+)'
            r'(?:_ib\d+)*', name)
    )
    if legacy_sections:
        raise ValueError(
            "legacy draw Owner/Atom sections cannot be mixed with canonical "
            "Geometry: %s" % ", ".join(legacy_sections))
    generated_names = set()
    geometry_run_counts = {}
    for target in re.findall(
            r'^\s*run\s*=\s*'
            r'(CommandListDrawGeometryComponent\d+(?:_ib\d+)*)\s*$',
            text, re.M):
        geometry_run_counts[target] = geometry_run_counts.get(target, 0) + 1

    def flush():
        if header is None:
            return
        match = re.match(r'CommandListDrawComponent(\d+)((?:_ib\d+)*)$', header)
        if not match:
            override = re.match(r'TextureOverrideComponent(\d+)((?:_ib\d+)*)$', header)
            if override:
                candidate = f"CommandListDrawComponent{override.group(1)}{override.group(2) or ''}"
                if candidate not in section_names:
                    match = override
        if not match:
            out.append((header, list(body)))
            return
        comp_id = int(match.group(1))
        suffix = match.group(2) or ""
        draw_indices = [
            idx for idx, line in enumerate(body)
            if re.fullmatch(
                r'drawindexed\s*=\s*\d+\s*,\s*\d+\s*,\s*-?\d+',
                line.strip())
        ]
        if not draw_indices:
            out.append((header, list(body)))
            return

        geometry = _draw_geometry_name(comp_id, suffix)
        if not geometry_run_counts.get(geometry):
            out.append((header, list(body)))
            return

        draw_start, draw_end = _geometry_region(body, draw_indices, header)
        geometry_body, caller_indent = _dedent_geometry(
            body[draw_start:draw_end])
        if geometry in section_names or geometry in generated_names:
            raise ValueError(
                "%s would define duplicate canonical geometry section %s"
                % (header, geometry))
        generated_names.add(geometry)
        geometry_body = _guard_geometry_draws(
            geometry_body, comp_id, suffix, guarded_skips)
        new_body = list(body[:draw_start])
        new_body.append(f"{caller_indent}run = {geometry}")
        new_body.extend(body[draw_end:])
        out.append((header, new_body))
        geometries.append((geometry, geometry_body))

    preamble = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            flush()
            header, body = stripped[1:-1], []
        elif header is None:
            preamble.append(line)
        else:
            body.append(line)
    flush()
    if not geometries:
        _validate_geometry_layout(text)
        return text

    parts = list(preamble)
    if parts:
        parts.append("")
    for section, lines in out:
        parts.append(f"[{section}]")
        parts.extend(lines)
        parts.append("")
    parts.append("; --- Canonical draw geometry (one list per component/IB) ---")
    parts.append("")
    for section, lines in geometries:
        parts.append(f"[{section}]")
        parts.extend(lines)
        parts.append("")
    result = "\n".join(parts).rstrip() + "\n"
    _validate_geometry_layout(result)
    return result


def assemble(out, mods, texture_root=None, *, write_ini=True, copy_textures=True, partial_export=False,
             suppress_body_hashes=None, namespace_aliases=None):
    """mods: ordered list of per-IB mod folders (each contains mod.ini + Meshes/ + Textures/).
    Writes the merged mod to out, returns a report dict.

    write_ini / copy_textures / partial_export: the user's stock file-output toggles, applied to THIS
    final assembled mod exactly as a single-IB export applies them -- ``Meshes/`` is always written,
    ``mod.ini`` only when ``not partial_export and write_ini``, ``Textures/`` only when
    ``not partial_export and copy_textures``. (The sub-IB exports always wrote everything; the
    orchestrator forces that so the merge can read them.) ``out`` is the user's mod folder, so only
    our own products (Meshes/ Textures/ mod.ini) are cleaned -- never the whole ``out``.

    texture_root: when given, the merged root is the single authoritative HASH-style allowlist --
    only blind-zone hashes whose ``t=<hash>.dds`` still exists directly at the merged root (root-only
    scan, so a ``123/`` trash subfolder counts as deleted, matching stock ``get_textures``) are
    shipped, and each shipped file is copied FROM the merged root (a root edit wins over the per-IB
    copy). Slot-style textures are bound by ps-t slot and ALWAYS ship (pruning one would dangle its
    slot binding); the root edit still wins as the copy source when present. texture_root=None keeps
    the legacy behavior (union of every per-IB referenced texture).

    suppress_body_hashes: hashes whose BODY stock hash fallback is known redundant (mapping
    hash -> reason, or a legacy iterable treated as ``fold-local``). Only mod 0 (body) is
    suppressed; own/editable sub-IBs can still keep a real hash fallback for the same hash."""
    write_textures = (not partial_export) and copy_textures
    write_final_ini = (not partial_export) and write_ini
    # Validate all external evidence before cleaning any existing output.
    delivery_inventory = _texture_delivery.build_delivery_inventory(
        texture_root, mods)
    slot_contracts = _collect_slot_contracts(mods, namespace_aliases)
    # Clean ONLY generated mesh products. Textures follow stock WWMI semantics: existing files are
    # author assets and are not deleted or overwritten.
    os.makedirs(out, exist_ok=True)
    meshes_dir = os.path.join(out, "Meshes")
    textures_dir = os.path.join(out, "Textures")
    if os.path.exists(meshes_dir):
        shutil.rmtree(meshes_dir)
    os.makedirs(meshes_dir, exist_ok=True)
    if write_textures:
        os.makedirs(textures_dir, exist_ok=True)

    # Root-only allowlist + hash -> merged-root file path (the authoritative copy source).
    allowed = None
    root_file_by_hash = {}
    root_name_by_hash = {}
    if texture_root is not None:
        allowed = set()
        for item in delivery_inventory.root_files:
            if item.texture_hash is None:
                continue
            h = item.texture_hash
            allowed.add(h)
            root_file_by_hash.setdefault(h, str(item.path))
            root_name_by_hash.setdefault(h, item.name)

    constants, present, others = [], [], []
    tex = {}                # hash -> source .dds absolute path (deduped; slot + blind-zone)
    blindzone = set()       # hashes still bound hash-style (no slot map covered them) -> one global
                            # [TextureOverride_Texture_<hash>] each, gated by owning $object_detected.
    blindzone_mods = {}     # hash -> mod indexes that still need the stock object-detected fallback
    blindzone_opt_out_components = {}  # hash -> set of (mod index, component id) opt-outs
    slot_hashes = set()     # hashes bound by ps-t slot -> per-IB resources, NO global hash override.
    tex_name = {}            # hash -> shipped filename under Textures/
    emitted_slot_resource_sections = set()
    if isinstance(suppress_body_hashes, dict):
        suppress_body_reasons = {
            str(h).lower(): str(reason or "body")
            for h, reason in suppress_body_hashes.items()
        }
    else:
        suppress_body_reasons = {
            str(h).lower(): "fold-local"
            for h in (suppress_body_hashes or set())
        }
    suppress_body_hashes = set(suppress_body_reasons)
    suppressed_body = set()
    tex_hash_per_mod = []
    # MERGED skeleton: every IB emits an identical [TextureOverrideMarkBoneDataCB] (hash = shared cb4,
    # filter_index = 3381.7777) -- a pure global registration of the bone-data CB. Per-IB duplicates would
    # be N overrides on the same hash; collapse them into ONE shared override (the per-IB skeleton fill stays
    # per-IB via CommandListUpdateMergedSkeleton_ibK in [Present]). COMPONENT mods have no such section, so
    # this path is inert there.
    _MARK_BONE = 'TextureOverrideMarkBoneDataCB'
    mark_bone_body = None
    mark_bone_count = 0
    mark_bone_mismatch = False

    for k, mod in enumerate(mods):
        mod_text = open(os.path.join(mod, "mod.ini"), encoding="utf-8").read()
        sections = _parse_sections(mod_text)
        resource_components = _slot_resource_components(
            sections, k, namespace_aliases)

        # Slot-style textures are rebound inside the component draw scope as
        # `ps-t{n} = ref ResourceTexture{i}`; those resources must be namespaced PER-IB like every
        # other Resource* (the namespaced slot command lists reference ResourceTexture{i}_ib{k}).
        # Hash-style mods have no such refs -> slot_covered empty -> the legacy global collapse below.
        slot_covered = set(_RE_PST_REF.findall(mod_text))

        res_filename, ov_pairs = {}, []
        for h, b in sections:
            if _RE_RESTEX.match(h):
                alias_name = _section_alias_name(
                    h, k, {}, resource_components, namespace_aliases)
                for l in b:
                    m = re.match(r'\s*filename\s*=\s*(.+\.dds)\s*$', l, re.I)
                    if m:
                        res_filename[h] = m.group(1).strip()
                        res_filename[alias_name] = m.group(1).strip()
            elif _RE_OVRTEX.match(h):
                hv = tgt = None
                for l in b:
                    mm = re.match(r'\s*hash\s*=\s*([0-9a-fA-F]+)', l, re.I)
                    if mm:
                        hv = mm.group(1).lower()
                    mm = _RE_TEXTURE_REF.match(l)
                    if mm:
                        tgt = mm.group(1)
                if hv and tgt:
                    ov_pairs.append((hv, tgt, _slot_opt_out_components(b)))
        mod_hashes = set()
        # Blind-zone hash overrides: a slot-covered texture had its TextureOverrideTexture section
        # removed by the slot transform, so anything still carrying a hash override is a fallback.
        for hv, tgt, opt_out_components in ov_pairs:
            if tgt in slot_covered and not opt_out_components:
                continue
            fn = res_filename.get(tgt)
            if not fn:
                continue
            if k == 0 and hv in suppress_body_hashes:
                suppressed_body.add(hv)
                mod_hashes.add(hv)
                continue
            mod_hashes.add(hv)
            blindzone.add(hv)
            blindzone_mods.setdefault(hv, set()).add(k)
            if opt_out_components:
                for comp_id in opt_out_components:
                    blindzone_opt_out_components.setdefault(hv, set()).add(
                        (k, _alias_for_component(namespace_aliases, k, comp_id)))
            if hv not in tex:
                tex[hv] = os.path.join(mod, fn)
                tex_name[hv] = _texture_filename(fn)
        # Slot-covered resources: dedup their .dds by hash (file level) so multiple IBs binding the
        # same texture ship one t=<hash>.dds; the hash is parsed from the filename.
        slot_hash_by_res = {}
        for name in sorted(slot_covered):
            fn = res_filename.get(name)
            if not fn:
                continue
            m = _RE_TEXHASH.search(fn)
            if not m:
                continue
            hv = m.group(1).lower()
            slot_hash_by_res[name] = hv
            mod_hashes.add(hv)
            slot_hashes.add(hv)
            if hv not in tex:
                tex[hv] = os.path.join(mod, fn)
                tex_name[hv] = _texture_filename(fn)
        tex_hash_per_mod.append(mod_hashes)
        resource_hashes = dict(slot_hash_by_res)
        for hv, tgt, _opt_out_components in ov_pairs:
            resource_hashes.setdefault(tgt, hv)

        for h, b in sections:
            if _RE_OVRTEX.match(h):
                continue  # blind-zone hash override -> emitted once globally below
            if _RE_RESTEX.match(h):
                if h in slot_covered:
                    # Keep per-IB (the slot command lists ref ResourceTexture{i}_ib{k}); normalise
                    # the filename to the deduped shipped name so all IBs share one canonical DDS.
                    hv = slot_hash_by_res.get(h)
                    nb = []
                    for l in b:
                        if hv and re.match(r'\s*filename\s*=', l, re.I):
                            shipped_name = root_name_by_hash.get(hv) or tex_name.get(hv) or f't={hv}.dds'
                            nb.append(f'filename = Textures/{shipped_name}')
                        else:
                            nb.append(l)
                    components = resource_components.get(h) or {None}
                    for comp in sorted(components, key=lambda value: -1 if value is None else value):
                        alias_section = _section_alias_name(
                            h, k, resource_hashes, resource_components,
                            namespace_aliases, comp)
                        if alias_section in emitted_slot_resource_sections:
                            continue
                        emitted_slot_resource_sections.add(alias_section)
                        others.append((
                            alias_section,
                            nb))
                # else: blind-zone resource -> collapsed into a global Resource_Texture_<hash>, skip
                continue
            if h == _MARK_BONE:
                # IB-agnostic (hash + filter_index only): keep ONE shared copy, never namespace per-IB.
                body = [l for l in b]
                mark_bone_count += 1
                norm = [l.strip() for l in body if l.strip()]
                if mark_bone_body is None:
                    mark_bone_body = body
                    others.append((h, body))  # emit once, un-namespaced -> single override on the cb4 hash
                elif norm != [l.strip() for l in mark_bone_body if l.strip()]:
                    mark_bone_mismatch = True  # IBs disagree on cb4/filter_index -> NOT one character
                continue
            shared_globals = _SHARED_BODY_GLOBALS if k == 0 else None
            slot_set_match = re.fullmatch(
                r'CommandListSetTexturesComponent(\d+)'
                r'(?:Route(?:Base|[0-9a-fA-F]{8}))?(?:_ib\d+)*', h)
            resource_component_override = None
            if slot_set_match:
                resource_component_override = _alias_for_component(
                    namespace_aliases, k, int(slot_set_match.group(1)))
            nb = [
                _ns_line(
                    l, k, shared_globals=shared_globals,
                    resource_hashes=resource_hashes,
                    resource_components=resource_components,
                    namespace_aliases=namespace_aliases,
                    resource_component_override=resource_component_override)
                for l in b]
            if h == 'Constants':
                constants += [f'; --- ib{k} ---'] + nb
            elif h == 'Present':
                present += [f'; --- ib{k} ---'] + nb
            else:
                others.append((
                    _section_alias_name(
                        h, k, resource_hashes, resource_components,
                        namespace_aliases),
                    nb))

        mesh_src = os.path.join(mod, "Meshes")
        if os.path.isdir(mesh_src):
            for fn in os.listdir(mesh_src):
                shutil.copy(os.path.join(mesh_src, fn), os.path.join(out, "Meshes", f'ib{k}_{fn}'))

    # Slot textures always ship (their slot binding would dangle otherwise); blind-zone (hash-style)
    # textures are gated by the root allowlist (ADR 0013). The root file wins as the copy source.
    shipped = {hv for hv in tex
               if hv in slot_hashes or allowed is None or hv in allowed}
    shipped_name_by_hash = {
        hv: root_name_by_hash.get(hv) or tex_name.get(hv) or f't={hv}.dds'
        for hv in shipped
    }
    required_texture_names = set(shipped_name_by_hash.values())
    if write_textures:
        for hv in tex:
            if hv not in shipped:
                continue
            src = root_file_by_hash[hv] if (allowed is not None and hv in root_file_by_hash) else tex[hv]
            shipped_name = shipped_name_by_hash[hv]
            dst = os.path.join(textures_dir, shipped_name)
            if os.path.exists(dst):
                continue
            shutil.copy(src, dst)
        delivery_report = _texture_delivery.deliver_root_dds(
            delivery_inventory, textures_dir,
            required_names=required_texture_names)
    else:
        delivery_report = _texture_delivery.inspect_root_dds(
            delivery_inventory, textures_dir,
            required_names=required_texture_names)

    gate = ' || '.join(f'$object_detected_ib{k}' for k in range(len(mods)))
    blindzone_shipped = sorted(hv for hv in blindzone if hv in shipped)

    with open(os.path.join(out, "mod.ini"), "w", encoding="utf-8") as f:
        f.write("; WWMI cross-scene multi-IB (namespace-merged; slot-style textures per-IB, "
                "hash-style textures deduped by hash)\n\n")
        f.write("[Constants]\n" + "\n".join(constants))
        f.write("\n\n[Present]\n" + "\n".join(present) + "\n\n")
        for h, b in others:
            f.write(f"[{h}]\n" + "\n".join(b) + "\n\n")
        if blindzone_shipped:
            f.write("; --- Shared hash-style textures (blind-zone fallback, deduped by hash) ---\n\n")
            for hv in blindzone_shipped:
                opt_out_scope = sorted(
                    blindzone_opt_out_components.get(hv, set()))
                gate_terms = [
                    f'$object_detected_ib{k}'
                    for k in sorted(blindzone_mods.get(hv, set()))
                ]
                if not gate_terms:
                    gate_terms = [f'$object_detected_ib{k}' for k in range(len(mods))]
                hv_gate = ' || '.join(gate_terms)
                shipped_name = root_name_by_hash.get(hv) or tex_name.get(hv) or f't={hv}.dds'
                f.write(f"[Resource_Texture_{hv}]\nfilename = Textures/{shipped_name}\n\n")
                f.write(f"[TextureOverride_Texture_{hv}]\n")
                if opt_out_scope:
                    f.write("; slot_opt_out_hash_fallback = 1\n")
                    f.write("; opt_out_component_scope = %s\n" % ", ".join(
                        f"c{comp}_ib{k}" for k, comp in opt_out_scope))
                f.write(f"hash = {hv}\nmatch_priority = 0\n")
                f.write(f"if {hv_gate}\n    this = Resource_Texture_{hv}\nendif\n\n")

    # ---- self-check ----
    ini_path = os.path.join(out, "mod.ini")
    text_before_postprocess = open(ini_path, encoding="utf-8").read()
    text, route_applications = _apply_component_route_setters(
        text_before_postprocess, slot_contracts["component_route_lists"])
    text = _canonicalize_draw_geometry_sections(text)
    text, format_stats = _format_tags.dedupe_format_tag_sections(text)
    restore_contract = slot_contracts["restore_contract"]
    text = _ps_resource_scope.apply_ps_resource_scope(
        text, restore_contract)
    text = _ini_document.stable_functional_sort(text)
    scope_errors = _ps_resource_scope.audit_ps_resource_scope(
        text, restore_contract)
    if text != text_before_postprocess:
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(text)
    with open(ini_path, "rb") as f:
        final_ini_bytes = f.read()
    text = final_ini_bytes.decode("utf-8")
    sections_set = set(re.findall(r'^\[([^\]]+)\]', text, re.M))
    section_keys = {name.casefold() for name in sections_set}
    refs = _collect_local_section_references(text)
    dangling = sorted(
        reference for reference in refs
        if reference.casefold() not in section_keys
        and not _is_framework_external_reference(reference))
    missing = [m.group(1).strip() for m in re.finditer(r'^\s*filename\s*=\s*(.+)$', text, re.M)
               if not os.path.exists(os.path.join(out, m.group(1).strip()))
               # textures are intentionally absent when the final output omits them (copy_textures off
               # / partial_export); don't flag those as a broken mod.
               and (write_textures or not m.group(1).strip().replace('\\', '/').startswith('Textures/'))]
    all_in = set().union(*tex_hash_per_mod) if tex_hash_per_mod else set()
    global_hashes = set(re.findall(r'\[TextureOverride_Texture_([0-9a-f]+)\]', text))
    # MERGED self-check: after collapse there must be EXACTLY one MarkBoneDataCB (or zero for COMPONENT),
    # and every IB must have agreed on its body (same cb4 / filter_index).
    mark_bone_emitted = len(re.findall(r'^\[' + _MARK_BONE + r'(?:_ib\d+)?\]', text, re.M))
    skeleton_ok = (not mark_bone_mismatch) and mark_bone_emitted <= 1
    # The only remaining texture-hash overrides are the blind-zone fallbacks; for a pure slot-style
    # mod this set is empty (= 0 texture-hash). They must match exactly what we emitted.
    tex_conserved = global_hashes == set(blindzone_shipped)
    suppressed_body_reasons = {
        hv: suppress_body_reasons.get(hv, "body")
        for hv in sorted(suppressed_body)
    }
    suppressed_fold = sorted(
        hv for hv, reason in suppressed_body_reasons.items()
        if "fold-local" in reason.split("+")
    )
    root_delivery_ok = (not write_textures) or not delivery_report["root_dds_missing"]
    report = {
        "out": out, "sections": len(sections_set), "refs": len(refs),
        "dangling": dangling, "missing": missing,
        "tex_conserved": tex_conserved,
        "tex_union": len(all_in), "tex_global": len(global_hashes),
        "tex_shipped": len(shipped),
        "tex_slot": sorted(hv for hv in slot_hashes if hv in shipped),
        "tex_blindzone": blindzone_shipped,  # residual hash-style textures (empty == 0-texture-hash)
        "tex_slot_opt_out_fallback": sorted(
            hv for hv in blindzone_shipped
            if blindzone_opt_out_components.get(hv)),
        "tex_suppressed_body": sorted(suppressed_body),
        "tex_suppressed_body_reasons": suppressed_body_reasons,
        "tex_suppressed_fold": suppressed_fold,
        "texture_gate": allowed is not None,
        "tex_root_allowed": (len(allowed) if allowed is not None else None),
        "tex_gated_out": sorted(all_in - shipped),
        "tex_blindzone_gates": {
            hv: [f"$object_detected_ib{k}"
                 for k in sorted(blindzone_mods.get(hv, set()))]
            for hv in blindzone_shipped
        },
        "textures_files": (len(os.listdir(textures_dir)) if os.path.isdir(textures_dir) else 0),
        "meshes_files": len(os.listdir(os.path.join(out, "Meshes"))),
        "ini_sha256": hashlib.sha256(final_ini_bytes).hexdigest(),
        "ini_size": len(final_ini_bytes),
        "section_count": len(sections_set),
        "gate": gate,
        "format_sections_raw": format_stats["format_sections_raw"],
        "format_sections_unique": format_stats["format_sections_unique"],
        "format_sections_removed": format_stats["format_sections_removed"],
        "format_sections_summary": format_stats["format_sections_summary"],
        "mark_bone_collapsed_from": mark_bone_count, "mark_bone_emitted": mark_bone_emitted,
        "mark_bone_mismatch": mark_bone_mismatch, "skeleton_ok": skeleton_ok,
        "scope_errors": scope_errors,
        "geometry_errors": [],
        "slot_contract_sidecars": slot_contracts["sidecars"],
        "slot_contract_missing": slot_contracts["missing"],
        "slot_contract_degraded": slot_contracts["degraded"],
        "slot_contract_conflicts": slot_contracts["conflicts"],
        "slot_restore_contract": restore_contract,
        "slot_branch_expectations": slot_contracts["branch_expectations"],
        "slot_component_route_lists": {
            f"c{component_id}_ib{ib_id}": dict(mapping)
            for (component_id, ib_id), mapping in sorted(
                slot_contracts["component_route_lists"].items())
        },
        "slot_route_applications": route_applications,
        "sound": (not dangling and not missing and tex_conserved and skeleton_ok
                  and not scope_errors and root_delivery_ok),
        "final_ini_written": write_final_ini,
        "final_textures_written": write_textures,
    }
    report.update(delivery_report)
    # The ini was written above so the self-check could validate the merged build; honor the user's
    # file-output toggles on the FINAL mod by dropping it if they asked for no ini / partial export.
    if not write_final_ini:
        try:
            os.remove(os.path.join(out, "mod.ini"))
        except OSError:
            pass
        report["ini_sha256"] = None
        report["ini_size"] = 0
        report["section_count"] = 0
    return report
