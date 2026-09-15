"""Reversible, export-scope MMD preprocessing for host export operators.

Apply the current mapping table to every eligible mesh, including material
fragments. Bake all copies before unlinking any originals; never change tool
selections, live mapping tables, or source mesh data during export.
"""
from __future__ import annotations

from velo_tools.i18n import iface_

import json
import re
import sys
import traceback
from contextlib import nullcontext
from pathlib import Path

import bpy

from . import preexport as _pe
from .context_batching import batch_export_context
from .material_partition import material_routing_enabled
from .selection import (
    direct_collection_object_provider,
    export_object_collections,
    get_export_collection_objects,
)


# {id(cls): (cls, orig_execute, src_label)}
_PATCHED = {}

_COMPONENT_PATTERN = re.compile(r'.*component[_ -]*(\d+).*', re.IGNORECASE)


def _find_class(class_name: str):
    """Find a bpy.types.Operator subclass in sys.modules."""
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        cls = getattr(mod, class_name, None)
        if cls is None or not isinstance(cls, type):
            continue
        if not hasattr(cls, "execute"):
            continue
        return cls
    return None


def _object_is_hidden(obj):
    hide_get = getattr(obj, "hide_get", None)
    return bool(hide_get and hide_get())


def _object_component_id(obj):
    if obj is None:
        return None
    try:
        value = int(obj.get("velo_component_id"))
        if value >= 0:
            return value
    except (TypeError, ValueError):
        pass
    match = _COMPONENT_PATTERN.findall(getattr(obj, "name", "") or "")
    return int(match[0]) if match else None


def _mapping_source_component_id(context, obj):
    settings = getattr(context.scene, "velo_endfield", None)
    source = getattr(settings, "mmd_source_object", None) if settings is not None else None
    source_component_id = _object_component_id(source)
    if source_component_id is None or obj is None:
        return None
    if obj is source:
        return source_component_id
    object_text = str(obj.get("velo_mmd_text", "") or "")
    source_text = str(source.get("velo_mmd_text", "") or "") if source is not None else ""
    active_text = getattr(settings, "active_mmd_text", None) if settings is not None else None
    expected_text = source_text or getattr(active_text, "name", "")
    return source_component_id if expected_text and object_text == expected_text else None


def _iter_export_meshes(context, cfg):
    root = getattr(cfg, "component_collection", None) if cfg is not None else None
    if root is None:
        return []

    objects = get_export_collection_objects(
        context,
        root,
        recursive=not bool(getattr(cfg, "ignore_nested_collections", False)),
        skip_hidden_collections=bool(
            getattr(cfg, "ignore_hidden_collections", False)),
        object_provider=direct_collection_object_provider,
        skip_hidden_objects=bool(getattr(cfg, "ignore_hidden_objects", False)),
        hidden_predicate=_object_is_hidden,
    )
    return [
        obj for obj in objects
        if getattr(obj, "type", None) == 'MESH'
        and not (getattr(obj, "name", "") or "").startswith('TEMP_')
    ]


def _collect_export_component_ids(context, cfg):
    component_ids = set()
    for obj in _iter_export_meshes(context, cfg):
        match = _COMPONENT_PATTERN.findall((obj.name or '').lower())
        if not match:
            continue
        component_ids.add(int(match[0]))
    return sorted(component_ids)


def _validate_merged_export_preconditions(context, settings_attr: str):
    cfg = getattr(context.scene, settings_attr, None)
    mode = getattr(cfg, "mod_skeleton_type", None) if cfg is not None else None
    if mode not in {"MERGED", "MERGED_SKELETON"}:
        return None

    export_component_ids = _collect_export_component_ids(context, cfg)
    if not export_component_ids:
        return None

    source_folder = getattr(cfg, "object_source_folder", "") or ""
    if not source_folder:
        return None

    source_path = Path(bpy.path.abspath(source_folder))
    metadata_path = source_path / "Metadata.json"
    if not metadata_path.is_file():
        return None

    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    format_version = int(payload.get("format_version") or 0)
    components = payload.get("components") or []

    if format_version < 4:
        return (
            f"\u5f53\u524d Merged \u5bfc\u51fa\u4f7f\u7528\u65e7 Metadata v{format_version}\uff0c\u6ca1\u6709\u5b98\u65b9 component.vg_map\u3002"
            "\u8bf7\u7528 EFMI Tools v0.6.2+ \u91cd\u65b0\u63d0\u53d6 Metadata v4\u3002"
        )

    maps = [component.get("vg_map") or {} for component in components]

    invalid_components = []
    for component_id in export_component_ids:
        if component_id < 0 or component_id >= len(components):
            continue
        component = components[component_id] or {}
        cpu_posed = bool(component.get("cpu_posed", False)) if isinstance(component, dict) else False
        component_map = maps[component_id] if component_id < len(maps) else {}
        if not component_map and not cpu_posed:
            invalid_components.append(component_id)

    if not invalid_components:
        return None

    labels = ", ".join(f"C{component_id}" for component_id in invalid_components)
    return (
        f"\u5f53\u524d\u5bfc\u51fa\u6a21\u5f0f\u7f3a\u5c11 Metadata v4 \u4e2d {labels} \u7684 vg_map\u3002"
        "\u8bf7\u91cd\u65b0\u63d0\u53d6\u6216\u6539\u7528 Per-Component \u5bfc\u51fa\u3002"
    )

