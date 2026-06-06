"""WWMI 跨场景多 IB 合并 —— 前处理 producer（host 驱动层，绝不改 ``_wwmi_core``）。

把"基底 IB 提取（showcase 8287b2f2 = Component 0-7 整角色）+ N 个其它帧提取的 dungeon IB
文件夹（衣服/面部/腰饰熊）"合并成**一份可编辑提取文件夹** + 一份 ``CrossSceneRouting.json``：

- 用**位置网格匹配**（同一份网格、position bit 级相同 → 精确，不用 EFMI 的 Chamfer 启发式）
  找出每条 dungeon IB 对应的基底 component 集；检测"单条 IB 被一个 Component 完全包裹"
  （腰饰熊 ⊆ Component 5）→ 触发把该 Component **buffer 级拆分**成 ``Component 5``(余) +
  ``Component 5.001``(熊) 两套 ``.vb/.ib/.fmt``，使导入后熊是可独立编辑的子对象。
- 各 dungeon IB 的原生提取整份拷进 ``scene_ibs/<hash>/``，供后续"一键智能导出"逐 IB 重导入作 host。
- ``CrossSceneRouting.json`` 记录导出期所需的全部路由信息（见 ``_build_routing``）。

本模块只读 ``_wwmi_core`` 的 buffer/fmt I/O（``NumpyBuffer``/``MigotoFormat``），不改其任何一行。
导出消费侧在 ``embedded/crossscene/`` 另行实现（monkey-patch ``VTWW_Export.execute``）。
"""

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path

import numpy as np

from ._wwmi_core.migoto_io.data_model.byte_buffer import (
    MigotoFormat, NumpyBuffer, Semantic, AbstractSemantic,
)

GRID = 0.001
TOL = 0.0015
_OVERLAP_WARN = 0.5  # fold IB 与基底网格重叠比低于此 → 防呆警告（疑似独立形态，应设 Editable）。
                     # 实测：正常折叠件(衣/面/熊)=1.0；form2 面部≈0.05（与基底几乎不是同一网格）。
_POS = AbstractSemantic(Semantic.Position)
_IDX = AbstractSemantic(Semantic.Index)
_BI = AbstractSemantic(Semantic.Blendindices)
_BW = AbstractSemantic(Semantic.Blendweight)
_NB = [(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)]


# --------------------------------------------------------------------------- IO

def _comp_names(folder: Path):
    """列出文件夹里的 component 名（按数字+子序号排序，"Component 5" 在 "Component 5.001" 前）。"""
    names = []
    for p in folder.glob("*.fmt"):
        m = re.match(r".*component[ _-]*([0-9]+)(?:\.([0-9]+))?", p.stem.lower())
        if m:
            names.append((int(m.group(1)), int(m.group(2) or 0), p.stem))
    return [n for _, _, n in sorted(names)]


def _fix_vb_strides(vb_layout, fmt_text):
    """从 .fmt 的 AlignedByteOffset 重算每个元素的真实 byte stride（WWMI 的 BLENDINDICES 声明
    R8 却实占 8 字节这类"声明格式 < 实际跨度"的情况，必须按相邻 offset 之差算，否则 dtype
    itemsize 对不上 .vb 文件大小）。元素本就按 offset 升序排列，最后一个用 header stride 收尾。"""
    header_stride = next(
        int(l.split(':', 1)[1]) for l in fmt_text.splitlines()
        if l.strip().lower().startswith('stride:')
    )
    sems = vb_layout.semantics
    for i, e in enumerate(sems):
        nxt = sems[i + 1].offset if i + 1 < len(sems) else header_stride
        e.stride = nxt - e.offset
    vb_layout.stride = header_stride


def _read_comp(folder: Path, name: str):
    fmt_text = (folder / f"{name}.fmt").read_text()
    fmt = MigotoFormat.from_fmt_text(fmt_text)
    _fix_vb_strides(fmt.vb_layout, fmt_text)
    vb = NumpyBuffer(fmt.vb_layout)
    vb.import_raw_data((folder / f"{name}.vb").read_bytes())
    ib = NumpyBuffer(fmt.ib_layout)
    ib.import_raw_data((folder / f"{name}.ib").read_bytes())
    return vb, ib, fmt_text


def _positions(vb) -> np.ndarray:
    return np.asarray(vb.get_field(_POS), dtype=float).reshape(-1, 3)


