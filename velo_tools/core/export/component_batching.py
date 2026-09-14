"""Scoped component assembly optimizations on disposable exporter meshes."""
from __future__ import annotations

from contextlib import contextmanager
import importlib

import bpy

from ...i18n import iface_


def _join_is_batchable(context, objects):
    if len(objects) < 3 or getattr(context, 'mode', None) != 'OBJECT':
        return False
    seen = set()
    for obj in objects:
        if obj.type != 'MESH' or obj.mode != 'OBJECT' or obj.library is not None:
            return False
        mesh = obj.data
        if (mesh.users != 1 or mesh.library is not None or not mesh.vertices
                or obj.hide_get() or obj.hide_viewport or obj.hide_select
                or not obj.visible_get(view_layer=context.view_layer)):
            return False
        if obj.constraints or obj.animation_data is not None or mesh.animation_data is not None:
            return False
        if any(modifier.type == 'MULTIRES' for modifier in obj.modifiers):
            return False
        if mesh.attributes.get('.sculpt_face_set') is not None:
            return False
        keys = mesh.shape_keys
        if keys is not None and (not keys.use_relative or keys.animation_data is not None):
            return False
        pointer = obj.as_pointer()
        if pointer in seen:
            return False
        seen.add(pointer)
    # Joining a parent changes the active object's transform during native Join.
    return not any(obj.parent is not None and obj.parent.as_pointer() in seen for obj in objects)


def _make_join(original, helpers):
    def join_objects(context, objects):
        if not _join_is_batchable(context, objects):
            return original(context, objects)
        unused_meshes = [obj.data for obj in objects[1:]]
        with helpers.OpenObject(context, objects[0], mode='OBJECT'):
            for obj in objects[1:]:
                helpers.select_object(obj)
            # Native Join consumes this collection in order, active mesh first.
            # Selection order alone does not define the exported vertex order.
            with context.temp_override(selected_objects=list(objects),
                                       selected_editable_objects=list(objects)):
                outcome = bpy.ops.object.join()
            if outcome != {'FINISHED'}:
                raise RuntimeError(iface_('Component mesh assembly did not finish'))
        for mesh in unused_meshes:
            helpers.remove_mesh(mesh)
    return join_objects


def _make_fill(original):
    def fill_gaps_in_vertex_groups(context, obj, internal_call=False):
        # Unified mapping already creates and orders the complete numeric range.
        # Preserve native handling for gaps, aliases, ignored and ambiguous names.
        if (obj.type == 'MESH' and obj.mode == 'OBJECT'
                and all(group.name == str(index) for index, group in enumerate(obj.vertex_groups))):
            return None
        return original(context, obj, internal_call=internal_call)
    return fill_gaps_in_vertex_groups


@contextmanager
def batch_component_operations():
    """Replace only merger-bound helpers for the lifetime of one export."""
    saved = []
    try:
        for game in ('arknights_endfield._efmi_core', 'wuthering_waves._wwmi_core'):
            prefix = 'velo_tools.games.' + game
            merger = importlib.import_module(prefix + '.blender_export.object_merger')
            helpers = importlib.import_module(prefix + '.migoto_io.blender_interface.objects')
            for name, wrapper in (('join_objects', lambda fn: _make_join(fn, helpers)),
                                  ('fill_gaps_in_vertex_groups', _make_fill)):
                original = getattr(merger, name)
                saved.append((merger, name, original))
                setattr(merger, name, wrapper(original))
        yield
    finally:
        for module, name, original in reversed(saved):
            setattr(module, name, original)
