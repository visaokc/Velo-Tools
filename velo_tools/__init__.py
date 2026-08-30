bl_info = {
    "name": "Velo-Tools",
    "author": "Velo",
    "version": (1, 6, 4),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar (N) > Velo Tools",
    "description": "Mod Authoring Toolset — Vertex Group, Mesh, Weight, Arknights: Endfield EFMI, and Wuthering Waves WWMI workflows",
    "doc_url": "https://github.com/visaokc/Velo-Tools",
    "category": "Object",
}

# v0.3.0 refactored skeleton (PLAN_v0.4_rebuild.md R1/R2/R3 phases)
from . import updater
from . import i18n
from . import properties
from . import operators
from . import ui
from . import bone_number_map
from . import overlay
from . import general_mapping
from . import mesh
from . import weights
from . import games
from .core import mapping as _core_mapping
from .core import export as _core_export

# core.mapping's UI sub-panel attaches under VELO_EF_PT_root, which is registered by
# games.arknights_endfield, so it must be registered after games (the unregister order
# is reversed automatically).
_modules = (updater, properties, operators, ui, bone_number_map, overlay, general_mapping, mesh, weights, games, _core_mapping, _core_export)


def register():
    i18n.register()
    for m in _modules:
        m.register()
    # V0.1.6: embedded (CrossIB / ShapeKey) registration and the export-hook install are
    # already done inside games.arknights_endfield.register() (which depends on the vendored
    # EFMI core being registered). Do not call register_embedded_late here again, to avoid
    # double patching.

def unregister():
    for m in reversed(_modules):
        try:
            m.unregister()
        except Exception:
            pass
    i18n.unregister()


if __name__ == "__main__":
    register()
