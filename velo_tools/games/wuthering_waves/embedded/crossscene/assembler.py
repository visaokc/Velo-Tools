"""Namespace-merge N independent per-IB WWMI mods -> one mod + textures deduped by hash + self-check.

Ported from the standalone, game-verified prototype ``merge_inis.py`` (in-process, no subprocess):
for each IB k, non-texture sections become ``[X]->[X_ibK]``, ``$v->$v_ibK``, ``Resource*/CommandList*->*_ibK``
(the ``\\WWMIv1\\`` framework references are preserved), ``Meshes/->Meshes/ibK_`` (flattened).

Textures take one of two paths per IB:
  * Slot-style (velo): textures rebound inside the component draw scope as ``ps-t{n} = ref
    ResourceTexture{i}`` are kept PER-IB (``[ResourceTexture{i}]`` -> ``[ResourceTexture{i}_ibK]``,
    filename normalised to the deduped ``Textures/t=<hash>.dds``), so the namespaced slot command
    lists resolve. They carry NO texture-hash matching -> immune to streaming.
  * Hash-style (stock / slot blind-zone fallback): ``[TextureOverrideTexture{i}]`` hash overrides
    collapse into one global ``[Resource_Texture_<hash>]``/``[TextureOverride_Texture_<hash>]`` per
    unique hash (gate = OR over each IB's ``$object_detected``), as before.

``[Constants]``/``[Present]`` each merged into one section. Returns a self-check report (the
``tex_blindzone`` field lists residual hash-style textures -> empty == a pure 0-texture-hash mod).
"""
import os
import re
import shutil

_RE_GLOBAL = re.compile(r'\$([A-Za-z]\w*)')
_RE_RESCMD = re.compile(r'\b(Resource[A-Za-z0-9_]+|CommandList[A-Za-z0-9_]+)\b')
_RE_RESTEX = re.compile(r'ResourceTexture\d+$')
_RE_OVRTEX = re.compile(r'TextureOverrideTexture\d+$')
_RE_TEXHASH = re.compile(r't=([0-9a-fA-F]+)')
# Slot-style binds: `ps-t{n} = ref ResourceTexture{i}` (the trailing \b keeps ResourceTextureBackupT*
# -- the per-slot backup resources -- out; those are namespaced via the generic Resource* path).
_RE_PST_REF = re.compile(r'ps-t\d+\s*=\s*ref\s+(ResourceTexture\d+)\b')


def _ns_line(line, k):
    line = _RE_GLOBAL.sub(lambda m: f'${m.group(1)}_ib{k}', line)
    line = _RE_RESCMD.sub(lambda m: f'{m.group(1)}_ib{k}', line)
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


