"""Monkey-patch hooks integrating ShapeKey into the EFMI-Tools pipeline.

Hooks (all reversible via remove_patches()):
1. `ModExporter.build_data_buffers` — bake position deltas immediately after
   the base produces VB0, while the merged mesh + shape keys are still alive.
2. `IniMaker.build_from_template` — append our generated INI section after
   the base renders mod.ini.
3. `IniMaker.write` — copy only the shader files needed by the current export
    mode into the mod's `hlsl/` subfolder.

A small `_state` dict keyed by `id(merged_object)` carries bake results
between hook 1 and hook 2.
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
    global _bake_results, _components_meta, _export_slot_map
    _bake_results = []
    _components_meta = {}
    _export_slot_map = None


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
        confs = detector.validate_no_name_conflicts(keys)
        if confs:
            raise RuntimeError(detector.format_conflict_message(confs))
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
    # 2. per-object: cross-slot name conflict (same name -> different slots).
    # 3. global: same Deform-N number wired to two different name parts.
    all_keys = _collect_all_deform_keys_from_context(context)

    if all_keys:
        disagree = detector.validate_no_slot_name_disagreement(all_keys)
        if disagree:
            raise RuntimeError(
                detector.format_slot_name_disagreement_message(disagree)
            )
        merge_buffers = True
        s2 = getattr(context.scene, "shapekey_settings", None)
        if s2 is not None:
            merge_buffers = bool(getattr(s2, "merge_buffers", True))
        baker.build_export_slot_map(all_keys, baker.slot_capacity(merge_buffers))


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

    result = orig(self, merged_object, component_id)

    if not _settings_enabled():
        return result

    blender_obj = getattr(merged_object, "object", None)
    if blender_obj is None or not getattr(blender_obj, "data", None):
        return result

    try:
        deform_keys = detector.collect_deform_keys(blender_obj)
        if not deform_keys:
            return result
        duplicates = detector.validate_no_duplicate_keys(deform_keys)
        if duplicates:
            msg = detector.format_duplicate_message(duplicates)
            print(f"[ShapeKey] {msg}")
            raise RuntimeError(msg)
        conflicts = detector.validate_no_name_conflicts(deform_keys)
        if conflicts:
            msg = detector.format_conflict_message(conflicts)
            print(f"[ShapeKey] {msg}")
            raise RuntimeError(msg)
        meshes_path = getattr(self, "meshes_path", None)
        if meshes_path is None:
            print("[ShapeKey] WARNING: ModExporter.meshes_path missing, cannot bake.")
            return result
        data_model = _get_current_efmi_data_model(self)
        if data_model is None:
            raise RuntimeError(
                "[ShapeKey] Missing current EFMI data_model instance; cannot read "
                "VB0->mesh VertexId mapping for shape-key bake."
            )
        vb0_vertex_ids = getattr(data_model, "last_export_vertex_ids", None)
        if vb0_vertex_ids is None:
            raise RuntimeError(
                "[ShapeKey] Missing VB0->mesh VertexId mapping from DataModelEFMI. "
                "Refusing to bake shape keys because POSITION0 fallback is ambiguous "
                "for duplicate Basis coordinates."
            )
        # Pull the freshly-built VB0 NumpyBuffer so baker can derive the
        # actual VB0 vertex count. VertexId provides the strict VB0 -> mesh map.
        vb0_key = f"Component{component_id}_VB0"
        vb0_buffer = None
        buffers = getattr(self, "buffers", None)
        if buffers is not None:
            vb0_buffer = buffers.get(vb0_key)
        if vb0_buffer is None:
            raise RuntimeError(
                f"[ShapeKey] Missing {vb0_key} in exporter buffers; cannot validate "
                "VB0->mesh VertexId mapping for shape-key bake."
            )
        # 透传 EFMI 导出的 mirror_mesh 设置；用户实际上几乎总会勾选镜像
        # 导入 + 镜像导出，否则 baker 算出的 delta 在 X 方向会反掉。
        mirror_mesh = False
        cfg = getattr(self, "cfg", None)
        if cfg is not None:
            mirror_mesh = bool(getattr(cfg, "mirror_mesh", False))
        # 读取 N 面板里的"合并 buf"开关；缺失时按默认 True 处理
        merge_buffers = True
        s2 = getattr(bpy.context.scene, "shapekey_settings", None)
        if s2 is not None:
            merge_buffers = bool(getattr(s2, "merge_buffers", True))
        slot_map = _get_export_slot_map(deform_keys, merge_buffers)
        baked = baker.bake_deform_keys(
            blender_obj, deform_keys, str(meshes_path), component_id,
            vb0_buffer=vb0_buffer, mirror_mesh=mirror_mesh,
            merge_buffers=merge_buffers, slot_map=slot_map,
            vb0_vertex_ids=vb0_vertex_ids,
        )
        _bake_results.extend(baked)
        if baked:
            # Use VB0 vertex count from the bake result (== actual VB0 size).
            _components_meta[component_id] = {
                "vertex_count": baked[0]["vertex_count"],
            }
    except RuntimeError:
        # Re-raise so EFMI shows it as a config error.
        raise
    except Exception:
        print("[ShapeKey] Baking failed:")
        traceback.print_exc()

    return result


# ───────────────────────── patched IniMaker ─────────────────────────

def _patched_build_from_template(self, context, cfg, template_string=None, with_checksum=False):
    entry = _patched_inimaker.get(id(type(self)))
    orig = entry[1] if entry else None
    if orig is None:
        return None

    # Render without checksum, post-process, then re-checksum.
    result = orig(self, context, cfg, template_string=template_string, with_checksum=False)

    if _settings_enabled() and _bake_results:
        # Cross-component validation runs OUTSIDE the try/except so a true
        # user-input error is not silently swallowed and printed -- it must
        # abort the export with a clear message.
        agg = [{"slot": int(r["slot"]), "name": r["name"]} for r in _bake_results]
        disagree = detector.validate_no_slot_name_disagreement(agg)
        if disagree:
            msg = detector.format_slot_name_disagreement_message(disagree)
            print(f"[ShapeKey] {msg}")
            raise RuntimeError(msg)
        try:
            # Inject [Constants] additions.
            cons_lines = generator.build_constants_lines(_bake_results)
            if cons_lines:
                result = _inject_into_section(result, "[Constants]", cons_lines)
            # Inject [Present] additions.
            pres_lines = generator.build_present_lines(_bake_results)
            if pres_lines:
                result = _inject_into_section(result, "[Present]", pres_lines)
            # Append all new resource + compute shader sections.
            appendix = generator.build_full_ini_appendix(_bake_results, _components_meta)
            if appendix:
                result = result.rstrip() + "\n" + appendix + "\n"
            print(f"[ShapeKey] Injected {len(_bake_results)} shape key(s) into mod.ini.")
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

    if not _settings_enabled() or not _bake_results:
        return out

    try:
        if ini_path is None:
            mod_folder = Path(os.path.expandvars(os.path.expanduser(str(self.cfg.mod_output_folder))))
            ini_path = mod_folder / "mod.ini"
        hlsl_dst = Path(ini_path).parent / "hlsl"
        hlsl_dst.mkdir(parents=True, exist_ok=True)
        required_names = set(_get_required_hlsl_names())
        for name in _SHAPEKEY_HLSL_NAMES:
            if name in required_names:
                continue
            stale = hlsl_dst / name
            if stale.exists():
                stale.unlink()
                print(f"[ShapeKey] Removed stale shader -> {stale}")
        for name in sorted(required_names):
            src = _HLSL_DIR / name
            if not src.exists():
                print(f"[ShapeKey] WARNING: missing shader file: {src}")
                continue
            dst = hlsl_dst / src.name
            _write_required_hlsl(src, dst)
    except Exception:
        print("[ShapeKey] HLSL copy failed:")
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
