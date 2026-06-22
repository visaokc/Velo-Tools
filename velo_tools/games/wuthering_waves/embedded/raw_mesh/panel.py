# Panel for the Velo raw-mesh tool: a self-contained sub-panel under the velo
# game container (VELO_PT_game), gated to the Wuthering Waves game. Isolated
# from the stock WWMI panels (VTWW_*); the gate() mechanism never touches it.

import bpy


class VELO_PT_raw_mesh(bpy.types.Panel):
    bl_idname = "VELO_PT_raw_mesh"
    bl_label = "原始网格工具"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Velo Tools"
    bl_parent_id = "VELO_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        # Peer of the LOD / cross-scene panels (child of VELO_PT_main, sibling of
        # the WWMI container), shown on the Game tab with WWMI active. Mirrors
        # VELO_PT_wwmi_lod.poll; we gate the tab ourselves since VELO_PT_main does
        # not (it no longer goes through the VELO_PT_game container).
        vt = getattr(context.scene, "velo_tools", None)
        return (vt is not None
                and getattr(vt, "active_tab", "") == 'GAME'
                and getattr(vt, "active_game", "") == 'WUTHERING')

    def draw(self, context):
        layout = self.layout
        cfg = context.scene.velo_raw_mesh_settings

        layout.prop(cfg, "tool_mode")
        layout.separator()

        if cfg.tool_mode == 'EXTRACT':
            self._draw_extract(layout, cfg)
        elif cfg.tool_mode == 'IMPORT':
            self._draw_import(layout, cfg)
        else:
            self._draw_export(layout, cfg)

    def _draw_extract(self, layout, cfg):
        layout.prop(cfg, "frame_dump_folder")
        layout.prop(cfg, "output_folder")
        layout.prop(cfg, "folder_name")
        layout.prop(cfg, "hashes")
        layout.prop(cfg, "position_override")

        col = layout.column(align=True)
        col.prop(cfg, "skip_jpg")
        row = col.row(align=True)
        row.prop(cfg, "skip_small")
        if cfg.skip_small:
            row.prop(cfg, "skip_small_kb")

        layout.separator()
        layout.operator("vtww_raw.extract", icon='IMPORT')

    def _draw_import(self, layout, cfg):
        layout.prop(cfg, "import_folder")
        layout.separator()
        layout.operator("vtww_raw.import_mesh", icon='IMPORT')

    def _draw_export(self, layout, cfg):
        layout.prop(cfg, "export_collection")
        layout.prop(cfg, "mod_output_folder")
        layout.prop(cfg, "export_mode")
        layout.separator()
        layout.operator("vtww_raw.export", icon='EXPORT')


def register():
    bpy.utils.register_class(VELO_PT_raw_mesh)


def unregister():
    try:
        bpy.utils.unregister_class(VELO_PT_raw_mesh)
    except Exception:
        pass
