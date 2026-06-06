"""跨场景折叠 consumer（M2 核心）——把"可折"dungeon IB 折进 base buffer，绝不改 ``_wwmi_core``。

从已游戏验证的独立原型 ``xscene_fold_prep.py`` 移植 + 由 ``CrossSceneRouting.json`` 的 ``fold`` 段驱动通用化。
三个硬模块：
  1. ``reproject_morph``  —— dungeon 形态键按 dun2body 重投影到 base 顶点序（每形态键每 vid 恰一次，
     防 ShapeKeyLoader.hlsl 的 InterlockedAdd 过累加 → 粘连/眼球冲出）。
  2. ``apply_blend_remap`` —— 按 producer 投票好的 VG 表重标 base 某 component 的 blend 索引 → 独立 buffer。
  3. ``emit_fold_sections`` —— Host 重定向段（dungeon IB 的 draw 绑 base buffer 区间）+ morph CS 段移植。

折叠件按 ``tag``（= IB 的 vb0 hash，唯一）命名段/buffer（``FoldHost_<tag>_C{n}`` / ``ShapeKey*_<tag>.buf``），
故同一 mod 里多个折叠件不撞名。**无形态键的折叠件**（如衣服，其导出 host 无 ShapeKeyOffset.buf）自动
跳过 morph 重投影 + morph CS 段移植，只发几何重定向段（+ 必要的 blend remap）——即纯几何折叠。

阶段策略（plan「先复现后扩展」）：
  * M2（破洞、未编辑）：morph 的 body↔dungeon 对应按**导出层位置匹配**重建 —— 与外部同算法同数据 → 逐字节 == 绿 mod。
  * M3（已编辑）：改用 **VertexId + producer 对应**（不依赖位置）做编辑稳健；blend remap 因为按 VG 表施加、本就编辑稳健。

本模块的 buffer/ini 函数是纯 numpy / 字符串，不依赖 bpy；编排（导出各 IB、调用本模块、合并）在 orchestrator 里。
"""
import re
from collections import defaultdict
from pathlib import Path

import numpy as np

GRID = 0.001
TOL2 = 0.0015 ** 2
_NB = [(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)]


def _qk(p):
    return (int(round(p[0] / GRID)), int(round(p[1] / GRID)), int(round(p[2] / GRID)))


def _rd(path, dt):
    return np.frombuffer(Path(path).read_bytes(), dtype=dt)


# ----------------------------------------------------------------- ini 解析

def parse_draws(text):
    """{component_id: [(index_count, first_index), ...]}（一个 component 可能有多画，如 C5 = c5-face + 小熊）。"""
    d = {}
    for m in re.finditer(r'\[TextureOverrideComponent(\d+)\][^\[]*', text):
        d[int(m.group(1))] = [(int(c), int(o)) for c, o in re.findall(r'drawindexed = (\d+), (\d+)', m.group(0))]
    return d


def parse_match(text):
    """{component_id: (match_first_index, match_index_count)}。"""
    d = {}
    for m in re.finditer(r'\[TextureOverrideComponent(\d+)\][^\[]*', text):
        fi = re.search(r'match_first_index = (\d+)', m.group(0))
        ic = re.search(r'match_index_count = (\d+)', m.group(0))
        if fi and ic:
            d[int(m.group(1))] = (int(fi.group(1)), int(ic.group(1)))
    return d


# ------------------------------------------------- 1) morph dun2body 重投影

