"""Consume identity manifests and render a disabled runtime-rule preview."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .fingerprint import (
    ALGORITHM_VERSION,
    DEFAULT_MINIMUM_MARGIN,
    DEFAULT_TOLERANCE_FLOOR,
    FingerprintError,
    select_common_resolution,
)
from .manifest import (
    MANIFEST_FILENAME,
    PROTOTYPE_ABI_VERSION,
    SCHEMA_VERSION,
)


PREVIEW_FILENAME = "TextureIdentityRules.prototype.ini.disabled"
LEGAL_COLLISION_POLICIES = {"reject", "merge", "require_draw_context"}


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
    *,
    tolerance_floor: float = DEFAULT_TOLERANCE_FLOOR,
    minimum_margin: float = DEFAULT_MINIMUM_MARGIN,
) -> list[dict[str, Any]]:
    _validate_manifest(manifest)
    identities = [
        identity
        for identity in manifest.get("identities") or []
        if identity.get("variants")
    ]
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for identity in identities:
        groups[_format_family(identity)].append(identity)

    replacements = dict(replacements or {})
    rules = []
    for family in sorted(groups):
        group = groups[family]
        try:
            selection = select_common_resolution(
                group,
                tolerance_floor=tolerance_floor,
                minimum_margin=minimum_margin,
            )
        except FingerprintError:
            continue
        selected_replacements = {
            replacements.get(str(identity.get("identity")), _resource_name(identity))
            for identity in group
        }
        collision_policy = "reject"
        if selection.pixel_ambiguous:
            collision_policy = (
                "merge"
                if len(selected_replacements) == 1
                else "require_draw_context"
            )
        if collision_policy not in LEGAL_COLLISION_POLICIES:
            raise FingerprintError(f"Illegal collision policy: {collision_policy}")

        group_id = f"default-r{selection.resolution}-{family}"
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
            selected_record = next(
                (
                    (variant.get("fingerprints") or {}).get(str(selection.resolution))
                    for variant in variants
                    if variant.get("variant") == selection.reference_variants[identity_id]
                ),
                {},
            ) or {}
            rules.append(
                {
                    "prototype_abi_version": PROTOTYPE_ABI_VERSION,
                    "algorithm_version": ALGORITHM_VERSION,
                    "abi_status": "blocked",
                    "abi_field_status": "aligned",
                    "abi_blockers": blockers,
                    "runtime_compatible": False,
                    "identity": identity_id,
                    "reference_variant": selection.reference_variants[identity_id],
                    "match_format_family": family,
                    "collision_group": group_id,
                    "match_resolution": selection.resolution,
                    "match_fingerprint": selection.fingerprints[identity_id],
                    "fingerprint_tolerance": selection.tolerance,
                    "tolerance_floor": selection.tolerance_floor,
                    "minimum_match_margin": selection.minimum_margin,
                    "nearest_inter_distance": selection.nearest_inter_distance,
                    "maximum_intra_distance": selection.maximum_intra_distance,
                    "pixel_ambiguous": selection.pixel_ambiguous,
                    "unavailable_resolutions": list(selection.unavailable_resolutions),
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
    for index, rule in enumerate(rules):
        lines.extend(
            [
                f"[TextureRoleOverride_{index:03d}]",
                f"; prototype_abi_version = {rule['prototype_abi_version']}",
                f"; runtime_fingerprint_algorithm_version = {rule['algorithm_version']}",
                f"; abi_status = {rule['abi_status']}",
                f"; abi_field_status = {rule['abi_field_status']}",
                f"; runtime_compatible = {str(rule['runtime_compatible']).lower()}",
                f"; canonical_format = {rule['canonical_format']}",
                f"; absolute_mip = {rule['absolute_mip']}",
                f"match_format_family = {rule['match_format_family']}",
                f"collision_group = {rule['collision_group']}",
                f"match_resolution = {rule['match_resolution']}",
                f"match_fingerprint = {rule['match_fingerprint']}",
                f"fingerprint_tolerance = {rule['fingerprint_tolerance']:.8f}",
                f"minimum_match_margin = {rule['minimum_match_margin']:.8f}",
                f"reference_variant = {rule['reference_variant']}",
                f"collision_policy = {rule['collision_policy']}",
                f"replacement = {rule['replacement']}",
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
