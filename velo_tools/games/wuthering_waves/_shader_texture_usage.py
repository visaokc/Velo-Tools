"""WWMI driver-layer patch: also emit a complete ``ShaderTextureUsage.json`` during extraction.

Key path: ``"Component {id}" -> "vs=<hash>-ps=<hash>" -> "ps-tN" -> "<texture hash>"``.

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

_INSTALLED = False
_ORIG_BUILD_COMPONENTS = None
_ORIG_WRITE_OBJECTS = None
# vb0_hash -> list of "full descriptor lists" ordered by component (component_id is the index)
_CAPTURE = {}


def _pair_key(descriptor):
    """Extract (vs, ps) from the descriptor's shader refs to form the stable pair key ``"vs=<hash>-ps=<hash>"``.

    Selected by ``ShaderRef.type`` (not by ``.shaders`` parse order, which is not
    enforced); when a stage is missing, fall back to ``vs=?`` / ``ps=?`` so it never
    raises ``StopIteration`` and the key stays self-describing and sortable.
    """
    vs = next((s for s in descriptor.shaders if s.type is ShaderType.Vertex), None)
    ps = next((s for s in descriptor.shaders if s.type is ShaderType.Pixel), None)
    vs_part = vs.raw if vs is not None else "vs=?"
    ps_part = ps.raw if ps is not None else "ps=?"
    return f"{vs_part}-{ps_part}"


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

            shader_texture_usage = OrderedDict()
            for component_id, component in enumerate(object_data.components):
                # slot_hash values surviving the original pipeline filter (whether a texture file is included is entirely decided by the original pipeline).
                surviving = {t.get_slot_hash() for t in component.textures}
                full = per_object[component_id] if (per_object and component_id < len(per_object)) else []

                pairs = {}
                for desc in full:
                    if desc.get_slot_hash() not in surviving:
                        continue
                    # A single draw call binds only one texture per ps-tN -> (pair, slot) is unique, value is the scalar hash.
                    pairs.setdefault(_pair_key(desc), {})[desc.get_slot()] = desc.hash

                # Deterministic ordering: sort pair keys and slot keys for easy diffing.
                component_out = OrderedDict()
                for pair in sorted(pairs):
                    component_out[pair] = OrderedDict(sorted(pairs[pair].items()))
                shader_texture_usage[f'Component {component_id}'] = component_out

            # Schema v2: preserve the slot-texture layer's "extra_forms" key
            # (merged extra-form maps) across re-extraction.
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