def _blend(vb):
    """返回 (indices, weights) 各 (N, K)：K=骨槽数（body/面部=8）。无 blend 语义则 (None, None)。
    WWMI 提取 BLENDINDICES/BLENDWEIGHT 各占 8 字节(R8)，get_field 按修正 stride 解为 (N,8) uint8
    （原始权重 0-255；投票按权重降序排，原始 uint8 序 == 归一序，故无需归一化）。"""
    bi = vb.get_field(_BI)
    bw = vb.get_field(_BW)
    if bi is None or bw is None:
        return None, None
    return np.asarray(bi).reshape(len(bi), -1), np.asarray(bw).reshape(len(bw), -1)


def _tris(ib) -> np.ndarray:
    return np.asarray(ib.get_field(_IDX)).reshape(-1, 3)


def _write_comp(folder: Path, name: str, vb, ib, fmt_text: str):
    (folder / f"{name}.fmt").write_text(fmt_text)
    (folder / f"{name}.vb").write_bytes(vb.get_bytes())
    (folder / f"{name}.ib").write_bytes(ib.get_bytes())


# --------------------------------------------------------------- foldability (.fmt)

def _fmt_vb_signature(fmt_text: str):
    """非形态键顶点布局签名：((SemanticName, SemanticIndex, Format, AlignedByteOffset), ...)，
    排除 SHAPEKEY 元素、且不比提取 stride。

    提取 .fmt 会把 inline 形态键也存进 vb0：不同 morph 数量不同 → stride 膨胀且互不相等，
    SHAPEKEY 的 SemanticIndex 也各 component 不同（面部从 9 起、base C2 从 23 起）。
    但折叠绑的是导出后的分离 buffer（Position/Blend/Vector/Color/TexCoord 各独立），形态键由 CS
    另行施加、不在折叠绑的 vb 里 —— 故可折性只看非形态键 vb0 语义布局，尤其 blend 骨数：
    body BLENDINDICES=R8_UINT@20→BLENDWEIGHT@28(span8=8 骨) vs 小熊 R8G8B8A8_UINT@20→@24(span4=4 骨)。
    offset 同时编码每字段跨度（到下一元素的间隔），(format, offset) 组合即可捕获骨数差异。
    （已实测：面部 vs baseC2 全等=可折；小熊 blend 格式+offset 均不同=不可折。）"""
    elems, cur = [], None
    for line in fmt_text.splitlines():
        s = line.strip()
        if s.startswith("element["):
            if cur is not None:
                elems.append(cur)
            cur = {}
        elif cur is not None and ":" in s:
            k, v = s.split(":", 1)
            cur[k.strip()] = v.strip()
    if cur is not None:
        elems.append(cur)
    return tuple((e.get("SemanticName"), e.get("SemanticIndex"), e.get("Format"), e.get("AlignedByteOffset"))
                 for e in elems if e.get("SemanticName") != "SHAPEKEY")


def _fmt_compatible(folder_a: Path, name_a: str, folder_b: Path, name_b: str) -> bool:
    """两个 component 的顶点布局是否逐元素全一致（决定 dungeon IB 能否折进 base buffer）。"""
    sa = _fmt_vb_signature((Path(folder_a) / f"{name_a}.fmt").read_text())
    sb = _fmt_vb_signature((Path(folder_b) / f"{name_b}.fmt").read_text())
    return sa == sb


# ---------------------------------------------------------------- position grid

def _gk(p):
    return (int(round(p[0] / GRID)), int(round(p[1] / GRID)), int(round(p[2] / GRID)))


def _build_grid(points: np.ndarray):
    g = defaultdict(list)
    for i, p in enumerate(points):
        g[_gk(p)].append(i)
    return g


def _nearest(p, points: np.ndarray, grid):
    """grid 内距 p 在 TOL 内的最近 points 索引；无则 None。"""
    k = _gk(p)
    best, bd = None, TOL * TOL
    for nb in _NB:
        for j in grid.get((k[0] + nb[0], k[1] + nb[1], k[2] + nb[2]), ()):
            dd = float(((points[j] - p) ** 2).sum())
            if dd <= bd:
                bd, best = dd, j
    return best


