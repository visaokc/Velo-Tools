"""Pure cross-IB INI text generator.

Produces three pieces consumed by the export-time string post-processor:

  - providers_map:  dict[comp_id] -> dict with 'header' (multi-line snippet that
                                       opens the `if vs == ...` block and binds
                                       Fake sources / vb3) and 'closer' ('endif')
  - consumers_map:  dict[comp_id] -> consumer post-block string (one or more
                                       auto-filtered borrow blocks, one per
                                       borrowed provider)
  - resources:      str (Resource decls + CustomShaders + ShaderOverrides)
"""
import re
from pathlib import Path

from .props import parse_component_id
from .pass_registry import CrossIBPassRegistry, build_pass_registry
from .transparency_classifier import classify_frame_analysis_transparency


_COMPONENT_PREFIX_RE = re.compile(r"^\s*component[_ -]*(\d+)(?:\s+|[_ -]+)?", re.IGNORECASE)


def _sanitize_source_name(name):
    text = (name or "").strip()
    return re.sub(r"__velo_export(?:\.\d{3})?$", "", text)


def _strip_component_prefix(name):
    text = (name or "").strip()
    if not text:
        return text
    stripped = _COMPONENT_PREFIX_RE.sub("", text, count=1).strip()
    return stripped or text


def _collection_is_descendant(root, collection):
    if root is None or collection is None:
        return False
    return collection == root or collection in root.children_recursive


def _collection_is_visible(collection, context=None):
    if collection is None:
        return False
    if context is None:
        return True

    def search(layer_collection):
        if layer_collection.collection == collection:
            return (not layer_collection.exclude) and (not layer_collection.hide_viewport)
        for child in layer_collection.children:
            result = search(child)
            if result is not None:
                return result
        return None

    return bool(search(context.view_layer.layer_collection))


def _object_export_collections(obj, cfg):
    collections = list(getattr(obj, "users_collection", ()) or ())
    root = getattr(cfg, "component_collection", None) if cfg is not None else None
    if root is None:
        return collections
    return [collection for collection in collections if _collection_is_descendant(root, collection)]


def _object_allowed_by_export_filters(obj, cfg=None, context=None):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return False
    if cfg is None:
        return True
    if bool(getattr(cfg, "ignore_hidden_objects", False)):
        hide_get = getattr(obj, "hide_get", None)
        if bool(hide_get and hide_get()):
            return False
    if bool(getattr(cfg, "ignore_hidden_collections", False)):
        root = getattr(cfg, "component_collection", None)
        collections = _object_export_collections(obj, cfg)
        if not collections:
            return False
        if not any(collection == root or _collection_is_visible(collection, context) for collection in collections):
            return False
    return True


def _iter_mapping_meshes(mapping, cfg=None, context=None):
    if mapping.source_kind == 'COLLECTION':
        collection = mapping.source_collection
        if collection is None:
            return
        for obj in collection.objects:
            if _object_allowed_by_export_filters(obj, cfg, context):
                yield obj
        for child in collection.children:
            stack = [child]
            while stack:
                current = stack.pop()
                for obj in current.objects:
                    if _object_allowed_by_export_filters(obj, cfg, context):
                        yield obj
                stack.extend(current.children)
        return
    obj = mapping.source_object
    if _object_allowed_by_export_filters(obj, cfg, context):
        yield obj


def _source_name_aliases(mapping, cfg=None, context=None):
    aliases = []
    seen = set()

    def add(name):
        clean = _sanitize_source_name(name)
        if clean and clean not in seen:
            seen.add(clean)
            aliases.append(clean)

    for obj in _iter_mapping_meshes(mapping, cfg, context) or ():
        add(obj.name)
        object_comp_id = parse_component_id(obj.name)
        for slot in getattr(obj, "material_slots", ()):
            material = getattr(slot, "material", None)
            material_name = (getattr(material, "name", "") or "").strip()
            if not material_name:
                continue
            material_comp_id = parse_component_id(material_name)
            comp_id = material_comp_id if material_comp_id is not None else object_comp_id
            if comp_id is None:
                continue
            stripped = _strip_component_prefix(material_name)
            add(f"Component {comp_id} {stripped}".strip())
    return aliases


