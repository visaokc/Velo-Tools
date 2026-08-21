"""Monkey-patch hooks integrating ShapeKey into the EFMI-Tools pipeline.

Hooks (all reversible via remove_patches()):
1. `ModExporter.build_data_buffers` — isolate the base mesh from live Deform
   values, then collect control metadata after the official EFMI exporter
   builds its native ShapeKey buffers.
2. `IniMaker.build_from_template` — connect Velo's persistent controls to the
   official `CommandListSetShapeKey` runtime entrypoint.
3. `IniMaker.write` — remove shaders left by the retired pre-v0.6.2 pipeline.

The official EFMI v0.6.2 exporter owns delta buffers and runtime processing.
This compatibility layer only keeps Velo's detection, validation, and control
variables; it must not generate a second set of buffers or compute shaders.
"""
import os
import shutil
import sys
import traceback
from pathlib import Path

import bpy

from . import detector, baker, generator


_HLSL_DIR = Path(__file__).parent / "hlsl"
_SHAPEKEY_HLSL_NAMES = (
    "shapekey_blend.hlsl",
    "shapekey_blend_merged.hlsl",
)

# id(IniMaker class)  -> (cls, orig_build_from_template, orig_write, mod_name)
_patched_inimaker = {}
# id(ModExporter class) -> (cls, orig_build_data_buffers, mod_name)
_patched_exporter = {}

_SUPPORTED_EFMI_MODULE_PREFIXES = (
    "velo_tools.games.arknights_endfield._efmi_core.",
)

# Per-export-session bake state (cleared at start of each export_mod call).
_bake_results = []          # list of dicts from baker.bake_deform_keys
_components_meta = {}       # comp_id -> {"vertex_count": int}
_export_slot_map = None     # original Deform number -> exported shader slot
_shape_defaults = {}


def _get_required_hlsl_names():
    """Return the shader filenames required by the current baked results."""
    names = set()
    if any(bool(r.get("merge_buffers", False)) for r in _bake_results):
        names.add("shapekey_blend_merged.hlsl")
    if any(not bool(r.get("merge_buffers", False)) for r in _bake_results):
        names.add("shapekey_blend.hlsl")
    return sorted(names)


def _write_required_hlsl(src: Path, dst: Path):
    if src.name == "shapekey_blend_merged.hlsl":
        active_slots = generator.collect_active_shader_slots(_bake_results, merge_buffers=True)
        template_text = src.read_text(encoding="utf-8")
        dst.write_text(
            generator.render_merged_hlsl(template_text, active_slots),
            encoding="utf-8",
            newline="\n",
        )
        print(f"[ShapeKey] Wrote {src.name} with active slots: {active_slots} -> {dst}")
        return
    shutil.copy(src, dst)
    print(f"[ShapeKey] Copied {src.name} -> {dst}")


def _is_inimaker(cls):
    return (
        isinstance(cls, type)
        and cls.__name__ == "IniMaker"
        and hasattr(cls, "build_from_template")
        and hasattr(cls, "write")
    )


def _is_modexporter(cls):
    return (
        isinstance(cls, type)
        and cls.__name__ == "ModExporter"
        and hasattr(cls, "build_data_buffers")
        and hasattr(cls, "export_mod")
    )


def _find_classes(predicate, attr_name):
    found = []
    seen = set()
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if not _is_supported_efmi_module(name):
            continue
        cls = getattr(mod, attr_name, None)
        if cls is None or id(cls) in seen or not predicate(cls):
            continue
        seen.add(id(cls))
        found.append((name, cls))
    return found


def _is_supported_efmi_module(module_name: str) -> bool:
    return any(module_name.startswith(prefix) for prefix in _SUPPORTED_EFMI_MODULE_PREFIXES)


# ───────────────────────── shared helpers ─────────────────────────

def _settings_enabled():
    s = getattr(bpy.context.scene, "shapekey_settings", None)
    return s is not None and bool(s.enabled)


def _reset_state():
    global _bake_results, _components_meta, _export_slot_map, _shape_defaults
    _bake_results = []
    _components_meta = {}
    _export_slot_map = None
    _shape_defaults = {}


