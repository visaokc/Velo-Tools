"""Machine-readable texture identity manifest construction."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .fingerprint import (
    ALGORITHM_NAME,
    ALGORITHM_VERSION,
    CANONICAL_COLOR_DOMAIN,
    CANONICAL_VIEW_POLICY,
    DEFAULT_MINIMUM_MARGIN,
    DEFAULT_TOLERANCE_FLOOR,
    MIP_SELECTION_POLICY,
    PAYLOAD_ENCODING,
    RESOLUTIONS,
    FingerprintError,
    fingerprint_dds,
    format_family,
)
from ..slot_textures.dds_meta import read_dds_meta


MANIFEST_FILENAME = "TextureIdentityManifest.json"
SOURCE_EVIDENCE_DIRECTORY = "TextureIdentitySources"
SCHEMA_ID = "urn:texture-identity-manifest:schema:v3"
SCHEMA_VERSION = 3
PROTOTYPE_ABI_VERSION = 3
_DDS_HASH = re.compile(r"\bt=([0-9a-fA-F]{8})\b")


def _component_id(component_key: str) -> int | None:
    match = re.fullmatch(r"Component\s+(\d+)", str(component_key))
    return int(match.group(1)) if match else None


def _stu_witnesses(stu: Mapping[str, Any]) -> tuple[dict[str, list[dict]], dict[str, set[str]]]:
    witnesses: dict[str, list[dict]] = defaultdict(list)
    co_bound: dict[str, set[str]] = defaultdict(set)
    for component_key, component_block in stu.items():
        component = _component_id(component_key)
        if component is None or not isinstance(component_block, Mapping):
            continue
        for vs_key, vs_block in component_block.items():
            if not str(vs_key).startswith("vs=") or not isinstance(vs_block, Mapping):
                continue
            for ps_key, ps_block in vs_block.items():
                if not str(ps_key).startswith("ps=") or not isinstance(ps_block, Mapping):
                    continue
                bound_hashes = {
                    str(record.get("hash") or "").lower()
                    for slot, record in ps_block.items()
                    if str(slot).startswith("ps-t")
                    and isinstance(record, Mapping)
                    and record.get("hash")
                }
                for slot, record in ps_block.items():
                    if not str(slot).startswith("ps-t") or not isinstance(record, Mapping):
                        continue
                    texture_hash = str(record.get("hash") or "").lower()
                    if not texture_hash:
                        continue
                    witness = {
                        "component": component,
                        "shader_stage": "ps",
                        "observed_slot": str(slot),
                        "vertex_shader": str(vs_key),
                        "pixel_shader": str(ps_key),
                    }
                    for flag in ("fresh", "verified_inherited", "observed_only"):
                        if flag in record:
                            witness[flag] = bool(record[flag])
                    witnesses[texture_hash].append(witness)
                    co_bound[texture_hash].update(bound_hashes - {texture_hash})
    return witnesses, co_bound


def _capture_anchors(capture: Mapping[str, Any] | None, object_hash: str) -> dict[str, Any]:
    object_hash = str(object_hash).lower()
    ib_hashes = set()
    vb0_hashes = {object_hash}
    draws = []
    for component_id, entry in enumerate((capture or {}).get("components") or []):
        ib_hash = str(entry.get("ib_hash") or "").lower()
        vb0_hash = str(entry.get("vb0_hash") or object_hash).lower()
        if ib_hash:
            ib_hashes.add(ib_hash)
        if vb0_hash:
            vb0_hashes.add(vb0_hash)
        draws.append(
            {
                "component": component_id,
                "call_id": entry.get("call_id"),
                "vb0_hash": vb0_hash,
                "ib_hash": ib_hash,
            }
        )
    return {
        "vb0_hashes": sorted(vb0_hashes),
        "ib_hashes": sorted(ib_hashes),
        "draws": draws,
    }


def _candidate_context(
    identity_id: str,
    variant_id: str,
    anchors: Mapping[str, Any],
    draw_bindings: list[dict],
) -> dict[str, Any]:
    components = sorted({int(binding["component"]) for binding in draw_bindings})
    draws = [
        dict(draw)
        for draw in anchors.get("draws") or []
        if not components or int(draw.get("component", -1)) in components
    ]
    return {
        "context_id": f"{identity_id}|{variant_id}",
        "identity": identity_id,
        "variant": variant_id,
        "vb0_hashes": list(anchors.get("vb0_hashes") or []),
        "ib_hashes": sorted(
            {
                str(draw.get("ib_hash") or "")
                for draw in draws
                if draw.get("ib_hash")
            }
        ),
        "components": components,
        "draws": draws,
        "bindings": draw_bindings,
    }


def build_manifest(
    object_directory: str | Path,
    object_hash: str,
    *,
    capture: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    object_directory = Path(object_directory)
    stu_path = object_directory / "ShaderTextureUsage.json"
    metadata_path = object_directory / "Metadata.json"
    stu = json.loads(stu_path.read_text(encoding="utf-8")) if stu_path.is_file() else {}
    witnesses, co_bound = _stu_witnesses(stu if isinstance(stu, Mapping) else {})
    anchors = _capture_anchors(capture, object_hash)

    identities = []
    for dds_path in sorted(object_directory.glob("*.dds"), key=lambda path: path.name.lower()):
        match = _DDS_HASH.search(dds_path.name)
        if match is None:
            continue
        texture_hash = match.group(1).lower()
        identity_id = f"texture:{texture_hash}"
        variant_id = "dump-base"
        meta = read_dds_meta(dds_path)
        if meta is None:
            continue
        fingerprint_status = "available"
        fingerprint_error = None
        source_bytes = dds_path.read_bytes()
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        fingerprint_source_ref = dds_path.name
        try:
            fingerprints = fingerprint_dds(dds_path)
            ready_fingerprints = [
                record
                for record in fingerprints.values()
                if record.get("status") == "payload-ready"
            ]
            if ready_fingerprints:
                fingerprint_status = "payload-ready-runtime-blocked"
            else:
                fingerprint_status = "blocked-no-exact-square-mip"
        except FingerprintError as exc:
            fingerprints = {}
            fingerprint_status = "source-retained-blocked-decoder"
            fingerprint_error = str(exc)
            evidence_directory = object_directory / SOURCE_EVIDENCE_DIRECTORY
            evidence_directory.mkdir(exist_ok=True)
            evidence_path = evidence_directory / f"{source_sha256}.dds"
            if (
                not evidence_path.is_file()
                or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != source_sha256
            ):
                shutil.copyfile(dds_path, evidence_path)
            fingerprint_source_ref = evidence_path.relative_to(object_directory).as_posix()

        draw_bindings = witnesses.get(texture_hash, [])
        candidate_context = _candidate_context(
            identity_id,
            variant_id,
            anchors,
            draw_bindings,
        )
        variant = {
            "variant": variant_id,
            "context_ref": candidate_context["context_id"],
            "stable_resource_ref": dds_path.name,
            "fingerprint_source_ref": fingerprint_source_ref,
            "source_sha256": source_sha256,
            "width": meta.width,
            "height": meta.height,
            "mips": meta.mips,
            "format": meta.format,
            "format_family": format_family(meta.format),
            "source_is_srgb": meta.format.endswith("_SRGB"),
            "fingerprints": fingerprints,
        }
        identity = {
            "identity": identity_id,
            "legacy_resource_hash": texture_hash,
            "fingerprint_status": fingerprint_status,
            "variants": [variant],
            "candidate_contexts": [candidate_context],
            "witnesses": {
                "components": sorted({entry["component"] for entry in draw_bindings}),
                "draw_bindings": draw_bindings,
                "anchors": anchors,
                "co_bound_resource_hashes": sorted(co_bound.get(texture_hash, set())),
            },
        }
        if fingerprint_error:
            identity["fingerprint_error"] = fingerprint_error
        identities.append(identity)

    blocked_decoders = [
        identity["identity"]
        for identity in identities
        if identity["fingerprint_status"] == "source-retained-blocked-decoder"
    ]
    blocked_exact_mips = [
        identity["identity"]
        for identity in identities
        if identity["fingerprint_status"] == "blocked-no-exact-square-mip"
    ]
    payload_ready = [
        identity["identity"]
        for identity in identities
        if identity["fingerprint_status"] == "payload-ready-runtime-blocked"
    ]
    return {
        "$schema": SCHEMA_ID,
        "schema": "texture-identity-manifest",
        "schema_version": SCHEMA_VERSION,
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "canonical_color_domain": CANONICAL_COLOR_DOMAIN,
            "canonical_view_policy": CANONICAL_VIEW_POLICY,
            "srgb_behavior": "preserve-dds-encoded-rgba8-through-compatible-non-srgb-srv",
            "mip_selection_policy": MIP_SELECTION_POLICY,
            "non_square_policy": "unavailable-no-resample",
            "missing_target_mip_policy": "unavailable-no-resample",
            "small_texture_policy": "unavailable-no-resample",
            "incomplete_chain_policy": "use-only-physically-complete-exact-square-mips",
            "candidate_resolutions": list(RESOLUTIONS),
            "payload": PAYLOAD_ENCODING,
            "distance": "normalized-encoded-rgba8-mean-absolute-error",
            "tolerance_floor": DEFAULT_TOLERANCE_FLOOR,
            "minimum_margin": DEFAULT_MINIMUM_MARGIN,
            "effective_tolerance": "max(tolerance_floor, maximum_intra_distance)",
            "selection_rule": (
                "nearest_inter_distance - effective_tolerance >= minimum_margin"
            ),
            "reference_strategy": "per-identity-minimax-medoid-over-all-variants",
            "legacy_cache_policy": "reject-non-v3-or-non-encoded-rgba8",
        },
        "source": {
            "metadata_ref": metadata_path.name if metadata_path.is_file() else None,
            "shader_texture_usage_ref": stu_path.name if stu_path.is_file() else None,
            "resource_origin": "original-game-dump",
        },
        "runtime_contract": {
            "prototype_abi_version": PROTOTYPE_ABI_VERSION,
            "runtime_fingerprint_algorithm_version": ALGORITHM_VERSION,
            "abi_reference": "texture-role-v3",
            "abi_field_status": "aligned",
            "owner_scope_source": "active-draw-vb-ib-gate",
            "scan_bound_srvs_only": True,
            "cache_key": [
                "resource",
                "resource_version",
                "algorithm_version",
                "resolution",
                "canonical_format",
                "absolute_mip",
            ],
            "analysis_phase": "present",
            "rule_scope_fields_emitted": False,
            "match_resolutions": list(RESOLUTIONS),
            "match_format_families": [
                "r8g8b8a8",
                "b8g8r8a8",
                "b8g8r8x8",
                "bc1",
                "bc2",
                "bc3",
                "bc4",
                "bc5",
                "bc6h",
                "bc7",
                "dxgi-<numeric-enum>",
            ],
            "collision_group_default": "default-r{resolution}-{descriptor_family}",
            "collision_policies": ["reject", "merge", "require_draw_context"],
            "candidate_context_abi_status": "evidence-only-unmapped",
            "abi_status": "blocked",
            "abi_blockers": [
                "srv-visible-absolute-mip-witness-unavailable",
                "runtime-v3-game-parity-not-validated",
                "candidate-context-draw_context-mapping-not-implemented",
                *(
                    ["compressed-dds-fingerprint-decoder-unavailable"]
                    if blocked_decoders
                    else []
                ),
                *(
                    ["exact-square-mip-unavailable"]
                    if blocked_exact_mips
                    else []
                ),
            ],
            "identity_counts": {
                "total": len(identities),
                "payload_ready_runtime_blocked": len(payload_ready),
                "blocked_decoder": len(blocked_decoders),
                "blocked_no_exact_square_mip": len(blocked_exact_mips),
                "runtime_compatible": 0,
            },
            "blocked_identity_count": len(identities),
        },
        "identities": identities,
    }


def write_manifest(
    object_directory: str | Path,
    object_hash: str,
    *,
    capture: Mapping[str, Any] | None = None,
) -> Path:
    object_directory = Path(object_directory)
    manifest = build_manifest(object_directory, object_hash, capture=capture)
    path = object_directory / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