def assemble(out, mods, texture_root=None, *, write_ini=True, copy_textures=True, partial_export=False,
             suppress_body_hashes=None):
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
    # Clean ONLY our own products (out is the user's mod folder now; never rmtree the whole thing).
    os.makedirs(out, exist_ok=True)
    meshes_dir = os.path.join(out, "Meshes")
    textures_dir = os.path.join(out, "Textures")
    for _p in (meshes_dir, textures_dir):
        if os.path.exists(_p):
            shutil.rmtree(_p)
    os.makedirs(meshes_dir, exist_ok=True)
    if write_textures:
        os.makedirs(textures_dir, exist_ok=True)

    # Root-only allowlist + hash -> merged-root file path (the authoritative copy source).
    allowed = None
    root_file_by_hash = {}
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

    constants, present, others = [], [], []
    tex = {}                # hash -> source .dds absolute path (deduped; slot + blind-zone)
    blindzone = set()       # hashes still bound hash-style (no slot map covered them) -> one global
                            # [TextureOverride_Texture_<hash>] each, gated by $object_detected.
    blindzone_mods = {}     # hash -> mod indexes that still need the hash-style fallback
    slot_hashes = set()     # hashes bound by ps-t slot -> per-IB resources, NO global hash override.
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

        # Slot-style textures are rebound inside the component draw scope as
        # `ps-t{n} = ref ResourceTexture{i}`; those resources must be namespaced PER-IB like every
        # other Resource* (the namespaced slot command lists reference ResourceTexture{i}_ib{k}).
        # Hash-style mods have no such refs -> slot_covered empty -> the legacy global collapse below.
        slot_covered = set(_RE_PST_REF.findall(mod_text))

        res_filename, ov_pairs = {}, []
        for h, b in sections:
            if _RE_RESTEX.match(h):
                for l in b:
                    m = re.match(r'\s*filename\s*=\s*(.+\.dds)\s*$', l, re.I)
                    if m:
                        res_filename[h] = m.group(1).strip()
            elif _RE_OVRTEX.match(h):
                hv = tgt = None
                for l in b:
                    mm = re.match(r'\s*hash\s*=\s*([0-9a-fA-F]+)', l, re.I)
                    if mm:
                        hv = mm.group(1).lower()
                    mm = re.match(r'\s*this\s*=\s*(ResourceTexture\d+)', l, re.I)
                    if mm:
                        tgt = mm.group(1)
                if hv and tgt:
                    ov_pairs.append((hv, tgt))
        mod_hashes = set()
        # Blind-zone hash overrides: a slot-covered texture had its TextureOverrideTexture section
        # removed by the slot transform, so anything still carrying a hash override is a fallback.
        for hv, tgt in ov_pairs:
            if tgt in slot_covered:
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
            if hv not in tex:
                tex[hv] = os.path.join(mod, fn)
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
        tex_hash_per_mod.append(mod_hashes)

        for h, b in sections:
            if _RE_OVRTEX.match(h):
                continue  # blind-zone hash override -> emitted once globally below
            if _RE_RESTEX.match(h):
                if h in slot_covered:
                    # Keep per-IB (the slot command lists ref ResourceTexture{i}_ib{k}); normalise
                    # the filename to the deduped shipped name so all IBs share one t=<hash>.dds.
                    hv = slot_hash_by_res.get(h)
                    nb = []
                    for l in b:
                        if hv and re.match(r'\s*filename\s*=', l, re.I):
                            nb.append(f'filename = Textures/t={hv}.dds')
                        else:
                            nb.append(l)
                    others.append((f'{h}_ib{k}', nb))
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
            nb = [_ns_line(l, k) for l in b]
            if h == 'Constants':
                constants += [f'; --- ib{k} ---'] + nb
            elif h == 'Present':
                present += [f'; --- ib{k} ---'] + nb
            else:
                others.append((f'{h}_ib{k}', nb))

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
            shutil.copy(src, os.path.join(textures_dir, f't={hv}.dds'))

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
                hv_gate = ' || '.join(
                    f'$object_detected_ib{k}'
                    for k in sorted(blindzone_mods.get(hv) or range(len(mods))))
                f.write(f"[Resource_Texture_{hv}]\nfilename = Textures/t={hv}.dds\n\n")
                f.write(f"[TextureOverride_Texture_{hv}]\nhash = {hv}\nmatch_priority = 0\n")
                f.write(f"if {hv_gate}\n    this = Resource_Texture_{hv}\nendif\n\n")

    # ---- self-check ----
    text = open(os.path.join(out, "mod.ini"), encoding="utf-8").read()
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
        "tex_suppressed_body": sorted(suppressed_body),
        "tex_suppressed_body_reasons": suppressed_body_reasons,
        "tex_suppressed_fold": suppressed_fold,
        "texture_gate": allowed is not None,
        "tex_root_allowed": (len(allowed) if allowed is not None else None),
        "tex_gated_out": sorted(all_in - shipped),
        "tex_blindzone_gates": {
            hv: [f"$object_detected_ib{k}" for k in sorted(blindzone_mods.get(hv, set()))]
            for hv in blindzone_shipped
        },
        "textures_files": (len(os.listdir(textures_dir)) if os.path.isdir(textures_dir) else 0),
        "meshes_files": len(os.listdir(os.path.join(out, "Meshes"))),
        "ini_size": len(text), "gate": gate,
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
