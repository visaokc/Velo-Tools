"""Bake CrossIB pass classification + transparency into sidecars at extract time.

Two products are written next to each extracted object (gated by the
"generate CrossIB.json" option), so a later export can consume them without
re-scanning the frame dump:

  - ``CrossIB.json``      per-component prep/borrow filter vs + transparency
                          flags, plus the global hash->filter shader-override map.
  - ``ShaderOverride.ini`` the global ``[ShaderOverrideCrossIB_*]`` blocks
                          (canonical filters), copied verbatim into the mod at
                          export time as the standalone shader ini.

The same builder is reused at export time as the old-folder fallback when these
two files are missing (see patch.py / generator.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import generator
from .pass_registry import CrossIBPassRegistry
from .transparency_classifier import classify_frame_analysis_transparency


CROSSIB_JSON_NAME = "CrossIB.json"
SHADER_OVERRIDE_INI_NAME = "ShaderOverride.ini"
FORMAT_VERSION = 1


# Default canonical fallbacks, mirroring the live registry's fallbacks for
# components not present in CrossIB.json.
_FALLBACK = {
    "record_filters": [200],
    "provider_self_filters": [200, 201],
    "provider_material_filters": [],
    "consumer_borrow_filters": [202, 203],
}


class JsonBackedPassRegistry:
    """A drop-in for CrossIBPassRegistry that answers from a baked CrossIB.json,
    so export consumes the pre-classified filters without re-scanning the dump.

    Implements exactly the methods generator.build_cross_ib uses."""

    def __init__(self, data):
        self._shader_overrides = [(str(h).lower(), int(f)) for h, f in data.get("shader_overrides", [])]
        self._by_component = {}
        for comp in data.get("components", []):
            try:
                self._by_component[int(comp["id"])] = comp
            except (KeyError, TypeError, ValueError):
                continue

    def _filters(self, component_id, key):
        comp = self._by_component.get(int(component_id))
        if comp is None or comp.get(key) is None:
            return list(_FALLBACK[key])
        return [int(v) for v in comp.get(key) or []]

    def record_filters(self, component_id):
        return self._filters(component_id, "record_filters")

    def provider_self_filters(self, component_id):
        return self._filters(component_id, "provider_self_filters")

    def provider_material_filters(self, component_id):
        return self._filters(component_id, "provider_material_filters")

    def consumer_borrow_filters(self, component_id):
        return self._filters(component_id, "consumer_borrow_filters")

    def condition(self, filters):
        values = sorted({int(v) for v in filters})
        if not values:
            values = [202]
        return "if " + " || ".join(f"vs == {value}" for value in values)

    def shader_overrides(self):
        return list(self._shader_overrides)


class JsonBackedTransparency:
    """A drop-in for the transparency classification result, from CrossIB.json."""

    def __init__(self, data):
        self.actual_transparent_components = {
            int(c["id"]) for c in data.get("components", []) if c.get("is_actual_transparent")
        }
        self.translucent_without_usable_texture_components = {
            int(c["id"]) for c in data.get("components", []) if c.get("is_translucent_without_texture")
        }
        self.default_blend_state = data.get("default_blend_state")
        self.report_lines = []

    def is_actual_transparent_component(self, component_id):
        return int(component_id) in self.actual_transparent_components


def _resolve_source(source_folder):
    folder = Path(source_folder)
    try:
        return CrossIBPassRegistry()._resolve_source_folder(folder)
    except Exception:
        return folder


def load_crossib_json(source_folder):
    """Return the parsed CrossIB.json dict iff BOTH sidecars exist, else None."""
    if not source_folder:
        return None
    folder = _resolve_source(source_folder)
    if not has_sidecars(folder):
        return None
    try:
        return json.loads((folder / CROSSIB_JSON_NAME).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[CrossIB] failed to read {folder / CROSSIB_JSON_NAME}: {exc}")
        return None


def ensure_crossib_sidecars(source_folder, frame_dump_folder):
    """Make sure both sidecars exist in the resolved source folder. If missing
    and a frame dump is available, generate them. Returns True if present or
    successfully generated, False if still missing (caller should prompt)."""
    folder = _resolve_source(source_folder)
    if has_sidecars(folder):
        return True
    if not frame_dump_folder:
        return False
    try:
        write_sidecars(folder, frame_dump_folder)
        return has_sidecars(folder)
    except Exception as exc:
        print(f"[CrossIB] failed to generate sidecars from {frame_dump_folder}: {exc}")
        return False


def _component_ids(registry):
    ids = set(registry._records_by_component) | set(registry._texture_records_by_component)
    return sorted(ids)


def _classify_transparency(source_folder, dump_path):
    # Resolve the character folder (Metadata/TextureUsage/DDS/geometry, from the
    # object source) and the frame_root (log.txt + *-ib=*.txt draw files, from the
    # dump) independently, so a non-co-located extract still classifies
    # transparency. Fall back to legacy single-path resolution for co-located /
    # dump-only inputs; failure degrades to no transparency.
    if source_folder and dump_path:
        try:
            return classify_frame_analysis_transparency(str(source_folder), frame_root=str(dump_path))
        except Exception as exc:
            print(f"[CrossIB] transparency classify (source+dump) failed: {exc}")
    for candidate in (source_folder, dump_path):
        if not candidate:
            continue
        try:
            return classify_frame_analysis_transparency(str(candidate))
        except Exception as exc:
            print(f"[CrossIB] transparency classify via {candidate} failed: {exc}")
    return None


def _data_and_ini(registry, source_folder, dump_path):
    """Build (data_dict, ShaderOverride.ini text) from an already-built registry."""
    transparency = _classify_transparency(source_folder, dump_path)
    actual = set(getattr(transparency, "actual_transparent_components", set()) or set())
    translucent = set(
        getattr(transparency, "translucent_without_usable_texture_components", set()) or set()
    )
    default_blend = getattr(transparency, "default_blend_state", None)

    components = []
    for cid in _component_ids(registry):
        components.append({
            "id": cid,
            # "preparation" vs the component records; "borrow" vs a consumer uses.
            "record_filters": registry.record_filters(cid),
            "provider_self_filters": registry.provider_self_filters(cid),
            "provider_material_filters": registry.provider_material_filters(cid),
            "consumer_borrow_filters": registry.consumer_borrow_filters(cid),
            "is_actual_transparent": cid in actual,
            "is_translucent_without_texture": cid in translucent,
        })

    data = {
        "format_version": FORMAT_VERSION,
        "default_blend_state": default_blend,
        # global game-shader hash -> canonical filter_index (the conflict-critical
        # map; emitted as the standalone ShaderOverride.ini too).
        "shader_overrides": [
            [hash_val, fi]
            for hash_val, fi in sorted(registry.shader_overrides(), key=lambda hf: (hf[1], hf[0]))
        ],
        "components": components,
    }
    shader_ini = generator.render_shader_override_ini(registry)
    return data, shader_ini


def build_crossib_data(source_folder, dump_path):
    """Return (data_dict, shader_override_ini_text) for one extracted object."""
    registry = generator._build_registry(
        source_folder=str(source_folder), frame_dump_folder=str(dump_path), consume_sidecar=False
    )
    return _data_and_ini(registry, source_folder, dump_path)


# Merge priority: when the SAME public game-shader hash is classified to a
# different filter across scenario dumps, the lower rank wins. Mirrors
# pass_registry's _KIND_PRIORITY (outline > material > prepass > record > other);
# the pinned eye(205)/eyelash(206) overrides are ranked to win so a fixed
# override is never displaced (those are specific hashes that never really
# conflict with a structural classification anyway).
_FILTER_PRIORITY = {205: -2, 206: -1, 203: 0, 202: 1, 201: 2, 200: 3, 204: 4}


def _filter_rank(filter_index):
    return _FILTER_PRIORITY.get(int(filter_index), 5)


def merge_crossib_data(existing, new):
    """Union two CrossIB.json dicts into one (accumulate across scenario dumps).

    - shader_overrides: union by hash; same-hash conflict resolved by
      _FILTER_PRIORITY (so e.g. the afterimage's 204 is added without disturbing
      the body material's 202).
    - per-component record/self/material/borrow filter lists: union (a scenario
      dump that adds 204 to a component's self-draw grows self to [200,201,204]).
    - transparency flags: OR.
    """
    merged_overrides = {}
    for hash_val, fi in list(existing.get("shader_overrides") or []) + list(new.get("shader_overrides") or []):
        key = str(hash_val).lower()
        fi = int(fi)
        if key not in merged_overrides or _filter_rank(fi) < _filter_rank(merged_overrides[key]):
            merged_overrides[key] = fi
    shader_overrides = sorted(([k, v] for k, v in merged_overrides.items()), key=lambda hf: (hf[1], hf[0]))

    list_keys = ("record_filters", "provider_self_filters", "provider_material_filters", "consumer_borrow_filters")
    comps = {}
    for comp in list(existing.get("components") or []) + list(new.get("components") or []):
        try:
            cid = int(comp["id"])
        except (KeyError, TypeError, ValueError):
            continue
        cur = comps.setdefault(cid, {k: set() for k in list_keys})
        for k in list_keys:
            cur[k] |= {int(v) for v in (comp.get(k) or [])}
        cur["is_actual_transparent"] = cur.get("is_actual_transparent", False) or bool(comp.get("is_actual_transparent"))
        cur["is_translucent_without_texture"] = cur.get("is_translucent_without_texture", False) or bool(comp.get("is_translucent_without_texture"))

    components = []
    for cid in sorted(comps):
        cur = comps[cid]
        entry = {"id": cid}
        for k in list_keys:
            entry[k] = sorted(cur[k])
        entry["is_actual_transparent"] = cur.get("is_actual_transparent", False)
        entry["is_translucent_without_texture"] = cur.get("is_translucent_without_texture", False)
        components.append(entry)

    return {
        "format_version": existing.get("format_version", FORMAT_VERSION),
        "default_blend_state": existing.get("default_blend_state") or new.get("default_blend_state"),
        "shader_overrides": shader_overrides,
        "components": components,
    }


def accumulate_crossib_sidecars(source_folder, dump_path, overwrite=False):
    """Classify a new scenario dump and ACCUMULATE its shaders into the object
    source's existing CrossIB.json / ShaderOverride.ini (union, not overwrite).

    Returns (ok, message). Safety: if the new dump has no frame-analysis draw
    matching THIS character's component IBs (wrong dump/scene picked), the
    existing sidecars are left untouched and ok=False.
    """
    source = _resolve_source(source_folder)
    registry = generator._build_registry(
        source_folder=str(source), frame_dump_folder=str(dump_path), consume_sidecar=False
    )
    if not registry._records_by_component:
        return False, "新 dump 未含本角色的 component draw（IB 不匹配，可能选错 dump/场景）；已保持原 CrossIB.json 不动"

    new_data, _ini = _data_and_ini(registry, source, dump_path)

    merged = False
    if not overwrite:
        existing = load_crossib_json(source)
        if existing is not None:
            new_data = merge_crossib_data(existing, new_data)
            merged = True

    json_path = source / CROSSIB_JSON_NAME
    json_path.write_text(json.dumps(new_data, indent=4, ensure_ascii=False), encoding="utf-8")
    ini_text = generator.render_shader_override_ini(JsonBackedPassRegistry(new_data))
    ini_path = source / SHADER_OVERRIDE_INI_NAME
    ini_path.write_text((ini_text + "\n") if ini_text else "", encoding="utf-8")

    verb = "合并累积" if merged else ("覆盖重建" if overwrite else "新建")
    msg = f"已{verb}：{len(new_data['shader_overrides'])} overrides, {len(new_data['components'])} components"
    print(f"[CrossIB] {msg}")
    return True, msg


def write_sidecars(source_folder, dump_path):
    """Write CrossIB.json + ShaderOverride.ini into source_folder. Returns paths."""
    source_folder = Path(source_folder)
    data, shader_ini = build_crossib_data(source_folder, dump_path)

    json_path = source_folder / CROSSIB_JSON_NAME
    json_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")

    ini_path = source_folder / SHADER_OVERRIDE_INI_NAME
    ini_text = (shader_ini + "\n") if shader_ini else ""
    ini_path.write_text(ini_text, encoding="utf-8")

    print(
        f"[CrossIB] wrote {json_path.name} + {ini_path.name} "
        f"({len(data['components'])} components, {len(data['shader_overrides'])} overrides)"
    )
    return json_path, ini_path


def has_sidecars(source_folder):
    source_folder = Path(source_folder)
    return (source_folder / CROSSIB_JSON_NAME).is_file() and (
        source_folder / SHADER_OVERRIDE_INI_NAME
    ).is_file()


def sidecars_present(source_folder):
    """has_sidecars after resolving a parent/Character source folder (UI poll)."""
    if not source_folder:
        return False
    return has_sidecars(_resolve_source(source_folder))