def _collect_shape_defaults_from_context(context):
    from .....core.export.shapekey_state import merge_shape_key_defaults

    mappings = []
    for item in _collect_all_deform_keys_from_context(context):
        mappings.append({int(item["slot"]): float(item["key_block"].value)})
    return merge_shape_key_defaults(mappings)


def _get_current_efmi_data_model(exporter):
    """Return the EFMI data model instance used by this exporter module."""
    module = sys.modules.get(type(exporter).__module__)
    data_models = getattr(module, "data_models", None) if module is not None else None
    if not isinstance(data_models, dict):
        return None
    return data_models.get("EFMI")


def _collect_all_deform_keys_from_context(context):
    cfg = getattr(context.scene, "VTEF_settings", None)
    coll = getattr(cfg, "component_collection", None) if cfg is not None else None
    if coll is None:
        return []

    all_keys = []
    for obj in coll.all_objects:
        if obj.type != 'MESH':
            continue
        keys = detector.collect_deform_keys(obj)
        if not keys:
            continue
        dups = detector.validate_no_duplicate_keys(keys)
        if dups:
            raise RuntimeError(detector.format_duplicate_message(dups))
        disagree = detector.validate_no_slot_name_disagreement(keys)
        if disagree:
            raise RuntimeError(
                detector.format_slot_name_disagreement_message(disagree))
        all_keys.extend(keys)
    return all_keys


def _get_export_slot_map(component_keys, merge_buffers):
    global _export_slot_map
    capacity = baker.slot_capacity(merge_buffers)
    if _export_slot_map is None:
        all_keys = _collect_all_deform_keys_from_context(bpy.context)
        source_keys = all_keys or component_keys
        _export_slot_map = baker.build_export_slot_map(source_keys, capacity)
        if _export_slot_map:
            pairs = ", ".join(
                f"Deform {slot}->{mapped}" for slot, mapped in sorted(_export_slot_map.items())
            )
            print(f"[ShapeKey] Export slot map: {pairs}")

    missing = sorted({int(d["slot"]) for d in component_keys} - set(_export_slot_map))
    if missing:
        if _bake_results:
            raise RuntimeError(
                "ShapeKey export slot map was incomplete after baking started; "
                f"late Deform slot(s): {missing}. Check component_collection export settings."
            )
        expanded = [{"slot": slot} for slot in sorted(set(_export_slot_map) | set(missing))]
        _export_slot_map = baker.build_export_slot_map(expanded, capacity)

    return _export_slot_map


def _early_validate_all_shape_keys(context):
    """Walk the configured component collection and validate ALL shape keys
    BEFORE any buffer / texture is written. Raises RuntimeError on the first
    failure with a user-readable message. No-op when ShapeKey export is off
    or no collection is set.
    """
    if not _settings_enabled():
        return
    cfg = getattr(context.scene, "VTEF_settings", None)
    coll = getattr(cfg, "component_collection", None) if cfg is not None else None
    if coll is None:
        return

    # 1. per-object: same (slot, name) appearing twice on one object.
    # 2. per-object: one Deform ID assigned to multiple data payloads.
    all_keys = _collect_all_deform_keys_from_context(context)

    if all_keys:
        from .....core.export.shapekey_state import merge_shape_key_defaults
        merge_shape_key_defaults(
            ({int(item["slot"]): float(item["key_block"].value)}
             for item in all_keys)
        )


# ───────────────────────── patched ModExporter ─────────────────────────

def _patched_export_mod(self, *args, **kwargs):
    """Run cross-collection validation BEFORE the original export_mod kicks
    off, so a naming bug aborts the export before any .buf / texture is
    written to disk."""
    entry = _patched_exporter.get(id(type(self)))
    orig_export = entry[3] if entry and len(entry) >= 4 else None
    try:
        _early_validate_all_shape_keys(bpy.context)
    except RuntimeError as e:
        # Surface the failure into Blender's INFO/ERROR area too.
        try:
            self.logger.error(str(e))  # type: ignore[attr-defined]
        except Exception:
            pass
        print(f"[ShapeKey] EARLY VALIDATION FAILED:\n{e}")
        raise
    if orig_export is None:
        return None
    return orig_export(self, *args, **kwargs)


