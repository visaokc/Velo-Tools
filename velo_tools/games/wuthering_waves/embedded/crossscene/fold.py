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

# Slot-style section names -- mirror velo_tools/.../slot_textures/constants.py (CMDLIST_SET_TEXTURES /
# CMDLIST_PROBE / CMDLIST_RESTORE / SEC_FORMAT_TAG). fold.py must stay standalone-importable (the
# headless tests load it by file path, with no package context, so a relative import of that module
# would fail) -- keep these literals in sync with constants.py by hand.
_SLOT_SET = "CommandListSetTexturesComponent%d"
_SLOT_PROBE = "CommandListProbeComponent%d"
_SLOT_RESTORE = "CommandListRestoreTextures"


def _qk(p):
    return (int(round(p[0] / GRID)), int(round(p[1] / GRID)), int(round(p[2] / GRID)))


def _rd(path, dt):
    return np.frombuffer(Path(path).read_bytes(), dtype=dt)


# ----------------------------------------------------------------- ini parsing

def _draw_entries(block):
    entries = []
    label = None
    for ln in (block or "").splitlines():
        s = ln.strip()
        if s.startswith("; Draw "):
            label = s.split("; Draw ", 1)[1].strip()
            continue
        m = re.match(r'drawindexed = (\d+), (\d+)', s)
        if not m:
            continue
        entries.append((int(m.group(1)), int(m.group(2)), label))
        label = None
    return entries


def parse_draw_plan(text):
    """{component_id: [(index_count, first_index, label), ...]} following LOD-fork draw lists."""
    d = {}
    for m in re.finditer(r'\[TextureOverrideComponent(\d+)\][^\[]*', text):
        cid = int(m.group(1))
        draws = _draw_entries(m.group(0))
        if not draws and re.search(r'run = CommandListDrawComponent%d\b' % cid, m.group(0)):
            blk = _section(text, 'CommandListDrawComponent%d' % cid)
            if blk:
                draws = _draw_entries(blk)
        d[cid] = draws
    return d


def parse_draws(text):
    """{component_id: [(index_count, first_index), ...]} (one component may have multiple draws, e.g. C5 = c5-face + the little bear).

    LOD-fork inis factor the inline draws into shared [CommandListDrawComponent{c}]
    lists (the component section just runs them); follow that indirection so both
    ini shapes parse identically."""
    return {
        cid: [(cnt, off) for cnt, off, _label in entries]
        for cid, entries in parse_draw_plan(text).items()
    }


def _normalise_draw_entries(draws):
    out = []
    for entry in draws or []:
        if len(entry) >= 3:
            out.append((int(entry[0]), int(entry[1]), entry[2]))
        else:
            out.append((int(entry[0]), int(entry[1]), None))
    return out


def _draw_atom_name(component_id, ordinal):
    return "CommandListDrawAtomComponent%d_%d" % (int(component_id), int(ordinal))


def _draw_atom_names(component_id, draws):
    names = {}
    for ordinal, (cnt, off, _label) in enumerate(_normalise_draw_entries(draws)):
        names.setdefault((cnt, off), _draw_atom_name(component_id, ordinal))
    return names


def _draw_owner_name(component_id):
    return "CommandListDrawOwnerComponent%d" % int(component_id)


def _skip_var_name(component_id, ordinal):
    return "$xscene_skip_draw_c%d_%d" % (int(component_id), int(ordinal))


def _draw_owner_run_lines(component_id, draws, selected):
    selected_pairs = {
        (int(cnt), int(off))
        for cnt, off, _label in _normalise_draw_entries(selected)
    }
    lines = []
    for ordinal, (cnt, off, _label) in enumerate(_normalise_draw_entries(draws)):
        if (cnt, off) not in selected_pairs:
            lines.append("    %s = 1" % _skip_var_name(component_id, ordinal))
    lines.append("    run = %s" % _draw_owner_name(component_id))
    for ordinal, (cnt, off, _label) in enumerate(_normalise_draw_entries(draws)):
        if (cnt, off) not in selected_pairs:
            lines.append("    %s = 0" % _skip_var_name(component_id, ordinal))
    return lines


