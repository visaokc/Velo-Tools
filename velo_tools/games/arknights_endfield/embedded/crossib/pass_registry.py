"""CrossIB pass discovery from extracted source data.

Classification uses pass-intrinsic GPU evidence read per-draw from the frame
dump — the rasterizer-state identity (cull cluster) and the render-target count
— NOT a hand-maintained vs-hash table. The outline pass is an inverted hull
drawn with its own rasterizer state, so it always lands in a rasterizer cluster
disjoint from the material pass; the remaining slots split by render-target
count (record=0, material=2, prepass=G-buffer≥3, other=1). Because the evidence
is shader-agnostic and character-agnostic, the same public game-shader hash maps
to the same filter_index across every character and game version (no cross-mod
conflict under ``allow_duplicate_hash = overrule``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


# Minimal residual hardcode (17 -> 4). record/prepass/material/outline are
# classified purely by structure (rasterizer cluster + RT count); only these 4
# fixed entries remain:
#   * two outline anchors  -> cross-check the structurally-picked outline cluster
#     (both alive across the whole 6-dump corpus; one hash alone is not reliable
#      — 4921f64a7c74226d is absent in 5 of 6 dumps).
#   * eye(205)/eyelash(206) overrides -> out of the 200-204 cross-IB scope, pinned
#     so eye (5 RT, would look like prepass) / eyelash (sits in the outline
#     cluster) keep the community-canonical 205/206 and never conflict. Users do
#     not cross-IB them; these two only prevent a mislabel + a cross-mod clash.
DEFAULT_SHADER_OVERRIDES: Tuple[Tuple[str, int, str], ...] = (
    ("0742626cbcda30ed", 203, "outline"),
    ("1b835d0e8dbbfb8f", 203, "outline"),
    ("2ff51b35a650fab2", 205, "material"),
    ("16dfc43ed1f233c8", 206, "outline"),
)

# VS hashes used ONLY to cross-check / disambiguate the outline rasterizer
# cluster (never persisted as a pointer value — the pointer is dump-local).
OUTLINE_ANCHORS: frozenset = frozenset({"0742626cbcda30ed", "1b835d0e8dbbfb8f"})

# Canonical pass-role -> filter_index slot.
KIND_TO_FILTER = {
    "record": 200,
    "prepass": 201,
    "material": 202,
    "outline": 203,
    "other": 204,
}

# A vs hash can serve more than one pass across a character's draws (a shared
# skinning VS used by both the material PS and the outline PS). filter_index is
# GLOBAL per vs hash, so the role MUST be a character-independent function of the
# hash, or two velo mods assign the same hash different filters and conflict.
# Majority-vote is NOT character-independent (one character may draw the shared
# VS more times in one pass than another). Resolve instead by FIXED priority of
# the roles the VS ever exhibits: outline is sticky (an outline shader is used
# for outlines in every character — verified: every shared hash has an outline
# draw in all 6 corpus dumps), then material (the most conflict-prone shared
# slot), then the preparation passes, then the 1-RT "other" pass.
_KIND_PRIORITY = {"outline": 0, "material": 1, "prepass": 2, "record": 3, "other": 4}

# A 2-RT draw binding at most this many PS textures is NOT a material (a material
# samples its full texture set, ~9-19 here) — it is a geometry-only special pass,
# e.g. the dodge/attack afterimage (canying) pass that draws the whole character
# with 0-2 textures. Route it to other(204), not material(202). Verified on real
# component-filtered extractions: catches exactly the afterimage passes and leaves
# real materials at 202.
_OTHER_PASS_MAX_TEX = 2

_COMPONENT_RE = re.compile(r"component\s*(\d+)", re.IGNORECASE)
_SLOT_RE = re.compile(r"^ps-t(\d+)$", re.IGNORECASE)
_USAGE_RE = re.compile(
    r"^(?P<texture>[a-f0-9]{8})-vs=(?P<vs>[a-f0-9]+)-ps=(?P<ps>[a-f0-9]+)$",
    re.IGNORECASE,
)
_DRAW_RE = re.compile(
    r"^(?P<idx>\d+)-ib=(?P<ib>[a-f0-9]+)(?:\([^)]*\))?-vs=(?P<vs>[a-f0-9]+)-ps=(?P<ps>[a-f0-9]+)\.txt$",
    re.IGNORECASE,
)
_FRAME_TEX_SLOT_RE = re.compile(r"-ps-t(\d+)=", re.IGNORECASE)


@dataclass
class PassRecord:
    component_id: int
    vs_hash: str
    ps_hash: str
    slots: Set[int] = field(default_factory=set)
    output_count: Optional[int] = None
    exact_draw: bool = False
    # pass-intrinsic evidence (set for exact frame-analysis draws):
    raster: Optional[str] = None  # rasterizer-state pointer (dump-local cluster id)
    event: Optional[int] = None

    @property
    def rt_count(self) -> Optional[int]:
        """Render-target count == number of dumped output targets for the draw."""
        return self.output_count

    @property
    def tex_count(self) -> int:
        """Distinct PS texture-binding slots used by the draw."""
        return len(self.slots)


class CrossIBPassRegistry:
    def __init__(self):
        self._filter_by_hash: Dict[str, int] = {}
        # Authoritative per-hash pass role (drives Layer-2 predicates). Seeded
        # from the 4 fixed overrides; unknown hashes get a resolved role in
        # _finalize_unknown_filters.
        self._kind_by_hash: Dict[str, str] = {}
        self._default_kind_by_hash: Dict[str, str] = {}
        self._default_hashes_by_filter: Dict[int, List[str]] = {}
        for hash_value, filter_index, kind in DEFAULT_SHADER_OVERRIDES:
            self._filter_by_hash[hash_value] = filter_index
            self._kind_by_hash[hash_value] = kind
            self._default_kind_by_hash[hash_value] = kind
            self._default_hashes_by_filter.setdefault(filter_index, []).append(hash_value)

        self._unknown_hashes: Set[str] = set()
        self._records_by_component: Dict[int, List[PassRecord]] = {}
        self._texture_records_by_component: Dict[int, List[PassRecord]] = {}
        self._exact_components: Set[int] = set()
        self._used_hashes: Set[str] = set()
        self._used_filters: Set[int] = set()

    @classmethod
    def from_source_folder(cls, source_folder) -> "CrossIBPassRegistry":
        registry = cls()
        folder = Path(source_folder) if source_folder else None
        if folder is None or not folder.is_dir():
            return registry

        folder = registry._resolve_source_folder(folder)
        ib_to_component = registry._load_ib_map(folder)
        registry._load_texture_usage(folder)
        registry._load_frame_analysis(folder, ib_to_component)
        registry._finalize_unknown_filters()
        return registry

    def record_filters(self, component_id: int) -> List[int]:
        records = [record for record in self._records(component_id) if self._is_record(record)]
        if records:
            return self._filters_from_records(records)
        return self._fallback_filter(200)

    def provider_self_filters(self, component_id: int) -> List[int]:
        records = [
            record for record in self._records(component_id)
            if self._is_provider_visible(record)
        ]
        base = self._filters_from_records(records) if records else self._fallback_filter(200, 201)
        # The afterimage(204) special pass must ALSO be in the provider self-draw
        # so its bones get recorded/redirected (matching `if vs == 200||201||204`),
        # ADDED on top of the record/prepass baseline — never replacing it (a
        # component whose only provider-visible draw this frame is the afterimage
        # must still self-draw at 200/201 for normal play).
        afterimage = [r for r in self._records(component_id) if self._is_afterimage(r)]
        if afterimage:
            return self._unique_ints(list(base) + self._filters_from_records(afterimage))
        return base

    def provider_material_filters(self, component_id: int) -> List[int]:
        records = [
            record for record in self._records(component_id)
            if self._is_provider_material(record)
        ]
        if records:
            return self._filters_from_records(records)
        return []

    def consumer_borrow_filters(self, component_id: int) -> List[int]:
        records = [
            record for record in self._records(component_id)
            if self._is_consumer_visible(record)
        ]
        if records:
            return self._filters_from_records(records)
        return self._fallback_filter(202, 203)

    def condition(self, filters: Sequence[int]) -> str:
        values = self._unique_ints(filters)
        if not values:
            values = [202]
        return "if " + " || ".join(f"vs == {value}" for value in values)

    def shader_overrides(self) -> List[Tuple[str, int]]:
        self._finalize_unknown_filters()
        required_hashes = set(self._used_hashes)
        for filter_index in self._used_filters:
            required_hashes.update(self._default_hashes_by_filter.get(filter_index, []))

        ordered = []
        seen = set()
        for hash_value, _filter_index, _kind in DEFAULT_SHADER_OVERRIDES:
            if hash_value in required_hashes and hash_value not in seen:
                ordered.append((hash_value, self._filter_by_hash[hash_value]))
                seen.add(hash_value)

        for hash_value in sorted(required_hashes - seen):
            ordered.append((hash_value, self._filter_by_hash[hash_value]))
        return ordered

    def _records(self, component_id: int) -> List[PassRecord]:
        if component_id in self._exact_components:
            return self._records_by_component.get(component_id, [])
        return self._texture_records_by_component.get(component_id, [])

    def _load_ib_map(self, folder: Path) -> Dict[str, int]:
        metadata_path = folder / "Metadata.json"
        if not metadata_path.is_file():
            return {}
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[CrossIB] Failed to parse Metadata.json for pass registry: {exc}")
            return {}

        result = {}
        for component_id, component in enumerate(payload.get("components") or []):
            if not isinstance(component, dict):
                continue
            ib_hash = (component.get("ib_hash") or "").lower()
            if ib_hash:
                result[ib_hash] = component_id
            for lod in component.get("lods") or []:
                if isinstance(lod, dict):
                    lod_hash = (lod.get("ib_hash") or "").lower()
                    if lod_hash:
                        result[lod_hash] = component_id
        return result

    def _resolve_source_folder(self, folder: Path) -> Path:
        if (folder / "Metadata.json").is_file():
            return folder

        candidates = [
            candidate for candidate in folder.iterdir()
            if candidate.is_dir() and (candidate / "Metadata.json").is_file()
        ]
        if not candidates:
            return folder

        def score(candidate: Path) -> Tuple[int, int, str]:
            name = candidate.name.lower()
            if name.startswith("character"):
                kind_score = 2
            elif name.startswith("weapon"):
                kind_score = 0
            else:
                kind_score = 1
            return (kind_score, self._metadata_component_count(candidate), candidate.name)

        return max(candidates, key=score)

    def _metadata_component_count(self, folder: Path) -> int:
        try:
            payload = json.loads((folder / "Metadata.json").read_text(encoding="utf-8"))
        except Exception:
            return 0
        components = payload.get("components") or []
        return len(components) if isinstance(components, list) else 0

    def _load_texture_usage(self, folder: Path):
        usage_path = folder / "TextureUsage.json"
        if not usage_path.is_file():
            return
        try:
            payload = json.loads(usage_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[CrossIB] Failed to parse TextureUsage.json for pass registry: {exc}")
            return

        grouped: Dict[Tuple[int, str, str], PassRecord] = {}
        for component_name, slots in payload.items():
            component_id = self._parse_component_id(component_name)
            if component_id is None or not isinstance(slots, dict):
                continue
            for slot_name, entries in slots.items():
                slot_match = _SLOT_RE.match(str(slot_name))
                if not slot_match:
                    continue
                slot_id = int(slot_match.group(1))
                for entry in entries or []:
                    usage_match = _USAGE_RE.match(str(entry))
                    if not usage_match:
                        continue
                    vs_hash = usage_match.group("vs").lower()
                    ps_hash = usage_match.group("ps").lower()
                    key = (component_id, vs_hash, ps_hash)
                    record = grouped.setdefault(
                        key,
                        PassRecord(component_id=component_id, vs_hash=vs_hash, ps_hash=ps_hash),
                    )
                    record.slots.add(slot_id)
                    self._note_hash(vs_hash)

        for record in grouped.values():
            self._texture_records_by_component.setdefault(record.component_id, []).append(record)

    def _load_frame_analysis(self, folder: Path, ib_to_component: Dict[str, int]):
        if not ib_to_component:
            return
        for candidate in self._frame_analysis_candidates(folder):
            records = self._scan_frame_analysis(candidate, ib_to_component)
            if records:
                for record in records:
                    self._records_by_component.setdefault(record.component_id, []).append(record)
                    self._exact_components.add(record.component_id)
                    self._note_hash(record.vs_hash)
                return

    def _frame_analysis_candidates(self, folder: Path) -> Iterable[Path]:
        seen = set()
        for candidate in (folder, folder.parent, folder.parent.parent):
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_dir():
                yield candidate

    def _load_raster_by_event(self, folder: Path) -> Dict[int, str]:
        """event -> rasterizer-state pointer, from the dump's log.txt (carry-forward).

        Reuses transparency_classifier's log parser (which now also captures
        RSSetState). Missing/unparsable log degrades to {} (no outline cluster).
        """
        log_path = folder / "log.txt"
        if not log_path.is_file():
            return {}
        try:
            from .transparency_classifier import _parse_log_draw_states
            states = _parse_log_draw_states(log_path)
        except Exception as exc:
            print(f"[CrossIB] rasterizer parse failed for {log_path}: {exc}")
            return {}
        return {event: st.raster_state for event, st in states.items() if st.raster_state}

    def _scan_frame_analysis(self, folder: Path, ib_to_component: Dict[str, int]) -> List[PassRecord]:
        result = []
        raster_by_event = self._load_raster_by_event(folder)
        for path in folder.glob("*-ib=*.txt"):
            match = _DRAW_RE.match(path.name)
            if not match:
                continue
            component_id = ib_to_component.get(match.group("ib").lower())
            if component_id is None:
                continue
            idx = match.group("idx")
            vs_hash = match.group("vs").lower()
            ps_hash = match.group("ps").lower()
            output_count = len(list(folder.glob(f"{idx}-o*=*vs={vs_hash}-ps={ps_hash}*")))
            slots = set()
            for texture_path in folder.glob(f"{idx}-ps-t*=*vs={vs_hash}-ps={ps_hash}*"):
                slot_match = _FRAME_TEX_SLOT_RE.search(texture_path.name)
                if slot_match:
                    slots.add(int(slot_match.group(1)))
            event = int(idx)
            result.append(
                PassRecord(
                    component_id=component_id,
                    vs_hash=vs_hash,
                    ps_hash=ps_hash,
                    slots=slots,
                    output_count=output_count,
                    exact_draw=True,
                    raster=raster_by_event.get(event),
                    event=event,
                )
            )
        return result

    def _note_hash(self, hash_value: str) -> None:
        """Register a seen VS hash at load time without assigning a filter yet.

        Classification is deferred to _finalize_unknown_filters so it runs once
        over the complete record set (deterministic), never on partial evidence
        mid-load.
        """
        hash_value = (hash_value or "").lower()
        if hash_value and hash_value not in self._filter_by_hash:
            self._unknown_hashes.add(hash_value)

    def _ensure_filter(self, hash_value: str) -> int:
        hash_value = (hash_value or "").lower()
        if hash_value not in self._filter_by_hash:
            self._note_hash(hash_value)
            self._finalize_unknown_filters()
        # Fallback to material/202 only if classification somehow left it unset.
        return self._filter_by_hash.get(hash_value, KIND_TO_FILTER["material"])

    # ── classification core (raster cluster + RT count) ─────────────────────

    def _exact_draws(self) -> List[PassRecord]:
        return [
            record
            for records in self._records_by_component.values()
            for record in records
            if record.exact_draw
        ]

    def _resolve_outline_raster(self) -> Optional[str]:
        """The rasterizer cluster of the inverted-hull outline pass, this dump.

        Among the non-zero-RT rasterizer clusters, the outline cluster is the one
        whose draws bind the FEWEST textures (outline PS samples a couple of edge
        textures; material/prepass PS sample the full material set). Verified
        unique across the 6-dump corpus. The pointer VALUE is dump-local and is
        NEVER persisted — it is only a within-extraction grouping key. The two
        outline anchors cross-check the structural pick (and override it on the
        rare future case where a new low-texture pass breaks uniqueness).
        """
        draws = [
            record for record in self._exact_draws()
            if record.raster and (record.output_count or 0) >= 1
        ]
        if not draws:
            return None
        by_raster: Dict[str, List[PassRecord]] = {}
        for record in draws:
            by_raster.setdefault(record.raster, []).append(record)

        def cluster_max_tex(records: List[PassRecord]) -> int:
            return max((record.tex_count for record in records), default=0)

        anchor_rasters = {record.raster for record in draws if record.vs_hash in OUTLINE_ANCHORS}
        # The structural pick is only meaningful with >=2 non-0RT clusters:
        # outline is the lowest-texture cluster RELATIVE to the material/prepass
        # clusters. A lone cluster is the material cluster, not an outline.
        structural = (
            min(by_raster, key=lambda key: (cluster_max_tex(by_raster[key]), key))
            if len(by_raster) >= 2 else None
        )
        if anchor_rasters:
            if structural is not None and structural in anchor_rasters:
                return structural
            chosen = min(anchor_rasters, key=lambda key: (cluster_max_tex(by_raster.get(key, [])), key))
            if structural is not None:
                print(
                    f"[CrossIB] outline raster: structural pick {structural} has no anchor; "
                    f"using anchor cluster {chosen} instead"
                )
            return chosen
        return structural

    def _raw_kind(self, record: PassRecord, outline_raster: Optional[str]) -> str:
        """Pass role of a single draw from pass-intrinsic evidence."""
        if outline_raster is not None and record.raster == outline_raster:
            return "outline"
        rt = record.output_count
        if rt == 0:
            return "record"
        if rt == 2:
            # 2-RT material (samples textures) vs 2-RT geometry-only special pass
            # (afterimage/canying, binds ~0-2 textures) — split by PS texture count.
            if record.tex_count <= _OTHER_PASS_MAX_TEX:
                return "other"
            return "material"
        if rt is not None and rt >= 3:
            return "prepass"
        return "other"  # rt == 1 (the real "other/special" pass, e.g. emission/decal)

    def _vote_kind(self, hash_value: str, outline_raster: Optional[str]) -> str:
        """A vs hash's role = the highest-PRIORITY role across its own draws.

        Per-draw (not per-hash-aggregate); resolved by _KIND_PRIORITY rather than
        majority so a vs shared across passes maps to the same slot in every
        character (a count-based vote would flip with per-character draw counts).
        """
        kinds = {
            self._raw_kind(record, outline_raster)
            for record in self._exact_draws()
            if record.vs_hash == hash_value
        }
        if not kinds:
            # texture-usage-only / no draw evidence -> safest most-shared slot.
            return "material"
        return min(kinds, key=lambda kind: _KIND_PRIORITY[kind])

    def _finalize_unknown_filters(self) -> None:
        """Assign every registered unknown VS hash a canonical filter by role.

        Idempotent: hashes already in _filter_by_hash (the 4 fixed overrides and
        previously finalized) are skipped. Deterministic: the outline cluster is
        resolved once over the full draw set, then each hash votes over its own
        draws.
        """
        outline_raster = self._resolve_outline_raster()
        for hash_value in sorted(self._unknown_hashes):
            if hash_value in self._filter_by_hash:
                continue
            kind = self._vote_kind(hash_value, outline_raster)
            filter_index = KIND_TO_FILTER[kind]
            self._filter_by_hash[hash_value] = filter_index
            self._kind_by_hash[hash_value] = kind
            print(f"[CrossIB] vs {hash_value} -> {kind} -> filter {filter_index}")

    def _filters_from_records(self, records: Sequence[PassRecord]) -> List[int]:
        filters = []
        for record in records:
            filter_index = self._ensure_filter(record.vs_hash)
            self._used_hashes.add(record.vs_hash)
            self._used_filters.add(filter_index)
            filters.append(filter_index)
        return self._unique_ints(filters)

    def _fallback_filter(self, *filters: int) -> List[int]:
        for filter_index in filters:
            self._used_filters.add(int(filter_index))
        return self._unique_ints(filters)

    # ── Layer-2 predicates (read the resolved per-hash role) ────────────────

    def _kind_of(self, record: PassRecord) -> Optional[str]:
        return self._kind_by_hash.get(record.vs_hash)

    def _is_record(self, record: PassRecord) -> bool:
        return self._kind_of(record) == "record"

    def _is_outline(self, record: PassRecord) -> bool:
        return self._kind_of(record) == "outline"

    def _is_provider_visible(self, record: PassRecord) -> bool:
        # Provider self/preparation draws = the record(200) + prepass(201) passes.
        # The afterimage(204) pass is added on top in provider_self_filters (not
        # here) so it augments the record/prepass baseline instead of replacing it.
        return self._kind_of(record) in ("record", "prepass")

    def _is_afterimage(self, record: PassRecord) -> bool:
        # the geometry-only other(204) special pass (afterimage/canying).
        return self._kind_of(record) == "other"

    def _is_provider_material(self, record: PassRecord) -> bool:
        return self._kind_of(record) == "material"

    def _is_consumer_visible(self, record: PassRecord) -> bool:
        # A consumer borrows the visible color passes: material(202) + outline(203)
        # (eye is pinned to the "material" role so it is also borrow-visible).
        return self._kind_of(record) in ("material", "outline")

    def _parse_component_id(self, value) -> Optional[int]:
        match = _COMPONENT_RE.search(str(value or ""))
        return int(match.group(1)) if match else None

    def _unique_ints(self, values: Sequence[int]) -> List[int]:
        return sorted({int(value) for value in values})


def build_pass_registry(source_folder=None) -> CrossIBPassRegistry:
    return CrossIBPassRegistry.from_source_folder(source_folder)
