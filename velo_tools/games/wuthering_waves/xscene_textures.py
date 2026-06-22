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