def select_fold_draws(draws, excluded_labels=None):
    """Return draw entries a FoldHost may replay for one base component.

    Own-buffer split draws are excluded by their ``; Draw`` label. If an old/custom ini
    has no matching labels for a known split, keep legacy primary-only behavior rather
    than risk drawing the split part through the fold.
    """
    entries = _normalise_draw_entries(draws)
    if not entries:
        return []
    excluded = {str(label) for label in (excluded_labels or set())}
    if not excluded:
        return entries
    labelled = [entry for entry in entries if entry[2] in excluded]
    if not labelled:
        return entries[:1]
    return [entry for entry in entries if entry[2] not in excluded]


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

def _iter_key_entries(sko):
    """Yield (global_key_id, lo, hi) entry ranges per shapekey from a stock ShapeKeyOffset
    table (uint32 x128 per batch, cumulative within batch; key id = batch*127 + slot)."""
    nbatch = len(sko) // 128
    bstart = 0
    for b in range(nbatch):
        bo = sko[b * 128:(b + 1) * 128]
        for s in range(127):
            lo, hi = bstart + int(bo[s]), bstart + int(bo[s + 1])
            if hi > lo:
                yield b * 127 + s, lo, hi
        bstart += int(bo[127])


def reproject_morph(body_meshes, face_meshes, body_segs, seg_comp, face_draws, tag, ref_meshes=None,
                    morph_id_map=None, morph_scale=None):
    """Build the dungeon morph buffers ShapeKey{Offset,VertexId,VertexOffset}_<tag>.buf in body
    vertex order. Each dungeon shapekey takes one of two sources:

    * **voted keys** (producer ``morph_id_map``: dungeon key id -> base key id): entries come
      from the BODY EXPORT's own ShapeKey buffers -- the user's EDITED shape keys, already in
      body vertex order (exactly the rows the folded draws read). No position matching involved,
      so delta edits propagate and full mesh replacement stays valid. Entries are restricted to
      the folded components' vertex ranges; ``morph_scale`` (cross-pipeline delta factor voted
      by the producer, expected ~1) is applied to the position components when it deviates.
    * **unvoted keys / no map (old routings)**: the original pristine reprojection -- for each
      body seg vertex find the nearest dungeon vertex (**component-scoped**: to prevent face
      vertices projecting onto a co-located different component), invert to
      ``dungeon_vert -> [body verts]`` and re-emit the dungeon extract's deltas, exactly once
      per shapekey per body vid. Known limitation: far-moved vertices approximate by nearest
      neighbor.

    Args:
      body_segs:  {body_comp: (index_count, first_index)}  the draw segment of each corresponding body component (c5-face takes the first draw).
      seg_comp:   [(body_comp, face_comp), ...]            body component <- dungeon face component pairing.
      face_draws: {face_comp: (index_count, first_index)}  the draw segment of each dungeon face component.
      ref_meshes: unedited body reference for the fallback position matching (edit-derived
                  exports pass it; body row numbers are identical under unchanged topology).
    Returns batch_counts (the number of emitted entries per dungeon batch, for ini constants).
    """
    body_meshes, face_meshes = Path(body_meshes), Path(face_meshes)

    sko = _rd(face_meshes / "ShapeKeyOffset.buf", np.uint32)
    svid = _rd(face_meshes / "ShapeKeyVertexId.buf", np.uint32)
    svoff_b = (face_meshes / "ShapeKeyVertexOffset.buf").read_bytes()
    stride = len(svoff_b) // len(svid)
    nbatch = len(sko) // 128

    id_map = {int(k): int(v) for k, v in (morph_id_map or {}).items()}

    # ---- mapped source: the body export's edited ShapeKey buffers, sliced per base key id
    mapped = {}
    body_sko_p = body_meshes / "ShapeKeyOffset.buf"
    if id_map and body_sko_p.is_file():
        bko = _rd(body_sko_p, np.uint32)
        bvid = _rd(body_meshes / "ShapeKeyVertexId.buf", np.uint32)
        bvoff_b = (body_meshes / "ShapeKeyVertexOffset.buf").read_bytes()
        bstride = len(bvoff_b) // max(1, len(bvid))
        if bstride != stride:
            raise AssertionError(
                "ShapeKeyVertexOffset stride mismatch body=%d dungeon=%d" % (bstride, stride))
        voff_rows = np.frombuffer(bvoff_b, np.uint8).reshape(-1, stride)
        # gate entries to this fold's component vertex ranges (a base morph component folded
        # into ANOTHER IB must not leak into this IB's buffers)
        body_idx_real = _rd(body_meshes / "Index.buf", np.uint32)
        allowed = np.unique(np.concatenate(
            [body_idx_real[off:off + cnt] for cnt, off in body_segs.values()]
            or [np.empty(0, np.uint32)]))
        base_entries = {key: (lo, hi) for key, lo, hi in _iter_key_entries(bko)}
        scale = float(morph_scale) if morph_scale else 1.0
        for dkey, bkey in id_map.items():
            rng = base_entries.get(bkey)
            if rng is None:
                # key absent on the edited base (e.g. user removed it) -> empty, consistent
                # with the showcase side
                mapped[dkey] = (np.empty(0, np.uint32), b"")
                continue
            vids = bvid[rng[0]:rng[1]]
            keep = np.isin(vids, allowed)
            rows = voff_rows[rng[0]:rng[1]][keep]
            if abs(scale - 1.0) > 0.01 and len(rows):
                rows = rows.copy()
                pos = rows[:, :6].copy().view(np.float16) * np.float16(scale)
                rows[:, :6] = pos.view(np.uint8).reshape(len(rows), 6)
            mapped[dkey] = (vids[keep], rows.tobytes())

    # ---- fallback source: pristine reprojection (position matching) for unvoted keys only
    dun2body = defaultdict(list)
    if any(key not in mapped for key, _lo, _hi in _iter_key_entries(sko)):
        # ref_meshes = unedited body reference (passed in when edit-derived hole=False): dun2body's position matching must use unedited geometry
        # to match the dungeon face, otherwise the edited body won't align with the dungeon. The body row numbers referenced by morph are
        # unchanged before/after editing under the same topology, so the morph computed from ref is written directly into (applies to) the actually exported body_meshes. When hole=True/unedited, ref==body.
        ref = Path(ref_meshes) if ref_meshes else body_meshes
        body_pos = _rd(ref / "Position.buf", np.float32).reshape(-1, 3)
        face_pos = _rd(face_meshes / "Position.buf", np.float32).reshape(-1, 3)
        body_idx = _rd(ref / "Index.buf", np.uint32)
        face_idx = _rd(face_meshes / "Index.buf", np.uint32)
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

    new_sko, new_svid, new_svoff = [], [], bytearray()
    batch_counts = []
    bstart = 0
    for b in range(nbatch):
        bo = sko[b * 128:(b + 1) * 128]
        nbo, cum = [0], 0
        for s in range(127):
            key = b * 127 + s
            if key in mapped:
                vids, blob = mapped[key]
                new_svid.extend(int(v) for v in vids)
                new_svoff += blob
                cum += len(vids)
            else:
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


