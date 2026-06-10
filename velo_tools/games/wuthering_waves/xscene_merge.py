"""WWMI cross-scene multi-IB merge -- preprocessing producer (host driver layer, never touches ``_wwmi_core``).

Merges "base IB extract (showcase 8287b2f2 = Component 0-7 whole character) + N dungeon IB
folders extracted from other frames (clothes/face/waist bear)" into **one editable extract folder** + one ``CrossSceneRouting.json``:

- Uses **position grid matching** (same mesh, bit-identical positions -> exact, no EFMI Chamfer heuristic)
  to find the base component set each dungeon IB corresponds to; detects "one IB fully wrapped by a single Component"
  (waist bear ⊆ Component 5) -> triggers a **buffer-level split** of that Component into ``Component 5``(remainder) +
  ``Component 5.001``(bear) as two ``.vb/.ib/.fmt`` sets, so after import the bear is an independently editable sub-object.
- Each dungeon IB's native extract is copied whole into ``scene_ibs/<hash>/``, for the later "one-click smart export" to reimport per IB as host.
- ``CrossSceneRouting.json`` records all routing info needed at export time (see ``_build_routing``).

This module only reads ``_wwmi_core``'s buffer/fmt I/O (``NumpyBuffer``/``MigotoFormat``), changing none of its lines.
The export consumer side is implemented separately in ``embedded/crossscene/`` (monkey-patches ``VTWW_Export.execute``).
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
from .embedded.lod import cross_scene as lod_cross_scene

GRID = 0.001
TOL = 0.0015
_OVERLAP_WARN = 0.5  # fold IB vs base mesh overlap ratio below this -> foolproof warning (likely independent morph, should be set Editable).
                     # Measured: normal fold parts (clothes/face/bear)=1.0; form2 face ≈0.05 (barely the same mesh as base).
_HOST_CORR_MIN = 0.95  # own-buffer split vs host vertex-correspondence coverage below this -> no VG table (legacy fallback).
_POS = AbstractSemantic(Semantic.Position)
_IDX = AbstractSemantic(Semantic.Index)
_BI = AbstractSemantic(Semantic.Blendindices)
_BW = AbstractSemantic(Semantic.Blendweight)
_NB = [(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)]


# --------------------------------------------------------------------------- IO

def _comp_names(folder: Path):
    """List the component names in the folder (sorted by number + sub-index, "Component 5" before "Component 5.001")."""
    names = []
    for p in folder.glob("*.fmt"):
        m = re.match(r".*component[ _-]*([0-9]+)(?:\.([0-9]+))?", p.stem.lower())
        if m:
            names.append((int(m.group(1)), int(m.group(2) or 0), p.stem))
    return [n for _, _, n in sorted(names)]


def _fix_vb_strides(vb_layout, fmt_text):
    """Recompute each element's real byte stride from the .fmt AlignedByteOffset (cases like WWMI's BLENDINDICES
    declared R8 but actually occupying 8 bytes, i.e. "declared format < actual span", must be computed from the
    difference of adjacent offsets, otherwise dtype itemsize won't match the .vb file size). Elements are already
    in ascending offset order; the last one is closed out with the header stride."""
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
    """Returns (indices, weights) each (N, K): K = number of bone slots (body/face=8). (None, None) if no blend semantics.
    WWMI extract has BLENDINDICES/BLENDWEIGHT each occupying 8 bytes (R8); get_field decodes them per the fixed stride
    as (N,8) uint8 (raw weights 0-255; voting sorts by descending weight, raw uint8 order == normalized order, so no normalization needed)."""
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
    """Non-shapekey vertex layout signature: ((SemanticName, SemanticIndex, Format, AlignedByteOffset), ...),
    excluding SHAPEKEY elements and not comparing extract stride.

    The extracted .fmt stores inline shapekeys into vb0 too: different morph counts -> bloated and mutually unequal
    strides, and SHAPEKEY's SemanticIndex also differs per component (face starts at 9, base C2 starts at 23).
    But the fold binds to the post-export separated buffers (Position/Blend/Vector/Color/TexCoord each independent);
    shapekeys are applied separately by the CS and aren't in the fold-bound vb -- so foldability only looks at the
    non-shapekey vb0 semantic layout, especially the blend bone count:
    body BLENDINDICES=R8_UINT@20->BLENDWEIGHT@28(span8=8 bones) vs bear R8G8B8A8_UINT@20->@24(span4=4 bones).
    The offset also encodes each field's span (gap to the next element), so the (format, offset) combo captures the bone-count difference.
    (Measured: face vs baseC2 fully equal = foldable; bear's blend format+offset both differ = not foldable.)"""
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
    """Whether two components' vertex layouts are element-by-element identical (decides whether a dungeon IB can fold into the base buffer)."""
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
    """Index of the nearest point in the grid within TOL of p; None if none."""
    k = _gk(p)
    best, bd = None, TOL * TOL
    for nb in _NB:
        for j in grid.get((k[0] + nb[0], k[1] + nb[1], k[2] + nb[2]), ()):
            dd = float(((points[j] - p) ** 2).sum())
            if dd <= bd:
                bd, best = dd, j
    return best


def _match_set(query: np.ndarray, target_points: np.ndarray, target_grid):
    """Returns whether each point in query has a <=TOL correspondence in target (exact same-grid match)."""
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
    """Split a component containing a sub-part wrapped by some dungeon IB into (remainder, member).

    Cuts by **triangle centroid** against the triangle centroids (ground truth) of all components in that wrapping
    IB (e.g. waist bear c4df4691): if a parent-component triangle centroid falls inside the wrapping IB -> goes to the
    sub-part, otherwise to the parent remainder.
    More accurate than the earlier union-find island method -- the sub-part and parent mesh often share edges/vertices,
    so union-find merges them into one island and **misses the cut** (measured: union-find ~2472 triangles, centroid
    method ~2702; external xscene_resplit has verified the centroid method correct). .fmt is reused as-is."""
    tris = _tris(ib)
    cents = _positions(vb)[tris].mean(axis=1)
    wf = Path(wrap_ib_folder)
    d_cent = []
    for dn in _comp_names(wf):
        dvb, dib, _ = _read_comp(wf, dn)
        d_cent.append(_positions(dvb)[_tris(dib)].mean(axis=1))
    d_cent = np.concatenate(d_cent)
    member_mask = _match_set(cents, d_cent, _build_grid(d_cent))  # whether each parent triangle centroid falls inside the wrapping IB

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
    """Copy ``* t=<hash>.dds`` textures from src into dst, deduplicated by hash (skip hashes already present)."""
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
    """Read-only analysis: which base components each dungeon IB corresponds to + whether it is wrapped by a single component (->split).
    dungeon_specs: [{"hash":..,"folder":Path,"role":..}]
    Returns [{hash, role, base_components, wrapped_in(None|comp_id), matched, member_in_comp:{cid:set}}]"""
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
        thr = max(1, int(0.005 * matched))  # filter boundary noise (e.g. a few face vertices mismatched onto the clothes component)
        base_components = sorted(c for c, n in hist.items() if n >= thr)
        wrapped = None
        if len(base_components) == 1:
            cid = base_components[0]
            ca, cb = base_off[cid]
            if len(member_in_comp[cid]) < (cb - ca):  # proper subset -> wrapped
                wrapped = cid
        # Fold gate: if the IB's vertex layout fully matches its corresponding base component layout -> foldable (bind base buffer);
        # if not (typically: different blend bone count, bear 4 bones vs body 8 bones) -> not foldable, go own-buffer.
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
    """Build a base VG -> IB VG remap by voting with corr_map (base local vertex row -> IB local vertex row) + each side's blend (8 idx + 8 wt).

    Background: during fold, FaceHost binds the base blend to the dungeon IB's host; if that component's VG numbering
    differs from the IB (e.g. c5-face was renumbered because bear 5.001 was merged back into parent C5), binding
    directly produces bone misalignment. Voting by vertex correspondence + sorted weights yields a base VG -> IB VG
    table; at export time the base blend is relabeled accordingly and bound separately to that host.

    Returns (remap|None, {matched, ambiguous, collisions, identity}); identity=True means naturally aligned (no remap needed)."""
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
    # per-VG coverage hard gate: every VG used by any vertex of the base component must be in the remap (even if that
    # vertex itself didn't match the IB, as long as its VG is covered by another matched vertex's vote); otherwise that
    # VG slot won't be relabeled at export time -> bone misalignment.
    used_vgs = set(int(v) for v in np.asarray(bbi)[np.asarray(bbw) > 0])
    uncovered_vgs = sorted(used_vgs - set(remap.keys()))
    return remap, {"matched": len(corr_map), "ambiguous": amb, "collisions": col, "identity": identity,
                   "used_vgs": len(used_vgs), "uncovered_vgs": len(uncovered_vgs)}


def _build_host_vg_table(out_dir, split_name, split_vb, host_folder):
    """For an own-buffer split part: vote a "split (base-component-local) VG -> host scene-IB
    local VG" translation table, so the export side can derive the host sub-mod from the EDITED
    split object instead of re-importing the pristine extract (edits then propagate). The table
    is bone-level, geometry-independent: it stays valid after any mesh edit or full replacement,
    as long as weights stay on the host's existing bones.

    Built at merge time because both sides are in bind pose with exactly overlapping positions
    (the only moment a vertex correspondence is trustworthy). Returns (record_fields|None, why):
    fields = {host_component_object, host_vg_remap(None=identity), host_vg_selfcheck} to merge
    into the split record; None + reason string when the table cannot be built (multi-component
    host / low coverage / uncovered VGs) -> the export side falls back to the legacy path."""
    host = Path(host_folder)
    host_names = _comp_names(host)
    if len(host_names) != 1:
        return None, "host has %d components, split part cannot be aligned" % len(host_names)
    host_vb, _, _ = _read_comp(host, host_names[0])
    hbi, hbw = _blend(host_vb)
    if hbi is None:
        return None, "host has no blend semantics"
    host_pos = _positions(host_vb)
    host_grid = _build_grid(host_pos)
    split_pos = _positions(split_vb)
    corr = {}
    for vi, p in enumerate(split_pos):
        j = _nearest(p, host_pos, host_grid)
        if j is not None:
            corr[vi] = int(j)
    coverage = len(corr) / max(1, len(split_pos))
    rmap, chk = _vote_vg_remap(out_dir, split_name, host, host_names[0], corr)
    wmask = np.asarray(hbw) > 0
    host_vg_count = int(np.asarray(hbi)[wmask].max()) + 1 if wmask.any() else 0
    chk.update({"covered": len(corr), "split_total": len(split_pos),
                "coverage": round(coverage, 4), "host_vg_count": host_vg_count})
    if rmap is None:
        return None, "split part has no blend semantics"
    if coverage < _HOST_CORR_MIN:
        return None, "split/host vertex correspondence coverage too low (%.1f%%)" % (coverage * 100)
    if chk.get("uncovered_vgs"):
        return None, "%d used VGs of the split part not covered by the vote" % chk["uncovered_vgs"]
    return {
        "host_component_object": host_names[0],
        "host_vg_remap": (None if chk.get("identity")
                          else {str(k): int(v) for k, v in rmap.items()}),
        "host_vg_selfcheck": chk,
    }, None


_MORPH_SAMPLE = 3000   # corresponded vertex pairs per component used for shapekey delta voting
_MORPH_MIN_SIG = 8     # min sampled entries with signal for a key to be votable
_MORPH_RESID_MAX = 0.05


def _read_shapekey_deltas(folder, name):
    """{global shapekey id: (N,3) float32 deltas} from a component vb's inline SHAPEKEY columns.
    The element's semantic index IS the game shapekey id (the importer names keys
    'Deform {index}' from it), so each pipeline's deltas come keyed in its own id space."""
    vb, _, _ = _read_comp(Path(folder), name)
    out = {}
    n = len(vb.data)
    for e in vb.layout.semantics:
        if e.abstract.enum == Semantic.ShapeKey:
            arr = np.asarray(vb.get_field(e.get_name()), dtype=np.float32).reshape(n, -1)
            out[int(e.abstract.index)] = arr[:, :3]
    return out


def _vote_morph_id_map(out_dir, base_comps, ib_dir, fold_entry):
    """Vote a "dungeon shapekey id -> base shapekey id" translation table for a morph-carrying
    foldable IB by comparing both extracts' inline per-key deltas at fold-corresponded vertex
    pairs (pristine vs pristine -- the only moment the comparison is valid; the user edits the
    base afterwards). Per dungeon key, the base key minimizing the scalar-fit residual
    (f*base_delta ~= dungeon_delta) wins; low-signal or ambiguous keys stay unvoted -- the
    export side falls back to pristine reprojection for them, so voting can only improve,
    never regress. The fitted f doubles as the cross-pipeline delta scale (expected ~1).

    Returns (id_map {dungeon_id: base_id} voted keys only, selfcheck)."""
    votes = defaultdict(lambda: defaultdict(int))
    fits = defaultdict(list)
    qual = defaultdict(set)  # per dungeon key: ALL base candidates whose residual qualifies
    keys_seen = set()
    ambiguous = 0
    ib_names = _comp_names(Path(ib_dir))
    for bc_s, info in (fold_entry.get("corr") or {}).items():
        bc = int(bc_s)
        base_sk = _read_shapekey_deltas(out_dir, base_comps[bc])
        ib_sk = _read_shapekey_deltas(ib_dir, ib_names[info["ib_comp"]])
        if not base_sk or not ib_sk:
            continue
        pairs = [(int(bl), int(il)) for bl, il in info["map"].items()]
        if len(pairs) > _MORPH_SAMPLE:
            pairs = pairs[::len(pairs) // _MORPH_SAMPLE]
        bl = np.array([p[0] for p in pairs])
        il = np.array([p[1] for p in pairs])
        base_ids = sorted(base_sk)
        B = np.stack([base_sk[b][bl] for b in base_ids])   # (KB, S, 3)
        for d, dd_full in sorted(ib_sk.items()):
            dd = dd_full[il]                                # (S, 3)
            sig = (np.abs(dd) > 1e-5).any(axis=1)
            keys_seen.add(d)
            if int(sig.sum()) < _MORPH_MIN_SIG:
                continue
            ddm = dd[sig]
            Bm = B[:, sig]
            den = np.maximum((Bm * Bm).sum(axis=(1, 2)), 1e-12)
            f = (Bm * ddm).sum(axis=(1, 2)) / den
            resid = (((f[:, None, None] * Bm - ddm) ** 2).sum(axis=(1, 2))
                     / max(float((ddm ** 2).sum()), 1e-12))
            order = np.argsort(resid)
            best = int(order[0])
            if resid[best] >= _MORPH_RESID_MAX:
                continue
            # Near-duplicate base keys can fit comparably well; picking the best is data-safe
            # (their deltas coincide on the folded vertices), so ambiguity is only tracked --
            # the modal-offset tie-break below settles the EDIT semantics.
            if len(order) > 1 and float(resid[int(order[1])]) < _MORPH_RESID_MAX:
                ambiguous += 1
            qual[d].update(base_ids[int(i)] for i in np.flatnonzero(resid < _MORPH_RESID_MAX))
            votes[d][base_ids[best]] += int(sig.sum())
            fits[d].append(float(f[best]))
    id_map, scales = {}, []
    conflicts = 0
    for d, v in votes.items():
        if len(v) > 1:
            conflicts += 1
        id_map[d] = max(v, key=v.get)
        scales.append(float(np.median(fits[d])))
    # Modal-offset tie-break: twin base keys (identical deltas on the folded vertices, e.g.
    # body-combo expressions whose face share equals a pure-face key) make the residual winner
    # arbitrary -- byte-equal output, but the EDITED key the dungeon follows would be the twin
    # instead of the structurally corresponding one. The dominant id offset across all winners
    # is the structural truth (the scene pipeline renumbers after dropping leading base-only
    # keys); when the offset-matching candidate also qualifies, prefer it.
    tie_broken = 0
    if id_map:
        offset_hist = defaultdict(int)
        for d, b in id_map.items():
            offset_hist[b - d] += 1
        modal_offset = max(offset_hist, key=offset_hist.get)
        for d in sorted(id_map):
            cand = d + modal_offset
            if cand != id_map[d] and cand in qual[d]:
                id_map[d] = cand
                tie_broken += 1
    else:
        modal_offset = None
    chk = {
        "keys_seen": len(keys_seen), "voted": len(id_map),
        "unvoted": len(keys_seen) - len(id_map),
        "unvoted_ids": sorted(keys_seen - set(id_map)),
        "ambiguous": ambiguous, "cross_component_conflicts": conflicts,
        "modal_offset": modal_offset, "tie_broken_to_modal": tie_broken,
        "identity": all(k == v for k, v in id_map.items()) if id_map else None,
        "scale": (round(float(np.median(scales)), 6) if scales else None),
    }
    return id_map, chk


def build_fold_correspondence(out_dir, base_comps, base_pos_c, base_pos_g, base_grid_g, comp_of_g, dspec):
    """For one foldable dungeon IB, build the fold correspondence on the (post-split) merged components. Keyed by
    VertexId = VB row number (proven: import is 1:1, vertex i = extract VB row i; export VertexId = that row number; stable under position editing).

    Must be called after build_cross_scene_merge's split step: the base-side positions/grid are read from out_dir, so
    c5-face no longer contains the bear (5.001), keeping the VG vote pure.

    Returns (written into the routing JSON's scene_ib['fold']):
      comp_map:  {ib_comp_id -> base_comp_id}                    base component each IB component dominantly matches
      corr:      {base_comp_id -> {ib_comp, map:{base_vid->ib_vid}, base_total, covered}}
                 each base component vertex -> nearest IB component vertex (local VB row number). morph dun2body = inverse of this map; used for VG voting.
      vg_remap:  {base_comp_id -> {base_vg->ib_vg}}              only components whose VG is misaligned (e.g. c5-face)
      selfcheck: {base_comp_id -> {matched, ambiguous, collisions, identity, covered, base_total, uncovered}}
    """
    d = Path(dspec["folder"])
    ib_names = _comp_names(d)
    comp_map, corr, vg_remap, selfcheck = {}, {}, {}, {}
    for ic, iname in enumerate(ib_names):
        ivb, _, _ = _read_comp(d, iname)
        ipos = _positions(ivb)
        # 1) base component this IB component dominantly matches (mode over the global base grid)
        hist = defaultdict(int)
        for p in ipos:
            j = _nearest(p, base_pos_g, base_grid_g)
            if j is not None:
                hist[comp_of_g(j)] += 1
        if not hist:
            continue
        bc = max(hist, key=hist.get)
        comp_map[ic] = bc
        # 2) base->IB per-vertex correspondence (each vertex of base component bc -> nearest vertex of IB component ic)
        igrid = _build_grid(ipos)
        bpos = base_pos_c[bc]
        mp = {}
        for bl, p in enumerate(bpos):
            j = _nearest(p, ipos, igrid)
            if j is not None:
                mp[bl] = int(j)
        corr[bc] = {"ib_comp": ic, "map": mp, "base_total": len(bpos), "covered": len(mp)}
        # 3) VG remap voting + selfcheck
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


def _validate_same_character(base, dungeon_specs, editable_ibs):
    """Foolproof: every merged IB must belong to the SAME character = share the base's skeleton constant
    buffer (cb4_hash) and carry vertex-group maps. Refuse to merge a foreign IB (different cb4), which would
    corrupt the unified skeleton. Each IB's own VG total is also bounded to the 512-bone merged-skeleton limit
    (every IB keeps its own skeleton under the per-sub-mod design, so the bound is per-IB, not the grand sum).
    Raises RuntimeError with a user-facing (Chinese) message on any violation."""
    def _meta(folder):
        p = Path(folder) / "Metadata.json"
        return json.loads(p.read_text()) if p.is_file() else {}

    base_meta = _meta(base)
    base_cb4 = base_meta.get("cb4_hash", "")
    if not base_cb4:
        raise RuntimeError("基底 Metadata.json 缺少 cb4_hash，无法校验跨场景合并的同源性。")

    checks = [("基底", str(base), base_meta)]
    for spec in list(dungeon_specs) + list(editable_ibs or []):
        checks.append((spec.get("hash", "?"), spec["folder"], _meta(spec["folder"])))

    for tag, _folder, m in checks:
        comps = m.get("components") or []
        cb4 = m.get("cb4_hash", "")
        if cb4 != base_cb4:
            raise RuntimeError(
                "IB %s 的骨架 cb4=%r 与基底 cb4=%r 不一致——疑似把不属于同一角色的 IB 合并进来，已阻止合并。"
                % (tag, cb4, base_cb4))
        if not comps or any(not c.get("vg_map") for c in comps):
            raise RuntimeError("IB %s 的 Metadata 缺少 vg_map，无法合并为统一骨架（请重新提取）。" % tag)
        ib_vg_total = sum(int(c.get("vg_count", 0)) for c in comps)
        if ib_vg_total > 512:
            raise RuntimeError(
                "IB %s 的统一 VG 总数 %d 超过 WWMI merged skeleton 上限 512。" % (tag, ib_vg_total))


def build_cross_scene_merge(base_folder, dungeon_specs, out_folder, editable_ibs=None):
    """Main entry: write out the merged extract folder + scene_ibs/ + CrossSceneRouting.json + deduplicated textures.
    editable_ibs: list of independently editable extra IBs [{hash, folder, role}] (e.g. form2 face 4e4dc18e) --
    their components are renamed and copied into the merge root after the base (C8-11), can be imported and edited together, and form their own IB group at export.
    Returns a report dict."""
    base = Path(base_folder)
    out = Path(out_folder)
    _validate_same_character(base, dungeon_specs, editable_ibs)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    info, base_comps, base_off = analyze(base, dungeon_specs)

    # 1) Copy all base components' vb/ib/fmt into the merge root (the wrapped one will later be overwritten by the split).
    for name in base_comps:
        for ext in (".fmt", ".vb", ".ib"):
            shutil.copy2(base / f"{name}{ext}", out / f"{name}{ext}")
    if (base / "Metadata.json").exists():
        shutil.copy2(base / "Metadata.json", out / "Metadata.json")
    # The slot-style texture export layer reads ShaderTextureUsage.json from the
    # export source folder; carry the base's copy (incl. extra_forms) along.
    if (base / "ShaderTextureUsage.json").exists():
        shutil.copy2(base / "ShaderTextureUsage.json", out / "ShaderTextureUsage.json")
    base_tex = _copy_textures(base, out)

    # 2) For each dungeon IB "wrapped by a single component": split the parent component into X + X.001.
    splits = []
    split_warnings = []
    for spec, meta in zip(dungeon_specs, info):
        cid = meta["wrapped_in"]
        if cid is None:
            continue
        name = base_comps[cid]
        vb, ib, fmt_text = _read_comp(base, name)
        # Cut by the triangle centroids of the dungeon IB wrapping this component (spec, i.e. the waist bear), no missed cut.
        (rem, bear) = _split_component(vb, ib, fmt_text, spec["folder"])
        if rem is None or bear is None:
            raise RuntimeError(f"split of {name} for IB {spec['hash']} produced empty partition")
        split_name = f"{name}.001"
        _write_comp(out, name, rem[0], rem[1], fmt_text)          # overwrite: parent component remainder
        _write_comp(out, split_name, bear[0], bear[1], fmt_text)  # new: sub-part
        rec = {
            "base_component": cid, "base_object": name, "split_object": split_name,
            "ib_hash": spec["hash"], "split_vertex_count": len(bear[0]),
            "split_tri_count": len(bear[1]), "remainder_vertex_count": len(rem[0]),
        }
        # 2.1) Own-buffer split parts additionally get a host VG translation table so the export
        #      side can derive the host sub-mod from the edited split object (free editing).
        if not meta.get("foldable"):
            fields, why = _build_host_vg_table(out, split_name, bear[0], spec["folder"])
            if fields:
                rec.update(fields)
            else:
                split_warnings.append(
                    "own-buffer 拆件 %s（IB %s）骨级翻译表构建失败（%s）——导出端将回退 legacy 重导入路径，"
                    "对该拆件的编辑不会传播到该场景。" % (split_name, spec["hash"], why))
        splits.append(rec)

    # 3) Copy each dungeon IB's native extract whole into scene_ibs/<hash>/ (for export to reimport per IB as host).
    scene_root = out / "scene_ibs"
    for spec in dungeon_specs:
        d = Path(spec["folder"])
        dst = scene_root / spec["hash"]
        dst.mkdir(parents=True, exist_ok=True)
        for f in d.iterdir():
            if f.is_file():
                shutil.copy2(f, dst / f.name)

    # 3.5) editable_ibs (independently editable extra IBs, e.g. form2 face 4e4dc18e): copy whole into scene_ibs/<hash>/
    #      + rename their Component 0..M and copy into the merge root as Component <next..> (after the base C0..N-1).
    #      .fmt/.vb/.ib as-is (with inline shapekey SHAPEKEY elements, keeping the high stride) -> import carries each one's own shapekeys.
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
        _copy_textures(src, out)  # also gather the extra IB's textures into the merge root (deduplicated)
        editable_ib_records.append({
            "ib_hash": h, "role": eib.get("role", ""), "source_folder": f"scene_ibs/{h}",
            "merged_components": merged_comps, "local_components": list(range(len(local_names))),
            "has_shapekeys": bool(sk.get("offsets_hash")), "offsets_hash": sk.get("offsets_hash", ""),
        })
        next_idx += len(local_names)

    # 3.6) Extend the merged Metadata.json with the editable IBs' component entries so that MERGED import
    #      resolves Components 8..N. blender_import reads extracted_object.components[id].vg_map by component
    #      number; the base Metadata only carries 0..7, so without this a MERGED import raises IndexError on 8+.
    #      Each editable IB is RE-BASED into its own non-overlapping VG range AFTER the body: its vg_offset/vg_map
    #      are shifted by the running unified size, so in the imported unified mesh its VGs read as a distinct
    #      range (e.g. for this asset form2 starts at 355 = max(base vg_map)+1) instead of colliding with the
    #      body's 0.. numbering. dedup against the body is impossible (different scene/pose -> bone matrices
    #      differ), so we append rather than dedup. The editable export later subtracts vg_base_offset to
    #      recover the IB's own 0-based numbering.
    #      COMPONENT import ignores Metadata.components, so this stays additive for COMPONENT.
    if editable_ib_records:
        meta_path = out / "Metadata.json"
        if meta_path.exists():
            merged_meta = json.loads(meta_path.read_text())
            comps = merged_meta.get("components") or []
            # Offset = the base's highest GLOBAL vg id + 1, auto-computed (never hardcoded). Use the max
            # vg_map VALUE (the real bone id), NOT the cumulative vg_offset+vg_count slot total: the latter
            # over-counts because dedup'd components (e.g. C6/C7) reuse lower ids and leave empty trailing slots.
            def _max_vg(cs):
                vs = [int(v) for c in cs for v in (c.get("vg_map") or {}).values()]
                return max(vs) if vs else -1

            running = _max_vg(comps) + 1
            extra = {}
            for eib in (editable_ibs or []):
                em = Path(eib["folder"]) / "Metadata.json"
                src_comps = (json.loads(em.read_text()).get("components") or []) if em.exists() else []
                rec = next((r for r in editable_ib_records if r["ib_hash"] == eib["hash"]), None)
                if rec is None or not src_comps:
                    continue
                rec["vg_base_offset"] = running  # export subtracts this to recover the IB's own 0-based VG
                for li, mi in zip(rec["local_components"], rec["merged_components"]):
                    if li < len(src_comps):
                        c = dict(src_comps[li])
                        c["vg_offset"] = int(c.get("vg_offset", 0)) + running
                        c["vg_map"] = {k: int(v) + running for k, v in (c.get("vg_map") or {}).items()}
                        extra[mi] = c
                running += _max_vg(src_comps) + 1
            for mi in sorted(extra):
                if mi >= len(comps):
                    comps.append(extra[mi])
            merged_meta["components"] = comps
            meta_path.write_text(json.dumps(merged_meta, indent=4, ensure_ascii=False))

    # 3.7) For each foldable dungeon IB, build the fold correspondence on the post-split merged components (base side reads out's c5-face, which no longer contains the bear).
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

    # 3.8) For morph-carrying foldable IBs, vote the shapekey id translation table (dungeon key
    #      id -> base key id) on pristine data. The export side then derives the dungeon morph
    #      from the EDITED shape keys for voted keys; unvoted keys keep pristine reprojection.
    for spec, meta in zip(dungeon_specs, info):
        fd = fold_data.get(spec["hash"])
        if not meta.get("foldable") or not fd:
            continue
        try:
            ib_meta = json.loads((Path(spec["folder"]) / "Metadata.json").read_text())
        except Exception:
            ib_meta = {}
        if not int(((ib_meta.get("shapekeys") or {}).get("shapekey_count")) or 0):
            continue
        id_map, chk = _vote_morph_id_map(out, base_comps, Path(spec["folder"]), fd)
        fd["morph_id_map"] = {str(k): int(v) for k, v in sorted(id_map.items())}
        fd["morph_selfcheck"] = chk

    # 4) Write CrossSceneRouting.json.
    routing = _build_routing(base, base_comps, info, splits, editable_ib_records, fold_data)
    (out / "CrossSceneRouting.json").write_text(json.dumps(routing, indent=2, ensure_ascii=False))

    # 4.5) Merge per-IB "lods" entries into the merged root Metadata.json (mirrors the
    #      automatic vg merge feel; see embedded/lod/cross_scene.py for the chain
    #      pre-composition). Run the per-IB "Extract LOD Data" BEFORE merging so the
    #      scene extracts carry their lods; re-running the merge refreshes the root copy.
    lod_entries_merged = 0
    meta_path = out / "Metadata.json"
    if meta_path.exists():
        merged_meta = json.loads(meta_path.read_text())
        scene_metas = {}
        for spec in list(dungeon_specs) + list(editable_ibs or []):
            scene_meta_path = scene_root / spec["hash"] / "Metadata.json"
            if scene_meta_path.exists():
                scene_metas[spec["hash"]] = json.loads(scene_meta_path.read_text())
        lod_entries_merged = lod_cross_scene.merge_lods_into_base(merged_meta, routing, scene_metas)
        if lod_entries_merged:
            meta_path.write_text(json.dumps(merged_meta, indent=4, ensure_ascii=False))

    # Foolproof: if a fold(dungeon) IB's overlap ratio with the base mesh is too low (barely the same mesh) -> likely
    # an independent morph (e.g. another form's face); mis-setting it as Fold would fold it into the base buffer and break it.
    # Overlap ratio = analyze's matched/total (measured: normal fold parts=1.0, form2≈0.05). Shapekey hashes can't tell them
    # apart (form1/form2 faces share the numbering range); geometric overlap is the criterion.
    warnings = list(split_warnings)
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
        "lod_entries_merged": lod_entries_merged,
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
            # insert the sub-part name right after the parent component
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
        # whether the face has morph: read this IB extract's Metadata.shapekeys.offsets_hash
        morph = None
        d_meta_path = base.parent  # placeholder; real morph info is read by the export side from scene_ibs/<hash>/Metadata.json
        scene_ibs.append({
            "ib_hash": h, "vb0_hash": h, "role": meta["role"],
            "source_folder": f"scene_ibs/{h}",
            # foldable=True -> fold (bind base buffer, consumer M2 goes through fold.py); False -> own-buffer (host-transfer).
            # Additive field: the existing orchestrator doesn't read it, behavior unchanged; the M2 consumer routes on it.
            "foldable": meta.get("foldable"),
            "derive": {
                "method": ("fold" if meta.get("foldable")
                           else ("delta" if meta["role"] == "face" else "absolute")),
                "base_components": meta["base_components"],
                "source_object": (s["split_object"] if s else None),
                "correspondence": "position_grid",
            },
            # fold correspondence (foldable only): comp_map + base->IB per-vertex correspondence + VG remap + selfcheck.
            # the consumer uses it to reproject morph / relabel blend / emit redirect sections; None for non-foldable.
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
