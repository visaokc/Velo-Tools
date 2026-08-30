"""Operators for export preprocessing + handing off to the adapter export (V0.1.5)."""
from __future__ import annotations

from velo_tools.i18n import iface_

import bpy
from bpy.props import BoolProperty, StringProperty

from . import preexport, adapters


def _get_settings(context):
    return getattr(context.scene, "velo_endfield", None)


def _get_source(context):
    s = _get_settings(context)
    if s is None:
        return None
    obj = s.mmd_source_object
    if obj and obj.type == 'MESH':
        return obj
    return None


class VELO_OT_mmd_pre_export(bpy.types.Operator):
    bl_idname = "velo.mmd_pre_export"
    bl_label = 'Run MMD Export Preprocessing'
    bl_description = (
        'Perform on MMD source objects: Rename according to mapping to unify numbering → Delete specially named VGs → Delete VGs without weights. Used to clean up meshes to meet exporter constraints before invoking EFMI / WWMI for export.'
    )
    bl_options = {'REGISTER', 'UNDO'}

    drop_special: BoolProperty(name='Delete specially named VG', default=True)
    drop_empty: BoolProperty(name='Delete unweighted VG', default=True)
    rename_to_unified: BoolProperty(name='Rename to unified number', default=True)

    def execute(self, context):
        s = _get_settings(context)
        obj = _get_source(context)
        if s is None or obj is None:
            self.report({'ERROR'}, iface_('Please first specify the MMD source object at the top of the MMD↔Unified Numbering Mapping panel'))
            return {'CANCELLED'}
        profile = s.mmd_profile if s else None
        r = preexport.apply_mmd_pre_export(
            obj, profile,
            drop_special=self.drop_special,
            drop_empty=self.drop_empty,
            rename_to_unified=self.rename_to_unified,
        )
        try:
            from .. import overlay as _ov  # this file lives in core/export/, so go back up to the velo_tools package
        except Exception:
            _ov = None
        try:
            from ... import overlay as _ov2
            if hasattr(_ov2, "invalidate_mmd_cache"):
                _ov2.invalidate_mmd_cache()
        except Exception:
            pass
        self.report(
            {'INFO'},
            iface_('Preprocessing completed: Renamed {0}, Merged {1}, Removed special {2}, Removed empty {3}').format(r['renamed'], r['merged'], r['dropped_special'], r['dropped_empty']),
        )
        return {'FINISHED'}


class VELO_OT_invoke_game_export(bpy.types.Operator):
    bl_idname = "velo.invoke_game_export"
    bl_label = 'Preprocess and Run Target Exporter'
    bl_description = (
        'First perform export preprocessing on the MMD source object, then invoke the currently selected export adapter (EFMI / WWMI)'
    )
    bl_options = {'REGISTER'}

    do_preprocess: BoolProperty(name='Perform preprocessing first', default=True)

    def execute(self, context):
        s = _get_settings(context)
        if s is None:
            self.report({'ERROR'}, iface_('Arknights: Endfield mapping data not found'))
            return {'CANCELLED'}

        obj = _get_source(context)
        if self.do_preprocess:
            if obj is None:
                self.report({'ERROR'}, iface_("Please specify the MMD source object first (or cancel 'Preprocess First')"))
                return {'CANCELLED'}
            preexport.apply_mmd_pre_export(obj, s.mmd_profile)

        # single active-game switch: derive the adapter key from velo_tools.active_game via the game registry
        from ...games import registry as _registry
        desc = _registry.get_active_descriptor(context.scene)
        adapter_key = desc.adapter_key if desc is not None else "EFMI"
        is_avail, invoke = adapters.get_adapter(adapter_key)
        if not is_avail():
            self.report(
                {'ERROR'},
                iface_('Target Exporter {0} not detected, please install/enable the corresponding plugin and try again').format(adapter_key),
            )
            return {'CANCELLED'}
        result = invoke(context)
        if not result.get("ok"):
            self.report({'ERROR'}, result.get("msg", iface_('Call export failed')))
            return {'CANCELLED'}
        self.report({'INFO'}, result.get("msg", iface_('Export triggered')))
        return {'FINISHED'}


_classes = (VELO_OT_mmd_pre_export, VELO_OT_invoke_game_export)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