def _slot_active(body_text, bc):
    """True when the body was exported slot-style for base component ``bc`` (its per-draw rebind
    list exists). Hash-style bodies have no such list -> the fold stays byte-identical to before."""
    return ('[%s]' % (_SLOT_SET % bc)) in body_text


def _fold_slot_runs(body_text, bc):
    """The slot command-list ``run`` lines a folded dungeon draw must execute so its textures are
    rebound to the BASE component's slot maps (= master content, streaming-immune): SetTextures runs
    after the resource-override trigger. Legacy probe/restore lists are still tolerated if an older body ini is folded. Returns
    (before_trigger, after_trigger, before_cleanup); all empty for a hash-style body."""
    if not _slot_active(body_text, bc):
        return [], [], []
    before_trigger, after_trigger, before_cleanup = [], [], []
    if ('[%s]' % (_SLOT_PROBE % bc)) in body_text:
        before_trigger.append('run = %s' % (_SLOT_PROBE % bc))
    after_trigger.append('run = %s' % (_SLOT_SET % bc))
    if ('[%s]' % _SLOT_RESTORE) in body_text:
        before_cleanup.append('run = %s' % _SLOT_RESTORE)
    return before_trigger, after_trigger, before_cleanup


def _fold_format_tag_twins(body_text, bc, fc, tag, mfi, mic):
    """Replicate the base component's format-family tag sections (``[TextureOverrideComponent{bc}{fmt}]``)
    at the folded dungeon draw's index range so the base SetTextures conditions fire there too.

    Format tags match by (format-family + index range) with NO hash; the base tags are scoped to the
    base draw range, so without a twin at the dungeon range the slot conditions go blind on the
    dungeon draw (same field-proven reason LOD draws need per-level twins). filter_index/match_format
    are copied verbatim (the family value is what the SetTextures list reads); only the match window
    is swapped. The bare ``[TextureOverrideComponent{bc}]`` draw section (no format suffix) and the
    ``TextureOverrideLod*`` twins are excluded. Empty for a hash-style body."""
    if not _slot_active(body_text, bc):
        return []
    twins = []
    pat = re.compile(r'^\[TextureOverrideComponent%d([A-Za-z][^\]]*)\]\s*$' % bc, re.M)
    for m in pat.finditer(body_text):
        fmt = m.group(1)
        sec = _section(body_text, 'TextureOverrideComponent%d%s' % (bc, fmt))
        if not sec:
            continue
        lines = sec.rstrip('\n').split('\n')
        new = ['[TextureOverride_FoldHost_%s_C%d_%s]' % (tag, fc, fmt)]
        for ln in lines[1:]:
            s = ln.strip()
            if s.startswith('match_first_index'):
                new.append('match_first_index = %d' % mfi)
            elif s.startswith('match_index_count'):
                new.append('match_index_count = %d' % mic)
            else:
                new.append(ln)
        twins.append('\n'.join(new))
    return twins


