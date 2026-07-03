"""Namespace-merge N independent per-IB WWMI mods -> one mod + textures deduped by hash + self-check.

Ported from the standalone, game-verified prototype ``merge_inis.py`` (in-process, no subprocess):
for each IB k, non-texture sections become ``[X]->[X_ibK]``, ``$v->$v_ibK``, ``Resource*/CommandList*->*_ibK``
(the ``\\WWMIv1\\`` framework references are preserved), ``Meshes/->Meshes/ibK_`` (flattened).

Textures take one of two paths per IB:
  * Slot-style (velo): textures rebound inside the component draw scope as ``ps-t{n} = ref
    ResourceTexture{i}`` are kept PER-IB (``[ResourceTexture{i}]`` -> ``[ResourceTexture{i}_ibK]``,
    filename normalised to the deduped ``Textures/Components-* t=<hash>.dds``), so the namespaced
    slot command lists resolve. They carry NO texture-hash matching -> immune to streaming.
  * Hash-style (stock / slot blind-zone fallback): ``[TextureOverrideTexture{i}]`` hash overrides
    collapse into one global ``[Resource_Texture_<hash>]``/``[TextureOverride_Texture_<hash>]`` per
    unique hash (gate = OR over each IB's ``$object_detected``), as before. Explicitly
    component-scoped excluded-component fallbacks keep their per-component gate instead.

``[Constants]``/``[Present]`` each merged into one section. Returns a self-check report (the
``tex_blindzone`` field lists residual hash-style textures -> empty == a pure 0-texture-hash mod).
"""
import os
import re
import shutil
import importlib.util


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