def _match_set(query: np.ndarray, target_points: np.ndarray, target_grid):
    """返回 query 中每个点是否在 target 里有 <=TOL 的对应（精确同网格匹配）。"""
    tol2 = TOL * TOL
    hits = np.zeros(len(query), dtype=bool)
    for i, p in enumerate(query):
        k = _gk(p)
        for d in _NB:
            for j in target_grid.get((k[0] + d[0], k[1] + d[1], k[2] + d[2]), ()):
                if float(((target_points[j] - p) ** 2).sum()) <= tol2:
                    hits[i] = True
                    break
            if hits[i]:
                break
    return hits


# ---------------------------------------------------------------- split + merge

def _split_component(vb, ib, fmt_text, wrap_ib_folder):
    """把含被某 dungeon IB 包裹的子件的 component 切成 (remainder, member)。

    按**三角形质心**对照该包裹 IB（如腰饰熊 c4df4691）所有 component 的三角形质心(ground truth)切：
    母 component 的某三角形质心落在包裹 IB 里 → 归子件，否则归母件余量。
    比早期的 union-find 岛法准 —— 子件与母网格常共享边/顶点，union-find 会把它们并成一座岛而**漏切**
    （实测 union-find ~2472 三角形、质心法 ~2702，外部 xscene_resplit 已验证质心法正确）。.fmt 原样复用。"""
    tris = _tris(ib)
    cents = _positions(vb)[tris].mean(axis=1)
    wf = Path(wrap_ib_folder)
    d_cent = []
    for dn in _comp_names(wf):
        dvb, dib, _ = _read_comp(wf, dn)
        d_cent.append(_positions(dvb)[_tris(dib)].mean(axis=1))
    d_cent = np.concatenate(d_cent)
    member_mask = _match_set(cents, d_cent, _build_grid(d_cent))  # 每个母三角形质心是否落在包裹 IB

    def _build(sub_tris):
        if len(sub_tris) == 0:
            return None
        used = np.unique(sub_tris.reshape(-1))
        remap = {int(o): k for k, o in enumerate(used)}
        new_vb = NumpyBuffer(vb.layout, data=vb.data[used])
        new_tris = np.array([[remap[int(v)] for v in t] for t in sub_tris], dtype=tris.dtype)
        new_ib = NumpyBuffer(ib.layout, size=len(new_tris))
        new_ib.set_field(_IDX, new_tris)
        return new_vb, new_ib

    return _build(tris[~member_mask]), _build(tris[member_mask])


def _copy_textures(src: Path, dst: Path):
    """把 src 里的 ``* t=<hash>.dds`` 贴图按 hash 去重拷进 dst（已存在的 hash 跳过）。"""
    dst.mkdir(parents=True, exist_ok=True)
    have = {m.group(1) for f in dst.glob("*.dds") if (m := re.search(r"t=([0-9a-fA-F]+)", f.name))}
    copied = 0
    for f in src.glob("*.dds"):
        m = re.search(r"t=([0-9a-fA-F]+)", f.name)
        if not m or m.group(1) in have:
            continue
        shutil.copy2(f, dst / f.name)
        have.add(m.group(1))
        copied += 1
    return copied