def _merge_offset_shift(remap_table):
    """For a NON-identity fold's vg_remap (base-component-local VG -> dungeon-IB-local VG), return
    ``(shift, count)`` so the MERGED FoldHost merges the dungeon cb4 at ``base_vg_offset + shift`` for
    ``count`` bones, landing them where the base component's surviving geometry reads them.

    Requires a UNIFORM shift: the split removed a contiguous LEADING block of bones (e.g. the bear is the
    first 11 VGs of C5), so base-local key ``k`` -> dungeon-local ``k - shift`` and dungeon locals are
    ``0..count-1`` contiguous. A non-uniform / permuted remap (the split block was not a contiguous lead)
    needs a general per-bone skeleton remap, which is NOT implemented -> hard error (foolproof)."""
    items = sorted((int(k), int(v)) for k, v in remap_table.items())
    keys = [k for k, _ in items]
    vals = [v for _, v in items]
    n = len(items)
    shift = keys[0] - vals[0]
    if vals != list(range(n)) or any(k - v != shift for k, v in items):
        raise RuntimeError(
            "Cross-scene MERGED fold: non-uniform vg_remap (%d entries, base %d..%d -> dungeon %d..%d). The "
            "split is not a contiguous leading-bone block; a general per-bone skeleton remap is required but "
            "not implemented." % (n, keys[0], keys[-1], vals[0], vals[-1]))
    return shift, n


