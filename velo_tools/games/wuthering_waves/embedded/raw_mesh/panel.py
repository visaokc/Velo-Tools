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
    bl_parent_id = "VELO_PT_game"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        s = getattr(context.scene, "velo_tools", None)
        return s is not None and getattr(s, "active_game", "") == "WUTHERING"

    def draw(self, context):
        layout = self.layout
        cfg = context.scene.velo_raw_mesh_settings

        layout.row().prop(cfg, "tool_mode", expand=True)
        layout.separator()

        if cfg.tool_mode == 'EXTRACT':
            self._draw_extract(layout, cfg)
        elif cfg.tool_mode == 'IMPORT':
            layout.label(text="导入（下一阶段）", icon='INFO')
        else:
            layout.label(text="导出（下一阶段）", icon='INFO')

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


def register():
    bpy.utils.register_class(VELO_PT_raw_mesh)


def unregister():
    try:
        bpy.utils.unregister_class(VELO_PT_raw_mesh)
    except Exception:
        pass