def reproject_morph(body_meshes, face_meshes, body_segs, seg_comp, face_draws, tag, ref_meshes=None):
    """把 dungeon IB 的形态键重投影到 base(body) 顶点序，写 ShapeKey{Offset,VertexId,VertexOffset}_<tag>.buf。

    body-centric：对每个 body seg 顶点找最近的 dungeon 顶点（**组件限定**：body C2↔face C0 等，
    防止面部顶点投到共址的别的 component 上），反向得 ``dungeon_vert -> [body verts]``；再对每形态键、
    每 dungeon 形态键条目，发它名下的 body 顶点 —— 保证**每形态键每 body vid 恰一次**。

    参数：
      body_segs:  {body_comp: (index_count, first_index)}  body 各对应 component 的画段（c5-face 取第一画）。
      seg_comp:   [(body_comp, face_comp), ...]            body component ← dungeon face component 配对。
      face_draws: {face_comp: (index_count, first_index)}  dungeon face 各 component 的画段。
    返回 batch_counts（每 batch 的重投影条目数，供 ini 常量用）。

    M2：用导出层位置匹配（与外部 xscene_fold_prep 同算法/同数据 → 逐字节一致）。
    """
    body_meshes, face_meshes = Path(body_meshes), Path(face_meshes)
    # ref_meshes = 未编辑 body 参照（编辑派生 hole=False 时传入）：dun2body 的位置匹配必须用未编辑几何
    # 去对 dungeon face，否则编辑后的 body 与 dungeon 对不上。morph 引用的 body 行号在同拓扑下编辑前后
    # 不变，故按 ref 算出的 morph 直接写进（适用于）实际导出的 body_meshes。hole=True/未编辑时 ref==body。
    ref = Path(ref_meshes) if ref_meshes else body_meshes
    body_pos = _rd(ref / "Position.buf", np.float32).reshape(-1, 3)
    face_pos = _rd(face_meshes / "Position.buf", np.float32).reshape(-1, 3)
    body_idx = _rd(ref / "Index.buf", np.uint32)
    face_idx = _rd(face_meshes / "Index.buf", np.uint32)

    dun2body = defaultdict(list)
    for bcomp, fcomp in seg_comp:
        fcd, fod = face_draws[fcomp]
        dgrid = defaultdict(list)
        for dv in np.unique(face_idx[fod:fod + fcd]):
            dgrid[_qk(face_pos[int(dv)])].append(int(dv))
        bcd, bod = body_segs[bcomp]
        for bv in np.unique(body_idx[bod:bod + bcd]):
            bv = int(bv)
            bp = body_pos[bv]
            k = _qk(bp)
            best, bb = None, TOL2
            for d in _NB:
                for dv in dgrid.get((k[0] + d[0], k[1] + d[1], k[2] + d[2]), ()):
                    dd = float(((face_pos[dv] - bp) ** 2).sum())
                    if dd <= bb:
                        bb, best = dd, dv
            if best is not None:
                dun2body[best].append(bv)

    sko = _rd(face_meshes / "ShapeKeyOffset.buf", np.uint32)
    svid = _rd(face_meshes / "ShapeKeyVertexId.buf", np.uint32)
    svoff_b = (face_meshes / "ShapeKeyVertexOffset.buf").read_bytes()
    stride = len(svoff_b) // len(svid)
    nbatch = len(sko) // 128

    new_sko, new_svid, new_svoff = [], [], bytearray()
    batch_counts = []
    bstart = 0
    for b in range(nbatch):
        bo = sko[b * 128:(b + 1) * 128]
        nbo, cum = [0], 0
        for s in range(127):
            for i in range(bstart + int(bo[s]), bstart + int(bo[s + 1])):
                bvs = dun2body.get(int(svid[i]))
                if not bvs:
                    continue
                ob = svoff_b[i * stride:(i + 1) * stride]
                for bv in bvs:
                    new_svid.append(bv)
                    new_svoff += ob
                    cum += 1
            nbo.append(cum)
        new_sko += nbo
        batch_counts.append(cum)
        bstart += int(bo[127])

    # 不变量自检：任一形态键内 body vid 不得重复（= InterlockedAdd 不过累加）
    _bs = 0
    for b in range(nbatch):
        for s in range(127):
            chunk = new_svid[_bs + int(new_sko[b * 128 + s]):_bs + int(new_sko[b * 128 + s + 1])]
            if len(chunk) != len(set(chunk)):
                raise AssertionError("duplicate vertex_id within a shapekey (over-accumulate not prevented)")
        _bs += batch_counts[b]
    if len(new_sko) != nbatch * 128:
        raise AssertionError("ShapeKeyOffset size mismatch")

    np.array(new_svid, np.uint32).tofile(body_meshes / ("ShapeKeyVertexId_%s.buf" % tag))
    (body_meshes / ("ShapeKeyVertexOffset_%s.buf" % tag)).write_bytes(bytes(new_svoff))
    np.array(new_sko, np.uint32).tofile(body_meshes / ("ShapeKeyOffset_%s.buf" % tag))
    return batch_counts


