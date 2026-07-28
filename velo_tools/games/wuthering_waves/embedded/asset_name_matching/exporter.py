"""Convert native texture Hash overrides to Unreal asset-name overrides."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class AssetNameMatchError(ValueError):
    pass


_SECTION = re.compile(
    r"(?ms)^(?P<header>\[TextureOverride[^\]\r\n]*\][^\r\n]*(?:\r?\n))"
    r"(?P<body>.*?)(?=^\[|\Z)"
)
_HASH_LINE = re.compile(
    r"(?im)^(?P<indent>[ \t]*)hash[ \t]*=[ \t]*(?P<hash>[0-9a-f]{8})[ \t]*$"
)
_MOD_ENABLED_DECLARATION = re.compile(
    r"(?im)^[ \t]*global[ \t]+(?P<name>\$mod_enabled(?:_ib[0-9]+)?)[ \t]*="
)
_OBJECT_DETECTED_DECLARATION = re.compile(
    r"(?im)^[ \t]*global[ \t]+\$object_detected[ \t]*="
)
_ASSET_GATE_LINE = re.compile(
    r"(?im)^(?P<indent>[ \t]*)if[ \t]+(?:"
    r"\$object_detected(?:_ib[0-9]+)?"
    r"|\$\\WWMIv1\\enable_mods"
    r"|\$mod_enabled(?:_ib[0-9]+)?"
    r"(?:[ \t]*\|\|[ \t]*\$mod_enabled_ib[0-9]+)*"
    r")(?:[ \t]*==[ \t]*1)?[ \t]*$"
)
_MATCH_PRIORITY_LINE = re.compile(
    r"(?im)^[ \t]*match_priority[ \t]*=[^\r\n]*(?:\r?\n|\Z)"
)
_CHECK_TEXTURE_OVERRIDE_LINE = re.compile(
    r"(?im)^[ \t]*CheckTextureOverride[ \t]*=[ \t]*ps-t[0-8][ \t]*$"
)
_TEXTURE_HASH = re.compile(r"^[0-9a-f]{8}$")


def _asset_name(asset_path: str) -> str:
    separator = asset_path.rfind(".")
    if separator >= 0 and separator + 1 < len(asset_path):
        return asset_path[separator + 1:]
    separator = asset_path.rfind("/")
    if separator >= 0 and separator + 1 < len(asset_path):
        return asset_path[separator + 1:]
    return asset_path


def _walk_texture_records(value: Any):
    if isinstance(value, dict):
        texture_hash = str(value.get("hash") or "").strip().lower()
        if _TEXTURE_HASH.fullmatch(texture_hash):
            yield value
        for child in value.values():
            yield from _walk_texture_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_texture_records(child)


def asset_names_by_hash(source_folder: str | Path) -> dict[str, str]:
    source_folder = Path(source_folder)
    usage_path = source_folder / "ShaderTextureUsage.json"
    if not usage_path.is_file():
        raise AssetNameMatchError(
            "Asset-name matching requires ShaderTextureUsage.json"
        )
    try:
        usage = json.loads(usage_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AssetNameMatchError(
            "ShaderTextureUsage.json is not readable"
        ) from exc

    paths_by_hash: dict[str, set[str]] = {}
    for record in _walk_texture_records(usage):
        texture_hash = str(record.get("hash") or "").strip().lower()
        asset_path = str(record.get("asset_path") or "").strip()
        filename = str(record.get("filename") or "").strip()
        if (
            not asset_path
            or not filename
            or not (source_folder / filename).is_file()
        ):
            continue
        paths_by_hash.setdefault(texture_hash, set()).add(asset_path)
        for variant in record.get("variants") or ():
            variant_hash = str(variant or "").strip().lower()
            if _TEXTURE_HASH.fullmatch(variant_hash):
                paths_by_hash.setdefault(variant_hash, set()).add(asset_path)

    result: dict[str, str] = {}
    paths_by_name: dict[str, set[str]] = {}
    display_names: dict[str, str] = {}
    for texture_hash, paths in sorted(paths_by_hash.items()):
        if len(paths) != 1:
            raise AssetNameMatchError(
                f"Texture Hash {texture_hash} has conflicting asset paths"
            )
        asset_path = next(iter(paths))
        asset_name = _asset_name(asset_path)
        if not asset_name:
            raise AssetNameMatchError(
                f"Texture Hash {texture_hash} has an invalid asset path"
            )
        result[texture_hash] = asset_name
        name_key = asset_name.casefold()
        display_names.setdefault(name_key, asset_name)
        paths_by_name.setdefault(name_key, set()).add(asset_path)

    ambiguous = {
        name: paths for name, paths in paths_by_name.items() if len(paths) > 1
    }
    if ambiguous:
        names = ", ".join(
            sorted(display_names[name_key] for name_key in ambiguous)
        )
        raise AssetNameMatchError(
            f"Asset names resolve to multiple full paths: {names}"
        )
    return result


def apply_stu_to_ini(
        source_folder: str | Path,
        ini_path: str | Path,
) -> int:
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        raise AssetNameMatchError(
            "Asset-name matching requires an exported mod.ini"
        )
    names_by_hash = asset_names_by_hash(source_folder)
    if not names_by_hash:
        raise AssetNameMatchError(
            "ShaderTextureUsage.json contains no captured asset paths"
        )

    text = ini_path.read_text(encoding="utf-8")
    mod_enabled_variables = {
        match.group("name").lower()
        for match in _MOD_ENABLED_DECLARATION.finditer(text)
    }
    if {"$mod_enabled_ib0", "$mod_enabled_ib2"} <= mod_enabled_variables:
        mod_gate = "$mod_enabled_ib0 || $mod_enabled_ib2"
    elif (
        "$mod_enabled" in mod_enabled_variables
        and _OBJECT_DETECTED_DECLARATION.search(text) is not None
    ):
        mod_gate = "$object_detected"
    else:
        raise AssetNameMatchError(
            "Asset-name matching requires the generated texture gate"
        )
    if _CHECK_TEXTURE_OVERRIDE_LINE.search(text) is None:
        raise AssetNameMatchError(
            "Asset-name matching requires the generated draw-scoped "
            "CheckTextureOverride commands"
        )
    replaced = 0

    def replace_section(match):
        nonlocal replaced
        body = match.group("body")
        hash_match = _HASH_LINE.search(body)
        if hash_match is None:
            return match.group(0)
        asset_name = names_by_hash.get(hash_match.group("hash").lower())
        if asset_name is None:
            return match.group(0)
        indent = hash_match.group("indent")
        replacement = f"{indent}match_asset_name = {asset_name}"
        body = body[:hash_match.start()] + replacement + body[hash_match.end():]
        body, gates_replaced = _ASSET_GATE_LINE.subn(
            lambda gate_match: (
                f"{gate_match.group('indent')}if {mod_gate}"
            ),
            body,
        )
        if gates_replaced == 0:
            raise AssetNameMatchError(
                "Asset-name matching found a TextureOverride without its "
                "generated mod-enabled gate"
            )
        body = _MATCH_PRIORITY_LINE.sub("", body)
        replaced += 1
        return match.group("header") + body

    transformed = _SECTION.sub(replace_section, text)
    if replaced == 0:
        raise AssetNameMatchError(
            "Asset-name matching found no matching texture Hash overrides "
            "in mod.ini"
        )
    ini_path.write_text(transformed, encoding="utf-8")
    return replaced
