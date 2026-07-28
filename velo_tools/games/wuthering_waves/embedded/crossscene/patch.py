"""Route schema-v3 WWMI aggregate roots into the Velo direct compiler.

The patch is Velo-prefix gated, idempotent, and leaves ordinary single-IB WWMI
imports/exports untouched. Legacy schema-v2 roots are rejected with a re-aggregate
message instead of falling through to the stock single-IB path.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import bpy

_PATCHED = {}


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
        cfg = getattr(context.scene, "VTWW_settings", None)
        src = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
        root = Path(bpy.path.abspath(src)) if src else None
        manifest = (root / "CrossSceneManifest.json") if root else None
        legacy = (root / "CrossSceneRouting.json") if root else None
        base_col = getattr(cfg, "component_collection", None) if cfg is not None else None
        if not ((manifest and manifest.is_file()) or (legacy and legacy.is_file())) or base_col is None:
            return orig_execute(self, context)  # not cross-scene -> stock export as-is
        from . import orchestrator
        from .....mesh import operators as mesh_operators
        # Write the compiled mod directly into the user's selected output root.
        out = str(Path(bpy.path.abspath(cfg.mod_output_folder)))
        try:
            with mesh_operators.suspend_material_route_auto_refresh(context.scene):
                rep = orchestrator.build_cross_scene_mod(
                    context, cfg, base_col, str(root), out, hole=False,
                    excluded_buffers=self.get_excluded_buffers(context))
                if (
                    bool(getattr(cfg, "use_asset_name_matching", False))
                    and not bool(getattr(cfg, "partial_export", False))
                    and bool(getattr(cfg, "write_ini", True))
                ):
                    from ..asset_name_matching.exporter import apply_stu_to_ini
                    rep["asset_name_rules"] = apply_stu_to_ini(
                        root,
                        Path(out) / "mod.ini",
                    )
        except Exception as e:
            traceback.print_exc()
            try:
                # Surface the actual reason (e.g. the own-buffer stray-weight guidance) instead
                # of only pointing at the console.
                self.report({'ERROR'}, "Cross-scene export failed: %s" % str(e))
            except Exception:
                pass
            return {'CANCELLED'}
        try:
            msg = "Cross-scene mod exported to %s | roles=%s" % (out, rep.get("roles"))
            if rep.get("slot_style"):
                msg += " | slot-style textures (%d slot, %d hash-fallback)" % (
                    len(rep.get("tex_slot") or []), len(rep.get("tex_blindzone") or []))
            if rep.get("asset_name_rules"):
                msg += " | asset-name textures %d" % int(
                    rep["asset_name_rules"])
            if rep.get("final_ini_written") is False:
                msg += " | final mod.ini skipped (write_ini off / partial export)"
            if rep.get("final_textures_written") is False:
                msg += " | final Textures/ skipped (copy textures off / partial export)"
            elif rep.get("root_dds_files") is not None:
                msg += " | root DDS %d/%d" % (
                    int(rep.get("root_dds_files", 0))
                    - len(rep.get("root_dds_missing") or []),
                    int(rep.get("root_dds_files", 0)))
                if rep.get("preserved_modified"):
                    msg += " (%d author edit(s) preserved)" % len(
                        rep.get("preserved_modified") or [])
            gated = rep.get("tex_gated_out") or []
            if rep.get("texture_gate") and gated:
                # The merge-root allowlist dropped these referenced hashes (absent from the root);
                # surface the count in the status line and the full hash list on the console.
                msg += " | pruned %d texture(s) not at merge root" % len(gated)
                print("[velo.xscene] merge-root texture allowlist pruned %d hash(es): %s"
                      % (len(gated), ", ".join(gated)))
            suppressed = rep.get("tex_suppressed_body") or rep.get("tex_suppressed_fold") or []
            if suppressed:
                msg += " | suppressed %d body hash fallback(s)" % len(suppressed)
                reasons = rep.get("tex_suppressed_body_reasons") or {}
                detail = ", ".join("%s:%s" % (h, reasons.get(h, "?")) for h in suppressed)
                print("[velo.xscene] body hash fallback suppressed %d hash(es): %s"
                      % (len(suppressed), detail or ", ".join(suppressed)))
            if not rep.get("sound", True):
                # The final IR audit failed; never report this as a clean success.
                msg += (" | self-check FAILED: %d dangling ref(s), %d missing file(s)"
                        % (len(rep.get("dangling") or []), len(rep.get("missing") or [])))
                root_missing = rep.get("root_dds_missing") or []
                if root_missing and rep.get("final_textures_written"):
                    msg += " | %d root DDS missing" % len(root_missing)
                    print("[velo.xscene] root DDS missing: %s" % ", ".join(root_missing))
                scope_errors = rep.get("scope_errors") or []
                geometry_errors = rep.get("geometry_errors") or []
                if scope_errors:
                    msg += " | PS scope %d error(s)" % len(scope_errors)
                    print("[velo.xscene] PS scope errors: %s" % " | ".join(scope_errors))
                if geometry_errors:
                    msg += " | Geometry %d error(s)" % len(geometry_errors)
                    print("[velo.xscene] Geometry errors: %s" % " | ".join(geometry_errors))
                audit_errors = rep.get("static_audit_errors") or []
                if audit_errors:
                    msg += " | static audit %d error(s)" % len(audit_errors)
                    print("[velo.xscene] static audit errors: %s" % " | ".join(audit_errors))
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
    print("[velo.xscene-hook] patched VTWW_Export.execute (schema-v3 direct compiler)")


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


def _xscene_import_root(context):
    cfg = getattr(context.scene, "VTWW_settings", None)
    if cfg is None:
        return None
    src = getattr(cfg, "object_source_folder", "")
    if not src:
        return None
    root = Path(bpy.path.abspath(src))
    if ((root / "CrossSceneManifest.json").is_file()
            or (root / "CrossSceneRouting.json").is_file()):
        return root
    return None


def _make_patched_import(orig_execute):
    def patched(self, context):
        root = _xscene_import_root(context)
        if root is None:
            return orig_execute(self, context)  # stock import: zero impact
        from .manifest import CrossSceneManifestError, load_manifest
        try:
            load_manifest(root)
        except CrossSceneManifestError as exc:
            try:
                self.report({'ERROR'}, str(exc))
            except Exception:
                pass
            return {'CANCELLED'}
        cfg = getattr(context.scene, "VTWW_settings", None)
        if getattr(cfg, "import_skeleton_type", "") != "MERGED":
            return orig_execute(self, context)
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