# ------------------------------------------------- 2) blend VG remap 施加

def apply_blend_remap(body_meshes, vg_remap_table, body_seg, tag):
    """按 producer 投票好的 VG 表，重标 body 某 component(body_seg 画段)顶点的 blend 索引 → ``Blend_<tag>.buf``。

    blend = R8_UINT stride16（8 idx + 8 wt）。只重标"该 component 顶点 + 该槽权重>0 + 该 VG 在表里"的槽；
    表外的 VG 槽保持原值（外部脚本同样不动这些槽，已验证两边逐字节一致）。返回输出文件名。
    """
    body_meshes = Path(body_meshes)
    blend = _rd(body_meshes / "Blend.buf", np.uint8).reshape(-1, 16).copy()
    body_idx = _rd(body_meshes / "Index.buf", np.uint32)
    bcd, bod = body_seg
    seg_verts = np.unique(body_idx[bod:bod + bcd])
    table = {int(k): int(v) for k, v in vg_remap_table.items()}
    bidx, bwt = blend[:, :8], blend[:, 8:]
    for v in seg_verts:
        v = int(v)
        for t in range(8):
            if bwt[v][t] > 0 and int(bidx[v][t]) in table:
                blend[v][t] = table[int(bidx[v][t])]
    fn = "Blend_%s.buf" % tag
    blend.tofile(body_meshes / fn)
    return fn


# ------------------------------------------------- 3) ini 段：Host 重定向 + morph CS 移植

def _section(text, header):
    """抽出 ``[header]`` 段（含头、到下一 ``[`` 前）。无则 None。"""
    m = re.search(r'(^\[' + re.escape(header) + r'\][^\[]*)', text, re.M)
    return m.group(1) if m else None


