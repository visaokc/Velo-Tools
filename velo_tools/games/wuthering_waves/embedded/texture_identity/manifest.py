"""Machine-readable texture identity manifest construction."""

from __future__ import annotations

import json
import re
import hashlib
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping

from .fingerprint import (
    ALGORITHM_NAME,
    ALGORITHM_VERSION,
    DEFAULT_MINIMUM_MARGIN,
    DEFAULT_TOLERANCE,
    RESOLUTIONS,
    FingerprintError,
    fingerprint_dds,
    format_family,
)
from ..slot_textures.dds_meta import read_dds_meta


MANIFEST_FILENAME = "TextureIdentityManifest.json"
SOURCE_EVIDENCE_DIRECTORY = "TextureIdentitySources"
SCHEMA_ID = "urn:texture-identity-manifest:schema:v1"
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
                    if str(slot).startswith("ps-t") and isinstance(record, Mapping) and record.get("hash")
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
    ib_hashes = set()
    draws = []
    for component_id, entry in enumerate((capture or {}).get("components") or []):
        ib_hash = str(entry.get("ib_hash") or "").lower()
        if ib_hash:
            ib_hashes.add(ib_hash)
        draws.append(
            {
                "component": component_id,
                "call_id": entry.get("call_id"),
                "ib_hash": ib_hash,
            }
        )
    return {
        "vb0_hashes": [str(object_hash).lower()],
        "ib_hashes": sorted(ib_hashes),
        "draws": draws,
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
        except FingerprintError as exc:
            fingerprints = {}
            fingerprint_status = "source-retained-for-recompute"
            fingerprint_error = str(exc)
            evidence_directory = object_directory / SOURCE_EVIDENCE_DIRECTORY
            evidence_directory.mkdir(exist_ok=True)
            evidence_path = evidence_directory / f"{source_sha256}.dds"
            if not evidence_path.is_file() or hashlib.sha256(evidence_path.read_bytes()).hexdigest() != source_sha256:
                shutil.copyfile(dds_path, evidence_path)
            fingerprint_source_ref = evidence_path.relative_to(object_directory).as_posix()
        variant = {
            "variant": "dump-base",
            "stable_resource_ref": dds_path.name,
            "fingerprint_source_ref": fingerprint_source_ref,
            "source_sha256": source_sha256,
            "width": meta.width,
            "height": meta.height,
            "mips": meta.mips,
            "format": meta.format,
            "format_family": format_family(meta.format),
            "fingerprints": fingerprints,
        }
        identity = {
            "identity": f"texture:{texture_hash}",
            "legacy_resource_hash": texture_hash,
            "fingerprint_status": fingerprint_status,
            "variants": [variant],
            "witnesses": {
                "components": sorted({entry["component"] for entry in witnesses.get(texture_hash, [])}),
                "draw_bindings": witnesses.get(texture_hash, []),
                "anchors": anchors,
                "co_bound_resource_hashes": sorted(co_bound.get(texture_hash, set())),
            },
        }
        if fingerprint_error:
            identity["fingerprint_error"] = fingerprint_error
        identities.append(identity)

    return {
        "$schema": SCHEMA_ID,
        "schema": "texture-identity-manifest",
        "schema_version": 1,
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "candidate_resolutions": list(RESOLUTIONS),
            "payload": "v3:rN:rgba8-zlib:<base64url>",
            "distance": "normalized-rgba8-mean-absolute-error",
            "tolerance_floor": DEFAULT_TOLERANCE,
            "minimum_margin": DEFAULT_MINIMUM_MARGIN,
            "selection_rule": (
                "nearest_inter_distance - "
                "(maximum_intra_distance + tolerance) >= minimum_margin"
            ),
        },
        "source": {
            "metadata_ref": metadata_path.name if metadata_path.is_file() else None,
            "shader_texture_usage_ref": stu_path.name if stu_path.is_file() else None,
            "resource_origin": "original-game-dump",
        },
        "runtime_contract": {
            "owner_scope_source": "active-draw-vb-ib-gate",
            "scan_bound_srvs_only": True,
            "cache_key": ["resource", "resolution"],
            "analysis_phase": "present",
            "rule_scope_fields_emitted": False,
            "abi_status": "prototype-not-supported-by-current-runtime",
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
