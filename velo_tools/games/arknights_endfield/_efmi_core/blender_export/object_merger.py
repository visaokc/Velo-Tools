import re
import bpy

from typing import List
from dataclasses import dataclass, field
from enum import Enum

from ..addon.exceptions import ConfigError

from ..migoto_io.blender_interface.collections import *
from ..migoto_io.blender_interface.objects import *

from ..migoto_io.blender_tools.vertex_groups import fill_gaps_in_vertex_groups, remove_unused_vertex_groups
from ..migoto_io.blender_tools.modifiers import apply_modifiers_for_object_with_shape_keys

from ..migoto_io.object_extractor.migoto_object.metadata_format import ExtractedObject

try:
    from .....core.mapping import algorithms as _velo_map_algo
    from .....core.export import preexport as _velo_preexport
except Exception:
    _velo_map_algo = None
    _velo_preexport = None

try:
    from ...embedded.shapekey import detector as _velo_shapekey_detector
except Exception:
    _velo_shapekey_detector = None


_VELO_EXPORT_SUFFIX_RE = re.compile(r'__velo_export(?:\.\d{3})?$')


def _sanitize_export_object_name(name: str) -> str:
    text = (name or '').strip()
    if not text:
        return text
    return _VELO_EXPORT_SUFFIX_RE.sub('', text)


def _should_preserve_shape_keys_for_export(context: bpy.types.Context) -> bool:
    scene = getattr(context, 'scene', None)
    if scene is None:
        return False
    shapekey_settings = getattr(scene, 'shapekey_settings', None)
    return bool(getattr(shapekey_settings, 'enabled', False))


def _has_exportable_shape_keys(blender_object: bpy.types.Object) -> bool:
    if blender_object is None or not getattr(blender_object, 'data', None):
        return False
    shape_keys = getattr(blender_object.data, 'shape_keys', None)
    if shape_keys is None or len(shape_keys.key_blocks) == 0:
        return False
    if _velo_shapekey_detector is None:
        return True
    try:
        return bool(_velo_shapekey_detector.collect_deform_keys(blender_object))
    except Exception:
        return True


class SkeletonType(Enum):
    Merged = 'Merged'
    PerComponent = 'Per-Component'


@dataclass
class TempObject:
    name: str
    object: bpy.types.Object
    vertex_count: int = 0
    index_count: int = 0
    index_offset: int = 0


@dataclass
class MergedObjectComponent:
    objects: List[TempObject]
    id: int = 0
    vertex_count: int = 0
    index_count: int = 0
    blend_remap_id: int = -1
    blend_remap_vg_count: int = 0
    
    def get_object(self, object_name):
        for obj in self.objects:
            if obj.name == object_name:
                return obj


@dataclass
class MergedObjectShapeKeys:
    vertex_count: int = 0


@dataclass
class MergedObject:
    object: bpy.types.Object
    components: List[MergedObjectComponent]
    shapekeys: MergedObjectShapeKeys
    skeleton_type: SkeletonType
    vertex_count: int = 0
    index_count: int = 0
    vg_count: int = 0
    blend_remap_count: int = 0


