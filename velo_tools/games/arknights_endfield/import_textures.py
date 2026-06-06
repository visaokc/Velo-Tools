"""Velo import-time texture assignment restored from the 0.9.4 workflow."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

try:
    import bpy
except ModuleNotFoundError:  # pragma: no cover - exercised by non-Blender unit tests
    bpy = None


BC7_UNORM_SRGB = 0x63


@dataclass
class TextureAssignSummary:
    assigned: int = 0
    missing_json: int = 0
    missing_texture: int = 0
    skipped: int = 0


def dds_format_byte(filepath: Path) -> int | None:
    try:
        with Path(filepath).open("rb") as handle:
            handle.seek(0x80)
            value = handle.read(1)
            return value[0] if value else None
    except Exception:
        return None


def parse_mesh_component(mesh_name: str) -> dict[str, str] | None:
    match = re.search(r"Component\s+(\d+)(?:\s+([a-f0-9]{8,}))?", mesh_name, re.IGNORECASE)
    if not match:
        return None
    return {"component_id": match.group(1), "hash": (match.group(2) or "").lower()}


def load_texture_usage(folder: Path) -> dict[str, dict[str, object]]:
    json_path = Path(folder) / "TextureUsage.json"
    if not json_path.exists():
        return {}

    data = json.loads(json_path.read_text(encoding="utf-8"))
    mapping: dict[str, dict[str, object]] = {}

    for component_name, slots in data.items():
        id_match = re.search(r"\d+", component_name)
        if not id_match:
            continue

        t0_entries = slots.get("ps-t0", [])
        if not t0_entries:
            continue

        t0_hashes: list[str] = []
        for entry in t0_entries:
            match = re.match(r"^([a-f0-9]+)-vs=", entry, re.IGNORECASE)
            if match:
                t0_hashes.append(match.group(1).lower())

        if not t0_hashes:
            continue

        t10_t19_counts: dict[str, int] = {}
        for slot_name, entries in slots.items():
            if not re.match(r"^ps-t1[0-9]$", slot_name):
                continue
            for entry in entries:
                match = re.match(r"^([a-f0-9]+)-vs=", entry, re.IGNORECASE)
                if match:
                    key = match.group(1).lower()
                    t10_t19_counts[key] = t10_t19_counts.get(key, 0) + 1

        mapping[id_match.group(0)] = {
            "t0": t0_hashes,
            "t10_t19_counts": t10_t19_counts,
        }

    return mapping


def build_hash_to_filepath(folder: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in Path(folder).glob("*.dds"):
        for match in re.finditer(r"[a-f0-9]{8,}", path.name, re.IGNORECASE):
            result.setdefault(match.group(0).lower(), path)
    return result


def resolve_texture(comp_data: dict[str, object], hash_to_file: dict[str, Path]) -> Path | None:
    seen = set()
    candidates: list[tuple[str, Path]] = []
    for texture_hash in comp_data.get("t0", []):
        texture_hash = str(texture_hash).lower()
        if texture_hash in seen:
            continue
        path = hash_to_file.get(texture_hash)
        if path is None:
            continue
        seen.add(texture_hash)
        candidates.append((texture_hash, path))

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0][1]

    counts = comp_data.get("t10_t19_counts", {})
    scored = sorted(candidates, key=lambda item: counts.get(item[0], 0), reverse=True)
    top_count = counts.get(scored[0][0], 0)
    tied = [item for item in scored if counts.get(item[0], 0) == top_count]
    if len(tied) == 1:
        return tied[0][1]

    srgb = [item for item in tied if dds_format_byte(item[1]) == BC7_UNORM_SRGB]
    if len(srgb) == 1:
        return srgb[0][1]

    pool = srgb if srgb else tied
    return max(pool, key=lambda item: item[1].stat().st_size)[1]


def _assign_image_to_mesh(obj, filepath: Path) -> None:
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

    node_tex = nodes.new("ShaderNodeTexImage")
    node_bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    node_out = nodes.new("ShaderNodeOutputMaterial")

    node_tex.location = (-300, 0)
    node_bsdf.location = (0, 0)
    node_out.location = (300, 0)

    img = bpy.data.images.get(filepath.name) or bpy.data.images.load(str(filepath))
    img.colorspace_settings.name = "sRGB"
    img.alpha_mode = "NONE"
    node_tex.image = img

    links.new(node_tex.outputs["Color"], node_bsdf.inputs["Base Color"])
    links.new(node_bsdf.outputs["BSDF"], node_out.inputs["Surface"])


def assign_textures(texture_folder, *, objects=None, debug: bool = False) -> TextureAssignSummary:
    if bpy is None:
        raise RuntimeError("Blender bpy module is required to assign imported textures")

    folder = Path(bpy.path.abspath(str(texture_folder)))
    summary = TextureAssignSummary()
    if not folder.exists():
        return summary

    comp_to_data = load_texture_usage(folder)
    if not comp_to_data:
        return summary

    hash_to_file = build_hash_to_filepath(folder)
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

        comp_data = comp_to_data.get(info["component_id"])
        if comp_data is None:
            summary.missing_json += 1
            continue

        filepath = resolve_texture(comp_data, hash_to_file)
        if filepath is None:
            summary.missing_texture += 1
            continue

        _assign_image_to_mesh(obj, filepath)
        summary.assigned += 1
        if debug:
            print(f"[Velo import texture] {obj.name} <- {filepath.name}")

    return summary