def _name_matches_wanted(wanted, name):
    if wanted is None:
        return True
    return name in wanted or _sanitize_source_name(name) in wanted


def _component_lod_levels(component):
    return [lod_id + 1 for lod_id, _lod in enumerate(getattr(component, "lods", ()) or ())]


def _get_component(extracted_object, comp_id):
    components = getattr(extracted_object, "components", {}) or {}
    try:
        return components[comp_id]
    except (KeyError, IndexError, TypeError):
        return None


def _append_lod_offset(lines, comp_id, component_res, component_res_lods, indent="    "):
    lod_res = component_res_lods.get(comp_id, {})
    if not lod_res:
        lines.append(f"{indent}cs-t2 = {component_res[comp_id]}")
        return

    lines.append(f"{indent}if $lod_level == 0")
    lines.append(f"{indent}    cs-t2 = {component_res[comp_id]}")
    for lod_level in sorted(lod_res):
        lines.append(f"{indent}elif $lod_level == {lod_level}")
        lines.append(f"{indent}    cs-t2 = {lod_res[lod_level]}")
    lines.append(f"{indent}else")
    lines.append(f"{indent}    cs-t2 = {component_res[comp_id]}")
    lines.append(f"{indent}endif")


def _build_run_header(comp_id, filters, registry, component_res, component_res_lods, include_redirect, include_record=True):
    lines = []
    lines.append(registry.condition(filters))
    if include_record:
        lines.append(f"    run = CustomShader_ExtractCB1_C{comp_id}")
        _append_lod_offset(lines, comp_id, component_res, component_res_lods)
        lines.append(f"    run = CustomShader_RecordBones_C{comp_id}")
    elif include_redirect:
        _append_lod_offset(lines, comp_id, component_res, component_res_lods)
    if include_redirect:
        lines.append(f"    run = CustomShader_RedirectCB1_C{comp_id}")
        lines.append("    vs-t0 = ResourceFakeT0_SRV")
        lines.append("    vs-cb1 = ResourceFakeCB1")
        lines.append("    vb3 = vb0")
    return "\n".join(lines)


def _buffer_key_exists(buffers, key):
    return buffers is not None and key in buffers


def _append_provider_vb2_binding(lines, provider_id, provider_component, buffers, indent="    "):
    main_key = f"Component{provider_id}_VB2"
    main_exists = _buffer_key_exists(buffers, main_key)
    lod_levels = []
    for lod_level in _component_lod_levels(provider_component):
        lod_key = f"Component{provider_id}_VB2_LOD{lod_level}"
        if _buffer_key_exists(buffers, lod_key):
            lod_levels.append(lod_level)

    if not main_exists and not lod_levels:
        return

    if not lod_levels:
        lines.append(f"{indent}vb2 = ref Resource_Component{provider_id}_VB2")
        return

    if main_exists:
        lines.append(f"{indent}if $lod_level == 0")
        lines.append(f"{indent}    vb2 = ref Resource_Component{provider_id}_VB2")
        for lod_level in lod_levels:
            lines.append(f"{indent}elif $lod_level == {lod_level}")
            lines.append(f"{indent}    vb2 = ref Resource_Component{provider_id}_VB2_LOD{lod_level}")
        lines.append(f"{indent}else")
        lines.append(f"{indent}    vb2 = ref Resource_Component{provider_id}_VB2")
        lines.append(f"{indent}endif")
        return

    first_lod = lod_levels[0]
    lines.append(f"{indent}if $lod_level == {first_lod}")
    lines.append(f"{indent}    vb2 = ref Resource_Component{provider_id}_VB2_LOD{first_lod}")
    for lod_level in lod_levels[1:]:
        lines.append(f"{indent}elif $lod_level == {lod_level}")
        lines.append(f"{indent}    vb2 = ref Resource_Component{provider_id}_VB2_LOD{lod_level}")
    lines.append(f"{indent}endif")


