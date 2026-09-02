"""EFMI driver-layer Slot-style texture export.

The pure planner consumes schema-v4 ShaderTextureUsage.json evidence and the
textures already collected by EFMI. The reversible hook post-processes the
rendered default INI without modifying the vendored core.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from ...core.export import slot_formats


_COMPONENT_RE = re.compile(r"^Component\s+(\d+)$", re.I)
_VS_RE = re.compile(r"^vs=([0-9a-f?]+)$", re.I)
_PS_RE = re.compile(r"^ps=([0-9a-f?]+)$", re.I)
_SLOT_RE = re.compile(r"^ps-t(\d+)$", re.I)
_SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_TEXTURE_OVERRIDE_RE = re.compile(r"^TextureOverride_Texture(\d+)$", re.I)
_DRAW_SECTION_RE = re.compile(r"^CommandList_Draw_Component(\d+)$", re.I)
_OVERRIDE_TRIGGER_RE = re.compile(
    r"^\s*run\s*=\s*CommandList\\EFMIv1\\OverrideTextures\s*$", re.I
)
_DRAW_RE = re.compile(r"^\s*drawindexed\s*=", re.I)
_HEX8_RE = re.compile(r"^[0-9a-f]{8}$")


class SlotStyleExportError(ValueError):
    pass


@dataclass(frozen=True)
class _Record:
    texture_hash: str
    format_name: str


@dataclass(frozen=True)
class _ObservedPair:
    component_id: int
    source: str
    slots: tuple[tuple[int, _Record], ...]


@dataclass(frozen=True)
class _Branch:
    signature: tuple[tuple[int, str], ...]
    assignments: tuple[tuple[int, str], ...]
    assignment_hashes: tuple[str, ...]
    source: str
    negative_signature: tuple[tuple[int, str], ...] = ()


@dataclass
class SlotExportPlan:
    block_text: str
    component_lists: dict[int, str]
    component_assignment_slots: dict[int, tuple[int, ...]]
    covered_resource_indices: set[int]
    covered_hashes: set[str]
    used_slots: tuple[int, ...]
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


_INSTALLED = False
_ORIGINAL_BUILD_FROM_TEMPLATE = None
last_report: list[str] = []


def _section_spans(lines: list[str]) -> list[tuple[str, int, int]]:
    headers = [
        (match.group(1), index)
        for index, line in enumerate(lines)
        if (match := _SECTION_RE.match(line))
    ]
    return [
        (name, start, headers[pos + 1][1] if pos + 1 < len(headers) else len(lines))
        for pos, (name, start) in enumerate(headers)
    ]


def _load_observed_pairs(source_folder: Path) -> list[_ObservedPair]:
    path = source_folder / "ShaderTextureUsage.json"
    if not path.is_file():
        raise SlotStyleExportError(
            "ShaderTextureUsage.json is missing; re-extract the EFMI object first"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SlotStyleExportError(
            f"failed to read ShaderTextureUsage.json: {exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("version") != 4:
        raise SlotStyleExportError(
            "EFMI Slot-style export requires schema-v4 ShaderTextureUsage.json "
            "with log-backed freshness evidence; re-extract with Skip Dirty Slot enabled"
        )

    pairs: list[_ObservedPair] = []
    for component_key, component in payload.items():
        match = _COMPONENT_RE.match(str(component_key))
        if match is None or not isinstance(component, dict):
            continue
        component_id = int(match.group(1))
        for vs_key, vs_block in component.items():
            if _VS_RE.match(str(vs_key)) is None or not isinstance(vs_block, dict):
                continue
            for ps_key, ps_block in vs_block.items():
                if _PS_RE.match(str(ps_key)) is None or not isinstance(ps_block, dict):
                    continue
                slots: list[tuple[int, _Record]] = []
                for slot_key, record in ps_block.items():
                    slot_match = _SLOT_RE.match(str(slot_key))
                    if slot_match is None or not isinstance(record, dict):
                        continue
                    fresh = record.get("fresh")
                    if not isinstance(fresh, bool):
                        raise SlotStyleExportError(
                            f"{component_key}/{vs_key}/{ps_key}/{slot_key} has no "
                            "schema-v4 freshness flag; re-extract the object"
                        )
                    if not fresh:
                        continue
                    texture_hash = str(record.get("hash") or "").strip().lower()
                    format_name = str(record.get("format") or "").strip().upper()
                    if not _HEX8_RE.fullmatch(texture_hash):
                        continue
                    slots.append((
                        int(slot_match.group(1)),
                        _Record(texture_hash=texture_hash, format_name=format_name),
                    ))
                if slots:
                    pairs.append(_ObservedPair(
                        component_id=component_id,
                        source=f"{component_key}/{vs_key}/{ps_key}",
                        slots=tuple(sorted(slots)),
                    ))
    if not pairs:
        raise SlotStyleExportError(
            "ShaderTextureUsage.json contains no fresh EFMI pixel-texture bindings"
        )
    return pairs


def _ranges_from_extracted_object(extracted_object) -> tuple[
        dict[int, tuple[int, int]], dict[int, list[tuple[int, int, int]]]]:
    base: dict[int, tuple[int, int]] = {}
    lods: dict[int, list[tuple[int, int, int]]] = {}
    for component_id, component in enumerate(
            getattr(extracted_object, "components", ()) or ()):
        base[component_id] = (
            int(getattr(component, "index_offset")),
            int(getattr(component, "index_count")),
        )
        for level, lod in enumerate(getattr(component, "lods", ()) or (), start=1):
            if getattr(lod, "present", True) is False:
                continue
            lods.setdefault(component_id, []).append((
                level,
                int(getattr(lod, "index_offset")),
                int(getattr(lod, "index_count")),
            ))
    return base, lods


def _signatures_overlap(
        left: tuple[tuple[int, str], ...],
        right: tuple[tuple[int, str], ...],
) -> bool:
    left_map = dict(left)
    right_map = dict(right)
    return not any(
        left_map[slot] != right_map[slot]
        for slot in left_map.keys() & right_map.keys()
    )


def _is_subset(
        subset: tuple[tuple[int, str], ...],
        superset: tuple[tuple[int, str], ...],
) -> bool:
    return set(subset).issubset(set(superset))


def component_texture_counts(source_folder: Path) -> dict[int, int]:
    hashes: dict[int, set[str]] = {}
    for pair in _load_observed_pairs(Path(source_folder)):
        bucket = hashes.setdefault(pair.component_id, set())
        bucket.update(record.texture_hash for _slot, record in pair.slots)
    return {
        component_id: len(values)
        for component_id, values in sorted(hashes.items())
    }


def build_plan(
        source_folder: Path,
        textures,
        extracted_object,
        eligible_components: set[int] | None = None,
) -> SlotExportPlan:
    pairs = _load_observed_pairs(Path(source_folder))
    observed_components_by_hash: dict[str, set[int]] = {}
    for pair in pairs:
        for _slot, record in pair.slots:
            observed_components_by_hash.setdefault(
                record.texture_hash, set()
            ).add(pair.component_id)
    resource_by_hash = {
        str(texture.hash).strip().lower(): f"Resource_Texture{index}"
        for index, texture in enumerate(textures)
        if _HEX8_RE.fullmatch(str(texture.hash).strip().lower())
    }
    if not resource_by_hash:
        raise SlotStyleExportError("the EFMI export contains no texture resources")

    raw_by_component: dict[int, list[_Branch]] = {}
    format_by_component_tag: dict[tuple[int, str], set[str]] = {}
    assigned_occurrences: set[tuple[int, str, int, str]] = set()
    required_occurrences: set[tuple[int, str, int, str]] = set()
    for pair in pairs:
        if (eligible_components is not None
                and pair.component_id not in eligible_components):
            continue
        slot_map = dict(pair.slots)
        assignments: list[tuple[int, str]] = []
        assignment_hashes: list[str] = []
        for slot, record in pair.slots:
            resource = resource_by_hash.get(record.texture_hash)
            if resource is None:
                continue
            occurrence = (pair.component_id, pair.source, slot, record.texture_hash)
            required_occurrences.add(occurrence)
            assignments.append((slot, resource))
            assignment_hashes.append(record.texture_hash)
        if not assignments:
            continue

        signature: list[tuple[int, str]] = []
        for slot, record in pair.slots:
            if not record.format_name:
                continue
            try:
                tag = slot_formats.filter_index_text(record.format_name)
            except ValueError:
                continue
            signature.append((slot, tag))
            format_by_component_tag.setdefault(
                (pair.component_id, tag), set()
            ).add(record.format_name)
        assignment_slots = {slot for slot, _resource in assignments}
        missing = assignment_slots - {slot for slot, _tag in signature}
        if missing:
            raise SlotStyleExportError(
                f"{pair.source} has no recorded DXGI format for assignment slot(s): "
                + ", ".join(f"ps-t{slot}" for slot in sorted(missing))
            )
        branch = _Branch(
            signature=tuple(signature),
            assignments=tuple(assignments),
            assignment_hashes=tuple(assignment_hashes),
            source=pair.source,
        )
        raw_by_component.setdefault(pair.component_id, []).append(branch)
        assigned_occurrences.update(
            (pair.component_id, pair.source, slot, slot_map[slot].texture_hash)
            for slot, _resource in assignments
        )

    missing_occurrences = required_occurrences - assigned_occurrences
    if missing_occurrences:
        sample = sorted(missing_occurrences)[0]
        raise SlotStyleExportError(
            f"Component {sample[0]} texture {sample[3]} at ps-t{sample[2]} "
            "cannot be represented by a Slot-style branch"
        )
    if not raw_by_component:
        raise SlotStyleExportError(
            "none of the exported textures has fresh Slot-style evidence"
        )

    branches_by_component: dict[int, list[_Branch]] = {}
    for component_id, raw_branches in raw_by_component.items():
        unique: dict[tuple[tuple[tuple[int, str], ...], tuple[tuple[int, str], ...]], _Branch] = {}
        signatures: dict[tuple[tuple[int, str], ...], tuple[tuple[int, str], ...]] = {}
        for branch in raw_branches:
            previous_assignments = signatures.get(branch.signature)
            if previous_assignments is not None and previous_assignments != branch.assignments:
                raise SlotStyleExportError(
                    f"Component {component_id} has multiple texture assignments "
                    "with the same Slot-style format signature"
                )
            signatures[branch.signature] = branch.assignments
            unique.setdefault((branch.signature, branch.assignments), branch)
        branches = sorted(
            unique.values(),
            key=lambda branch: (
                -len(branch.signature), branch.signature, branch.assignments
            ),
        )
        resolved: list[_Branch] = []
        for index, left in enumerate(branches):
            negative_terms: set[tuple[int, str]] = set()
            for right in branches:
                if right is left:
                    continue
                if left.assignments == right.assignments:
                    continue
                if not _signatures_overlap(left.signature, right.signature):
                    continue
                if _is_subset(right.signature, left.signature):
                    continue
                candidates = sorted(set(right.signature) - set(left.signature))
                if not candidates:
                    raise SlotStyleExportError(
                        f"Component {component_id} has indistinguishable "
                        f"Slot-style assignments: {left.source} / {right.source}"
                    )
                negative_terms.add(candidates[0])
            resolved.append(replace(
                left,
                negative_signature=tuple(sorted(negative_terms)),
            ))
        branches_by_component[component_id] = resolved

    base_ranges, lod_ranges = _ranges_from_extracted_object(extracted_object)
    missing_ranges = set(branches_by_component) - set(base_ranges)
    if missing_ranges:
        raise SlotStyleExportError(
            "component index range missing for Component(s): "
            + ", ".join(map(str, sorted(missing_ranges)))
        )

    component_lists: dict[int, str] = {}
    component_assignment_slots: dict[int, tuple[int, ...]] = {}
    used_slots: set[int] = set()
    used_formats: dict[int, dict[str, set[str]]] = {}
    block: list[str] = [
        "",
        "; ============================================================",
        "; Slot-style texture layer",
        "; Conditions use fresh schema-v4 STU format-family evidence.",
        "; ============================================================",
    ]
    assigned_hashes: set[str] = set()
    for component_id in sorted(branches_by_component):
        name = f"CommandListSetTexturesComponent{component_id}"
        component_lists[component_id] = name
        assignment_slots: set[int] = set()
        block.extend(("", f"[{name}]"))
        for branch_index, branch in enumerate(branches_by_component[component_id]):
            condition = " && ".join(
                f"ps-t{slot} == {tag}" for slot, tag in branch.signature
            )
            negative = " && ".join(
                f"ps-t{slot} != {tag}"
                for slot, tag in branch.negative_signature
            )
            if negative:
                condition += " && " + negative
            block.append(f"{'if' if branch_index == 0 else 'else if'} {condition}")
            for slot, resource in branch.assignments:
                backup = f"ResourceSlotBackupC{component_id}T{slot}"
                block.append(f"    {backup} = reference ps-t{slot}")
                block.append(f"    ps-t{slot} = {resource}")
                assignment_slots.add(slot)
                used_slots.add(slot)
            assigned_hashes.update(branch.assignment_hashes)
            for _slot, tag in branch.signature + branch.negative_signature:
                format_names = format_by_component_tag.get((component_id, tag))
                if format_names:
                    used_formats.setdefault(component_id, {}).setdefault(
                        tag, set()
                    ).update(format_names)
        block.append("endif")
        component_assignment_slots[component_id] = tuple(sorted(assignment_slots))

    block.extend(("", "; -- Component-local slot backup resources and restore commands"))
    for component_id in sorted(component_assignment_slots):
        slots = component_assignment_slots[component_id]
        for slot in slots:
            block.extend(("", f"[ResourceSlotBackupC{component_id}T{slot}]"))
        block.extend(("", f"[CommandListRestoreTexturesComponent{component_id}]"))
        for slot in slots:
            backup = f"ResourceSlotBackupC{component_id}T{slot}"
            block.extend((
                f"if {backup} !== null",
                f"    ps-t{slot} = reference {backup}",
                f"    {backup} = null",
                "endif",
            ))

    block.extend(("", "; -- Format-family tags"))
    format_sections = 0
    for component_id in sorted(used_formats):
        ranges = [("Base", *base_ranges[component_id])]
        ranges.extend(
            (f"Lod{level}", first, count)
            for level, first, count in lod_ranges.get(component_id, ())
        )
        for tag, format_names in sorted(used_formats[component_id].items()):
            for member in sorted(format_names):
                for range_name, first, count in ranges:
                    safe_member = re.sub(r"[^A-Za-z0-9]", "", member)
                    block.extend((
                        "",
                        f"[TextureOverrideSlotFormatC{component_id}{range_name}{safe_member}]",
                        f"match_first_index = {first}",
                        f"match_index_count = {count}",
                        f"match_priority = {slot_formats.FORMAT_TAG_PRIORITY}",
                        f"match_format = {member}",
                        f"filter_index = {tag}",
                    ))
                    format_sections += 1
    block.append("")

    slotted_components = set(branches_by_component)
    covered_hashes = {
        texture_hash
        for texture_hash in assigned_hashes
        if observed_components_by_hash.get(texture_hash, set()).issubset(
            slotted_components
        )
    }
    covered_resource_indices = {
        index
        for index, texture in enumerate(textures)
        if str(texture.hash).strip().lower() in covered_hashes
    }
    return SlotExportPlan(
        block_text="\n".join(block),
        component_lists=component_lists,
        component_assignment_slots=component_assignment_slots,
        covered_resource_indices=covered_resource_indices,
        covered_hashes=covered_hashes,
        used_slots=tuple(sorted(used_slots)),
        stats={
            "components": len(component_lists),
            "branches": sum(len(value) for value in branches_by_component.values()),
            "textures": len(assigned_hashes),
            "hash_fallbacks": len(assigned_hashes - covered_hashes),
            "slots": len(used_slots),
            "format_sections": format_sections,
        },
    )


def transform_ini(ini_text: str, plan: SlotExportPlan) -> str:
    lines = ini_text.split("\n")
    spans = _section_spans(lines)
    if not spans:
        raise SlotStyleExportError("rendered EFMI INI has no sections")
    existing_sections = {name.casefold() for name, _start, _end in spans}
    generated_sections = {
        match.group(1).casefold()
        for line in plan.block_text.splitlines()
        if (match := _SECTION_RE.match(line))
    }
    conflicts = existing_sections & generated_sections
    if conflicts:
        raise SlotStyleExportError(
            "rendered EFMI INI already contains Slot-style section(s): "
            + ", ".join(sorted(conflicts)[:5])
        )

    deleted: set[int] = set()
    insert_after: dict[int, list[str]] = {}
    removed_indices: set[int] = set()
    transformed_components: set[int] = set()

    for name, start, end in spans:
        texture_match = _TEXTURE_OVERRIDE_RE.match(name.strip())
        if texture_match is not None:
            index = int(texture_match.group(1))
            if index in plan.covered_resource_indices:
                deleted.update(range(start, end))
                removed_indices.add(index)
            continue

        draw_match = _DRAW_SECTION_RE.match(name.strip())
        if draw_match is None:
            continue
        component_id = int(draw_match.group(1))
        list_name = plan.component_lists.get(component_id)
        if list_name is None:
            continue
        triggers = [
            index for index in range(start + 1, end)
            if _OVERRIDE_TRIGGER_RE.match(lines[index])
        ]
        draws = [
            index for index in range(start + 1, end)
            if _DRAW_RE.match(lines[index])
        ]
        if len(triggers) != 1 or not draws:
            raise SlotStyleExportError(
                f"Component {component_id} has no safe OverrideTextures/draw anchor "
                "in the rendered default EFMI INI"
            )
        trigger = triggers[0]
        indent = lines[trigger][:len(lines[trigger]) - len(lines[trigger].lstrip())]
        insert_after[trigger] = [f"{indent}run = {list_name}"]
        section_tail = max(
            index for index in range(start + 1, end) if lines[index].strip()
        )
        insert_after[section_tail] = [
            f"{indent}run = CommandListRestoreTexturesComponent{component_id}"
        ]
        transformed_components.add(component_id)

    missing_indices = plan.covered_resource_indices - removed_indices
    if missing_indices:
        raise SlotStyleExportError(
            "rendered EFMI INI is missing texture override section(s): "
            + ", ".join(map(str, sorted(missing_indices)))
        )
    missing_components = set(plan.component_lists) - transformed_components
    if missing_components:
        raise SlotStyleExportError(
            "rendered EFMI INI is missing component draw section(s): "
            + ", ".join(map(str, sorted(missing_components)))
        )

    output: list[str] = []
    for index, line in enumerate(lines):
        if index not in deleted:
            output.append(line)
        if index in insert_after:
            output.extend(insert_after[index])
    result = "\n".join(output).rstrip() + "\n" + plan.block_text
    if not result.endswith("\n"):
        result += "\n"
    return result


def install() -> None:
    global _INSTALLED, _ORIGINAL_BUILD_FROM_TEMPLATE
    if _INSTALLED:
        return
    from ._efmi_core.blender_export import ini_maker as module
    from ._efmi_core.migoto_io.blender_interface.utility import resolve_path

    _ORIGINAL_BUILD_FROM_TEMPLATE = module.IniMaker.build_from_template

    def wrapped(self, context, cfg, template_string=None, with_checksum=False):
        if (not getattr(cfg, "slot_style_textures", False)
                or getattr(cfg, "use_custom_template", False)
                or getattr(cfg, "custom_template_live_update", False)):
            return _ORIGINAL_BUILD_FROM_TEMPLATE(
                self,
                context,
                cfg,
                template_string=template_string,
                with_checksum=with_checksum,
            )
        from . import slot_component_ui

        eligible_components = slot_component_ui.selected_components(context)
        if eligible_components == set():
            return _ORIGINAL_BUILD_FROM_TEMPLATE(
                self,
                context,
                cfg,
                template_string=template_string,
                with_checksum=with_checksum,
            )
        result = _ORIGINAL_BUILD_FROM_TEMPLATE(
            self,
            context,
            cfg,
            template_string=template_string,
            with_checksum=False,
        )
        del last_report[:]
        try:
            plan = build_plan(
                resolve_path(cfg.object_source_folder),
                self.textures,
                self.extracted_object,
                eligible_components=eligible_components,
            )
            result = transform_ini(result, plan)
            message = f"[SlotTextures] EFMI Slot-style texture layer applied: {plan.stats}"
            print(message)
            last_report.append(message)
        except SlotStyleExportError as exc:
            message = f"[SlotTextures] ERROR: EFMI Slot-style export aborted: {exc}"
            print(message)
            last_report.append(message)
            raise
        if with_checksum:
            result = module.IniMaker.with_checksum(result)
        self.ini_string = result
        return result

    wrapped._slot_texture_export_hook = True
    module.IniMaker.build_from_template = wrapped
    _INSTALLED = True


def remove() -> None:
    global _INSTALLED, _ORIGINAL_BUILD_FROM_TEMPLATE
    if not _INSTALLED:
        return
    from ._efmi_core.blender_export import ini_maker as module

    module.IniMaker.build_from_template = _ORIGINAL_BUILD_FROM_TEMPLATE
    _ORIGINAL_BUILD_FROM_TEMPLATE = None
    _INSTALLED = False
