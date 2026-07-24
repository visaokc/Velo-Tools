"""Consume identity manifests and render a disabled runtime-rule preview."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .fingerprint import (
    DEFAULT_MINIMUM_MARGIN,
    DEFAULT_TOLERANCE,
    FingerprintError,
    select_common_resolution,
)
from .manifest import MANIFEST_FILENAME


PREVIEW_FILENAME = "TextureIdentityRules.prototype.ini.disabled"


def _resource_name(identity: Mapping[str, Any]) -> str:
    legacy_hash = re.sub(r"[^0-9a-zA-Z_]", "_", str(identity.get("legacy_resource_hash") or "unknown"))
    return f"ResourceTextureIdentity_{legacy_hash}"


def select_rules(
    manifest: Mapping[str, Any],
    replacements: Mapping[str, str] | None = None,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    minimum_margin: float = DEFAULT_MINIMUM_MARGIN,
) -> list[dict[str, Any]]:
    identities = list(manifest.get("identities") or [])
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for identity in identities:
        variants = identity.get("variants") or []
        family = str((variants[0] if variants else {}).get("format_family") or "unknown")
        groups[family].append(identity)

    replacements = dict(replacements or {})
    rules = []
    for family in sorted(groups):
        group = groups[family]
        try:
            selection = select_common_resolution(
                group,
                tolerance=tolerance,
                minimum_margin=minimum_margin,
            )
        except FingerprintError:
            continue
        selected_replacements = {
            replacements.get(str(identity.get("identity")), _resource_name(identity))
            for identity in group
        }
        collision_policy = "none"
        if selection.pixel_ambiguous:
            collision_policy = "merge" if len(selected_replacements) == 1 else "require_draw_context"
        group_id = f"{family.lower()}-r{selection.resolution}"
        for identity in group:
            identity_id = str(identity.get("identity"))
            variants = identity.get("variants") or []
            replacement_filename = str(
                (variants[0] if variants else {}).get("stable_resource_ref") or ""
            )
            rules.append(
                {
                    "identity": identity_id,
                    "collision_group": group_id,
                    "match_resolution": selection.resolution,
                    "match_fingerprint": selection.fingerprints[identity_id],
                    "fingerprint_tolerance": selection.tolerance,
                    "minimum_match_margin": selection.minimum_margin,
                    "nearest_inter_distance": selection.nearest_inter_distance,
                    "maximum_intra_distance": selection.maximum_intra_distance,
                    "pixel_ambiguous": selection.pixel_ambiguous,
                    "collision_policy": collision_policy,
                    "replacement": replacements.get(identity_id, _resource_name(identity)),
                    "replacement_filename": replacement_filename,
                }
            )
    return rules


def render_preview(rules: list[Mapping[str, Any]]) -> str:
    lines = [
        "; PROTOTYPE ONLY - current WWMI runtime does not implement this ABI.",
        "; This file is deliberately disabled and is not loaded by the mod.",
        "; Owner scope comes from the active Draw VB/IB gate; no scope fields are duplicated here.",
        "",
    ]
    for index, rule in enumerate(rules):
        lines.extend(
            [
                f"[TextureRoleOverride_{index:03d}]",
                f"match_resolution = {rule['match_resolution']}",
                f"match_fingerprint = {rule['match_fingerprint']}",
                f"fingerprint_tolerance = {rule['fingerprint_tolerance']:.8f}",
                f"minimum_match_margin = {rule['minimum_match_margin']:.8f}",
                f"collision_policy = {rule['collision_policy']}",
                f"replacement = {rule['replacement']}",
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
    if not rules:
        return None
    output_path = Path(output_folder) / PREVIEW_FILENAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_preview(rules), encoding="utf-8")
    return output_path
