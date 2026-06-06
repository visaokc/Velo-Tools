"""Export preprocessing + adapter layer (V0.1.6).

Since V0.1.6:
- No longer registers a standalone "MOD 导出" panel. Instead, when the user clicks
  "导出 Mod" in the vendored EFMI panel, the hook automatically triggers
  non-destructive preprocessing and then hands off to the original exporter.
- 1.0.8: the export target is now uniformly decided by velo_tools.active_game of the
  "游戏" tab (via games/registry); there is no longer a standalone export_adapter dropdown.
- The WWMI adapter likewise wraps the execute of vtww.export_mod via the hook (Velo built-in fork).
"""
from . import preexport  # noqa: F401
from . import adapters   # noqa: F401
from . import operators  # noqa: F401
from . import hook       # noqa: F401


def register():
    operators.register()
    # The hook installation is placed at the end of games.arknights_endfield.register(),
    # because it depends on the vendored EFMI having finished _al.register() before the VTEF_Export class can be found.


def unregister():
    try:
        hook.remove_export_hook()
    except Exception:
        pass
    try:
        operators.unregister()
    except Exception:
        pass
