"""FrameAnalysis coverage and grafting helpers for slot-texture evidence."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import constants
from . import dds_meta
from . import log_freshness
from . import stu_metadata


@dataclass(frozen=True)
class RawDraw:
    call_id: str
    ib: str
    vb0: Optional[str]
    vs: str
    ps: str
    ps_slots: Tuple[int, ...]
    fresh_slots: Tuple[int, ...]


@dataclass(frozen=True)
class RawSlot:
    call_id: str
    slot: int
    tex_hash: str
    old_hash: Optional[str]
    vs: str
    ps: str
    path: Path


@dataclass(frozen=True)
class RawDrawDetail:
    draw: RawDraw
    first_index: Optional[int]
    index_count: Optional[int]
    first_vertex: Optional[int]
    vertex_count: Optional[int]
    slots: Dict[int, RawSlot]


@dataclass(frozen=True)
class GraftResult:
    rows_added: int
    draws_seen: int
    draws_mapped: int
    missing_component: Tuple[str, ...] = ()


_DRAW_RE = re.compile(
    r'^(?P<call>\d{6})-ib=(?P<ib>[0-9a-fA-F]{8})-'
    r'vs=(?P<vs>[0-9a-fA-F]{16})-ps=(?P<ps>[0-9a-fA-F]{16})\.buf$')
_VB0_RE = re.compile(
    r'^(?P<call>\d{6})-vb0=(?P<vb0>[0-9a-fA-F]{8})-'
    r'vs=(?P<vs>[0-9a-fA-F]{16})-ps=(?P<ps>[0-9a-fA-F]{16})\.buf$')
_PS_SLOT_RE = re.compile(
    r'^(?P<call>\d{6})-ps-t(?P<slot>\d+)=(?P<hash>[0-9a-fA-F]{8})'
    r'(?:\((?P<old>[0-9a-fA-F]{8})\))?-vs=(?P<vs>[0-9a-fA-F]{16})-'
    r'ps=(?P<ps>[0-9a-fA-F]{16})\.dds$')
_COMP_RE = re.compile(r'Component\s+(\d+)', re.I)
_VS_RE = re.compile(r'vs=([0-9a-fA-F]{16})')
_PS_RE = re.compile(r'ps=([0-9a-fA-F]{16})')
_FIRST_INDEX_RE = re.compile(r'^first index:\s*(\d+)\s*$', re.I | re.M)
_INDEX_COUNT_RE = re.compile(r'^index count:\s*(\d+)\s*$', re.I | re.M)
_FIRST_VERTEX_RE = re.compile(r'^first vertex:\s*(\d+)\s*$', re.I | re.M)
_VERTEX_COUNT_RE = re.compile(r'^vertex count:\s*(\d+)\s*$', re.I | re.M)


def _load_stu(stu) -> dict:
    if isinstance(stu, (str, Path)):
        return json.loads(Path(stu).read_text(encoding="utf-8"))
    return dict(stu or {})


def _iter_component_maps(stu):
    if isinstance(stu, dict):
        yield stu
        for entry in stu_metadata.form_entries(stu):
            if isinstance(entry, dict) and isinstance(entry.get("components"), dict):
                yield entry["components"]


def _stu_passes(stu) -> Set[Tuple[int, str, str]]:
    out: Set[Tuple[int, str, str]] = set()
    for components in _iter_component_maps(stu):
        for comp_name, pairs in (components or {}).items():
            comp_m = _COMP_RE.search(str(comp_name))
            if not comp_m or not isinstance(pairs, dict):
                continue
            comp_id = int(comp_m.group(1))
            for vs_key, value in pairs.items():
                vs_m = _VS_RE.search(str(vs_key))
                if vs_m and isinstance(value, dict):
                    for ps_key in value:
                        ps_m = _PS_RE.search(str(ps_key))
                        if ps_m:
                            out.add((comp_id, vs_m.group(1).lower(),
                                     ps_m.group(1).lower()))
                    continue
                ps_m = _PS_RE.search(str(vs_key))
                if ps_m:
                    out.add((comp_id, "", ps_m.group(1).lower()))
    return out


def _read_int_field(path: Path, pattern: re.Pattern) -> Optional[int]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = pattern.search(text)
    return int(match.group(1)) if match else None


def _sidecar_txt(root: Path, stem: str) -> Path:
    return root / f"{stem}.txt"


def scan_raw_draws(dump_path) -> List[RawDraw]:
    root = Path(dump_path)
    evidence = log_freshness.parse_log_freshness(root)
    vb0_by_call: Dict[Tuple[str, str, str], str] = {}
    slots_by_call: Dict[Tuple[str, str, str], Dict[int, str]] = {}
    draws: Dict[Tuple[str, str, str], Tuple[str, str]] = {}

    for path in root.iterdir():
        if not path.is_file():
            continue
        name = path.name
        draw_m = _DRAW_RE.match(name)
        if draw_m:
            call = draw_m.group("call")
            vs = draw_m.group("vs").lower()
            ps = draw_m.group("ps").lower()
            draws[(call, vs, ps)] = (draw_m.group("ib").lower(), call)
            continue
        vb0_m = _VB0_RE.match(name)
        if vb0_m:
            key = (vb0_m.group("call"), vb0_m.group("vs").lower(),
                   vb0_m.group("ps").lower())
            vb0_by_call[key] = vb0_m.group("vb0").lower()
            continue
        slot_m = _PS_SLOT_RE.match(name)
        if slot_m:
            key = (slot_m.group("call"), slot_m.group("vs").lower(),
                   slot_m.group("ps").lower())
            slots_by_call.setdefault(key, {})[int(slot_m.group("slot"))] = (
                slot_m.group("hash").lower())

    out: List[RawDraw] = []
    for key, (ib_hash, call) in sorted(draws.items()):
        _call, vs, ps = key
        slots = slots_by_call.get(key, {})
        fresh: List[int] = []
        if evidence is not None:
            for slot, tex_hash in sorted(slots.items()):
                if log_freshness.slot_is_fresh(evidence, call, slot, tex_hash):
                    fresh.append(slot)
        out.append(RawDraw(
            call_id=call,
            ib=ib_hash,
            vb0=vb0_by_call.get(key),
            vs=vs,
            ps=ps,
            ps_slots=tuple(sorted(slots)),
            fresh_slots=tuple(fresh),
        ))
    return out


def scan_raw_draw_details(dump_path) -> List[RawDrawDetail]:
    root = Path(dump_path)
    draws = scan_raw_draws(root)
    slot_by_key: Dict[Tuple[str, str, str], Dict[int, RawSlot]] = {}
    for path in root.iterdir():
        if not path.is_file():
            continue
        slot_m = _PS_SLOT_RE.match(path.name)
        if not slot_m:
            continue
        key = (slot_m.group("call"), slot_m.group("vs").lower(),
               slot_m.group("ps").lower())
        slot_by_key.setdefault(key, {})[int(slot_m.group("slot"))] = RawSlot(
            call_id=slot_m.group("call"),
            slot=int(slot_m.group("slot")),
            tex_hash=slot_m.group("hash").lower(),
            old_hash=(slot_m.group("old").lower()
                      if slot_m.group("old") else None),
            vs=slot_m.group("vs").lower(),
            ps=slot_m.group("ps").lower(),
            path=path,
        )

    details: List[RawDrawDetail] = []
    for draw in draws:
        ib_stem = (f"{draw.call_id}-ib={draw.ib}-vs={draw.vs}-ps={draw.ps}")
        vb0_stem = (f"{draw.call_id}-vb0={draw.vb0}-vs={draw.vs}-ps={draw.ps}"
                    if draw.vb0 else "")
        ib_txt = _sidecar_txt(root, ib_stem)
        vb0_txt = _sidecar_txt(root, vb0_stem) if vb0_stem else None
        key = (draw.call_id, draw.vs, draw.ps)
        details.append(RawDrawDetail(
            draw=draw,
            first_index=_read_int_field(ib_txt, _FIRST_INDEX_RE),
            index_count=_read_int_field(ib_txt, _INDEX_COUNT_RE),
            first_vertex=(_read_int_field(vb0_txt, _FIRST_VERTEX_RE)
                          if vb0_txt is not None else None),
            vertex_count=(_read_int_field(vb0_txt, _VERTEX_COUNT_RE)
                          if vb0_txt is not None else None),
            slots=slot_by_key.get(key, {}),
        ))
    return details


def _mapped_components(component_by_vb0, vb0: str) -> Tuple[int, ...]:
    value = component_by_vb0.get(vb0.lower())
    if value is None:
        return ()
    if isinstance(value, int):
        return (value,)
    return tuple(sorted({int(item) for item in value}))


def audit_raw_pass_coverage(dump_path, stu, component_by_vb0,
                            *, target_components: Optional[Iterable[int]] = None,
                            require_fresh: bool = True) -> List[str]:
    """Report raw FrameAnalysis passes absent from ShaderTextureUsage.

    ``component_by_vb0`` is intentionally explicit: raw dumps do not encode the
    merged component id in filenames, so callers must provide the trusted map.
    A vb0 may map to multiple merged components; any covered component pass is
    enough to accept the raw draw.
    """
    raw_draws = scan_raw_draws(dump_path)
    stu_passes = _stu_passes(_load_stu(stu))
    target_set = None if target_components is None else set(target_components)
    errors: List[str] = []
    for draw in raw_draws:
        if not draw.vb0:
            continue
        comp_ids = _mapped_components(component_by_vb0, draw.vb0)
        if not comp_ids:
            continue
        if target_set is not None:
            comp_ids = tuple(comp_id for comp_id in comp_ids
                             if comp_id in target_set)
        if not comp_ids:
            continue
        if require_fresh and not draw.fresh_slots:
            continue
        if any(
                (comp_id, draw.vs, draw.ps) in stu_passes
                or (comp_id, "", draw.ps) in stu_passes
                for comp_id in comp_ids):
            continue
        comp_label = ",".join(str(comp_id) for comp_id in comp_ids)
        errors.append(
            "raw pass not covered by STU: draw %s Component %s vb0=%s ib=%s "
            "vs=%s ps=%s fresh_slots=%s"
            % (draw.call_id, comp_label, draw.vb0, draw.ib, draw.vs, draw.ps,
               ",".join("ps-t%d" % slot for slot in draw.fresh_slots) or "none"))
    return errors


def audit_local_raw_pass_coverage(dump_path, stu, metadata,
                                  *, source_folder=None,
                                  require_fresh: bool = True) -> List[str]:
    """Report raw passes absent from a local source-folder STU.

    Unlike ``audit_raw_pass_coverage`` this uses Metadata.json draw ranges to
    map each raw draw to one local Component, so same-vb0 multi-component
    objects are not treated as covered by a sibling component's pass.
    """
    usage = _load_stu(stu)
    meta = _load_stu(metadata)
    metadata_vb0 = str((meta or {}).get("vb0_hash") or "").strip().lower()
    if not metadata_vb0:
        return []
    ranges = _component_ranges(meta)
    if not ranges:
        return []
    stu_passes = _stu_passes(usage)
    source_hashes = (_target_slot_hashes(Path(source_folder))
                     if source_folder is not None else set())
    errors: List[str] = []
    for detail in scan_raw_draw_details(dump_path):
        draw = detail.draw
        if not draw.vb0 or draw.vb0.lower() != metadata_vb0:
            continue
        if require_fresh and not draw.fresh_slots:
            continue
        comp_id = _component_for_draw(detail, ranges)
        if comp_id is None:
            errors.append(
                "raw pass cannot be mapped to local Component: draw %s "
                "vb0=%s ib=%s vs=%s ps=%s first_index=%s index_count=%s "
                "first_vertex=%s vertex_count=%s fresh_slots=%s"
                % (draw.call_id, draw.vb0, draw.ib, draw.vs, draw.ps,
                   detail.first_index, detail.index_count,
                   detail.first_vertex, detail.vertex_count,
                   ",".join("ps-t%d" % slot for slot in draw.fresh_slots)
                   or "none"))
            continue
        if ((comp_id, draw.vs, draw.ps) in stu_passes
                or (comp_id, "", draw.ps) in stu_passes):
            continue
        if source_hashes and not any(
                (detail.slots.get(slot) is not None
                 and detail.slots[slot].tex_hash in source_hashes)
                for slot in draw.fresh_slots):
            continue
        errors.append(
            "raw pass not covered by local STU: draw %s Component %d vb0=%s "
            "ib=%s vs=%s ps=%s fresh_slots=%s"
            % (draw.call_id, comp_id, draw.vb0, draw.ib, draw.vs, draw.ps,
               ",".join("ps-t%d" % slot for slot in draw.fresh_slots)
               or "none"))
    return errors


def _component_ranges(metadata) -> List[Tuple[int, int, int, int, int]]:
    ranges: List[Tuple[int, int, int, int, int]] = []
    for comp_id, comp in enumerate((metadata or {}).get("components") or []):
        try:
            index_offset = int(comp.get("index_offset") or 0)
            index_count = int(comp.get("index_count") or 0)
            vertex_offset = int(comp.get("vertex_offset") or 0)
            vertex_count = int(comp.get("vertex_count") or 0)
        except (TypeError, ValueError):
            continue
        ranges.append((comp_id, index_offset, index_count,
                       vertex_offset, vertex_count))
    return ranges


def _component_for_draw(detail: RawDrawDetail,
                        ranges: Iterable[Tuple[int, int, int, int, int]]
                        ) -> Optional[int]:
    exact_vertex = None
    exact_index = None
    for comp_id, index_offset, index_count, vertex_offset, vertex_count in ranges:
        if (detail.first_index is not None
                and detail.index_count is not None
                and detail.first_index == index_offset
                and detail.index_count == index_count):
            exact_index = comp_id
        if (detail.first_vertex is not None
                and detail.vertex_count is not None
                and detail.first_vertex == vertex_offset
                and detail.vertex_count == vertex_count):
            exact_vertex = comp_id
    if exact_index is not None and (exact_vertex is None or exact_vertex == exact_index):
        return exact_index
    if exact_vertex is not None and exact_index is None:
        return exact_vertex
    return None


def _slot_key(slot: int) -> str:
    return f"ps-t{slot}"


def _texture_record(slot: RawSlot) -> dict:
    meta = dds_meta.read_dds_meta(slot.path)
    return {
        "filename": "",
        "hash": slot.tex_hash,
        "format": meta.format if meta else "",
        "width": meta.width if meta else 0,
        "height": meta.height if meta else 0,
        "fresh": True,
    }


def _upsert_source_note(usage: dict, comp_id: int, note: str):
    key = f"Component {comp_id}"
    block = usage.setdefault(key, {})
    if not isinstance(block, dict):
        return
    values = block.setdefault(constants.COMPONENT_SOURCES_KEY, [])
    if isinstance(values, str):
        values = [values]
        block[constants.COMPONENT_SOURCES_KEY] = values
    if isinstance(values, list) and note not in values:
        values.append(note)


def _pass_exists(usage: dict, comp_id: int, vs: str, ps: str) -> bool:
    comp = usage.get(f"Component {comp_id}")
    if not isinstance(comp, dict):
        return False
    vs_map = comp.get(f"vs={vs}")
    return isinstance(vs_map, dict) and f"ps={ps}" in vs_map


def _target_slot_hashes(source_folder: Path) -> Set[str]:
    hashes: Set[str] = set()
    for path in source_folder.glob("* t=*.*"):
        match = re.search(r"t=([0-9a-fA-F]{8})", path.name)
        if match:
            hashes.add(match.group(1).lower())
    return hashes


def graft_raw_passes_into_usage(dump_path, usage, metadata,
                                *, source_folder=None,
                                require_resource_hash: bool = True) -> GraftResult:
    """Merge whole fresh raw passes into an STU dict.

    The graft is evidence-only: it records the runtime slot layout and formats
    from raw FrameAnalysis, but it does not copy raw game textures into the mod
    source folder. Existing model-folder DDS hashes decide which slots can later
    become ResourceTexture assignments.
    """
    if not isinstance(usage, dict):
        return GraftResult(rows_added=0, draws_seen=0, draws_mapped=0)
    metadata_vb0 = str((metadata or {}).get("vb0_hash") or "").strip().lower()
    if not metadata_vb0:
        return GraftResult(rows_added=0, draws_seen=0, draws_mapped=0)
    ranges = _component_ranges(metadata)
    if not ranges:
        return GraftResult(rows_added=0, draws_seen=0, draws_mapped=0)
    source_hashes = (_target_slot_hashes(Path(source_folder))
                     if source_folder is not None else set())
    details = scan_raw_draw_details(dump_path)
    rows_added = 0
    draws_mapped = 0
    missing: List[str] = []
    for detail in details:
        draw = detail.draw
        if not draw.vb0:
            continue
        if draw.vb0.lower() != metadata_vb0:
            continue
        comp_id = _component_for_draw(detail, ranges)
        if comp_id is None:
            missing.append(
                "draw %s vb0=%s ib=%s vs=%s ps=%s first_index=%s "
                "index_count=%s first_vertex=%s vertex_count=%s"
                % (draw.call_id, draw.vb0, draw.ib, draw.vs, draw.ps,
                   detail.first_index, detail.index_count,
                   detail.first_vertex, detail.vertex_count))
            continue
        draws_mapped += 1
        if not draw.fresh_slots:
            continue
        if _pass_exists(usage, comp_id, draw.vs, draw.ps):
            continue
        slot_records = {}
        has_assignable = False
        for slot in sorted(draw.fresh_slots):
            raw_slot = detail.slots.get(slot)
            if raw_slot is None:
                continue
            record = _texture_record(raw_slot)
            slot_records[_slot_key(slot)] = record
            if raw_slot.tex_hash in source_hashes:
                has_assignable = True
        if require_resource_hash and source_hashes and not has_assignable:
            continue
        if not slot_records:
            continue
        comp = usage.setdefault(f"Component {comp_id}", {})
        vs_map = comp.setdefault(f"vs={draw.vs}", {})
        ps_map = dict(slot_records)
        ps_map["depth_only"] = False
        vs_map[f"ps={draw.ps}"] = ps_map
        usage["version"] = max(int(usage.get("version") or 0), 4)
        _upsert_source_note(
            usage, comp_id,
            "raw graft draw %s vb0=%s ib=%s ps=%s" % (
                draw.call_id, draw.vb0, draw.ib, draw.ps))
        rows_added += 1
    return GraftResult(
        rows_added=rows_added,
        draws_seen=len(details),
        draws_mapped=draws_mapped,
        missing_component=tuple(missing),
    )


def graft_raw_passes_into_file(dump_path, usage_path, metadata_path,
                               *, source_folder=None,
                               refresh_audit: bool = True) -> GraftResult:
    usage_path = Path(usage_path)
    metadata_path = Path(metadata_path)
    if not usage_path.is_file() or not metadata_path.is_file():
        return GraftResult(rows_added=0, draws_seen=0, draws_mapped=0)
    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    result = graft_raw_passes_into_usage(
        dump_path, usage, metadata,
        source_folder=source_folder or usage_path.parent)
    if result.rows_added:
        if refresh_audit:
            from . import form_merge
            form_merge.refresh_local_discriminator_audit_in_usage(usage)
        from . import stu_metadata
        stu_metadata.write_usage(usage_path, usage)
    return result
