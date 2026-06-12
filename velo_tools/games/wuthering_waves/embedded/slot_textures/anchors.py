"""Form-anchor candidate recommendation (shared core, velo-owned; vendored
into WWMISlotStyleConverter by its sync_vendor at build time).

Field rules (ADR 0007 rev 5/6, field-proven): this WWMI 3dmigoto fork
matches draws by VERTEX BUFFER hash only - an ib hash never fires - so
candidates are vb0 hashes exclusively (never shaders, never scene props).
Anchors stay USER-SPECIFIED: this module only puts ranked candidates on
the table; exclusivity is hard-validated downstream (converter
validate_exclusivity / generator anchor warnings).

Ranking (field calibration, AMS dual-form): the proven anchor part draws
in a PRE-pass well before the body with its own form-exclusive material
ps - neither ps-sharing nor draw adjacency alone finds it. What does: its
draws bind several of the character's own textures (vs 1-2 for scene
props that merely inherit global LUT slots). Character-texture affinity
is therefore the primary key; ps-sharing and adjacency keep the pool
inclusive. More dumps per form shrink scene-prop noise via the
exclusivity intersection.

rev 12 upgrade: the affinity texture set is derived from each dump
directly - textures FRESHLY bound during the object's draws
(log_freshness evidence; falls back to all bound when no usable log) -
so stale pipeline-state inheritance on scene props no longer fakes
affinity. Callers may still inject a curated set via
char_textures_by_form.

Import contract: the module body is dependency-free (duck-typed call /
descriptor access, no enum identity) so it loads standalone in unit
tests; only load_dump_calls() needs the dump_parser modules, resolved
lazily for the two real layouts (converter vendor / velo addon) or
injected explicitly.
"""

import os

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import log_freshness

# A draw is "character-related" when its call id sits within this many calls
# of one of the object's own draws (form-exclusive parts draw back-to-back
# with the body) - the looser net besides sharing a character pixel shader.
PROXIMITY_LIMIT = 50


@dataclass
class LoadedDumpCalls:
    """Minimal loaded-dump shape the recommendation consumes; the converter's
    own LoadedDump is duck-compatible (path / calls / name)."""
    path: Path
    calls: Dict[str, object]
    skipped_files: int = 0

    @property
    def name(self) -> str:
        return Path(self.path).name


@dataclass
class AnchorCandidate:
    vb0: str
    form_id: int
    form_label: str
    shared_textures: int          # character textures bound by its draws
    shares_character_ps: bool
    min_call_distance: int
    hits: Dict[str, int] = field(default_factory=dict)


# ------------------------------------------------------------- dump load --

def _resolve_parsers(filename_parser=None, log_parser=None):
    if filename_parser is not None and log_parser is not None:
        return filename_parser, log_parser
    try:
        # converter vendor layout: _velo_vendor/{slot_textures,dump_parser}/
        from ..dump_parser import filename_parser as fp
        from ..dump_parser import log_parser as lp
    except ImportError:
        # velo addon layout: games/wuthering_waves/_wwmi_core/migoto_io/
        from ..._wwmi_core.migoto_io.dump_parser import filename_parser as fp
        from ..._wwmi_core.migoto_io.dump_parser import log_parser as lp
    return filename_parser or fp, log_parser or lp