def _patched_build_data_buffers(self, merged_object, component_id=-1):
    entry = _patched_exporter.get(id(type(self)))
    orig = entry[1] if entry else None
    if orig is None:
        return None

    # Reset state at the START of the first component (component_id=0 or -1).
    # ModExporter loops components in export_mod; we use component_id == 0 as
    # a sentinel for the first iteration. (-1 would mean "merged" mode.)
    if component_id <= 0:
        _reset_state()

    enabled = _settings_enabled()
    blender_obj = getattr(merged_object, "object", None)
    deform_keys = (
        detector.collect_deform_keys(blender_obj)
        if enabled and blender_obj is not None
        else []
    )
    if deform_keys:
        from .....core.export.shapekey_state import neutralized_shape_key_values

        with neutralized_shape_key_values(
            blender_obj,
            (item["key_block"] for item in deform_keys),
        ):
            result = orig(self, merged_object, component_id)
    else:
        result = orig(self, merged_object, component_id)

    if not enabled:
        return result

    if blender_obj is None or not getattr(blender_obj, "data", None):
        return result

    component = merged_object.components[0] if merged_object.components else None
    if component is None or component.shapekeys.vertex_count <= 0:
        return result

    seen = {(int(item["component_id"]), int(item["slot"])) for item in _bake_results}
    for item in deform_keys:
        key = (int(component_id), int(item["slot"]))
        if key in seen:
            continue
        seen.add(key)
        _bake_results.append({
            "component_id": int(component_id),
            "slot": int(item["slot"]),
            "name": str(item["name"]),
            "raw_name": str(item.get("raw_name") or item["key_block"].name),
            "default_value": float(item["key_block"].value),
        })

    return result


# ───────────────────────── patched IniMaker ─────────────────────────

def _format_control_value(value):
    return format(float(value), ".9g")


def _build_official_control_constants(items):
    by_slot = {}
    for item in items:
        by_slot.setdefault(int(item["slot"]), item)

    lines = ["", "; --- ShapeKey: persistent Velo controls for official EFMI runtime ---"]
    for slot, item in sorted(by_slot.items()):
        lines.append(f"; Deform {slot}: {item['raw_name']}")
        lines.append(f"global persist $ShapeKey_{slot} = {_format_control_value(item['default_value'])}")
    for item in sorted(items, key=lambda value: (int(value["component_id"]), int(value["slot"]))):
        component_id = int(item["component_id"])
        slot = int(item["slot"])
        lines.append(f"global $ShapeKeyLast_C{component_id}_S{slot} = -1")
    return lines


def _build_official_control_updates(items):
    lines = ["", "; --- ShapeKey: update official EFMI values when controls change ---"]
    for item in sorted(items, key=lambda value: (int(value["component_id"]), int(value["slot"]))):
        component_id = int(item["component_id"])
        slot = int(item["slot"])
        last_var = f"$ShapeKeyLast_C{component_id}_S{slot}"
        lines.extend([
            f"if {last_var} != $ShapeKey_{slot}",
            f"    $component_id = {component_id}",
            f"    $shapekey_id = {slot}",
            f"    $shapekey_value = $ShapeKey_{slot}",
            "    run = CommandListSetShapeKey",
            f"    {last_var} = $ShapeKey_{slot}",
            "endif",
        ])
    return lines

def _patched_build_from_template(self, context, cfg, template_string=None, with_checksum=False):
    entry = _patched_inimaker.get(id(type(self)))
    orig = entry[1] if entry else None
    if orig is None:
        return None

    # Render without checksum, post-process, then re-checksum.
    result = orig(self, context, cfg, template_string=template_string, with_checksum=False)

    if _settings_enabled() and _bake_results:
        try:
            cons_lines = _build_official_control_constants(_bake_results)
            if cons_lines:
                result = _inject_into_section(result, "[Constants]", cons_lines)
            pres_lines = _build_official_control_updates(_bake_results)
            if pres_lines:
                result = _inject_into_section(result, "[Present]", pres_lines)
            print(f"[ShapeKey] Connected {len(_bake_results)} control(s) to official EFMI ShapeKey runtime.")
        except Exception:
            print("[ShapeKey] INI injection failed:")
            traceback.print_exc()

    if with_checksum and hasattr(self, "with_checksum"):
        try:
            result = self.with_checksum(result)
        except Exception:
            print("[ShapeKey] Checksum re-add failed:")
            traceback.print_exc()

    self.ini_string = result
    return result


