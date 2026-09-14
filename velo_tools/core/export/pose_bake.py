"""Bake disposable export meshes before vertex-group names lose rig bindings."""

import bpy
import numpy as np

from ...i18n import iface_
from .shapekey_state import has_animation_inputs


_MERGER_PATCHES = []


def _bake_steps(obj, apply_modifiers, *, require_armature=True):
    """Freeze the visible stack once, preserving relative ShapeKey coordinates."""
    if not apply_modifiers or not any(
        mod.show_viewport and (not require_armature or
                              (mod.type == 'ARMATURE' and mod.object is not None))
        for mod in obj.modifiers
    ):
        return
    keys = obj.data.shape_keys
    if keys is not None and not keys.use_relative:
        raise ValueError(iface_("Pose export requires relative ShapeKeys"))
    depsgraph = yield
    evaluated = obj.evaluated_get(depsgraph)
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
            depsgraph = yield
            evaluated = obj.evaluated_get(depsgraph)
            if baked is None:
                baked = bpy.data.meshes.new_from_object(
                    evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
            else:
                # Only Basis needs a persistent mesh and all custom data layers.
                # Subsequent samples consume positions from the evaluated mesh.
                mesh = evaluated.to_mesh()
                try:
                    if len(mesh.vertices) != len(baked.vertices):
                        raise ValueError(iface_("Modifiers changed ShapeKey vertex counts during pose export"))
                    values = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
                    mesh.vertices.foreach_get('co', values)
                    coordinates.append(values)
                finally:
                    evaluated.to_mesh_clear()
        obj.data = baked
        if hasattr(old_mesh, "smooth_normal_color_enabled"):
            baked.smooth_normal_color_enabled = old_mesh.smooth_normal_color_enabled
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

        depsgraph = yield
        evaluated = obj.evaluated_get(depsgraph)
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
    except BaseException:
        obj.data = old_mesh
        if baked is not None and baked.users == 0:
            bpy.data.meshes.remove(baked)
        raise
    finally:
        obj.show_only_shape_key = show_only
        obj.active_shape_key_index = active_index



def _run_bake_steps(context, jobs):
    """Advance independent copies behind one native dependency-graph barrier."""
    active = []
    try:
        for job in jobs:
            try:
                next(job)
                active.append(job)
            except StopIteration:
                pass
        while active:
            context.view_layer.update()
            depsgraph = context.evaluated_depsgraph_get()
            pending = []
            for job in active:
                try:
                    job.send(depsgraph)
                    pending.append(job)
                except StopIteration:
                    pass
            active = pending
    finally:
        for job in reversed(jobs):
            job.close()


def bake_before_group_remap(context, obj, apply_modifiers, *, require_armature=True):
    """Preserve the serial API and its current-frame validation contract."""
    _run_bake_steps(context, [_bake_steps(obj, apply_modifiers,
                                       require_armature=require_armature)])


def _has_python_driver(block):
    animation = getattr(block, 'animation_data', None)
    return bool(animation and any(
        curve.driver.type == 'SCRIPTED' and not curve.driver.is_simple_expression
        for curve in animation.drivers))


def can_batch_pose_bakes(objects, apply_modifiers):
    """Fail closed for stacks that could read changing scene-wide state."""
    if not apply_modifiers or len(objects) < 2:
        return False
    local_types = {'ARMATURE', 'UV_WARP', 'MIRROR', 'ARRAY'}
    for obj in objects:
        if obj.type != 'MESH' or obj.mode != 'OBJECT' or obj.constraints:
            return False
        if has_animation_inputs(obj) or has_animation_inputs(obj.data):
            return False
        keys = obj.data.shape_keys
        if keys is not None and (not keys.use_relative or _has_python_driver(keys)):
            return False
        if obj.parent is not None and (obj.parent.type != 'ARMATURE'
                                       or _has_python_driver(obj.parent)):
            return False
        for modifier in obj.modifiers:
            if not modifier.show_viewport:
                continue
            if modifier.type not in local_types:
                return False
            if modifier.type == 'ARRAY' and modifier.fit_type != 'FIXED_COUNT':
                return False
            for attr in ('object', 'object_from', 'object_to', 'mirror_object',
                         'offset_object'):
                reference = getattr(modifier, attr, None)
                if reference is not None and (reference.type != 'ARMATURE'
                                               or _has_python_driver(reference)):
                    return False
    return True


def _sample_storage_estimate(obj):
    keys = obj.data.shape_keys
    key_count = len(keys.key_blocks) if keys is not None else 0
    expansion = 1
    for modifier in obj.modifiers:
        if not modifier.show_viewport:
            continue
        if modifier.type == 'MIRROR':
            expansion *= 2 ** sum(modifier.use_axis)
        elif modifier.type == 'ARRAY':
            expansion *= max(1, modifier.count)
    return len(obj.data.vertices) * expansion * 12 * (2 * key_count + 6)


def bake_before_group_remap_batch(context, objects, apply_modifiers, *, batch_size=64):
    """Synchronize independent copies in memory, using Blender's native workers.

    The sample-array budget is an estimate, not a limit on total Blender memory.
    A single oversized object retains the serial algorithm's memory requirements.
    """
    if batch_size < 1:
        raise ValueError('Batch size must be positive')
    batch_size = min(batch_size, 64)
    batch = []
    storage = 0
    for obj in objects:
        cost = _sample_storage_estimate(obj)
        if batch and (len(batch) >= batch_size or storage + cost > 128 * 1024**2):
            _run_bake_steps(context, [_bake_steps(item, apply_modifiers) for item in batch])
            batch, storage = [], 0
        batch.append(obj)
        storage += cost
    if batch:
        _run_bake_steps(context, [_bake_steps(item, apply_modifiers) for item in batch])


def install_merger_hooks():
    """Bake ShapeKey modifier stacks once on native disposable export objects."""
    if _MERGER_PATCHES:
        return
    from ...games.arknights_endfield._efmi_core.blender_export.blender_export import ObjectMergerEFMI
    from ...games.wuthering_waves._wwmi_core.blender_export.blender_export import ObjectMergerWWMI

    def wrap(original):
        def finalize_temp_objects_geometry(self):
            if self.apply_modifiers:
                for component in self.components:
                    for temp in component.objects:
                        if temp.object.data.shape_keys is not None:
                            bake_before_group_remap(self.context, temp.object, True, require_armature=False)
            return original(self)
        return finalize_temp_objects_geometry

    for cls in (ObjectMergerEFMI, ObjectMergerWWMI):
        original = cls.finalize_temp_objects_geometry
        cls.finalize_temp_objects_geometry = wrap(original)
        _MERGER_PATCHES.append((cls, original))


def remove_merger_hooks():
    for cls, original in reversed(_MERGER_PATCHES):
        cls.finalize_temp_objects_geometry = original
    _MERGER_PATCHES.clear()
