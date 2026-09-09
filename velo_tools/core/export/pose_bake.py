"""Bake disposable export meshes before vertex-group names lose rig bindings."""

import bpy
import numpy as np

from ...i18n import iface_


def bake_before_group_remap(context, obj, apply_modifiers):
    """Freeze the visible stack once, preserving relative ShapeKey coordinates."""
    if not apply_modifiers or not any(
        mod.type == 'ARMATURE' and mod.show_viewport and mod.object is not None
        for mod in obj.modifiers
    ):
        return
    keys = obj.data.shape_keys
    if keys is not None and not keys.use_relative:
        raise ValueError(iface_("Pose export requires relative ShapeKeys"))
    context.view_layer.update()
    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    visible = evaluated.to_mesh()
    try:
        expected = np.empty(len(visible.vertices) * 3, dtype=np.float32)
        visible.vertices.foreach_get('co', expected)
    finally:
        evaluated.to_mesh_clear()
    old_mesh = obj.data
    active_index = obj.active_shape_key_index
    show_only = obj.show_only_shape_key
    metadata = []
    if keys is not None:
        for key in keys.key_blocks:
            metadata.append({
                'name': key.name, 'value': key.value, 'mute': key.mute,
                'relative': key.relative_key.name,
                'slider_min': key.slider_min, 'slider_max': key.slider_max,
                'interpolation': key.interpolation,
            })
    if metadata:
        # Animation on the disposable key datablock must not override isolation.
        keys.animation_data_clear()
        for key in keys.key_blocks:
            key.slider_min = min(0.0, key.slider_min)
            key.value = 0.0
            key.mute = False
            key.slider_max = max(1.0, key.slider_max)
    baked = None
    coordinates = []
    try:
        # Evaluate Basis and each isolated relative contribution. Reconstruct the
        # original relative-key graph after baking its masks into the deltas.
        for index in range(max(1, len(metadata))):
            if metadata:
                obj.show_only_shape_key = False
                if index:
                    keys.key_blocks[index].value = 1.0
                if index > 1:
                    keys.key_blocks[index - 1].value = 0.0
            context.view_layer.update()
            depsgraph = context.evaluated_depsgraph_get()
            evaluated = obj.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(
                evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
            if baked is None:
                baked = mesh
            try:
                if len(mesh.vertices) != len(baked.vertices):
                    raise ValueError(iface_("Modifiers changed ShapeKey vertex counts during pose export"))
                if index:
                    values = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
                    mesh.vertices.foreach_get('co', values)
                    coordinates.append(values)
            finally:
                if mesh != baked:
                    bpy.data.meshes.remove(mesh)
        obj.data = baked
        if metadata:
            basis = np.empty(len(baked.vertices) * 3, dtype=np.float32)
            baked.vertices.foreach_get('co', basis)
            resolved = {metadata[0]['name']: basis}
            pending = list(range(1, len(metadata)))
            while pending:
                progress = []
                for index in pending:
                    relative = resolved.get(metadata[index]['relative'])
                    if relative is not None:
                        resolved[metadata[index]['name']] = relative + (coordinates[index - 1] - basis)
                        progress.append(index)
                if not progress:
                    raise ValueError(iface_("Cyclic relative ShapeKeys cannot be baked for pose export"))
                pending = [index for index in pending if index not in progress]
            for index, meta in enumerate(metadata):
                key = obj.shape_key_add(name=meta['name'], from_mix=False)
                if index:
                    key.data.foreach_set('co', resolved[meta['name']])
                key.slider_min = meta['slider_min']
                key.slider_max = meta['slider_max']
                key.value = meta['value']
                key.mute = meta['mute']
                key.interpolation = meta['interpolation']
                # The evaluated key already includes its original vertex-group mask.
                key.vertex_group = ''
            for key, meta in zip(obj.data.shape_keys.key_blocks, metadata):
                key.relative_key = obj.data.shape_keys.key_blocks[meta['relative']]
        # The downstream exporter must not apply the same deformation twice.
        # Hidden modifiers were not evaluated and must remain absent from output.
        obj.modifiers.clear()
        obj.show_only_shape_key = show_only
        obj.active_shape_key_index = active_index
        context.view_layer.update()
        evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
        visible = evaluated.to_mesh()
        try:
            actual = np.empty(len(visible.vertices) * 3, dtype=np.float32)
            visible.vertices.foreach_get('co', actual)
            if actual.shape != expected.shape or not np.allclose(actual, expected, atol=1e-6, rtol=1e-6):
                raise ValueError(iface_("Visible modifiers cannot preserve the current ShapeKey mix during pose export"))
        finally:
            evaluated.to_mesh_clear()
        if old_mesh.users == 0:
            bpy.data.meshes.remove(old_mesh)
    except Exception:
        obj.data = old_mesh
        if baked is not None and baked.users == 0:
            bpy.data.meshes.remove(baked)
        raise
    finally:
        obj.show_only_shape_key = show_only
        obj.active_shape_key_index = active_index
