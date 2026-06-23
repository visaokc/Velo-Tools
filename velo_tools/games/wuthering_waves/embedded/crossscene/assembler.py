"""Namespace-merge N independent per-IB WWMI mods -> one mod + textures deduped by hash + self-check.

Ported from the standalone, game-verified prototype ``merge_inis.py`` (in-process, no subprocess):
for each IB k, non-texture sections become ``[X]->[X_ibK]``, ``$v->$v_ibK``, ``Resource*/CommandList*->*_ibK``
(the ``\\WWMIv1\\`` framework references are preserved), ``Meshes/->Meshes/ibK_`` (flattened); texture sections
collapse into one global ``[Resource_Texture_<hash>]``/``[TextureOverride_Texture_<hash>]`` per unique hash
(gate = OR over each IB's ``$object_detected``); ``[Constants]``/``[Present]`` each merged into one section. Returns a self-check report.
"""
import os
import re
import shutil

_RE_GLOBAL = re.compile(r'\$([A-Za-z]\w*)')
_RE_RESCMD = re.compile(r'\b(Resource[A-Za-z0-9_]+|CommandList[A-Za-z0-9_]+)\b')
_RE_RESTEX = re.compile(r'ResourceTexture\d+$')
_RE_OVRTEX = re.compile(r'TextureOverrideTexture\d+$')
_RE_TEXHASH = re.compile(r't=([0-9a-fA-F]+)')


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


def assemble(out, mods, texture_root=None):
    """mods: ordered list of per-IB mod folders (each contains mod.ini + Meshes/ + Textures/).
    Writes the merged mod to out, returns a report dict.

    texture_root: when given, the merged root is the single authoritative texture allowlist --
    only hashes whose ``t=<hash>.dds`` still exists directly at the merged root (root-only scan,
    so a ``123/`` trash subfolder counts as deleted, matching stock ``get_textures``) are shipped,
    and each shipped file is copied FROM the merged root (a root edit wins over the per-IB copy).
    Sub-IB folders (scene_ibs/<hash>/) no longer re-supply a hash the author pruned from the root.
    texture_root=None keeps the legacy behavior (union of every per-IB referenced texture)."""
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "Meshes"), exist_ok=True)
    os.makedirs(os.path.join(out, "Textures"), exist_ok=True)

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
    tex = {}                # hash -> source .dds absolute path (deduped)
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
        sections = _parse_sections(open(os.path.join(mod, "mod.ini"), encoding="utf-8").read())

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
        for hv, tgt in ov_pairs:
            fn = res_filename.get(tgt)
            if not fn:
                continue
            mod_hashes.add(hv)
            if hv not in tex:
                tex[hv] = os.path.join(mod, fn)
        tex_hash_per_mod.append(mod_hashes)

        for h, b in sections:
            if _RE_RESTEX.match(h) or _RE_OVRTEX.match(h):
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

    shipped = {hv for hv in tex if allowed is None or hv in allowed}
    for hv in tex:
        if hv not in shipped:
            continue
        # When gating, copy the authoritative merged-root file; otherwise the per-IB copy.
        src = root_file_by_hash[hv] if allowed is not None else tex[hv]
        shutil.copy(src, os.path.join(out, "Textures", f't={hv}.dds'))

    gate = ' || '.join(f'$object_detected_ib{k}' for k in range(len(mods)))

    with open(os.path.join(out, "mod.ini"), "w", encoding="utf-8") as f:
        f.write("; WWMI cross-scene multi-IB (namespace-merged, textures deduped by hash)\n\n")
        f.write("[Constants]\n" + "\n".join(constants))
        f.write("\n\n[Present]\n" + "\n".join(present) + "\n\n")
        for h, b in others:
            f.write(f"[{h}]\n" + "\n".join(b) + "\n\n")
        f.write("; --- Shared textures (deduped by hash, global overrides) ---\n\n")
        for hv in sorted(shipped):
            f.write(f"[Resource_Texture_{hv}]\nfilename = Textures/t={hv}.dds\n\n")
            f.write(f"[TextureOverride_Texture_{hv}]\nhash = {hv}\nmatch_priority = 0\n")
            f.write(f"if {gate}\n    this = Resource_Texture_{hv}\nendif\n\n")

    # ---- self-check ----
    text = open(os.path.join(out, "mod.ini"), encoding="utf-8").read()
    sections_set = set(re.findall(r'^\[([^\]]+)\]', text, re.M))
    refs = set(re.findall(r'(?:ref|run\s*=|this\s*=)\s+(Resource[A-Za-z0-9_]+|CommandList[A-Za-z0-9_]+)', text))
    dangling = sorted(r for r in refs if r not in sections_set)
    missing = [m.group(1).strip() for m in re.finditer(r'^\s*filename\s*=\s*(.+)$', text, re.M)
               if not os.path.exists(os.path.join(out, m.group(1).strip()))]
    all_in = set().union(*tex_hash_per_mod) if tex_hash_per_mod else set()
    global_hashes = set(re.findall(r'\[TextureOverride_Texture_([0-9a-f]+)\]', text))
    # MERGED self-check: after collapse there must be EXACTLY one MarkBoneDataCB (or zero for COMPONENT),
    # and every IB must have agreed on its body (same cb4 / filter_index).
    mark_bone_emitted = len(re.findall(r'^\[' + _MARK_BONE + r'(?:_ib\d+)?\]', text, re.M))
    skeleton_ok = (not mark_bone_mismatch) and mark_bone_emitted <= 1
    report = {
        "out": out, "sections": len(sections_set), "refs": len(refs),
        "dangling": dangling, "missing": missing,
        "tex_conserved": global_hashes == shipped,
        "tex_union": len(all_in), "tex_global": len(global_hashes),
        "tex_shipped": len(shipped),
        "texture_gate": allowed is not None,
        "tex_root_allowed": (len(allowed) if allowed is not None else None),
        "tex_gated_out": sorted(all_in - shipped),
        "textures_files": len(os.listdir(os.path.join(out, "Textures"))),
        "meshes_files": len(os.listdir(os.path.join(out, "Meshes"))),
        "ini_size": len(text), "gate": gate,
        "mark_bone_collapsed_from": mark_bone_count, "mark_bone_emitted": mark_bone_emitted,
        "mark_bone_mismatch": mark_bone_mismatch, "skeleton_ok": skeleton_ok,
        "sound": not dangling and not missing and (global_hashes == shipped) and skeleton_ok,
    }
    return report
