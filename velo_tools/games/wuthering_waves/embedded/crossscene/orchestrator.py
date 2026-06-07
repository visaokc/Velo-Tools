"""Cross-scene export orchestration: derive each dungeon IB from the merged folder + CrossSceneRouting.json, then namespace-merge them into a single mod.

= The game-verified standalone prototype ``xscene_onemesh_build.py`` pipeline ported into velo, driven by JSON:
  1. body: export the (edited) merged base -> showcase shared buffer (copied verbatim to work/body).
  2. Foldable IB (clothing/face etc., fully .fmt-layout compatible): each stock-exports its host -> ``fold.apply_fold`` redirects the geometry
     (+ face morph reprojection / blend remap; clothing etc. without shape keys folds geometry only) **folded in place into work/body**, not made into a separate mod.
  3. Non-foldable IB (the bear, bone count 4!=8): each host-exports its own buffer (host-transfer).
  4. editable IB (form2 face, geometry not part of the base): exported separately as a new component.
  5. assembler namespace-merges [body, own..., editable...] + texture hash dedup + self-check.
``hole=True`` runs the punch-hole test variant (position-deterministic); ``hole=False`` runs real edit-derived output.
Never touch ``_wwmi_core``: every IB exports via stock ``bpy.ops.vtww.export_mod`` against its own source
(so the face's native morph is automatically preserved); we only orchestrate + string-merge on the outside.

Folded parts no longer distinguish clothing/face: the producer auto-determines foldability (``foldable``); foldable ones all go through fold.py
(fold morph along with geometry if shape keys exist, otherwise fold geometry only), non-foldable ones go own-buffer. Each IB uses its vb0 hash as the tag.
"""
import json
import shutil
from pathlib import Path

import bpy
import bmesh


def _dhash(x, y, z):
    h = 2166136261
    for v in (x, y, z):
        iv = (int(round(v * 100)) + 1000000) & 0xffffffff
        h = ((h ^ iv) * 16777619) & 0xffffffff
    return h