def _get_export_states(context, settings_attr: str):
    """Apply the current table across export scope, independent of selections."""
    from ..mapping.filters import is_special_vg_name
    from ..mapping.algorithms import build_mmd_to_unified
    settings = getattr(context.scene, "velo_endfield", None)
    cfg = getattr(context.scene, settings_attr, None)
    profile = getattr(settings, "mmd_profile", None)
    mapping = build_mmd_to_unified(profile)
    requests = []
    for obj in _iter_export_meshes(context, cfg):
        if any(g.name in mapping or is_special_vg_name(g.name) for g in obj.vertex_groups):
            requests.append((obj, profile))
    from .pose_bake import can_batch_pose_bakes, bake_before_group_remap_batch
    apply_modifiers = bool(getattr(cfg, 'apply_all_modifiers', False))
    use_batch = can_batch_pose_bakes([obj for obj, _profile in requests], apply_modifiers)
    states = []
    prepared = []
    try:
        for obj, profile in requests:
            state = _prepare_export_copy(
                context, cfg, obj, profile,
                defer_bake=use_batch,
                mapping_component_id=(
                    _mapping_source_component_id(context, obj)
                    if settings_attr == "VTEF_settings" else None
                ),
            )
            if state:
                states.append(state)
                prepared.append((state, profile))
        if use_batch:
            bake_before_group_remap_batch(context, [state['clone'] for state in states],
                                         apply_modifiers)
            for state, profile in prepared:
                _pe.apply_mmd_pre_export(state['clone'], profile)
        for state in states:
            _unlink_export_source(state)
        return states
    except BaseException:
        for state in reversed(states):
            _restore_export_state(state)
        raise



def _prepare_export_copy(
    context,
    cfg,
    obj,
    profile,
    *,
    defer_bake=False,
    mapping_component_id=None,
):
    """Bake and remap an independent copy while originals remain available."""
    target_col = getattr(cfg, "component_collection", None) if cfg is not None else None
    ignore_hidden_objects = bool(getattr(cfg, "ignore_hidden_objects", False)) if cfg is not None else False
    ignore_hidden_collections = bool(getattr(cfg, "ignore_hidden_collections", False)) if cfg is not None else False
    recursive = not bool(getattr(cfg, "ignore_nested_collections", False)) if cfg is not None else True

    if target_col is None:
        return None
    if ignore_hidden_objects and _object_is_hidden(obj):
        return None

    export_cols = export_object_collections(
        context,
        obj,
        target_col,
        recursive=recursive,
        skip_hidden_collections=ignore_hidden_collections,
    )
    if not export_cols:
        return None

    # Clone object + independent mesh
    clone = obj.copy()
    clone.data = obj.data.copy()
    clone.name = f"{obj.name}__export_copy"
    clone.data.name = f"{obj.data.name}__export_copy"
    from .ini_names import DISPLAY_NAME_KEY
    clone[DISPLAY_NAME_KEY] = obj.name
    try:
        del clone[_pe.BONE_MAPPING_COMPONENT_KEY]
    except KeyError:
        pass
    if mapping_component_id is not None:
        clone[_pe.BONE_MAPPING_COMPONENT_KEY] = int(mapping_component_id)

    # Link the clone into all the same collections as the source (keep the parent/child organization consistent)
    linked_to = []
    try:
        for c in export_cols:
            if clone.name not in c.objects:
                c.objects.link(clone)
                linked_to.append(c)
        # Bake while original rig bindings and dependency objects are present.
        from .pose_bake import bake_before_group_remap
        if not defer_bake:
            bake_before_group_remap(context, clone, bool(getattr(cfg, "apply_all_modifiers", False)))
            _pe.apply_mmd_pre_export(clone, profile)
    except Exception:
        _restore_export_state({"orig": obj, "clone": clone,
                               "linked_to": linked_to, "unlinked_from": []})
        raise

    return {
        "orig": obj,
        "clone": clone,
        "linked_to": linked_to,
        "unlinked_from": [],
    }


