"""WWMI driver-layer patch: also emit a complete ``ShaderTextureUsage.json`` during extraction.

Schema v3 (aligned with the XQFA WWMI-Tools fork so extractions are
interchangeable between the two plugins):

    "Component {id}" -> "vs=<hash>" -> "ps=<hash>" -> "ps-tN" ->
        {"filename", "hash", "format", "width", "height", "asset_path"}

Schema v4 (ADR 0007 rev 12, additive - emitted only when the dump's log.txt
yields usable binding-freshness evidence): top-level ``"version": 4``, per
slot record ``"fresh": true/false`` (slot was explicitly PSSetShaderResources
-bound under the draw's call id, vs. state inherited from an earlier draw),
optional ``"verified_inherited": true`` for service slots when that inherited
binding's writer and consumer are color passes, freshly bind the same
character cb4 identity, share a fresh material cb5/cb6 identity, and belong
to different vb0 objects,
and per (vs, ps) pair ``"depth_only": true/false`` (no color render target
bound at any of the pair's draws). Consumers that predate v4 skip the extra
keys structurally; without log evidence the writer emits a v3-identical file.

``format`` is the canonical DXGI format name (vocabulary of
``embedded/slot_textures/constants.DXGI_FORMAT_NAMES``) read from the dump
DDS headers by the pure-python ``dds_meta`` parser (the XQFA fork shells out
to texdiag.exe for the same data). Formats are captured at EXTRACTION time on
purpose: they describe the ORIGINAL game resources and are the core matching
key of the slot-style layer — the model-folder files get overwritten by the
author and cannot be trusted at export time.

Background (bug): in vendored WWMI-Tools 1.7.3, ``MeshObject.build_component`` in
``_wwmi_core/extract_frame_data/component_builder.py`` builds the texture dict keyed
by ``get_slot_hash()``, which collapses descriptors that share the same (slot, texture
hash) within one component but come from different (vs, ps) shader pairs into a single
entry, losing the second pair's slot->texture mapping (the texture files are not lost,
only the pair attribution). The existing ``TextureUsage.json`` is therefore incomplete
for multi-pair slots, and ``ShaderTextureUsage.json`` is not produced at all.

Fix (Approach C, patch-based, never touch ``_wwmi_core``):
1. Capture: wrap ``MeshObject.build_components``; after the original runs, store each
   component's **full** per-draw texture descriptor list (``draw_data.textures``, before
   collapsing) into the side channel ``_CAPTURE``, keyed by ``vb0_hash``
   (= output folder name = ``object_hash`` in ``write_objects``, the only stable key
   linking build time and write time).
   **Do not touch ``component.textures``** -> the original pipeline (filtering, file
   extraction, TextureUsage.json) stays byte-for-byte unchanged.
2. Emit: wrap module-level ``write_objects`` (called inside ``extract_frame_data`` by
   bare global name, so overwriting the module attribute takes effect); first call the
   original (writes all products as usual), then iterate with the **exact same**
   object_name/directory/missing-shapekey skip logic, and for each component use the set
   of surviving slot_hash values (post original-pipeline filtering) as the inclusion
   criterion, retrieve all descriptors with the same slot_hash from the side channel,
   group them by (vs,ps) pair, and write out ``ShaderTextureUsage.json``.

Scope boundary:
- Fixes the per-pair loss of "same (slot,hash), different (vs,ps) pair within one
  component" (the bug itself).
- Does **not** recover the secondary pair with "same slot, different hash" that the
  upstream dict drops at the collapse point -- the original TextureUsage.json and file
  extraction do not include it either, and forcibly recovering it would require rerunning
  the filter and would in fact change the extracted texture files (a regression), so it
  is left out.

Idempotent and reversible: ``_INSTALLED`` guard; ``uninstall_patches()`` restores both
original functions and clears the side channel.
"""

import json
from collections import OrderedDict
from pathlib import Path

