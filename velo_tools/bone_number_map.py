"""WWMI numeric vertex-group <-> original bone-name mapping UI."""

from __future__ import annotations

from velo_tools.i18n import iface_

import json
from pathlib import Path
import re

import bpy


class VELO_WWMI_BoneMapRow(bpy.types.PropertyGroup):
    numeric_id: bpy.props.StringProperty(name='Numerical numbering', default="")
    original_name: bpy.props.StringProperty(name='Original Bone Name', default="")
    component_name: bpy.props.StringProperty(name='Source Component', default="")


class VELO_WWMI_BoneMapSettings(bpy.types.PropertyGroup):
    unpack_folder: bpy.props.StringProperty(name='Unpack path', subtype="DIR_PATH", default="")
    rows: bpy.props.CollectionProperty(type=VELO_WWMI_BoneMapRow)
    active_row: bpy.props.IntProperty(default=0)
    show_original: bpy.props.BoolProperty(name='Currently displaying the original name', default=False)
    voxel_size: bpy.props.FloatProperty(
        name='Voxel size', default=0.05, min=0.005, max=0.2, precision=3,
        description='Normalized voxel size used for geometry matching',
    )
    similarity_threshold: bpy.props.FloatProperty(
        name='Minimum similarity', default=55.0, min=1.0, max=100.0, subtype='PERCENTAGE',
        description='Minimum voxel similarity that each WWMI Component must reach',
    )
    rename_side_suffix: bpy.props.BoolProperty(
        name='Rename the skeleton with .L/.R suffix', default=True,
        description='When importing a skeleton and restoring the original weight group names, explicit left and right suffixes like _L/_R are unified into Blender .L/.R',
    )


def _selected_meshes(context):
    return [o for o in context.selected_objects if o.type == "MESH"]


def _collection_contains(collection, obj):
    return collection.objects.get(obj.name) is not None or any(
        _collection_contains(child, obj) for child in collection.children
    )


_MATCHING_RESULT_NAME = "WWMI_MatchingResult.json"


def _matching_result_path(source_folder):
    return Path(bpy.path.abspath(source_folder)).resolve() / _MATCHING_RESULT_NAME


def _load_hierarchy_bones(settings, source_folder):
    from .games.wuthering_waves.skeleton_import import _find_skeleton, load_saved_skeleton

    if settings.unpack_folder.strip():
        return _find_skeleton(Path(bpy.path.abspath(settings.unpack_folder)))
    if source_folder and source_folder.strip():
        return load_saved_skeleton(_matching_result_path(source_folder))
    raise ValueError("按骨骼层级重排需要解包路径，或对象源目录中的匹配结果")


class VELO_OT_wwmi_bone_map_generate(bpy.types.Operator):
    bl_idname = "velo.wwmi_bone_map_generate"
    bl_label = 'Generate Mapping Table'
    bl_description = 'Unpack all .uemodel sections and WWMI Component in the voxel matching directory, then generate a mapping from global IDs to the original skeleton names'

    def execute(self, context):
        settings = context.scene.velo_wwmi_bone_map
        cfg = getattr(context.scene, "VTWW_settings", None)
        source_folder = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
        if not settings.unpack_folder.strip():
            self.report({'ERROR'}, iface_('Please first fill in the unpack path'))
            return {'CANCELLED'}
        if not source_folder.strip():
            self.report({'ERROR'}, iface_('Please first fill in the source directory for the WWMI object'))
            return {'CANCELLED'}
        from .games.wuthering_waves.bone_mapping import BoneMappingError, generate_mapping
        try:
            mapping, evidence = generate_mapping(
                Path(bpy.path.abspath(settings.unpack_folder)),
                Path(bpy.path.abspath(source_folder)),
                voxel_size=settings.voxel_size,
                similarity_threshold=settings.similarity_threshold,
            )
        except BoneMappingError as exc:
            self.report({'ERROR'}, iface_(str(str(exc))))
            return {'CANCELLED'}
        settings.rows.clear()
        for number, bone_name, component_name, _support in mapping:
            row = settings.rows.add()
            row.numeric_id = str(number)
            row.original_name = bone_name
            row.component_name = component_name
        settings.show_original = False
        self.report({'INFO'}, iface_('{1} rows generated from voxel matching of {0} Component').format(len(evidence), len(mapping)))
        return {'FINISHED'}


