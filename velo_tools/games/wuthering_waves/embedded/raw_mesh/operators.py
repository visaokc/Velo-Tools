
from velo_tools.i18n import iface_
# Operators for the Velo raw-mesh tool. Phase 1 ships the extract operator;
# import/export operators are added in later phases.

import bpy


class VELO_OT_RawMeshExtract(bpy.types.Operator):
    bl_idname = "vtww_raw.extract"
    bl_label = 'Extract Mesh by Hash'
    bl_description = 'Extract special effects/scene meshes from Dump into an integrated folder according to IB/VB Hash.'
    bl_options = {'REGISTER'}

    def execute(self, context):
        from . import extract
        cfg = context.scene.velo_raw_mesh_settings

        dump = bpy.path.abspath(cfg.frame_dump_folder).strip()
        out = bpy.path.abspath(cfg.output_folder).strip()
        if not dump:
            self.report({'ERROR'}, iface_('Please first specify the Frame Dump directory'))
            return {'CANCELLED'}
        if not out:
            self.report({'ERROR'}, iface_('Please specify the output directory first'))
            return {'CANCELLED'}
        if not cfg.hashes.strip():
            self.report({'ERROR'}, iface_('Please first fill in the list of Hash to be extracted'))
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
            self.report({'ERROR'}, iface_(str(str(e))))
            return {'CANCELLED'}
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, iface_('Extraction failed: {0}').format(e))
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            iface_('Extracted {0} components, {1} textures → {2}').format(summary['components'], summary['textures'], summary['folder']))
        return {'FINISHED'}


class VELO_OT_RawMeshImport(bpy.types.Operator):
    bl_idname = "vtww_raw.import_mesh"
    bl_label = 'Import Merged Folder'
    bl_description = 'Import the consolidated folder extracted by this tool into Blender (retain all vertex attributes, editable Position)'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from . import import_mesh
        cfg = context.scene.velo_raw_mesh_settings

        folder = bpy.path.abspath(cfg.import_folder).strip()
        if not folder:
            self.report({'ERROR'}, iface_('Please specify the integrated folder to import first'))
            return {'CANCELLED'}

        try:
            summary = import_mesh.import_folder(folder, context)
        except import_mesh.RawMeshImportError as e:
            self.report({'ERROR'}, iface_(str(str(e))))
            return {'CANCELLED'}
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, iface_('Import failed: {0}').format(e))
            return {'CANCELLED'}

        self.report({'INFO'}, iface_("Imported {0} components → Collection '{1}'").format(summary['objects'], summary['collection']))
        return {'FINISHED'}


class VELO_OT_RawMeshExport(bpy.types.Operator):
    bl_idname = "vtww_raw.export"
    bl_label = 'Export as Mod'
    bl_description = 'Export raw-mesh objects in the collection as a plain 3dmigoto mod (each component independently overrides its own source draw).'
    bl_options = {'REGISTER'}

    def execute(self, context):
        from . import export_mesh
        cfg = context.scene.velo_raw_mesh_settings

        coll = cfg.export_collection
        out = bpy.path.abspath(cfg.mod_output_folder).strip()
        if coll is None:
            self.report({'ERROR'}, iface_('Please specify the export set first'))
            return {'CANCELLED'}
        if not out:
            self.report({'ERROR'}, iface_('Please specify the Mod output directory first'))
            return {'CANCELLED'}

        try:
            summary = export_mesh.export_mod(coll, out, cfg.export_mode)
        except export_mesh.RawMeshExportError as e:
            self.report({'ERROR'}, iface_(str(str(e))))
            return {'CANCELLED'}
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.report({'ERROR'}, iface_('Export failed: {0}').format(e))
            return {'CANCELLED'}

        msg = f"已导出 {summary['components']} 个 component、{summary['textures']} 张贴图 → {summary['folder']}"
        if summary['rebuilt']:
            msg += f"（{summary['rebuilt']} 个走 Rebuild：非标准属性有损）"
        self.report({'INFO'}, iface_(str(msg)))
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