from ._wwmi_core.extract_frame_data import component_builder as _cb_module
from ._wwmi_core.extract_frame_data import extract_frame_data as _efd_module
from ._wwmi_core.migoto_io.dump_parser import dump_parser as _dump_module
from ._wwmi_core.migoto_io.dump_parser.filename_parser import ShaderType

from .embedded.slot_textures import dds_meta as _dds_meta
from .embedded.slot_textures import log_freshness as _log_freshness
from .embedded.slot_textures import stu_metadata as _stu_metadata
from .embedded.slot_textures.constants import SERVICE_SLOTS
from .embedded import asset_paths as _asset_paths

_INSTALLED = False
_ORIG_BUILD_COMPONENTS = None
_ORIG_WRITE_OBJECTS = None
_ORIG_DUMP_POST_INIT = None
# vb0_hash -> list of "full descriptor lists" ordered by component (component_id is the index)
_CAPTURE = {}


def _skip_dirty_slot_enabled() -> bool:
    """Read the current extraction setting when Blender is available.

    Tests import this module without a real bpy context, so the default follows
    the production default: enabled.
    """
    try:
        import bpy  # type: ignore
        cfg = getattr(getattr(bpy.context, 'scene', None), 'VTWW_settings', None)
        if cfg is None or not hasattr(cfg, 'skip_slot_residual_textures'):
            return True
        return bool(cfg.skip_slot_residual_textures)
    except Exception:
        return True


def _shader_keys(descriptor):
    """Extract the nested-schema keys ("vs=<hash>", "ps=<hash>") from the
    descriptor's shader refs. Selected by ``ShaderRef.type`` (not by parse
    order); a missing stage falls back to a self-describing placeholder."""
    vs = next((s for s in descriptor.shaders if s.type is ShaderType.Vertex), None)
    ps = next((s for s in descriptor.shaders if s.type is ShaderType.Pixel), None)
    return (vs.raw if vs is not None else 'vs=?',
            ps.raw if ps is not None else 'ps=?')


def texture_record(
        descriptor,
        filename: str = '',
        asset_path: str = '',
) -> OrderedDict:
    """Rich slot record (XQFA-compatible shape) for one texture descriptor.
    Format/size come from the dump DDS header; unreadable / non-DDS sources
    yield empty format and zero size (the generator skips such slots in
    conditions, exactly like unknown formats in the XQFA exporter)."""
    meta = _dds_meta.read_dds_meta(descriptor.path)
    return OrderedDict((
        ('filename', filename),
        ('hash', descriptor.hash),
        ('format', meta.format if meta else ''),
        ('width', meta.width if meta else 0),
        ('height', meta.height if meta else 0),
        ('asset_path', asset_path),
    ))


def _wrapped_build_components(self, vb_layout, shapekeys):
    """Capture: after the original runs, store each component's full per-draw texture descriptors into the side channel."""
    _ORIG_BUILD_COMPONENTS(self, vb_layout, shapekeys)
    # verify() inside the original already set self.vb0_hash, and components_data is already
    # sorted by vertex_offset, in the same order as self.components / later ComponentData /
    # component_id. Overwrite: each round build precedes write, so it is naturally fresh.
    _CAPTURE[self.vb0_hash] = [list(cd.draw_data.textures) for cd in self.components_data]


def _wrapped_dump_post_init(self):
    original_listdir = _dump_module.os.listdir

    def list_dump_resources(path):
        return [
            filename for filename in original_listdir(path)
            if filename.casefold() != _asset_paths.MANIFEST_FILENAME.casefold()
        ]

    _dump_module.os.listdir = list_dump_resources
    try:
        return _ORIG_DUMP_POST_INIT(self)
    finally:
        _dump_module.os.listdir = original_listdir


