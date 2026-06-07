"""Cross-scene fold consumer (M2 core) -- folds a "foldable" dungeon IB into the base buffer, never touching ``_wwmi_core``.

Ported from the game-verified standalone prototype ``xscene_fold_prep.py`` + generalized, driven by the ``fold`` section of ``CrossSceneRouting.json``.
Three hard modules:
  1. ``reproject_morph``  -- reproject dungeon shapekeys onto the base vertex order via dun2body (exactly once per shapekey per vid,
     to prevent over-accumulation by ShapeKeyLoader.hlsl's InterlockedAdd -> sticking / eyeballs bursting out).
  2. ``apply_blend_remap`` -- relabel the blend indices of some base component via the VG table voted by the producer -> standalone buffer.
  3. ``emit_fold_sections`` -- Host redirect section (the dungeon IB's draw bound to a base buffer range) + morph CS section transplant.

Fold pieces name their sections/buffers by ``tag`` (= the IB's vb0 hash, unique) (``FoldHost_<tag>_C{n}`` / ``ShapeKey*_<tag>.buf``),
so multiple fold pieces in the same mod do not collide. **Fold pieces without shapekeys** (e.g. clothing, whose exported host has no ShapeKeyOffset.buf) automatically
skip morph reprojection + morph CS section transplant, emitting only geometry redirect sections (+ the necessary blend remap) -- i.e. pure geometry fold.

Phase strategy (plan "reproduce first, then extend"):
  * M2 (holes, unedited): the morph body<->dungeon correspondence is rebuilt by **export-layer position matching** -- same algorithm and data as the external one -> byte-for-byte == green mod.
  * M3 (edited): switch to **VertexId + producer correspondence** (position-independent) for edit robustness; blend remap is already edit-robust since it's applied via the VG table.

This module's buffer/ini functions are pure numpy / strings, with no bpy dependency; orchestration (exporting each IB, calling this module, merging) lives in the orchestrator.
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


# ----------------------------------------------------------------- ini parsing

def parse_draws(text):
    """{component_id: [(index_count, first_index), ...]} (one component may have multiple draws, e.g. C5 = c5-face + the little bear)."""
    d = {}
    for m in re.finditer(r'\[TextureOverrideComponent(\d+)\][^\[]*', text):
        d[int(m.group(1))] = [(int(c), int(o)) for c, o in re.findall(r'drawindexed = (\d+), (\d+)', m.group(0))]
    return d


def parse_match(text):
    """{component_id: (match_first_index, match_index_count)}."""
    d = {}
    for m in re.finditer(r'\[TextureOverrideComponent(\d+)\][^\[]*', text):
        fi = re.search(r'match_first_index = (\d+)', m.group(0))
        ic = re.search(r'match_index_count = (\d+)', m.group(0))
        if fi and ic:
            d[int(m.group(1))] = (int(fi.group(1)), int(ic.group(1)))
    return d


# ------------------------------------------------- 1) morph dun2body reprojection

def reproject_morph(body_meshes, face_meshes, body_segs, seg_comp, face_draws, tag, ref_meshes=None):
    """Reproject the dungeon IB's shapekeys onto the base(body) vertex order, writing ShapeKey{Offset,VertexId,VertexOffset}_<tag>.buf.

    body-centric: for each body seg vertex find the nearest dungeon vertex (**component-scoped**: body C2<->face C0 etc.,
    to prevent face vertices projecting onto a co-located different component), invert to get ``dungeon_vert -> [body verts]``; then for each shapekey,
    each dungeon shapekey entry, emit the body vertices under it -- guaranteeing **exactly once per shapekey per body vid**.

    Args:
      body_segs:  {body_comp: (index_count, first_index)}  the draw segment of each corresponding body component (c5-face takes the first draw).
      seg_comp:   [(body_comp, face_comp), ...]            body component <- dungeon face component pairing.
      face_draws: {face_comp: (index_count, first_index)}  the draw segment of each dungeon face component.
    Returns batch_counts (the number of reprojected entries per batch, for ini constants).

    M2: use export-layer position matching (same algorithm/data as the external xscene_fold_prep -> byte-for-byte identical).
    """
    body_meshes, face_meshes = Path(body_meshes), Path(face_meshes)
    # ref_meshes = unedited body reference (passed in when edit-derived hole=False): dun2body's position matching must use unedited geometry
    # to match the dungeon face, otherwise the edited body won't align with the dungeon. The body row numbers referenced by morph are
    # unchanged before/after editing under the same topology, so the morph computed from ref is written directly into (applies to) the actually exported body_meshes. When hole=True/unedited, ref==body.
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

    # invariant self-check: within any shapekey, body vid must not repeat (= InterlockedAdd does not over-accumulate)
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


# ------------------------------------------------- 2) blend VG remap application

def apply_blend_remap(body_meshes, vg_remap_table, body_seg, tag):
    """Per the VG table voted by the producer, relabel the blend indices of the vertices of some body component (the body_seg draw segment) -> ``Blend_<tag>.buf``.

    blend = R8_UINT stride16 (8 idx + 8 wt). Only relabel slots that are "a vertex of this component + this slot's weight>0 + this VG is in the table";
    VG slots not in the table keep their original value (the external script likewise leaves these slots untouched, byte-for-byte identical on both sides, verified). Returns the output filename.
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


