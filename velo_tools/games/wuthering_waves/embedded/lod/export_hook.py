# Export hook: append per-LOD blend buffers + ini override sections to the
# stock WWMI mod export (single-mod LOD).
#
# Patches _wwmi_core.blender_export.blender_export.ModExporter.export_mod as a
# class attribute (same technique as _shader_texture_usage.py; _wwmi_core
# sources untouched, install()/remove() idempotent and reversible). After the
# stock export finishes, the exporter instance still holds cfg / buffers
# (incl. the 16-bit BlendRemapVertexVG truth when the merged object exceeds
# 256 VGs) / merged_object / paths — everything needed to derive the LOD data.
#
# Round 1 scope: MERGED skeleton mode only, single-IB objects, no LOD
# shapekeys, no LOD-only texture overrides, no cross-scene interplay.

import json
import traceback

import numpy

from ..._wwmi_core.blender_export import blender_export as _be_module
from ..._wwmi_core.migoto_io.data_model.byte_buffer import NumpyBuffer, AbstractSemantic, Semantic

from . import remap as _remap
from . import ini_gen as _ini_gen

_INSTALLED = False
_ORIG_EXPORT_MOD = None


class LodExportError(Exception):
    pass


def install():
    global _INSTALLED, _ORIG_EXPORT_MOD
    if _INSTALLED:
        return

    _ORIG_EXPORT_MOD = _be_module.ModExporter.export_mod

    def _wrapped_export_mod(self):
        _ORIG_EXPORT_MOD(self)
        _post_export_lod(self)

    _wrapped_export_mod._velo_lod_hook = True
    _be_module.ModExporter.export_mod = _wrapped_export_mod
    _INSTALLED = True


def remove():
    global _INSTALLED, _ORIG_EXPORT_MOD
    if not _INSTALLED:
        return
    _be_module.ModExporter.export_mod = _ORIG_EXPORT_MOD
    _ORIG_EXPORT_MOD = None
    _INSTALLED = False


def _post_export_lod(exporter):
    cfg = exporter.cfg

    # Gates: silently / verbosely skip configurations out of round-1 scope.
    if getattr(cfg, 'custom_template_live_update', False):
        return
    if cfg.partial_export or not cfg.write_ini:
        return
    if (exporter.object_source_folder / 'CrossSceneRouting.json').is_file():
        print('[VeloLOD] Cross-scene export detected, LOD overrides are not supported yet — skipped.')
        return

    try:
        with open(exporter.object_source_folder / 'Metadata.json', encoding='utf-8') as f:
            metadata = json.load(f)
    except Exception:
        return

    # Per-component lods entries (EFMI-style), grouped per LOD object.
    full_components_meta = metadata['components']
    lod_groups = {}  # lod_object_name -> {component_id: entry}
    for component_id, component_meta in enumerate(full_components_meta):
        for lod_entry in component_meta.get('lods') or []:
            name = lod_entry.get('lod_object_name')
            if name:
                lod_groups.setdefault(name, {})[component_id] = lod_entry
    if not lod_groups:
        if metadata.get('lods'):
            print('[VeloLOD] Legacy top-level "lods" metadata found — the schema has moved into '
                  'components[].lods; re-run Extract LOD Data. LOD export skipped.')
        return

    if cfg.mod_skeleton_type != 'MERGED':
        print('[VeloLOD] LOD data found, but LOD export currently supports the MERGED skeleton mode only — skipped.')
        return

    blend = exporter.buffers.get('Blend')
    index_buffer = exporter.buffers.get('Index')
    if blend is None or index_buffer is None:
        print('[VeloLOD] Blend/Index buffers unavailable, LOD overrides skipped.')
        return

    if getattr(cfg, 'use_ini_toggles', False):
        print('[VeloLOD] Note: ini toggles are ignored at LOD distance (all objects drawn).')

    weights_name = blend.layout.get_element(AbstractSemantic(Semantic.Blendweight, 0)).get_name()
    weights = blend.get_field(weights_name)

    vg16 = exporter.buffers.get('BlendRemapVertexVG')
    if vg16 is not None:
        # 16-bit truth (merged object exceeds 256 VGs; the 8-bit Blend ids are remapped)
        ids_name = vg16.layout.get_element(AbstractSemantic(Semantic.Blendindices, 1)).get_name()
        true_vg_ids = vg16.get_field(ids_name)
    else:
        ids_name = blend.layout.get_element(AbstractSemantic(Semantic.Blendindices, 0)).get_name()
        true_vg_ids = blend.get_field(ids_name)

    index_data = index_buffer.get_field(0).ravel()
    index_layout = [component.index_count for component in exporter.merged_object.components]
    vertex_count = true_vg_ids.shape[0]

    component_of_vertex = _remap.build_vertex_component_map(index_data, index_layout, vertex_count)

    shared = {
        'color_present': 'Color' in exporter.buffers,
        'blend_stride': blend.layout.stride,
        'skeleton_scale': cfg.skeleton_scale,
        'full_cb4_hash': metadata.get('cb4_hash', ''),
    }

    # Regenerate all velo LOD buffers from scratch to avoid stale leftovers.
    for stale in exporter.meshes_path.glob('VeloLod*.buf'):
        stale.unlink()

    # Level numbering: groups ordered by total matched vertex_count desc
    # (more retained geometry = nearer LOD level), name as deterministic tie-break.
    ordered_groups = sorted(
        lod_groups.items(),
        key=lambda item: (-sum(e.get('vertex_count', 0) for e in item[1].values()), item[0]),
    )

    try:
        lod_plans = []
        for lod_index, (lod_object_name, entries) in enumerate(ordered_groups):
            lod_plans.append(_build_lod_level(exporter, lod_index + 1, lod_object_name, entries,
                                              full_components_meta, true_vg_ids, weights,
                                              index_data, index_layout, component_of_vertex))
    except _remap.LodRemapError as e:
        raise LodExportError(f'LOD export failed: {e}') from e

    if not lod_plans:
        return

    ini_path = exporter.mod_output_folder / 'mod.ini'
    ini_text = ini_path.read_text(encoding='utf-8')
    try:
        new_text = _ini_gen.append_lod_overrides(ini_text, lod_plans, shared)
    except _ini_gen.IniAnchorError as e:
        print(f'[VeloLOD] WARNING: {e} — LOD sections were NOT added '
              f'(custom ini templates are not supported by the LOD hook).')
        return
    ini_path.write_text(new_text, encoding='utf-8')

    total_sections = sum(len(plan['components']) for plan in lod_plans)
    print(f'[VeloLOD] Appended {len(lod_plans)} LOD level(s) ({total_sections} override sections) to mod.ini.')


