"""Transactional material separation shared by host and game export paths."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

import bpy

from ..i18n import iface_
from .split_normals import capture_split_corner_normals, restore_split_corner_normals


_FACE_SLOT_ATTRIBUTE = "__material_split_source_slot"
_CONTEXT_COLLECTION = "__material_split_context"


def _unique_attribute_name(mesh, base: str) -> str:
    name = base
    suffix = 1
    while mesh.attributes.get(name) is not None:
        name = f"{base}.{suffix:03d}"
        suffix += 1
    return name


def _object_pointer(obj) -> int:
    try:
        return int(obj.as_pointer())
    except Exception:
        return id(obj)


def _mesh_pointer(mesh) -> int:
    try:
        return int(mesh.as_pointer())
    except Exception:
        return id(mesh)


def _remove_object(obj) -> None:
    mesh = getattr(obj, "data", None)
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    finally:
        if mesh is not None and getattr(mesh, "users", 1) == 0:
            bpy.data.meshes.remove(mesh)


def _remove_mesh(mesh) -> None:
    if mesh is not None and getattr(mesh, "users", 1) == 0:
        bpy.data.meshes.remove(mesh)


def _materialize_object_slots(obj) -> list[tuple[str, object]]:
    state = [(slot.link, slot.material) for slot in obj.material_slots]
    for index, (_link, material) in enumerate(state):
        obj.data.materials[index] = material
        obj.material_slots[index].link = "DATA"
    return state


def _restore_object_slots(obj, state: Iterable[tuple[str, object]]) -> None:
    for index, (link, material) in enumerate(state):
        if index >= len(obj.material_slots):
            break
        obj.material_slots[index].link = link
        obj.material_slots[index].material = material


def _face_slot_counts(mesh) -> Counter[int]:
    return Counter(int(poly.material_index) for poly in mesh.polygons)


def _write_face_slots(mesh, name: str) -> None:
    attribute = mesh.attributes.new(name, "INT", "FACE")
    for polygon, item in zip(mesh.polygons, attribute.data):
        item.value = int(polygon.material_index)


def _read_face_slots(mesh, name: str) -> tuple[int, ...]:
    attribute = mesh.attributes.get(name)
    if attribute is None or attribute.domain != "FACE" or attribute.data_type != "INT":
        raise RuntimeError(
            iface_("Material split failed for `{0}`: temporary face metadata was not preserved.")
            .format(mesh.name)
        )
    if len(attribute.data) != len(mesh.polygons):
        raise RuntimeError(
            iface_("Material split failed for `{0}`: temporary face metadata was not preserved.")
            .format(mesh.name)
        )
    return tuple(int(item.value) for item in attribute.data)


def _remove_face_slot_attribute(objects, name: str) -> None:
    seen = set()
    for obj in objects:
        mesh = getattr(obj, "data", None)
        if mesh is None:
            continue
        key = _mesh_pointer(mesh)
        if key in seen:
            continue
        seen.add(key)
        attribute = mesh.attributes.get(name)
        if attribute is not None:
            mesh.attributes.remove(attribute)


def _validate_fragments(obj, fragments, attribute_name: str, expected: Counter[int]) -> None:
    actual: Counter[int] = Counter()
    fragment_slots = []
    for fragment in fragments:
        mesh = getattr(fragment, "data", None)
        if mesh is None or len(mesh.polygons) == 0:
            raise RuntimeError(
                iface_("Material split failed for `{0}`: Blender produced an empty fragment.")
                .format(obj.name)
            )
        slots = _read_face_slots(mesh, attribute_name)
        unique = set(slots)
        if len(unique) != 1:
            raise RuntimeError(
                iface_("Material split failed for `{0}`: a fragment still contains multiple material slots.")
                .format(obj.name)
            )
        fragment_slots.append(next(iter(unique)))
        actual.update(slots)

    if actual != expected or Counter(fragment_slots) != Counter(expected.keys()):
        raise RuntimeError(
            iface_("Material split failed for `{0}`: Blender did not produce one complete fragment per used material slot.")
            .format(obj.name)
        )


def _context_collection(context, obj):
    """Give an excluded object one temporary visible path for native operators."""
    try:
        if obj.visible_get(view_layer=context.view_layer):
            return None
    except TypeError:
        if obj.visible_get():
            return None
    collection = bpy.data.collections.new(_CONTEXT_COLLECTION)
    context.scene.collection.children.link(collection)
    collection.objects.link(obj)
    context.view_layer.update()
    try:
        visible = obj.visible_get(view_layer=context.view_layer)
    except TypeError:
        visible = obj.visible_get()
    if not visible:
        context.scene.collection.children.unlink(collection)
        bpy.data.collections.remove(collection)
        raise RuntimeError(
            iface_("Material split failed for `{0}`: the object could not be made available to Blender's material separator.")
            .format(obj.name)
        )
    return collection


def _restore_context(context, active, selected, mode: str) -> None:
    try:
        if context.object is not None and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except RuntimeError:
        pass
    try:
        bpy.ops.object.select_all(action="DESELECT")
    except RuntimeError:
        pass
    for item in selected:
        try:
            if item.name in bpy.data.objects and item.visible_get() and not item.hide_select:
                item.select_set(True)
        except (ReferenceError, RuntimeError):
            pass
    try:
        if active is not None and active.name in bpy.data.objects:
            context.view_layer.objects.active = active
            if mode != "OBJECT":
                bpy.ops.object.mode_set(mode=mode)
    except (ReferenceError, RuntimeError):
        pass


def split_object_by_material(context, obj) -> list:
    """Separate one mesh by used material slot or raise without leaving partial data."""
    if obj is None or getattr(obj, "type", None) != "MESH" or obj.data is None:
        raise TypeError("split_object_by_material requires a mesh object")

    expected = _face_slot_counts(obj.data)
    if len(expected) <= 1:
        return [obj]

    active = context.view_layer.objects.active
    selected = tuple(context.selected_objects)
    mode = getattr(context.object, "mode", "OBJECT") if context.object else "OBJECT"
    original_links = tuple(obj.users_collection)
    original_hide_get = bool(obj.hide_get())
    original_hide_viewport = bool(obj.hide_viewport)
    original_hide_select = bool(obj.hide_select)
    original_active_key = int(obj.active_shape_key_index)
    original_mesh = obj.data
    shared_mesh = original_mesh.users > 1
    working_mesh = original_mesh.copy()
    obj.data = working_mesh
    slot_state = _materialize_object_slots(obj)
    before_objects = {_object_pointer(item) for item in bpy.data.objects}
    normal_attribute = None
    face_attribute = None
    temp_collection = None
    fragments = [obj]
    success = False

    try:
        face_attribute = _unique_attribute_name(working_mesh, _FACE_SLOT_ATTRIBUTE)
        _write_face_slots(working_mesh, face_attribute)
        normal_attribute = capture_split_corner_normals(working_mesh)

        try:
            if context.object is not None and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
        bpy.ops.object.select_all(action="DESELECT")
        obj.hide_viewport = False
        obj.hide_select = False
        obj.hide_set(False)
        temp_collection = _context_collection(context, obj)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        if obj.data.shape_keys:
            obj.active_shape_key_index = 0

        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        result = bpy.ops.mesh.separate(type="MATERIAL")
        bpy.ops.object.mode_set(mode="OBJECT")
        if result != {"FINISHED"}:
            raise RuntimeError(
                iface_("Material split failed for `{0}`: Blender cancelled the separation operation.")
                .format(obj.name)
            )

        fragments = [obj]
        fragments.extend(
            item for item in bpy.data.objects
            if _object_pointer(item) not in before_objects
            and getattr(item, "type", None) == "MESH"
        )
        _validate_fragments(obj, fragments, face_attribute, expected)

        for fragment in fragments:
            for collection in original_links:
                if fragment.name not in collection.objects:
                    collection.objects.link(fragment)
        _remove_face_slot_attribute(fragments, face_attribute)
        restore_split_corner_normals(fragments, normal_attribute)
        face_attribute = None
        normal_attribute = None

        for fragment in fragments:
            fragment.hide_viewport = original_hide_viewport
            fragment.hide_select = original_hide_select
            fragment.hide_set(original_hide_get)
            if fragment.data.shape_keys:
                fragment.active_shape_key_index = 0
        success = True
        return fragments
    finally:
        try:
            if context.object is not None and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass

        if temp_collection is not None:
            try:
                bpy.data.collections.remove(temp_collection)
            except (ReferenceError, RuntimeError):
                pass

        if not success:
            cleanup = {
                _object_pointer(fragment): fragment
                for fragment in fragments
                if fragment is not obj
            }
            cleanup.update({
                _object_pointer(item): item
                for item in bpy.data.objects
                if item is not obj
                and _object_pointer(item) not in before_objects
                and getattr(item, "type", None) == "MESH"
            })
            for fragment in cleanup.values():
                try:
                    _remove_object(fragment)
                except (ReferenceError, RuntimeError):
                    pass
            current_mesh = getattr(obj, "data", None)
            obj.data = original_mesh
            _remove_mesh(current_mesh)
            _restore_object_slots(obj, slot_state)
            obj.hide_viewport = original_hide_viewport
            obj.hide_select = original_hide_select
            obj.hide_set(original_hide_get)
            if obj.data.shape_keys:
                obj.active_shape_key_index = original_active_key
        elif not shared_mesh:
            _remove_mesh(original_mesh)

        _restore_context(context, active, selected, mode)
