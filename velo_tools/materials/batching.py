"""Stable material batching before native offsets and sequential mesh assembly."""
from __future__ import annotations

import json
from pathlib import Path


def stable_order(keys):
    """Group equal keys by first occurrence; None is an immovable barrier.

    Each bucket retains its original order, including the first object used as
    the native Join destination. No Component, mesh or name is changed here.
    """
    result, buckets = [], {}
    for index, key in enumerate(keys):
        if key is None:
            result.extend(item for bucket in buckets.values() for item in bucket)
            buckets.clear()
            result.append(index)
        else:
            buckets.setdefault(key, []).append(index)
    result.extend(item for bucket in buckets.values() for item in bucket)
    return result


def binding_runs(keys):
    previous = None
    total = 0
    for key in keys:
        if key is None:
            previous = None
        else:
            total += key != previous
            previous = key
    return total


def preserve_material_order(material):
    """Known alpha workflows and an explicit authoring opt-out are barriers.

    Blender preview state cannot prove every game pass opaque. The explicit
    property is needed for order-dependent game shaders absent from evidence.
    """
    from . import nodes
    if material is None or getattr(material, "material_texture_preserve_order", False):
        return True
    if getattr(material, "surface_render_method", "") == "BLENDED":
        return True
    if getattr(material, "blend_method", "") == "BLEND":
        return True
    assignment = nodes.assignment_node(material)
    if assignment is None:
        return True
    alpha = assignment.inputs.get("Alpha")
    return alpha is None or alpha.is_linked or float(alpha.default_value) < 1.0


def protected_components(folder, game):
    """Consume only positive, already-recorded transparency flags; never re-dump.

    Invalid optional evidence conservatively disables reordering, not export.
    Missing evidence does not override the authoring opt-out/alpha checks.
    """
    if game != "ENDFIELD":
        return set()
    path = Path(folder) / "CrossIB.json"
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not isinstance(data.get("components"), list):
            return None
        return {int(row["id"]) for row in data["components"]
                if row.get("is_actual_transparent") or row.get("is_translucent_without_texture")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def prepare_merger(merger, cfg, game, resolve_images, image_payload):
    """Stage complete permutations before changing temporary object lists.

    Called after material splitting, modifiers and VG translation, but before
    native statistics assign index offsets and before sequential native Join.
    Mixed-binding meshes remain intact and prevent movement across themselves.
    """
    import bpy
    from ..i18n import iface_

    folder = Path(bpy.path.abspath(cfg.object_source_folder))
    protected = protected_components(folder, game)
    catalogs, materials, image_resources = {}, {}, {}
    plans = []
    report = {"objects": 0, "reordered": 0, "runs_before": 0, "runs_after": 0, "barriers": 0}
    for index, component in enumerate(merger.components):
        comp = getattr(component, "id", index)
        original = list(component.objects)
        keys = []
        metadata = merger.extracted_object.components[comp]
        component_barrier = protected is None or comp in protected or bool(getattr(metadata, "cpu_posed", False))
        for temp in original:
            obj = temp.object
            used = {polygon.material_index for polygon in obj.data.polygons}
            signatures = set()
            blocked = component_barrier or not used
            for slot_id in sorted(used):
                if blocked:
                    break
                material = obj.material_slots[slot_id].material if slot_id < len(obj.material_slots) else None
                key = (comp, material.as_pointer() if material else 0)
                if key not in materials:
                    if preserve_material_order(material):
                        materials[key] = None
                    else:
                        replacements = {}
                        for identity, image in resolve_images(material, game, comp, folder, catalogs):
                            pointer = image.as_pointer()
                            if pointer not in image_resources:
                                image_resources[pointer] = image_payload(image)[0]
                            resource = image_resources[pointer]
                            if identity in replacements and replacements[identity] != resource:
                                raise ValueError(iface_("Two semantic inputs replace the same original texture differently"))
                            replacements[identity] = resource
                        materials[key] = tuple(sorted(replacements.items())) or None
                signature = materials[key]
                if signature is None:
                    blocked = True
                    break
                signatures.add(signature)
            keys.append(next(iter(signatures)) if not blocked and len(signatures) == 1 else None)
        order = stable_order(keys)
        arranged = [original[i] for i in order]
        report["objects"] += len(original)
        report["reordered"] += sum(left != right for left, right in enumerate(order))
        report["barriers"] += sum(key is None for key in keys)
        report["runs_before"] += binding_runs(keys)
        report["runs_after"] += binding_runs([keys[i] for i in order])
        plans.append((component, arranged))
    for component, arranged in plans:
        component.objects[:] = arranged
    merger.material_batch_report = report
    print("[MaterialBatching]", game, report)
    return report


def copy_uv_layer_snapshot(mesh, src_uv_layer_name, dst_uv_layer_name):
    """Read source values before adding a layer invalidates CustomData handles.

    Used by the native exporter's missing-UV fallback. Keeping a source RNA
    layer across uv_layers.new can access freed storage and corrupt the graph.
    """
    from array import array
    values = array("f", [0.0]) * (len(mesh.loops) * 2)
    mesh.uv_layers[src_uv_layer_name].data.foreach_get("uv", values)
    destination = mesh.uv_layers.new(name=dst_uv_layer_name)
    destination.data.foreach_set("uv", values)
