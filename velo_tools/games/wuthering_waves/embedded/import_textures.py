"""Velo import-time texture assignment for WWMI (WuWa-specific heuristic).

Diverged from the EFMI module it was ported from: WuWa does not keep the
diffuse at ps-t0 (slot semantics vary per shader), so candidates are gathered
from every slot of ShaderTextureUsage.json (v4 nested records carry
format/size/fresh metadata that survives file deletion; the legacy flat
schema falls back to on-disk DDS headers) and the diffuse is identified by
format + resolution + component-exclusivity. The identification is
intentionally independent of which files still exist: when the identified
diffuse was deleted from the folder, only a bare material (Principled BSDF,
no image) is created - an unrelated texture is never bound instead.

bpy is import-optional so the pure logic stays pytest-able.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from pathlib import Path

try:
    import bpy
except ModuleNotFoundError:  # pragma: no cover - exercised by non-Blender unit tests
    bpy = None

_FORM_COMPONENT_MODE_KEY = "form_component_mode"
_LEGACY_FORM_COMPONENT_MODE_KEYS = ("_velo_form_component_mode",)


# Diffuse candidates at or above this area compete on component-exclusivity
# first (a component's own texture beats a shared atlas); below it, area
# leads so small exclusive masks cannot beat a big shared diffuse atlas.
_MIN_PRIMARY_AREA = 1024 * 1024

# Tier-2 formats: legacy uncompressed color textures (no sRGB flag exists in
# their headers). BC*_UNORM linear formats are deliberately NOT eligible -
# they are the typical normal/ORM/mask formats this heuristic must avoid.
_COLOR_LINEAR_FORMATS = {
    "B8G8R8A8_UNORM",
    "B8G8R8X8_UNORM",
    "R8G8B8A8_UNORM",
}

# DX10 dxgiFormat index -> canonical name (texture-relevant subset, matching
# the vocabulary written into ShaderTextureUsage.json v4 records). A local
# mini reader is used instead of slot_textures.dds_meta because this module
# must stay importable standalone (no package context) for unit tests.
_DXGI_NAMES = {
    28: "R8G8B8A8_UNORM",
    29: "R8G8B8A8_UNORM_SRGB",
    71: "BC1_UNORM",
    72: "BC1_UNORM_SRGB",
    74: "BC2_UNORM",
    75: "BC2_UNORM_SRGB",
    77: "BC3_UNORM",
    78: "BC3_UNORM_SRGB",
    80: "BC4_UNORM",
    83: "BC5_UNORM",
    87: "B8G8R8A8_UNORM",
    88: "B8G8R8X8_UNORM",
    91: "B8G8R8A8_UNORM_SRGB",
    93: "B8G8R8X8_UNORM_SRGB",
    95: "BC6H_UF16",
    98: "BC7_UNORM",
    99: "BC7_UNORM_SRGB",
}

_FOURCC_NAMES = {
    b"DXT1": "BC1_UNORM",
    b"DXT2": "BC2_UNORM",
    b"DXT3": "BC2_UNORM",
    b"DXT4": "BC3_UNORM",
    b"DXT5": "BC3_UNORM",
    b"ATI1": "BC4_UNORM",
    b"ATI2": "BC5_UNORM",
}

_HASH_RE = re.compile(r"^[a-f0-9]{8,}$", re.IGNORECASE)
_COMPONENT_KEY_RE = re.compile(r"component[ _-]*([0-9]+)", re.IGNORECASE)
_SLOT_RE = re.compile(r"^ps-t[0-9]+$")
_NESTED_VS_RE = re.compile(r"^vs=[a-f0-9?]+$", re.IGNORECASE)


@dataclass
class TextureAssignSummary:
    assigned: int = 0
    bare: int = 0
    missing_json: int = 0
    missing_texture: int = 0
    skipped: int = 0


def parse_mesh_component(mesh_name: str) -> dict[str, str] | None:
    match = re.search(r"Component\s+(\d+)(?:\s+([a-f0-9]{8,}))?", mesh_name, re.IGNORECASE)
    if not match:
        return None
    return {"component_id": match.group(1), "hash": (match.group(2) or "").lower()}


def read_dds_header(filepath: Path) -> tuple[int, int, str] | None:
    """(width, height, canonical format name or '') from a DDS file header."""
    try:
        with Path(filepath).open("rb") as handle:
            head = handle.read(148)
    except OSError:
        return None
    if len(head) < 128 or head[:4] != b"DDS ":
        return None
    height, width = struct.unpack_from("<II", head, 12)
    pf_flags = struct.unpack_from("<I", head, 80)[0]
    four_cc = head[84:88]
    fmt = ""
    if (pf_flags & 0x4) or four_cc != b"\x00\x00\x00\x00":
        if four_cc == b"DX10" and len(head) >= 132:
            dxgi = struct.unpack_from("<I", head, 128)[0]
            fmt = _DXGI_NAMES.get(dxgi, "")
        else:
            fmt = _FOURCC_NAMES.get(four_cc, "")
    if not fmt and (pf_flags & 0x40):  # legacy RGB masks
        bits = struct.unpack_from("<I", head, 88)[0]
        r, g, b, a = struct.unpack_from("<IIII", head, 92)
        if bits == 32:
            if (r, g, b) == (0x00FF0000, 0x0000FF00, 0x000000FF):
                fmt = "B8G8R8A8_UNORM" if a else "B8G8R8X8_UNORM"
            elif (r, g, b) == (0x000000FF, 0x0000FF00, 0x00FF0000):
                fmt = "R8G8B8A8_UNORM"
    return width, height, fmt


def build_hash_to_filepath(folder: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in Path(folder).glob("*.dds"):
        for match in re.finditer(r"[a-f0-9]{8,}", path.name, re.IGNORECASE):
            result.setdefault(match.group(0).lower(), path)
    return result


def _new_candidate() -> dict[str, object]:
    return {"format": "", "width": 0, "height": 0,
            "fresh": 0, "stale": 0, "pairs": 0, "components": set()}


def _harvest_record(cand: dict[str, object], record: dict) -> None:
    if record.get("format") and not cand["format"]:
        cand["format"] = str(record["format"])
        cand["width"] = int(record.get("width") or 0)
        cand["height"] = int(record.get("height") or 0)
    fresh = record.get("fresh")
    if fresh is True:
        cand["fresh"] += 1
    elif fresh is False:
        cand["stale"] += 1


def load_component_candidates(folder: Path) -> dict[str, dict[str, dict]]:
    """{component_id: {texture_hash: candidate meta}} from
    ShaderTextureUsage.json (both schemas), falling back to TextureUsage.json
    (every slot, not just ps-t0). Returns {} when no usage data exists."""
    folder = Path(folder)
    out: dict[str, dict[str, dict]] = {}

    stu_path = folder / "ShaderTextureUsage.json"
    if stu_path.exists():
        try:
            raw = json.loads(stu_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        for comp_name, pairs in (raw or {}).items():
            comp_match = _COMPONENT_KEY_RE.search(str(comp_name))
            if not comp_match or not isinstance(pairs, dict):
                continue
            comp = out.setdefault(comp_match.group(1), {})
            for pair_key, value in pairs.items():
                if str(pair_key) == _FORM_COMPONENT_MODE_KEY:
                    continue
                if str(pair_key) in _LEGACY_FORM_COMPONENT_MODE_KEYS:
                    continue
                if not isinstance(value, dict):
                    continue
                if _NESTED_VS_RE.match(str(pair_key)):
                    # v3/v4 nested: vs key -> {ps key -> {slot -> record}}
                    for slots in value.values():
                        if not isinstance(slots, dict):
                            continue
                        seen_hashes = set()
                        for slot_name, record in slots.items():
                            if not _SLOT_RE.match(str(slot_name)):
                                continue
                            records = record if isinstance(record, list) else [record]
                            for rec in records:
                                if not isinstance(rec, dict):
                                    continue
                                tex_hash = str(rec.get("hash") or "").lower()
                                if not _HASH_RE.match(tex_hash):
                                    continue
                                cand = comp.setdefault(tex_hash, _new_candidate())
                                _harvest_record(cand, rec)
                                seen_hashes.add(tex_hash)
                        for tex_hash in seen_hashes:
                            comp[tex_hash]["pairs"] += 1
                else:
                    # legacy flat: "vs=..-ps=.." -> slot -> bare hash string
                    seen_hashes = set()
                    for slot_name, tex_hash in value.items():
                        if not _SLOT_RE.match(str(slot_name)) or not isinstance(tex_hash, str):
                            continue
                        tex_hash = tex_hash.lower()
                        if not _HASH_RE.match(tex_hash):
                            continue
                        comp.setdefault(tex_hash, _new_candidate())
                        seen_hashes.add(tex_hash)
                    for tex_hash in seen_hashes:
                        comp[tex_hash]["pairs"] += 1

    if not any(out.values()):
        tu_path = folder / "TextureUsage.json"
        if tu_path.exists():
            try:
                raw = json.loads(tu_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            for comp_name, slots in (raw or {}).items():
                comp_match = _COMPONENT_KEY_RE.search(str(comp_name))
                if not comp_match or not isinstance(slots, dict):
                    continue
                comp = out.setdefault(comp_match.group(1), {})
                for slot_name, entries in slots.items():
                    if not _SLOT_RE.match(str(slot_name)) or not isinstance(entries, list):
                        continue
                    for entry in entries:
                        tex_hash = str(entry).split("-", 1)[0].lower()
                        if not _HASH_RE.match(tex_hash):
                            continue
                        cand = comp.setdefault(tex_hash, _new_candidate())
                        cand["pairs"] += 1

    # Component-exclusivity: how many components bind each hash.
    sharing: dict[str, set] = {}
    for comp_id, cands in out.items():
        for tex_hash in cands:
            sharing.setdefault(tex_hash, set()).add(comp_id)
    for cands in out.values():
        for tex_hash, cand in cands.items():
            cand["components"] = sharing[tex_hash]

    return {comp: cands for comp, cands in out.items() if cands}


def supplement_from_files(candidates: dict[str, dict[str, dict]],
                          hash_to_file: dict[str, Path]) -> None:
    """Fill missing format/size from on-disk DDS headers (legacy extractions
    whose usage jsons carry no metadata)."""
    for cands in candidates.values():
        for tex_hash, cand in cands.items():
            if cand["format"]:
                continue
            path = hash_to_file.get(tex_hash)
            if path is None:
                continue
            header = read_dds_header(path)
            if header:
                cand["width"], cand["height"], cand["format"] = header[0], header[1], header[2]


def identify_diffuse(cands: dict[str, dict]) -> str | None:
    """The component's diffuse hash, judged purely from candidate metadata
    (file presence plays no part). None when nothing qualifies."""
    pool = list(cands.items())
    # v4 freshness evidence: stale-only candidates are inherited pipeline
    # state (scene LUTs etc.), the main junk source - drop them.
    if any(c["fresh"] or c["stale"] for _, c in pool):
        filtered = [(h, c) for h, c in pool if not (c["fresh"] == 0 and c["stale"] > 0)]
        if filtered:
            pool = filtered

    def pick(tier):
        if not tier:
            return None
        big = [(h, c) for h, c in tier
               if int(c["width"]) * int(c["height"]) >= _MIN_PRIMARY_AREA]
        def by_exclusivity(item):
            h, c = item
            return (len(c["components"]) or 99, -(int(c["width"]) * int(c["height"])),
                    -int(c["pairs"]), h)
        def by_area(item):
            h, c = item
            return (-(int(c["width"]) * int(c["height"])), len(c["components"]) or 99,
                    -int(c["pairs"]), h)
        chosen = min(big, key=by_exclusivity) if big else min(tier, key=by_area)
        return chosen[0]

    srgb = [(h, c) for h, c in pool if str(c["format"]).endswith("_SRGB")]
    result = pick(srgb)
    if result:
        return result
    color = [(h, c) for h, c in pool if str(c["format"]) in _COLOR_LINEAR_FORMATS]
    return pick(color)


def _build_material(obj):
    if bpy is None:
        raise RuntimeError("Blender bpy module is required to assign imported textures")

    mat = bpy.data.materials.get(obj.name) or bpy.data.materials.new(name=obj.name)
    mat.use_nodes = True

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    node_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    node_out = nodes.new("ShaderNodeOutputMaterial")
    node_bsdf.location = (0, 0)
    node_out.location = (300, 0)
    links.new(node_bsdf.outputs["BSDF"], node_out.inputs["Surface"])
    return mat, nodes, links, node_bsdf


def _create_bare_material(obj) -> None:
    _build_material(obj)


def _assign_image_to_mesh(obj, filepath: Path) -> None:
    _, nodes, links, node_bsdf = _build_material(obj)

    node_tex = nodes.new("ShaderNodeTexImage")
    node_tex.location = (-300, 0)

    img = bpy.data.images.get(filepath.name) or bpy.data.images.load(str(filepath))
    img.colorspace_settings.name = "sRGB"
    img.alpha_mode = "NONE"
    node_tex.image = img

    links.new(node_tex.outputs["Color"], node_bsdf.inputs["Base Color"])


def assign_textures(texture_folder, *, objects=None, debug: bool = False) -> TextureAssignSummary:
    if bpy is None:
        raise RuntimeError("Blender bpy module is required to assign imported textures")

    folder = Path(bpy.path.abspath(str(texture_folder)))
    summary = TextureAssignSummary()
    if not folder.exists():
        return summary

    candidates = load_component_candidates(folder)
    if not candidates:
        return summary

    hash_to_file = build_hash_to_filepath(folder)
    supplement_from_files(candidates, hash_to_file)

    if objects is None:
        objects = [obj for obj in bpy.data.objects if obj.type == "MESH" and not obj.data.materials]

    for obj in objects:
        if getattr(obj, "type", None) != "MESH":
            summary.skipped += 1
            continue
        if obj.data.materials:
            summary.skipped += 1
            continue

        info = parse_mesh_component(obj.name)
        if not info:
            summary.skipped += 1
            continue

        comp_cands = candidates.get(info["component_id"])
        if comp_cands is None:
            summary.missing_json += 1
            continue

        diffuse = identify_diffuse(comp_cands)
        if diffuse is None:
            # Nothing qualifies as a diffuse: never bind an unrelated texture.
            _create_bare_material(obj)
            summary.bare += 1
            continue

        filepath = hash_to_file.get(diffuse)
        if filepath is None:
            # The identified diffuse was deleted from the folder (user choice):
            # material + Principled only, no substitute texture.
            _create_bare_material(obj)
            summary.bare += 1
            summary.missing_texture += 1
            if debug:
                print(f"[Velo import texture] {obj.name}: diffuse {diffuse} not in folder, bare material")
            continue

        _assign_image_to_mesh(obj, filepath)
        summary.assigned += 1
        if debug:
            print(f"[Velo import texture] {obj.name} <- {filepath.name}")

    return summary