def load_dump_calls(dump_path, filename_parser=None,
                    log_parser=None) -> LoadedDumpCalls:
    """Tolerant FrameAnalysis loader: files whose names do not parse as dump
    resources (user screenshots etc.) are skipped instead of aborting.
    Raises plain ValueError (callers wrap/localize)."""
    fp, lp = _resolve_parsers(filename_parser, log_parser)
    dump_path = Path(dump_path)
    if not (dump_path / "log.txt").is_file():
        raise ValueError(f"not a frame dump (log.txt missing): {dump_path}")
    log = lp.FrameDumpLog(dump_path)
    calls: Dict[str, object] = {}
    skipped = 0
    for filename in os.listdir(dump_path):
        resource_path = os.path.join(str(dump_path), filename)
        if not os.path.isfile(resource_path) or filename.endswith("txt"):
            continue
        try:
            descriptor = fp.ResourceDescriptor(resource_path)
        except Exception:
            skipped += 1
            continue
        if descriptor.call_id not in calls:
            calls[descriptor.call_id] = fp.CallDescriptor(descriptor.call_id)
        call = calls[descriptor.call_id]
        descriptor.call = call
        call.import_resource_descriptor(descriptor)
    for call_id, call in calls.items():
        logged = log.calls.get(call_id)
        if logged is not None:
            call.parameters = logged.parameters
    if not calls:
        raise ValueError(f"no parseable dump resources in {dump_path}")
    return LoadedDumpCalls(path=dump_path, calls=calls, skipped_files=skipped)


# --------------------------------------------------- duck-typed call view --

def _slot_kind(descriptor) -> Optional[str]:
    return getattr(descriptor.slot_type, "value", None)


def call_draw_params(call):
    """The call's DrawIndexed-shaped parameters, if any (duck-typed: no
    dependency on the CallParameters enum identity)."""
    for params in call.parameters.values():
        if hasattr(params, "StartIndexLocation"):
            return params
    return None


def call_vb0_hash(call) -> Optional[str]:
    for descriptor in call.resources.values():
        if _slot_kind(descriptor) == "vb" and descriptor.slot_id == 0:
            return descriptor.hash
    return None


def call_ib_hash(call) -> Optional[str]:
    for descriptor in call.resources.values():
        if _slot_kind(descriptor) == "ib":
            return descriptor.hash
    return None


def call_ps_key(call) -> str:
    for shader in call.shaders.values():
        raw = getattr(shader, "raw", "")
        if raw.startswith("ps="):
            return raw
    return "ps=?"


def call_ps_textures(call, max_slot: int = 8) -> List[object]:
    return [d for d in call.resources.values()
            if _slot_kind(d) == "t"
            and getattr(d.slot_shader_type, "value", None) == "ps"
            and d.hash and d.slot_id is not None and d.slot_id <= max_slot]


def object_draw_calls(loaded, object_hash: str):
    """DrawIndexed calls whose vb0 is the object hash, as (call, params)."""
    out = []
    for call in loaded.calls.values():
        params = call_draw_params(call)
        if params is None:
            continue
        if call_vb0_hash(call) == object_hash:
            out.append((call, params))
    return out


def vb0_draw_hits(loaded, vb0: str) -> int:
    return sum(1 for call in loaded.calls.values()
               if call_draw_params(call) is not None
               and call_vb0_hash(call) == vb0)


def object_texture_hashes(loaded, object_hash: str, evidence) -> Set[str]:
    """ps-t texture hashes bound during the object's draws. With log
    evidence only FRESHLY bound slots count (rev 12: stale inheritance is
    not the object's material); without evidence every binding counts."""
    out: Set[str] = set()
    for call, _ in object_draw_calls(loaded, object_hash):
        for descriptor in call_ps_textures(call):
            if evidence is None or log_freshness.slot_is_fresh(
                    evidence, call.id, descriptor.slot_id, descriptor.hash,
                    getattr(descriptor, "old_hash", None)):
                out.add(descriptor.hash)
    return out


# -------------------------------------------------------- recommendation --

def _character_ps_keys(loaded, object_hash: str) -> Set[str]:
    out = {call_ps_key(call)
           for call, _ in object_draw_calls(loaded, object_hash)}
    out.discard("ps=?")
    return out


def _draw_vb0_index(loaded):
    """vb0 -> list of (call id as int, call descriptor) for draw calls."""
    out: Dict[str, List[Tuple[int, object]]] = {}
    for call in loaded.calls.values():
        if call_draw_params(call) is None:
            continue
        vb0 = call_vb0_hash(call)
        if vb0:
            out.setdefault(vb0, []).append((int(call.id), call))
    return out


