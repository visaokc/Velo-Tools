"""Export hook for canonical, LOD-stable WWMI skeleton indices."""

import json
from pathlib import Path

from ..._wwmi_core.blender_export import blender_export as _be_module
from ..._wwmi_core.blender_export import ini_maker as _im_module

from .cross_scene import collect_active_lod_groups
from .mapping import LodMappingError, build_canonical_lod_map, make_map_buffer
from .runtime import write_runtime_assets


_INSTALLED = False
_BASE_BUILD_DATA_BUFFERS = None
_ORIG_BUILD_MOD_INI = None
_ORIG_BUILD_FROM_TEMPLATE = None
_ORIG_WRITE_FILES = None

_TEMPLATE_PATHS = {
    "MERGED": Path(__file__).parent / "templates" / "merged_lod.ini.j2",
    "COMPONENT": Path(__file__).parent / "templates" / "per_component_lod.ini.j2",
}


class LodExportError(Exception):
    pass


def install():
    global _INSTALLED, _BASE_BUILD_DATA_BUFFERS, _ORIG_BUILD_MOD_INI
    global _ORIG_BUILD_FROM_TEMPLATE, _ORIG_WRITE_FILES
    if _INSTALLED:
        return

    _BASE_BUILD_DATA_BUFFERS = _be_module.ModExporter.build_data_buffers
    _ORIG_BUILD_MOD_INI = _be_module.ModExporter.build_mod_ini
    _ORIG_BUILD_FROM_TEMPLATE = _im_module.IniMaker.build_from_template
    _ORIG_WRITE_FILES = _be_module.ModExporter.write_files

    def _wrapped_build_mod_ini(self):
        _prepare_lod_export(self)
        _ORIG_BUILD_MOD_INI(self)

    def _wrapped_build_from_template(self, context, cfg, template_string=None,
                                     with_checksum=False):
        if template_string is None and getattr(self.extracted_object, "velo_lods", None):
            template_string = _load_template(cfg)
        return _ORIG_BUILD_FROM_TEMPLATE(
            self,
            context,
            cfg,
            template_string=template_string,
            with_checksum=with_checksum,
        )

    def _wrapped_write_files(self):
        result = _ORIG_WRITE_FILES(self)
        if getattr(self.extracted_object, "velo_lods", None):
            write_runtime_assets(self.mod_output_folder)
        return result

    _wrapped_build_mod_ini._velo_lod_hook = True
    _wrapped_build_from_template._velo_lod_hook = True
    _wrapped_write_files._velo_lod_hook = True
    _be_module.ModExporter.build_mod_ini = _wrapped_build_mod_ini
    _im_module.IniMaker.build_from_template = _wrapped_build_from_template
    _be_module.ModExporter.write_files = _wrapped_write_files
    _INSTALLED = True


def remove():
    global _INSTALLED, _BASE_BUILD_DATA_BUFFERS, _ORIG_BUILD_MOD_INI
    global _ORIG_BUILD_FROM_TEMPLATE, _ORIG_WRITE_FILES
    if not _INSTALLED:
        return
    _be_module.ModExporter.build_mod_ini = _ORIG_BUILD_MOD_INI
    _im_module.IniMaker.build_from_template = _ORIG_BUILD_FROM_TEMPLATE
    _be_module.ModExporter.write_files = _ORIG_WRITE_FILES
    _BASE_BUILD_DATA_BUFFERS = None
    _ORIG_BUILD_MOD_INI = None
    _ORIG_BUILD_FROM_TEMPLATE = None
    _ORIG_WRITE_FILES = None
    _INSTALLED = False


def build_base_data_buffers(exporter):
    """Build a Cross-Scene unit before outer ShapeKey lifecycle wrappers."""
    builder = _BASE_BUILD_DATA_BUFFERS or _be_module.ModExporter.build_data_buffers
    return builder(exporter)


def _load_template(cfg) -> str:
    with open(_TEMPLATE_PATHS[cfg.mod_skeleton_type], "r", encoding="utf-8") as stream:
        source = stream.read()
    if cfg.comment_ini:
        return source
    return "".join(
        line + "\n"
        for line in source.split("\n")
        if not line.strip().startswith("{{note")
    )


