"""Texture semantics and extraction evidence, independent of Blender."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROLES = {
    "DIFFUSE": "Diffuse",
    "NORMAL": "Normal",
    "PACKED_PBR": "Packed PBR",
    "FTM": "FTM",
    "MASK": "Mask",
    "EMISSION": "Emission",
    "LIGHT_MAP": "Light Map",
    "DETAIL": "Detail",
}
DATA_KEY = "material_texture_sources"
NODE_KEY = "material_texture_inputs"
SCHEMA = 1
_HASH = re.compile(r"(?:t=|ps-t\d+-)([0-9a-f]{8})(?![0-9a-f])", re.I)
_COMPONENT = re.compile(r"component[_ -]*(\d+)", re.I)


def component_id(name):
    match = _COMPONENT.search(str(name or ""))
    return int(match.group(1)) if match else None


def texture_identity(name):
    match = _HASH.search(str(name or ""))
    return match.group(1).lower() if match else ""


def source_catalog(payload, component):
    """Collect original identities, not replacement-image formats or slot guesses.

    Role hints are intentionally weaker than the existing runtime slot planner.
    A candidate never authorizes a slot absent from that planner's safe setters.
    """
    catalog = {}
    root = payload.get(f"Component {component}", {})

    def visit(value):
        if isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, dict):
            peers = sorted({str(record.get("hash", "")).lower()
                            for key, record in value.items()
                            if re.fullmatch(r"ps-t\d+", str(key), re.I)
                            and isinstance(record, dict) and record.get("fresh") is not False
                            and re.fullmatch(r"[0-9a-f]{8}", str(record.get("hash", "")).lower())})
            for key, record in value.items():
                if re.fullmatch(r"ps-t\d+", str(key), re.I) and isinstance(record, dict):
                    identity = str(record.get("hash", "")).lower()
                    if record.get("fresh") is False or not re.fullmatch(r"[0-9a-f]{8}", identity):
                        continue
                    row = catalog.setdefault(identity, {"formats": [], "names": [], "peers": []})
                    row["peers"] = sorted(set(row["peers"]) | set(peers))
                    fmt = str(record.get("format", "")).upper().removeprefix("DXGI_FORMAT_")
                    if fmt and fmt not in row["formats"]:
                        row["formats"].append(fmt)
                    for field in ("filename", "asset_name", "asset_path", "name"):
                        name = str(record.get(field, ""))
                        if name and name not in row["names"]:
                            row["names"].append(name)
                else:
                    visit(record)
    visit(root)
    return dict(sorted(catalog.items()))


def role_hints(record, game):
    names = " ".join(record.get("names", ())).lower()
    words = set(re.findall(r"[a-z]+", names))
    explicit = {
        "DIFFUSE": {"diffuse", "albedo", "basecolor"},
        "NORMAL": {"normal", "normalmap"},
        "PACKED_PBR": {"pbr"}, "FTM": {"ftm"},
        "MASK": {"mask", "maskmap"},
        "EMISSION": {"emission", "emissive"},
        "LIGHT_MAP": {"lightmap"}, "DETAIL": {"detail"},
    }
    named = {role for role, terms in explicit.items() if words & terms}
    if named:
        return named
    formats = record.get("formats", ())
    if any("SRGB" in value for value in formats):
        return {"DIFFUSE"}
    if any(value.startswith("BC5_") for value in formats):
        return {"NORMAL"}
    if any(value == "BC7_UNORM" for value in formats):
        return {"PACKED_PBR" if game == "ENDFIELD" else "FTM"}
    return set()


def infer_bindings(catalog, game, diffuse_identity=""):
    result = {}
    for role in ROLES:
        candidates = [key for key, row in catalog.items() if role in role_hints(row, game)]
        if role != "DIFFUSE" and diffuse_identity in catalog:
            related = [key for key in candidates if diffuse_identity in catalog[key].get("peers", ())]
            if related or any("peers" in row for row in catalog.values()):
                candidates = related
        if role == "DIFFUSE" and diffuse_identity in catalog:
            result[role] = diffuse_identity
        elif len(candidates) == 1:
            result[role] = candidates[0]
    return result


def dds_format(path):
    """Read a format hint from a DDS header only; never classify pixel content."""
    with Path(path).open("rb") as stream:
        header = stream.read(148)
    if len(header) < 128 or header[:4] != b"DDS ":
        return ""
    fourcc = header[84:88]
    if fourcc == b"DX10" and len(header) >= 148:
        return {71: "BC1_UNORM", 72: "BC1_UNORM_SRGB", 74: "BC2_UNORM",
                75: "BC2_UNORM_SRGB", 77: "BC3_UNORM", 78: "BC3_UNORM_SRGB",
                80: "BC4_UNORM", 81: "BC4_SNORM", 83: "BC5_UNORM",
                84: "BC5_SNORM", 98: "BC7_UNORM", 99: "BC7_UNORM_SRGB"
                }.get(int.from_bytes(header[128:132], "little"), "")
    return {b"DXT1": "BC1_UNORM", b"DXT3": "BC2_UNORM", b"DXT5": "BC3_UNORM",
            b"ATI1": "BC4_UNORM", b"BC4U": "BC4_UNORM", b"ATI2": "BC5_UNORM",
            b"BC5U": "BC5_UNORM", b"BC5S": "BC5_SNORM"}.get(fourcc, "")


def read_evidence(folder, component):
    path = Path(folder) / "ShaderTextureUsage.json"
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid ShaderTextureUsage.json")
    catalog = source_catalog(payload, component)
    # Display actual extracted filenames even when STU omits asset-name evidence.
    for candidate in Path(folder).iterdir():
        if not candidate.is_file() or candidate.suffix.lower() not in {".dds", ".png", ".jpg", ".jpeg", ".tga", ".bmp"}:
            continue
        identity = texture_identity(candidate.name)
        row = catalog.get(identity)
        if row is None:
            continue
        if candidate.name not in row["names"]:
            row["names"].append(candidate.name)
        # Extraction evidence wins over a subsequently re-saved DDS format.
        if not row["formats"] and candidate.suffix.lower() == ".dds":
            hint = dds_format(candidate)
            if hint:
                row["formats"] = [hint]
    return catalog


def catalog_fingerprint(catalog):
    canonical = [(key, sorted(row["formats"])) for key, row in sorted(catalog.items())]
    return hashlib.sha256(json.dumps(canonical).encode("utf-8")).hexdigest()


def pack_sources(game, component, catalog, bindings):
    return json.dumps({"version": SCHEMA, "game": game, "component": component,
                       "catalog": catalog, "fingerprint": catalog_fingerprint(catalog),
                       "bindings": bindings}, sort_keys=True)


def unpack_sources(material):
    value = json.loads(str(material.get(DATA_KEY, "{}")))
    if value and value.get("version") != SCHEMA:
        raise ValueError("Unsupported material texture schema")
    return value
