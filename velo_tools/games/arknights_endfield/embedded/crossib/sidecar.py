"""CrossIB v2 evidence sidecar generation and validation."""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .classifier import CLASSIFIER_PROFILE
from .transparency_classifier import classify_frame_analysis_transparency


CROSSIB_JSON_NAME = "CrossIB.json"
FORMAT_VERSION = 2
GENERATOR_VERSION = 2
_DRAW_IB_RE = re.compile(r"^(?P<event>\d+)-ib=(?P<ib>[0-9a-f]{8})", re.IGNORECASE)


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

    def is_actual_transparent_component(self, component_id):
        return int(component_id) in self.actual_transparent_components


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
    if data.get("classifier_profile") != CLASSIFIER_PROFILE:
        raise CrossIBSchemaError(
            f"Unsupported CrossIB classifier_profile={data.get('classifier_profile')!r}{location}"
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
        return ("outdated" if "v1 is obsolete" in text else "invalid"), text
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

    components = []
    for component_id, _component in enumerate(metadata.get("components") or []):
        components.append({
            "id": component_id,
            "matched_draw_count": len(events_by_component.get(component_id, ())),
            "is_actual_transparent": component_id in actual,
            "is_translucent_without_texture": component_id in translucent,
            "transparent_draw_events": transparent_events.get(component_id, []),
            "translucent_draw_events": translucent_events.get(component_id, []),
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