def _inject_into_section(ini_text, section_header, extra_lines):
    """Append `extra_lines` at the end of the contiguous body of the named
    section (just before the next [Header] or end of file). If the section is
    missing, append it at the end with the lines under it.
    """
    lines = ini_text.split("\n")
    section_start = None
    for i, ln in enumerate(lines):
        if ln.strip() == section_header:
            section_start = i
            break
    if section_start is None:
        # Section missing — append at end.
        return ini_text.rstrip() + "\n\n" + section_header + "\n" + "\n".join(extra_lines) + "\n"

    section_end = len(lines)
    for j in range(section_start + 1, len(lines)):
        s = lines[j].strip()
        if s.startswith("[") and s.endswith("]") and not s.startswith(";"):
            section_end = j
            break

    insert_at = section_end
    while insert_at > section_start + 1 and lines[insert_at - 1].strip() == "":
        insert_at -= 1

    new_lines = lines[:insert_at] + list(extra_lines) + lines[insert_at:]
    return "\n".join(new_lines)


def _patched_write(self, ini_string=None, ini_path=None):
    entry = _patched_inimaker.get(id(type(self)))
    orig = entry[2] if entry else None
    if orig is None:
        return None
    out = orig(self, ini_string=ini_string, ini_path=ini_path)

    try:
        if ini_path is None:
            mod_folder = Path(os.path.expandvars(os.path.expanduser(str(self.cfg.mod_output_folder))))
            ini_path = mod_folder / "mod.ini"
        hlsl_dst = Path(ini_path).parent / "hlsl"
        for name in _SHAPEKEY_HLSL_NAMES:
            stale = hlsl_dst / name
            if stale.exists():
                stale.unlink()
                print(f"[ShapeKey] Removed retired pre-v0.6.2 shader -> {stale}")
    except Exception:
        print("[ShapeKey] Stale HLSL cleanup failed:")
        traceback.print_exc()

    return out


# ───────────────────────── install / remove ─────────────────────────

def install_patches():
    new_count = 0

    for name, cls in _find_classes(_is_inimaker, "IniMaker"):
        if id(cls) in _patched_inimaker:
            continue
        _patched_inimaker[id(cls)] = (cls, cls.build_from_template, cls.write, name)
        cls.build_from_template = _patched_build_from_template
        cls.write = _patched_write
        new_count += 1
        print(f"[ShapeKey] Patched IniMaker in {name}")

    for name, cls in _find_classes(_is_modexporter, "ModExporter"):
        if id(cls) in _patched_exporter:
            continue
        # Tuple layout: (cls, orig_build_data_buffers, name, orig_export_mod)
        _patched_exporter[id(cls)] = (cls, cls.build_data_buffers, name, cls.export_mod)
        cls.build_data_buffers = _patched_build_data_buffers
        cls.export_mod = _patched_export_mod
        new_count += 1
        print(f"[ShapeKey] Patched ModExporter in {name}")

    if new_count == 0:
        # Try again on a delayed timer in case modules aren't loaded yet.
        bpy.app.timers.register(_retry_install, first_interval=0.5)


def _retry_install():
    install_patches()
    return None  # one-shot


def remove_patches():
    for entry in list(_patched_inimaker.values()):
        cls, orig_bft, orig_write, _ = entry
        try:
            cls.build_from_template = orig_bft
            cls.write = orig_write
        except Exception:
            pass
    _patched_inimaker.clear()

    for entry in list(_patched_exporter.values()):
        cls = entry[0]
        orig_bdb = entry[1]
        orig_export = entry[3] if len(entry) >= 4 else None
        try:
            cls.build_data_buffers = orig_bdb
            if orig_export is not None:
                cls.export_mod = orig_export
        except Exception:
            pass
    _patched_exporter.clear()
    _reset_state()