def _prepare_lod_export(exporter):
    try:
        metadata = json.loads(
            (exporter.object_source_folder / "Metadata.json").read_text(encoding="utf-8"))
    except Exception:
        return
    excluded_names = set()
    manifest_path = exporter.object_source_folder / "CrossSceneManifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            excluded_names = {
                str((entry.get("split_route") or {}).get("split_object"))
                for entry in manifest.get("runtime_ibs") or []
                if entry.get("kind") == "own_buffer"
                and (entry.get("split_route") or {}).get("split_object")
            }
        except Exception:
            excluded_names = set()
    prepare_lod_export_memory(
        exporter,
        metadata=metadata,
        excluded_names=excluded_names,
        cleanup_stale=True,
    )


def prepare_lod_export_memory(exporter, *, metadata, excluded_names=(),
                              cleanup_stale=False):
    """Attach canonical LOD maps without changing the exported Blend buffer."""
    cfg = exporter.cfg
    if getattr(cfg, "custom_template_live_update", False):
        return
    if getattr(cfg, "use_custom_template", False):
        print("[LOD] Custom ini template active - LOD sections skipped.")
        return

    mode = str(getattr(cfg, "mod_skeleton_type", ""))
    if mode not in _TEMPLATE_PATHS:
        return

    merged_components = exporter.merged_object.components
    extracted_components = exporter.extracted_object.components
    exporter.extracted_object.velo_lods = []
    exporter.extracted_object.velo_lod_excluded_objects = sorted(
        str(value) for value in excluded_names or ())
    exporter.extracted_object.velo_draws = [
        [
            {
                "name": temp_object.name,
                "index_count": int(temp_object.index_count),
                "index_offset": int(temp_object.index_offset),
            }
            for temp_object in component.objects
        ]
        for component in merged_components
    ]

    active_component_ids = {
        component_id
        for component_id, component in enumerate(merged_components)
        if component.objects
    }
    lod_groups = collect_active_lod_groups(metadata, active_component_ids)
    if not lod_groups:
        return

    if cleanup_stale and hasattr(exporter, "meshes_path"):
        for pattern in ("BlendLOD*.buf", "CanonicalLodMap*.buf"):
            for stale in exporter.meshes_path.glob(pattern):
                stale.unlink()

    ordered_groups = sorted(
        lod_groups.items(),
        key=lambda item: (
            -sum(entry.get("vertex_count", 0) for entry in item[1].values()),
            item[0],
        ),
    )

    velo_lods = []
    try:
        for lod_index, (lod_object_name, entries) in enumerate(ordered_groups):
            level = lod_index + 1
            rendered_entries = [None] * len(extracted_components)
            for component_id, entry in sorted(entries.items()):
                if component_id >= len(merged_components):
                    continue
                if not merged_components[component_id].objects:
                    continue
                component = extracted_components[component_id]
                mapping = build_canonical_lod_map(
                    component_id,
                    level,
                    int(getattr(component, "vg_offset", 0)),
                    int(getattr(component, "vg_count", 0)),
                    entry,
                    merged=mode == "MERGED",
                    max_canonical_bones=(
                        512 if mode == "MERGED" and int(getattr(
                            exporter.merged_object, "blend_remap_count", 0)) > 0
                        else 256),
                )
                exporter.buffers[mapping.buffer_name] = make_map_buffer(mapping)
                rendered = dict(entry)
                rendered["canonical_map_buffer"] = mapping.buffer_name
                rendered["canonical_map_bone_count"] = mapping.bone_count
                rendered["canonical_destination_offset"] = (
                    mapping.destination_offset)
                rendered_entries[component_id] = rendered

            if not any(rendered_entries):
                continue
            first_entry = next(entry for entry in rendered_entries if entry is not None)
            velo_lods.append({
                "level": level,
                "lod_object_name": lod_object_name,
                "vb0_hash": first_entry["vb0_hash"],
                "components": rendered_entries,
            })
    except LodMappingError as error:
        raise LodExportError(f"LOD export failed: {error}") from error

    exporter.extracted_object.velo_lods = velo_lods
    print(
        f"[LOD] Prepared {len(velo_lods)} LOD level(s) with stable canonical "
        "Blend IDs and per-bone source maps.")
