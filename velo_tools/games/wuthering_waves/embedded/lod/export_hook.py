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
# Round scope: MERGED skeleton mode only (COMPONENT-mode support would be a
# trivial future addition: its Blend.buf is already component-local). Note:
# a LodExportError raised here surfaces through export_mod's try/except as a
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
_ORIG_BUILD_MOD_INI = None
_ORIG_BUILD_FROM_TEMPLATE = None

_TEMPLATE_PATH = Path(__file__).parent / 'templates' / 'merged_lod.ini.j2'


class LodExportError(Exception):
    pass


def install():
    global _INSTALLED, _ORIG_BUILD_MOD_INI, _ORIG_BUILD_FROM_TEMPLATE
    if _INSTALLED:
        return

    _ORIG_BUILD_MOD_INI = _be_module.ModExporter.build_mod_ini
    _ORIG_BUILD_FROM_TEMPLATE = _im_module.IniMaker.build_from_template

    def _wrapped_build_mod_ini(self):
        _prepare_lod_export(self)
        _ORIG_BUILD_MOD_INI(self)

    def _wrapped_build_from_template(self, context, cfg, template_string=None, with_checksum=False):
        if template_string is None and getattr(self.extracted_object, 'velo_lods', None):
            template_string = _load_velo_template(cfg)
        return _ORIG_BUILD_FROM_TEMPLATE(self, context, cfg,
                                         template_string=template_string,
                                         with_checksum=with_checksum)

    _wrapped_build_mod_ini._velo_lod_hook = True
    _wrapped_build_from_template._velo_lod_hook = True
    _be_module.ModExporter.build_mod_ini = _wrapped_build_mod_ini
    _im_module.IniMaker.build_from_template = _wrapped_build_from_template
    _INSTALLED = True


def remove():
    global _INSTALLED, _ORIG_BUILD_MOD_INI, _ORIG_BUILD_FROM_TEMPLATE
    if not _INSTALLED:
        return
    _be_module.ModExporter.build_mod_ini = _ORIG_BUILD_MOD_INI
    _im_module.IniMaker.build_from_template = _ORIG_BUILD_FROM_TEMPLATE
    _ORIG_BUILD_MOD_INI = None
    _ORIG_BUILD_FROM_TEMPLATE = None
    _INSTALLED = False


def _load_velo_template(cfg) -> str:
    """Loads the velo fork template, mirroring get_default_template's note stripping."""
    with open(_TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        raw_data = f.read()
    if cfg.comment_ini:
        return raw_data
    return ''.join(
        line + '\n' for line in raw_data.split('\n')
        if not line.strip().startswith('{{note')
    )


def _prepare_lod_export(exporter):
    cfg = exporter.cfg

    # Gates: silently / verbosely skip configurations out of scope.
    # (partial_export / write_ini gates are inherited: export_mod only calls
    # build_mod_ini when `not partial_export and write_ini`.)
    if getattr(cfg, 'custom_template_live_update', False):
        return
    if getattr(cfg, 'use_custom_template', False):
        print('[LOD] Custom ini template active — LOD sections skipped.')
        return
    if (exporter.object_source_folder / 'CrossSceneRouting.json').is_file():
        print('[LOD] Cross-scene export detected, LOD overrides are not supported yet — skipped.')
        return

    try:
        with open(exporter.object_source_folder / 'Metadata.json', encoding='utf-8') as f:
            metadata = json.load(f)
    except Exception:
        return

    full_components_meta = metadata.get('components') or []
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

    if cfg.mod_skeleton_type != 'MERGED':
        print('[LOD] LOD data found, but LOD export currently supports the MERGED skeleton '
              'mode only — skipped.')
        return

    blend = exporter.buffers.get('Blend')
    index_buffer = exporter.buffers.get('Index')
    if blend is None or index_buffer is None:
        print('[LOD] Blend/Index buffers unavailable, LOD overrides skipped.')
        return

    # Regenerate LOD buffers from scratch (incl. legacy round-2 leftovers).
    for stale in exporter.meshes_path.glob('BlendLOD*.buf'):
        stale.unlink()
    for stale in exporter.meshes_path.glob('VeloLod*.buf'):
        stale.unlink()

    # True merged ids: 16-bit truth when the merged object exceeds 256 VGs.
    vg16 = exporter.buffers.get('BlendRemapVertexVG')
    if vg16 is not None:
        ids_name = vg16.layout.get_element(AbstractSemantic(Semantic.Blendindices, 1)).get_name()
        true_vg_ids = vg16.get_field(ids_name)
    else:
        ids_name = blend.layout.get_element(AbstractSemantic(Semantic.Blendindices, 0)).get_name()
        true_vg_ids = blend.get_field(ids_name)

    weights_name = blend.layout.get_element(AbstractSemantic(Semantic.Blendweight, 0)).get_name()
    weights = blend.get_field(weights_name)

    index_data = index_buffer.get_field(0).ravel()
    index_layout = [component.index_count for component in exporter.merged_object.components]
    component_of_vertex = _remap.build_vertex_component_map(
        index_data, index_layout, true_vg_ids.shape[0])

    # Level numbering: groups ordered by total matched vertex_count desc
    # (more retained geometry = nearer LOD level), name as deterministic tie-break.
    ordered_groups = sorted(
        lod_groups.items(),
        key=lambda item: (-sum(e.get('vertex_count', 0) for e in item[1].values()), item[0]),
    )

    merged_components = exporter.merged_object.components
    velo_lods = []

    try:
        for lod_index, (lod_object_name, entries) in enumerate(ordered_groups):
            level = lod_index + 1

            component_maps = {
                component_id: _remap.compose_full_to_lod_local(
                    full_components_meta[component_id], entry)
                for component_id, entry in entries.items()
                if component_id < len(merged_components)
                and len(merged_components[component_id].objects) > 0
            }

            lod_ids = _remap.remap_blend_component_local(
                true_vg_ids, weights, component_of_vertex, component_maps)

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

    total_sections = sum(
        1 for lod in velo_lods for entry in lod['components'] if entry is not None)
    print(f'[LOD] Prepared {len(velo_lods)} LOD level(s) '
          f'({total_sections} override sections, per-draw stateless).')