class VELO_OT_wwmi_auto_bind(bpy.types.Operator):
    bl_idname = "velo.wwmi_auto_bind"
    bl_label = 'Bind Skeleton to Mod Mesh'
    bl_description = 'Generate Mapping, Rename Current WWMI Component Set and Import Skeleton'

    def execute(self, context):
        settings = context.scene.velo_wwmi_bone_map
        cfg = getattr(context.scene, "VTWW_settings", None)
        source_folder = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
        if not source_folder.strip():
            self.report({'ERROR'}, iface_('Please first fill in the source directory of the object'))
            return {'CANCELLED'}
        result_exists = _matching_result_path(source_folder).is_file()
        if not result_exists and not settings.unpack_folder.strip():
            self.report({'ERROR'}, iface_('No matches found in source directory, please fill in the unpack path first'))
            return {'CANCELLED'}
        component_collection = getattr(cfg, "component_collection", None) if cfg is not None else None
        targets = [
            obj for obj in context.scene.objects
            if obj.type == 'MESH' and component_collection is not None
            and _collection_contains(component_collection, obj)
        ]
        if component_collection is None or not targets:
            self.report({'ERROR'}, iface_('Please first specify the component set with a mesh in WWMI export mode'))
            return {'CANCELLED'}
        previous = [(obj, obj.select_get()) for obj in context.scene.objects]
        active = context.view_layer.objects.active
        try:
            if not result_exists:
                if bpy.ops.velo.wwmi_bone_map_generate() != {'FINISHED'}:
                    return {'CANCELLED'}
            elif not settings.rows:
                if bpy.ops.velo.wwmi_bone_map_load_result() != {'FINISHED'}:
                    return {'CANCELLED'}
            for obj, selected in previous:
                obj.select_set(False)
            for obj in targets:
                obj.select_set(True)
            context.view_layer.objects.active = targets[0]
            result = bpy.ops.velo.wwmi_bone_map_toggle(target='ORIGINAL')
            if result != {'FINISHED'}:
                return result
            result = bpy.ops.velo.wwmi_skeleton_import()
            if result != {'FINISHED'}:
                return result
        finally:
            for obj, selected in previous:
                if obj.name in bpy.context.scene.objects:
                    obj.select_set(selected)
            context.view_layer.objects.active = active
        self.report({'INFO'}, iface_('Completed mapping renaming and skeleton binding for {0} Mod meshes').format(len(targets)))
        return {'FINISHED'}