def _wrapped_write_objects(output_directory, objects, allow_missing_shapekeys=False):
    """Emit: after the original writes all products as usual, additionally write ShaderTextureUsage.json (without changing the original products)."""
    _ORIG_WRITE_OBJECTS(output_directory, objects, allow_missing_shapekeys)
    try:
        output_directory = Path(output_directory)
        for object_hash, object_data in objects.items():
            # Exact same object_name / missing-shapekey skip logic as the original write_objects.
            object_name = object_hash
            if object_data.shapekeys.offsets_hash and not object_data.shapekeys.shapekey_offsets:
                if allow_missing_shapekeys:
                    object_name += '_MISSING_SHAPEKEYS'
                else:
                    continue

            object_directory = output_directory / object_name
            per_object = _CAPTURE.get(object_hash)

            # ADR 0007 rev 12: binding-freshness evidence from the dump's
            # log.txt (None -> legacy json without freshness flags).
            evidence = None
            asset_path_index = {}
            if per_object:
                first_desc = next((d for lst in per_object for d in lst), None)
                if first_desc is not None:
                    dump_root = _log_freshness.find_dump_root(first_desc.path)
                    if dump_root is not None:
                        evidence = _log_freshness.parse_log_freshness(dump_root)
                        asset_path_index = _asset_paths.load_asset_paths(dump_root)
                if evidence is None:
                    print(f'[velo slot-textures] {object_name}: no usable log.txt '
                          f'freshness evidence - writing legacy ShaderTextureUsage.json '
                          f'(stale-inherited records cannot be flagged)')

            # hash -> set of component ids carrying it, to rebuild the exact
            # texture filenames the original write loop produced
            # (Components-{ids} t={hash}{suffix}).
            hash_components = {}
            hashless_components = set()
            for component_id, component in enumerate(object_data.components):
                for texture in component.textures:
                    if not texture.hash:
                        hashless_components.add(str(component_id))
                        continue
                    hash_components.setdefault(texture.hash, set()).add(str(component_id))
            if hashless_components:
                joined = '-'.join(sorted(hashless_components))
                for invalid_path in object_directory.glob(
                        f'Components-{joined} t=None.*'):
                    invalid_path.unlink(missing_ok=True)

            def stock_filename(descriptor):
                if not descriptor.hash:
                    return ''
                ids = hash_components.get(descriptor.hash)
                if not ids:
                    return ''
                joined = '-'.join(sorted(ids))
                return f'Components-{joined} t={descriptor.hash}{Path(descriptor.path).suffix}'

            shader_texture_usage = OrderedDict()
            skip_dirty_slot = _skip_dirty_slot_enabled()
            try:
                native_metadata = (
                    json.loads(object_data.metadata)
                    if isinstance(object_data.metadata, str)
                    else object_data.metadata)
            except (TypeError, ValueError):
                native_metadata = {}
            if not isinstance(native_metadata, dict):
                native_metadata = {}
            character_cb_hash = str(
                native_metadata.get('cb4_hash') or '').strip().lower()
            if evidence is not None:
                shader_texture_usage['version'] = 4
            elif per_object and skip_dirty_slot:
                print(f'[velo slot-textures] {object_name}: Skip Dirty Slot is enabled '
                      f'but no usable log.txt freshness evidence was found - '
                      f'legacy ShaderTextureUsage.json kept unfiltered')
            record_cache = {}
            asset_paths_by_hash = {}
            skipped_dirty_slots = 0
            for component_id, component in enumerate(object_data.components):
                # slot_hash values surviving the original pipeline filter (whether a texture file is included is entirely decided by the original pipeline).
                surviving = {t.get_slot_hash() for t in component.textures}
                full = per_object[component_id] if (per_object and component_id < len(per_object)) else []

                # (vs_key, ps_key) -> slot_key ->
                # [record, fresh, verified_inherited, observed_only]. Freshness is
                # OR-aggregated across the pair's draws; on same-seat hash
                # disagreements fresh beats inherited (legacy: last-wins).
                seats = {}
                pair_depth_only = {}
                for desc in full:
                    vs_key, ps_key = _shader_keys(desc)
                    survives = desc.get_slot_hash() in surviving
                    captured_path = _asset_paths.asset_path_for_dump_file(
                        asset_path_index, desc.path)
                    if desc.hash and captured_path:
                        existing_path = asset_paths_by_hash.get(desc.hash)
                        if existing_path and captured_path != existing_path:
                            raise ValueError(
                                f"Texture Hash {desc.hash} maps to conflicting "
                                "Unreal asset paths in this extraction"
                            )
                        asset_paths_by_hash[desc.hash] = captured_path
                    if desc.hash not in record_cache:
                        record_cache[desc.hash] = texture_record(
                            desc,
                            stock_filename(desc),
                        )
                    record = record_cache[desc.hash]
                    slot_key = desc.get_slot()
                    fresh = None
                    verified_inherited = False
                    observed_only = False
                    if evidence is not None:
                        fresh = _log_freshness.slot_is_fresh(
                            evidence, desc.call_id, desc.slot_id,
                            desc.hash, desc.old_hash)
                        rt = _log_freshness.call_has_color_rt(evidence, desc.call_id)
                        depth = (rt is False)
                        prev = pair_depth_only.get((vs_key, ps_key))
                        pair_depth_only[(vs_key, ps_key)] = (
                            depth if prev is None else (prev and depth))
                        if not survives:
                            if rt is not True:
                                continue
                            observed_only = True
                        if skip_dirty_slot and fresh is False:
                            verified_inherited = (
                                desc.slot_id in SERVICE_SLOTS
                                and _log_freshness.slot_is_verified_character_inherited(
                                    evidence, desc.call_id, desc.slot_id,
                                    desc.hash, character_cb_hash, desc.old_hash))
                            if not verified_inherited:
                                if rt is not True:
                                    skipped_dirty_slots += 1
                                    continue
                                observed_only = True
                    elif not survives:
                        continue
                    slot_map = seats.setdefault((vs_key, ps_key), {})
                    entry = slot_map.get(slot_key)
                    if entry is None:
                        slot_map[slot_key] = [
                            record, fresh, verified_inherited, observed_only]
                    elif fresh is None:
                        slot_map[slot_key] = [record, None, False, False]
                    elif entry[0]['hash'] == record['hash']:
                        entry[1] = bool(entry[1]) or fresh
                        entry[2] = bool(entry[2]) or verified_inherited
                        entry[3] = bool(entry[3]) and observed_only
                    elif fresh or not entry[1]:
                        slot_map[slot_key] = [
                            record, fresh, verified_inherited, observed_only]
                    # else: seated record is fresh, newcomer is stale -> keep seat

                # Deterministic ordering: sort vs / ps / slot keys for easy diffing.
                component_out = OrderedDict()
                for vs_key in sorted({k[0] for k in seats}):
                    vs_out = OrderedDict()
                    for ps_key in sorted({k[1] for k in seats if k[0] == vs_key}):
                        slot_map = seats[(vs_key, ps_key)]
                        ps_out = OrderedDict()
                        for slot_key in sorted(slot_map):
                            record, fresh, verified_inherited, observed_only = slot_map[slot_key]
                            if fresh is None:
                                ps_out[slot_key] = record
                            else:
                                # record_cache entries are shared across seats:
                                # per-seat flags must go on a copy.
                                seat_record = OrderedDict(record)
                                seat_record['fresh'] = bool(fresh)
                                if verified_inherited:
                                    seat_record['verified_inherited'] = True
                                if observed_only:
                                    seat_record['observed_only'] = True
                                ps_out[slot_key] = seat_record
                        if evidence is not None:
                            ps_out['depth_only'] = bool(
                                pair_depth_only.get((vs_key, ps_key), False))
                        vs_out[ps_key] = ps_out
                    component_out[vs_key] = vs_out
                shader_texture_usage[f'Component {component_id}'] = component_out

            if skipped_dirty_slots:
                print(f'[velo slot-textures] {object_name}: Skip Dirty Slot removed '
                      f'{skipped_dirty_slots} stale-inherited slot record(s) from '
                      f'ShaderTextureUsage.json')

            _stu_metadata.sync_form_component_modes(shader_texture_usage)

            if evidence is not None and skip_dirty_slot:
                allowed_usage = {}
                emitted_hash_components = {}
                for component_key, component_block in shader_texture_usage.items():
                    if (not str(component_key).startswith('Component ')
                            or not isinstance(component_block, dict)):
                        continue
                    component_id = str(component_key).split()[-1]
                    component_allowed = allowed_usage.setdefault(component_key, {})
                    for vs_key, vs_block in component_block.items():
                        if not str(vs_key).startswith('vs=') or not isinstance(vs_block, dict):
                            continue
                        for ps_key, ps_block in vs_block.items():
                            if not str(ps_key).startswith('ps=') or not isinstance(ps_block, dict):
                                continue
                            for slot_key, record in ps_block.items():
                                if (not str(slot_key).startswith('ps-t')
                                        or not isinstance(record, dict)):
                                    continue
                                if record.get('observed_only') is True:
                                    continue
                                tex_hash = str(record.get('hash') or '')
                                if not tex_hash:
                                    continue
                                component_allowed.setdefault(slot_key, set()).add(
                                    (tex_hash, vs_key, ps_key))
                                emitted_hash_components.setdefault(tex_hash, set()).add(
                                    component_id)

                usage_path = object_directory / 'TextureUsage.json'
                try:
                    with open(usage_path, encoding='utf-8') as f:
                        texture_usage = json.load(f)
                except (OSError, TypeError, ValueError):
                    texture_usage = None
                if isinstance(texture_usage, dict):
                    for component_key, component_block in texture_usage.items():
                        if not isinstance(component_block, dict):
                            continue
                        component_allowed = allowed_usage.get(component_key, {})
                        for slot_key in list(component_block):
                            entries = component_block.get(slot_key)
                            if not isinstance(entries, list):
                                continue
                            allowed = component_allowed.get(slot_key, set())
                            kept = []
                            for entry in entries:
                                parts = str(entry).split('-')
                                if any(
                                        parts and parts[0] == tex_hash
                                        and vs_key in parts and ps_key in parts
                                        for tex_hash, vs_key, ps_key in allowed):
                                    kept.append(entry)
                            if kept:
                                component_block[slot_key] = kept
                            else:
                                del component_block[slot_key]
                    with open(usage_path, 'w') as f:
                        f.write(json.dumps(texture_usage, indent=4))

                final_filenames = {}
                for tex_hash, stock_components in hash_components.items():
                    record = record_cache.get(tex_hash)
                    if record is None:
                        continue
                    suffix = Path(record.get('filename') or '').suffix
                    stock_ids = '-'.join(sorted(stock_components))
                    stock_path = object_directory / (
                        f'Components-{stock_ids} t={tex_hash}{suffix}')
                    emitted_components = emitted_hash_components.get(tex_hash, set())
                    if not emitted_components:
                        if stock_path.is_file():
                            stock_path.unlink()
                        continue
                    emitted_ids = '-'.join(sorted(emitted_components))
                    final_filename = f'Components-{emitted_ids} t={tex_hash}{suffix}'
                    final_filenames[tex_hash] = final_filename
                    final_path = object_directory / final_filename
                    if stock_path != final_path and stock_path.is_file():
                        stock_path.replace(final_path)

                for component_block in shader_texture_usage.values():
                    if not isinstance(component_block, dict):
                        continue
                    for vs_block in component_block.values():
                        if not isinstance(vs_block, dict):
                            continue
                        for ps_block in vs_block.values():
                            if not isinstance(ps_block, dict):
                                continue
                            for record in ps_block.values():
                                if not isinstance(record, dict):
                                    continue
                                final_filename = final_filenames.get(record.get('hash'))
                                if final_filename:
                                    record['filename'] = final_filename

            # Preserve the slot-texture layer's user-editable lean metadata
            # across re-extraction.
            usage_path = object_directory / 'ShaderTextureUsage.json'
            if usage_path.is_file():
                try:
                    with open(usage_path, encoding='utf-8') as f:
                        previous = json.load(f)
                    previous = _stu_metadata.canonicalize_lean_usage(previous)
                    form_anchors = previous.get('form_anchors')
                    if isinstance(form_anchors, str) and form_anchors.strip():
                        shader_texture_usage['form_anchors'] = form_anchors
                    for comp_name in _stu_metadata.component_ids_in_usage(
                            previous):
                        key = _stu_metadata.component_key(comp_name)
                        previous_block = previous.get(key)
                        current_block = shader_texture_usage.get(key)
                        if not isinstance(previous_block, dict):
                            continue
                        if not isinstance(current_block, dict):
                            continue
                        mode = previous_block.get(
                            _stu_metadata.constants.FORM_COMPONENT_MODE_KEY)
                        if str(mode).strip().lower() == 'multi':
                            current_block[
                                _stu_metadata.constants.FORM_COMPONENT_MODE_KEY
                            ] = 'multi'
                        sources = previous_block.get(
                            _stu_metadata.constants.COMPONENT_SOURCES_KEY)
                        if sources:
                            current_block[
                                _stu_metadata.constants.COMPONENT_SOURCES_KEY
                            ] = sources
                        variants = previous_block.get(
                            _stu_metadata.constants.FORM_VARIANTS_KEY)
                        if variants:
                            current_block[
                                _stu_metadata.constants.FORM_VARIANTS_KEY
                            ] = variants
                except Exception:
                    pass  # unreadable previous file: write fresh maps only
            _stu_metadata.sync_form_component_modes(shader_texture_usage)
            _asset_paths.enrich_existing_texture_records(
                shader_texture_usage,
                object_directory,
                asset_paths_by_hash,
            )

            with open(usage_path, "w") as f:
                f.write(_stu_metadata.dumps_usage(shader_texture_usage))
    finally:
        # Bound the side-channel lifetime (also prevents cross-round leftovers). The next build round refills it.
        _CAPTURE.clear()