def _build_morph_sections(face_text, tag):
    """把 dungeon face 的 morph CS 段移植成 ``_<tag>``：数据 buffer 改名 + 重指向 ``_<tag>.buf``、
    自定义顶点偏移/数量换成 ``_<tag>`` 全局；RW 暂存(CBRW/CustomShapeKeyValuesRW)沿用 body 共享、不复制。
    hash/checksum/dispatch/cs 等参数从 face 的 mod.ini 解析（本就是 stock 导出写进去的）。"""
    R = tag
    mh = re.search(r'\[TextureOverrideShapeKeyOffsets\]\s*\nhash = (\w+)', face_text).group(1)
    sh = re.search(r'\[TextureOverrideShapeKeyScale\]\s*\nhash = (\w+)', face_text).group(1)
    chk = re.findall(r'shapekey_checksum_batch\d+ = (\d+)', face_text)
    orig = re.findall(r'shapekey_vertex_offset_original_batch\d+ = (\d+)', face_text)
    disp = re.findall(r'shapekey_dispatch_size_y_original_batch\d+ = (\d+)', face_text)
    csL = re.search(r'if cs == ([\d.]+)\s*\n\s*handling = skip\s*\n\s*run = CommandListSetupShapeKeysBatch', face_text).group(1)
    csM = re.search(r'if cs == ([\d.]+)\s*\n\s*handling = skip\s*\n\s*run = CommandListMultiplyShapeKeys', face_text).group(1)
    nb = len(chk)
    s = ["\n; ==== %s morph (%s) re-projected onto base vertex order ====\n" % (R, mh)]
    s.append("[TextureOverrideShapeKeyOffsets_%s]\nhash = %s\nmatch_priority = 0\n"
             "override_byte_stride = 24\noverride_vertex_count = $mesh_vertex_count\n\n" % (R, mh))
    s.append("[TextureOverrideShapeKeyScale_%s]\nhash = %s\nmatch_priority = 0\n"
             "override_byte_stride = 4\noverride_vertex_count = $mesh_vertex_count\n\n" % (R, sh))
    setup = "[CommandListSetupShapeKeysBatch_%s]\n" % R
    for n in range(nb):
        setup += ("$\\WWMIv1\\shapekey_checksum_batch%d = %s\n"
                  "$\\WWMIv1\\shapekey_vertex_offset_original_batch%d = %s\n"
                  "$\\WWMIv1\\shapekey_vertex_offset_custom_batch%d = $shapekey_vertex_offset_batch%d_%s\n"
                  % (n, chk[n], n, orig[n], n, n, R))
    setup += ("cs-t33 = ResourceShapeKeyOffsetBuffer_%s\ncs-u5 = ResourceCustomShapeKeyValuesRW\n"
              "cs-u6 = ResourceShapeKeyCBRW\nrun = CustomShader\\WWMIv1\\ShapeKeyBatchOverrider\n\n" % R)
    s.append(setup)
    load = "[CommandListLoadShapeKeysBatch_%s]\n" % R
    for n in range(nb):
        load += ("$\\WWMIv1\\shapekey_dispatch_size_y_original_batch%d = %s\n"
                 "$\\WWMIv1\\shapekey_vertex_count_batch%d = $shapekey_vertex_count_batch%d_%s\n"
                 % (n, disp[n], n, n, R))
    load += ("cs-t0 = ResourceShapeKeyVertexIdBuffer_%s\ncs-t1 = ResourceShapeKeyVertexOffsetBuffer_%s\n"
             "cs-u6 = ResourceShapeKeyCBRW\nrun = CommandList\\WWMIv1\\LoadShapeKeysBatch\n\n" % (R, R))
    s.append(load)
    s.append("[TextureOverrideShapeKeyLoaderCallback_%s]\nhash = %s\nmatch_priority = 0\n"
             "if $mod_enabled\n    if cs == %s\n        handling = skip\n"
             "        run = CommandListSetupShapeKeysBatch_%s\n        run = CommandListLoadShapeKeysBatch_%s\n"
             "    endif\nendif\n\n" % (R, mh, csL, R, R))
    s.append("[CommandListMultiplyShapeKeys_%s]\n$\\WWMIv1\\custom_vertex_count = $mesh_vertex_count\n"
             "run = CustomShader\\WWMIv1\\ShapeKeyMultiplier\n\n" % R)
    s.append("[TextureOverrideShapeKeyMultiplierCallback_%s]\nhash = %s\nmatch_priority = 0\n"
             "if $mod_enabled\n    if cs == %s\n        handling = skip\n"
             "        run = CommandListMultiplyShapeKeys_%s\n    endif\nendif\n\n" % (R, mh, csM, R))
    s.append("[ResourceShapeKeyOffsetBuffer_%s]\ntype = Buffer\nformat = DXGI_FORMAT_R32G32B32A32_UINT\n"
             "stride = 16\nfilename = Meshes/ShapeKeyOffset_%s.buf\n\n" % (R, R))
    s.append("[ResourceShapeKeyVertexIdBuffer_%s]\ntype = Buffer\nformat = DXGI_FORMAT_R32_UINT\n"
             "stride = 4\nfilename = Meshes/ShapeKeyVertexId_%s.buf\n\n" % (R, R))
    s.append("[ResourceShapeKeyVertexOffsetBuffer_%s]\ntype = Buffer\nformat = DXGI_FORMAT_R16_FLOAT\n"
             "stride = 2\nfilename = Meshes/ShapeKeyVertexOffset_%s.buf\n" % (R, R))
    return "".join(s)