def _pos_hole(obj, frac=35):
    """Position-deterministic punch-hole: hash by face centroid position and delete ~frac% of faces (showcase and each host delete the same set of physical faces -> identical across scenes)."""
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    faces = list(bm.faces)
    if len(faces) >= 10:
        fd = [f for f in faces if _dhash(*f.calc_center_median()) % 100 < frac]
        if len(fd) >= len(faces):
            fd = fd[:len(faces) // 2]
        bmesh.ops.delete(bm, geom=fd, context='FACES')
        bmesh.update_edit_mesh(obj.data, destructive=True)
    bpy.ops.object.mode_set(mode='OBJECT')


def _import_one(cfg, src, want_hash):
    before = set(c.name for c in bpy.data.collections)
    cfg.object_source_folder = src
    bpy.ops.vtww.import_object()
    new = set(c.name for c in bpy.data.collections) - before
    return bpy.data.collections.get(want_hash) or (bpy.data.collections[sorted(new)[0]] if new else None)


def _export_col(cfg, col, modout, name, src):
    Path(modout).mkdir(parents=True, exist_ok=True)
    cfg.object_source_folder = src
    cfg.component_collection = col
    cfg.mod_output_folder = modout
    cfg.mod_name = name
    bpy.ops.vtww.export_mod()


def _export_body_with_trimmed_metadata(cfg, body_col, work, merged_folder, keep_count):
    """Body export must see ONLY the body components [0, keep_count). The producer appends the editable
    form2 components (8-11) to Metadata.json for MERGED *import*; if the body export saw them it would emit
    spurious empty Component 8+ sections (COMPONENT) or inflate the unified skeleton vg_count sum
    (MERGED, object_merger.py). Trim Metadata to the body components for the export, restore afterwards.
    The full Metadata (incl. 8-11) is only needed at import time, which has already happened."""
    meta_p = Path(merged_folder) / "Metadata.json"
    meta_full = meta_p.read_text(encoding="utf-8") if meta_p.is_file() else None
    try:
        if meta_full is not None:
            m = json.loads(meta_full)
            comps = m.get("components") or []
            if len(comps) > keep_count:
                m["components"] = comps[:keep_count]
                meta_p.write_text(json.dumps(m, indent=4, ensure_ascii=False), encoding="utf-8")
        _export_col(cfg, body_col, str(work / "sc"), "om_sc", str(merged_folder))
    finally:
        if meta_full is not None:
            meta_p.write_text(meta_full, encoding="utf-8")


def _copy_body(work: Path):
    """body = showcase shared buffer mod: copy the base export (work/sc) verbatim into work/body.
    Each foldable IB then has ``fold.apply_fold`` append a FoldHost (+ morph) section to work/body/mod.ini in place."""
    body = work / "body"
    if body.exists():
        shutil.rmtree(body)
    body.mkdir()
    shutil.copytree(work / "sc" / "Meshes", body / "Meshes")
    shutil.copytree(work / "sc" / "Textures", body / "Textures")
    shutil.copy(work / "sc" / "mod.ini", body / "mod.ini")
    return body


def build_cross_scene_mod(context, cfg, base_collection, merged_folder, out_folder, hole=True, workdir=None):
    """Main entry: derive + merge into a single cross-scene mod. Returns the assembler's self-check report.
    base_collection: the imported (and possibly edited) merged base collection (Component 0-7 + 5.001).
    merged_folder: the merged folder produced by the producer (contains CrossSceneRouting.json + scene_ibs/)."""
    merged_folder = Path(merged_folder)
    routing = json.loads((merged_folder / "CrossSceneRouting.json").read_text(encoding="utf-8"))
    work = Path(workdir) if workdir else (Path(out_folder).parent / "_xscene_work")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    # Foldable IB (clothing/face, fully .fmt-layout compatible) folds into the base buffer (fold.py); non-foldable (the bear, bone count 4!=8) gets its own buffer.
    foldable_ibs = [s for s in routing["scene_ibs"] if s.get("foldable")]
    own_ibs = [s for s in routing["scene_ibs"] if not s.get("foldable")]
    editable_ibs = routing.get("editable_ibs", [])
    # Merged component names of the editable extra IBs (e.g. form2 face) -> must be excluded from the body export
    # (otherwise ObjectMerger treats C8-11 as showcase comp 8-11, base Metadata has no 8-11 -> reports missing Component 8).
    eib_comp_names = {f"Component {mi}" for rec in editable_ibs for mi in rec["merged_components"]}

    saved = (cfg.object_source_folder, getattr(cfg, "component_collection", None),
             cfg.mod_output_folder, cfg.mod_name)
    temp_cols = []
    try:
        # 1) body: (punch-hole) all meshes of the base (including extra IB components, each punched separately); the body export takes only the showcase group
        if hole:
            for o in [o for o in base_collection.objects if o.type == 'MESH']:
                _pos_hole(o)
        if eib_comp_names:
            body_col = bpy.data.collections.new("xs_body")
            bpy.context.scene.collection.children.link(body_col)
            temp_cols.append(body_col)
            for o in base_collection.objects:
                if o.type == 'MESH' and o.name not in eib_comp_names:
                    body_col.objects.link(o)
            body_export_col = body_col
        else:
            body_export_col = base_collection
        _export_body_with_trimmed_metadata(
            cfg, body_export_col, work, merged_folder, routing["base"]["component_count"])

        # body = showcase shared buffer mod (base export copied verbatim into body); all foldable IBs fold into it.
        mods = [str(_copy_body(work))]

        # 2) Non-foldable IB (the bear waist): each host-exports its own buffer (host-transfer)
        for s in own_ibs:
            src = str(merged_folder / s["source_folder"])
            col = _import_one(cfg, src, s["ib_hash"])
            if hole:
                for o in [o for o in col.objects if o.type == 'MESH']:
                    _pos_hole(o)
            tag = s["ib_hash"]
            _export_col(cfg, col, str(work / tag), "om_" + tag, src)
            mods.append(str(work / tag))

        # 3) Foldable IB (clothing/face): export takes its host (face carries morph), then fold.apply_fold redirects the geometry
        #    + (face) morph reprojection + blend remap, all folded into the body buffer mod (modifies work/body in place); not made into a separate mod.
        from . import fold
        # Morph reprojection does position matching against **the edited body itself** (how the green mod did it originally): surviving vertices stay in place and naturally match
        # the dungeon face; morph vid = the real row number of the edited body, naturally aligned with the body -> topology-changing edits like geometry deletion are also correct.
        # (There used to be a morph_ref unedited-reference hack assuming identical topology, where deleting vertices caused row-number misalignment and welding; removed. reproject_morph's
        #   ref defaults to body_meshes, so we don't pass morph_ref. When vertices are moved far from their original position, moved vertices are approximated by nearest-neighbor -- a known limitation.)
        for s in foldable_ibs:
            src = str(merged_folder / s["source_folder"])
            col = _import_one(cfg, src, s["ib_hash"])
            if hole:
                for o in [o for o in col.objects if o.type == 'MESH']:
                    _pos_hole(o)
            tag = s["ib_hash"]
            _export_col(cfg, col, str(work / tag), "om_" + tag, src)
            fold.apply_fold(work, s, tag)

        # 4) editable_ibs (form2 face etc.): copy C8-11 -> temporary Component 0-3 -> export against their own source
        #    (shape keys are per-object and must be exported separately; mesh.copy() carries the form2 shape keys -> export re-emits them automatically).
        eib_roles = []
        for rec in editable_ibs:
            tag = rec["ib_hash"]
            eib_col = bpy.data.collections.new("xs_eib_" + rec["ib_hash"])
            bpy.context.scene.collection.children.link(eib_col)
            temp_cols.append(eib_col)
            temp_objs = []
            for li, mi in zip(rec["local_components"], rec["merged_components"]):
                src_obj = base_collection.objects.get(f"Component {mi}")
                if src_obj is None:
                    continue
                cp = src_obj.copy()
                cp.data = src_obj.data.copy()
                cp.name = f"Component {li}"
                eib_col.objects.link(cp)
                temp_objs.append(cp)
            # MERGED: the editable IB was imported with UNIFIED VG names (vg_base_offset + its own 0-based
            # numbering). Its export runs against the IB's own 0-based source, where object_merger fills VG
            # gaps by name and then drops VGs whose collection index >= the source's total_vg_count; unified
            # names (e.g. 355+) gap-fill to high indices and get dropped (the whole skeleton is lost, Blend
            # collapses to bone 0). Re-base the temp objects' VG names to the IB's own numbering first.
            base_off = int(rec.get("vg_base_offset") or 0)
            if cfg.mod_skeleton_type == 'MERGED' and base_off:
                for cp in temp_objs:
                    for vg in cp.vertex_groups:
                        if vg.name.lstrip("-").isdigit():
                            vg.name = str(int(vg.name) - base_off)
            eib_src = str(merged_folder / rec["source_folder"])
            _export_col(cfg, eib_col, str(work / tag), "om_" + tag, eib_src)
            mods.append(str(work / tag))
            eib_roles.append(tag)
            for cp in temp_objs:
                mesh = cp.data
                bpy.data.objects.remove(cp, do_unlink=True)
                try:
                    bpy.data.meshes.remove(mesh)
                except Exception:
                    pass

        # 5) Namespace merge + texture dedup + self-check
        from . import assembler
        report = assembler.assemble(str(out_folder), mods)
        report["ib_count"] = len(mods)
        report["roles"] = ["body"] + [s["ib_hash"] for s in own_ibs] + eib_roles
        return report
    finally:
        cfg.object_source_folder, cfg.mod_output_folder, cfg.mod_name = saved[0], saved[2], saved[3]
        if saved[1] is not None:
            cfg.component_collection = saved[1]
        for col in temp_cols:
            for o in list(col.objects):
                col.objects.unlink(o)
            try:
                bpy.context.scene.collection.children.unlink(col)
            except Exception:
                pass
            try:
                bpy.data.collections.remove(col)
            except Exception:
                pass
        # Clean up the temporary work directory (intermediate exports sc/body/each host), leaving the mod output folder with only the clean cross_scene_velo.
        shutil.rmtree(work, ignore_errors=True)