def analyze(base_folder, dungeon_specs):
    """只读分析：每条 dungeon IB 对应哪些基底 component + 是否被单 component 包裹（→拆分）。
    dungeon_specs: [{"hash":..,"folder":Path,"role":..}]
    返回 [{hash, role, base_components, wrapped_in(None|comp_id), matched, member_in_comp:{cid:set}}]"""
    base = Path(base_folder)
    base_comps = _comp_names(base)
    base_pos_list, base_off = [], {}
    cursor = 0
    for cid, name in enumerate(base_comps):
        vb, _, _ = _read_comp(base, name)
        p = _positions(vb)
        base_off[cid] = (cursor, cursor + len(p))
        cursor += len(p)
        base_pos_list.append(p)
    base_pos = np.concatenate(base_pos_list)
    base_grid = _build_grid(base_pos)

    def comp_of(global_idx):
        for cid, (a, b) in base_off.items():
            if a <= global_idx < b:
                return cid, global_idx - a
        return None, None

    out = []
    for spec in dungeon_specs:
        d = Path(spec["folder"])
        dpos = np.concatenate([_positions(_read_comp(d, dn)[0]) for dn in _comp_names(d)])
        hist = defaultdict(int)
        member_in_comp = defaultdict(set)
        matched = 0
        for p in dpos:
            k = _gk(p)
            best = None
            bd = TOL * TOL
            for nb in _NB:
                for j in base_grid.get((k[0] + nb[0], k[1] + nb[1], k[2] + nb[2]), ()):
                    dd = float(((base_pos[j] - p) ** 2).sum())
                    if dd <= bd:
                        bd, best = dd, j
            if best is not None:
                matched += 1
                cid, local = comp_of(best)
                hist[cid] += 1
                member_in_comp[cid].add(local)
        thr = max(1, int(0.005 * matched))  # 滤掉边界噪声（如面部少量顶点误配到衣服 component）
        base_components = sorted(c for c, n in hist.items() if n >= thr)
        wrapped = None
        if len(base_components) == 1:
            cid = base_components[0]
            ca, cb = base_off[cid]
            if len(member_in_comp[cid]) < (cb - ca):  # 真子集 → 包裹
                wrapped = cid
        # 折叠闸：IB 的顶点布局与其对应 base component 全布局一致 → 可折（绑 base buffer）；
        # 不一致（典型：blend 骨数不同，小熊 4 骨 vs body 8 骨）→ 不可折，走 own-buffer。
        foldable = None
        ib_names = _comp_names(d)
        if base_components and ib_names:
            foldable = _fmt_compatible(base, base_comps[base_components[0]], d, ib_names[0])
        out.append({
            "hash": spec["hash"], "role": spec.get("role", ""),
            "base_components": base_components, "wrapped_in": wrapped,
            "matched": matched, "total": len(dpos), "foldable": foldable,
            "member_in_comp": {c: s for c, s in member_in_comp.items()},
        })
    return out, base_comps, base_off


# ------------------------------------------------------- fold correspondence (M1b)

def _vote_vg_remap(out_dir, base_name, ib_dir, ib_name, corr_map):
    """用 corr_map(base 局部顶点行→IB 局部顶点行) + 各自 blend(8 idx + 8 wt) 投票建 base VG→IB VG 重映射。

    背景：折叠时 FaceHost 把 base blend 绑给 dungeon IB 的 host；若该 component 的 VG 编号与 IB
    不一致（如 c5-face 因小熊 5.001 合并回母 C5 而被重新编号），直接绑会绑骨错位。按顶点对应 +
    排序权重投票得 base VG→IB VG 表，导出期据此重标 base blend 单独绑给该 host。

    返回 (remap|None, {matched, ambiguous, collisions, identity})；identity=True 表示天然对齐(无需 remap)。"""
    bbi, bbw = _blend(_read_comp(out_dir, base_name)[0])
    ibi, ibw = _blend(_read_comp(ib_dir, ib_name)[0])
    if bbi is None or ibi is None:
        return None, {"matched": 0, "ambiguous": 0, "collisions": 0, "identity": None}
    vote = defaultdict(lambda: defaultdict(int))
    for bl, il in corr_map.items():
        bpp = sorted([(int(bbw[bl][t]), int(bbi[bl][t])) for t in range(bbi.shape[1]) if bbw[bl][t] > 0], reverse=True)
        ipp = sorted([(int(ibw[il][t]), int(ibi[il][t])) for t in range(ibi.shape[1]) if ibw[il][t] > 0], reverse=True)
        for (_bw, bvg), (_iw, ivg) in zip(bpp, ipp):
            vote[bvg][ivg] += 1
    remap = {k: max(v, key=v.get) for k, v in vote.items()}
    amb = sum(1 for v in vote.values() if len(v) > 1 and sorted(v.values(), reverse=True)[1] > 0)
    inv = defaultdict(list)
    for k, w in remap.items():
        inv[w].append(k)
    col = sum(1 for ks in inv.values() if len(ks) > 1)
    identity = all(k == v for k, v in remap.items())
    # per-VG 覆盖硬关口：base component 所有顶点用到的 VG 都须在 remap 里（即使该顶点本身没匹配到 IB，
    # 其 VG 只要被别的已匹配顶点投票覆盖即可）；否则该 VG 槽导出期不会被重标 → 绑骨错位。
    used_vgs = set(int(v) for v in np.asarray(bbi)[np.asarray(bbw) > 0])
    uncovered_vgs = sorted(used_vgs - set(remap.keys()))
    return remap, {"matched": len(corr_map), "ambiguous": amb, "collisions": col, "identity": identity,
                   "used_vgs": len(used_vgs), "uncovered_vgs": len(uncovered_vgs)}