def emit_fold_sections(body_text, face_text, fold_entry, body_draws, face_match, batch_counts, tag, has_morph=True):
    """注入折叠所需 ini 段，返回修改后的 body_text：
      1) 常量：每 batch 的 $shapekey_vertex_offset/count_batch{N}_<tag>（插在 body 最后一个 batch 常量后）——仅 has_morph；
      2) FoldHost：每个 dungeon component 的 draw 绑到对应 base component 的 buffer 区间（VG 不齐者走 _remap CommandList）；
      3) _remap CommandList（拷 body 的 CommandListOverrideSharedResources、只换 vb4）+ ResourceBlendBuffer_<tag>；
      4) morph CS 段移植（_build_morph_sections）——仅 has_morph（无形态键折叠件如衣服跳过）。
    """
    fh = fold_entry["ib_hash"]
    comp_map = {int(k): v for k, v in fold_entry["fold"]["comp_map"].items()}
    vg_remap = fold_entry["fold"].get("vg_remap", {})
    remap_cmd = {int(k): ("CommandListOverrideSharedResources_c%dremap" % int(k), "c%dremap" % int(k))
                 for k in vg_remap}

    if has_morph and batch_counts:
        consts, off = "", 0
        for n, cnt in enumerate(batch_counts):
            consts += ("global $shapekey_vertex_offset_batch%d_%s = %d\n"
                       "global $shapekey_vertex_count_batch%d_%s = %d\n" % (n, tag, off, n, tag, cnt))
            off += cnt
        anchors = list(re.finditer(r'global \$shapekey_vertex_count_batch\d+ = \d+\n', body_text))
        if anchors:
            a = anchors[-1]
            body_text = body_text[:a.end()] + consts + body_text[a.end():]

    out = ["\n; ==== fold %s (%s) into base buffer ranges ====\n" % (tag, fh)]
    for fc in sorted(comp_map):
        bc = comp_map[fc]
        mfi, mic = face_match[fc]
        cnt, offd = body_draws[bc][0]
        ovr = remap_cmd[bc][0] if bc in remap_cmd else "CommandListOverrideSharedResources"
        out.append("[TextureOverride_FoldHost_%s_C%d]\nhash = %s\n"
                   "match_first_index = %d\nmatch_index_count = %d\n$object_detected = 1\n"
                   "if $mod_enabled\n    handling = skip\n    run = CommandListTriggerResourceOverrides\n"
                   "    run = %s\n    drawindexed = %d, %d, 0\n    run = CommandListCleanupSharedResources\nendif\n"
                   % (tag, fc, fh, mfi, mic, ovr, cnt, offd))
    base_cmd = _section(body_text, "CommandListOverrideSharedResources")
    for bc, (cmdname, btag) in remap_cmd.items():
        blk = base_cmd.replace("[CommandListOverrideSharedResources]", "[%s]" % cmdname, 1)
        blk = re.sub(r'vb4 = \w+', "vb4 = ResourceBlendBuffer_%s" % btag, blk)
        out.append("\n" + blk.rstrip() + "\n")
        out.append("\n[ResourceBlendBuffer_%s]\ntype = Buffer\nformat = DXGI_FORMAT_R8_UINT\n"
                   "stride = 16\nfilename = Meshes/Blend_%s.buf\n" % (btag, btag))
    if has_morph:
        out.append(_build_morph_sections(face_text, tag))
    return body_text + "".join(out)


def apply_fold(work, fold_entry, tag, morph_ref=None):
    """对一条可折 IB：在已 stock 导出的 ``work/body`` 与 ``work/<tag>`` 上，重投影 morph（若有）+ 施加 blend remap
    + 注入 ini 段，**就地改 work/body**（geometry 折叠、morph 重投影、VG remap 全合进 base buffer mod）。

    ``tag`` = 该 IB 的 vb0 hash（唯一），用于段/buffer 命名 + 定位导出 host 目录 ``work/<tag>``。
    无形态键的折叠件（衣服：其导出 host 无 ShapeKeyOffset.buf）只折几何 + 必要的 blend remap，跳过 morph。"""
    work = Path(work)
    body, face = work / "body", work / tag
    body_text = (body / "mod.ini").read_text(encoding="utf-8")
    face_text = (face / "mod.ini").read_text(encoding="utf-8")
    bd, fd, fm = parse_draws(body_text), parse_draws(face_text), parse_match(face_text)
    comp_map = {int(k): v for k, v in fold_entry["fold"]["comp_map"].items()}
    vg_remap = fold_entry["fold"].get("vg_remap", {})
    has_morph = (face / "Meshes" / "ShapeKeyOffset.buf").exists()
    if has_morph:
        body_segs = {bc: bd[bc][0] for bc in comp_map.values()}
        seg_comp = [(comp_map[fc], fc) for fc in sorted(comp_map)]
        face_draws = {fc: fd[fc][0] for fc in comp_map}
        batch_counts = reproject_morph(body / "Meshes", face / "Meshes", body_segs, seg_comp, face_draws, tag, ref_meshes=morph_ref)
    else:
        batch_counts = []
    for k, table in vg_remap.items():
        apply_blend_remap(body / "Meshes", table, bd[int(k)][0], "c%dremap" % int(k))
    new_body = emit_fold_sections(body_text, face_text, fold_entry, bd, fm, batch_counts, tag, has_morph=has_morph)
    (body / "mod.ini").write_text(new_body, encoding="utf-8")
