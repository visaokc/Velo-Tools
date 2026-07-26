"""Consume identity manifests for native r16 TextureOverride export."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .fingerprint import (
    ALGORITHM_VERSION,
    FingerprintError,
)
from .manifest import (
    MANIFEST_FILENAME,
    PROTOTYPE_ABI_VERSION,
    SCHEMA_VERSION,
)


PREVIEW_FILENAME = "TextureIdentityRules.prototype.ini.disabled"
LEGAL_COLLISION_POLICIES = {"reject", "merge", "require_draw_context"}
_SECTION = re.compile(
    r"(?ms)^(?P<header>\[TextureOverride[^\]\r\n]*\][^\r\n]*(?:\r?\n))"
    r"(?P<body>.*?)(?=^\[|\Z)"
)
_HASH_LINE = re.compile(
    r"(?im)^(?P<indent>[ \t]*)hash[ \t]*=[ \t]*(?P<hash>[0-9a-f]{8})[ \t]*$"
)


def _resource_name(identity: Mapping[str, Any]) -> str:
    legacy_hash = re.sub(
        r"[^0-9a-zA-Z_]",
        "_",
        str(identity.get("legacy_resource_hash") or "unknown"),
    )
    return f"ResourceTextureIdentity_{legacy_hash}"


def _format_family(identity: Mapping[str, Any]) -> str:
    families = {
        str(variant.get("format_family") or "")
        for variant in identity.get("variants") or []
    }
    if len(families) != 1 or not next(iter(families), ""):
        raise FingerprintError("Every identity variant must share one non-empty format family")
    return next(iter(families))


def _match_format(identity: Mapping[str, Any]) -> str:
    formats = {
        str(variant.get("format") or "")
        for variant in identity.get("variants") or []
    }
    if len(formats) != 1 or not next(iter(formats), ""):
        raise FingerprintError("Every identity variant must share one non-empty format")
    return next(iter(formats))


def _candidate_contexts(identity: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], bool]:
    contexts = {
        str(context.get("context_id") or ""): context
        for context in identity.get("candidate_contexts") or []
        if isinstance(context, Mapping)
    }
    selected = []
    complete = True
    for variant in identity.get("variants") or []:
        context = contexts.get(str(variant.get("context_ref") or ""))
        if context is None:
            complete = False
            continue
        selected.append(context)
        draws = context.get("draws") or []
        if (
            not context.get("vb0_hashes")
            or not context.get("ib_hashes")
            or not context.get("components")
            or not context.get("bindings")
            or not draws
            or any(
                not draw.get("vb0_hash")
                or not draw.get("ib_hash")
                or draw.get("component") is None
                for draw in draws
            )
        ):
            complete = False
    return selected, complete


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if int(manifest.get("schema_version", -1)) != SCHEMA_VERSION:
        raise FingerprintError("Texture identity manifest schema version mismatch")
    algorithm = manifest.get("algorithm") or {}
    if int(algorithm.get("version", -1)) != ALGORITHM_VERSION:
        raise FingerprintError("Texture identity algorithm version mismatch")
    runtime_contract = manifest.get("runtime_contract") or {}
    if runtime_contract.get("abi_status") != "blocked":
        raise FingerprintError("Prototype exporter requires abi_status=blocked")


def select_rules(
    manifest: Mapping[str, Any],
    replacements: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    _validate_manifest(manifest)
    identities = [
        identity
        for identity in manifest.get("identities") or []
        if identity.get("variants")
    ]
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for identity in identities:
        groups[_match_format(identity)].append(identity)

    replacements = dict(replacements or {})
    rules = []
    for match_format in sorted(groups):
        group = groups[match_format]
        family = _format_family(group[0])
        selected = {}
        for identity in group:
            identity_id = str(identity.get("identity"))
            variant = next(
                (
                    item
                    for item in identity.get("variants") or []
                    if item.get("variant") == "dump-base"
                ),
                None,
            )
            if variant is None:
                variant = next(
                    (
                        item
                        for item in identity.get("variants") or []
                        if item.get("variant") == "base"
                    ),
                    None,
                )
            record = ((variant or {}).get("fingerprints") or {}).get("16") or {}
            payload = str(record.get("payload") or "")
            if (
                record.get("status") != "payload-ready"
                or not payload.startswith("v3:r16:rgba8-phash:")
            ):
                selected = {}
                break
            selected[identity_id] = (variant, record, payload)
        if len(selected) != len(group):
            continue
        selected_replacements = {
            replacements.get(str(identity.get("identity")), _resource_name(identity))
            for identity in group
        }
        payloads = [selected[str(identity.get("identity"))][2] for identity in group]
        pixel_ambiguous = len(set(payloads)) != len(payloads)
        collision_policy = "reject"
        if pixel_ambiguous:
            collision_policy = (
                "merge"
                if len(selected_replacements) == 1
                else "require_draw_context"
            )
        if collision_policy not in LEGAL_COLLISION_POLICIES:
            raise FingerprintError(f"Illegal collision policy: {collision_policy}")

        group_id = (
            "default-r16-exact-"
            f"{match_format.lower()}"
        )
        for identity in group:
            identity_id = str(identity.get("identity"))
            variants = identity.get("variants") or []
            replacement_filename = str(
                (variants[0] if variants else {}).get("stable_resource_ref") or ""
            )
            contexts, context_complete = _candidate_contexts(identity)
            blockers = [
                "runtime-v3-game-parity-not-validated",
            ]
            if collision_policy == "require_draw_context":
                blockers.append("candidate-context-draw_context-mapping-not-implemented")
                if not context_complete:
                    blockers.append("candidate-context-evidence-incomplete")
            selected_variant, selected_record, selected_payload = selected[identity_id]
            rules.append(
                {
                    "prototype_abi_version": PROTOTYPE_ABI_VERSION,
                    "algorithm_version": ALGORITHM_VERSION,
                    "abi_status": "blocked",
                    "abi_field_status": "aligned",
                    "abi_blockers": blockers,
                    "runtime_compatible": False,
                    "identity": identity_id,
                    "reference_variant": selected_variant.get("variant"),
                    "match_format": match_format,
                    "match_format_family": family,
                    "collision_group": group_id,
                    "match_resolution": 16,
                    "match_fingerprint": selected_payload,
                    "pixel_ambiguous": pixel_ambiguous,
                    "collision_policy": collision_policy,
                    "candidate_contexts": contexts,
                    "candidate_context_complete": context_complete,
                    "candidate_context_abi_status": "evidence-only-unmapped",
                    "absolute_mip": selected_record.get("absolute_mip"),
                    "canonical_format": selected_record.get("canonical_format"),
                    "offline_cache_identity": selected_record.get("offline_cache_identity"),
                    "replacement": replacements.get(identity_id, _resource_name(identity)),
                    "replacement_filename": replacement_filename,
                }
            )
    return rules


def render_preview(
    rules: list[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None = None,
) -> str:
    runtime_contract = (manifest or {}).get("runtime_contract") or {}
    blockers = runtime_contract.get("abi_blockers") or [
        "runtime-v3-game-parity-not-validated",
    ]
    lines = [
        "; PROTOTYPE ONLY - runtime v3 fields are aligned, but offline/runtime parity is not validated.",
        "; This file is deliberately disabled and is not loaded by the mod.",
        "; abi_status = blocked",
        "; abi_field_status = aligned",
        f"; prototype_abi_version = {PROTOTYPE_ABI_VERSION}",
        f"; runtime_fingerprint_algorithm_version = {ALGORITHM_VERSION}",
        f"; abi_blockers = {','.join(map(str, blockers))}",
        "; Owner scope comes from the active Draw VB/IB gate; no scope fields are duplicated here.",
        "",
    ]
    for rule in rules:
        identity_hash = re.sub(
            r"[^0-9a-zA-Z_]",
            "_",
            str(rule["identity"]).rsplit(":", 1)[-1],
        )
        lines.extend(
            [
                f"[TextureOverrideTexture_{identity_hash}]",
                f"; prototype_abi_version = {rule['prototype_abi_version']}",
                f"; runtime_fingerprint_algorithm_version = {rule['algorithm_version']}",
                f"; abi_status = {rule['abi_status']}",
                f"; abi_field_status = {rule['abi_field_status']}",
                f"; runtime_compatible = {str(rule['runtime_compatible']).lower()}",
                f"; canonical_format = {rule['canonical_format']}",
                f"; absolute_mip = {rule['absolute_mip']}",
                f"fingerprint = {rule['match_fingerprint']}",
                f"format = {rule['match_format']}",
                f"collision_group = {rule['collision_group']}",
                f"collision_policy = {rule['collision_policy']}",
                "stages = ps",
                f"this = {rule['replacement']}",
            ]
        )
        for context in rule.get("candidate_contexts") or []:
            lines.append(
                "; candidate_context_evidence = "
                + json.dumps(context, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            )
        for blocker in rule.get("abi_blockers") or []:
            lines.append(f"; abi_blocker = {blocker}")
        lines.append("")

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
    output_path.write_text(render_preview(rules, manifest), encoding="utf-8")
    return output_path


def apply_manifest_to_ini(
    source_folder: str | Path,
    ini_path: str | Path,
) -> int:
    source_folder = Path(source_folder)
    manifest_path = source_folder / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise FingerprintError(
            f"Texture identity mode requires {MANIFEST_FILENAME}")
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
                f"{indent}collision_group = {rule['collision_group']}",
                f"{indent}collision_policy = {rule['collision_policy']}",
                f"{indent}stages = ps",
            )
        )
        body = body[:hash_match.start()] + replacement + body[hash_match.end():]
        replaced += 1
        return match.group("header") + body

    transformed = _SECTION.sub(replace_section, text)
    if replaced == 0:
        raise FingerprintError(
            "Texture identity mode found no matching texture Hash overrides in mod.ini")
    ini_path.write_text(transformed, encoding="utf-8")
    return replaced
