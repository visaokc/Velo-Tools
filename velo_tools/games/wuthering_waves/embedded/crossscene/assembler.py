"""命名空间合并 N 个独立 per-IB WWMI mod → 一个 mod + 贴图按 hash 去重 + 自检。

port 自已游戏验证的独立原型 ``merge_inis.py``（in-process，无 subprocess）：
每条 IB k 的非贴图段 ``[X]→[X_ibK]``、``$v→$v_ibK``、``Resource*/CommandList*→*_ibK``
（保留 ``\\WWMIv1\\`` 框架引用）、``Meshes/→Meshes/ibK_``（扁平）；贴图段塌缩成每唯一 hash
一条全局 ``[Resource_Texture_<hash>]``/``[TextureOverride_Texture_<hash>]``（gate=各 IB
``$object_detected`` 求或）；``[Constants]``/``[Present]`` 各并一段。返回自检 report。
"""
import os
import re
import shutil

_RE_GLOBAL = re.compile(r'\$([A-Za-z]\w*)')
_RE_RESCMD = re.compile(r'\b(Resource[A-Za-z0-9_]+|CommandList[A-Za-z0-9_]+)\b')
_RE_RESTEX = re.compile(r'ResourceTexture\d+$')
_RE_OVRTEX = re.compile(r'TextureOverrideTexture\d+$')


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


def assemble(out, mods):
    """mods: 有序的 per-IB mod 文件夹列表（每个含 mod.ini + Meshes/ + Textures/）。
    写出合并 mod 到 out，返回 report dict。"""
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(os.path.join(out, "Meshes"), exist_ok=True)
    os.makedirs(os.path.join(out, "Textures"), exist_ok=True)

    constants, present, others = [], [], []
    tex = {}                # hash -> 源 .dds 绝对路径（去重）
    tex_hash_per_mod = []

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

    for hv, src in tex.items():
        shutil.copy(src, os.path.join(out, "Textures", f't={hv}.dds'))

    gate = ' || '.join(f'$object_detected_ib{k}' for k in range(len(mods)))

    with open(os.path.join(out, "mod.ini"), "w", encoding="utf-8") as f:
        f.write("; WWMI cross-scene multi-IB (namespace-merged, textures deduped by hash)\n\n")
        f.write("[Constants]\n" + "\n".join(constants))
        f.write("\n\n[Present]\n" + "\n".join(present) + "\n\n")
        for h, b in others:
            f.write(f"[{h}]\n" + "\n".join(b) + "\n\n")
        f.write("; --- Shared textures (deduped by hash, global overrides) ---\n\n")
        for hv in sorted(tex.keys()):
            f.write(f"[Resource_Texture_{hv}]\nfilename = Textures/t={hv}.dds\n\n")
            f.write(f"[TextureOverride_Texture_{hv}]\nhash = {hv}\nmatch_priority = 0\n")
            f.write(f"if {gate}\n    this = Resource_Texture_{hv}\nendif\n\n")

    # ---- 自检 ----
    text = open(os.path.join(out, "mod.ini"), encoding="utf-8").read()
    sections_set = set(re.findall(r'^\[([^\]]+)\]', text, re.M))
    refs = set(re.findall(r'(?:ref|run\s*=|this\s*=)\s+(Resource[A-Za-z0-9_]+|CommandList[A-Za-z0-9_]+)', text))
    dangling = sorted(r for r in refs if r not in sections_set)
    missing = [m.group(1).strip() for m in re.finditer(r'^\s*filename\s*=\s*(.+)$', text, re.M)
               if not os.path.exists(os.path.join(out, m.group(1).strip()))]
    all_in = set().union(*tex_hash_per_mod) if tex_hash_per_mod else set()
    global_hashes = set(re.findall(r'\[TextureOverride_Texture_([0-9a-f]+)\]', text))
    report = {
        "out": out, "sections": len(sections_set), "refs": len(refs),
        "dangling": dangling, "missing": missing,
        "tex_conserved": all_in == global_hashes,
        "tex_union": len(all_in), "tex_global": len(global_hashes),
        "textures_files": len(os.listdir(os.path.join(out, "Textures"))),
        "meshes_files": len(os.listdir(os.path.join(out, "Meshes"))),
        "ini_size": len(text), "gate": gate,
        "sound": not dangling and not missing and (all_in == global_hashes),
    }
    return report
