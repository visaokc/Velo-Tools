"""Form-anchor candidate recommendation (shared core, velo-owned; vendored
into WWMISlotStyleConverter by its sync_vendor at build time).

Field rules (ADR 0007 rev 5/6, field-proven): this WWMI 3dmigoto fork
matches draws by VERTEX BUFFER hash only - an ib hash never fires - so
candidates are vb0 hashes exclusively (never shaders, never scene props).
Anchors stay USER-SPECIFIED: this module only puts ranked candidates on
the table; exclusivity is hard-validated downstream (converter
validate_exclusivity / generator anchor warnings).

Ranking (ADR 0007 rev 13, AMS dual-form, field-proven): the ONLY reliable
"real character part vs scene/UI noise" signal is the vertex SKINNING
constant buffer. The body binds its bone-matrix / skin-param cbs at
vs-cb3/cb4 and also feeds them to the compute-shader skinning pass; the
view/camera cb sits at the same vs slots but never enters the CS. So a
candidate is a confirmed character part iff it FRESHLY binds (the log's
API stream, not the inheritance-polluted dump state) one of the body's
vs-cb3/cb4 hashes that is ALSO a CS input. A candidate that instead
freshly binds the body's CS-less view cb at those slots is screen-space
(UI) and is dropped. Texture affinity / ps-sharing / draw adjacency were
all field-disproven as primary signals (they rank merged-in body parts and
scene props above the real form-exclusive accessories) and survive only as
the candidate-pool gate and human-readable hints.

recommend_anchors() returns ONLY cb-confirmed parts (user-locked): an
incidental effect binding neither a skin cb nor the view cb is left for
manual specification, not padded into the list as unverifiable noise. The
display-only affinity set is still freshness-filtered per dump
(log_freshness evidence) and may be injected via char_textures_by_form.

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
    # Display-only signals (not part of the ranking key): let a human tell a
    # real rigged character part from an incidental buffer at a glance.
    skinned: bool = False         # binds a vertex buffer beyond vb0 (rigged
                                  # part, not a static scene prop)
    index_count: int = 0          # largest DrawIndexed IndexCount over its
                                  # draws - a mesh-size proxy
    character_cb: bool = False    # freshly binds one of the body's vs-cb3/cb4
                                  # SKINNING cbs (a CS-fed bone/skin cb): a
                                  # confirmed rigged character part
    screen_space: bool = False    # freshly binds the body's CS-less view cb at
                                  # a skinning slot and no skin cb: UI / screen-
                                  # space, excluded from recommendations
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


def call_is_skinned(call) -> bool:
    """True when the draw binds any vertex buffer beyond vb0 (slot >= 1): a
    rigged character part. Static scene props bind vb0 only. Mirrors the inline
    test in detect_object_hash."""
    for descriptor in call.resources.values():
        if _slot_kind(descriptor) == "vb" and (descriptor.slot_id or 0) >= 1:
            return True
    return False


def call_cs_cb_hashes(call):
    """cb hashes this call binds to the COMPUTE shader. The CS skinning pass
    consumes the character's bone/skin cbs; the view/camera cb (which shares the
    same vs-cb3/cb4 slots) never feeds a CS. Intersecting the body's vs-cb3/cb4
    signature with the frame's CS cbs is what separates a real skinning cb from
    the global view cb - the basis of the character_cb / screen_space split."""
    out = set()
    for d in call.resources.values():
        if (_slot_kind(d) == "cb"
                and getattr(d.slot_shader_type, "value", None) == "cs"
                and d.hash):
            out.add(d.hash)
    return out


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


@dataclass
class ObjectCandidate:
    """One character-object candidate from dump auto-detection."""
    vb0: str
    components: int      # min across dumps of distinct StartIndexLocation count
    draws: int           # total draw count across dumps
    skinned: bool        # some draw binds vertex buffers beyond vb0


def detect_object_hash(loaded_dumps: List[object]) -> List[ObjectCandidate]:
    """Character-object auto-detection from RAW dumps alone (no extraction
    folder anywhere): the character is present in EVERY dump (forms swap
    outfits, not the person), skinned (binds vertex buffers beyond vb0 -
    scene props don't), and multi-component (several distinct
    StartIndexLocation draws on one vb0). Candidates are ranked by
    (components desc, draws desc); empty list = nothing plausible."""
    per_dump: List[Dict[str, dict]] = []
    for loaded in loaded_dumps:
        stats: Dict[str, dict] = {}
        for call in loaded.calls.values():
            params = call_draw_params(call)
            if params is None:
                continue
            vb0 = call_vb0_hash(call)
            if not vb0:
                continue
            entry = stats.setdefault(vb0, {"starts": set(), "draws": 0,
                                           "skinned": False})
            entry["starts"].add(params.StartIndexLocation)
            entry["draws"] += 1
            if not entry["skinned"]:
                for descriptor in call.resources.values():
                    if (_slot_kind(descriptor) == "vb"
                            and (descriptor.slot_id or 0) >= 1):
                        entry["skinned"] = True
                        break
        per_dump.append(stats)
    if not per_dump:
        return []

    present = set(per_dump[0])
    for stats in per_dump[1:]:
        present &= set(stats)

    candidates: List[ObjectCandidate] = []
    for vb0 in sorted(present):
        entries = [stats[vb0] for stats in per_dump]
        skinned = any(e["skinned"] for e in entries)
        if not skinned:
            continue  # static scene prop
        candidates.append(ObjectCandidate(
            vb0=vb0,
            components=min(len(e["starts"]) for e in entries),
            draws=sum(e["draws"] for e in entries),
            skinned=skinned))
    candidates.sort(key=lambda c: (-c.components, -c.draws, c.vb0))
    return candidates


def resolve_form_rows(rows: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Normalizes the anchor finder's extra-form dump rows (UI order):
    rows = [(dump_folder, form_label), ...]. Blank rows are skipped, blank
    labels are numbered form2/form3/... by surviving row order. Returns
    [(label, folder)]; duplicate labels, the reserved label 'base' or zero
    usable rows raise ValueError with an actionable message. Folder
    validity (log.txt) is load_dump_calls' job, not checked here."""
    out: List[Tuple[str, str]] = []
    seen: Set[str] = set()
    for folder, label in rows:
        folder = (folder or "").strip()
        if not folder:
            continue
        label = (label or "").strip() or f"form{len(out) + 2}"
        key = label.lower()
        if key == "base":
            raise ValueError(
                "form label 'base' is the base form itself - give the extra "
                "form another label")
        if key in seen:
            raise ValueError(f"duplicate form label '{label}' - every extra "
                             f"form dump needs its own label")
        seen.add(key)
        out.append((label, folder))
    if not out:
        raise ValueError("no extra-form dump specified - fill at least one "
                         "form dump row")
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
    """Form-exclusive vb0 hashes confirmed as real character parts by the
    skinning-cb signal (module docstring), one form at a time. Returns ONLY
    cb-confirmed parts; char_textures_by_form=None derives the display-only
    affinity sets from the dumps with freshness filtering."""
    # Display-only affinity set, freshness-filtered on the CHARACTER side only
    # (scene inputs inherited onto the object's draws must not enter the set);
    # candidate-side bindings still count stale inheritance (see scoring loop).
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

    # Skinning-cb signal inputs: FRESH vs-cb bindings per dump (dump state is
    # inheritance-polluted) and the set of cbs ever fed to a compute shader
    # (the CS skinning pass) - this is what tells a bone/skin cb from the view
    # cb that shares the same vs-cb3/cb4 slots.
    fresh_cb_by_dump: Dict[int, Dict[str, Dict[int, str]]] = {}
    cs_cbs: Set[str] = set()
    for form_id, dumps in dumps_by_form.items():
        for loaded in dumps:
            path = getattr(loaded, "path", None)
            fresh_cb_by_dump[id(loaded)] = (
                log_freshness.parse_fresh_vs_cb(path) if path else {})
            for call in loaded.calls.values():
                cs_cbs |= call_cs_cb_hashes(call)

    def _fresh_cb34(loaded, call) -> Set[str]:
        slots = fresh_cb_by_dump.get(id(loaded), {}).get(
            str(call.id).zfill(6), {})
        return {slots[s] for s in (3, 4) if s in slots}

    candidates: List[AnchorCandidate] = []
    signal_ok = False  # any form yielded a usable (non-empty) skin-cb signature
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
        # The body's own FRESH vs-cb3/cb4 signature, split by CS membership:
        # skin_cbs feed the compute skinning pass (bone matrices / skin params),
        # camera_cbs are the view/global cb at those slots that never does.
        body_sig: Set[str] = set()
        for loaded, _bi in indexed:
            for call, _ in object_draw_calls(loaded, object_hash):
                body_sig |= _fresh_cb34(loaded, call)
        skin_cbs = body_sig & cs_cbs
        camera_cbs = body_sig - cs_cbs
        signal_ok = signal_ok or bool(skin_cbs)

        for vb0 in sorted(present):
            shares_ps = False
            shared_tex: Set[str] = set()
            min_distance = None
            skinned = False
            index_count = 0
            cand_cb: Set[str] = set()
            hits: Dict[str, int] = {}
            for loaded, vb0_index in indexed:
                entries = vb0_index[vb0]
                hits[loaded.name] = len(entries)
                char_ps = _character_ps_keys(loaded, object_hash)
                obj_calls = sorted(int(c.id) for c, _ in
                                   object_draw_calls(loaded, object_hash))
                for call_id, call in entries:
                    if call_is_skinned(call):
                        skinned = True
                    cand_cb |= _fresh_cb34(loaded, call)
                    params = call_draw_params(call)
                    if params is not None and params.IndexCount > index_count:
                        index_count = params.IndexCount
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
            # FRESH (not dump-inherited) skinning-cb test, the only reliable
            # signal: shares a CS-fed skin cb -> confirmed character part;
            # freshly binds the CS-less view cb at a skinning slot instead ->
            # screen-space / UI.
            character_cb = bool(cand_cb & skin_cbs)
            # screen_space only means something when the skin signal exists;
            # without it camera_cbs degenerates to the whole body signature and
            # would wrongly tag real parts (the fallback path stays inclusive).
            screen_space = (bool(skin_cbs) and not character_cb
                            and bool(cand_cb & camera_cbs))
            if (character_cb or skinned or shared_tex or shares_ps
                    or min_distance <= PROXIMITY_LIMIT):
                candidates.append(AnchorCandidate(
                    vb0=vb0, form_id=form_id, form_label=form_labels[form_id],
                    shared_textures=len(shared_tex),
                    shares_character_ps=shares_ps,
                    min_call_distance=min_distance,
                    skinned=skinned, index_count=index_count,
                    character_cb=character_cb, screen_space=screen_space,
                    hits=hits))

    # User-locked policy: surface ONLY cb-confirmed character parts. When the
    # signal worked but nothing qualifies, recommend nothing (effects/UI are
    # specified by hand) rather than pad the list with noise. Only when the
    # signal is underivable (no CS skinning cb anywhere - other engine / partial
    # dump) fall back to the prior skinned ranking, minus screen-space.
    confirmed = [c for c in candidates if c.character_cb]
    if confirmed:
        chosen = confirmed
    elif signal_ok:
        chosen = []
    else:
        chosen = [c for c in candidates if not c.screen_space]
    chosen.sort(key=lambda c: (not c.character_cb,
                               not c.skinned,
                               -c.index_count,
                               c.min_call_distance, c.vb0))
    return chosen[:top_n]