def _build_merged_foldhost(body_text, bc, fc, tag, fh, mfi, mic, vg_shift=0, vg_count_override=None,
                           draws=None, all_draws=None):
    """MERGED FoldHost = the native body ``[TextureOverrideComponent<bc>]`` override replicated verbatim,
    so it inherits everything that component needs to draw correctly: the ``if $merge_status_id != 2``
    skeleton-build block, the ``if ResourceMergedSkeleton !== null`` draw block, and -- crucially for
    components whose VG ids reach >=256 (C4/C5 of this character) -- the per-component BlendRemap override
    refs (``ResourceBlendBufferOverride/MergedSkeletonOverride/ExtraMergedSkeletonOverride = ref
    Resource{Remapped...}Component<bc>``) that route the draw through the remapped blend+skeleton. <256
    components carry no such refs in the native override, so they are naturally omitted.

    Only the section header, hash, and match window are swapped (so the dungeon draw triggers it), and the
    draws are filtered to the base component's fold draw plan -- any own-buffer split sub-draw (e.g. the
    bear C5.001) is drawn by its own IB, not the fold. Other visible sub-draws of the same component must
    remain in the plan. Indentation (incl. the merge block's tabs) is preserved verbatim.
    The per-component remapped buffers are filled every frame by InitializeBlendRemaps + RemapMergedSkeleton
    (from [Present], gated by $object_detected, which this section sets), so they are populated in pure-
    dungeon scenes too.

    ``vg_shift``/``vg_count_override`` (non-identity folds only): the dungeon model lacks the split-out
    sub-component (e.g. the bear), so its bones are numbered ``vg_shift`` lower and there are
    ``vg_count_override`` of them. The merge block's ``$\\WWMIv1\\vg_offset``/``vg_count`` are rewritten so
    the dungeon cb4 lands at ``base_offset + vg_shift`` (where the base surviving geometry reads), not at the
    bear-inclusive base offset. Identity folds pass ``vg_shift=0`` (no rewrite, byte-identical).

    ``draws`` = selected fold draw plan entries ``(index_count, first_index, label)``. Needed for the
    LOD-fork ini shape, where the native section carries no inline draws but delegates to the shared
    ``[CommandListDrawComponent{N}]`` list -- that list may hold own-buffer split sub-draws plus the
    ``$lod_level`` dispatch, so running it from the FoldHost would also draw the split part with shifted
    (wrong) bones in the dungeon scene. FoldHost therefore runs the canonical draw owner with temporary
    skip vars for excluded sub-draws instead of expanding atom runs itself."""
    native = _section(body_text, "TextureOverrideComponent%d" % bc)
    if not native:
        return ""
    selected = _normalise_draw_entries(draws)
    all_draws = _normalise_draw_entries(all_draws if all_draws is not None else draws)
    selected_pairs = {(cnt, off) for cnt, off, _label in selected}
    out, seen_draw, header_done, pending_comment = [], False, False, None
    for ln in native.rstrip("\n").split("\n"):
        s = ln.strip()
        if not header_done and s.startswith("[TextureOverrideComponent"):
            out.append("[TextureOverride_FoldHost_%s_C%d]" % (tag, fc))
            header_done = True
            pending_comment = None
        elif s.startswith("hash ="):
            out.append("hash = %s" % fh)
        elif s.startswith("match_first_index ="):
            out.append("match_first_index = %d" % mfi)
        elif s.startswith("match_index_count ="):
            out.append("match_index_count = %d" % mic)
        elif vg_shift and s.startswith("$\\WWMIv1\\vg_offset ="):
            indent = ln[:len(ln) - len(ln.lstrip())]
            out.append("%s$\\WWMIv1\\vg_offset = %d" % (indent, int(s.split("=", 1)[1]) + vg_shift))
        elif vg_count_override is not None and s.startswith("$\\WWMIv1\\vg_count ="):
            indent = ln[:len(ln) - len(ln.lstrip())]
            out.append("%s$\\WWMIv1\\vg_count = %d" % (indent, vg_count_override))
        elif s.startswith("drawindexed"):
            m = re.match(r'drawindexed = (\d+), (\d+)', s)
            if m and (int(m.group(1)), int(m.group(2))) in selected_pairs:
                if pending_comment:
                    out.append(pending_comment)
                if not seen_draw:
                    indent = ln[:len(ln) - len(ln.lstrip())]
                    for run_line in _draw_owner_run_lines(bc, all_draws, selected):
                        out.append("%s%s" % (indent, run_line.strip()))
                    seen_draw = True
            pending_comment = None
        elif s.startswith("run = CommandListDrawComponent"):
            # LOD-fork shape: replace the shared-list delegation with the stock inline
            # sequence, selected draws only (see docstring). Slot-style: weave in the base
            # component's per-draw texture rebind (probe/set/restore) so the dungeon draw
            # rebinds to master textures -- the shared draw list's slot runs aren't reachable
            # from this inlined sequence. No-ops for a hash-style body.
            if not selected:
                raise ValueError("LOD-fork body ini shape requires at least one fold draw of C%d" % bc)
            indent = ln[:len(ln) - len(ln.lstrip())]
            bt, at, bcl = _fold_slot_runs(body_text, bc)
            out.extend("%s%s" % (indent, r) for r in bt)
            out.append("%srun = CommandListTriggerResourceOverrides" % indent)
            out.extend("%s%s" % (indent, r) for r in at)
            out.append("%srun = CommandListOverrideSharedResources" % indent)
            for run_line in _draw_owner_run_lines(bc, all_draws, selected):
                out.append("%s%s" % (indent, run_line.strip()))
            out.extend("%s%s" % (indent, r) for r in bcl)
            out.append("%srun = CommandListCleanupSharedResources" % indent)
            seen_draw = True
        elif s.startswith("; Draw "):
            pending_comment = ln
        else:
            pending_comment = None
            out.append(ln)
    return "\n".join(out) + "\n"


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