# ------------------------------------------------- 3) ini sections: Host redirect + morph CS transplant

def _section(text, header):
    """Extract the ``[header]`` section (including the header, up to the next ``[``). None if absent."""
    m = re.search(r'(^\[' + re.escape(header) + r'\][^\[]*)', text, re.M)
    return m.group(1) if m else None


def _build_morph_sections(face_text, tag):
    """Transplant the dungeon face's morph CS sections into ``_<tag>``: rename data buffers + repoint to ``_<tag>.buf``,
    swap the custom vertex offset/count to the ``_<tag>`` globals; RW scratch (CBRW/CustomShapeKeyValuesRW) reuses the body's shared one, not copied.
    Parameters such as hash/checksum/dispatch/cs are parsed from the face's mod.ini (which the stock export already wrote in)."""
    R = tag
    mh = re.search(r'\[TextureOverrideShapeKeyOffsets\]\s*\nhash = (\w+)', face_text).group(1)
    sh = re.search(r'\[TextureOverrideShapeKeyScale\]\s*\nhash = (\w+)', face_text).group(1)
    chk = re.findall(r'shapekey_checksum_batch\d+ = (\d+)', face_text)
    orig = re.findall(r'shapekey_vertex_offset_original_batch\d+ = (\d+)', face_text)
    disp = re.findall(r'shapekey_dispatch_size_y_original_batch\d+ = (\d+)', face_text)
    # COMPONENT face callback: 'if cs == <v>'; MERGED face: 'if cs == <v> && ResourceMergedSkeleton !== null'.
    # Capture the WHOLE condition (not just the value) so the transplanted callback keeps the MERGED skeleton
    # guard; for COMPONENT this is identical to the old behavior (group == 'cs == <v>').
    csL = re.search(r'if (cs ==[^\n]*?)\s*\n\s*handling = skip\s*\n\s*run = CommandListSetupShapeKeysBatch', face_text).group(1).strip()
    csM = re.search(r'if (cs ==[^\n]*?)\s*\n\s*handling = skip\s*\n\s*run = CommandListMultiplyShapeKeys', face_text).group(1).strip()
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
             "if $mod_enabled\n    if %s\n        handling = skip\n"
             "        run = CommandListSetupShapeKeysBatch_%s\n        run = CommandListLoadShapeKeysBatch_%s\n"
             "    endif\nendif\n\n" % (R, mh, csL, R, R))
    s.append("[CommandListMultiplyShapeKeys_%s]\n$\\WWMIv1\\custom_vertex_count = $mesh_vertex_count\n"
             "run = CustomShader\\WWMIv1\\ShapeKeyMultiplier\n\n" % R)
    s.append("[TextureOverrideShapeKeyMultiplierCallback_%s]\nhash = %s\nmatch_priority = 0\n"
             "if $mod_enabled\n    if %s\n        handling = skip\n"
             "        run = CommandListMultiplyShapeKeys_%s\n    endif\nendif\n\n" % (R, mh, csM, R))
    s.append("[ResourceShapeKeyOffsetBuffer_%s]\ntype = Buffer\nformat = DXGI_FORMAT_R32G32B32A32_UINT\n"
             "stride = 16\nfilename = Meshes/ShapeKeyOffset_%s.buf\n\n" % (R, R))
    s.append("[ResourceShapeKeyVertexIdBuffer_%s]\ntype = Buffer\nformat = DXGI_FORMAT_R32_UINT\n"
             "stride = 4\nfilename = Meshes/ShapeKeyVertexId_%s.buf\n\n" % (R, R))
    s.append("[ResourceShapeKeyVertexOffsetBuffer_%s]\ntype = Buffer\nformat = DXGI_FORMAT_R16_FLOAT\n"
             "stride = 2\nfilename = Meshes/ShapeKeyVertexOffset_%s.buf\n" % (R, R))
    return "".join(s)


def emit_fold_sections(body_text, face_text, fold_entry, body_draws, face_match, batch_counts, tag, has_morph=True):
    """Inject the ini sections needed for folding, return the modified body_text:
      1) constants: per-batch $shapekey_vertex_offset/count_batch{N}_<tag> (inserted after the body's last batch constant) -- only when has_morph;
      2) FoldHost: each dungeon component's draw bound to the buffer range of the corresponding base component (those with mismatched VG go through the _remap CommandList);
      3) _remap CommandList (copy the body's CommandListOverrideSharedResources, only swap vb4) + ResourceBlendBuffer_<tag>;
      4) morph CS section transplant (_build_morph_sections) -- only when has_morph (skipped for fold pieces without shapekeys such as clothing).
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
    """For one foldable IB: on the already stock-exported ``work/body`` and ``work/<tag>``, reproject morph (if any) + apply blend remap
    + inject ini sections, **modifying work/body in place** (geometry fold, morph reprojection, VG remap all merged into the base buffer mod).

    ``tag`` = the IB's vb0 hash (unique), used for section/buffer naming + locating the exported host directory ``work/<tag>``.
    Fold pieces without shapekeys (clothing: their exported host has no ShapeKeyOffset.buf) only fold geometry + the necessary blend remap, skipping morph."""
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
