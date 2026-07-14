"""Merge-time texture filename + ShaderTextureUsage re-basing for the cross-scene producer.

Pure helpers (stdlib only, no bpy / numpy / _wwmi_core) so they stay unit-testable in
isolation. When an editable IB is folded into the merge root its components are renumbered
(local 0..M -> merged next_idx..); these helpers re-base the texture filenames and the STU
component keys to that merged numbering so the merge-root folder stays self-consistent.

The 'Components-{ids}' prefix is a human-readable label only (binding is by hash), but it must
reflect the merged component numbering, not the editable IB's own 0-based extraction numbering.
"""

from __future__ import annotations

import re
import shutil
from copy import deepcopy
from pathlib import Path

from .embedded.slot_textures import constants as slot_constants
from .embedded.slot_textures import stu_metadata

# Stock texture filename shape: "Components-{ids} t=<hash> [<encoding>-<colorspace>].dds".
_TEX_NAME_RE = re.compile(r'^Components-([0-9-]+)(\s+t=.*)$', re.IGNORECASE)
_COMPONENT_KEY_RE = re.compile(r'component[ _-]*([0-9]+)', re.IGNORECASE)
_TEXTURE_HASH_RE = re.compile(r't=([0-9a-fA-F]+)')
_SLOT_KEY_RE = re.compile(r'^ps-t\d+$', re.IGNORECASE)


def remap_texture_name(name: str, id_map: dict) -> str:
    """Rewrite the leading 'Components-{ids}' of a texture filename via id_map (local -> merged).
    Names without that prefix are returned unchanged."""
    m = _TEX_NAME_RE.match(name)
    if not m:
        return name
    ids = sorted({id_map.get(int(x), int(x)) for x in m.group(1).split('-') if x != ''})
    return f"Components-{'-'.join(map(str, ids))}{m.group(2)}"


def copy_textures_remapped(src: Path, dst: Path, id_map: dict) -> int:
    """Copy '* t=<hash>.dds' textures from src into dst, deduplicated by hash, rewriting each
    file's component-id prefix via id_map. Mirrors xscene_merge._copy_textures' dedup: a hash
    already present under the base numbering wins (shared textures keep the base name; the
    remapped STU entry still locates them by hash)."""
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    have = {m.group(1) for f in dst.glob("*.dds") if (m := re.search(r"t=([0-9a-fA-F]+)", f.name))}
    copied = 0
    for f in Path(src).glob("*.dds"):
        m = re.search(r"t=([0-9a-fA-F]+)", f.name)
        if not m or m.group(1) in have:
            continue
        shutil.copy2(f, dst / remap_texture_name(f.name, id_map))
        have.add(m.group(1))
        copied += 1
    return copied