def build_fold_correspondence(out_dir, base_comps, base_pos_c, base_pos_g, base_grid_g, comp_of_g, dspec):
    """对一条可折 dungeon IB，在(拆分后的)合并 component 上建折叠对应关系。VertexId=VB 行号作键
    （已证：导入 1:1，顶点 i=提取 VB 行 i；导出 VertexId=该行号；位置编辑下稳定）。

    须在 build_cross_scene_merge 的拆分步骤之后调用：base 侧位置/网格读自 out_dir，故 c5-face
    已不含小熊（5.001），VG 投票才纯。

    返回(写进路由 JSON 的 scene_ib['fold'])：
      comp_map:  {ib_comp_id -> base_comp_id}                    每 IB component 主导匹配的 base component
      corr:      {base_comp_id -> {ib_comp, map:{base_vid->ib_vid}, base_total, covered}}
                 base component 每顶点 → 最近 IB component 顶点（局部 VB 行号）。morph dun2body=反转此映射；VG 投票用。
      vg_remap:  {base_comp_id -> {base_vg->ib_vg}}              仅 VG 不对齐的 component（如 c5-face）
      selfcheck: {base_comp_id -> {matched, ambiguous, collisions, identity, covered, base_total, uncovered}}
    """
    d = Path(dspec["folder"])
    ib_names = _comp_names(d)
    comp_map, corr, vg_remap, selfcheck = {}, {}, {}, {}
    for ic, iname in enumerate(ib_names):
        ivb, _, _ = _read_comp(d, iname)
        ipos = _positions(ivb)
        # 1) 该 IB component 主导匹配的 base component（按全局 base 网格统计众数）
        hist = defaultdict(int)
        for p in ipos:
            j = _nearest(p, base_pos_g, base_grid_g)
            if j is not None:
                hist[comp_of_g(j)] += 1
        if not hist:
            continue
        bc = max(hist, key=hist.get)
        comp_map[ic] = bc
        # 2) base→IB 逐顶点对应（base component bc 每顶点 → 最近 IB component ic 顶点）
        igrid = _build_grid(ipos)
        bpos = base_pos_c[bc]
        mp = {}
        for bl, p in enumerate(bpos):
            j = _nearest(p, ipos, igrid)
            if j is not None:
                mp[bl] = int(j)
        corr[bc] = {"ib_comp": ic, "map": mp, "base_total": len(bpos), "covered": len(mp)}
        # 3) VG remap 投票 + 自检
        rmap, chk = _vote_vg_remap(out_dir, base_comps[bc], d, iname, mp)
        chk.update({"covered": len(mp), "base_total": len(bpos), "uncovered": len(bpos) - len(mp)})
        selfcheck[bc] = chk
        if rmap is not None and not chk.get("identity", True):
            vg_remap[bc] = rmap
    return {
        "comp_map": {str(k): v for k, v in comp_map.items()},
        "corr": {str(k): {"ib_comp": v["ib_comp"],
                          "map": {str(a): b for a, b in v["map"].items()},
                          "base_total": v["base_total"], "covered": v["covered"]}
                 for k, v in corr.items()},
        "vg_remap": {str(k): {str(a): b for a, b in v.items()} for k, v in vg_remap.items()},
        "selfcheck": {str(k): v for k, v in selfcheck.items()},
    }


