"""Undoable Blender adapter for conservative repeated-field cleanup."""

import traceback

import bpy
import numpy as np

from velo_tools.i18n import iface_
from .shape_key_patterns import material_regions, plan_cleanup


_SNAPSHOT_LIMIT = 512 * 1024 * 1024


class CleanupWriteError(RuntimeError):
    def __init__(self, rollback_failed):
        super().__init__("ShapeKey cleanup write failed")
        self.rollback_failed = rollback_failed


def _unsupported_reason(obj):
    mesh = obj.data
    keys = mesh.shape_keys
    if mesh.users != 1 or (keys and keys.users != 1):
        return "shared"
    if not obj.is_editable or not mesh.is_editable or (keys and not keys.is_editable):
        return "readonly"
    if keys and (not keys.use_relative or keys.reference_key != keys.key_blocks[0]
                 or any(key.relative_key != keys.reference_key or key.vertex_group
                        for key in keys.key_blocks[1:])):
        return "relative"
    return None


def prepare_cleanup(objects):
    """Read and plan every object before writing any selected mesh."""
    pending = []
    skipped = []
    allocated = 0
    for obj in sorted(objects, key=lambda item: item.name):
        if obj.type != 'MESH' or not obj.data.shape_keys:
            continue
        reason = _unsupported_reason(obj)
        if reason:
            skipped.append((obj.name, reason))
            continue
        mesh = obj.data
        blocks = mesh.shape_keys.key_blocks
        size = len(blocks) * len(mesh.vertices) * 3 * np.dtype(np.float32).itemsize
        if size + allocated > _SNAPSHOT_LIMIT:
            skipped.append((obj.name, "size"))
            continue
        coordinates = np.empty((len(blocks), len(mesh.vertices), 3), dtype=np.float32)
        for index, key in enumerate(blocks):
            key.data.foreach_get('co', coordinates[index].ravel())
        vertices = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get('vertex_index', vertices)
        materials = np.empty(len(mesh.polygons), dtype=np.int32)
        loop_counts = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.polygons.foreach_get('material_index', materials)
        mesh.polygons.foreach_get('loop_total', loop_counts)
        regions = material_regions(len(mesh.vertices), vertices, np.repeat(materials, loop_counts))
        try:
            plan = plan_cleanup(coordinates, regions)
        except ValueError:
            skipped.append((obj.name, "invalid"))
            continue
        # Retain full snapshots only for meshes that will be written.
        snapshot = coordinates if plan.edits else None
        pending.append((obj, plan, snapshot))
        if snapshot is not None:
            allocated += size
    return pending, skipped


def _write_coordinates(key, coordinates):
    key.data.foreach_set('co', coordinates.ravel())


def _refresh(mesh):
    mesh.shape_keys.update_tag()
    mesh.update()


def apply_cleanup(pending):
    """Commit only planned coordinates, rolling back the entire batch on failure."""
    journal = []
    touched = set()
    try:
        for obj, plan, coordinates in pending:
            if not plan.edits:
                continue
            mesh = obj.data
            touched.add(mesh)
            for index, vertices in plan.edits.items():
                key = mesh.shape_keys.key_blocks[index]
                replacement = coordinates[index].copy()
                replacement[vertices] = coordinates[0, vertices]
                journal.append((key, coordinates[index]))
                _write_coordinates(key, replacement)
            _refresh(mesh)
    except Exception as exc:
        rollback_failed = False
        for key, original in reversed(journal):
            try:
                _write_coordinates(key, original)
            except Exception:
                rollback_failed = True
                traceback.print_exc()
        for mesh in touched:
            try:
                _refresh(mesh)
            except Exception:
                rollback_failed = True
                traceback.print_exc()
        raise CleanupWriteError(rollback_failed) from exc


class MESH_OT_clean_shape_key_contamination(bpy.types.Operator):
    bl_idname = "mesh.clean_shape_key_contamination"
    bl_label = "Clean ShapeKey Contamination"
    bl_description = (
        "Clean exact repeated material-region offsets on selected meshes without a reference copy. "
        "Preserve the source ShapeKey and different deformations; skip ambiguous sources. "
        "Intentional identical reuse cannot be distinguished automatically. Supports Undo"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and any(
            obj.type == 'MESH' and obj.data.shape_keys for obj in context.selected_objects
        )

    def execute(self, context):
        try:
            pending, skipped = prepare_cleanup(context.selected_objects)
            apply_cleanup(pending)
        except CleanupWriteError as exc:
            traceback.print_exc()
            if exc.rollback_failed:
                self.report({'ERROR'}, iface_(
                    "ShapeKey cleanup failed and rollback was incomplete; undo this operation"
                ))
                # Keep an undo step when a low-level failure prevented full rollback.
                return {'FINISHED'}
            self.report({'ERROR'}, iface_("ShapeKey cleanup failed; all changes were rolled back"))
            return {'CANCELLED'}
        except Exception:
            traceback.print_exc()
            self.report({'ERROR'}, iface_("ShapeKey cleanup analysis failed; no coordinates were changed"))
            return {'CANCELLED'}

        reasons = {
            "shared": "shared mesh or ShapeKey data",
            "readonly": "read-only data",
            "relative": "absolute, chained, or vertex-group-masked ShapeKeys",
            "size": "snapshot memory limit",
            "invalid": "invalid ShapeKey coordinates",
        }
        for name, reason in skipped:
            self.report({'INFO'}, iface_("Skipped {0}: {1}").format(name, iface_(reasons[reason])))
        changed = [(obj, plan) for obj, plan, _ in pending if plan.edits]
        ambiguous = sum(plan.skipped_patterns for _, plan, _ in pending)
        for obj, plan in changed:
            names = [obj.data.shape_keys.key_blocks[index].name for index in plan.sources]
            self.report({'INFO'}, iface_("{0}: protected source ShapeKeys: {1}").format(
                obj.name, ", ".join(names)
            ))
        if not changed:
            self.report({'INFO'}, iface_(
                "No safely identifiable repeated contamination to clean; skipped {0} ambiguous patterns and {1} meshes"
            ).format(ambiguous, len(skipped)))
            return {'CANCELLED'}
        self.report({'INFO'}, iface_(
            "Cleaned {0} meshes, {1} ShapeKeys, {2} vertex records; protected {3} source keys; "
            "skipped {4} ambiguous patterns and {5} meshes. Undo is available"
        ).format(len(changed), sum(len(plan.edits) for _, plan in changed),
                 sum(plan.vertex_edits for _, plan in changed),
                 sum(len(plan.sources) for _, plan in changed), ambiguous, len(skipped)))
        return {'FINISHED'}


def register():
    bpy.utils.register_class(MESH_OT_clean_shape_key_contamination)


def unregister():
    bpy.utils.unregister_class(MESH_OT_clean_shape_key_contamination)