def _remap_block_filenames(obj, id_map: dict) -> None:
    """Recursively rewrite any 'filename' string field's Components-{ids} prefix via id_map, in
    place. JSON has no aliasing, so a freshly-loaded STU block can be mutated directly."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "filename" and isinstance(v, str):
                obj[k] = remap_texture_name(v, id_map)
            else:
                _remap_block_filenames(v, id_map)
    elif isinstance(obj, list):
        for item in obj:
            _remap_block_filenames(item, id_map)


def remap_editable_stu(eib_stu: dict, id_map: dict) -> dict:
    """Return {merged 'Component N' key: block} from an editable IB's ShaderTextureUsage.json,
    remapping 'Component {local}' top-level keys to merged ids (via id_map) and rewriting any
    nested record 'filename' field's prefix. Non-component keys (version /
    form_anchors / legacy extra_forms) are skipped; components whose local id is
    not in id_map are skipped."""
    out = {}
    for key, block in (eib_stu or {}).items():
        m = _COMPONENT_KEY_RE.match(str(key))
        if not m or not isinstance(block, dict):
            continue
        local = int(m.group(1))
        if local not in id_map:
            continue
        block = deepcopy(block)
        _remap_block_filenames(block, id_map)
        out[f"Component {id_map[local]}"] = block
    return out


def remap_form_component_modes(stu: dict, id_map: dict) -> dict:
    """Return remapped per-component form modes for component ids present in id_map."""
    stu = deepcopy(stu or {})
    stu_metadata.sync_form_component_modes(stu)
    out = {}
    for local, merged in sorted((id_map or {}).items()):
        value = "single"
        block = (stu or {}).get(f"Component {int(local)}")
        if isinstance(block, dict):
            value = str(block.get(slot_constants.FORM_COMPONENT_MODE_KEY)
                        or "single").lower()
            for legacy_key in slot_constants.LEGACY_FORM_COMPONENT_MODE_KEYS:
                value = str(block.get(legacy_key) or value).lower()
        legacy = (stu or {}).get("form_component_modes")
        if isinstance(legacy, dict):
            value = str(legacy.get(f"Component {int(local)}") or value).lower()
        out[f"Component {int(merged)}"] = (
            "multi" if value == "multi" else "single")
    return out


def merge_fold_form_component_modes(root_stu: dict,
                                    runtime_sources: dict,
                                    fold_data: dict) -> bool:
    """Fold source form variants into the remapped root component blocks."""
    if isinstance(root_stu, dict):
        before_modes = {
            key: (block.get(slot_constants.FORM_COMPONENT_MODE_KEY)
                  if isinstance(block, dict) else None)
            for key, block in root_stu.items()
            if _component_id(key) is not None
        }
        stu_metadata.sync_form_component_modes(root_stu)
        changed = any(
            isinstance(root_stu.get(key), dict)
            and root_stu[key].get(slot_constants.FORM_COMPONENT_MODE_KEY)
            != before_modes.get(key)
            for key in before_modes)
    else:
        changed = False
    for ib_hash, fold in (fold_data or {}).items():
        comp_map = {
            int(local): int(base)
            for local, base in ((fold or {}).get("comp_map") or {}).items()
        }
        if not comp_map:
            continue
        source = (runtime_sources or {}).get(str(ib_hash)) or {}
        stu = source.get("stu") or {}
        for entry in stu_metadata.form_entries(stu):
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or entry.get("source") or "").strip().lower()
            if not label:
                continue
            components = entry.get("components") or {}
            if not isinstance(components, dict):
                continue
            for comp_name, comp_block in components.items():
                local = _component_id(comp_name)
                if local is None or local not in comp_map:
                    continue
                root_key = f"Component {comp_map[local]}"
                block = (root_stu or {}).get(root_key)
                if not isinstance(block, dict):
                    continue
                if block.get(slot_constants.FORM_COMPONENT_MODE_KEY) != "multi":
                    block[slot_constants.FORM_COMPONENT_MODE_KEY] = "multi"
                    changed = True
                sources = block.setdefault(slot_constants.COMPONENT_SOURCES_KEY, [])
                if isinstance(sources, str):
                    sources = [sources]
                    block[slot_constants.COMPONENT_SOURCES_KEY] = sources
                source_note = (
                    f"merged {root_key} <- fold {ib_hash} local Component {local}")
                if source_note not in sources:
                    sources.append(source_note)
                    changed = True
                variants = block.setdefault(slot_constants.FORM_VARIANTS_KEY, {})
                if not isinstance(variants, dict):
                    variants = {}
                    block[slot_constants.FORM_VARIANTS_KEY] = variants
                    changed = True
                variant = deepcopy(comp_block) if isinstance(comp_block, dict) else {}
                _remap_block_filenames(variant, comp_map)
                for meta_key in ("source", "matched_by", "vb0_hash"):
                    if meta_key not in variant and entry.get(meta_key) not in (None, ""):
                        variant[meta_key] = entry.get(meta_key)
                if variants.get(label) != variant:
                    variants[label] = variant
                    changed = True
    return changed


def editable_stu_component_sources(source_label: str, id_map: dict) -> dict:
    """Return merged-component provenance for an editable IB STU rebase."""
    out = {}
    label = str(source_label or "editable")
    for local, merged in sorted((id_map or {}).items()):
        out[f"Component {int(merged)}"] = [
            f"merged Component {int(merged)} <- {label} local Component {int(local)}"
        ]
    return out


def texture_hash_from_name(name: str) -> str | None:
    m = _TEXTURE_HASH_RE.search(str(name))
    return m.group(1).lower() if m else None


def texture_name_with_components(name: str, component_ids: set[int], tex_hash: str | None = None) -> str:
    """Return a stock texture filename whose Components-* prefix uses merged component ids."""
    if not component_ids:
        return name
    tex_hash = (tex_hash or texture_hash_from_name(name) or "").lower()
    if not tex_hash:
        return name
    # Preserve everything after the hash (format/color-space suffixes, extension) from the source name.
    m = re.search(r'(\s+t=' + re.escape(tex_hash) + r'.*)$', str(name), re.IGNORECASE)
    if m:
        tail = m.group(1)
    else:
        m = re.search(r'(t=' + re.escape(tex_hash) + r'.*)$', str(name), re.IGNORECASE)
        tail = " " + m.group(1) if m else f" t={tex_hash}.dds"
    return f"Components-{'-'.join(map(str, sorted(component_ids)))}{tail}"


def _component_id(name: str) -> int | None:
    m = _COMPONENT_KEY_RE.match(str(name))
    return int(m.group(1)) if m else None


def _record_hash(rec) -> str | None:
    if isinstance(rec, dict):
        h = rec.get("hash")
    else:
        h = rec
    return str(h).lower() if h else None


def _record_fresh(rec) -> bool:
    return (not isinstance(rec, dict)) or rec.get("fresh") is not False


def _iter_pair_slots(obj):
    if isinstance(obj, dict):
        slots = {k: v for k, v in obj.items() if _SLOT_KEY_RE.match(str(k))}
        if slots:
            yield slots
            return
        for v in obj.values():
            yield from _iter_pair_slots(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_pair_slots(item)


def effective_usage_hashes(components: dict) -> tuple[set[str], set[str]]:
    """Return (effective, phantom_only) texture hashes from a component usage map.

    A shader pair with at least one fresh/unknown-fresh seat is an effective consumer,
    and keeps all replaced seats in that pair. A pair whose every recorded seat is
    explicitly stale is a phantom inherited-state pair and does not keep root DDS.
    """
    effective: set[str] = set()
    phantom: set[str] = set()
    for comp_pairs in (components or {}).values():
        if not isinstance(comp_pairs, dict):
            continue
        for slots in _iter_pair_slots(comp_pairs):
            hashes = [_record_hash(rec) for rec in slots.values()]
            hashes = [h for h in hashes if h]
            if not hashes:
                continue
            if any(_record_fresh(rec) for rec in slots.values()):
                effective.update(hashes)
            else:
                phantom.update(hashes)
    return effective, phantom - effective


def _component_map(stu: dict, predicate=None) -> dict:
    out = {}
    for key, block in (stu or {}).items():
        cid = _component_id(key)
        if cid is None or not isinstance(block, dict):
            continue
        if predicate is None or predicate(cid):
            out[key] = block
    return out


def _extra_form_component_maps(stu: dict):
    for entry in stu_metadata.form_entries(stu):
        if isinstance(entry, dict):
            components = entry.get("components") or {}
            if isinstance(components, dict):
                yield components


def _read_stu(folder: Path) -> dict:
    path = Path(folder) / "ShaderTextureUsage.json"
    if not path.is_file():
        return {}
    try:
        import json
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _root_texture_files(folder: Path) -> dict[str, Path]:
    files = {}
    for f in Path(folder).glob("*.dds"):
        h = texture_hash_from_name(f.name)
        if h:
            files[h] = f
    return files


def _merge_usage_component_ids(out: dict[str, set[int]], components: dict, id_map: dict[int, int] | None = None) -> None:
    for comp_name, comp_pairs in (components or {}).items():
        local = _component_id(comp_name)
        if local is None or not isinstance(comp_pairs, dict):
            continue
        if id_map is None:
            merged = local
        else:
            if local not in id_map:
                continue
            merged = id_map[local]
        for slots in _iter_pair_slots(comp_pairs):
            for rec in slots.values():
                h = _record_hash(rec)
                if h:
                    out.setdefault(h, set()).add(int(merged))


def _route_component_map(scene: dict) -> dict[int, int]:
    mapped = {}
    for key, value in ((scene or {}).get("component_map") or {}).items():
        try:
            mapped[int(key)] = int(value)
        except Exception:
            continue
    return mapped


def cross_scene_root_texture_component_ids(merge_root: Path, manifest: dict) -> dict[str, set[int]]:
    """Map texture hash to consumers recorded by the final root STU."""
    merge_root = Path(merge_root)
    out: dict[str, set[int]] = {}

    root_stu = _read_stu(merge_root)
    _merge_usage_component_ids(out, _component_map(root_stu))
    for components in _extra_form_component_maps(root_stu):
        _merge_usage_component_ids(out, components)

    return out


def _rewrite_usage_filenames(obj, hash_to_name: dict[str, str]) -> None:
    if isinstance(obj, dict):
        h = _record_hash(obj)
        if h in hash_to_name and isinstance(obj.get("filename"), str):
            obj["filename"] = hash_to_name[h]
        for v in obj.values():
            _rewrite_usage_filenames(v, hash_to_name)
    elif isinstance(obj, list):
        for item in obj:
            _rewrite_usage_filenames(item, hash_to_name)


def canonicalize_cross_scene_root_textures(merge_root: Path, manifest: dict) -> dict:
    """Rename root DDS files so their Components-* prefix reflects merged component ids after hash dedupe."""
    merge_root = Path(merge_root)
    root_files = _root_texture_files(merge_root)
    component_ids = cross_scene_root_texture_component_ids(merge_root, manifest)
    canonical_names = {}
    renamed = {}

    for h, path in sorted(root_files.items()):
        ids = component_ids.get(h)
        if not ids:
            continue
        new_name = texture_name_with_components(path.name, ids, h)
        canonical_names[h] = new_name
        target = path.with_name(new_name)
        if target == path:
            continue
        if target.exists():
            path.unlink()
        else:
            path.rename(target)
        renamed[h] = new_name

    usage_path = merge_root / "ShaderTextureUsage.json"
    if canonical_names and usage_path.is_file():
        try:
            import json
            usage = json.loads(usage_path.read_text(encoding="utf-8"))
            _rewrite_usage_filenames(usage, canonical_names)
            stu_metadata.write_usage(usage_path, usage)
        except Exception:
            pass

    return {
        "root_texture_canonical_names": canonical_names,
        "root_textures_renamed": renamed,
        "root_texture_component_ids": {h: sorted(ids) for h, ids in sorted(component_ids.items())},
    }


def _add_reasons(reasons: dict[str, set[str]], hashes: set[str], reason: str) -> None:
    for h in hashes:
        reasons.setdefault(h, set()).add(reason)


def cross_scene_root_texture_keep_set(merge_root: Path, manifest: dict) -> tuple[set[str], dict[str, str], dict[str, str]]:
    """Compute DDS retention exclusively from the final root STU."""
    merge_root = Path(merge_root)
    root_files = _root_texture_files(merge_root)
    keep_reasons: dict[str, set[str]] = {}
    evidence_reasons: dict[str, set[str]] = {}

    def consume(components: dict, keep_reason: str, evidence_reason: str | None = None, keep: bool = True) -> None:
        effective, phantom = effective_usage_hashes(components)
        _add_reasons(evidence_reasons, effective, evidence_reason or keep_reason)
        _add_reasons(evidence_reasons, phantom, "phantom-only")
        if keep:
            _add_reasons(keep_reasons, effective, keep_reason)

    root_stu = _read_stu(merge_root)
    consume(_component_map(root_stu), "root-stu")
    for components in _extra_form_component_maps(root_stu):
        consume(components, "root-extra-form")

    keep = set(keep_reasons)
    keep_map = {h: "+".join(sorted(v)) for h, v in sorted(keep_reasons.items())}
    evidence_map = {h: "+".join(sorted(v)) for h, v in sorted(evidence_reasons.items())}
    return keep, keep_map, evidence_map


def prune_cross_scene_root_textures(merge_root: Path, manifest: dict) -> dict:
    """Delete redundant root DDS files from the aggregate delivery inventory."""
    merge_root = Path(merge_root)
    root_files = _root_texture_files(merge_root)
    keep, keep_reasons, evidence_reasons = cross_scene_root_texture_keep_set(merge_root, manifest)
    pruned = {}
    for h, path in sorted(root_files.items()):
        if h in keep:
            continue
        pruned[h] = evidence_reasons.get(h, "no-consumer")
        path.unlink()
    return {
        "root_textures_kept": sorted(set(root_files) & keep),
        "root_textures_pruned": sorted(pruned),
        "root_texture_keep_reasons": {h: keep_reasons[h] for h in sorted(set(root_files) & keep)},
        "root_texture_prune_reasons": pruned,
    }
