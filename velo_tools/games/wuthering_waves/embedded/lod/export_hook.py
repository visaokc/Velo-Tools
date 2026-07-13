# Export hook: per-draw stateless LOD support for the stock WWMI mod export.
#
# Two class-attribute wrappers (same idempotent install()/remove() pattern as
# _shader_texture_usage.py; _wwmi_core sources untouched):
#
#   1. ModExporter.build_mod_ini — pre-work before the stock ini build: read
#      the per-component "lods" entries from Metadata.json, compute one
#      BlendLOD{n} buffer per LOD level (each vertex row holds its own
#      component's LOD-LOCAL 8-bit blend ids; LOD draws keep the game's
#      native bone constant buffers, so no skeleton machinery is needed) and
#      inject it into self.buffers (the stock write_files/templates then write
#      the file and the resource section for free), then attach the grouped
#      LOD data as extracted_object.velo_lods for the template.
#   2. IniMaker.build_from_template — when the exporter carries velo_lods and
#      no explicit/custom template is in play, substitute the velo fork
#      template (embedded/lod/templates/merged_lod.ini.j2), which factors the
#      per-component draws into shared [CommandListDrawComponent{c}] lists and
#      emits the per-draw LOD sections.
#
# Supported modes: MERGED (16-bit merged-id truth composed through the
# own-component inverse) and COMPONENT (Blend.buf is already component-local
# 8-bit — the matcher vg_map applies directly, no inverse). Per-Component
# (from Merged) flips mod_skeleton_type to COMPONENT during its inner stock
# export and therefore takes the COMPONENT path automatically. Note: a
# LodExportError raised here surfaces through export_mod's try/except as a
# ConfigError tagged 'use_custom_template' — the field hint is cosmetic, the
# message text is preserved verbatim.

import json
import traceback

from pathlib import Path

import numpy

from ..._wwmi_core.blender_export import blender_export as _be_module
from ..._wwmi_core.blender_export import ini_maker as _im_module
from ..._wwmi_core.migoto_io.data_model.byte_buffer import NumpyBuffer, AbstractSemantic, Semantic

from . import remap as _remap

_INSTALLED = False
_ORIG_BUILD_DATA_BUFFERS = None
_ORIG_BUILD_MOD_INI = None
_ORIG_BUILD_FROM_TEMPLATE = None

_TEMPLATE_PATHS = {
    'MERGED': Path(__file__).parent / 'templates' / 'merged_lod.ini.j2',
    'COMPONENT': Path(__file__).parent / 'templates' / 'per_component_lod.ini.j2',
}


class LodExportError(Exception):
    pass


def install():
    global _INSTALLED, _ORIG_BUILD_DATA_BUFFERS, _ORIG_BUILD_MOD_INI, _ORIG_BUILD_FROM_TEMPLATE
    if _INSTALLED:
        return

    _ORIG_BUILD_DATA_BUFFERS = _be_module.ModExporter.build_data_buffers
    _ORIG_BUILD_MOD_INI = _be_module.ModExporter.build_mod_ini
    _ORIG_BUILD_FROM_TEMPLATE = _im_module.IniMaker.build_from_template

    def _wrapped_build_data_buffers(self):
        result = _ORIG_BUILD_DATA_BUFFERS(self)
        _snapshot_lod_export_state(self)
        return result

    def _wrapped_build_mod_ini(self):
        _prepare_lod_export(self)
        _ORIG_BUILD_MOD_INI(self)

    def _wrapped_build_from_template(self, context, cfg, template_string=None, with_checksum=False):
        if template_string is None and getattr(self.extracted_object, 'velo_lods', None):
            template_string = _load_velo_template(cfg)
        return _ORIG_BUILD_FROM_TEMPLATE(self, context, cfg,
                                         template_string=template_string,
                                         with_checksum=with_checksum)

    _wrapped_build_data_buffers._velo_lod_hook = True
    _wrapped_build_mod_ini._velo_lod_hook = True
    _wrapped_build_from_template._velo_lod_hook = True
    _be_module.ModExporter.build_data_buffers = _wrapped_build_data_buffers
    _be_module.ModExporter.build_mod_ini = _wrapped_build_mod_ini
    _im_module.IniMaker.build_from_template = _wrapped_build_from_template
    _INSTALLED = True


def remove():
    global _INSTALLED, _ORIG_BUILD_DATA_BUFFERS, _ORIG_BUILD_MOD_INI, _ORIG_BUILD_FROM_TEMPLATE
    if not _INSTALLED:
        return
    _be_module.ModExporter.build_data_buffers = _ORIG_BUILD_DATA_BUFFERS
    _be_module.ModExporter.build_mod_ini = _ORIG_BUILD_MOD_INI
    _im_module.IniMaker.build_from_template = _ORIG_BUILD_FROM_TEMPLATE
    _ORIG_BUILD_DATA_BUFFERS = None
    _ORIG_BUILD_MOD_INI = None
    _ORIG_BUILD_FROM_TEMPLATE = None
    _INSTALLED = False


