"""WWMI driver-layer patch: also emit a complete ``ShaderTextureUsage.json`` during extraction.

Schema v3 (aligned with the XQFA WWMI-Tools fork so extractions are
interchangeable between the two plugins):

    "Component {id}" -> "vs=<hash>" -> "ps=<hash>" -> "ps-tN" ->
        {"filename", "hash", "format", "width", "height"}

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
from ._wwmi_core.migoto_io.dump_parser.filename_parser import ShaderType

from .embedded.slot_textures import dds_meta as _dds_meta

_INSTALLED = False
_ORIG_BUILD_COMPONENTS = None
_ORIG_WRITE_OBJECTS = None
# vb0_hash -> list of "full descriptor lists" ordered by component (component_id is the index)
_CAPTURE = {}


def _shader_keys(descriptor):
    """Extract the nested-schema keys ("vs=<hash>", "ps=<hash>") from the
    descriptor's shader refs. Selected by ``ShaderRef.type`` (not by parse
    order); a missing stage falls back to a self-describing placeholder."""
    vs = next((s for s in descriptor.shaders if s.type is ShaderType.Vertex), None)
    ps = next((s for s in descriptor.shaders if s.type is ShaderType.Pixel), None)
    return (vs.raw if vs is not None else 'vs=?',
            ps.raw if ps is not None else 'ps=?')


def texture_record(descriptor, filename: str = '') -> OrderedDict:
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
    ))


def _wrapped_build_components(self, vb_layout, shapekeys):
    """Capture: after the original runs, store each component's full per-draw texture descriptors into the side channel."""
    _ORIG_BUILD_COMPONENTS(self, vb_layout, shapekeys)
    # verify() inside the original already set self.vb0_hash, and components_data is already
    # sorted by vertex_offset, in the same order as self.components / later ComponentData /
    # component_id. Overwrite: each round build precedes write, so it is naturally fresh.
    _CAPTURE[self.vb0_hash] = [list(cd.draw_data.textures) for cd in self.components_data]


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

            # hash -> set of component ids carrying it, to rebuild the exact
            # texture filenames the original write loop produced
            # (Components-{ids} t={hash}{suffix}).
            hash_components = {}
            for component_id, component in enumerate(object_data.components):
                for texture in component.textures:
                    hash_components.setdefault(texture.hash, set()).add(str(component_id))

            def stock_filename(descriptor):
                ids = hash_components.get(descriptor.hash)
                if not ids:
                    return ''
                joined = '-'.join(sorted(ids))
                return f'Components-{joined} t={descriptor.hash}{Path(descriptor.path).suffix}'

            shader_texture_usage = OrderedDict()
            record_cache = {}
            for component_id, component in enumerate(object_data.components):
                # slot_hash values surviving the original pipeline filter (whether a texture file is included is entirely decided by the original pipeline).
                surviving = {t.get_slot_hash() for t in component.textures}
                full = per_object[component_id] if (per_object and component_id < len(per_object)) else []

                pairs = {}
                for desc in full:
                    if desc.get_slot_hash() not in surviving:
                        continue
                    vs_key, ps_key = _shader_keys(desc)
                    if desc.hash not in record_cache:
                        record_cache[desc.hash] = texture_record(desc, stock_filename(desc))
                    # A single draw call binds only one texture per ps-tN -> (pair, slot) is unique.
                    pairs.setdefault(vs_key, {}).setdefault(ps_key, {})[desc.get_slot()] = record_cache[desc.hash]

                # Deterministic ordering: sort vs / ps / slot keys for easy diffing.
                component_out = OrderedDict()
                for vs_key in sorted(pairs):
                    vs_out = OrderedDict()
                    for ps_key in sorted(pairs[vs_key]):
                        vs_out[ps_key] = OrderedDict(sorted(pairs[vs_key][ps_key].items()))
                    component_out[vs_key] = vs_out
                shader_texture_usage[f'Component {component_id}'] = component_out

            # Preserve the slot-texture layer's "extra_forms" key (merged
            # extra-form maps) across re-extraction.
            usage_path = object_directory / 'ShaderTextureUsage.json'
            if usage_path.is_file():
                try:
                    with open(usage_path, encoding='utf-8') as f:
                        previous = json.load(f)
                    extra_forms = previous.get('extra_forms')
                    if isinstance(extra_forms, list) and extra_forms:
                        shader_texture_usage['extra_forms'] = extra_forms
                except Exception:
                    pass  # unreadable previous file: write fresh maps only

            with open(usage_path, "w") as f:
                f.write(json.dumps(shader_texture_usage, indent=4))
    finally:
        # Bound the side-channel lifetime (also prevents cross-round leftovers). The next build round refills it.
        _CAPTURE.clear()


def install_patches():
    global _INSTALLED, _ORIG_BUILD_COMPONENTS, _ORIG_WRITE_OBJECTS
    if _INSTALLED:
        return
    _ORIG_BUILD_COMPONENTS = _cb_module.MeshObject.build_components
    _ORIG_WRITE_OBJECTS = _efd_module.write_objects
    _cb_module.MeshObject.build_components = _wrapped_build_components
    _efd_module.write_objects = _wrapped_write_objects
    _INSTALLED = True


def uninstall_patches():
    global _INSTALLED, _ORIG_BUILD_COMPONENTS, _ORIG_WRITE_OBJECTS
    if not _INSTALLED:
        return
    try:
        if _ORIG_BUILD_COMPONENTS is not None:
            _cb_module.MeshObject.build_components = _ORIG_BUILD_COMPONENTS
        if _ORIG_WRITE_OBJECTS is not None:
            _efd_module.write_objects = _ORIG_WRITE_OBJECTS
    finally:
        _ORIG_BUILD_COMPONENTS = None
        _ORIG_WRITE_OBJECTS = None
        _CAPTURE.clear()
        _INSTALLED = False