class VELO_OT_wwmi_skeleton_import(bpy.types.Operator):
    bl_idname = "velo.wwmi_skeleton_import"
    bl_label = 'Import Skeleton'
    bl_description = "Prefer importing the armature from the unpack path; if not filled in, import from matching results in the object's source directory"

    def execute(self, context):
        settings = context.scene.velo_wwmi_bone_map
        try:
            from .games.wuthering_waves.skeleton_import import import_skeleton, load_saved_skeleton
            cfg = getattr(context.scene, "VTWW_settings", None)
            source_folder = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
            if settings.unpack_folder.strip():
                folder = Path(bpy.path.abspath(settings.unpack_folder))
                bones = None
            elif source_folder.strip():
                result_path = _matching_result_path(source_folder)
                if not result_path.is_file():
                    raise ValueError(f"源目录中找不到匹配结果：{result_path.name}")
                folder = Path(".")
                bones = load_saved_skeleton(result_path)
            else:
                raise ValueError("没有可用的解包路径或源目录匹配结果")
            arm_obj, bone_count, bound_count = import_skeleton(
                folder,
                bones=bones,
                component_collection=getattr(cfg, "component_collection", None) if cfg is not None else None,
                mirror_mesh=bool(getattr(cfg, "mirror_mesh", False)),
                rename_side_suffix=settings.rename_side_suffix)
        except Exception as exc:
            self.report({'ERROR'}, iface_('Skeleton import failed: {0}').format(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, iface_('Imported {0} root skeletons; bound {1} Component meshes').format(bone_count, bound_count))
        return {'FINISHED'}


class VELO_OT_wwmi_bone_map_load_result(bpy.types.Operator):
    bl_idname = "velo.wwmi_bone_map_load_result"
    bl_label = 'Load Mapping Table from Source Directory'
    bl_description = 'Restore mapping table from WWMI_MatchingResult.json in the object source directory.'

    def execute(self, context):
        settings = context.scene.velo_wwmi_bone_map
        cfg = getattr(context.scene, "VTWW_settings", None)
        source_folder = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
        if not source_folder.strip():
            self.report({'ERROR'}, iface_('Please first fill in the source directory of the object'))
            return {'CANCELLED'}
        try:
            payload = json.loads(_matching_result_path(source_folder).read_text(encoding="utf-8"))
            records = payload["mapping"]
            if not isinstance(records, list) or not records:
                raise ValueError("映射记录为空")
            restored = []
            for record in records:
                restored.append((str(record["numeric_id"]), str(record["original_name"]),
                                 str(record.get("component_name", ""))))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self.report({'ERROR'}, iface_('Failed to load match results: {0}').format(exc))
            return {'CANCELLED'}
        settings.rows.clear()
        for numeric_id, original_name, component_name in restored:
            row = settings.rows.add()
            row.numeric_id = numeric_id
            row.original_name = original_name
            row.component_name = component_name
        settings.active_row = 0
        settings.show_original = False
        self.report({'INFO'}, iface_('Loaded {0} lines of mapping from the source directory').format(len(restored)))
        return {'FINISHED'}


class VELO_OT_wwmi_bone_map_save_result(bpy.types.Operator):
    bl_idname = "velo.wwmi_bone_map_save_result"
    bl_label = 'Save Matching Results to Source Directory'
    bl_description = 'Save the mapping table and unpacked skeleton snapshot to the object source directory'

    def execute(self, context):
        settings = context.scene.velo_wwmi_bone_map
        cfg = getattr(context.scene, "VTWW_settings", None)
        source_folder = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
        if not source_folder.strip():
            self.report({'ERROR'}, iface_('Please first fill in the source directory of the object'))
            return {'CANCELLED'}
        rows = [row for row in settings.rows if row.numeric_id and row.original_name]
        if not rows:
            self.report({'ERROR'}, iface_('The mapping table has no complete records to save'))
            return {'CANCELLED'}
        if not settings.unpack_folder.strip():
            self.report({'ERROR'}, iface_('To save the matching result, you need to fill in the unpacking path to read the skeleton snapshot'))
            return {'CANCELLED'}
        try:
            from .games.wuthering_waves.skeleton_import import _find_skeleton
            bones = _find_skeleton(Path(bpy.path.abspath(settings.unpack_folder)))
            target = _matching_result_path(source_folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "format": "Velo WWMI matching result",
                "version": 1,
                "mapping": [
                    {
                        "numeric_id": row.numeric_id,
                        "original_name": row.original_name,
                        "component_name": row.component_name,
                    }
                    for row in rows
                ],
                "skeleton": [
                    {
                        "name": bone.name,
                        "parent": bone.parent,
                        "position": list(bone.position),
                        "rotation": list(bone.rotation),
                    }
                    for bone in bones
                ],
            }
            target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except Exception as exc:
            self.report({'ERROR'}, iface_('Failed to save match result: {0}').format(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, iface_('Saved {0} lines of mapping and {1} root skeletons to the source directory').format(len(rows), len(bones)))
        return {'FINISHED'}


class VELO_OT_wwmi_bone_map_toggle(bpy.types.Operator):
    bl_idname = "velo.wwmi_bone_map_toggle"
    bl_label = 'Toggle Name Display'
    target: bpy.props.EnumProperty(items=[("ORIGINAL", 'Original Name', ""), ("NUMERIC", 'Numerical numbering', "")])

    @staticmethod
    def _component_name(obj):
        match = re.search(r'Component\s+(\d+(?:\.\d+)?)', obj.name)
        return f"C{match.group(1)}" if match else ""

    @staticmethod
    def _component_key(value):
        keys = []
        for token in re.split(r'\s*,\s*', value or ""):
            range_match = re.fullmatch(r'C(\d+)-C(\d+)', token.strip())
            if range_match:
                keys.extend(f"C{index}" for index in range(int(range_match.group(1)), int(range_match.group(2)) + 1))
                continue
            match = re.fullmatch(r'C(\d+(?:\.\d+)?)', token.strip())
            if match:
                keys.append(f"C{match.group(1)}")
        return keys

    def _pairs_for_object(self, obj, rows, rename_side_suffix):
        component_name = self._component_name(obj)
        scoped = [row for row in rows if component_name in self._component_key(row.component_name)] if component_name else []
        candidates = scoped or rows
        active_names = {group.name for group in obj.vertex_groups}
        pairs = {}
        collisions = set()
        from .games.wuthering_waves.skeleton_import import side_suffix_names
        suffixes = side_suffix_names([row.original_name for row in rows]) if rename_side_suffix else {}
        for row in candidates:
            if self.target == "NUMERIC":
                keys = {row.original_name, suffixes.get(row.original_name, row.original_name)}
                for key in keys:
                    previous = pairs.get(key)
                    if previous is not None and previous != row.numeric_id:
                        if not component_name:
                            continue
                        collisions.add(key)
                    else:
                        pairs[key] = row.numeric_id
            else:
                key = row.numeric_id
                value = suffixes.get(row.original_name, row.original_name) if rename_side_suffix else row.original_name
                previous = pairs.get(key)
                if previous is not None and previous != value:
                    if key in active_names:
                        collisions.add(key)
                else:
                    pairs[key] = value
        if collisions:
            preview = "、".join(sorted(collisions)[:3])
            scope = component_name or "当前网格"
            raise ValueError(f"{scope} 的映射仍有歧义：{preview}")
        return pairs

    @staticmethod
    def _numeric_sort_key(row):
        try:
            return (0, int(row.numeric_id), row.numeric_id)
        except ValueError:
            return (1, row.numeric_id)

    @staticmethod
    def _hierarchy_sort_key(rows, settings, source_folder):
        bones = _load_hierarchy_bones(settings, source_folder)
        bone_order = {bone.name: index for index, bone in enumerate(bones)}
        hierarchy_keys = {}
        for index, bone in enumerate(bones):
            ancestors = []
            current = index
            while 0 <= current < len(bones):
                ancestors.append(current)
                current = bones[current].parent
            hierarchy_keys[bone.name] = tuple(reversed(ancestors))
        return sorted(
            rows,
            key=lambda row: (0, hierarchy_keys[row.original_name], VELO_OT_wwmi_bone_map_toggle._numeric_sort_key(row))
            if row.original_name in bone_order
            else (1, row.original_name, VELO_OT_wwmi_bone_map_toggle._numeric_sort_key(row)),
        )

    def _sort_rows(self, settings):
        rows = list(settings.rows)
        if self.target == 'NUMERIC':
            ordered = sorted(rows, key=self._numeric_sort_key)
        else:
            cfg = getattr(bpy.context.scene, "VTWW_settings", None)
            source_folder = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
            ordered = self._hierarchy_sort_key(rows, settings, source_folder)
        if rows == ordered:
            return
        active = settings.active_row
        snapshot = [(row.numeric_id, row.original_name, row.component_name) for row in ordered]
        settings.rows.clear()
        for numeric_id, original_name, component_name in snapshot:
            row = settings.rows.add()
            row.numeric_id = numeric_id
            row.original_name = original_name
            row.component_name = component_name
        settings.active_row = min(active, max(0, len(snapshot) - 1))

    def execute(self, context):
        settings = context.scene.velo_wwmi_bone_map
        rows = [r for r in settings.rows if r.numeric_id and r.original_name]
        if not rows:
            self.report({'ERROR'}, iface_('The mapping table does not have complete numeric identifiers and original bone names'))
            return {'CANCELLED'}
        if self.target == 'ORIGINAL':
            try:
                cfg = getattr(context.scene, "VTWW_settings", None)
                source_folder = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
                self._hierarchy_sort_key(list(settings.rows), settings, source_folder)
            except Exception as exc:
                self.report({'ERROR'}, iface_('Mapping table reordering failed: {0}').format(exc))
                return {'CANCELLED'}
        for obj in _selected_meshes(context):
            try:
                pairs = self._pairs_for_object(obj, rows, settings.rename_side_suffix)
            except ValueError as exc:
                self.report({'ERROR'}, iface_(str(str(exc))))
                return {'CANCELLED'}
            renames = [(vg.name, pairs[vg.name]) for vg in obj.vertex_groups if vg.name in pairs and pairs[vg.name] != vg.name]
            used = {vg.name for vg in obj.vertex_groups}
            for old, new in renames:
                if new in used and new != old:
                    continue
                vg = obj.vertex_groups.get(old)
                if vg:
                    vg.name = f"__velo_tmp__{old}"
            for old, new in renames:
                vg = obj.vertex_groups.get(f"__velo_tmp__{old}")
                if vg:
                    vg.name = new
        try:
            self._sort_rows(settings)
        except Exception as exc:
            self.report({'ERROR'}, iface_('Mapping table reordering failed: {0}').format(exc))
            return {'CANCELLED'}
        settings.show_original = self.target == "ORIGINAL"
        return {'FINISHED'}


class VELO_UL_wwmi_bone_map(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "numeric_id", text="")
        arrow = row.row()
        arrow.ui_units_x = 1.6
        arrow.alignment = 'CENTER'
        arrow.label(text="→")
        row.prop(item, "original_name", text="")
        source_keys = VELO_OT_wwmi_bone_map_toggle._component_key(item.component_name)
        row.label(text=item.component_name if source_keys else "-")
        op = row.operator("velo.wwmi_bone_map_remove", text="", icon='REMOVE')
        op.index = index


class VELO_OT_wwmi_bone_map_remove(bpy.types.Operator):
    bl_idname = "velo.wwmi_bone_map_remove"
    bl_label = 'Delete Mapping Row'
    index: bpy.props.IntProperty()
    def execute(self, context):
        rows = context.scene.velo_wwmi_bone_map.rows
        if 0 <= self.index < len(rows): rows.remove(self.index)
        return {'FINISHED'}


class VELO_OT_wwmi_bone_map_add(bpy.types.Operator):
    bl_idname = "velo.wwmi_bone_map_add"
    bl_label = 'Add Mapping Row'
    def execute(self, context):
        context.scene.velo_wwmi_bone_map.rows.add()
        return {'FINISHED'}


class VELO_PT_wwmi_bone_map(bpy.types.Panel):
    bl_idname = "VELO_PT_wwmi_bone_map"
    bl_label = 'WWMI Numeric ID ↔ Original Bone Name'
    bl_space_type = 'VIEW_3D'; bl_region_type = 'UI'; bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'; bl_order = 1
    bl_options = {'DEFAULT_CLOSED'}
    @classmethod
    def poll(cls, context):
        s = getattr(context.scene, "velo_tools", None)
        return s is not None and s.active_tab == 'MATCH'
    def draw(self, context):
        layout = self.layout; settings = context.scene.velo_wwmi_bone_map
        layout.prop(settings, "unpack_folder")
        cfg = getattr(context.scene, "VTWW_settings", None)
        if cfg is not None:
            layout.prop(cfg, "object_source_folder", text='Object source directory')
        match_box = layout.box()
        match_box.prop(settings, "similarity_threshold")
        match_box.prop(settings, "voxel_size")
        row = layout.row(align=True)
        row.operator("velo.wwmi_bone_map_load_result", icon='IMPORT')
        row.operator("velo.wwmi_bone_map_save_result", icon='EXPORT')
        row = layout.row(align=True)
        row.operator("velo.wwmi_bone_map_generate", icon='SORTBYEXT')
        row.operator("velo.wwmi_skeleton_import", icon='ARMATURE_DATA')
        layout.operator("velo.wwmi_auto_bind", icon='ARMATURE_DATA')
        if cfg is not None:
            row = layout.row(align=True)
            row.prop(cfg, "mirror_mesh", text='Mirror Skeleton')
            row.prop(settings, "rename_side_suffix", text='Rename the skeleton with .L/.R suffix')
        layout.template_list("VELO_UL_wwmi_bone_map", "", settings, "rows", settings, "active_row", rows=8)
        layout.operator("velo.wwmi_bone_map_add", text='Add a new blank row', icon='ADD')
        row = layout.row(align=True)
        op = row.operator("velo.wwmi_bone_map_toggle", text='Switch to original name', icon='FONT_DATA'); op.target = 'ORIGINAL'
        op = row.operator("velo.wwmi_bone_map_toggle", text='Switch to numeric numbering', icon='SORT_ASC'); op.target = 'NUMERIC'
        layout.label(text='Renaming only affects currently selected mesh objects; unselected objects will not be modified.', icon='INFO')


_classes = (VELO_WWMI_BoneMapRow, VELO_WWMI_BoneMapSettings, VELO_OT_wwmi_bone_map_generate,
            VELO_OT_wwmi_auto_bind,
            VELO_OT_wwmi_skeleton_import, VELO_OT_wwmi_bone_map_load_result,
            VELO_OT_wwmi_bone_map_save_result,
            VELO_OT_wwmi_bone_map_toggle, VELO_UL_wwmi_bone_map, VELO_OT_wwmi_bone_map_remove,
            VELO_OT_wwmi_bone_map_add,
            VELO_PT_wwmi_bone_map)

def register():
    for cls in _classes: bpy.utils.register_class(cls)
    bpy.types.Scene.velo_wwmi_bone_map = bpy.props.PointerProperty(type=VELO_WWMI_BoneMapSettings)

def unregister():
    if hasattr(bpy.types.Scene, "velo_wwmi_bone_map"): del bpy.types.Scene.velo_wwmi_bone_map
    for cls in reversed(_classes):
        try: bpy.utils.unregister_class(cls)
        except Exception: pass
