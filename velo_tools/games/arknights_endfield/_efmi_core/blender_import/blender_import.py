import time
from collections import defaultdict
from textwrap import dedent

import bpy

from ..addon.exceptions import ConfigError

from ..migoto_io.blender_interface.utility import *
from ..migoto_io.blender_interface.collections import *
from ..migoto_io.blender_interface.objects import *

from ..migoto_io.object_extractor.migoto_object.migoto_object import MigotoObject

from ..data_models.data_model_efmi import DataModelEFMI
from ... import vgmap as velo_vgmap


_PRESERVE_EMPTY_COLLECTION_KEY = "velo_preserve_empty_collection"
_COMPONENT_COLLECTION_KEY = "velo_component_id"


def _zh(text: str) -> str:
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return text


def _mark_preserve_empty_collection(collection):
    if collection is not None:
        collection[_PRESERVE_EMPTY_COLLECTION_KEY] = True
    return collection


def _mark_component_collection(collection, component_id):
    if collection is not None:
        collection[_COMPONENT_COLLECTION_KEY] = int(component_id)
        _mark_preserve_empty_collection(collection)
    return collection


def _reorder_vertex_groups_numerically(obj):
    if obj.type != "MESH" or not obj.vertex_groups:
        return
    snapshot = []
    for vg in obj.vertex_groups:
        try:
            key = (0, int(vg.name))
        except (ValueError, TypeError):
            key = (1, vg.index)
        snapshot.append((key, vg.name, vg.index))

    weights = {idx: [] for _, _, idx in snapshot}
    for vertex in obj.data.vertices:
        for group in vertex.groups:
            if group.group in weights:
                weights[group.group].append((vertex.index, group.weight))

    snapshot.sort(key=lambda item: item[0])
    while obj.vertex_groups:
        obj.vertex_groups.remove(obj.vertex_groups[0])
    for _key, name, old_idx in snapshot:
        new_vg = obj.vertex_groups.new(name=name)
        for vertex_idx, weight in weights[old_idx]:
            new_vg.add([vertex_idx], weight, "REPLACE")


def _apply_merged_vertex_group_names(obj, component_id, vertex_group_map, cfg):
    entry = vertex_group_map.components[component_id]
    local_to_global = {int(k): int(v) for k, v in entry.vg_map.items()}
    if not local_to_global:
        raise ConfigError(
            "object_source_folder",
            _zh(f"VertexGroupMap.json Component {component_id} 没有 vg_map，无法使用 Merged 导入。"),
        )

    global_to_local_names = defaultdict(list)
    for vg in obj.vertex_groups:
        try:
            local_id = int(vg.name)
        except (ValueError, TypeError):
            continue
        if local_id in local_to_global:
            global_to_local_names[local_to_global[local_id]].append(vg.name)

    for _global_id, names in global_to_local_names.items():
        if len(names) <= 1:
            continue
        canonical = obj.vertex_groups.get(names[0])
        if canonical is None:
            continue
        for dup_name in names[1:]:
            dup = obj.vertex_groups.get(dup_name)
            if dup is None:
                continue
            dup_idx = dup.index
            for vertex in obj.data.vertices:
                for group in vertex.groups:
                    if group.group == dup_idx:
                        canonical.add([vertex.index], group.weight, "ADD")
                        break
            obj.vertex_groups.remove(dup)

    tmp_prefix = "__vtef_tmp_g_"
    for vg in obj.vertex_groups:
        try:
            local_id = int(vg.name)
        except (ValueError, TypeError):
            continue
        global_id = local_to_global.get(local_id)
        if global_id is not None:
            vg.name = f"{tmp_prefix}{global_id}"
    for vg in obj.vertex_groups:
        if vg.name.startswith(tmp_prefix):
            vg.name = vg.name[len(tmp_prefix):]

    if not getattr(cfg, "skip_empty_vertex_groups", False):
        existing_names = {vg.name for vg in obj.vertex_groups}
        for global_id in velo_vgmap.global_palette(vertex_group_map):
            name = str(int(global_id))
            if name not in existing_names:
                obj.vertex_groups.new(name=name)
                existing_names.add(name)

    _reorder_vertex_groups_numerically(obj)