def recommend_anchors(dumps_by_form: Dict[int, List[object]],
                      form_labels: Dict[int, str],
                      object_hash: str,
                      char_textures_by_form: Optional[Dict[int, Set[str]]] = None,
                      top_n: int = 5) -> List[AnchorCandidate]:
    """vb0 hashes exclusive to one form across ALL provided dumps, restricted
    to character-related draws and ranked by character affinity (see module
    docstring). char_textures_by_form=None derives the affinity sets from
    the dumps (freshness-filtered)."""
    # Derived affinity sets are freshness-filtered on the CHARACTER side
    # only: scene inputs inherited onto the object's draws must not enter
    # the set (they would hand every prop free affinity), but candidate-
    # side bindings keep counting stale inheritance (see the scoring loop).
    if char_textures_by_form is None:
        char_textures_by_form = {}
        for form_id, dumps in dumps_by_form.items():
            tex: Set[str] = set()
            for loaded in dumps:
                path = getattr(loaded, "path", None)
                evidence = (log_freshness.parse_log_freshness(path)
                            if path else None)
                tex |= object_texture_hashes(loaded, object_hash, evidence)
            char_textures_by_form[form_id] = tex

    indexes: Dict[int, List[Tuple[object, Dict[str, List[Tuple[int, object]]]]]] = {}
    for form_id, dumps in dumps_by_form.items():
        indexes[form_id] = [(loaded, _draw_vb0_index(loaded)) for loaded in dumps]

    candidates: List[AnchorCandidate] = []
    for form_id, indexed in indexes.items():
        if not indexed:
            continue
        present = None
        for _, vb0_index in indexed:
            keys = set(vb0_index)
            present = keys if present is None else (present & keys)
        present = (present or set()) - {object_hash}
        # Exclusive: zero draw hits in every other form's dump.
        for other_id, other_indexed in indexes.items():
            if other_id == form_id:
                continue
            for _, vb0_index in other_indexed:
                present -= set(vb0_index)
        if not present:
            continue

        char_tex = char_textures_by_form.get(form_id, set())
        for vb0 in sorted(present):
            shares_ps = False
            shared_tex: Set[str] = set()
            min_distance = None
            hits: Dict[str, int] = {}
            for loaded, vb0_index in indexed:
                entries = vb0_index[vb0]
                hits[loaded.name] = len(entries)
                char_ps = _character_ps_keys(loaded, object_hash)
                obj_calls = sorted(int(c.id) for c, _ in
                                   object_draw_calls(loaded, object_hash))
                for call_id, call in entries:
                    if call_ps_key(call) in char_ps:
                        shares_ps = True
                    # Candidate-side bindings deliberately count STALE
                    # inheritance too: an anchor part draws adjacent to the
                    # character and swims in its pipeline state - inheriting
                    # many character textures is exactly what separates it
                    # from scene props (field-disproven alternative: fresh-
                    # only candidate bindings collapse the real anchors'
                    # affinity to ~1 and drop them from the list).
                    for descriptor in call_ps_textures(call):
                        if descriptor.hash in char_tex:
                            shared_tex.add(descriptor.hash)
                    if obj_calls:
                        nearest = min(abs(call_id - oc) for oc in obj_calls)
                        if min_distance is None or nearest < min_distance:
                            min_distance = nearest
            if min_distance is None:
                continue
            if shared_tex or shares_ps or min_distance <= PROXIMITY_LIMIT:
                candidates.append(AnchorCandidate(
                    vb0=vb0, form_id=form_id, form_label=form_labels[form_id],
                    shared_textures=len(shared_tex),
                    shares_character_ps=shares_ps,
                    min_call_distance=min_distance, hits=hits))

    candidates.sort(key=lambda c: (-c.shared_textures,
                                   not c.shares_character_ps,
                                   c.min_call_distance, c.vb0))
    return candidates[:top_n]
