"""Import-time binding for Component-local Endfield bone-name sidecars."""

from __future__ import annotations

import json
from pathlib import Path

import bpy

from .named_bone_mapping import (
    MAPPING_FILE_NAME,
    SKELETON_FILE_NAME,
    NamedBoneMappingError,
    component_name_maps,
    load_mapping,
)


def _collection_objects(collection):
    seen = set()
    pending = [collection]
    while pending:
        current = pending.pop()
        pending.extend(current.children)
        for obj in current.objects:
            if obj not in seen:
                seen.add(obj)
                yield obj


def _rename_imported_groups(source_folder: Path, collection, payload, dedupe_bones=True):
    try:
        metadata = json.loads((source_folder / "Metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NamedBoneMappingError(f"Cannot read Metadata.json for named import: {exc}") from exc
    renamed = 0
    for obj in _collection_objects(collection):
        if obj.type != "MESH" or "velo_component_id" not in obj:
            continue
        component_id = int(obj["velo_component_id"])
        local_to_name, _name_to_local, _ambiguous_names = component_name_maps(payload, component_id)
        try:
            local_to_global = {
                int(local): int(global_id)
                for local, global_id in (metadata["components"][component_id].get("vg_map") or {}).items()
            }
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise NamedBoneMappingError(f"Metadata.json has invalid Component {component_id} vg_map") from exc
        global_to_local = {}
        ambiguous_globals = set()
        for local_id, global_id in local_to_global.items():
            previous = global_to_local.get(global_id)
            if previous is not None and previous != local_id:
                ambiguous_globals.add(global_id)
            else:
                global_to_local[global_id] = local_id
        for global_id in ambiguous_globals:
            global_to_local.pop(global_id, None)
        pending = []
        for group in obj.vertex_groups:
            name = (group.name or "").strip()
            if not name.isdigit():
                continue
            global_id = int(name)
            if not dedupe_bones:
                local_id = global_id if global_id in local_to_name else None
            elif global_id in ambiguous_globals:
                raise NamedBoneMappingError(
                    f"Component {component_id} global group {global_id} cannot be uniquely restored to local"
                )
            else:
                local_id = global_to_local.get(global_id)
            bone_name = local_to_name.get(local_id) if local_id is not None else None
            if bone_name:
                pending.append((group, bone_name))
        for index, (group, _bone_name) in enumerate(pending):
            group.name = f"__named_bone_import_{index}"
        for group, bone_name in pending:
            group.name = bone_name
            renamed += 1
    return renamed


def _import_and_bind_skeleton(source_folder: Path, collection):
    skeleton_path = source_folder / SKELETON_FILE_NAME
    if not skeleton_path.is_file():
        raise NamedBoneMappingError(f"{MAPPING_FILE_NAME} exists but {SKELETON_FILE_NAME} is missing")
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=str(skeleton_path))
    imported = [obj for obj in bpy.data.objects if obj not in before]
    armatures = [obj for obj in imported if obj.type == "ARMATURE"]
    if not armatures:
        raise NamedBoneMappingError(f"{SKELETON_FILE_NAME} contains no Armature")
    if len(armatures) > 1:
        bpy.ops.object.select_all(action="DESELECT")
        for armature in armatures:
            armature.select_set(True)
        bpy.context.view_layer.objects.active = armatures[0]
        bpy.ops.object.join()
    armature = bpy.context.view_layer.objects.active if len(armatures) > 1 else armatures[0]
    armature.name = f"{source_folder.name} Named Skeleton"
    for obj in imported:
        if obj.type == "MESH":
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
    bound = 0
    for obj in _collection_objects(collection):
        if obj.type != "MESH" or "velo_component_id" not in obj:
            continue
        modifier = next((item for item in obj.modifiers if item.type == "ARMATURE"), None)
        if modifier is None:
            modifier = obj.modifiers.new(name="Armature", type="ARMATURE")
        modifier.object = armature
        matrix_world = obj.matrix_world.copy()
        obj.parent = armature
        obj.matrix_world = matrix_world
        bound += 1
    return armature, bound


def apply_after_merged_import(context):
    cfg = getattr(context.scene, "VTEF_settings", None)
    if cfg is None or getattr(cfg, "import_skeleton_type", "") != "MERGED":
        return None
    source_folder = Path(bpy.path.abspath(cfg.object_source_folder)).resolve()
    payload = load_mapping(source_folder)
    if payload is None:
        return None
    collection = getattr(cfg, "component_collection", None)
    if collection is None:
        raise NamedBoneMappingError("Merged import did not create a Component collection")
    renamed = _rename_imported_groups(
        source_folder,
        collection,
        payload,
        dedupe_bones=bool(getattr(cfg, "dedupe_bones", True)),
    )
    armature, bound = _import_and_bind_skeleton(source_folder, collection)
    return armature, renamed, bound