@dataclass
class ObjectMerger:
    # Input
    context: bpy.types.Context
    extracted_object: ExtractedObject
    ignore_nested_collections: bool
    ignore_hidden_collections: bool
    ignore_hidden_objects: bool
    ignore_muted_shape_keys: bool
    apply_modifiers: bool
    collection: str
    skeleton_type: SkeletonType
    component_id: int = -1
    add_missing_vertex_groups: bool = False
    force_object_name: str =''
    allow_empty_components: bool = False
    fill_missing_mesh_data: bool = False
    # When True, the user imported components in MERGED mode (VG names are global
    # bone ids). Before the existing per-component pipeline runs, we translate VG
    # names back to this component's LOCAL bone ids using the inverse vg_map, and
    # drop VGs whose global id is not in this component's bone palette.
    vg_names_are_global: bool = False
    global_vg_count: int | None = None
    preserve_global_vgs: bool = False
    # Output
    merged_object: MergedObject = field(init=False)

    def __post_init__(self):
        collection_was_hidden = collection_is_hidden(self.collection)
        unhide_collection(self.collection)
        try:
            self.initialize_components()
            self.import_objects_from_collection()
            if len(self.components) == 1 and self.allow_empty_components and len(self.components[0].objects) == 0:
                self.merged_object = MergedObject(
                    object=None,
                    components=self.components,
                    vertex_count=0,
                    index_count=0,
                    vg_count=0,
                    shapekeys=None,
                    skeleton_type=SkeletonType.PerComponent,
                )
            else:
                self.finalize_temp_objects_geometry()
                if self.fill_missing_mesh_data:
                    self.fill_missing_temp_objects_data()
                self.finalize_temp_objects_data()
                self.finalize_temp_objects_stats()
                self.build_merged_object()
        except Exception:
            self.remove_temp_objects()
            raise
        finally:
            if collection_was_hidden:
                hide_collection(self.collection)

    def initialize_components(self):
        self.components = []
        for component_id, component in enumerate(self.extracted_object.components):
            if self.component_id == -1 or self.component_id == component_id:
                self.components.append(
                    MergedObjectComponent(
                        id=self.component_id if self.component_id != -1 else component_id,
                        objects=[],
                        index_count=0,
                    )
                )

    def import_objects_from_collection(self):

        num_objects = 0
        
        if self.force_object_name:
            component_pattern = re.compile(r'^{}'.format(self.force_object_name))
        elif self.component_id == -1:
            component_pattern = re.compile(r'.*component[_ -]*(\d+).*')
        else:
            component_pattern = re.compile(r'.*component[_ -]*({})(?!\d).*'.format(self.component_id))

        for obj in get_collection_objects(self.collection, 
                                          recursive = not self.ignore_nested_collections, 
                                          skip_hidden_collections = self.ignore_hidden_collections):

            if obj.type != 'MESH':
                continue

            if self.ignore_hidden_objects and object_is_hidden(obj):
                continue

            if obj.name.startswith('TEMP_'):
                continue
            
            if not self.force_object_name:
                match = component_pattern.findall(obj.name.lower())
                if len(match) == 0:
                    continue
                component_id = int(match[0])
            elif self.force_object_name == obj.name:
                component_id = 0
            else:
                continue

            if component_id >= len(self.extracted_object.components):
                raise ConfigError('object_source_folder', f'Metadata.json in specified folder is missing Component {component_id}!\nMost likely it contains sources for other object.')

            temp_obj = copy_object(self.context, obj, name=f'TEMP_{obj.name}', collection=self.collection)

            for component in self.components:
                if component.id != component_id:
                    continue
                component.objects.append(TempObject(
                    name=_sanitize_export_object_name(obj.name),
                    object=temp_obj,
                ))
                break

            num_objects += 1

        if num_objects == 0 and not self.allow_empty_components:
            raise ValueError(f'No eligible `Component {self.component_id}` objects found!')
        
        for component in self.components:
            component.objects.sort(key=lambda x: x.name)

    def finalize_temp_objects_geometry(self):
        for component_id, component in enumerate(self.components):
            for temp_object in component.objects:
                temp_obj = temp_object.object
                # Remove muted shape keys
                if self.ignore_muted_shape_keys and temp_obj.data.shape_keys:
                    muted_shape_keys = []
                    for shapekey_id in range(len(temp_obj.data.shape_keys.key_blocks)):
                        shape_key = temp_obj.data.shape_keys.key_blocks[shapekey_id]
                        if shape_key.mute:
                            muted_shape_keys.append(shape_key)
                    for shape_key in muted_shape_keys:
                        temp_obj.shape_key_remove(shape_key)
                # Modify temporary object
                with OpenObject(self.context, temp_obj, mode='OBJECT') as obj:
                    # Apply all transforms
                    bpy.ops.object.transform_apply(location = True, rotation = True, scale = True)
                    # Apply all modifiers
                    if self.apply_modifiers:
                        preserve_shape_keys = (
                            _should_preserve_shape_keys_for_export(self.context)
                            and _has_exportable_shape_keys(obj)
                        )
                        if preserve_shape_keys and obj.data.shape_keys and len(obj.data.shape_keys.key_blocks) > 0:
                            selected_modifiers = [modifier.name for modifier in obj.modifiers]
                            success, error_info = apply_modifiers_for_object_with_shape_keys(
                                self.context,
                                selected_modifiers,
                                disable_armatures=False,
                            )
                            if not success:
                                raise ConfigError(
                                    'component_collection',
                                    f'导出失败：物体 `{obj.name}` 在应用修改器并保留形态键时失败：\n{error_info}',
                                )
                        else:
                            bpy.ops.object.convert(target='MESH')
                    # Triangulate (this step is crucial since export supports only triangles)
                    triangulate_object(self.context, obj)

    def fill_missing_temp_objects_data(self):
        pass

    def finalize_temp_objects_data(self):
        for component in self.components:
            actual_component_id = component.id
            for temp_object in component.objects:
                temp_obj = temp_object.object
                self._prepare_temp_object_for_numeric_export(temp_obj)
                local_vg_count = self._get_component_local_vg_count(actual_component_id)
                # MERGED-import support: translate VG names from GLOBAL bone ids back
                # to this component's LOCAL bone ids using the inverse vg_map.
                # VGs whose global id is not in this component's bone palette are
                # dropped (warned if they carry weights). After translation the rest
                # of the pipeline runs exactly like a PerComponent-import flow.
                if self.vg_names_are_global and not self.preserve_global_vgs:
                    # Pre-clean: drop zero-weight VGs introduced by import-time
                    # full-palette injection (Step 3.5 in blender_import). This
                    # keeps the downstream stray-VG check honest: any VG that
                    # survives this pass and isn't in this component's vg_map
                    # MUST carry weights, which then triggers a hard error.
                    try:
                        remove_unused_vertex_groups(self.context, temp_obj)
                    except Exception as _e:
                        print(f'[Velo Tools Endfield][WARN] cleanup empty VGs failed for {temp_obj.name}: {_e}')
                    self._translate_global_vg_names_to_local(temp_obj, actual_component_id)
                elif not self.vg_names_are_global:
                    self._validate_per_component_vg_ids(temp_obj, actual_component_id, local_vg_count)
                # Handle Vertex Groups
                vertex_groups = get_vertex_groups(temp_obj)
                # Fill gaps in Vertex Groups list based on VG names (i.e. add group '1' between '0' and '2' if it's missing)
                if self.add_missing_vertex_groups:
                    fill_gaps_in_vertex_groups(self.context, temp_obj, internal_call=True)
                    if _velo_map_algo is not None:
                        _velo_map_algo.reorder_numeric_vertex_groups_first(temp_obj)
                # Remove ignored or unexpected vertex groups
                if self.skeleton_type == SkeletonType.Merged:
                    # Exclude VGs with 'ignore' tag or with higher VG id than total VG count from Metadata.ini
                    total_vg_count = self.global_vg_count
                    if total_vg_count is None:
                        total_vg_count = sum([component.vg_count for component in self.extracted_object.components])
                    ignore_list = [vg for vg in vertex_groups if 'ignore' in vg.name.lower() or vg.index >= total_vg_count]
                elif self.skeleton_type == SkeletonType.PerComponent:
                    # Exclude VGs with 'ignore' tag or with higher id VG count from Metadata.ini for current component
                    numeric_vg_ids = self._collect_numeric_vg_ids(temp_obj)
                    if not numeric_vg_ids:
                        raise ConfigError(
                            'component_collection',
                            (
                                f'导出失败：物体 `{temp_obj.name}` (Component {actual_component_id}) 上没有任何可识别的数字顶点组。\n'
                                '当前导出器要求部件对象至少包含数字编号顶点组。\n'
                                '如果这是从 MMD 素体分尸得到的部件，请先确保它们已按映射表转成统一编号，'
                                '或保持导入骨架类型为 MERGED 并使用映射表导出。'
                            ),
                        )
                    total_vg_count = local_vg_count if local_vg_count is not None else max(numeric_vg_ids) + 1
                    # total_vg_count = len(extracted_component.vg_map)
                    ignore_list = [vg for vg in vertex_groups if 'ignore' in vg.name.lower() or vg.index >= total_vg_count]
                remove_vertex_groups(temp_obj, ignore_list)
                # Rename VGs to their indicies to merge ones of different components together
                for vg in get_vertex_groups(temp_obj):
                    vg.name = str(vg.index)

    @staticmethod
    def _collect_numeric_vg_ids(temp_obj):
        result = []
        for vg in temp_obj.vertex_groups:
            name = (vg.name or '').strip()
            if name.isdigit():
                result.append(int(name))
        return result

    def _get_component_local_vg_count(self, component_id):
        if component_id < 0 or component_id >= len(self.extracted_object.components):
            return None
        extracted_component = self.extracted_object.components[component_id]
        try:
            vg_count = int(getattr(extracted_component, 'vg_count', 0) or 0)
        except (TypeError, ValueError):
            vg_count = 0
        if vg_count > 0:
            return vg_count
        local_ids = []
        for key in (getattr(extracted_component, 'vg_map', None) or {}).keys():
            try:
                local_ids.append(int(key))
            except (TypeError, ValueError):
                continue
        if local_ids:
            return max(local_ids) + 1
        return None

    @staticmethod
    def _vertex_group_has_weight(temp_obj, vg):
        if vg is None:
            return False
        try:
            for vertex in temp_obj.data.vertices:
                for group in vertex.groups:
                    if group.group == vg.index and group.weight > 1e-6:
                        return True
        except Exception:
            return False
        return False

    def _validate_per_component_vg_ids(self, temp_obj, component_id, local_vg_count):
        if local_vg_count is None:
            return
        invalid = []
        for vg in temp_obj.vertex_groups:
            name = (vg.name or '').strip()
            if not name.isdigit():
                continue
            vg_id = int(name)
            if vg_id < local_vg_count:
                continue
            if self._vertex_group_has_weight(temp_obj, vg):
                invalid.append(name)
        if not invalid:
            return
        preview = ', '.join(invalid[:12])
        if len(invalid) > 12:
            preview += f', ... (+{len(invalid) - 12})'
        raise ConfigError(
            'component_collection',
            (
                f"导出失败：物体 `{temp_obj.name}` (Component {component_id}) 当前选择的是 Per-Component 导出，"
                f"但存在超出该部件本地骨表范围 0..{local_vg_count - 1} 的带权数字顶点组：{preview}。\n"
                "这通常说明该网格是按 MERGED 模式导入/编辑的统一顶点组编号。"
                "请切回 Merged 导出，让导出器按 Metadata.json 的 vg_map 回译到部件独立编号；"
                "或者先手动把这些统一编号映射回当前 Component 的独立顶点组序列后再使用 Per-Component 导出。"
            ),
        )

    def _collapse_numeric_suffix_groups(self, temp_obj):
        if _velo_map_algo is None:
            return False
        rename_map = {}
        for vg in temp_obj.vertex_groups:
            name = (vg.name or '').strip()
            root = _velo_map_algo.strip_dup_suffix(name)
            if root != name and root.isdigit():
                rename_map[name] = root
        if not rename_map:
            return False
        _velo_map_algo.rename_and_reorder(
            temp_obj,
            rename_map,
            sort_renamed_numerically=True,
            untouched_to_end=True,
            save_backup=False,
            restore_backup=False,
        )
        return True

    def _prepare_temp_object_for_numeric_export(self, temp_obj):
        if temp_obj is None or temp_obj.type != 'MESH':
            return
        # Always normalize the temp clone. Mixed MMD + numeric families are a
        # valid authoring state, but PerComponent export expects the clone to be
        # fully numeric before palette validation runs.
        self._collapse_numeric_suffix_groups(temp_obj)
        if _velo_preexport is None:
            return
        scene = getattr(self.context, 'scene', None)
        ef = getattr(scene, 'velo_endfield', None) if scene is not None else None
        profile = getattr(ef, 'mmd_profile', None) if ef is not None else None
        if profile is None:
            return
        _velo_preexport.apply_mmd_pre_export(
            temp_obj,
            profile,
            drop_special=True,
            drop_empty=True,
            rename_to_unified=True,
        )
        self._collapse_numeric_suffix_groups(temp_obj)

    def _translate_global_vg_names_to_local(self, temp_obj, component_id):
        """Translate VG names from global bone ids back to local ids using the
        inverse of this component's vg_map. Used when components were imported in
        MERGED mode (cross-component unified naming). Drops VGs whose global id
        is not part of this component's bone palette; warns if dropped VGs carry
        non-trivial weights.
        """
        if component_id < 0 or component_id >= len(self.extracted_object.components):
            return
        extracted_component = self.extracted_object.components[component_id]
        vg_map = getattr(extracted_component, 'vg_map', None) or {}
        if not vg_map:
            return
        # vg_map: local_id -> global_id. Invert.
        # When multiple local ids map to the same global id (intra-component bone
        # palette duplication), the import-side dedup keeps the SMALLEST local
        # as canonical and merges weights into it. Mirror that choice here so the
        # round-trip is consistent.
        inverse = {}
        for k, v in vg_map.items():
            try:
                local_id = int(k)
                global_id = int(v)
            except (TypeError, ValueError):
                continue
            existing = inverse.get(global_id)
            if existing is None or local_id < existing:
                inverse[global_id] = local_id
        if not inverse:
            return

        to_remove = []
        # Two-pass rename: temporarily prefix all targets to avoid name collisions
        # against existing global-named VGs during the rename loop.
        TMP_PREFIX = '__vtef_tmp_local_'
        for vg in list(temp_obj.vertex_groups):
            name = vg.name
            if 'ignore' in name.lower():
                continue
            if not name.lstrip('-').isdigit():
                # Non-numeric VGs (user added) -- leave as-is, downstream will warn/drop.
                continue
            try:
                global_id = int(name)
            except ValueError:
                continue
            local_id = inverse.get(global_id)
            if local_id is None:
                to_remove.append(vg)
                continue
            vg.name = f'{TMP_PREFIX}{local_id}'
        # Strip the temp prefix.
        for vg in temp_obj.vertex_groups:
            if vg.name.startswith(TMP_PREFIX):
                vg.name = vg.name[len(TMP_PREFIX):]
        # Drop VGs that don't belong to this component's palette.
        # If a stray VG still carries weights AFTER the export-time empty-VG
        # cleanup, it means the user painted weights on a bone this component
        # was never supposed to use. We refuse to silently lose data: hard error.
        stray_with_weights = []
        for vg in to_remove:
            has_weight = False
            try:
                for v in temp_obj.data.vertices:
                    for g in v.groups:
                        if g.group == vg.index and g.weight > 1e-6:
                            has_weight = True
                            break
                    if has_weight:
                        break
            except Exception:
                pass
            if has_weight:
                stray_with_weights.append(vg.name)
            temp_obj.vertex_groups.remove(vg)
        if stray_with_weights:
            raise ConfigError(
                'object_source_folder',
                (
                    f"导出失败：物体 `{temp_obj.name}` (Component {component_id}) 上的顶点组 "
                    f"{stray_with_weights} 携带权重，但它们对应的全局骨骼 id 不在该部件的 vg_map 内。\n"
                    "可能原因：你把权重画到了原本不属于这个部件的骨骼上。\n"
                    "解决方法：要么把这些权重转移到该部件 vg_map 包含的骨骼上，要么把它们刷为零再导出。"
                )
            )

    def finalize_temp_objects_stats(self):
        index_offset = 0
        for component_id, component in enumerate(self.components):
            for temp_object in component.objects:
                temp_obj = temp_object.object
                # Calculate vertex count of temporary object
                temp_object.vertex_count = len(temp_obj.data.vertices)
                # Calculate index count of temporary object, IB stores 3 indices per triangle
                temp_object.index_count = len(temp_obj.data.polygons) * 3
                # Set index offset of temporary object to global index_offset
                temp_object.index_offset = index_offset
                # Update global index_offset
                index_offset += temp_object.index_count
                # Update vertex and index count of custom component
                component.vertex_count += temp_object.vertex_count
                component.index_count += temp_object.index_count

    def remove_temp_objects(self):
        for component_id, component in enumerate(self.components):
            for temp_object in component.objects:
                remove_mesh(temp_object.object.data)

    def build_merged_object(self):

        merged_object = []
        vertex_count, index_count = 0, 0
        for component in self.components:
            for temp_object in component.objects:
                merged_object.append(temp_object.object)
            vertex_count += component.vertex_count
            index_count += component.index_count
            
        join_objects(self.context, merged_object)

        obj = merged_object[0]

        rename_object(obj, 'TEMP_EXPORT_OBJECT')

        deselect_all_objects()
        select_object(obj)
        set_active_object(bpy.context, obj)

        self.merged_object = MergedObject(
            object=obj,
            components=self.components,
            vertex_count=len(obj.data.vertices),
            index_count=len(obj.data.polygons) * 3,
            vg_count=len(get_vertex_groups(obj)),
            shapekeys=MergedObjectShapeKeys(),
            skeleton_type=self.skeleton_type,
        )

        if vertex_count != self.merged_object.vertex_count:
            raise ValueError('vertex_count mismatch between merged object and its components')

        if index_count != self.merged_object.index_count:
            raise ValueError('index_count mismatch between merged object and its components')