def _build_registry(source_folder=None, frame_dump_folder=None, consume_sidecar=True):
    # consume_sidecar=False forces a fresh scan (used by sidecar GENERATION, which
    # must never read back its own possibly-stale CrossIB.json).
    if consume_sidecar:
        from . import sidecar
        data = sidecar.load_crossib_json(source_folder)
        if data is not None:
            print("[CrossIB] consuming baked CrossIB.json (no frame-dump scan).")
            return sidecar.JsonBackedPassRegistry(data)

    if not frame_dump_folder:
        return build_pass_registry(source_folder)

    registry = CrossIBPassRegistry()
    source_path = Path(source_folder) if source_folder else None
    dump_path = Path(frame_dump_folder)
    if source_path is None or not source_path.is_dir() or not dump_path.is_dir():
        return registry

    source_path = registry._resolve_source_folder(source_path)
    ib_to_component = registry._load_ib_map(source_path)
    registry._load_texture_usage(source_path)
    for record in registry._scan_frame_analysis(dump_path, ib_to_component):
        registry._records_by_component.setdefault(record.component_id, []).append(record)
        registry._exact_components.add(record.component_id)
        registry._note_hash(record.vs_hash)
    registry._finalize_unknown_filters()
    return registry


def render_shader_override_ini(registry):
    """Render the global [ShaderOverrideCrossIB_*] blocks for a registry.

    Shared by the in-mod resource emission (build_cross_ib) and the extract-time
    standalone ShaderOverride.ini sidecar, so both always use the same canonical
    hash->filter map.
    """
    lines = []
    # Sort by filter_index (then hash) so the file groups 200s, 201s, ... 206s
    # in order instead of defaults-then-appended-unknowns.
    pairs = sorted(registry.shader_overrides(), key=lambda hf: (hf[1], hf[0]))
    for idx, (hash_val, fi) in enumerate(pairs):
        lines.append(f"[ShaderOverrideCrossIB_vs{idx}]")
        lines.append(f"hash = {hash_val}")
        lines.append(f"filter_index = {fi}")
        lines.append("allow_duplicate_hash = overrule")
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def _log_standard_transparent_receivers(registry, target_ids):
    for target_id in sorted(set(target_ids)):
        if _target_is_standard_transparent(registry, target_id):
            print(f"[CrossIB] standard transparent receiver by pass shape: C{target_id}")


def _classify_transparency(source_folder=None, frame_dump_folder=None):
    from . import sidecar
    data = sidecar.load_crossib_json(source_folder)
    if data is not None:
        return sidecar.JsonBackedTransparency(data)

    if not source_folder and not frame_dump_folder:
        return None
    try:
        # character folder (Metadata/DDS/geometry) = object source; frame_root
        # (log.txt + draw files) = frame dump — resolved independently so a
        # non-co-located extract still classifies transparency.
        result = classify_frame_analysis_transparency(
            source_folder or frame_dump_folder, frame_root=frame_dump_folder
        )
    except Exception as exc:
        print(f"[CrossIB] Actual transparency classification failed: {exc}")
        return None
    for line in result.report_lines:
        print(f"[CrossIB] {line}")
    return result


def _target_is_actual_transparent(transparency, target_id):
    return bool(
        transparency is not None
        and transparency.is_actual_transparent_component(target_id)
    )


def _target_is_standard_transparent(registry, target_id):
    target_record_filters = registry.record_filters(target_id)
    target_self_filters = registry.provider_self_filters(target_id)
    target_borrow_filters = registry.consumer_borrow_filters(target_id)

    return (
        target_record_filters
        and set(target_self_filters).issubset(set(target_record_filters))
        and target_borrow_filters == [202]
    )