def build_cross_scene_merge(base_folder, dungeon_specs, out_folder, editable_ibs=None):
    """主入口：写出合并提取文件夹 + scene_ibs/ + CrossSceneRouting.json + 去重贴图。
    editable_ibs: 独立可编辑额外 IB 列表 [{hash, folder, role}]（如 form2 面部 4e4dc18e）——
    其 component 改名拷进合并根目录接在基底之后(C8-11)、可一起导入编辑、导出时归独立 IB 组。
    返回 report dict。"""
    base = Path(base_folder)
    out = Path(out_folder)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    info, base_comps, base_off = analyze(base, dungeon_specs)

    # 1) 拷基底所有 component 的 vb/ib/fmt 到合并根目录（待会儿对 wrapped 的那个做拆分覆盖）。
    for name in base_comps:
        for ext in (".fmt", ".vb", ".ib"):
            shutil.copy2(base / f"{name}{ext}", out / f"{name}{ext}")
    if (base / "Metadata.json").exists():
        shutil.copy2(base / "Metadata.json", out / "Metadata.json")
    base_tex = _copy_textures(base, out)

    # 2) 对每条"被单 component 包裹"的 dungeon IB：把母 component 拆成 X + X.001。
    splits = []
    for spec, meta in zip(dungeon_specs, info):
        cid = meta["wrapped_in"]
        if cid is None:
            continue
        name = base_comps[cid]
        vb, ib, fmt_text = _read_comp(base, name)
        # 用包裹该 component 的 dungeon IB（spec，即腰饰熊）的三角形质心切，不漏切。
        (rem, bear) = _split_component(vb, ib, fmt_text, spec["folder"])
        if rem is None or bear is None:
            raise RuntimeError(f"split of {name} for IB {spec['hash']} produced empty partition")
        split_name = f"{name}.001"
        _write_comp(out, name, rem[0], rem[1], fmt_text)          # 覆盖：母 component 余量
        _write_comp(out, split_name, bear[0], bear[1], fmt_text)  # 新：子件
        splits.append({
            "base_component": cid, "base_object": name, "split_object": split_name,
            "ib_hash": spec["hash"], "split_vertex_count": len(bear[0]),
            "split_tri_count": len(bear[1]), "remainder_vertex_count": len(rem[0]),
        })

    # 3) 各 dungeon IB 原生提取整份拷进 scene_ibs/<hash>/（供导出逐 IB 重导入作 host）。
    scene_root = out / "scene_ibs"
    for spec in dungeon_specs:
        d = Path(spec["folder"])
        dst = scene_root / spec["hash"]
        dst.mkdir(parents=True, exist_ok=True)
        for f in d.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)

    # 3.5) editable_ibs（独立可编辑额外 IB，如 form2 面部 4e4dc18e）：整份拷进 scene_ibs/<hash>/
    #      + 把其 Component 0..M 改名拷进合并根目录为 Component <next..>（接在基底 C0..N-1 后）。
    #      .fmt/.vb/.ib 原样（含 inline 形态键 SHAPEKEY 元素、保留高 stride）→ 导入即带各自形态键。
    editable_ib_records = []
    next_idx = len(base_comps)
    for eib in (editable_ibs or []):
        h = eib["hash"]
        src = Path(eib["folder"])
        dst = scene_root / h
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)
        local_names = _comp_names(src)
        merged_comps = []
        for li, lname in enumerate(local_names):
            mi = next_idx + li
            for ext in (".fmt", ".vb", ".ib"):
                shutil.copy2(src / f"{lname}{ext}", out / f"Component {mi}{ext}")
            merged_comps.append(mi)
        eib_meta = json.loads((src / "Metadata.json").read_text()) if (src / "Metadata.json").exists() else {}
        sk = eib_meta.get("shapekeys", {}) or {}
        _copy_textures(src, out)  # 把额外 IB 的贴图也收进合并根目录（去重）
        editable_ib_records.append({
            "ib_hash": h, "role": eib.get("role", ""), "source_folder": f"scene_ibs/{h}",
            "merged_components": merged_comps, "local_components": list(range(len(local_names))),
            "has_shapekeys": bool(sk.get("offsets_hash")), "offsets_hash": sk.get("offsets_hash", ""),
        })
        next_idx += len(local_names)

    # 3.7) 对每条可折 dungeon IB，在拆分后的合并 component 上建折叠对应关系（base 侧读 out 的 c5-face 已不含小熊）。
    base_pos_c, _all, _off, _cur = {}, [], {}, 0
    for cid, name in enumerate(base_comps):
        bp = _positions(_read_comp(out, name)[0])
        base_pos_c[cid] = bp
        _off[cid] = (_cur, _cur + len(bp))
        _cur += len(bp)
        _all.append(bp)
    base_pos_g = np.concatenate(_all) if _all else np.zeros((0, 3))
    base_grid_g = _build_grid(base_pos_g)

    def _comp_of_g(gi):
        for cid, (a, b) in _off.items():
            if a <= gi < b:
                return cid
        return None

    fold_data = {}
    for spec, meta in zip(dungeon_specs, info):
        if meta.get("foldable"):
            fold_data[spec["hash"]] = build_fold_correspondence(
                out, base_comps, base_pos_c, base_pos_g, base_grid_g, _comp_of_g, spec)

    # 4) 写 CrossSceneRouting.json。
    routing = _build_routing(base, base_comps, info, splits, editable_ib_records, fold_data)
    (out / "CrossSceneRouting.json").write_text(json.dumps(routing, indent=2, ensure_ascii=False))

    # 防呆：fold(dungeon) IB 与基底网格的重叠比若过低（几乎不是同一网格）→ 很可能是独立形态（如另一形态的
    # 面部），误设成 Fold 会把它折进基底 buffer、折坏。重叠比 = analyze 的 matched/total
    # （实测正常折叠件=1.0、form2≈0.05）。形态键 hash 分不了（form1/form2 面共用编号范围），几何重叠才是判据。
    warnings = []
    for spec, meta in zip(dungeon_specs, info):
        ratio = meta["matched"] / max(1, meta["total"])
        if ratio < _OVERLAP_WARN:
            warnings.append(
                "IB %s 仅 %.1f%% 顶点与基底重叠，几乎不是同一网格——很可能是独立形态（如另一形态的面部），"
                "应在合并面板把它的角色设为 Editable，而不是 Fold（当前按 Fold 折入基底，会折坏）。"
                % (spec["hash"], ratio * 100))

    return {
        "out_folder": str(out), "base_components": len(base_comps),
        "splits": splits, "scene_ibs": [s["hash"] for s in dungeon_specs],
        "editable_ibs": editable_ib_records, "warnings": warnings,
        "base_textures": base_tex, "analyze": [
            {k: (sorted(v) if isinstance(v, set) else v) for k, v in m.items() if k != "member_in_comp"}
            for m in info
        ],
    }