def _build_empty_foldhost(fc, tag, fh, mfi, mic, reason):
    lines = [
        "[TextureOverride_FoldHost_%s_C%d]" % (tag, fc),
        "hash = %s" % fh,
        "match_first_index = %d" % mfi,
        "match_index_count = %d" % mic,
        "$object_detected = 1",
        "if $mod_enabled",
        "    handling = skip",
        "    ; Draw skipped: %s" % reason,
        "endif",
    ]
    return "\n".join(lines) + "\n"


def emit_empty_fold_sections(body_text, fold_entry, face_match, tag, comp_map, reason):
    """Append FoldHost skip-only sections for scene draws whose mapped base component is excluded."""
    if not comp_map:
        return body_text
    fh = fold_entry["ib_hash"]
    out = ["\n; ==== fold %s (%s) skipped excluded base buffer ranges ====\n" % (tag, fh)]
    for fc in sorted(comp_map):
        mfi, mic = face_match[fc]
        out.append(_build_empty_foldhost(fc, tag, fh, mfi, mic, reason))
    return body_text + "".join(out)


def emit_fold_sections(body_text, face_text, fold_entry, body_draws, face_match, batch_counts, tag,
                       has_morph=True, comp_map=None, draw_excludes=None):
    """Inject the ini sections needed for folding, return the modified body_text:
      1) constants: per-batch $shapekey_vertex_offset/count_batch{N}_<tag> (inserted after the body's last batch constant) -- only when has_morph;
      2) FoldHost: each dungeon component's draw bound to the buffer range of the corresponding base component (those with mismatched VG go through the _remap CommandList);
      3) _remap CommandList (copy the body's CommandListOverrideSharedResources, only swap vb4) + ResourceBlendBuffer_<tag>;
      4) morph CS section transplant (_build_morph_sections) -- only when has_morph (skipped for fold pieces without shapekeys such as clothing).
    """
    fh = fold_entry["ib_hash"]
    if comp_map is None:
        comp_map = {int(k): v for k, v in fold_entry["fold"]["comp_map"].items()}
    # MERGED body carries the merged-skeleton machinery; COMPONENT body has none.
    is_merged = "ResourceMergedSkeleton" in body_text
    vg_remap_all = fold_entry["fold"].get("vg_remap", {})
    body_draw_plan = {
        int(cid): _normalise_draw_entries(draws)
        for cid, draws in (body_draws or {}).items()
    }
    draw_excludes = {int(k): set(v or set()) for k, v in (draw_excludes or {}).items()}
    # The producer vg_remap maps base-component-local VG -> dungeon-IB-local VG. In COMPONENT it drives an
    # 8-bit Blend relabel (_c{N}remap CommandList + Blend_c{N}remap.buf). In MERGED that relabel is neither
    # needed nor correct; instead the same remap is folded into the FoldHost's merge vg_offset (see
    # _merge_offset_shift) so the dungeon cb4 lands where the base surviving geometry reads. So the _c{N}remap
    # machinery is COMPONENT-only.
    _fold_targets = set(comp_map.values())
    remap_cmd = {} if is_merged else {int(k): ("CommandListOverrideSharedResources_c%dremap" % int(k), "c%dremap" % int(k))
                                      for k in vg_remap_all if int(k) in _fold_targets}

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
        if is_merged:
            # Replicate the native body component override (incl. the >=256 RemappedBlend override refs),
            # swapping only header/hash/match + trimming to the primary draw. This both feeds the body
            # skeleton in pure-dungeon scenes ($object_detected) and routes >=256 components through the
            # remapped blend/skeleton -- which the old hand-built FoldHost failed to do (C4 mis-weighted).
            # Non-identity fold (e.g. C5, whose bear C5.001 was split off): shift the merge vg_offset by the
            # dungeon's missing-leading-bone count so its cb4 lands where the base surviving geometry reads.
            # (Slot-style: the non-LOD-fork replica inherits the body override's SetTextures run
            # verbatim; the LOD-fork inlined sequence gets them re-woven in _build_merged_foldhost.)
            rt = vg_remap_all.get(str(bc))
            vg_shift, vg_count_override = _merge_offset_shift(rt) if rt else (0, None)
            selected = select_fold_draws(body_draw_plan.get(bc), draw_excludes.get(bc))
            out.append(_build_merged_foldhost(body_text, bc, fc, tag, fh, mfi, mic, vg_shift, vg_count_override,
                                              draws=selected, all_draws=body_draw_plan.get(bc)))
        else:
            selected = select_fold_draws(body_draw_plan.get(bc), draw_excludes.get(bc))
            ovr = remap_cmd[bc][0] if bc in remap_cmd else "CommandListOverrideSharedResources"
            # Slot-style: rebind the dungeon draw's textures to the BASE component's slot maps (master
            # content, streaming-immune). Empty for a hash-style body -> output byte-identical to before.
            bt, at, bcl = _fold_slot_runs(body_text, bc)
            lines = ["[TextureOverride_FoldHost_%s_C%d]" % (tag, fc),
                     "hash = %s" % fh,
                     "match_first_index = %d" % mfi,
                     "match_index_count = %d" % mic,
                     "$object_detected = 1",
                     "if $mod_enabled",
                     "    handling = skip"]
            lines += ["    %s" % r for r in bt]
            lines.append("    run = CommandListTriggerResourceOverrides")
            lines += ["    %s" % r for r in at]
            lines.append("    run = %s" % ovr)
            lines += _draw_owner_run_lines(bc, body_draw_plan.get(bc), selected)
            lines += ["    %s" % r for r in bcl]
            lines.append("    run = CommandListCleanupSharedResources")
            lines.append("endif")
            out.append("\n".join(lines) + "\n")
        # Slot-style: format-family tag twins at the dungeon draw's index range so the base
        # SetTextures conditions fire on it (no-op for a hash-style body).
        for twin in _fold_format_tag_twins(body_text, bc, fc, tag, mfi, mic):
            out.append("\n" + twin + "\n")
    base_cmd = _section(body_text, "CommandListOverrideSharedResources")
    for bc, (cmdname, btag) in remap_cmd.items():  # empty in MERGED (vg_remap dropped above)
        blk = base_cmd.replace("[CommandListOverrideSharedResources]", "[%s]" % cmdname, 1)
        blk = re.sub(r'vb4 = \w+', "vb4 = ResourceBlendBuffer_%s" % btag, blk)
        out.append("\n" + blk.rstrip() + "\n")
        out.append("\n[ResourceBlendBuffer_%s]\ntype = Buffer\nformat = DXGI_FORMAT_R8_UINT\n"
                   "stride = 16\nfilename = Meshes/Blend_%s.buf\n" % (btag, btag))
    if has_morph:
        out.append(_build_morph_sections(face_text, tag))
    return body_text + "".join(out)


