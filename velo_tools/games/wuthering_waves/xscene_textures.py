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
from pathlib import Path

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
    nested record 'filename' field's prefix. Non-component keys (version / extra_forms) are
    skipped; components whose local id is not in id_map are skipped."""
    out = {}
    for key, block in (eib_stu or {}).items():
        m = _COMPONENT_KEY_RE.match(str(key))
        if not m or not isinstance(block, dict):
            continue
        local = int(m.group(1))
        if local not in id_map:
            continue
        _remap_block_filenames(block, id_map)
        out[f"Component {id_map[local]}"] = block
    return out


def texture_hash_from_name(name: str) -> str | None:
    m = _TEXTURE_HASH_RE.search(str(name))
    return m.group(1).lower() if m else None


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
    for entry in (stu or {}).get("extra_forms") or []:
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


def _add_reasons(reasons: dict[str, set[str]], hashes: set[str], reason: str) -> None:
    for h in hashes:
        reasons.setdefault(h, set()).add(reason)


def cross_scene_root_texture_keep_set(merge_root: Path, routing: dict) -> tuple[set[str], dict[str, str], dict[str, str]]:
    """Compute root-level DDS hashes kept by cross-scene slot/resource consumers.

    The keep set is intentionally broader than the body slot plan: editable and
    own-buffer scene IBs keep their own slot resources, while foldable native
    local textures do not keep root DDS unless they are remapped through
    extra_forms into the body form plan. Files absent from every STU are kept as
    legacy blind-zone fallbacks.
    """
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
    keep_count = int(((routing or {}).get("base") or {}).get("component_count") or 0)
    consume(_component_map(root_stu), "root-stu")
    for components in _extra_form_component_maps(root_stu):
        consume(components, "root-extra-form")

    for scene in (routing or {}).get("scene_ibs") or []:
        source = scene.get("source_folder") or ""
        scene_stu = _read_stu(merge_root / source)
        if scene.get("foldable"):
            consume(_component_map(scene_stu), "fold-native-local", keep=False)
            comp_map = {}
            for k, v in ((scene.get("fold") or {}).get("comp_map") or {}).items():
                try:
                    comp_map[int(k)] = int(v)
                except Exception:
                    continue
            for components in _extra_form_component_maps(scene_stu):
                mapped = _component_map(
                    components,
                    lambda local: local in comp_map and 0 <= comp_map[local] < keep_count)
                consume(mapped, "fold-form")
        else:
            consume(_component_map(scene_stu), "own-buffer")
            for components in _extra_form_component_maps(scene_stu):
                consume(components, "own-buffer-form")

    for editable in (routing or {}).get("editable_ibs") or []:
        source = editable.get("source_folder") or ""
        editable_stu = _read_stu(merge_root / source)
        consume(_component_map(editable_stu), "editable")
        for components in _extra_form_component_maps(editable_stu):
            consume(components, "editable-form")

    for h in set(root_files) - set(evidence_reasons):
        keep_reasons.setdefault(h, set()).add("root-unclassified-fallback")

    keep = set(keep_reasons)
    keep_map = {h: "+".join(sorted(v)) for h, v in sorted(keep_reasons.items())}
    evidence_map = {h: "+".join(sorted(v)) for h, v in sorted(evidence_reasons.items())}
    return keep, keep_map, evidence_map


def prune_cross_scene_root_textures(merge_root: Path, routing: dict) -> dict:
    """Delete redundant root-level DDS files, leaving scene_ibs/* evidence untouched."""
    merge_root = Path(merge_root)
    root_files = _root_texture_files(merge_root)
    keep, keep_reasons, evidence_reasons = cross_scene_root_texture_keep_set(merge_root, routing)
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
