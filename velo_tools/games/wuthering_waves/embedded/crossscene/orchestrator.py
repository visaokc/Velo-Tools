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
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import bpy

from ..slot_textures import constants as slot_constants
from ..slot_textures import stu_metadata
import bmesh


@contextmanager
def _cross_scene_export_guard():
    """Make sub-export operator calls bypass the cross-scene patch.

    UI export already sets this guard before calling the orchestrator. Direct
    headless calls to build_cross_scene_mod need the same protection, otherwise
    sub-exports recurse and namespace an already merged body mod a second time.
    """
    try:
        from . import patch as crossscene_patch
    except Exception:
        package = globals().get("__package__") or __name__.rsplit(".", 1)[0]
        candidates = []
        if package:
            candidates.append(package + ".patch")
        candidates.append(__name__.rsplit(".", 1)[0] + ".patch")
        candidates.append(".patch")
        crossscene_patch = None
        for name in dict.fromkeys(candidates):
            module = sys.modules.get(name)
            if module is not None and hasattr(module, "_IN_XSCENE"):
                crossscene_patch = module
                break
        if crossscene_patch is None:
            yield
            return
    saved = crossscene_patch._IN_XSCENE[0]
    crossscene_patch._IN_XSCENE[0] = True
    try:
        yield
    finally:
        crossscene_patch._IN_XSCENE[0] = saved


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


def _rename_digit_vertex_groups(obj, renames):
    if not renames:
        return
    from . import vg_translate
    by_name = {vg.name: vg for vg in obj.vertex_groups}
    for old, new in renames.items():
        vg = by_name.get(old)
        if vg is not None:
            vg.name = vg_translate.TMP_PREFIX + new
    for vg in obj.vertex_groups:
        if vg.name.startswith(vg_translate.TMP_PREFIX):
            vg.name = vg.name[len(vg_translate.TMP_PREFIX):]


def _weighted_vg_entries(obj):
    weighted = set()
    for v in obj.data.vertices:
        for g in v.groups:
            if g.weight > 1e-6:
                weighted.add(g.group)
    return [(vg.name, vg.index in weighted) for vg in obj.vertex_groups]


def _prepare_own_buffer_vgs(obj, split_rec, component_vg_map, tag):
    from . import vg_translate
    chk = split_rec.get("host_vg_selfcheck") or {}
    renames, skip_host, strays = vg_translate.plan_own_buffer_vg_normalization(
        _weighted_vg_entries(obj),
        component_vg_map,
        split_rec.get("host_vg_remap"),
        chk.get("host_vg_count"))
    if strays:
        raise RuntimeError(
            "own-buffer 部件 %s（IB %s）的顶点组 %s 权重越界——它们既不属于 host 骨表，"
            "也无法通过该部件的 vg_map 翻译成本部件骨。请把这些权重转回本部件的骨，或刷零后再导出。"
            % (split_rec.get("split_object"), tag, strays))
    _rename_digit_vertex_groups(obj, renames)
    if not skip_host:
        _translate_host_vgs(obj, split_rec, tag)


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


_KEEP = object()  # _export_col sentinel: leave the slot-style eligibility override untouched.


def _component_id(name):
    m = re.match(r'Component (\d+)', str(name))
    return int(m.group(1)) if m else None


def _iter_usage_hashes(components):
    """Hashes referenced by a ShaderTextureUsage component map."""
    for _comp, comp_pairs in (components or {}).items():
        if not isinstance(comp_pairs, dict):
            continue
        for _vs, ps_map in comp_pairs.items():
            if not isinstance(ps_map, dict):
                continue
            for _ps, pair_map in ps_map.items():
                if not isinstance(pair_map, dict):
                    continue
                for _slot, rec in pair_map.items():
                    if isinstance(rec, dict):
                        h = rec.get("hash")
                    else:
                        h = rec
                    if h:
                        yield str(h).lower()


def _merge_component_usage(dst, src):
    """Merge STU component maps (Component -> vs -> ps -> slot)."""
    for comp_name, comp_pairs in (src or {}).items():
        comp_dst = dst.setdefault(comp_name, {})
        if not isinstance(comp_pairs, dict) or not isinstance(comp_dst, dict):
            dst[comp_name] = comp_pairs
            continue
        for vs_key, ps_map in comp_pairs.items():
            vs_dst = comp_dst.setdefault(vs_key, {})
            if not isinstance(ps_map, dict) or not isinstance(vs_dst, dict):
                comp_dst[vs_key] = ps_map
                continue
            for ps_key, slot_map in ps_map.items():
                ps_dst = vs_dst.setdefault(ps_key, {})
                if isinstance(slot_map, dict) and isinstance(ps_dst, dict):
                    ps_dst.update(slot_map)
                else:
                    vs_dst[ps_key] = slot_map