def _provider_geometry_afterimage_filters(registry, provider_id, target_id):
    """Hybrid provider cross-block filter (replaces the is_actual_transparent
    binary special-case that collapsed every transparent target to [200]).

    Geometry passes (record 200 / prepass 201) follow the TARGET's own pass set
    — whether the borrowed mesh needs the opaque G-buffer prepass is the
    target's material property: a pure-transparent target has no 201 (so it is
    dropped naturally), a hair-like partial-transparent target with a real
    prepass keeps it. Afterimage 204 follows the PROVIDER — the residual-image
    is a whole-character pass over the provider's own geometry, so the borrowed
    mesh ghosts iff the provider participates (keying 204 to the target would
    drop it whenever the target's afterimage frame was not captured).
    """
    geom = [f for f in registry.provider_self_filters(target_id) if f in (200, 201)]
    after = [f for f in registry.provider_self_filters(provider_id) if f == 204]
    filters = sorted(set(geom) | set(after))
    return filters or registry.record_filters(provider_id)


def _provider_preparation_filters_for_mapping(registry, provider_id, target_id, transparency=None):
    # standard_transparent (structural pass-shape heuristic, not DDS-confirmed)
    # keeps its original record-only preparation; everything else
    # (actual_transparent + opaque) uses the hybrid geometry/afterimage split.
    if _target_is_standard_transparent(registry, target_id) and not _target_is_actual_transparent(transparency, target_id):
        return registry.record_filters(provider_id)
    return _provider_geometry_afterimage_filters(registry, provider_id, target_id)


def _provider_draw_filters_for_mapping(registry, provider_id, target_id, transparency=None):
    if _target_is_standard_transparent(registry, target_id) and not _target_is_actual_transparent(transparency, target_id):
        return registry.provider_material_filters(provider_id) or registry.record_filters(provider_id)
    return _provider_geometry_afterimage_filters(registry, provider_id, target_id)


def _provider_filters_for_mapping(registry, provider_id, target_id, transparency=None):
    return _provider_preparation_filters_for_mapping(registry, provider_id, target_id, transparency)