def _load_velo_template(cfg) -> str:
    """Loads the velo fork template, mirroring get_default_template's note stripping."""
    with open(_TEMPLATE_PATHS[cfg.mod_skeleton_type], 'r', encoding='utf-8') as f:
        raw_data = f.read()
    if cfg.comment_ini:
        return raw_data
    return ''.join(
        line + '\n' for line in raw_data.split('\n')
        if not line.strip().startswith('{{note')
    )


def _snapshot_lod_export_state(exporter):
    """Capture data that Blender invalidates before build_mod_ini runs."""
    try:
        exporter.extracted_object.velo_export_vg_names = [
            vg.name for vg in exporter.merged_object.object.vertex_groups
        ]
    except Exception:
        exporter.extracted_object.velo_export_vg_names = []


def _prepare_lod_export(exporter):
    try:
        with open(exporter.object_source_folder / 'Metadata.json', encoding='utf-8') as f:
            metadata = json.load(f)
    except Exception:
        return
    excluded_names = set()
    manifest_path = exporter.object_source_folder / 'CrossSceneManifest.json'
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            excluded_names = {
                str((entry.get('split_route') or {}).get('split_object'))
                for entry in manifest.get('runtime_ibs') or []
                if entry.get('kind') == 'own_buffer'
                and (entry.get('split_route') or {}).get('split_object')
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
    """Attach LOD IR and buffers without reading or deleting export folders."""
    cfg = exporter.cfg

    # Gates: silently / verbosely skip configurations out of scope.
    # (partial_export / write_ini gates are inherited: export_mod only calls
    # build_mod_ini when `not partial_export and write_ini`.)
    if getattr(cfg, 'custom_template_live_update', False):
        return
    if getattr(cfg, 'use_custom_template', False):
        print('[LOD] Custom ini template active — LOD sections skipped.')
        return
    full_components_meta = metadata.get('components') or []
    merged_components = exporter.merged_object.components
    exporter.extracted_object.velo_lods = []
    exporter.extracted_object.velo_lod_excluded_objects = sorted(
        str(value) for value in (excluded_names or ()))
    exporter.extracted_object.velo_draws = [
        [
            {
                'name': temp_obj.name,
                'index_count': int(temp_obj.index_count),
                'index_offset': int(temp_obj.index_offset),
            }
            for temp_obj in component.objects
        ]
        for component in merged_components
    ]
    lod_groups = {}  # lod_object_name -> {component_id: entry}
    for component_id, component_meta in enumerate(full_components_meta):
        for lod_entry in component_meta.get('lods') or []:
            name = lod_entry.get('lod_object_name')
            if name:
                lod_groups.setdefault(name, {})[component_id] = lod_entry
    if not lod_groups:
        if metadata.get('lods'):
            print('[LOD] Legacy top-level "lods" metadata found — the schema has moved into '
                  'components[].lods; re-run Extract LOD Data. LOD export skipped.')
        return

    if cfg.mod_skeleton_type not in _TEMPLATE_PATHS:
        print(f'[LOD] LOD data found, but skeleton mode {cfg.mod_skeleton_type} is not '
              f'supported — skipped.')
        return
    is_merged = cfg.mod_skeleton_type == 'MERGED'

    blend = exporter.buffers.get('Blend')
    index_buffer = exporter.buffers.get('Index')
    if blend is None or index_buffer is None:
        print('[LOD] Blend/Index buffers unavailable, LOD overrides skipped.')
        return

    if cleanup_stale and hasattr(exporter, 'meshes_path'):
        for stale in exporter.meshes_path.glob('BlendLOD*.buf'):
            stale.unlink()
        for stale in exporter.meshes_path.glob('VeloLod*.buf'):
            stale.unlink()

    if is_merged:
        # True merged ids: 16-bit truth when the merged object exceeds 256 VGs.
        vg16 = exporter.buffers.get('BlendRemapVertexVG')
        if vg16 is not None:
            ids_name = vg16.layout.get_element(AbstractSemantic(Semantic.Blendindices, 1)).get_name()
            true_vg_ids = vg16.get_field(ids_name)
        else:
            ids_name = blend.layout.get_element(AbstractSemantic(Semantic.Blendindices, 0)).get_name()
            true_vg_ids = blend.get_field(ids_name)
    else:
        # COMPONENT: Blend.buf ids are already component-local 8-bit truth.
        ids_name = blend.layout.get_element(AbstractSemantic(Semantic.Blendindices, 0)).get_name()
        true_vg_ids = blend.get_field(ids_name)

    weights_name = blend.layout.get_element(AbstractSemantic(Semantic.Blendweight, 0)).get_name()
    weights = blend.get_field(weights_name)

    index_data = index_buffer.get_field(0).ravel()
    component_object_ranges = [
        [
            (obj.name, obj.index_offset, obj.index_count)
            for obj in component.objects
        ]
        for component in merged_components
    ]
    component_ranges = [
        [
            (index_offset, index_count)
            for _name, index_offset, index_count in ranges
        ]
        for ranges in component_object_ranges
    ]
    if any(component_ranges):
        component_of_vertex = _remap.build_vertex_component_map_from_ranges(
            index_data, component_ranges, true_vg_ids.shape[0])
    else:
        index_layout = [component.index_count for component in exporter.merged_object.components]
        component_of_vertex = _remap.build_vertex_component_map(
            index_data, index_layout, true_vg_ids.shape[0])

    excluded_names = {str(value) for value in (excluded_names or ())}

    excluded_vertex_mask = None
    if excluded_names:
        excluded_vertex_mask = numpy.zeros(true_vg_ids.shape[0], dtype=bool)
        for component in exporter.merged_object.components:
            for temp_object in component.objects:
                if temp_object.name in excluded_names:
                    vertex_ids = numpy.unique(
                        index_data[temp_object.index_offset:
                                   temp_object.index_offset + temp_object.index_count])
                    excluded_vertex_mask[vertex_ids] = True

    # Level numbering: groups ordered by total matched vertex_count desc
    # (more retained geometry = nearer LOD level), name as deterministic tie-break.
    ordered_groups = sorted(
        lod_groups.items(),
        key=lambda item: (-sum(e.get('vertex_count', 0) for e in item[1].values()), item[0]),
    )

    velo_lods = []

    try:
        for lod_index, (lod_object_name, entries) in enumerate(ordered_groups):
            level = lod_index + 1

            component_maps = {}
            for component_id, entry in entries.items():
                if (component_id >= len(merged_components)
                        or len(merged_components[component_id].objects) == 0):
                    continue
                if is_merged:
                    component_maps[component_id] = _remap.compose_full_to_lod_local(
                        full_components_meta[component_id], entry)
                else:
                    export_vg_names = getattr(exporter.extracted_object, 'velo_export_vg_names', None)
                    if export_vg_names is None:
                        export_vg_names = [vg.name for vg in exporter.merged_object.object.vertex_groups]
                    component_maps[component_id] = _remap.compose_export_indices_to_lod_local(
                        export_vg_names,
                        entry)

            extra_excluded_names = _remap.find_unmapped_lod_object_names(
                index_data,
                component_object_ranges,
                true_vg_ids,
                weights,
                component_maps,
                excluded_names=excluded_names)
            if extra_excluded_names:
                excluded_names.update(extra_excluded_names)

            active_excluded_vertex_mask = excluded_vertex_mask
            if extra_excluded_names:
                active_excluded_vertex_mask = (
                    numpy.zeros(true_vg_ids.shape[0], dtype=bool)
                    if active_excluded_vertex_mask is None
                    else active_excluded_vertex_mask.copy()
                )
                for ranges in component_object_ranges:
                    for obj_name, index_offset, index_count in ranges:
                        if obj_name in extra_excluded_names:
                            vertex_ids = numpy.unique(
                                index_data[index_offset:index_offset + index_count])
                            active_excluded_vertex_mask[vertex_ids] = True
                excluded_vertex_mask = active_excluded_vertex_mask

            lod_ids = _remap.remap_blend_component_local(
                true_vg_ids, weights, component_of_vertex, component_maps,
                excluded_vertex_mask=active_excluded_vertex_mask)

            lod_blend = NumpyBuffer(blend.layout, data=blend.data.copy())
            lod_blend.set_field(
                blend.layout.get_element(AbstractSemantic(Semantic.Blendindices, 0)).get_name(),
                lod_ids)
            exporter.buffers[f'BlendLOD{level}'] = lod_blend

            first_entry = next(iter(entries.values()))
            velo_lods.append({
                'level': level,
                'lod_object_name': lod_object_name,
                'vb0_hash': first_entry['vb0_hash'],
                # Index-aligned list (entry | None) — template-friendly.
                'components': [entries.get(i) for i in range(len(full_components_meta))],
            })
    except _remap.LodRemapError as e:
        raise LodExportError(f'LOD export failed: {e}') from e

    exporter.extracted_object.velo_lods = velo_lods
    exporter.extracted_object.velo_lod_excluded_objects = sorted(excluded_names)

    total_sections = sum(
        1 for lod in velo_lods for entry in lod['components'] if entry is not None)
    print(f'[LOD] Prepared {len(velo_lods)} LOD level(s) '
          f'({total_sections} override sections, per-draw stateless).')
