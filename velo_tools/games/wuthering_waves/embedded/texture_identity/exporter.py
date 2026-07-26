"""Consume r16 identity manifests for native TextureOverride export."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .fingerprint import FingerprintError
from .manifest import MANIFEST_FILENAME, SCHEMA_VERSION


PREVIEW_FILENAME = "TextureIdentityRules.prototype.ini.disabled"
_SECTION = re.compile(
    r"(?ms)^(?P<header>\[TextureOverride[^\]\r\n]*\][^\r\n]*(?:\r?\n))"
    r"(?P<body>.*?)(?=^\[|\Z)"
)
_HASH_LINE = re.compile(
    r"(?im)^(?P<indent>[ \t]*)hash[ \t]*=[ \t]*(?P<hash>[0-9a-f]{8})[ \t]*$"
)
_TEXTURE_HASH = re.compile(r"^[0-9a-f]{8}$")
_FINGERPRINT_PREFIX = "v3:r16:rgba8-phash:"


def _resource_name(texture_hash: str) -> str:
    return f"ResourceTextureIdentity_{texture_hash}"


def _compact_rules(
    manifest: Mapping[str, Any],
    replacements: Mapping[str, str],
) -> list[dict[str, str]]:
    if int(manifest.get("schema_version", -1)) != SCHEMA_VERSION:
        raise FingerprintError("Texture identity manifest schema version mismatch")
    textures = manifest.get("textures")
    if not isinstance(textures, Mapping):
        raise FingerprintError("Texture identity manifest has no texture inventory")

    rules = []
    for texture_hash, record in sorted(textures.items()):
        texture_hash = str(texture_hash).lower()
        if not _TEXTURE_HASH.fullmatch(texture_hash) or not isinstance(record, Mapping):
            raise FingerprintError("Texture identity manifest contains an invalid texture entry")
        fingerprint = str(record.get("fingerprint") or "")
        match_format = str(record.get("format") or "")
        if not fingerprint.startswith(_FINGERPRINT_PREFIX) or not match_format:
            raise FingerprintError(
                f"Texture identity {texture_hash} has no usable r16 fingerprint or format"
            )
        identity = f"texture:{texture_hash}"
        rules.append(
            {
                "identity": identity,
                "match_fingerprint": fingerprint,
                "match_format": match_format,
                "replacement": replacements.get(identity, _resource_name(texture_hash)),
                "replacement_filename": "",
            }
        )
    return rules


def select_rules(
    manifest: Mapping[str, Any],
    replacements: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    return _compact_rules(manifest, dict(replacements or {}))


def render_preview(
    rules: list[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None = None,
) -> str:
    del manifest
    lines = [
        "; Disabled texture identity preview.",
        "",
    ]
    for rule in rules:
        identity_hash = str(rule["identity"]).rsplit(":", 1)[-1]
        lines.extend(
            [
                f"[TextureOverrideTexture_{identity_hash}]",
                f"fingerprint = {rule['match_fingerprint']}",
                f"format = {rule['match_format']}",
                f"this = {rule['replacement']}",
                "",
            ]
        )

    declared = set()
    for rule in rules:
        resource = str(rule["replacement"])
        filename = str(rule.get("replacement_filename") or "")
        if not filename or resource in declared:
            continue
        declared.add(resource)
        lines.extend(
            [
                f"[{resource}]",
                f"filename = {filename}",
                "",
            ]
        )
    return "\n".join(lines)


def consume_manifest(source_folder: str | Path, output_folder: str | Path) -> Path | None:
    source_folder = Path(source_folder)
    manifest_path = source_folder / MANIFEST_FILENAME
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rules = select_rules(manifest)
    output_path = Path(output_folder) / PREVIEW_FILENAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_preview(rules), encoding="utf-8")
    return output_path


def apply_manifest_to_ini(
    source_folder: str | Path,
    ini_path: str | Path,
) -> int:
    source_folder = Path(source_folder)
    manifest_path = source_folder / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise FingerprintError(
            f"Texture identity mode requires {MANIFEST_FILENAME}"
        )
    ini_path = Path(ini_path)
    if not ini_path.is_file():
        raise FingerprintError("Texture identity mode requires an exported mod.ini")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rules = select_rules(manifest)
    rules_by_hash = {
        str(rule["identity"]).rsplit(":", 1)[-1].lower(): rule
        for rule in rules
    }
    if not rules_by_hash:
        raise FingerprintError("Texture identity manifest contains no usable r16 rules")

    text = ini_path.read_text(encoding="utf-8")
    replaced = 0

    def replace_section(match):
        nonlocal replaced
        body = match.group("body")
        hash_match = _HASH_LINE.search(body)
        if hash_match is None:
            return match.group(0)
        rule = rules_by_hash.get(hash_match.group("hash").lower())
        if rule is None:
            return match.group(0)
        newline = "\r\n" if "\r\n" in match.group(0) else "\n"
        indent = hash_match.group("indent")
        replacement = newline.join(
            (
                f"{indent}fingerprint = {rule['match_fingerprint']}",
                f"{indent}format = {rule['match_format']}",
            )
        )
        body = body[:hash_match.start()] + replacement + body[hash_match.end():]
        replaced += 1
        return match.group("header") + body

    transformed = _SECTION.sub(replace_section, text)
    if replaced == 0:
        raise FingerprintError(
            "Texture identity mode found no matching texture Hash overrides in mod.ini"
        )
    ini_path.write_text(transformed, encoding="utf-8")
    return replaced
