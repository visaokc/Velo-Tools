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
import re
import shutil
from pathlib import Path

import bpy
import bmesh


def _relabel_draw_comments(mod_ini, index_to_label):
    """Rewrite the stock '; Draw Component {N}[.001]' comments of a sub-IB mod.ini to the
    merged/Blender component labels (index_to_label: {export-local component index -> label str}).
    The WWMI template emits '; Draw {obj.name}', so a cross-scene sub-IB export -- whose temp objects
    are renamed to export-local 'Component N' and collide with the body's same-named objects (Blender
    appends '.001') -- otherwise annotates the draw with the export-local number plus a '.001' artifact
    instead of the real component the user edited. Comment-only: functional lines and the export-local
    section indices (required for the draw-range matching) are untouched."""
    p = Path(mod_ini)
    if not p.is_file() or not index_to_label:
        return

    def _sub(m):
        label = index_to_label.get(int(m.group(2)))
        return f"{m.group(1)}Component {label}" if label is not None else m.group(0)

    p.write_text(re.sub(r'(;\s*Draw )Component (\d+)(?:\.\d+)?', _sub,
                        p.read_text(encoding="utf-8")), encoding="utf-8")


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


def _bake_shapekeys(obj):
    """Bake the current shape-key mix into plain mesh coordinates (own-buffer hosts have no
    shape-key pipeline at runtime; the edited part exports whatever mix is currently visible)."""
    sk = obj.data.shape_keys
    if not sk:
        return
    mix = obj.shape_key_add(name="xs_mix", from_mix=True)
    co = [0.0] * (len(obj.data.vertices) * 3)
    mix.data.foreach_get("co", co)
    for kb in list(sk.key_blocks):
        obj.shape_key_remove(kb)
    obj.data.vertices.foreach_set("co", co)
    obj.data.update()


def _translate_unified_to_local(obj, component_vg_map, split_name, tag):
    """MERGED import carries UNIFIED VG names; translate them back to the split's base-component
    local numbering (the domain of host_vg_remap) via the component's vg_map inverse. Reuses the
    per_from_merged applier (two-pass rename + drop out-of-palette). Weighted VGs outside the
    component's palette are cross-component weights -> hard error (per_from_merged semantics)."""
    from ..per_from_merged import _remap_object
    stray = _remap_object(obj, component_vg_map)
    if stray:
        raise RuntimeError(
            "own-buffer 部件 %s（IB %s）的顶点组 %s 权重越界——它们对应的统一骨不在该部件的 vg_map 内"
            "（跨部件权重无法进入该饰品的 host 骨架）。请把这些权重转回本部件的骨，或刷零后再导出。"
            % (split_name, tag, stray))


def _translate_host_vgs(obj, split_rec, tag):
    """Rename the split copy's digit VG names (base-component-local) to the host extract's
    local numbering per the producer's host_vg_remap table; drop weightless out-of-table
    digit VGs; hard-error on weighted VGs outside the host skeleton. Two-pass rename (temp
    prefix) so transient name collisions during the swap are safe (per_from_merged pattern)."""
    from . import vg_translate
    weighted = set()
    for v in obj.data.vertices:
        for g in v.groups:
            if g.weight > 1e-6:
                weighted.add(g.group)
    entries = [(vg.name, vg.index in weighted) for vg in obj.vertex_groups]
    chk = split_rec.get("host_vg_selfcheck") or {}
    remap = split_rec.get("host_vg_remap")
    renames, drops, strays = vg_translate.plan_host_vg_translation(
        entries, remap, chk.get("host_vg_count"))
    if strays:
        usable = (sorted(remap.keys(), key=int) if remap
                  else ["0..%d" % (int(chk.get("host_vg_count") or 1) - 1)])
        raise RuntimeError(
            "own-buffer 部件 %s（IB %s）存在带权重的顶点组 %s 不在 host 骨表内——该饰品的权重必须"
            "刷在 host 既有骨上（本部件可用顶点组：%s）。请把越界权重转移到可用顶点组或刷零后再导出。"
            % (split_rec.get("split_object"), tag, strays, usable))
    by_name = {vg.name: vg for vg in obj.vertex_groups}
    for old in drops:
        obj.vertex_groups.remove(by_name[old])
    for old, new in renames.items():
        by_name[old].name = vg_translate.TMP_PREFIX + new
    for vg in obj.vertex_groups:
        if vg.name.startswith(vg_translate.TMP_PREFIX):
            vg.name = vg.name[len(vg_translate.TMP_PREFIX):]