# TODO: Add support of import of unhandled semantics into vertex attributes
def import_object(
    context,
    cfg,
    collection_name: str,
    migoto_object: MigotoObject,
    extended_mesh_name: bool = False,
):
    model = DataModelEFMI()
    model.legacy_vertex_colors = cfg.color_storage == "LEGACY"

    if migoto_object.metadata.format_version < 3:
        cfg.last_error_setting_name = "object_source_folder"
        cfg.last_error_text = dedent(f"""
            Specified sources folder uses outdated data format `v{migoto_object.metadata.format_version}`!
            When used for mod export, it will not work correctly.
            Please extract data again from a new frame dump.
        """).strip()

    skeleton_mode = getattr(cfg, "import_skeleton_type", "COMPONENT")
    vertex_group_map = None
    if skeleton_mode == "MERGED":
        try:
            vertex_group_map = velo_vgmap.load_for_metadata(resolve_path(cfg.object_source_folder), migoto_object.metadata)
        except velo_vgmap.VertexGroupMapError as exc:
            raise ConfigError("object_source_folder", str(exc))

    imported_objects = []

    for component_id, component in enumerate(migoto_object.components):
        start_time = time.time()

        if extended_mesh_name:
            mesh_name = f"{component.metadata.mesh_name} {component.metadata.ib_hash}"
            if component.metadata.cpu_posed:
                mesh_name += " CPU-posed (only textures modding supported)"
        else:
            mesh_name = component.metadata.mesh_name

        mesh = bpy.data.meshes.new(mesh_name)
        obj = bpy.data.objects.new(mesh.name, mesh)

        model.set_data(
            obj=obj,
            mesh=mesh,
            index_buffer=component.mesh.index_buffer,
            vertex_buffer=component.mesh.vertex_buffer,
            vg_remap=None,
            mirror_mesh=cfg.mirror_mesh,
            mesh_scale=1.00,
            mesh_rotation=migoto_object.metadata.rotation.to_tuple(),
            import_tangent_data_to_attribute=cfg.import_tangent_data_to_attribute,
        )
        obj["velo_component_id"] = int(component_id)

        if skeleton_mode == "MERGED":
            _apply_merged_vertex_group_names(obj, component_id, vertex_group_map, cfg)

        imported_objects.append(obj)

        num_shapekeys = 0 if obj.data.shape_keys is None else len(getattr(obj.data.shape_keys, "key_blocks", []))

        print(f"{component.metadata.mesh_name} import time: {time.time()-start_time :.3f}s ({len(obj.data.vertices)} vertices, {len(obj.data.loops)} indices, {num_shapekeys} shapekeys)")

    col = _mark_preserve_empty_collection(new_collection(collection_name))
    component_children = {}
    for obj in imported_objects:
        if getattr(cfg, "import_as_component_collections", True):
            component_id = int(obj.get("velo_component_id"))
            target_col = component_children.get(component_id)
            if target_col is None:
                target_col = _mark_component_collection(new_collection(f"C{component_id}", col_parent=col), component_id)
                component_children[component_id] = target_col
            link_object_to_collection(obj, target_col)
        else:
            link_object_to_collection(obj, col)

    try:
        cfg.component_collection = col
        cfg.ignore_nested_collections = not getattr(cfg, "import_as_component_collections", True)
    except Exception:
        pass


def blender_import(operator, context, cfg):
    start_time = time.time()

    object_source_folder = resolve_path(cfg.object_source_folder)

    print(f"Object import started for '{object_source_folder.stem}' folder")

    if not object_source_folder.is_dir():
        raise ConfigError("object_source_folder", "Specified sources folder does not exist!")

    metadata_path = object_source_folder / "Metadata.json"
    if not metadata_path.is_file():
        raise ConfigError("object_source_folder", "Specified folder is missing Metadata.json!")

    try:
        migoto_object = MigotoObject.from_exported_files(object_source_folder, metadata_path)
    except Exception as e:
        raise ConfigError("object_source_folder", f"Failed to load object from sources folder:\n{e}")

    collection_name = object_source_folder.stem

    try:
        import_object(context, cfg, collection_name, migoto_object, extended_mesh_name=True)
    except Exception as e:
        raise ConfigError("object_source_folder", f"Failed to import object from sources folder:\n{e}")

    print(f"Total import time: {time.time() - start_time :.3f}s")
