# WWMI slot-style texture layer (velo driver layer).
#
# Replaces the per-hash [TextureOverrideTextureN] sections of an exported mod
# with slot-keyed rebinding inside the component draw scope, making textures
# immune to streaming hash churn (each mip level is a separate D3D resource
# with its own hash since WuWa 3.4). Optional per export: VTWW_Settings
# `velo_slot_style_textures` (default ON); unchecked exports stay byte-equal
# to stock. See games/wuthering_waves/docs/adr/0006.
#
#   constants.py   game-level constants (filter_index namespace, structural
#                  ShaderRegex patterns, emitted brand-free names) - the only
#                  non-derived knowledge
#   dds_meta.py    pure: minimal DDS header reader (descriptors read live from
#                  the source-folder files at export time)
#   generator.py   pure: per-form maps + texture list -> slot-style ini block
#   transform.py   pure: rendered mod.ini -> slot-style mod.ini (anchor-based,
#                  template-agnostic: stock + LOD fork + future templates)
#   form_merge.py  raw extra-form FrameAnalysis dump -> "extra_forms" key inside
#                  ShaderTextureUsage.json (multi-form characters need one dump
#                  per form; any form count supported)
#   hook.py        IniMaker.build_from_template wrap (installed after the LOD
#                  hook so it post-processes the final rendered text)
#   ui.py          "Velo 兼容选项" export box + form-merge sub-panel/operator
#
# Nothing in this package modifies _wwmi_core sources. This module stays
# import-light (no bpy at import time) so tests can load the pure parts
# headless; everything heavy is imported lazily.


def inject_settings():
    from . import ui
    ui.inject_settings()


def restore_settings():
    from . import ui
    ui.restore_settings()


def install():
    from . import hook
    hook.install()


def remove():
    from . import hook
    hook.remove()


def register_ui():
    from . import ui
    ui.register()


def unregister_ui():
    from . import ui
    ui.unregister()