def _import_one(cfg, src, want_hash):
    before = set(c.name for c in bpy.data.collections)
    cfg.object_source_folder = src
    bpy.ops.vtww.import_object()
    new = [c for c in bpy.data.collections if c.name not in before]
    # Prefer the collection created by THIS import: a previous export in the same session may
    # have left a same-named collection imported under another skeleton mode, whose VG naming
    # would silently corrupt this export (e.g. COMPONENT-local digits read as unified ids).
    for c in sorted(new, key=lambda c: c.name):
        if c.name.startswith(want_hash):
            return c
    if new:
        return sorted(new, key=lambda c: c.name)[0]
    return bpy.data.collections.get(want_hash)


def _purge_collection(col):
    """Fully remove a sub-IB import (objects + meshes + collection) once its export is done --
    keeps the scene clean across repeated exports and makes stale-collection reuse impossible."""
    if col is None:
        return
    for o in list(col.objects):
        data = o.data
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except Exception:
            pass
        try:
            if data is not None:
                bpy.data.meshes.remove(data)
        except Exception:
            pass
    try:
        bpy.data.collections.remove(col)
    except Exception:
        pass


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


def _base_meshes(cfg, collection):
    """Component mesh objects of the base collection, honoring the stock WWMI export settings
    (Ignore Nested Collections / Ignore Hidden Collections / Ignore Hidden Objects) EXACTLY as a
    normal single-IB export does -- it reuses the vendored core's own ``get_collection_objects`` +
    ``object_is_hidden``, so the gathering semantics cannot drift from stock ("checked = really
    ignored").

    The velo "create component sub-collections" import lowers ``ignore_nested_collections`` so the
    C{n} children are still traversed (recursive); a flat import keeps the default, where the
    recursive gather equals ``collection.objects``."""
    # Lazy import: keep this module importable without a full bpy (the vendored core's
    # collections/objects pull in bpy at import time; pure-function unit tests must not need it).
    from ..._wwmi_core.migoto_io.blender_interface.collections import get_collection_objects
    from ..._wwmi_core.migoto_io.blender_interface.objects import object_is_hidden
    objs = get_collection_objects(
        collection,
        recursive=not cfg.ignore_nested_collections,
        skip_hidden_collections=cfg.ignore_hidden_collections)
    return [o for o in objs
            if o.type == 'MESH'
            and not (cfg.ignore_hidden_objects and object_is_hidden(o))]


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
    # Sub-IBs (own_ibs / foldable_ibs) are RE-imported below; they must be imported in the EXPORT
    # skeleton mode, not the user's original import mode. Normally import==export so this is a no-op,
    # but the "Per-Component (from Merged)" path imports MERGED yet exports COMPONENT -- without this
    # the re-imported sub-IBs keep unified VG names and a COMPONENT export drops them (skeleton
    # collapse). Aligning sub-IB import to the export mode is generic (no asset-specific logic).
    saved_import_type = cfg.import_skeleton_type
    cfg.import_skeleton_type = cfg.mod_skeleton_type
    # Cross-scene textures are namespace-merged + deduped into ONE global per-hash override (assembler),
    # which is architecturally incompatible with per-IB slot-style rebinding: slot-style emits per-IB
    # `ResourceTextureN` refs that the assembler's texture-section collapse leaves dangling. The slot
    # hook's own guard only catches the body export (whose source folder has CrossSceneRouting.json); the
    # sub-IB exports run against scene_ibs/<hash> (no json) and would still slot-style. Force hash-style
    # for the WHOLE cross-scene build (body + every sub-IB); restored in finally.
    saved_slot_style = getattr(cfg, "velo_slot_style_textures", None)
    if saved_slot_style:
        cfg.velo_slot_style_textures = False
        print("[velo.xscene] slot-style textures bypassed for cross-scene export "
              "(the namespace merge requires hash-style textures)")
    temp_cols = []
    try:
        # 1) body: gather the base's Component meshes once, honoring the stock collection settings
        #    (Ignore Nested / Hidden Collections / Hidden Objects), exactly like a single-IB export.
        base_meshes = _base_meshes(cfg, base_collection)
        if (not base_meshes and cfg.ignore_nested_collections
                and any(o.type == 'MESH' for o in base_collection.all_objects)):
            raise RuntimeError(
                "跨场景导出：基底集合『%s』的组件网格都在子集合里，但当前勾选了"
                "「忽略嵌套集合」(Ignore Nested Collections)，导致一个组件都取不到。"
                "请取消勾选该选项后重试。" % base_collection.name)
        base_by_name = {o.name: o for o in base_meshes}
        if hole:
            for o in base_meshes:
                _pos_hole(o)
        # Always export the body from a flat temp collection (identical whether or not there are
        # editable IBs to exclude), so the body path no longer depends on ignore_nested_collections.
        body_col = bpy.data.collections.new("xs_body")
        bpy.context.scene.collection.children.link(body_col)
        temp_cols.append(body_col)
        for o in base_meshes:
            if o.name not in eib_comp_names:
                body_col.objects.link(o)
        _export_body_with_trimmed_metadata(
            cfg, body_col, work, merged_folder, routing["base"]["component_count"])

        # body = showcase shared buffer mod (base export copied verbatim into body); all foldable IBs fold into it.
        mods = [str(_copy_body(work))]

        # 2) Non-foldable IB (the bear waist): exports its own buffer.
        #    New path (split part with a producer host VG translation table): export the EDITED
        #    split object from the base collection with its VG names translated to host-local
        #    numbering -- edits propagate (move / delete / full mesh replacement). MERGED exports
        #    take an extra unified->component-local rename first. Falls back to the legacy
        #    pristine reimport when the table is missing (old routing JSON / table build failed /
        #    split object not found) -- edits don't propagate there.
        own_legacy = []
        own_excluded = []
        splits_by_ib = {sp.get("ib_hash"): sp for sp in routing["base"].get("splits", [])}
        for s in own_ibs:
            src = str(merged_folder / s["source_folder"])
            tag = s["ib_hash"]
            sp = splits_by_ib.get(tag)
            split_obj = base_by_name.get(sp["split_object"]) if sp else None
            if sp is not None and split_obj is None \
                    and base_collection.all_objects.get(sp["split_object"]) is not None:
                # The split part IS in the base collection but the stock settings excluded it
                # (hidden / Ignore Hidden Objects) -> honor that: skip the whole own-buffer sub-IB
                # (that accessory just isn't in the mod; the IB is left to the game).
                own_excluded.append("%s (%s)" % (tag, sp["split_object"]))
                print("[velo.xscene] own-buffer IB %s 的拆件 %s 被忽略（隐藏/排除），跳过该子 IB。"
                      % (tag, sp["split_object"]))
                continue
            if sp is not None and "host_vg_remap" in sp and split_obj is not None \
                    and cfg.mod_skeleton_type in ('COMPONENT', 'MERGED'):
                own_col = bpy.data.collections.new("xs_own_" + tag)
                bpy.context.scene.collection.children.link(own_col)
                temp_cols.append(own_col)
                cp = split_obj.copy()
                cp.data = split_obj.data.copy()
                cp.name = sp.get("host_component_object", "Component 0")
                own_col.objects.link(cp)
                try:
                    _bake_shapekeys(cp)
                    if cfg.mod_skeleton_type == 'MERGED':
                        # MERGED import names VGs by UNIFIED ids; bring them back to the base
                        # component's local numbering first (host_vg_remap's domain).
                        meta = json.loads((merged_folder / "Metadata.json").read_text(encoding="utf-8"))
                        comp_meta = (meta.get("components") or [])[sp["base_component"]]
                        _translate_unified_to_local(cp, comp_meta.get("vg_map") or {},
                                                    sp["split_object"], tag)
                    _translate_host_vgs(cp, sp, tag)
                    if hole:
                        _pos_hole(cp)
                    _export_col(cfg, own_col, str(work / tag), "om_" + tag, src)
                    # Annotate the own-buffer draw with the split's real (Blender) name (e.g.
                    # Component 5.001) instead of the export-local 'Component 0.001' artifact.
                    _m_idx = re.search(r'(\d+)', cp.name)
                    if _m_idx:
                        _relabel_draw_comments(
                            work / tag / "mod.ini",
                            {int(_m_idx.group(1)): sp["split_object"].split("Component ", 1)[-1]})
                finally:
                    mesh = cp.data
                    bpy.data.objects.remove(cp, do_unlink=True)
                    try:
                        bpy.data.meshes.remove(mesh)
                    except Exception:
                        pass
            else:
                why = ("路由无骨级翻译表（请重跑合并以生成）" if sp is None or "host_vg_remap" not in sp
                       else ("不支持的导出骨架模式 %s" % cfg.mod_skeleton_type
                             if cfg.mod_skeleton_type not in ('COMPONENT', 'MERGED')
                             else "基底集合中找不到拆件对象 %s" % sp["split_object"]))
                own_legacy.append("%s: %s" % (tag, why))
                print("[velo.xscene] own-buffer IB %s 走 legacy 重导入路径（%s）——对该部件的编辑不会传播。"
                      % (tag, why))
                col = _import_one(cfg, src, tag)
                if hole:
                    for o in [o for o in col.objects if o.type == 'MESH']:
                        _pos_hole(o)
                _export_col(cfg, col, str(work / tag), "om_" + tag, src)
                _purge_collection(col)
            mods.append(str(work / tag))

        # 3) Foldable IB (clothing/face): export takes its host (face carries morph), then fold.apply_fold redirects the geometry
        #    + (face) morph reprojection + blend remap, all folded into the body buffer mod (modifies work/body in place); not made into a separate mod.
        from . import fold
        # Morph reprojection does position matching against **the edited body itself** (how the green mod did it originally): surviving vertices stay in place and naturally match
        # the dungeon face; morph vid = the real row number of the edited body, naturally aligned with the body -> topology-changing edits like geometry deletion are also correct.
        # (There used to be a morph_ref unedited-reference hack assuming identical topology, where deleting vertices caused row-number misalignment and welding; removed. reproject_morph's
        #   ref defaults to body_meshes, so we don't pass morph_ref. When vertices are moved far from their original position, moved vertices are approximated by nearest-neighbor -- a known limitation.)
        fold_skipped = []
        for s in foldable_ibs:
            src = str(merged_folder / s["source_folder"])
            col = _import_one(cfg, src, s["ib_hash"])
            if hole:
                for o in [o for o in col.objects if o.type == 'MESH']:
                    _pos_hole(o)
            tag = s["ib_hash"]
            _export_col(cfg, col, str(work / tag), "om_" + tag, src)
            _purge_collection(col)
            skipped = fold.apply_fold(work, s, tag)
            if skipped:
                fold_skipped.append("%s: base components %s" % (tag, skipped))
                print("[velo.xscene] foldable IB %s：折叠目标组件 %s 被排除，已跳过对应 fold 片。"
                      % (tag, skipped))

        # 4) editable_ibs (form2 face etc.): copy C8-11 -> temporary Component 0-3 -> export against their own source
        #    (shape keys are per-object and must be exported separately; mesh.copy() carries the form2 shape keys -> export re-emits them automatically).
        eib_roles = []
        eib_excluded = []
        for rec in editable_ibs:
            tag = rec["ib_hash"]
            eib_col = bpy.data.collections.new("xs_eib_" + rec["ib_hash"])
            bpy.context.scene.collection.children.link(eib_col)
            temp_cols.append(eib_col)
            temp_objs = []
            for li, mi in zip(rec["local_components"], rec["merged_components"]):
                src_obj = base_by_name.get(f"Component {mi}")
                if src_obj is None:
                    continue
                cp = src_obj.copy()
                cp.data = src_obj.data.copy()
                cp.name = f"Component {li}"
                eib_col.objects.link(cp)
                temp_objs.append(cp)
            if not temp_objs:
                # every component of this editable IB was excluded (hidden / Ignore Hidden Objects)
                # or absent -> skip the whole editable sub-IB (that form just isn't in the mod).
                eib_excluded.append(tag)
                print("[velo.xscene] editable IB %s 的所有组件都被忽略（隐藏/排除/缺失），跳过该子 IB。" % tag)
                continue
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
            # Annotate the editable draws with the merged (Blender) component numbers (e.g. 8-11)
            # instead of the export-local 'Component 0-3.001' artifacts.
            _relabel_draw_comments(
                work / tag / "mod.ini",
                {li: str(mi) for li, mi in zip(rec["local_components"], rec["merged_components"])})
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
        report["slot_style_bypassed"] = bool(saved_slot_style)
        if own_legacy:
            report["own_buffer_legacy"] = own_legacy
        if own_excluded:
            report["own_buffer_excluded"] = own_excluded
        if eib_excluded:
            report["editable_excluded"] = eib_excluded
        if fold_skipped:
            report["fold_skipped"] = fold_skipped
        return report
    finally:
        cfg.object_source_folder, cfg.mod_output_folder, cfg.mod_name = saved[0], saved[2], saved[3]
        cfg.import_skeleton_type = saved_import_type
        if saved_slot_style is not None:
            cfg.velo_slot_style_textures = saved_slot_style
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
