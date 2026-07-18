"""Full/LOD form identities and same-IB runtime routing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


VB_HASHES_KEY = "vb_hashes"
_HASH_RE = re.compile(r"^[0-9a-f]{8}$", re.IGNORECASE)
_DUMP_IB_RE = re.compile(
    r"^(?P<event>[0-9.]+)-ib=(?P<hash>[0-9a-f]{8})(?:\([^)]*\))?-vs=.*\.txt$",
    re.IGNORECASE,
)
_DUMP_VB_RE = re.compile(
    r"^(?P<event>[0-9.]+)-vb(?P<slot>\d+)=(?P<hash>[0-9a-f]{8})"
    r"(?:\([^)]*\))?-vs=.*\.txt$",
    re.IGNORECASE,
)
_SECTION_RE = re.compile(r"^\s*\[([^]]+)\]\s*$")
_COMPONENT_OVERRIDE_RE = re.compile(r"^TextureOverride_Component(?P<id>\d+)$")
_LOD_OVERRIDE_RE = re.compile(r"^TextureOverride_Component(?P<id>\d+)_LOD(?P<lod>\d+)$")
_LOD_ASSIGN_RE = re.compile(r"^\s*\$lod_level\s*=\s*\d+\s*$")
_ROUTE_MARKER = "; >>> same-IB form routing (auto-generated; do not edit) >>>"
_OVERRIDE_MATCH_KEYS = {
    "allow_duplicate_hash",
    "hash",
    "match_byte_width",
    "match_first_index",
    "match_first_vertex",
    "match_format",
    "match_index_count",
    "match_instance_count",
    "match_priority",
    "match_stride",
    "match_type",
    "match_vertex_count",
}


class FormIdentityError(ValueError):
    pass


@dataclass(frozen=True)
class _Form:
    level: int
    ib_hash: str
    index_count: int
    vb_hashes: dict[str, tuple[str, ...]]
    preferred_slots: tuple[str, ...]
    vg_map: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Route:
    component_id: int
    ib_hash: str
    index_count: int
    slot: str
    forms: tuple[_Form, ...]


def _valid_hash(value: Any) -> str | None:
    value = str(value or "").lower()
    return value if _HASH_RE.fullmatch(value) else None


def _normalize_vb_hashes(value: Any) -> dict[str, tuple[str, ...]]:
    result = {}
    if not isinstance(value, dict):
        return result
    for raw_slot, raw_hash in value.items():
        match = re.fullmatch(r"VB(\d+)", str(raw_slot or "").upper())
        raw_values = raw_hash if isinstance(raw_hash, list) else [raw_hash]
        hashes = tuple(sorted({value for item in raw_values if (value := _valid_hash(item))}))
        if match and hashes:
            result[f"VB{int(match.group(1))}"] = hashes
    return dict(sorted(result.items(), key=lambda item: int(item[0][2:])))


def _split_sections(text: str) -> list[tuple[str | None, list[str]]]:
    sections = []
    header = None
    body = []
    for line in text.split("\n"):
        match = _SECTION_RE.match(line)
        if match:
            if header is not None or body:
                sections.append((header, body))
            header = match.group(1)
            body = []
        else:
            body.append(line)
    sections.append((header, body))
    return sections


def _join_sections(sections: list[tuple[str | None, list[str]]]) -> str:
    output = []
    for header, body in sections:
        if header is not None:
            output.append(f"[{header}]")
        output.extend(body)
    return "\n".join(output)


def _forms_for_component(component: dict[str, Any]) -> list[_Form]:
    full_hashes = _normalize_vb_hashes(component.get(VB_HASHES_KEY))
    if not full_hashes:
        vb0_hash = _valid_hash(component.get("vb0_hash"))
        if vb0_hash:
            full_hashes["VB0"] = (vb0_hash,)
    forms = [
        _Form(
            level=0,
            ib_hash=str(component.get("ib_hash") or "").lower(),
            index_count=int(component.get("index_count") or 0),
            vb_hashes=full_hashes,
            preferred_slots=(),
            vg_map=tuple(
                sorted(
                    (int(source), int(target))
                    for source, target in (component.get("vg_map") or {}).items()
                )
            ),
        )
    ]
    for level, lod in enumerate(component.get("lods") or [], 1):
        hashes = _normalize_vb_hashes(lod.get(VB_HASHES_KEY))
        if not hashes:
            vb0_hash = _valid_hash(lod.get("vb0_hash"))
            if vb0_hash:
                hashes["VB0"] = (vb0_hash,)
        preferred = tuple(
            sorted(
                (
                    slot
                    for slot in (lod.get("vb_formats") or {})
                    if re.fullmatch(r"VB\d+", str(slot or "").upper())
                ),
                key=lambda slot: int(str(slot)[2:]),
            )
        )
        forms.append(
            _Form(
                level=level,
                ib_hash=str(lod.get("ib_hash") or "").lower(),
                index_count=int(lod.get("index_count") or 0),
                vb_hashes=hashes,
                preferred_slots=preferred,
                vg_map=tuple(
                    sorted(
                        (int(source), int(target))
                        for source, target in (lod.get("vg_map") or {}).items()
                    )
                ),
            )
        )
    return forms


def _has_non_identity_vg_map(form: _Form) -> bool:
    return any(source != target for source, target in form.vg_map)


def _needs_same_ib_form_route(component_id: int, forms: list[_Form]) -> bool:
    if not forms or forms[0].level != 0:
        return False
    full = forms[0]
    full_vb0 = set(full.vb_hashes.get("VB0", ()))
    full_vb1 = set(full.vb_hashes.get("VB1", ()))
    needs_route = False
    for lod in forms[1:]:
        lod_vb0 = set(lod.vb_hashes.get("VB0", ()))
        if not full_vb0 or full_vb0 != lod_vb0:
            continue
        if not _has_non_identity_vg_map(lod):
            continue
        lod_vb1 = set(lod.vb_hashes.get("VB1", ()))
        if not full_vb1 or not lod_vb1:
            if "VB1" in lod.preferred_slots:
                raise FormIdentityError(
                    f"Same-IB form routing for C{component_id} lacks unique VB identities. "
                    "Re-extract the full model and every LOD with the current build."
                )
            continue
        if full_vb1.isdisjoint(lod_vb1):
            needs_route = True
    return needs_route


def _choose_route_slot(component_id: int, forms: list[_Form]) -> str | None:
    if not _needs_same_ib_form_route(component_id, forms):
        return None
    hashes = [set(form.vb_hashes.get("VB1", ())) for form in forms]
    if all(hashes) and all(
        left.isdisjoint(right)
        for index, left in enumerate(hashes)
        for right in hashes[index + 1:]
    ):
        return "VB1"
    raise FormIdentityError(
        f"Same-IB form routing for C{component_id} lacks unique VB identities. "
        "Re-extract the full model and every LOD with the current build."
    )


def _build_routes(metadata: dict[str, Any]) -> list[_Route]:
    routes = []
    for component_id, component in enumerate(metadata.get("components") or []):
        groups = {}
        for form in _forms_for_component(component):
            groups.setdefault((form.ib_hash, form.index_count), []).append(form)
        for (ib_hash, index_count), forms in groups.items():
            if len(forms) < 2 or not _valid_hash(ib_hash) or index_count <= 0:
                continue
            slot = _choose_route_slot(component_id, forms)
            if slot is None:
                continue
            routes.append(
                _Route(
                    component_id=component_id,
                    ib_hash=ib_hash,
                    index_count=index_count,
                    slot=slot,
                    forms=tuple(forms),
                )
            )
    return routes


def apply_same_ib_form_routes(ini_text: str, metadata: dict[str, Any]) -> str:
    if _ROUTE_MARKER in ini_text:
        return ini_text
    routes = _build_routes(metadata)
    if not routes:
        return ini_text

    sections = _split_sections(ini_text)
    route_by_component = {route.component_id: route for route in routes}
    canonical_bodies = {}
    output = []
    for header, body in sections:
        component_match = _COMPONENT_OVERRIDE_RE.match(header or "")
        lod_match = _LOD_OVERRIDE_RE.match(header or "")
        component_id = None
        if component_match:
            component_id = int(component_match.group("id"))
        elif lod_match:
            component_id = int(lod_match.group("id"))
        route = route_by_component.get(component_id) if component_id is not None else None
        if route is None:
            output.append((header, body))
            continue

        section_hash = None
        section_count = None
        for line in body:
            key, sep, value = line.partition("=")
            if not sep:
                continue
            if key.strip().lower() == "hash":
                section_hash = value.strip().lower()
            elif key.strip().lower() == "match_index_count":
                try:
                    section_count = int(value.strip())
                except ValueError:
                    pass
        if (section_hash, section_count) != (route.ib_hash, route.index_count):
            output.append((header, body))
            continue
        if lod_match:
            continue
        canonical_bodies[component_id] = body
        output.append((header, body))

    missing = sorted(set(route_by_component) - set(canonical_bodies))
    if missing:
        raise FormIdentityError(
            "Generated INI is missing same-IB component override(s): "
            + ", ".join(f"C{component_id}" for component_id in missing)
        )

    all_hashes = sorted(
        {
            vb_hash
            for route in routes
            for form in route.forms
            for vb_hash in form.vb_hashes[route.slot]
        }
    )
    filter_by_hash = {vb_hash: 7700 + index for index, vb_hash in enumerate(all_hashes)}
    tag_sections = [
        (None, [_ROUTE_MARKER])
    ]
    for vb_hash in all_hashes:
        tag_sections.append(
            (
                f"TextureOverride_FormIdentity_{vb_hash}",
                [f"hash = {vb_hash}", f"filter_index = {filter_by_hash[vb_hash]}", ""],
            )
        )

    final_sections = []
    tags_inserted = False
    for header, body in output:
        match = _COMPONENT_OVERRIDE_RE.match(header or "")
        component_id = int(match.group("id")) if match else None
        route = route_by_component.get(component_id) if component_id is not None else None
        if route is None:
            final_sections.append((header, body))
            continue
        if not tags_inserted:
            final_sections.extend(tag_sections)
            tags_inserted = True
        match_declarations = []
        common_body = []
        for line in body:
            key, sep, _value = line.partition("=")
            if sep and key.strip().lower() in _OVERRIDE_MATCH_KEYS:
                match_declarations.append(line)
            elif not _LOD_ASSIGN_RE.match(line):
                common_body.append(line)
        routed_body = []
        routed_body.extend(match_declarations)
        slot_expr = route.slot.lower()
        for form_index, form in enumerate(route.forms):
            keyword = "if" if form_index == 0 else "elif"
            conditions = [
                f"{slot_expr} == {filter_by_hash[vb_hash]}"
                for vb_hash in form.vb_hashes[route.slot]
            ]
            routed_body.append(f"{keyword} " + " || ".join(conditions))
            routed_body.append(f"    $lod_level = {form.level}")
            routed_body.extend(
                ("    " + line if line else line)
                for line in common_body
            )
        routed_body.append("endif")
        final_sections.append((header, routed_body))
    return _join_sections(final_sections)


def _index_count(path: Path) -> int | None:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for _ in range(16):
            line = handle.readline()
            if not line:
                break
            match = re.match(r"\s*index count:\s*(\d+)", line, re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


def _scan_dump_signatures(dump_path: Path) -> dict[tuple[str, int, str], set[tuple[tuple[str, str], ...]]]:
    dump_path = Path(dump_path)
    vb_by_event = {}
    ib_draws = []
    for path in dump_path.glob("*.txt"):
        vb_match = _DUMP_VB_RE.match(path.name)
        if vb_match:
            vb_by_event.setdefault(vb_match.group("event"), {})[
                f"VB{int(vb_match.group('slot'))}"
            ] = vb_match.group("hash").lower()
            continue
        ib_match = _DUMP_IB_RE.match(path.name)
        if ib_match:
            count = _index_count(path)
            if count is not None:
                ib_draws.append((ib_match.group("event"), ib_match.group("hash").lower(), count))
    result = {}
    for event, ib_hash, count in ib_draws:
        hashes = vb_by_event.get(event, {})
        vb0_hash = hashes.get("VB0")
        if not vb0_hash:
            continue
        result.setdefault((ib_hash, count, vb0_hash), set()).add(
            tuple(sorted(hashes.items(), key=lambda item: int(item[0][2:])))
        )
    return result


def _find_dump_identity(
    signatures: dict[tuple[str, int, str], set[tuple[tuple[str, str], ...]]],
    form: dict[str, Any],
) -> dict[str, list[str]] | None:
    key = (
        str(form.get("ib_hash") or "").lower(),
        int(form.get("index_count") or 0),
        str(form.get("vb0_hash") or "").lower(),
    )
    matches = signatures.get(key, set())
    if not matches:
        return None
    by_slot = {}
    for signature in matches:
        for slot, vb_hash in signature:
            by_slot.setdefault(slot, set()).add(vb_hash)
    return {
        slot: sorted(hashes)
        for slot, hashes in sorted(by_slot.items(), key=lambda item: int(item[0][2:]))
    }


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def backfill_metadata_from_dumps(
    metadata_path: Path,
    full_dump_path: Path,
    lod_dump_path: Path,
) -> dict[str, Any]:
    metadata_path = Path(metadata_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    full_signatures = _scan_dump_signatures(Path(full_dump_path))
    lod_signatures = _scan_dump_signatures(Path(lod_dump_path))
    for component_id, component in enumerate(metadata.get("components") or []):
        full_identity = _find_dump_identity(full_signatures, component)
        if full_identity:
            component[VB_HASHES_KEY] = full_identity
        for lod in component.get("lods") or []:
            lod_identity = _find_dump_identity(lod_signatures, lod)
            if lod_identity:
                lod[VB_HASHES_KEY] = lod_identity
        collision_forms = [
            form
            for form in _forms_for_component(component)
            if (form.ib_hash, form.index_count)
            == (str(component.get("ib_hash") or "").lower(), int(component.get("index_count") or 0))
        ]
        if len(collision_forms) > 1:
            _choose_route_slot(component_id, collision_forms)
    _write_json_atomic(metadata_path, metadata)
    return metadata


def _component_vb_hashes(component: Any) -> dict[str, list[str]]:
    hashes_by_slot = {}
    raw_data = getattr(component, "raw_data", None)
    for shader_call in getattr(raw_data, "shader_calls", ()) or ():
        resources = getattr(shader_call, "resources", None)
        for slot in range(8):
            try:
                resource = resources.get_by_slot(f"vb{slot}")
            except Exception:
                resource = None
            vb_hash = _valid_hash(getattr(resource, "hash", None))
            if vb_hash:
                hashes_by_slot.setdefault(f"VB{slot}", set()).add(vb_hash)
    return {
        slot: sorted(hashes)
        for slot, hashes in sorted(hashes_by_slot.items(), key=lambda item: int(item[0][2:]))
    }


def _write_full_identities(folder_path: Path, migoto_object: Any) -> None:
    metadata_path = Path(folder_path) / "Metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    for raw_component, component in zip(
        getattr(migoto_object, "components", ()) or (),
        metadata.get("components") or [],
    ):
        hashes = _component_vb_hashes(raw_component)
        if hashes:
            component[VB_HASHES_KEY] = hashes
    _write_json_atomic(metadata_path, metadata)


def _write_lod_identities(cfg: Any, full_object: Any, lod_object: Any, matched_components: dict) -> None:
    from ._efmi_core.migoto_io.blender_interface.utility import resolve_path

    metadata_path = Path(resolve_path(cfg.object_source_folder)) / "Metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    for full_component, component in zip(
        getattr(full_object, "components", ()) or (),
        metadata.get("components") or [],
    ):
        lod_component = matched_components.get(full_component, (None, None))[0]
        hashes = (
            _component_vb_hashes(lod_component)
            if lod_component is not None
            else {
                slot: list(hashes)
                for slot, hashes in _normalize_vb_hashes(component.get(VB_HASHES_KEY)).items()
            }
        )
        if not hashes:
            continue
        for lod in component.get("lods") or []:
            if lod.get("lod_object_name") == getattr(lod_object, "id", None):
                lod[VB_HASHES_KEY] = hashes
    _write_json_atomic(metadata_path, metadata)


_ORIGINAL_EXPORT = None
_EXPORTER_CLASS = None
_ORIGINAL_IMPORT_LODS = None
_EXTRACT_MODULE = None
_ORIGINAL_BUILD_FROM_TEMPLATE = None
_INIMAKER_CLASS = None


def install() -> None:
    global _ORIGINAL_EXPORT, _EXPORTER_CLASS
    global _ORIGINAL_IMPORT_LODS, _EXTRACT_MODULE
    global _ORIGINAL_BUILD_FROM_TEMPLATE, _INIMAKER_CLASS
    if _ORIGINAL_EXPORT is not None:
        return

    from ._efmi_core.migoto_io.object_extractor.migoto_object.migoto_object_exporter import ObjectExporter
    from ._efmi_core.extract_frame_data import extract_frame_data as extract_module
    from ._efmi_core.blender_export.ini_maker import IniMaker
    from ._efmi_core.migoto_io.blender_interface.utility import resolve_path

    _EXPORTER_CLASS = ObjectExporter
    _ORIGINAL_EXPORT = ObjectExporter.export

    def export_with_form_identities(self, folder_path, migoto_object, textures_descriptor):
        result = _ORIGINAL_EXPORT(self, folder_path, migoto_object, textures_descriptor)
        _write_full_identities(folder_path, migoto_object)
        return result

    ObjectExporter.export = export_with_form_identities

    _EXTRACT_MODULE = extract_module
    _ORIGINAL_IMPORT_LODS = extract_module.import_lods

    def import_lods_with_form_identities(context, cfg, full_object, lod_object, matched_components):
        result = _ORIGINAL_IMPORT_LODS(context, cfg, full_object, lod_object, matched_components)
        _write_lod_identities(cfg, full_object, lod_object, matched_components)
        return result

    extract_module.import_lods = import_lods_with_form_identities

    _INIMAKER_CLASS = IniMaker
    _ORIGINAL_BUILD_FROM_TEMPLATE = IniMaker.build_from_template

    def build_with_form_routes(self, context, cfg, template_string=None, with_checksum=False):
        result = _ORIGINAL_BUILD_FROM_TEMPLATE(
            self,
            context,
            cfg,
            template_string=template_string,
            with_checksum=False,
        )
        metadata_path = Path(resolve_path(cfg.object_source_folder)) / "Metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        result = apply_same_ib_form_routes(result, metadata)
        if with_checksum:
            result = type(self).with_checksum(result)
        self.ini_string = result
        return result

    IniMaker.build_from_template = build_with_form_routes


def remove() -> None:
    global _ORIGINAL_EXPORT, _EXPORTER_CLASS
    global _ORIGINAL_IMPORT_LODS, _EXTRACT_MODULE
    global _ORIGINAL_BUILD_FROM_TEMPLATE, _INIMAKER_CLASS
    if _ORIGINAL_BUILD_FROM_TEMPLATE is not None and _INIMAKER_CLASS is not None:
        _INIMAKER_CLASS.build_from_template = _ORIGINAL_BUILD_FROM_TEMPLATE
    if _ORIGINAL_IMPORT_LODS is not None and _EXTRACT_MODULE is not None:
        _EXTRACT_MODULE.import_lods = _ORIGINAL_IMPORT_LODS
    if _ORIGINAL_EXPORT is not None and _EXPORTER_CLASS is not None:
        _EXPORTER_CLASS.export = _ORIGINAL_EXPORT
    _ORIGINAL_BUILD_FROM_TEMPLATE = None
    _INIMAKER_CLASS = None
    _ORIGINAL_IMPORT_LODS = None
    _EXTRACT_MODULE = None
    _ORIGINAL_EXPORT = None
    _EXPORTER_CLASS = None
