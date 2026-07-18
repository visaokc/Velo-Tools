"""CrossIB v2 evidence sidecar generation and validation."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .classifier import CLASSIFIER_PROFILE, LEGACY_CLASSIFIER_PROFILES
from .transparency_classifier import (
    _parse_log_draw_states,
    classify_frame_analysis_transparency,
)


CROSSIB_JSON_NAME = "CrossIB.json"
FORMAT_VERSION = 2
GENERATOR_VERSION = 5
_DRAW_IB_RE = re.compile(r"^(?P<event>\d+)-ib=(?P<ib>[0-9a-f]{8})", re.IGNORECASE)
_DRAW_PASS_RE = re.compile(
    r"^(?P<event>\d+)-ib=(?P<ib>[0-9a-f]{8})(?:\([^)]*\))?"
    r"-vs=(?P<vs>[0-9a-f]+)-ps=(?P<ps>[0-9a-f]+)\.txt$",
    re.IGNORECASE,
)
_FRAME_TEX_SLOT_RE = re.compile(r"-ps-t(\d+)=", re.IGNORECASE)
_PASS_KIND_PRIORITY = {"outline": 0, "material": 1, "prepass": 2, "pose": 3, "effect": 4}
_DRAW_VB0_RE = re.compile(
    r"^(?P<event>\d+)-vb0=(?P<vb>[0-9a-f]{8})\([^)]*\)-vs=[0-9a-f]+-ps=[0-9a-f]+\.txt$",
    re.IGNORECASE,
)
_FMT_ELEMENT_RE = re.compile(
    r"element\[\d+\]:\s*"
    r".*?SemanticName:\s*(?P<name>\S+)\s*"
    r".*?SemanticIndex:\s*(?P<index>\d+)\s*"
    r".*?Format:\s*(?P<format>\S+)",
    re.IGNORECASE | re.DOTALL,
)


class CrossIBSchemaError(RuntimeError):
    pass


class JsonBackedTransparency:
    def __init__(self, data):
        self.default_blend_state = data.get("default_blend_state")
        self.actual_transparent_components = {
            int(component["id"])
            for component in data.get("components") or []
            if component.get("is_actual_transparent")
        }
        self.translucent_without_usable_texture_components = {
            int(component["id"])
            for component in data.get("components") or []
            if component.get("is_translucent_without_texture")
        }
        self.report_lines = list((data.get("diagnostics") or {}).get("report_lines") or [])
        self.skinning_profiles = {
            int(component["id"]): component.get("skinning_profile")
            for component in data.get("components") or []
            if component.get("skinning_profile") is not None
        }
        self.material_input_profiles = {
            int(component["id"]): list(component.get("material_input_profiles") or [])
            for component in data.get("components") or []
        }
        self.pass_topology = {
            int(component["id"]): dict(component.get("pass_topology") or {})
            for component in data.get("components") or []
        }

    def is_actual_transparent_component(self, component_id):
        return int(component_id) in self.actual_transparent_components

    def is_translucent_component(self, component_id):
        component_id = int(component_id)
        return (
            component_id in self.actual_transparent_components
            or component_id in self.translucent_without_usable_texture_components
        )

    def skinning_profiles_compatible(self, provider_id, target_id):
        provider = self.skinning_profiles.get(int(provider_id))
        target = self.skinning_profiles.get(int(target_id))
        return provider is not None and provider == target

    def material_profiles_compatible(self, provider_id, target_id):
        provider_profiles = self.material_input_profiles.get(int(provider_id), [])
        target_profiles = self.material_input_profiles.get(int(target_id), [])
        if not provider_profiles or not target_profiles:
            return False
        provider_sets = [set(profile) for profile in provider_profiles]
        return all(
            any(set(target).issubset(provider) for provider in provider_sets)
            for target in target_profiles
        )


def _component_skinning_profile(source: Path, component_id: int, component: dict):
    if component.get("cpu_posed"):
        return {"mode": "cpu_posed"}

    fmt_path = source / f"Component {component_id}.fmt"
    if not fmt_path.is_file():
        raise FileNotFoundError(f"CrossIB component format not found: {fmt_path}")
    text = fmt_path.read_text(encoding="utf-8", errors="replace")
    elements = {
        (match.group("name").upper(), int(match.group("index"))): match.group("format").upper()
        for match in _FMT_ELEMENT_RE.finditer(text)
    }
    weights = elements.get(("BLENDWEIGHTS", 0))
    indices = elements.get(("BLENDINDICES", 0))
    if weights and indices:
        mode = "explicit"
    elif indices:
        mode = "implicit_weights"
    else:
        mode = "unskinned"
    return {
        "mode": mode,
        "blend_weights_format": weights,
        "blend_indices_format": indices,
    }


def _metadata_component_count(folder: Path) -> int:
    try:
        payload = json.loads((folder / "Metadata.json").read_text(encoding="utf-8"))
    except Exception:
        return 0
    components = payload.get("components") or []
    return len(components) if isinstance(components, list) else 0


def _resolve_source(source_folder) -> Path:
    folder = Path(source_folder)
    if (folder / "Metadata.json").is_file():
        return folder
    candidates = [
        child for child in folder.iterdir()
        if child.is_dir() and (child / "Metadata.json").is_file()
    ] if folder.is_dir() else []
    if not candidates:
        return folder

    def score(candidate: Path):
        name = candidate.name.lower()
        kind = 2 if name.startswith("character") else (0 if name.startswith("weapon") else 1)
        return kind, _metadata_component_count(candidate), candidate.name

    return max(candidates, key=score)


def _read_metadata(source: Path):
    path = source / "Metadata.json"
    if not path.is_file():
        raise FileNotFoundError(f"CrossIB source Metadata.json not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    components = payload.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError(f"CrossIB source Metadata.json has no components: {path}")
    return payload


def _metadata_ib_map(metadata):
    result = {}
    for component_id, component in enumerate(metadata.get("components") or []):
        if not isinstance(component, dict):
            continue
        hashes = [component.get("ib_hash")]
        hashes.extend(lod.get("ib_hash") for lod in component.get("lods") or [] if isinstance(lod, dict))
        for value in hashes:
            ib_hash = str(value or "").lower()
            if re.fullmatch(r"[0-9a-f]{8}", ib_hash):
                result[ib_hash] = component_id
    return result


def _scan_matched_draws(dump_path: Path, ib_to_component):
    events_by_component = {}
    seen = set()
    for path in dump_path.rglob("*.txt"):
        match = _DRAW_IB_RE.match(path.name)
        if not match:
            continue
        event = int(match.group("event"))
        component_id = ib_to_component.get(match.group("ib").lower())
        if component_id is None or (component_id, event) in seen:
            continue
        seen.add((component_id, event))
        events_by_component.setdefault(component_id, []).append(event)
    for events in events_by_component.values():
        events.sort()
    return events_by_component


def _scan_component_pass_topology(dump_path: Path, ib_to_component):
    states = {}
    log_path = dump_path / "log.txt"
    if log_path.is_file():
        try:
            states = _parse_log_draw_states(log_path)
        except Exception as exc:
            print(f"[CrossIB] pass-topology log parse failed: {exc}")

    records = []
    for path in dump_path.glob("*-ib=*.txt"):
        match = _DRAW_PASS_RE.match(path.name)
        if not match:
            continue
        component_id = ib_to_component.get(match.group("ib").lower())
        if component_id is None:
            continue
        event = int(match.group("event"))
        vs_hash = match.group("vs").lower()
        ps_hash = match.group("ps").lower()
        output_count = len(list(dump_path.glob(f"{match.group('event')}-o*=*vs={vs_hash}-ps={ps_hash}*")))
        texture_slots = {
            int(slot.group(1))
            for candidate in dump_path.glob(
                f"{match.group('event')}-ps-t*=*vs={vs_hash}-ps={ps_hash}*"
            )
            if (slot := _FRAME_TEX_SLOT_RE.search(candidate.name))
        }
        state = states.get(event)
        records.append({
            "component_id": component_id,
            "vs_hash": vs_hash,
            "output_count": output_count,
            "texture_count": len(texture_slots),
            "raster": getattr(state, "raster_state", None),
        })

    raster_groups = {}
    for record in records:
        if record["raster"] and record["output_count"] >= 1:
            raster_groups.setdefault(record["raster"], []).append(record)
    outline_raster = None
    if len(raster_groups) >= 2:
        outline_raster = min(
            raster_groups,
            key=lambda raster: (
                max(item["texture_count"] for item in raster_groups[raster]),
                raster,
            ),
        )

    def raw_kind(record):
        if outline_raster is not None and record["raster"] == outline_raster:
            return "outline"
        outputs = record["output_count"]
        if outputs == 0:
            return "pose"
        if outputs == 2:
            return "effect" if record["texture_count"] <= 2 else "material"
        if outputs >= 3:
            return "prepass"
        return "effect"

    kinds_by_shader = {}
    for record in records:
        kinds_by_shader.setdefault(record["vs_hash"], set()).add(raw_kind(record))
    resolved_kind = {
        shader: min(kinds, key=lambda kind: _PASS_KIND_PRIORITY[kind])
        for shader, kinds in kinds_by_shader.items()
    }

    result = {}
    for component_id in set(ib_to_component.values()):
        kinds = {
            resolved_kind[record["vs_hash"]]
            for record in records
            if record["component_id"] == component_id
        }
        result[component_id] = {
            "pose_capture_cb1": "pose" in kinds,
            "prepass_cb2": "prepass" in kinds,
            "material_cb2": "material" in kinds,
            "outline_cb2": "outline" in kinds,
            "effect_observed": "effect" in kinds,
        }
    return result


def _metadata_vb_map(metadata):
    result = {}
    for component_id, component in enumerate(metadata.get("components") or []):
        if not isinstance(component, dict):
            continue
        vb_hash = str(component.get("vb0_hash") or "").lower()
        if re.fullmatch(r"[0-9a-f]{8}", vb_hash):
            result[vb_hash] = component_id
    return result


def _parse_draw_input_profile(path: Path):
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(
        r"element\[\d+\]:\s*"
        r".*?SemanticName:\s*(\S+)\s*"
        r".*?SemanticIndex:\s*(\d+)\s*"
        r".*?Format:\s*(\S+)\s*"
        r".*?InputSlot:\s*(\d+)\s*"
        r".*?AlignedByteOffset:\s*(\d+)",
        re.IGNORECASE | re.DOTALL,
    )
    return [
        f"{match.group(1).upper()}{int(match.group(2))}:"
        f"{match.group(3).upper()}:S{int(match.group(4))}@{int(match.group(5))}"
        for match in pattern.finditer(text)
    ]


def _scan_material_input_profiles(dump_path: Path, metadata, transparent_events):
    vb_to_component = _metadata_vb_map(metadata)
    primary = {}
    transparent_fallback = {}
    for path in dump_path.glob("*-vb0=*.txt"):
        match = _DRAW_VB0_RE.match(path.name)
        if not match:
            continue
        component_id = vb_to_component.get(match.group("vb").lower())
        if component_id is None:
            continue
        event = int(match.group("event"))
        profile = _parse_draw_input_profile(path)
        if not profile:
            continue
        semantics = {item.split(":", 1)[0] for item in profile}
        if {"TANGENT0", "TEXCOORD5", "TEXCOORD6"}.issubset(semantics):
            primary.setdefault(component_id, []).append(profile)
        elif event in set(transparent_events.get(component_id, ())):
            transparent_fallback.setdefault(component_id, []).append(profile)

    result = {}
    for component_id in range(len(metadata.get("components") or [])):
        profiles = primary.get(component_id) or transparent_fallback.get(component_id) or []
        unique = []
        seen = set()
        for profile in profiles:
            key = tuple(profile)
            if key not in seen:
                seen.add(key)
                unique.append(profile)
        result[component_id] = unique
    return result


def _validate_v2(data, path: Path | None = None):
    version = data.get("format_version") if isinstance(data, dict) else None
    location = f" ({path})" if path else ""
    if version != FORMAT_VERSION:
        if version == 1:
            raise CrossIBSchemaError(
                "CrossIB.json v1 is obsolete. Select one current FrameAnalysis dump "
                "in the CrossIB panel to regenerate CrossIB.json v2."
            )
        raise CrossIBSchemaError(f"Unsupported CrossIB.json format_version={version!r}{location}")
    if data.get("classifier_profile") not in {CLASSIFIER_PROFILE, *LEGACY_CLASSIFIER_PROFILES}:
        raise CrossIBSchemaError(
            f"Unsupported CrossIB classifier_profile={data.get('classifier_profile')!r}{location}"
        )
    if int(data.get("generator_version") or 0) < GENERATOR_VERSION:
        raise CrossIBSchemaError(
            "CrossIB.json v2 lacks the current pass-topology or input-compatibility evidence. "
            "Select one current FrameAnalysis dump in the CrossIB panel to regenerate it."
        )
    if not isinstance(data.get("components"), list):
        raise CrossIBSchemaError(f"CrossIB.json v2 components must be a list{location}")
    forbidden = {
        "shader_overrides",
        "record_filters",
        "provider_self_filters",
        "provider_material_filters",
        "consumer_borrow_filters",
    }
    found = forbidden.intersection(data)
    for component in data.get("components") or []:
        if isinstance(component, dict):
            found.update(forbidden.intersection(component))
            if not isinstance(component.get("skinning_profile"), dict):
                raise CrossIBSchemaError(
                    f"CrossIB.json v2 component C{component.get('id')} has no skinning_profile{location}"
                )
            if not isinstance(component.get("material_input_profiles"), list):
                raise CrossIBSchemaError(
                    f"CrossIB.json v2 component C{component.get('id')} has no material_input_profiles{location}"
                )
            topology = component.get("pass_topology")
            if not isinstance(topology, dict) or not all(
                isinstance(topology.get(key), bool)
                for key in (
                    "pose_capture_cb1",
                    "prepass_cb2",
                    "material_cb2",
                    "outline_cb2",
                    "effect_observed",
                )
            ):
                raise CrossIBSchemaError(
                    f"CrossIB.json v2 component C{component.get('id')} has no pass_topology{location}"
                )
    if found:
        raise CrossIBSchemaError(f"CrossIB.json v2 contains obsolete fields: {sorted(found)}{location}")
    return data


def load_crossib_json(source_folder):
    if not source_folder:
        return None
    source = _resolve_source(source_folder)
    path = source / CROSSIB_JSON_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CrossIBSchemaError(f"Failed to read {path}: {exc}") from exc
    return _validate_v2(data, path)


def sidecar_status(source_folder):
    if not source_folder:
        return "missing", "CrossIB.json not found"
    path = _resolve_source(source_folder) / CROSSIB_JSON_NAME
    if not path.is_file():
        return "missing", "CrossIB.json not found"
    try:
        load_crossib_json(source_folder)
    except CrossIBSchemaError as exc:
        text = str(exc)
        return (
            "outdated" if "Select one current FrameAnalysis dump" in text else "invalid"
        ), text
    return "ready", f"{CLASSIFIER_PROFILE} ready"


def sidecars_present(source_folder):
    return sidecar_status(source_folder)[0] == "ready"


def has_sidecars(source_folder):
    return sidecars_present(source_folder)


def build_crossib_data(source_folder, dump_path):
    source = _resolve_source(source_folder)
    dump = Path(dump_path)
    if not dump.is_dir():
        raise FileNotFoundError(f"FrameAnalysis folder not found: {dump}")
    metadata = _read_metadata(source)
    ib_to_component = _metadata_ib_map(metadata)
    events_by_component = _scan_matched_draws(dump, ib_to_component)
    if not events_by_component:
        raise ValueError(
            "The selected dump contains no draw matching this source object's Component IBs"
        )

    diagnostics = []
    try:
        transparency = classify_frame_analysis_transparency(source, frame_root=dump)
        actual = set(transparency.actual_transparent_components)
        translucent = set(transparency.translucent_without_usable_texture_components)
        default_blend = transparency.default_blend_state
        diagnostics.extend(transparency.report_lines)
        transparent_events = {
            int(component_id): sorted({int(item.draw_event) for item in evidence})
            for component_id, evidence in transparency.evidence_by_component.items()
        }
        translucent_events = {
            int(component_id): sorted({int(event) for event in events})
            for component_id, events in transparency.translucent_draws_by_component.items()
        }
    except Exception as exc:
        actual = set()
        translucent = set()
        default_blend = None
        transparent_events = {}
        translucent_events = {}
        diagnostics.append(f"Transparency classification failed: {exc}")

    material_profiles = _scan_material_input_profiles(
        dump, metadata, transparent_events
    )
    pass_topology = _scan_component_pass_topology(dump, ib_to_component)

    components = []
    for component_id, component in enumerate(metadata.get("components") or []):
        components.append({
            "id": component_id,
            "matched_draw_count": len(events_by_component.get(component_id, ())),
            "is_actual_transparent": component_id in actual,
            "is_translucent_without_texture": component_id in translucent,
            "transparent_draw_events": transparent_events.get(component_id, []),
            "translucent_draw_events": translucent_events.get(component_id, []),
            "skinning_profile": _component_skinning_profile(source, component_id, component),
            "material_input_profiles": material_profiles.get(component_id, []),
            "pass_topology": pass_topology.get(component_id, {
                "pose_capture_cb1": False,
                "prepass_cb2": False,
                "material_cb2": False,
                "outline_cb2": False,
                "effect_observed": False,
            }),
        })

    counts = Counter({component_id: len(events) for component_id, events in events_by_component.items()})
    return {
        "format_version": FORMAT_VERSION,
        "classifier_profile": CLASSIFIER_PROFILE,
        "generator_version": GENERATOR_VERSION,
        "default_blend_state": default_blend,
        "source_match": {
            "dump_name": dump.name,
            "metadata_component_count": len(components),
            "matched_component_ids": sorted(counts),
            "matched_draw_count": sum(counts.values()),
        },
        "components": components,
        "diagnostics": {
            "report_lines": diagnostics,
        },
    }


def write_sidecars(source_folder, dump_path):
    source = _resolve_source(source_folder)
    data = build_crossib_data(source, dump_path)
    path = source / CROSSIB_JSON_NAME
    path.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"[CrossIB] wrote {path.name} v{FORMAT_VERSION}: "
        f"{data['source_match']['matched_draw_count']} matched draws, "
        f"{len(data['components'])} components"
    )
    return path


def regenerate_crossib_json(source_folder, dump_path):
    source = _resolve_source(source_folder)
    try:
        path = write_sidecars(source, dump_path)
        data = load_crossib_json(source)
    except Exception as exc:
        return False, str(exc)
    match = data["source_match"]
    return True, (
        f"已重新生成 {path.name} v2："
        f"{len(match['matched_component_ids'])} 个 Component，"
        f"{match['matched_draw_count']} 个匹配 draw"
    )