def _build_lod_level(exporter, level, lod_object_name, entries, full_components_meta,
                     true_vg_ids, weights, index_data, index_layout, component_of_vertex):
    merged_components = exporter.merged_object.components

    # Rebuild the index-aligned per-component entry list (None = no entry).
    entry_components = [entries.get(i) for i in range(len(full_components_meta))]

    # Components that get real LOD draws: matched + non-empty custom geometry.
    draw_indices = [
        component_id for component_id, entry_component in enumerate(entry_components)
        if entry_component is not None
        and component_id < len(merged_components)
        and len(merged_components[component_id].objects) > 0
    ]

    relevant_mask = numpy.isin(component_of_vertex, numpy.array(draw_indices, dtype=numpy.int32))

    compose = _remap.compose_full_to_lod_merged(full_components_meta, entry_components)
    lod_ids = _remap.remap_blend(true_vg_ids, weights, compose, relevant_mask)
    compaction_plans = _remap.plan_component_compaction(lod_ids, weights, index_data, index_layout, draw_indices)

    # Write blend buffer variants + packed forward maps.
    has_shared_buffer = False
    forward_blocks = []
    remap_ids = {}
    for component_id in draw_indices:
        plan = compaction_plans.get(component_id, {"needs_compaction": False})
        if plan['needs_compaction']:
            remap_ids[component_id] = len(forward_blocks)
            forward_blocks.append(plan['forward'])
            compacted = _remap.compact_component_ids(lod_ids, plan)
            _write_blend_variant(exporter, f'VeloLod{level}BlendC{component_id}.buf', compacted)
        else:
            has_shared_buffer = True

    if has_shared_buffer:
        _write_blend_variant(exporter, f'VeloLod{level}Blend.buf', lod_ids)

    if forward_blocks:
        packed = numpy.concatenate(forward_blocks).astype(numpy.uint16)
        with open(exporter.meshes_path / f'VeloLod{level}BlendRemapForward.buf', 'wb') as f:
            f.write(packed.tobytes())

    # Assemble the pure-data ini plan.
    plan_components = []
    for component_id, entry_component in enumerate(entry_components):
        if entry_component is None:
            continue
        draws = []
        if component_id in draw_indices:
            draws = [
                (temp_object.index_count, temp_object.index_offset)
                for temp_object in merged_components[component_id].objects
            ]
        compaction = None
        if component_id in remap_ids:
            compaction = {
                'remap_id': remap_ids[component_id],
                'vg_count': compaction_plans[component_id]['vg_count'],
            }
        plan_components.append({
            'index': component_id,
            'index_offset': entry_component['index_offset'],
            'index_count': entry_component['index_count'],
            'vg_offset': entry_component['vg_offset'],
            'vg_count': entry_component['vg_count'],
            'draws': draws,
            'compaction': compaction,
        })

    # All entries of a group share the LOD object's hashes (single writer).
    first_entry = next(iter(entries.values()))
    return {
        'level': level,
        'lod_object_name': lod_object_name,
        'vb0_hash': first_entry['vb0_hash'],
        'cb4_hash': first_entry.get('cb4_hash', ''),
        'components': plan_components,
        'has_compaction': bool(forward_blocks),
    }


def _write_blend_variant(exporter, filename, lod_ids):
    blend = exporter.buffers['Blend']
    out = NumpyBuffer(blend.layout, data=blend.data.copy())
    indices_name = blend.layout.get_element(AbstractSemantic(Semantic.Blendindices, 0)).get_name()
    out.set_field(indices_name, (lod_ids % 256).astype(numpy.uint8))
    with open(exporter.meshes_path / filename, 'wb') as f:
        f.write(out.get_bytes())