def build_cross_ib(crossib_settings, extracted_object, buffers, merged_object, source_folder=None, frame_dump_folder=None, cfg=None, context=None):
    """Compute providers/consumers/resources for the given mappings.

    v1.5 architecture (per dev guidance — both bug fixes in one):

    1. ALL participating components (providers ∪ consumers) extract their own
       CB1 + record their own bones in shadow pass — each into a unique
       per-component DumpedCB1_C{N} buffer pair and at a unique FakeT0 offset
       (cs-t2 = ResourceID_CrossIB_C{N}).

    2. In a consumer's color-pass borrow block, RedirectCB1_C{consumer_id}
       reads the CONSUMER's own DumpedCB1 (preserving consumer's world
       transform) and rewrites only CB1[5] to point at the PROVIDER's bone
       offset in FakeT0. Result: borrowed mesh appears at the consumer's
       world position with the provider's bone palette — fixes the v1.4 bug
       where every borrowed mesh ended up at the last provider's position.

    3. vs filter is per-MAPPING (not merged): each mapping emits its own
       provider-side `if vs == ...` block on the provider component, with its
       OWN vs condition and its OWN draws. Multiple mappings on the same
       provider yield multiple if blocks. Extract is idempotent (writes to
       same DumpedCB1_C{N}) so repeated extract per-block is harmless.

    Returns:
      providers_map[comp_id] = {
          "mapping_blocks": list[dict] each with:
              "vs_cond": str (e.g. "if vs == 200 || vs == 201"),
              "borrowed_objs": set[str] of obj names this block draws,
          "borrowed_objs": set[str] union of all borrowed_objs (used by
              patch.py to identify non-borrowed sub-meshes),
          "is_consumer_only": False,
      }
      For a consumer-only component (in consumers but not providers):
      providers_map[comp_id] = {
          "mapping_blocks": [{
              "vs_cond": "if vs == 200 || vs == 201",
              "borrowed_objs": set(),  # no draws inside
          }],
          "borrowed_objs": set(),
          "is_consumer_only": True,
      }
      consumers_map[comp_id] = str (consumer borrow blocks)
      resources_str = str
    """
    providers = set()                 # components used as bone source by some mapping
    consumers = set()                 # components targeted by some mapping
    consumer_to_providers = {}        # comp_id -> ordered list of provider comp_ids
    consumer_provider_objs = {}       # (consumer_id, provider_id) -> set[str]
    # Per-mapping bookkeeping. The old UI-provided vs condition is now replaced
    # by auto-discovered provider self filters from the source dump registry.
    # in the order the user added them in the UI.
    provider_mapping_blocks = {}

    for mapping in crossib_settings.mappings:
        target_comp_id = mapping.target_component
        m_objs_per_provider = {}  # provider_id -> set[str] for THIS mapping
        for src_name in _source_name_aliases(mapping, cfg, context):
            src_comp_id = parse_component_id(src_name)
            if src_comp_id is None or src_comp_id == target_comp_id:
                continue
            providers.add(src_comp_id)
            consumers.add(target_comp_id)
            bucket = consumer_to_providers.setdefault(target_comp_id, [])
            if src_comp_id not in bucket:
                bucket.append(src_comp_id)
            consumer_provider_objs.setdefault(
                (target_comp_id, src_comp_id), set()
            ).add(src_name)
            m_objs_per_provider.setdefault(src_comp_id, set()).add(src_name)

        # One provider mapping_block per (mapping, provider). The concrete
        # self-draw condition is decided after the pass registry has been built.
        for src_comp_id, names in m_objs_per_provider.items():
            provider_mapping_blocks.setdefault(src_comp_id, []).append({
                "borrowed_objs": names,
                "target_component": target_comp_id,
            })

    if not providers:
        return {}, {}, ""

    # All components needing per-component DumpedCB1 + extract/record/redirect.
    # Consumers need their own extract too (so RedirectCB1_C{consumer} can read
    # the consumer's own world transform).
    participating = sorted(providers | consumers)
    registry = _build_registry(source_folder, frame_dump_folder)
    _log_standard_transparent_receivers(registry, consumers)
    transparency = _classify_transparency(source_folder, frame_dump_folder)


    # ── Resource declarations ──────────────────────────────────
    res = []
    res.append("")
    res.append("; Cross Index Buffer -------------------------")
    res.append("")
    res.append("[ResourceFakeCB1_UAV]")
    res.append("type = RWStructuredBuffer")
    res.append("stride = 16")
    res.append("array = 4096")
    res.append("")
    res.append("[ResourceFakeCB1]")
    res.append("type = Buffer")
    res.append("stride = 16")
    res.append("format = R32G32B32A32_UINT")
    res.append("array = 4096")
    res.append("")
    res.append("[ResourceFakeT0_UAV]")
    res.append("type = RWStructuredBuffer")
    res.append("stride = 16")
    res.append("array = 200000")
    res.append("")
    res.append("[ResourceFakeT0_SRV]")
    res.append("type = StructuredBuffer")
    res.append("stride = 16")
    res.append("array = 200000")
    res.append("")

    component_res = {}
    component_res_lods = {}
    max_lod_count = max(
        (len(getattr(_get_component(extracted_object, comp_id), "lods", ()) or ()) for comp_id in participating),
        default=0,
    )
    fake_t0_slot_stride = (max_lod_count + 1) * 1000
    for i, comp_id in enumerate(participating):
        offset = i * fake_t0_slot_stride
        name = f"ResourceID_CrossIB_C{comp_id}"
        component_res[comp_id] = name
        res.append(f"[{name}]")
        res.append("type = Buffer")
        res.append("format = R32_FLOAT")
        res.append(f"data = {float(offset)}")
        res.append("")
        for lod_level in _component_lod_levels(_get_component(extracted_object, comp_id)):
            lod_offset = offset + lod_level * 1000
            name_lod = f"ResourceID_CrossIB_C{comp_id}_LOD{lod_level}"
            component_res_lods.setdefault(comp_id, {})[lod_level] = name_lod
            res.append(f"[{name_lod}]")
            res.append("type = Buffer")
            res.append("format = R32_FLOAT")
            res.append(f"data = {float(lod_offset)}")
            res.append("")

    # ── Per-component DumpedCB1 + Extract/Record/Redirect CustomShaders ──
    # CrossIB v1.5 (per dev guidance): every participating component (provider
    # OR consumer) gets its OWN DumpedCB1 buffer pair so that:
    #   * Multiple providers don't overwrite each other in shadow pass
    #     (was the v1.3/early-v1.4 bug)
    #   * Each consumer can RedirectCB1 from ITS OWN DumpedCB1 in color pass,
    #     preserving the consumer's world transform while only swapping the
    #     bone-offset pointer (CB1[5]) to the provider's bones via cs-t2.
    for comp_id in participating:
        res.append(f"[ResourceDumpedCB1_C{comp_id}_UAV]")
        res.append("type = RWStructuredBuffer")
        res.append("stride = 16")
        res.append("array = 4096")
        res.append("")
        res.append(f"[ResourceDumpedCB1_C{comp_id}_SRV]")
        res.append("type = Buffer")
        res.append("stride = 16")
        res.append("array = 4096")
        res.append("")

    for comp_id in participating:
        res.append(f"[CustomShader_ExtractCB1_C{comp_id}]")
        res.append("vs = hlsl/extract_cb1_vs.hlsl")
        res.append("ps = hlsl/extract_cb1_ps.hlsl")
        res.append(f"ps-u7 = ResourceDumpedCB1_C{comp_id}_UAV")
        res.append("depth_enable = false")
        res.append("blend = ADD SRC_ALPHA INV_SRC_ALPHA")
        res.append("cull = none")
        res.append("topology = point_list")
        res.append("draw = 4096, 0")
        res.append("ps-u7 = null")
        res.append(f"ResourceDumpedCB1_C{comp_id}_SRV = copy ResourceDumpedCB1_C{comp_id}_UAV")
        res.append("")
        res.append(f"[CustomShader_RecordBones_C{comp_id}]")
        res.append("cs = hlsl/record_bones_cs.hlsl")
        res.append("cs-t0 = vs-t0")
        res.append(f"cs-t1 = ResourceDumpedCB1_C{comp_id}_SRV")
        res.append("cs-u1 = ResourceFakeT0_UAV")
        res.append("dispatch = 12, 1, 1")
        res.append("cs-u1 = null")
        res.append("cs-t0 = null")
        res.append("cs-t1 = null")
        res.append("ResourceFakeT0_SRV = copy ResourceFakeT0_UAV")
        res.append("")
        res.append(f"[CustomShader_RedirectCB1_C{comp_id}]")
        res.append("cs = hlsl/redirect_cb1_cs.hlsl")
        res.append(f"cs-t0 = ResourceDumpedCB1_C{comp_id}_SRV")
        res.append("cs-u0 = ResourceFakeCB1_UAV")
        res.append("dispatch = 4, 1, 1")
        res.append("cs-u0 = null")
        res.append("cs-t0 = null")
        res.append("ResourceFakeCB1 = copy ResourceFakeCB1_UAV")
        res.append("")

    # ── providers_map: per-component if blocks emitted in patch.py ──────
    # For each participating component we describe what shadow-pass if blocks
    # to emit at the top of its CommandList. patch.py uses borrowed_objs to
    # route each `; Draw <name>` entry into the right block (or into the
    # "non-borrowed, drawn unconditionally" group after all blocks).
    providers_map = {}
    for comp_id in participating:
        is_provider = comp_id in providers
        is_consumer = comp_id in consumers
        mapping_blocks_out = []

        record_filters = registry.record_filters(comp_id)
        self_filters = registry.provider_self_filters(comp_id) if is_provider else []
        record_covered_by_self = bool(
            is_provider
            and record_filters
            and set(record_filters).issubset(set(self_filters))
        )
        if not record_covered_by_self:
            mapping_blocks_out.append({
                "header": _build_run_header(
                    comp_id, record_filters, registry,
                    component_res, component_res_lods,
                    include_redirect=False,
                ),
                "borrowed_objs": set(),
            })

        if is_provider:
            # One self-draw block per (mapping, this provider). The pass
            # registry decides visible provider filters from the source data.
            for mb in provider_mapping_blocks[comp_id]:
                preparation_filters = _provider_preparation_filters_for_mapping(
                    registry, comp_id, mb["target_component"], transparency
                )
                draw_filters = _provider_draw_filters_for_mapping(
                    registry, comp_id, mb["target_component"], transparency
                )
                if preparation_filters != draw_filters:
                    mapping_blocks_out.append({
                        "header": _build_run_header(
                            comp_id, preparation_filters, registry,
                            component_res, component_res_lods,
                            include_redirect=False,
                        ),
                        "borrowed_objs": set(),
                    })
                mapping_blocks_out.append({
                    "header": _build_run_header(
                        comp_id, draw_filters, registry,
                        component_res, component_res_lods,
                        include_redirect=True,
                        include_record=(preparation_filters == draw_filters),
                    ),
                    "borrowed_objs": set(mb["borrowed_objs"]),
                })

        # Union of all sub-mesh names borrowed FROM this provider by any
        # consumer (used by patch.py to know which draws are non-borrowed).
        borrowed = set()
        for (cid, pid), names in consumer_provider_objs.items():
            if pid == comp_id:
                borrowed |= names

        providers_map[comp_id] = {
            "mapping_blocks": mapping_blocks_out,
            "borrowed_objs": borrowed,
            "is_consumer_only": (is_consumer and not is_provider),
        }

    # ── Consumer post-blocks (one auto-discovered visible block per borrowed provider) ───
    consumers_map = {}
    for consumer_id, provider_ids in consumer_to_providers.items():
        all_blocks = []
        all_blocks.append(
            "; Cross-IB: Borrow provider Component(s) "
            + ", ".join(str(p) for p in provider_ids)
            + " (bones + mesh)"
        )
        all_blocks.append("vs-t0 = ResourceFakeT0_SRV")
        all_blocks.append("vs-cb1 = ResourceFakeCB1")

        for provider_id in provider_ids:
            provider_component = _get_component(extracted_object, provider_id)
            b = []
            b.append(f"; --- Borrow provider Component {provider_id} ---")
            b.append(registry.condition(registry.consumer_borrow_filters(consumer_id)))
            _append_lod_offset(b, provider_id, component_res, component_res_lods)
            # CrossIB v1.5: read CONSUMER's own DumpedCB1 (preserves consumer
            # transform) and only flip CB1[5] → provider bones via cs-t2.
            b.append(f"    run = CustomShader_RedirectCB1_C{consumer_id}")
            b.append(f"    ib  = ref Resource_Component{provider_id}_IB")
            b.append(f"    vb0 = ref Resource_Component{provider_id}_VB0")
            b.append(f"    vb1 = ref Resource_Component{provider_id}_VB1")
            _append_provider_vb2_binding(b, provider_id, provider_component, buffers)
            b.append("    vb3 = vb0")
            objs = merged_object.components[provider_id].objects
            wanted = consumer_provider_objs.get((consumer_id, provider_id))
            if not objs:
                b.append("    ; (no provider objects to draw)")
            else:
                emitted = 0
                for obj in objs:
                    if not _name_matches_wanted(wanted, obj.name):
                        continue  # User did not select this sub-mesh for borrowing.
                    b.append(f"    ; Draw provider {obj.name}")
                    b.append(
                        f"    drawindexedinstanced = {obj.index_count}, INSTANCE_COUNT, "
                        f"{obj.index_offset}, 0, FIRST_INSTANCE"
                    )
                    emitted += 1
                if emitted == 0:
                    b.append(
                        "    ; (no matching provider sub-meshes \u2014 selection "
                        "resolved to zero objects)"
                    )
            b.append("endif")
            all_blocks.append("\n".join(b))

        consumers_map[consumer_id] = "\n".join(all_blocks)

    shader_override_ini = render_shader_override_ini(registry)
    if shader_override_ini:
        res.append(shader_override_ini)
        res.append("")

    resources_str = "\n".join(res)

    return providers_map, consumers_map, resources_str
