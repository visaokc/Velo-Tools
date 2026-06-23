"""Cross-scene export hook -- monkey-patch ``VTWW_Export.execute``.

When the user clicks the regular "Export Mod" on a base imported and edited from a
merged folder: if the export source folder contains ``CrossSceneRouting.json``, it
automatically runs the cross-scene orchestrator (folds and merges into a single mod
that works across all scenes); otherwise it does the stock export as-is (zero impact
on non-cross-scene projects).

- **Prefix gating**: only patch the velo vendored ``VTWW_Export`` (class name taken
  from the game registry), never touching the same-named operator of a standalone
  WWMI-Tools installed separately by the user.
- **Recursion guard**: inside the orchestrator, re-exporting each sub IB and
  re-exporting the morph reference (the reference also points at the merged folder
  containing the JSON) will trigger ``vtww.export_mod`` again; once ``_IN_XSCENE`` is
  set, this patch passes straight through to orig (stock) and no longer recurses into
  the cross-scene branch.
- Idempotent: already-patched classes are skipped (``_PATCHED``). Never modify
  ``_wwmi_core``.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import bpy

_PATCHED = {}
_IN_XSCENE = [False]


def _find_vtww_export():
    """Take the WWMI export operator class name from the game registry, then locate that Operator subclass in sys.modules."""
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
        if _IN_XSCENE[0]:
            return orig_execute(self, context)  # recursion guard: sub-export/reference-export goes stock
        cfg = getattr(context.scene, "VTWW_settings", None)
        src = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
        routing = (Path(bpy.path.abspath(src)) / "CrossSceneRouting.json") if src else None
        base_col = getattr(cfg, "component_collection", None) if cfg is not None else None
        if not (routing and routing.is_file()) or base_col is None:
            return orig_execute(self, context)  # not cross-scene -> stock export as-is
        from . import orchestrator
        # Write the merged mod DIRECTLY into the user's output folder (like a stock single-IB export),
        # not a hardcoded `cross_scene_velo` subfolder. The assembler cleans only its own products
        # (Meshes/ Textures/ mod.ini), so sibling files in the output folder are left intact.
        out = str(Path(bpy.path.abspath(cfg.mod_output_folder)))
        _IN_XSCENE[0] = True
        try:
            rep = orchestrator.build_cross_scene_mod(
                context, cfg, base_col, str(Path(bpy.path.abspath(src))), out, hole=False)
        except Exception as e:
            traceback.print_exc()
            try:
                # Surface the actual reason (e.g. the own-buffer stray-weight guidance) instead
                # of only pointing at the console.
                self.report({'ERROR'}, "Cross-scene export failed: %s" % str(e)[:400])
            except Exception:
                pass
            return {'CANCELLED'}
        finally:
            _IN_XSCENE[0] = False
        try:
            msg = "Cross-scene mod exported to %s | roles=%s" % (out, rep.get("roles"))
            if rep.get("slot_style"):
                msg += " | slot-style textures (%d slot, %d hash-fallback)" % (
                    len(rep.get("tex_slot") or []), len(rep.get("tex_blindzone") or []))
            if rep.get("final_ini_written") is False:
                msg += " | final mod.ini skipped (write_ini off / partial export)"
            if rep.get("final_textures_written") is False:
                msg += " | final Textures/ skipped (copy textures off / partial export)"
            gated = rep.get("tex_gated_out") or []
            if rep.get("texture_gate") and gated:
                # The merge-root allowlist dropped these referenced hashes (absent from the root);
                # surface the count in the status line and the full hash list on the console.
                msg += " | pruned %d texture(s) not at merge root" % len(gated)
                print("[velo.xscene] merge-root texture allowlist pruned %d hash(es): %s"
                      % (len(gated), ", ".join(gated)))
            if not rep.get("sound", True):
                # The assembler self-check failed (dangling refs / missing files / texture or skeleton
                # mismatch): the mod was written but may not work -- never report this as a clean success.
                msg += (" | self-check FAILED: %d dangling ref(s), %d missing file(s)"
                        % (len(rep.get("dangling") or []), len(rep.get("missing") or [])))
                self.report({'WARNING'}, msg)
            else:
                self.report({'INFO'}, msg)
        except Exception:
            pass
        return {'FINISHED'}

    return patched


def install():
    cls = _find_vtww_export()
    if cls is None:
        print("[velo.xscene-hook] VTWW_Export not found, skip install")
        return
    if id(cls) in _PATCHED:
        return
    _PATCHED[id(cls)] = (cls, cls.execute)
    cls.execute = _make_patched(cls.execute)
    print("[velo.xscene-hook] patched VTWW_Export.execute (cross-scene fold on CrossSceneRouting.json)")


def remove():
    for _cid, (cls, orig) in list(_PATCHED.items()):
        try:
            cls.execute = orig
        except Exception:
            traceback.print_exc()
    _PATCHED.clear()


# --- Cross-scene MERGED import fix: _wwmi_core component-index regex bug ---
#
# The stock _wwmi_core MERGED importer maps each Component*.fmt to its Metadata
# component with the regex r'.*component[ -_]*([0-9]+).*'. Its class [ -_] is an ASCII
# RANGE (0x20-0x5F) that INCLUDES digits, so the greedy [ -_]* eats all but the LAST
# digit: 'Component 10' -> '0', 'Component 11' -> '1', 'Component 5.001' -> '1'.
# Single-digit components (0-9) are unaffected. In a cross-scene MERGED merge the body
# fills components 0-7 and the editable form2 adds 8-11, so C10/C11 (and the split bear
# 'Component 5.001') pick the WRONG component's vg_map and lose their unified VG numbers.
# We must not edit _wwmi_core, so only for a cross-scene MERGED import we temporarily
# swap the blender_import module's `re` for a shim that corrects that one pattern (full
# number; 'Component 5.001' -> parent component 5) and delegates everything else. Stock
# single-IB imports never take this branch, so their behavior is byte-for-byte unchanged.
import re as _re

_IMPORT_PATCHED = {}
_BUGGY_COMPONENT_RE = r'.*component[ -_]*([0-9]+).*'
# [ _-] is a literal class (space / underscore / dash), NOT a range, so digits are never
# eaten; ([0-9]+) captures the full first integer ('component 5.001' -> parent '5').
_CORRECT_COMPONENT_RE = _re.compile(r'component[ _-]*([0-9]+)')


class _ComponentPattern:
    """Drop-in for the buggy component-number regex used by the MERGED importer."""

    def findall(self, s):
        m = _CORRECT_COMPONENT_RE.search(s)
        return [m.group(1)] if m else []


class _ReShim:
    """Wraps the real ``re`` module: returns the corrected pattern for the one buggy
    component regex, delegates every other attribute (incl. other compile calls)."""

    def __init__(self, real):
        self._real = real

    def compile(self, pattern, *args, **kwargs):
        if pattern == _BUGGY_COMPONENT_RE:
            return _ComponentPattern()
        return self._real.compile(pattern, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _find_vtww_import():
    """Locate the velo vendored ``VTWW_Import`` operator class. The 'VTWW_' prefix is
    velo-only (a standalone WWMI-Tools uses 'WWMI_'), so we never patch the user's
    separate install."""
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        cls = getattr(mod, "VTWW_Import", None)
        if isinstance(cls, type) and hasattr(cls, "execute"):
            return cls
    return None


def _is_xscene_merged_import(context):
    cfg = getattr(context.scene, "VTWW_settings", None)
    if cfg is None or getattr(cfg, "import_skeleton_type", "") != "MERGED":
        return False
    src = getattr(cfg, "object_source_folder", "")
    if not src:
        return False
    return (Path(bpy.path.abspath(src)) / "CrossSceneRouting.json").is_file()


def _make_patched_import(orig_execute):
    def patched(self, context):
        if not _is_xscene_merged_import(context):
            return orig_execute(self, context)  # stock import: zero impact
        from ..._wwmi_core.blender_import import blender_import as _bi
        real_re = _bi.re
        _bi.re = _ReShim(real_re)
        try:
            return orig_execute(self, context)
        finally:
            _bi.re = real_re
    return patched


def install_import():
    cls = _find_vtww_import()
    if cls is None:
        print("[velo.xscene-hook] VTWW_Import not found, skip import-fix install")
        return
    if id(cls) in _IMPORT_PATCHED:
        return
    _IMPORT_PATCHED[id(cls)] = (cls, cls.execute)
    cls.execute = _make_patched_import(cls.execute)
    print("[velo.xscene-hook] patched VTWW_Import.execute (MERGED component-index regex fix)")


def remove_import():
    for _cid, (cls, orig) in list(_IMPORT_PATCHED.items()):
        try:
            cls.execute = orig
        except Exception:
            traceback.print_exc()
    _IMPORT_PATCHED.clear()
