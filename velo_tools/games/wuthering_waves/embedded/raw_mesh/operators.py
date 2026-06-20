# Operators for the Velo raw-mesh tool. Phase 1 ships the extract operator;
# import/export operators are added in later phases.

import bpy


class VELO_OT_RawMeshExtract(bpy.types.Operator):
    bl_idname = "vtww_raw.extract"
    bl_label = "按 Hash 提取网格"
    bl_description = "按 IB/VB Hash 从 Dump 提取特效/场景网格到一个整合文件夹"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from . import extract
        cfg = context.scene.velo_raw_mesh_settings

        dump = bpy.path.abspath(cfg.frame_dump_folder).strip()
        out = bpy.path.abspath(cfg.output_folder).strip()
        if not dump:
            self.report({'ERROR'}, "请先指定 Frame Dump 目录")
            return {'CANCELLED'}
        if not out:
            self.report({'ERROR'}, "请先指定输出目录")
            return {'CANCELLED'}
        if not cfg.hashes.strip():
            self.report({'ERROR'}, "请先填写要提取的 Hash 列表")
            return {'CANCELLED'}

        try:
            summary = extract.extract(
                dump_folder=dump,
                output_folder=out,
                hashes_text=cfg.hashes,
                position_override=cfg.position_override,
                folder_name=(cfg.folder_name.strip() or None),
                skip_jpg=cfg.skip_jpg,
                skip_small=cfg.skip_small,
                skip_small_kb=cfg.skip_small_kb,
            )
        except (extract.RawMeshExtractError,
                extract.scan.RawMeshScanError,
                extract._layout.RawMeshLayoutError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"提取失败：{e}")
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"已提取 {summary['components']} 个 component、{summary['textures']} 张贴图 → {summary['folder']}")
        return {'FINISHED'}


class VELO_OT_RawMeshImport(bpy.types.Operator):
    bl_idname = "vtww_raw.import_mesh"
    bl_label = "导入整合文件夹"
    bl_description = "把本工具提取出的整合文件夹导入 Blender（保留全部顶点属性，可编辑 Position）"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from . import import_mesh
        cfg = context.scene.velo_raw_mesh_settings

        folder = bpy.path.abspath(cfg.import_folder).strip()
        if not folder:
            self.report({'ERROR'}, "请先指定要导入的整合文件夹")
            return {'CANCELLED'}

        try:
            summary = import_mesh.import_folder(folder, context)
        except import_mesh.RawMeshImportError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"导入失败：{e}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"已导入 {summary['objects']} 个 component → 集合「{summary['collection']}」")
        return {'FINISHED'}


class VELO_OT_RawMeshExport(bpy.types.Operator):
    bl_idname = "vtww_raw.export"
    bl_label = "导出为 Mod"
    bl_description = "把集合里的 raw-mesh 对象导出为 plain 3dmigoto mod（每个 component 独立覆盖各自的源 draw）"
    bl_options = {'REGISTER'}

    def execute(self, context):
        from . import export_mesh
        cfg = context.scene.velo_raw_mesh_settings

        coll = cfg.export_collection
        out = bpy.path.abspath(cfg.mod_output_folder).strip()
        if coll is None:
            self.report({'ERROR'}, "请先指定导出集合")
            return {'CANCELLED'}
        if not out:
            self.report({'ERROR'}, "请先指定 Mod 输出目录")
            return {'CANCELLED'}

        try:
            summary = export_mesh.export_mod(coll, out, cfg.export_mode)
        except export_mesh.RawMeshExportError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, f"导出失败：{e}")
            return {'CANCELLED'}

        msg = f"已导出 {summary['components']} 个 component、{summary['textures']} 张贴图 → {summary['folder']}"
        if summary['rebuilt']:
            msg += f"（{summary['rebuilt']} 个走 Rebuild：非标准属性有损）"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


_CLASSES = (VELO_OT_RawMeshExtract, VELO_OT_RawMeshImport, VELO_OT_RawMeshExport)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
