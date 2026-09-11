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


IMAGE_EXTENSIONS = {".dds", ".png", ".jpg", ".jpeg", ".tga", ".bmp"}


def read_evidence(folder, component):
    """Only retained source files participate in authoring; STU stays untouched."""
    folder = Path(folder)
    payload = json.loads((folder / "ShaderTextureUsage.json").read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid ShaderTextureUsage.json")
    catalog = source_catalog(payload, component)
    candidates = sorted((path for path in folder.iterdir()
                         if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
                        key=lambda path: path.name.casefold())
    by_name = {path.name.casefold(): path for path in candidates}
    by_identity = {}
    for path in candidates:
        by_identity.setdefault(texture_identity(path.name), []).append(path)
    retained = {}
    for identity, row in catalog.items():
        files = list(by_identity.get(identity, ()))
        for name in row["names"]:
            # A recorded file must still live directly in this source folder.
            basename = str(name).replace("\\", "/").rsplit("/", 1)[-1]
            candidate = by_name.get(basename.casefold())
            if candidate and candidate not in files and texture_identity(candidate.name) in ("", identity):
                files.append(candidate)
        if not files:
            continue
        row["files"] = sorted(path.name for path in files)
        row["names"] = list(dict.fromkeys(row["files"] + row["names"]))
        if not row["formats"]:
            for candidate in files:
                if candidate.suffix.lower() == ".dds":
                    hint = dds_format(candidate)
                    if hint and hint not in row["formats"]:
                        row["formats"].append(hint)
        retained[identity] = row
    for row in retained.values():
        row["peers"] = [identity for identity in row["peers"] if identity in retained]
    return retained



def catalog_fingerprint(catalog):
    canonical = [(key, sorted(row["formats"])) for key, row in sorted(catalog.items())]
    return hashlib.sha256(json.dumps(canonical).encode("utf-8")).hexdigest()


def pack_sources(game, component, catalog, bindings):
    return json.dumps({"version": SCHEMA, "game": game, "component": component,
                       "catalog": catalog, "fingerprint": catalog_fingerprint(catalog),
                       "bindings": bindings, "manual": {}, "omitted": {}}, sort_keys=True)


def unpack_sources(material):
    value = json.loads(str(material.get(DATA_KEY, "{}")))
    if value and value.get("version") != SCHEMA:
        raise ValueError("Unsupported material texture schema")
    return value


def resolve_sources(game, component, catalog, previous=None, image_identities=None, witnesses=None):
    """Apply unique suggestions immediately, preserving explicit edits and omissions.

    Legacy saved bindings cannot be distinguished from manual edits, so migrate
    them conservatively. No confirmation state is required or written.
    """
    previous = previous or {}
    same_scope = previous.get("game") == game and previous.get("component") == component
    old = previous if same_scope else {}
    manual = dict(old.get("manual", old.get("bindings", {})))
    manual = {role: identity for role, identity in manual.items() if role in ROLES}
    image_identities = image_identities or {}
    witnesses = witnesses or {}
    old_bindings = old.get("bindings", {})
    diffuse = manual.get("DIFFUSE") or image_identities.get("DIFFUSE") or old_bindings.get("DIFFUSE", "")
    if diffuse not in catalog:
        choices = set(witnesses.get("DIFFUSE", ())) & catalog.keys()
        diffuse = next(iter(choices)) if len(choices) == 1 else ""
    bindings = infer_bindings(catalog, game, diffuse)
    for role in ROLES:
        direct = image_identities.get(role)
        candidates = set(witnesses.get(role, ())) & catalog.keys()
        if direct in catalog:
            bindings[role] = direct
        elif role not in bindings and len(candidates) == 1:
            bindings[role] = next(iter(candidates))
    # Changing the diffuse witness changes the associated material map family.
    diffuse = manual.get("DIFFUSE", bindings.get("DIFFUSE", ""))
    if diffuse in catalog:
        associated = infer_bindings(catalog, game, diffuse)
        for role in ROLES:
            if role not in manual and role not in image_identities and role not in witnesses:
                bindings.pop(role, None)
                if role in associated:
                    bindings[role] = associated[role]
    omitted = {}
    for role in ROLES:
        requested = manual.get(role, old_bindings.get(role, old.get("omitted", {}).get(role)))
        if role in manual:
            if requested in catalog:
                bindings[role] = requested
            else:
                bindings.pop(role, None)
                if requested:
                    omitted[role] = requested
        elif role not in bindings and requested and requested not in catalog:
            omitted[role] = requested
    result = json.loads(pack_sources(game, component, catalog, bindings))
    result["manual"] = manual
    result["omitted"] = omitted
    return result


def inherits_game_source(data, role):
    return role in data.get("omitted", {}) or data.get("manual", {}).get(role) == ""