def apply_fold(work, fold_entry, tag, morph_ref=None, draw_excludes=None):
    """For one foldable IB: on the already stock-exported ``work/body`` and ``work/<tag>``, reproject morph (if any) + apply blend remap
    + inject ini sections, **modifying work/body in place** (geometry fold, morph reprojection, VG remap all merged into the base buffer mod).

    ``tag`` = the IB's vb0 hash (unique), used for section/buffer naming + locating the exported host directory ``work/<tag>``.
    Fold pieces without shapekeys (clothing: their exported host has no ShapeKeyOffset.buf) only fold geometry + the necessary blend remap, skipping morph."""
    work = Path(work)
    body, face = work / "body", work / tag
    body_text = (body / "mod.ini").read_text(encoding="utf-8")
    face_text = (face / "mod.ini").read_text(encoding="utf-8")
    # MERGED replicates the native component override (handles >=256 via runtime RemappedBlend); the
    # COMPONENT-mode producer vg_remap (8-bit Blend relabel) is neither needed nor consumed there.
    is_merged = "ResourceMergedSkeleton" in body_text
    body_plan = parse_draw_plan(body_text)
    bd, fd, fm = parse_draws(body_text), parse_draws(face_text), parse_match(face_text)
    comp_map_all = {int(k): v for k, v in fold_entry["fold"]["comp_map"].items()}
    # A fold target whose base component was excluded from the body export (Ignore Hidden Objects /
    # Ignore Nested Collections, or the object deleted) still needs to match and skip the dungeon
    # native draw. Otherwise the game's original component remains visible under the modded body.
    excluded_map = {fc: bc for fc, bc in comp_map_all.items() if not bd.get(bc)}
    excluded = sorted(set(excluded_map.values()))
    comp_map = {fc: bc for fc, bc in comp_map_all.items() if bd.get(bc)}
    vg_remap = {} if is_merged else fold_entry["fold"].get("vg_remap", {})
    has_morph = (face / "Meshes" / "ShapeKeyOffset.buf").exists()
    if has_morph and comp_map:
        body_segs = {bc: bd[bc][0] for bc in comp_map.values()}
        seg_comp = [(comp_map[fc], fc) for fc in sorted(comp_map)]
        face_draws = {fc: fd[fc][0] for fc in comp_map}
        batch_counts = reproject_morph(
            body / "Meshes", face / "Meshes", body_segs, seg_comp, face_draws, tag,
            ref_meshes=morph_ref,
            morph_id_map=fold_entry["fold"].get("morph_id_map"),
            morph_scale=(fold_entry["fold"].get("morph_selfcheck") or {}).get("scale"))
    else:
        batch_counts = []
    for k, table in vg_remap.items():
        if bd.get(int(k)):
            apply_blend_remap(body / "Meshes", table, bd[int(k)][0], "c%dremap" % int(k))
    if comp_map:
        new_body = emit_fold_sections(body_text, face_text, fold_entry, body_plan, fm, batch_counts, tag,
                                        has_morph=has_morph, comp_map=comp_map,
                                        draw_excludes=draw_excludes)
    else:
        new_body = body_text
    new_body = emit_empty_fold_sections(
        new_body, fold_entry, fm, tag, excluded_map,
        "mapped base component hidden/excluded from body export")
    (body / "mod.ini").write_text(new_body, encoding="utf-8")
    return excluded