def _unlink_export_source(state):
    for collection in state["linked_to"]:
        obj = state["orig"]
        if obj.name in collection.objects:
            collection.objects.unlink(obj)
            state["unlinked_from"].append(collection)


def _restore_export_state(state):
    if not state:
        return
    obj = state["orig"]
    clone = state["clone"]
    # Re-link the source back into its original collections
    for c in state["unlinked_from"]:
        try:
            if obj.name not in c.objects:
                c.objects.link(obj)
        except Exception:
            traceback.print_exc()
    # Unlink the clone + delete the clone + clone mesh
    for c in state["linked_to"]:
        try:
            if clone.name in c.objects:
                c.objects.unlink(clone)
        except Exception:
            pass
    try:
        mesh = clone.data
        bpy.data.objects.remove(clone, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    except Exception:
        traceback.print_exc()


def _make_patched_execute(orig_execute, settings_attr: str, adapter_key: str = ""):
    def patched(self, context):
        states = []
        mesh_state = None
        use_material_routes = material_routing_enabled(
            getattr(context.scene, settings_attr, None))
        # EFMI validates the map source required by each unified export mode.
        # WWMI validates its own Metadata contract, so this gate is EFMI-only.
        validation_error = (None if adapter_key == "WWMI"
                            else _validate_merged_export_preconditions(context, settings_attr))
        if validation_error:
            print(f"[velo.export-hook] {validation_error}")
            try:
                self.report({'ERROR'}, iface_(str(validation_error)))
            except Exception:
                pass
            return {'CANCELLED'}
        try:
            from ...mesh import operators as _mesh_ops
        except Exception:
            traceback.print_exc()
            _mesh_ops = None
        transaction = (
            _mesh_ops.suspend_material_route_auto_refresh(
                context.scene, refresh_on_exit=use_material_routes)
            if _mesh_ops is not None else nullcontext()
        )
        with transaction, batch_export_context(context):
            try:
                try:
                    states = _get_export_states(context, settings_attr)
                    if _mesh_ops is not None and use_material_routes:
                        mesh_state = _mesh_ops.prepare_material_route_export(context)
                except Exception as exc:
                    traceback.print_exc()
                    self.report({'ERROR'}, iface_('Export preprocessing failed: {0}').format(str(exc)))
                    return {'CANCELLED'}
                return orig_execute(self, context)
            finally:
                try:
                    if _mesh_ops is not None:
                        _mesh_ops.restore_material_route_export(mesh_state)
                except Exception:
                    traceback.print_exc()
                try:
                    for state in reversed(states):
                        _restore_export_state(state)
                except Exception:
                    traceback.print_exc()

    return patched


def install_export_hook():
    from .pose_bake import install_merger_hooks
    install_merger_hooks()
    # Targets are derived from the game registry (single source of truth), one per game: export operator class name + its settings attr.
    # Idempotent: already-patched operator classes are skipped. Each game can hook in by calling this function after its driver registers the descriptor.
    try:
        from ...games import registry as _registry
        targets = [(d.export_op_class, d.settings_attr, d.adapter_key) for d in _registry.all_descriptors()]
    except Exception:
        targets = []
    for class_name, settings_attr, key in targets:
        cls = _find_class(class_name)
        if cls is None:
            print(f"[velo.export-hook] {class_name} not found, skip ({key} not loaded)")
            continue
        if id(cls) in _PATCHED:
            continue
        _PATCHED[id(cls)] = (cls, cls.execute, class_name)
        cls.execute = _make_patched_execute(cls.execute, settings_attr, key)
        print(f"[velo.export-hook] patched {class_name} (cfg={settings_attr})")


def remove_export_hook():
    from .pose_bake import remove_merger_hooks
    remove_merger_hooks()
    for cls_id, (cls, orig, label) in list(_PATCHED.items()):
        try:
            cls.execute = orig
            print(f"[velo.export-hook] restored {label}")
        except Exception:
            traceback.print_exc()
    _PATCHED.clear()
