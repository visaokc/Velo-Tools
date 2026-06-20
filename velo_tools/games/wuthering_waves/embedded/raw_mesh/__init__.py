# Velo raw-mesh tool (driver layer).
#
# A self-contained Velo sub-tool to extract / import / export ARBITRARY
# (non-character) meshes from a Wuthering Waves frame dump by buffer hash:
# VFX-layer and scene/environment meshes that the stock WWMI pose-chain
# extractor cannot detect. Everything here is isolated from the stock WWMI
# tool (VTWW_*) and never modifies _wwmi_core sources; the core is only
# reused read-only (dump parser, buffers.py text parsers).
#
# Modules:
#   scan.py    pure: dump -> hash index -> resolution units (no bpy)
#   layout.py  pure: per-slot input layout / .fmt / position heuristic (no bpy)
#   schema.py  pure: Metadata.json + additive velo_raw_mesh block (no bpy)
#   extract.py orchestration + self-contained consolidated-folder writer
#   (import_mesh.py / export_mesh.py / panel.py / settings.py / operators.py
#    are added in later phases)
#
# This package stays import-light: heavy / bpy-dependent submodules are
# imported lazily inside the register surface so the pure parts load headless.


def register():
    from . import settings, operators, panel
    settings.register()
    operators.register()
    panel.register()


def unregister():
    # LIFO; tolerate partial registration.
    from . import settings, operators, panel
    for mod in (panel, operators, settings):
        try:
            mod.unregister()
        except Exception:
            import traceback
            traceback.print_exc()
