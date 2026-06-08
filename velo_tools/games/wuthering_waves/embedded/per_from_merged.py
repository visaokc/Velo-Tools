"""Per-Component (from Merged) export mode for WWMI -- Velo driver layer (never edits ``_wwmi_core``).

Lets a user import a character in MERGED skeleton mode (unified vertex-group naming, convenient for
weighting) and export a PER-COMPONENT mod -- sidestepping the MERGED runtime's documented degradation
(*mod pauses while more than one of the same object is on screen*). On export the unified VG names are
translated back to each component's local 0-based numbering via the inverse of that component's
``vg_map`` (from ``Metadata.json``); a stock COMPONENT export then runs on temp copies.

Mirrors the EFMI ``_translate_global_vg_names_to_local`` pattern, but lives entirely in the velo driver
layer: ``VTWW_Export.execute`` is monkey-patched, the remap runs on temporary duplicates, and the core
``mod_skeleton_type`` is set to ``COMPONENT`` only for the underlying stock export.

Hard validation (like EFMI): a vertex weighted to a bone outside its component (a unified id not in the
component's ``vg_map``) cannot be represented per-component, so export is refused with an error telling
the user to move or zero those weights. This mode gives unified NAMING convenience, NOT true
cross-component weighting (that is only possible with the MERGED runtime, which has the on-screen-
duplicate pause).
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

import bpy

MODE_VALUE = 'COMPONENT_FROM_MERGED'
_TMP_PREFIX = '__vpfm_tmp_local_'
_COMPONENT_RE = re.compile(r'component[ _-]*([0-9]+)', re.I)


# ----------------------------------------------------------------- pure core (no bpy, unit-testable)

def build_inverse_vg_map(vg_map):
    """``vg_map``: ``{local_id: unified_id}`` (keys/values may be str or int).

    Returns ``{unified_id: smallest local_id}`` -- mirrors the import-side dedup where several local
    ids sharing one bone collapse onto the smallest local."""
    inverse = {}
    for k, v in vg_map.items():
        local_id, unified_id = int(k), int(v)
        cur = inverse.get(unified_id)
        if cur is None or local_id < cur:
            inverse[unified_id] = local_id
    return inverse


def plan_vg_remap(vg_map, vg_entries):
    """Plan one component's unified->local VG rename.

    ``vg_entries``: iterable of ``(name, has_weight)``. Returns ``(renames, drop_with_weight)``:
      * ``renames`` -- ``{old_name: local_name_str}`` for VGs in this component's palette, or
        ``{old_name: None}`` for VGs to drop (unified id not in this component's ``vg_map``).
      * ``drop_with_weight`` -- names of dropped VGs that still carry weight = stray cross-component
        weights -> the caller must hard-error.
    Non-numeric or ``ignore``-tagged VGs are left untouched (not renamed, not flagged)."""
    inverse = build_inverse_vg_map(vg_map)
    renames, drop_with_weight = {}, []
    for name, has_weight in vg_entries:
        if 'ignore' in name.lower() or not name.lstrip('-').isdigit():
            continue
        local = inverse.get(int(name))
        if local is None:
            if has_weight:
                drop_with_weight.append(name)
            renames[name] = None
        else:
            renames[name] = str(local)
    return renames, drop_with_weight


# ----------------------------------------------------------------- bpy helpers

def _component_id(name):
    m = _COMPONENT_RE.search(name)
    return int(m.group(1)) if m else None


def _load_vg_maps(source_folder):
    """Read ``{component_id: vg_map}`` from ``Metadata.json`` of the export source folder."""
    meta = json.loads((Path(source_folder) / 'Metadata.json').read_text(encoding='utf-8'))
    return {cid: (comp.get('vg_map') or {}) for cid, comp in enumerate(meta.get('components') or [])}


def _vgs_with_weight(obj, threshold=1e-6):
    """Set of vertex-group indices that carry any vertex weight > threshold (single pass)."""
    have = set()
    for v in obj.data.vertices:
        for g in v.groups:
            if g.weight > threshold:
                have.add(g.group)
    return have


def _remap_object(obj, vg_map):
    """Rename ``obj``'s unified VG names back to local 0-based (via ``vg_map`` inverse) and drop
    out-of-palette VGs. Returns the names of dropped VGs that still carried weight (stray cross-
    component weights). Two-pass (temp prefix) so transient name collisions during the swap are safe."""
    weighted = _vgs_with_weight(obj)
    entries = [(vg.name, vg.index in weighted) for vg in obj.vertex_groups]
    renames, drop_with_weight = plan_vg_remap(vg_map, entries)
    by_name = {vg.name: vg for vg in obj.vertex_groups}
    for old, new in renames.items():
        if new is not None and old in by_name:
            by_name[old].name = _TMP_PREFIX + new
    for vg in obj.vertex_groups:
        if vg.name.startswith(_TMP_PREFIX):
            vg.name = vg.name[len(_TMP_PREFIX):]
    for old, new in renames.items():
        if new is None and old in by_name:
            obj.vertex_groups.remove(by_name[old])
    return drop_with_weight


# ----------------------------------------------------------------- export hook (monkey-patch)

_PATCHED = {}


def _find_vtww_export():
    """Locate the velo vendored ``VTWW_Export`` operator class (name from the game registry)."""
    name = "VTWW_Export"
    try:
        from ...games import registry as _registry
        for d in _registry.all_descriptors():
            if getattr(d, "adapter_key", None) == "WWMI":
                name = d.export_op_class
                break
    except Exception:
        pass
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        cls = getattr(mod, name, None)
        if isinstance(cls, type) and hasattr(cls, "execute"):
            return cls
    return None


def _make_patched(orig_execute):
    def patched(self, context):
        cfg = getattr(context.scene, "VTWW_settings", None)
        if cfg is None or getattr(cfg, "mod_skeleton_type", None) != MODE_VALUE:
            return orig_execute(self, context)  # not our mode -> stock (incl. crossscene/core hooks)

        base_col = getattr(cfg, "component_collection", None)
        if base_col is None:
            self.report({'ERROR'}, "Per-Component (from Merged)：未选择 component collection。")
            return {'CANCELLED'}
        src = bpy.path.abspath(getattr(cfg, "object_source_folder", "") or "")
        try:
            vg_maps = _load_vg_maps(src)
        except Exception as e:
            self.report({'ERROR'}, "Per-Component (from Merged)：读取 Metadata.json 失败：%s" % e)
            return {'CANCELLED'}

        tmp_col = bpy.data.collections.new("vpfm_export")
        context.scene.collection.children.link(tmp_col)
        tmp_objs, renamed = [], []   # renamed: [(orig_obj, orig_name)] to restore in finally
        saved_col, saved_mode = cfg.component_collection, cfg.mod_skeleton_type
        try:
            for o in base_col.objects:
                if o.type != 'MESH':
                    continue
                orig_name = o.name
                cp = o.copy()
                cp.data = o.data.copy()
                # Give the copy the ORIGINAL Component name (Blender would otherwise suffix it '.001',
                # which breaks the cross-scene orchestrator's exact-name component routing -- the body
                # export's editable exclusion and the editable copy both look up "Component N" by exact
                # name). Free the name by temporarily renaming the original; restored in finally.
                o.name = orig_name + "__vpfm_orig"
                cp.name = orig_name
                renamed.append((o, orig_name))
                tmp_col.objects.link(cp)
                tmp_objs.append(cp)
                cid = _component_id(orig_name)
                if cid is not None and vg_maps.get(cid):
                    stray = _remap_object(cp, vg_maps[cid])
                    if stray:
                        self.report({'ERROR'},
                                    "导出失败：物体 `%s` (Component %s) 的顶点组 %s 权重越界——它们对应的统一骨不在"
                                    "该部件的 vg_map 内（COMPONENT 运行期无法表达跨部件权重）。请把这些权重转回本部件"
                                    "的骨，或刷零后再导出。" % (orig_name, cid, stray))
                        return {'CANCELLED'}
            cfg.component_collection = tmp_col
            cfg.mod_skeleton_type = 'COMPONENT'
            return orig_execute(self, context)
        finally:
            cfg.component_collection = saved_col
            cfg.mod_skeleton_type = saved_mode
            for cp in tmp_objs:   # remove copies first (frees the clean Component names)
                mesh = cp.data
                try:
                    bpy.data.objects.remove(cp, do_unlink=True)
                except Exception:
                    pass
                try:
                    bpy.data.meshes.remove(mesh)
                except Exception:
                    pass
            for o, name in renamed:   # then restore the originals' names
                try:
                    o.name = name
                except Exception:
                    pass
            try:
                context.scene.collection.children.unlink(tmp_col)
            except Exception:
                pass
            try:
                bpy.data.collections.remove(tmp_col)
            except Exception:
                pass

    return patched


def install():
    cls = _find_vtww_export()
    if cls is None:
        print("[velo.per-from-merged] VTWW_Export not found, skip install")
        return
    if id(cls) in _PATCHED:
        return
    _PATCHED[id(cls)] = (cls, cls.execute)
    cls.execute = _make_patched(cls.execute)
    print("[velo.per-from-merged] patched VTWW_Export.execute (Per-Component from Merged)")


def remove():
    for _cid, (cls, orig) in list(_PATCHED.items()):
        try:
            cls.execute = orig
        except Exception:
            traceback.print_exc()
    _PATCHED.clear()