def install_patches():
    global _INSTALLED, _ORIG_BUILD_COMPONENTS, _ORIG_WRITE_OBJECTS
    global _ORIG_DUMP_POST_INIT
    if _INSTALLED:
        return
    _ORIG_BUILD_COMPONENTS = _cb_module.MeshObject.build_components
    _ORIG_WRITE_OBJECTS = _efd_module.write_objects
    _ORIG_DUMP_POST_INIT = _dump_module.Dump.__post_init__
    _cb_module.MeshObject.build_components = _wrapped_build_components
    _efd_module.write_objects = _wrapped_write_objects
    _dump_module.Dump.__post_init__ = _wrapped_dump_post_init
    _INSTALLED = True


def uninstall_patches():
    global _INSTALLED, _ORIG_BUILD_COMPONENTS, _ORIG_WRITE_OBJECTS
    global _ORIG_DUMP_POST_INIT
    if not _INSTALLED:
        return
    try:
        if _ORIG_BUILD_COMPONENTS is not None:
            _cb_module.MeshObject.build_components = _ORIG_BUILD_COMPONENTS
        if _ORIG_WRITE_OBJECTS is not None:
            _efd_module.write_objects = _ORIG_WRITE_OBJECTS
        if _ORIG_DUMP_POST_INIT is not None:
            _dump_module.Dump.__post_init__ = _ORIG_DUMP_POST_INIT
    finally:
        _ORIG_BUILD_COMPONENTS = None
        _ORIG_WRITE_OBJECTS = None
        _ORIG_DUMP_POST_INIT = None
        _CAPTURE.clear()
        _INSTALLED = False