def _remap_stu_components(components, comp_map, keep_count, source_label=None):
    """Remap a scene-IB-local STU component map to merged base component ids."""
    out = {}
    sources = {}
    comp_map = {int(k): int(v) for k, v in (comp_map or {}).items()}
    for comp_name, comp_pairs in (components or {}).items():
        local = _component_id(comp_name)
        if local is None or local not in comp_map:
            continue
        base = comp_map[local]
        if base < 0 or base >= keep_count:
            continue
        _merge_component_usage(out, {f"Component {base}": comp_pairs})
        if source_label:
            sources.setdefault(f"Component {base}", []).append(
                f"merged Component {base} <- {source_label} local Component {local}")
    return out, sources


def _merge_local_component_sources(target, sources):
    if not sources:
        return
    for comp_name, values in sources.items():
        block = target.get(comp_name)
        if not isinstance(block, dict):
            continue
        bucket = block.setdefault(slot_constants.COMPONENT_SOURCES_KEY, [])
        if isinstance(bucket, str):
            bucket = [bucket]
            block[slot_constants.COMPONENT_SOURCES_KEY] = bucket
        for value in values:
            if value not in bucket:
                bucket.append(value)


def _merge_form_component_modes(target, modes):
    if not modes:
        return
    for comp_name, mode in modes.items():
        block = target.get(comp_name)
        if isinstance(block, dict):
            block[stu_metadata.constants.FORM_COMPONENT_MODE_KEY] = (
                "multi" if str(mode).lower() == "multi" else "single")