_format_tags = _load_format_tags()
_ps_resource_scope = _load_ps_resource_scope()

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
_RE_COMPONENT_FALLBACK_VAR = re.compile(r'\$component_hash_fallback_c(\d+)(?:_ib\d+)*\b')
_SHARED_BODY_GLOBALS = {"form_id"}


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
    match = re.fullmatch(r'component_hash_fallback_c(\d+)', name)
    if match:
        comp = _alias_for_component(namespace_aliases, k, int(match.group(1)))
        return f'component_hash_fallback_c{comp}'
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
    comp_match = re.fullmatch(r'(CommandListSetTexturesComponent|CommandListProbeComponent|'
                              r'CommandListDrawComponent|CommandListDrawOwnerComponent|'
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


def _component_fallback_components(body):
    components = set()
    tagged = False
    for line in body:
        stripped = line.strip().lower()
        if stripped.startswith(';'):
            if 'component_scoped_hash_fallback' in stripped:
                tagged = True
            continue
        for match in _RE_COMPONENT_FALLBACK_VAR.finditer(line):
            components.add(int(match.group(1)))
    return components if tagged and components else set()


def _slot_resource_components(sections, k, namespace_aliases):
    out = {}
    for header, body in sections:
        match = re.fullmatch(r'CommandListSetTexturesComponent(\d+)(?:_ib\d+)*', header)
        if not match:
            continue
        comp = _alias_for_component(namespace_aliases, k, int(match.group(1)))
        for line in body:
            for resource in _RE_PST_REF.findall(line):
                out.setdefault(resource, set()).add(comp)
    return out


def _draw_atom_name(component_id, ordinal, suffix):
    return f"CommandListDrawAtomComponent{component_id}_{ordinal}{suffix or ''}"


def _draw_owner_name(component_id, suffix):
    return f"CommandListDrawOwnerComponent{component_id}{suffix or ''}"


def _skip_var_name(component_id, ordinal, suffix):
    return f"$xscene_skip_draw_c{component_id}_{ordinal}{suffix or ''}"


def _atomize_draw_owner_sections(text):
    """Move actual drawindexed calls into deterministic atom command lists.

    Existing CommandListDrawComponent sections keep setup, LOD dispatch, slot
    rebinding and cleanup lines, then run one canonical draw owner. The owner
    owns the draw atom list; FoldHost sections may run that owner, but must not
    expand the same atom list themselves.
    """
    out = []
    owners = []
    atoms = []
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
            if re.match(r'\s*drawindexed = \d+, \d+, -?\d+', line)
        ]
        if not draw_indices:
            out.append((header, list(body)))
            return

        draw_start = draw_indices[0]
        while draw_start > 0:
            prev = body[draw_start - 1].strip()
            if prev.startswith(("if ", "else if ", "elif ", "else")):
                draw_start -= 1
                continue
            if prev.startswith("; Draw "):
                draw_start -= 1
                continue
            break
        draw_end = len(body)
        for idx in range(draw_indices[-1] + 1, len(body)):
            if re.match(r'\s*run\s*=\s*CommandListCleanupSharedResources(?:_ib\d+)*\b', body[idx]):
                draw_end = idx
                break

        owner_anchor = body[draw_start] if draw_start < len(body) else body[draw_indices[0]]
        owner_indent = owner_anchor[:len(owner_anchor) - len(owner_anchor.lstrip())]
        owner = _draw_owner_name(comp_id, suffix)
        new_body = list(body[:draw_start])
        new_body.append(f"{owner_indent}run = {owner}")
        new_body.extend(body[draw_end:])

        owner_body = []
        pending_comment = None
        ordinal = 0
        owner_control_depth = 0
        for line in body[draw_start:draw_end]:
            stripped = line.strip()
            if stripped.startswith("; Draw "):
                pending_comment = stripped
                owner_body.append(line)
                continue
            if stripped == "endif":
                owner_control_depth = max(0, owner_control_depth - 1)
                owner_body.append(line)
                continue
            if stripped.startswith(("else if ", "elif ", "else")):
                owner_body.append(line)
                continue
            if stripped.startswith("if "):
                owner_body.append(line)
                owner_control_depth += 1
                continue
            draw = re.match(r'drawindexed = (\d+), (\d+), (-?\d+)', stripped)
            if not draw:
                owner_body.append(line)
                continue
            atom = _draw_atom_name(comp_id, ordinal, suffix)
            indent = line[:len(line) - len(line.lstrip())]
            if not indent and owner_control_depth:
                indent = "    " * owner_control_depth
            skip_var = _skip_var_name(comp_id, ordinal, suffix)
            if (comp_id, ordinal, suffix) in guarded_skips:
                owner_body.append(f"{indent}if {skip_var} != 1")
                owner_body.append(f"{indent}    run = {atom}")
                owner_body.append(f"{indent}endif")
            else:
                owner_body.append(f"{indent}run = {atom}")
            atom_body = []
            if pending_comment:
                atom_body.append(pending_comment)
            atom_body.append(stripped)
            atoms.append((atom, atom_body))
            pending_comment = None
            ordinal += 1
        out.append((header, new_body))
        owners.append((owner, owner_body))

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
    if not atoms:
        return text

    parts = list(preamble)
    if parts:
        parts.append("")
    for section, lines in out:
        parts.append(f"[{section}]")
        parts.extend(lines)
        parts.append("")
    parts.append("; --- Draw owners (canonical draw route per component) ---")
    parts.append("")
    for section, lines in owners:
        parts.append(f"[{section}]")
        parts.extend(lines)
        parts.append("")
    parts.append("; --- Draw owner atoms (one actual drawindexed per atom) ---")
    parts.append("")
    for section, lines in atoms:
        parts.append(f"[{section}]")
        parts.extend(lines)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


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
        for name in sorted(os.listdir(texture_root)):
            if not name.lower().endswith(".dds"):
                continue
            m = _RE_TEXHASH.search(name)
            if not m:
                continue
            h = m.group(1).lower()
            allowed.add(h)
            root_file_by_hash.setdefault(h, os.path.join(texture_root, name))
            root_name_by_hash.setdefault(h, name)

    constants, present, others = [], [], []
    tex = {}                # hash -> source .dds absolute path (deduped; slot + blind-zone)
    blindzone = set()       # hashes still bound hash-style (no slot map covered them) -> one global
                            # [TextureOverride_Texture_<hash>] each, gated by $object_detected
                            # or by explicit component-scoped fallback vars.
    blindzone_mods = {}     # hash -> mod indexes that still need the stock object-detected fallback
    blindzone_component_mods = {}  # hash -> set of (mod index, component id) scoped fallbacks
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
                    ov_pairs.append((hv, tgt, _component_fallback_components(b)))
        mod_hashes = set()
        # Blind-zone hash overrides: a slot-covered texture had its TextureOverrideTexture section
        # removed by the slot transform, so anything still carrying a hash override is a fallback.
        for hv, tgt, component_scope in ov_pairs:
            if tgt in slot_covered and not component_scope:
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
            if component_scope:
                for comp_id in component_scope:
                    blindzone_component_mods.setdefault(hv, set()).add(
                        (k, _alias_for_component(namespace_aliases, k, comp_id)))
            else:
                blindzone_mods.setdefault(hv, set()).add(k)
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
        for hv, tgt, _component_scope in ov_pairs:
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
                r'CommandListSetTexturesComponent(\d+)(?:_ib\d+)*', h)
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
    if write_textures:
        for hv in tex:
            if hv not in shipped:
                continue
            src = root_file_by_hash[hv] if (allowed is not None and hv in root_file_by_hash) else tex[hv]
            shipped_name = root_name_by_hash.get(hv) or tex_name.get(hv) or f't={hv}.dds'
            dst = os.path.join(textures_dir, shipped_name)
            if os.path.exists(dst):
                continue
            shutil.copy(src, dst)

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
                component_scope = sorted(blindzone_component_mods.get(hv, set()))
                gate_terms = [
                    f'$object_detected_ib{k}'
                    for k in sorted(blindzone_mods.get(hv, set()))
                ]
                gate_terms.extend(
                    f'$component_hash_fallback_c{comp}_ib{k} == 1'
                    for k, comp in component_scope
                )
                if not gate_terms:
                    gate_terms = [f'$object_detected_ib{k}' for k in range(len(mods))]
                hv_gate = ' || '.join(gate_terms)
                shipped_name = root_name_by_hash.get(hv) or tex_name.get(hv) or f't={hv}.dds'
                f.write(f"[Resource_Texture_{hv}]\nfilename = Textures/{shipped_name}\n\n")
                f.write(f"[TextureOverride_Texture_{hv}]\n")
                if component_scope:
                    f.write("; component_scoped_hash_fallback = 1\n")
                    f.write("; fallback_component_scope = %s\n" % ", ".join(
                        f"c{comp}_ib{k}" for k, comp in component_scope))
                f.write(f"hash = {hv}\nmatch_priority = 0\n")
                f.write(f"if {hv_gate}\n    this = Resource_Texture_{hv}\nendif\n\n")

    # ---- self-check ----
    ini_path = os.path.join(out, "mod.ini")
    text_before_postprocess = open(ini_path, encoding="utf-8").read()
    text = _atomize_draw_owner_sections(text_before_postprocess)
    text, format_stats = _format_tags.dedupe_format_tag_sections(text)
    text = _ps_resource_scope.apply_ps_resource_scope(text)
    if text != text_before_postprocess:
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(text)
    sections_set = set(re.findall(r'^\[([^\]]+)\]', text, re.M))
    refs = set(re.findall(r'(?:ref|run\s*=|this\s*=)\s+(Resource[A-Za-z0-9_]+|CommandList[A-Za-z0-9_]+)', text))
    dangling = sorted(r for r in refs if r not in sections_set)
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
    report = {
        "out": out, "sections": len(sections_set), "refs": len(refs),
        "dangling": dangling, "missing": missing,
        "tex_conserved": tex_conserved,
        "tex_union": len(all_in), "tex_global": len(global_hashes),
        "tex_shipped": len(shipped),
        "tex_slot": sorted(hv for hv in slot_hashes if hv in shipped),
        "tex_blindzone": blindzone_shipped,  # residual hash-style textures (empty == 0-texture-hash)
        "tex_component_scoped_fallback": sorted(
            hv for hv in blindzone_shipped
            if blindzone_component_mods.get(hv)),
        "tex_suppressed_body": sorted(suppressed_body),
        "tex_suppressed_body_reasons": suppressed_body_reasons,
        "tex_suppressed_fold": suppressed_fold,
        "texture_gate": allowed is not None,
        "tex_root_allowed": (len(allowed) if allowed is not None else None),
        "tex_gated_out": sorted(all_in - shipped),
        "tex_blindzone_gates": {
            hv: (
                [f"$object_detected_ib{k}" for k in sorted(blindzone_mods.get(hv, set()))]
                + [f"$component_hash_fallback_c{comp}_ib{k} == 1"
                   for k, comp in sorted(blindzone_component_mods.get(hv, set()))]
            )
            for hv in blindzone_shipped
        },
        "textures_files": (len(os.listdir(textures_dir)) if os.path.isdir(textures_dir) else 0),
        "meshes_files": len(os.listdir(os.path.join(out, "Meshes"))),
        "ini_size": len(text), "gate": gate,
        "format_sections_raw": format_stats["format_sections_raw"],
        "format_sections_unique": format_stats["format_sections_unique"],
        "format_sections_removed": format_stats["format_sections_removed"],
        "format_sections_summary": format_stats["format_sections_summary"],
        "mark_bone_collapsed_from": mark_bone_count, "mark_bone_emitted": mark_bone_emitted,
        "mark_bone_mismatch": mark_bone_mismatch, "skeleton_ok": skeleton_ok,
        "sound": not dangling and not missing and tex_conserved and skeleton_ok,
        "final_ini_written": write_final_ini,
        "final_textures_written": write_textures,
    }
    # The ini was written above so the self-check could validate the merged build; honor the user's
    # file-output toggles on the FINAL mod by dropping it if they asked for no ini / partial export.
    if not write_final_ini:
        try:
            os.remove(os.path.join(out, "mod.ini"))
        except OSError:
            pass
    return report