def _build_routing(base, base_comps, info, splits, editable_ib_records=None, fold_data=None):
    base_meta = {}
    mp = base / "Metadata.json"
    if mp.exists():
        base_meta = json.loads(mp.read_text())
    editable = list(base_comps)
    for s in splits:
        if s["split_object"] not in editable:
            # 把子件名插在母 component 之后
            i = editable.index(s["base_object"])
            editable.insert(i + 1, s["split_object"])
    for rec in (editable_ib_records or []):
        for mi in rec["merged_components"]:
            editable.append(f"Component {mi}")

    split_by_ib = {s["ib_hash"]: s for s in splits}
    scene_ibs = []
    for meta in info:
        h = meta["hash"]
        s = split_by_ib.get(h)
        # 面部是否带 morph：读该 IB 提取的 Metadata.shapekeys.offsets_hash
        morph = None
        d_meta_path = base.parent  # 占位，真实 morph 信息由导出侧从 scene_ibs/<hash>/Metadata.json 读
        scene_ibs.append({
            "ib_hash": h, "vb0_hash": h, "role": meta["role"],
            "source_folder": f"scene_ibs/{h}",
            # foldable=True → fold（绑 base buffer，consumer M2 走 fold.py）；False → own-buffer（host-transfer）。
            # 加性字段：现有 orchestrator 不读它、行为不变；M2 consumer 据它分流。
            "foldable": meta.get("foldable"),
            "derive": {
                "method": ("fold" if meta.get("foldable")
                           else ("delta" if meta["role"] == "face" else "absolute")),
                "base_components": meta["base_components"],
                "source_object": (s["split_object"] if s else None),
                "correspondence": "position_grid",
            },
            # fold 对应关系（仅 foldable）：comp_map + base→IB 逐顶点对应 + VG remap + 自检。
            # consumer 据此重投影 morph / 重标 blend / 出重定向段；非 foldable 为 None。
            "fold": (fold_data or {}).get(h),
            "object_detected_var": f"$object_detected_{h}",
        })

    return {
        "schema_version": 2,
        "generator": "velo_tools.games.wuthering_waves.xscene_merge",
        "grid_size": GRID, "match_tol": TOL,
        "base": {
            "vb0_hash": base_meta.get("vb0_hash", base.name),
            "cb4_hash": base_meta.get("cb4_hash", ""),
            "component_count": len(base_comps),
            "editable_objects": editable,
            "splits": splits,
        },
        "scene_ibs": scene_ibs,
        "editable_ibs": editable_ib_records or [],
        "object_detected_or_gate": (
            [f"$object_detected_{m['hash']}" for m in info]
            + [f"$object_detected_{rec['ib_hash']}" for rec in (editable_ib_records or [])]),
        "textures": {"dedup_by_hash": True, "note": "final dedup done by export assembler"},
    }