def _body_stu_for_export(root_stu, merged_folder, routing, keep_count):
    """Body export STU: base components plus foldable scene-IB form variants remapped to base ids."""
    trimmed = {}
    for k, v in (root_stu or {}).items():
        cid = _component_id(k)
        if cid is not None:
            if cid < keep_count:
                trimmed[k] = v
            continue
        if k != slot_constants.EXTRA_FORMS_KEY:
            trimmed[k] = v

    extra_by_label = {}
    for entry in stu_metadata.form_entries(root_stu or {}):
        if not isinstance(entry, dict):
            continue
        label = entry.get("label") or entry.get("source") or f"form{len(extra_by_label) + 2}"
        target = extra_by_label.setdefault(label, dict(entry, components={}))
        _merge_component_usage(target["components"], {
            k: v for k, v in (entry.get("components") or {}).items()
            if (_component_id(k) is not None and _component_id(k) < keep_count)
        })

    for scene in routing.get("scene_ibs") or []:
        fold = scene.get("fold") or {}
        if not scene.get("foldable") or not fold.get("comp_map"):
            continue
        stu_path = Path(merged_folder) / scene.get("source_folder", "") / "ShaderTextureUsage.json"
        if not stu_path.is_file():
            continue
        try:
            scene_stu = json.loads(stu_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for entry in stu_metadata.form_entries(scene_stu):
            if not isinstance(entry, dict):
                continue
            source_label = (scene.get("tag") or scene.get("source_folder")
                            or scene.get("ib_hash") or "?")
            remapped, remap_sources = _remap_stu_components(
                entry.get("components") or {}, fold.get("comp_map") or {},
                keep_count, source_label=source_label)
            if not remapped:
                continue
            label = entry.get("label") or entry.get("source") or f"form{len(extra_by_label) + 2}"
            target = extra_by_label.setdefault(label, dict(entry, label=label, components={}))
            _merge_component_usage(target["components"], remapped)
            _merge_local_component_sources(target, remap_sources)
            _merge_form_component_modes(
                trimmed,
                {comp_name: "multi" for comp_name in remapped})

    if extra_by_label:
        trimmed[slot_constants.EXTRA_FORMS_KEY] = list(extra_by_label.values())
    if isinstance(trimmed, dict):
        stu_metadata.sync_form_component_modes(trimmed)
        stu_metadata.sync_form_anchors_field(trimmed)
        try:
            from ..slot_textures import form_merge as slot_form_merge
        except ImportError:
            pass
        else:
            slot_form_merge.refresh_local_discriminator_audit_in_usage(trimmed)
    return trimmed


def _fold_redundant_hashes(merged_folder, routing, keep_count, eligible):
    """Fold-local texture hashes whose draws are replayed through eligible base slot maps."""
    if eligible is not None and not eligible:
        return set()
    root_path = Path(merged_folder) / "ShaderTextureUsage.json"
    root_hashes = set()
    if root_path.is_file():
        try:
            root_stu = json.loads(root_path.read_text(encoding="utf-8"))
            root_hashes = set(_iter_usage_hashes({
                k: v for k, v in root_stu.items()
                if (_component_id(k) is not None and _component_id(k) < keep_count)
            }))
        except Exception:
            root_hashes = set()
    redundant = set()
    for scene in routing.get("scene_ibs") or []:
        fold = scene.get("fold") or {}
        if not scene.get("foldable") or not fold.get("comp_map"):
            continue
        comp_map = {int(k): int(v) for k, v in (fold.get("comp_map") or {}).items()}
        stu_path = Path(merged_folder) / scene.get("source_folder", "") / "ShaderTextureUsage.json"
        if not stu_path.is_file():
            continue
        try:
            scene_stu = json.loads(stu_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        component_sets = [scene_stu]
        component_sets.extend((entry.get("components") or {}) for entry in stu_metadata.form_entries(scene_stu)
                              if isinstance(entry, dict))
        for components in component_sets:
            for comp_name, comp_pairs in (components or {}).items():
                local = _component_id(comp_name)
                if local is None or local not in comp_map:
                    continue
                base = comp_map[local]
                if base < 0 or base >= keep_count:
                    continue
                if eligible is not None and base not in eligible:
                    continue
                redundant.update(_iter_usage_hashes({comp_name: comp_pairs}))
    return redundant - root_hashes


def _root_non_body_hashes(merged_folder, keep_count):
    """Root DDS hashes that belong only to merged non-body components (editable IBs, etc.)."""
    root_path = Path(merged_folder) / "ShaderTextureUsage.json"
    if not root_path.is_file():
        return set()
    try:
        root_stu = json.loads(root_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    body_hashes = set()
    non_body_hashes = set()
    component_sets = [root_stu]
    component_sets.extend((entry.get("components") or {}) for entry in stu_metadata.form_entries(root_stu)
                          if isinstance(entry, dict))
    for components in component_sets:
        for comp_name, comp_pairs in (components or {}).items():
            cid = _component_id(comp_name)
            if cid is None:
                continue
            hashes = set(_iter_usage_hashes({comp_name: comp_pairs}))
            if cid < keep_count:
                body_hashes.update(hashes)
            else:
                non_body_hashes.update(hashes)
    return non_body_hashes - body_hashes


def _add_suppression_reason(reasons, tex_hash, reason):
    current = reasons.get(tex_hash)
    if not current:
        reasons[tex_hash] = reason
        return
    parts = set(current.split("+"))
    parts.add(reason)
    reasons[tex_hash] = "+".join(sorted(parts))


def _body_hash_suppressions(merged_folder, routing, keep_count, eligible):
    reasons = {}
    for h in _fold_redundant_hashes(merged_folder, routing, keep_count, eligible):
        _add_suppression_reason(reasons, h, "fold-local")
    for h in _root_non_body_hashes(merged_folder, keep_count):
        _add_suppression_reason(reasons, h, "non-body-root")
    return reasons


def _add_component_map_entry(mapping, vb0_hash, components):
    if not vb0_hash:
        return
    comps = {int(c) for c in (components or []) if c is not None}
    if not comps:
        return
    key = str(vb0_hash).lower()
    mapping.setdefault(key, set()).update(comps)


def _raw_audit_component_map(routing, keep_count):
    """Map raw dump vb0 hashes to merged component ids for FrameAnalysis replay."""
    mapping = {}
    base_vb0 = (routing.get("base") or {}).get("vb0_hash")
    _add_component_map_entry(mapping, base_vb0, range(int(keep_count or 0)))
    for split in (routing.get("base") or {}).get("splits") or []:
        _add_component_map_entry(
            mapping, split.get("vb0_hash") or split.get("ib_hash"),
            [split.get("base_component")])
    for scene in routing.get("scene_ibs") or []:
        components = (scene.get("derive") or {}).get("base_components")
        if not components:
            comp_map = (scene.get("fold") or {}).get("comp_map") or {}
            components = list(comp_map.values())
        _add_component_map_entry(
            mapping, scene.get("vb0_hash") or scene.get("ib_hash"),
            components)
    for rec in routing.get("editable_ibs") or []:
        _add_component_map_entry(
            mapping, rec.get("vb0_hash") or rec.get("ib_hash"),
            rec.get("merged_components"))
    return mapping


def _slot_audit_dump_folder(context):
    try:
        value = context.scene.vtww_slot_settings.slot_audit_dump_folder
    except Exception:
        return ""
    return str(value or "").strip()


def _run_slot_raw_audit(context, merged_folder, routing, keep_count,
                        target_components=None, audit_module=None):
    dump_folder = _slot_audit_dump_folder(context)
    if not dump_folder:
        return []
    stu_path = Path(merged_folder) / "ShaderTextureUsage.json"
    component_map = _raw_audit_component_map(routing, keep_count)
    if audit_module is None:
        from ..slot_textures import raw_replay_audit as audit_module
    errors = audit_module.audit_raw_pass_coverage(
        dump_folder, stu_path, component_map,
        target_components=target_components, require_fresh=True)
    if errors:
        preview = "\n".join("  - " + err for err in errors[:20])
        more = "" if len(errors) <= 20 else "\n  ... %d more" % (len(errors) - 20)
        raise RuntimeError(
            "跨场景 slot-style raw dump 审计失败：当前 ShaderTextureUsage.json "
            "没有覆盖真实 FrameAnalysis 中 fresh 绑定过的 pass。\n%s%s"
            % (preview, more))
    return []


def _graft_slot_raw_passes(context, source_folder, audit_module=None):
    dump_folder = _slot_audit_dump_folder(context)
    if not dump_folder:
        return None
    source_folder = Path(source_folder)
    stu_path = source_folder / "ShaderTextureUsage.json"
    meta_path = source_folder / "Metadata.json"
    if audit_module is None:
        from ..slot_textures import raw_replay_audit as audit_module
    result = audit_module.graft_raw_passes_into_file(
        dump_folder, stu_path, meta_path, source_folder=source_folder)
    errors = audit_module.audit_local_raw_pass_coverage(
        dump_folder, stu_path, meta_path,
        source_folder=source_folder, require_fresh=True)
    if errors:
        preview = "\n".join("  - " + err for err in errors[:20])
        more = "" if len(errors) <= 20 else "\n  ... %d more" % (len(errors) - 20)
        raise RuntimeError(
            "跨场景 slot-style raw dump 审计失败：%s 的 ShaderTextureUsage.json "
            "在 raw graft 后仍没有覆盖真实 FrameAnalysis 中 fresh 绑定过的 pass。\n%s%s"
            % (source_folder, preview, more))
    if result.rows_added:
        print("[velo.xscene] raw slot graft %s: added %d pass row(s), mapped %d/%d draw(s)."
              % (source_folder.name, result.rows_added,
                 result.draws_mapped, result.draws_seen))
    return result


def _slot_bound_hashes_from_ini(mod_ini):
    path = Path(mod_ini)
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8")
    slot_resources = set(re.findall(
        r'\bps-t\d+\s*=\s*ref\s+(ResourceTexture\d+(?:_ib\d+)*)\b',
        text))
    if not slot_resources:
        return set()
    resource_hashes = {}
    current = None
    for line in text.splitlines():
        m = re.match(r'^\[([^\]]+)\]\s*$', line)
        if m:
            current = m.group(1)
            continue
        if current not in slot_resources:
            continue
        fm = re.match(r'\s*filename\s*=\s*(.+\.dds)\s*$', line, re.I)
        if not fm:
            continue
        hm = re.search(r't=([0-9a-fA-F]+)', fm.group(1))
        if hm:
            resource_hashes[current] = hm.group(1).lower()
    return {resource_hashes[name] for name in slot_resources if name in resource_hashes}


def _prune_unrepresented_fold_suppressions(body_mod_ini, suppressions):
    """Keep fold-local hash fallback unless body slot resources actually represent the hash."""
    if not suppressions:
        return suppressions
    slot_hashes = _slot_bound_hashes_from_ini(body_mod_ini)
    pruned = {}
    for tex_hash, reason in suppressions.items():
        parts = {p for p in str(reason or "").split("+") if p}
        if "fold-local" in parts and tex_hash not in slot_hashes:
            parts.remove("fold-local")
        if parts:
            pruned[tex_hash] = "+".join(sorted(parts))
    return pruned


def _export_col(cfg, col, modout, name, src, eligible=_KEEP, slot_style=_KEEP,
                raw_graft_context=None):
    Path(modout).mkdir(parents=True, exist_ok=True)
    cfg.object_source_folder = src
    cfg.component_collection = col
    cfg.mod_output_folder = modout
    cfg.mod_name = name
    saved_slot_style = getattr(cfg, "velo_slot_style_textures", None)
    stu_p = Path(src) / "ShaderTextureUsage.json"
    stu_full = stu_p.read_text(encoding="utf-8") if stu_p.is_file() else None
    if slot_style is not _KEEP and saved_slot_style is not None:
        cfg.velo_slot_style_textures = bool(slot_style)
    try:
        if raw_graft_context is not None:
            _graft_slot_raw_passes(raw_graft_context, src)
        if eligible is _KEEP:
            bpy.ops.vtww.export_mod()
            return
        # Cross-scene: this sub-export's components use a DIFFERENT local numbering than the user's
        # merged-numbered slot rules, so inject the translated per-component eligibility for the slot
        # hook (set of eligible LOCAL component ids, or None = all eligible). Cleared right after so it
        # never leaks to the next sub-export.
        from ..slot_textures import hook as slot_hook
        slot_hook.set_eligible_override(eligible)
        try:
            bpy.ops.vtww.export_mod()
        finally:
            slot_hook.clear_eligible_override()
    finally:
        if stu_full is not None:
            stu_p.write_text(stu_full, encoding="utf-8")
        if slot_style is not _KEEP and saved_slot_style is not None:
            cfg.velo_slot_style_textures = saved_slot_style


def _export_body_with_trimmed_metadata(cfg, body_col, work, merged_folder, keep_count,
                                       routing, eligible=_KEEP,
                                       raw_graft_context=None):
    """Body export must see ONLY the body components [0, keep_count). The producer appends the editable
    form2 components (8-11) to Metadata.json AND ShaderTextureUsage.json for MERGED *import*; if the body
    export saw them it would emit spurious empty Component 8+ sections (COMPONENT) / inflate the unified
    skeleton vg_count (MERGED, object_merger.py), and -- under slot-style -- the slot generator would hit
    components carrying textures but no draw range and degrade the WHOLE body slot layer (it raises on an
    unranged used component). Trim BOTH inputs to the body components for the export, restore afterwards.
    The full files (incl. 8-11) are only needed at import time, which has already happened."""
    meta_p = Path(merged_folder) / "Metadata.json"
    stu_p = Path(merged_folder) / "ShaderTextureUsage.json"
    meta_full = meta_p.read_text(encoding="utf-8") if meta_p.is_file() else None
    stu_full = stu_p.read_text(encoding="utf-8") if stu_p.is_file() else None
    try:
        if meta_full is not None:
            m = json.loads(meta_full)
            comps = m.get("components") or []
            if len(comps) > keep_count:
                m["components"] = comps[:keep_count]
                meta_p.write_text(json.dumps(m, indent=4, ensure_ascii=False), encoding="utf-8")
        if stu_full is not None:
            s = json.loads(stu_full)
            trimmed = _body_stu_for_export(s, merged_folder, routing, keep_count)
            if trimmed != s:
                stu_metadata.write_usage(stu_p, trimmed)
        _export_col(cfg, body_col, str(work / "sc"), "om_sc", str(merged_folder),
                    eligible=eligible, raw_graft_context=raw_graft_context)
    finally:
        if meta_full is not None:
            meta_p.write_text(meta_full, encoding="utf-8")
        if stu_full is not None:
            stu_p.write_text(stu_full, encoding="utf-8")


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


def _component_index_count(component):
    return int(component.get("index_count") or component.get("indexCount") or 0)


def _write_empty_skip_mod(modout, tag, src, label):
    """Create a minimal sub-mod that matches a hidden/excluded own-buffer IB and draws nothing."""
    modout = Path(modout)
    if modout.exists():
        shutil.rmtree(modout)
    (modout / "Meshes").mkdir(parents=True, exist_ok=True)
    (modout / "Textures").mkdir(parents=True, exist_ok=True)
    meta_path = Path(src) / "Metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    source_hash = meta.get("vb0_hash") or tag
    lines = [
        "; Cross-scene empty own-buffer stub for hidden/excluded source object %s" % label,
        "",
        "[Constants]",
        "global $mod_enabled = 1",
        "global $object_detected = 0",
        "",
    ]
    first_index = 0
    for cid, component in enumerate(meta.get("components") or [{"index_count": 0}]):
        index_count = _component_index_count(component)
        if index_count > 0:
            lines += [
                "[TextureOverrideComponent%d]" % cid,
                "hash = %s" % source_hash,
                "match_first_index = %d" % first_index,
                "match_index_count = %d" % index_count,
                "$object_detected = 1",
                "if $mod_enabled",
                "    handling = skip",
                "    ; Draw skipped: hidden/excluded source object %s" % label,
                "endif",
                "",
            ]
        for lod_index, lod in enumerate(component.get("lods") or [], start=1):
            lod_hash = lod.get("vb0_hash") or lod.get("lod_object_name")
            lod_count = int(lod.get("index_count") or 0)
            if not lod_hash or lod_count <= 0:
                continue
            lines += [
                "[TextureOverrideComponent%dLOD%d]" % (cid, lod_index),
                "hash = %s" % lod_hash,
                "match_first_index = %d" % int(lod.get("index_offset") or 0),
                "match_index_count = %d" % lod_count,
                "$object_detected = 1",
                "if $mod_enabled",
                "    handling = skip",
                "    ; Draw skipped: hidden/excluded source object %s" % label,
                "endif",
                "",
            ]
        first_index += index_count
    (modout / "mod.ini").write_text("\n".join(lines), encoding="utf-8")


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


def build_cross_scene_mod(context, cfg, base_collection, merged_folder, out_folder,
                          hole=True, workdir=None, hole_frac=35):
    """Main entry: derive + merge into a single cross-scene mod. Returns the assembler's self-check report.
    base_collection: the imported (and possibly edited) merged base collection (Component 0-7 + 5.001).
    merged_folder: the merged folder produced by the producer (contains CrossSceneRouting.json + scene_ibs/)."""
    merged_folder = Path(merged_folder)
    routing = json.loads((merged_folder / "CrossSceneRouting.json").read_text(encoding="utf-8"))
    from ..slot_textures import hook as slot_hook
    # Per-component slot eligibility is chosen by the user in MERGED component numbers (the merged root
    # STU lists Component 0..N); each sub-export below translates it to its own LOCAL numbering (None =
    # all eligible). Cross-scene now KEEPS slot-style: textures bind per-IB by ps-t slot (the assembler
    # keeps per-IB slot resources; fold replays the base maps onto the dungeon draws).
    merged_eligible = slot_hook.read_global_eligible(context)
    # Transient per-IB sub-exports live here, deleted in finally. Use a real temp dir (not a sibling
    # of the output folder) so flattening the output to mod_output_folder doesn't leave a _xscene_work
    # next to the user's other mods.
    if workdir:
        work = Path(workdir)
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
    else:
        work = Path(tempfile.mkdtemp(prefix="velo_xscene_work_"))

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
    export_skeleton_type = ('COMPONENT' if cfg.mod_skeleton_type == 'COMPONENT_FROM_MERGED'
                            else cfg.mod_skeleton_type)
    saved_import_type = cfg.import_skeleton_type
    cfg.import_skeleton_type = export_skeleton_type
    # Cross-scene now supports slot-style textures: each independently-exported IB (body / own-buffer /
    # editable) emits its own slot layer (the assembler keeps per-IB slot resources), and the fold
    # replays the base component maps onto the dungeon draws (fold.py). Just record the setting for the
    # report -- no global force-off.
    slot_style_on = bool(getattr(cfg, "velo_slot_style_textures", False))
    # The build runs N stock sub-exports into `work` and the assembler reads each one's mod.ini +
    # Meshes + Textures to merge them -- so every sub-export MUST write ini + textures + buffers
    # regardless of the user's file-output toggles (write_ini=off / partial_export=on /
    # custom_template_live_update=on would leave the assembler with no mod.ini -> FileNotFoundError).
    # Force the safe values for the whole build; the user's ORIGINAL flags are applied to the FINAL
    # assembled mod instead (the assembler honors the same stock gating on its output). Restored in finally.
    saved_gating = {
        "write_ini": cfg.write_ini,
        "partial_export": cfg.partial_export,
        "copy_textures": getattr(cfg, "copy_textures", True),
        "custom_template_live_update": getattr(cfg, "custom_template_live_update", False),
    }
    if saved_gating["custom_template_live_update"]:
        print("[velo.xscene] custom_template_live_update (a live single-IB mode) has no batch-merge "
              "output meaning -- disabled for the cross-scene build.")
    cfg.write_ini = True
    cfg.partial_export = False
    if hasattr(cfg, "copy_textures"):
        cfg.copy_textures = True
    if hasattr(cfg, "custom_template_live_update"):
        cfg.custom_template_live_update = False
    temp_cols = []
    try:
        with _cross_scene_export_guard():
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
                    _pos_hole(o, frac=hole_frac)
            # Always export the body from a flat temp collection (identical whether or not there are
            # editable IBs to exclude), so the body path no longer depends on ignore_nested_collections.
            body_col = bpy.data.collections.new("xs_body")
            bpy.context.scene.collection.children.link(body_col)
            temp_cols.append(body_col)
            for o in base_meshes:
                if o.name not in eib_comp_names:
                    body_col.objects.link(o)
            keep_count = routing["base"]["component_count"]
            body_elig = (None if merged_eligible is None
                         else {c for c in merged_eligible if c < keep_count})
            body_hash_suppressions = (_body_hash_suppressions(merged_folder, routing, keep_count, body_elig)
                                      if slot_style_on else {})
            _export_body_with_trimmed_metadata(
                cfg, body_col, work, merged_folder, keep_count, routing,
                eligible=body_elig,
                raw_graft_context=(context if slot_style_on else None))
            body_hash_suppressions = _prune_unrepresented_fold_suppressions(
                work / "sc" / "mod.ini", body_hash_suppressions)

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
            own_excluded_tags = {}
            splits_by_ib = {sp.get("ib_hash"): sp for sp in routing["base"].get("splits", [])}
            source_mesh_names = {o.name for o in base_collection.all_objects if o.type == 'MESH'}
            try:
                from .. import per_from_merged
                source_mesh_names.update(per_from_merged.current_excluded_object_names())
            except Exception:
                pass
            fold_draw_excludes = {}
            for sp in routing["base"].get("splits", []):
                try:
                    bc = int(sp.get("base_component"))
                except Exception:
                    continue
                split_name = sp.get("split_object")
                if split_name:
                    fold_draw_excludes.setdefault(bc, set()).add(split_name)
            for s in own_ibs:
                src = str(merged_folder / s["source_folder"])
                tag = s["ib_hash"]
                # Slot eligibility: the own-buffer host renumbers to its local components, but it is the
                # split of base component(s) derive.base_components -> slot iff any of those is checked
                # (None = all eligible; empty set = all hash).
                own_base = (s.get("derive") or {}).get("base_components") or []
                own_elig = (None if (merged_eligible is None or not own_base
                                     or any(b in merged_eligible for b in own_base)) else set())
                sp = splits_by_ib.get(tag)
                split_obj = base_by_name.get(sp["split_object"]) if sp else None
                split_name = sp.get("split_object") if sp else None
                if sp is not None and split_obj is None and split_name in source_mesh_names:
                    # The split part IS in the base collection but the stock settings excluded it
                    # (hidden / Ignore Hidden Objects / PFM temp filtering) -> still match the IB but draw
                    # nothing, so excluded means empty draw rather than falling back to the game's original.
                    own_excluded.append("%s (%s)" % (tag, sp["split_object"]))
                    own_excluded_tags[tag] = sp["split_object"]
                    print("[velo.xscene] own-buffer IB %s 的拆件 %s 被忽略（隐藏/排除），生成空 skip 子 IB。"
                          % (tag, sp["split_object"]))
                    _write_empty_skip_mod(work / tag, tag, src, sp["split_object"])
                elif sp is not None and "host_vg_remap" in sp and split_obj is not None \
                        and export_skeleton_type in ('COMPONENT', 'MERGED'):
                    own_col = bpy.data.collections.new("xs_own_" + tag)
                    bpy.context.scene.collection.children.link(own_col)
                    temp_cols.append(own_col)
                    cp = split_obj.copy()
                    cp.data = split_obj.data.copy()
                    cp.name = sp.get("host_component_object", "Component 0")
                    own_col.objects.link(cp)
                    try:
                        _bake_shapekeys(cp)
                        # Some saved files or headless scripts can disagree about the UI import
                        # skeleton mode even though the split copy still carries MERGED unified
                        # VG names. Decide from the weighted digit VGs themselves, not from UI state.
                        meta = json.loads((merged_folder / "Metadata.json").read_text(encoding="utf-8"))
                        comp_meta = (meta.get("components") or [])[sp["base_component"]]
                        _prepare_own_buffer_vgs(cp, sp, comp_meta.get("vg_map") or {}, tag)
                        if hole:
                            _pos_hole(cp, frac=hole_frac)
                        _export_col(
                            cfg, own_col, str(work / tag), "om_" + tag, src,
                            eligible=own_elig,
                            raw_graft_context=(context if slot_style_on else None))
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
                                 if export_skeleton_type not in ('COMPONENT', 'MERGED')
                                 else "基底集合中找不到拆件对象 %s" % sp["split_object"]))
                    own_legacy.append("%s: %s" % (tag, why))
                    print("[velo.xscene] own-buffer IB %s 走 legacy 重导入路径（%s）——对该部件的编辑不会传播。"
                          % (tag, why))
                    col = _import_one(cfg, src, tag)
                    if hole:
                        for o in [o for o in col.objects if o.type == 'MESH']:
                            _pos_hole(o, frac=hole_frac)
                    _export_col(
                        cfg, col, str(work / tag), "om_" + tag, src,
                        eligible=own_elig,
                        raw_graft_context=(context if slot_style_on else None))
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
                        _pos_hole(o, frac=hole_frac)
                tag = s["ib_hash"]
                # Foldable host: its slot layer is discarded (fold replays the BASE maps onto the dungeon
                # draw), so keep this intermediate export hash-style. This avoids requiring form anchors
                # for a temporary ini whose texture layer never reaches the final mod.
                _export_col(cfg, col, str(work / tag), "om_" + tag, src,
                            eligible=None, slot_style=False)
                _purge_collection(col)
                skipped = fold.apply_fold(work, s, tag, draw_excludes=fold_draw_excludes)
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
                if export_skeleton_type == 'MERGED' and base_off:
                    for cp in temp_objs:
                        for vg in cp.vertex_groups:
                            if vg.name.lstrip("-").isdigit():
                                vg.name = str(int(vg.name) - base_off)
                if hole:
                    for cp in temp_objs:
                        _pos_hole(cp, frac=hole_frac)
                eib_src = str(merged_folder / rec["source_folder"])
                eib_elig = (None if merged_eligible is None
                            else {li for li, mi in zip(rec["local_components"], rec["merged_components"])
                                  if mi in merged_eligible})
                _export_col(
                    cfg, eib_col, str(work / tag), "om_" + tag, eib_src,
                    eligible=eib_elig,
                    raw_graft_context=(context if slot_style_on else None))
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

            # 5) Namespace merge + texture dedup + self-check.
            # The merged root is the single authoritative texture allowlist: only hashes still present
            # at merged_folder root ship (sub-IB scene_ibs/<hash>/ no longer re-supply a pruned hash).
            from . import assembler
            report = assembler.assemble(
                str(out_folder), mods, texture_root=str(merged_folder),
                write_ini=saved_gating["write_ini"],
                partial_export=saved_gating["partial_export"],
                copy_textures=saved_gating["copy_textures"],
                suppress_body_hashes=body_hash_suppressions)
            report["ib_count"] = len(mods)
            report["roles"] = ["body"] + [s["ib_hash"] for s in own_ibs] + eib_roles
            report["slot_style"] = slot_style_on
            if own_legacy:
                report["own_buffer_legacy"] = own_legacy
            if own_excluded:
                report["own_buffer_excluded"] = own_excluded
            if eib_excluded:
                report["editable_excluded"] = eib_excluded
            if fold_skipped:
                report["fold_skipped"] = fold_skipped
            try:
                from . import audit
                static_audit = audit.audit_cross_scene_ini(
                    Path(out_folder) / "mod.ini", routing, report["roles"],
                    own_excluded=own_excluded_tags,
                    draw_excludes=fold_draw_excludes)
                report["static_audit"] = static_audit
                if static_audit.get("errors"):
                    report["static_audit_errors"] = static_audit["errors"]
                    report["sound"] = False
            except Exception as e:
                report["static_audit"] = {"skipped": True, "reason": str(e), "errors": []}
            return report
    finally:
        cfg.object_source_folder, cfg.mod_output_folder, cfg.mod_name = saved[0], saved[2], saved[3]
        cfg.import_skeleton_type = saved_import_type
        slot_hook.clear_eligible_override()
        cfg.write_ini = saved_gating["write_ini"]
        cfg.partial_export = saved_gating["partial_export"]
        if hasattr(cfg, "copy_textures"):
            cfg.copy_textures = saved_gating["copy_textures"]
        if hasattr(cfg, "custom_template_live_update"):
            cfg.custom_template_live_update = saved_gating["custom_template_live_update"]
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
