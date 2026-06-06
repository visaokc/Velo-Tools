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
        out = str(Path(bpy.path.abspath(cfg.mod_output_folder)) / "cross_scene_velo")
        _IN_XSCENE[0] = True
        try:
            rep = orchestrator.build_cross_scene_mod(
                context, cfg, base_col, str(Path(bpy.path.abspath(src))), out, hole=False)
        except Exception:
            traceback.print_exc()
            try:
                self.report({'ERROR'}, "Cross-scene export failed (see system console).")
            except Exception:
                pass
            return {'CANCELLED'}
        finally:
            _IN_XSCENE[0] = False
        try:
            self.report({'INFO'}, "Cross-scene mod exported to %s | roles=%s" % (out, rep.get("roles")))
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
