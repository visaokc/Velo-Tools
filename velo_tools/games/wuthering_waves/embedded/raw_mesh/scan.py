"""Pure dump scanning + hash resolution for the Velo raw-mesh tool.

No bpy import; headless-testable. Indexes every DrawIndexed draw call in a
frame dump by buffer hash and resolves user-supplied IB/VB hashes to a flat,
de-duplicated, input-ordered list of components.

Granularity rule (hash type controls it):
  - a VB hash -> its VB0 -> ALL draw calls sharing that VB0 become components
                 (character-style auto-split of a multi-component object).
  - an IB hash -> only the draw calls that bind THAT index buffer (one
                 component for a single-component mesh; the matching components
                 of a multi-component object).
  - a non-shared VB0 -> one component.

Real dumps reuse buffers heavily: a sprite/quad is one geometry drawn many
times (same vb0+ib+draw-range, different transform/texture). Such repeats are
collapsed by the draw signature (vb0, ib, StartIndexLocation, IndexCount,
BaseVertexLocation) so an instanced quad yields ONE component, not N.

A hash that resolves to more than one VB0 object ambiguously is reported as an
error listing the candidates. Reuses _wwmi_core dump model read-only.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..._wwmi_core.migoto_io.dump_parser.dump_parser import Dump
from ..._wwmi_core.migoto_io.dump_parser.filename_parser import SlotType, ShaderType
from ..._wwmi_core.migoto_io.dump_parser.log_parser import CallParameters


class RawMeshScanError(Exception):
    """User-facing error during dump scan / hash resolution."""
    pass


def _fmt_candidates(ids, limit=8):
    ids = list(ids)
    if len(ids) <= limit:
        return ', '.join(ids)
    return ', '.join(ids[:limit]) + f', ... and {len(ids) - limit} more'


@dataclass
class DrawCallInfo:
    """A single indexed draw call's geometry resources (one mesh draw)."""
    call_id: str
    ib_hash: str
    ib_path: str
    vb_slots: Dict[int, str]              # slot_id -> hash
    vb_paths: Dict[int, str]             # slot_id -> .buf path
    index_count: int
    start_index: int
    base_vertex: int
    textures: list = field(default_factory=list)   # ps-tN ResourceDescriptors

    @property
    def vb0_hash(self) -> Optional[str]:
        return self.vb_slots.get(0)

    @property
    def signature(self) -> Tuple:
        """Identity of the geometry drawn (collapses instanced repeats)."""
        return (self.vb0_hash, self.ib_hash, self.start_index,
                self.index_count, self.base_vertex)


@dataclass
class DumpIndex:
    calls: Dict[str, DrawCallInfo]                 # call_id -> info
    by_hash: Dict[str, List[Tuple[str, str, Optional[int]]]]  # hash -> [(call_id, 'ib'|'vb', slot_id)]
    calls_by_vb0: Dict[str, List[str]]             # vb0_hash -> [call_id, ...] (draw order)


@dataclass
class ResolvedComponent:
    """One unique component to extract, with everything the builder needs."""
    call_id: str                  # representative draw call
    vb0_hash: str
    ib_hash: str
    ib_path: str
    vb_paths: Dict[int, str]      # slot_id -> .buf path
    index_count: int
    start_index: int
    base_vertex: int
    textures: list
    token: str                    # the input token that produced it


def build_index(dump: Dump) -> DumpIndex:
    """Index every DrawIndexed draw call by IB/VB hash.

    Calls without an index buffer or without a slot-0 vertex buffer are not
    geometry draws we can extract, so they are skipped.
    """
    calls: Dict[str, DrawCallInfo] = {}
    by_hash: Dict[str, List[Tuple[str, str, Optional[int]]]] = {}
    calls_by_vb0: Dict[str, List[str]] = {}

    for call_id, call in dump.calls.items():
        draw = call.parameters.get(CallParameters.DrawIndexed)
        if draw is None:
            continue

        ib_hash = ib_path = None
        vb_slots: Dict[int, str] = {}
        vb_paths: Dict[int, str] = {}
        textures = []

        for desc in call.resources.values():
            st = desc.slot_type
            if st == SlotType.IndexBuffer:
                ib_hash = desc.hash
                ib_path = desc.path
            elif st == SlotType.VertexBuffer:
                vb_slots[desc.slot_id] = desc.hash
                vb_paths[desc.slot_id] = desc.path
            elif st == SlotType.Texture and desc.slot_shader_type == ShaderType.Pixel:
                textures.append(desc)

        if ib_hash is None or 0 not in vb_slots:
            continue

        info = DrawCallInfo(
            call_id=call_id, ib_hash=ib_hash, ib_path=ib_path,
            vb_slots=vb_slots, vb_paths=vb_paths,
            index_count=draw.IndexCount, start_index=draw.StartIndexLocation,
            base_vertex=draw.BaseVertexLocation, textures=textures,
        )
        calls[call_id] = info

        by_hash.setdefault(ib_hash, []).append((call_id, 'ib', None))
        for slot_id, h in vb_slots.items():
            by_hash.setdefault(h, []).append((call_id, 'vb', slot_id))
        calls_by_vb0.setdefault(info.vb0_hash, []).append(call_id)

    return DumpIndex(calls=calls, by_hash=by_hash, calls_by_vb0=calls_by_vb0)


def _parse_token(token: str) -> Tuple[Optional[str], str]:
    """Split a user token into (intended_kind, hash).

    intended_kind is 'ib', 'vb', or None (infer from the index). Tolerates an
    optional "slot=" prefix and a trailing "(oldhash)" (3dmigoto texture_hash=1).
    """
    token = token.strip()
    if '=' in token:
        slotspec, _, h = token.partition('=')
        slotspec = slotspec.strip().lower()
        h = h.strip().lower().split('(')[0]
        if slotspec.startswith('ib'):
            return 'ib', h
        if slotspec.startswith('vb'):
            return 'vb', h
        return None, h
    return None, token.strip().lower().split('(')[0]


def _component_calls(index: DumpIndex, token: str, intended: Optional[str], h: str) -> List[str]:
    """Return the draw-call ids that make up the unit for one token's hash."""
    hits = index.by_hash.get(h)
    if not hits:
        raise RawMeshScanError(
            f'Hash "{h}" (from "{token}") was not found as an IB or VB in any draw '
            f'call of the dump.')

    ib_calls = sorted({c for (c, st, _s) in hits if st == 'ib'})
    vb_calls = sorted({c for (c, st, _s) in hits if st == 'vb'})

    if intended is None and ib_calls and vb_calls:
        raise RawMeshScanError(
            f'Hash "{h}" (from "{token}") is bound as BOTH an IB and a VB in the dump; '
            f'prefix it with "ib=" or "vb0=" to disambiguate.')

    if (intended == 'ib') or (intended is None and ib_calls):
        if not ib_calls:
            raise RawMeshScanError(f'Hash "{h}" (from "{token}") is not an index buffer in the dump.')
        vb0s = {index.calls[c].vb0_hash for c in ib_calls}
        if len(vb0s) > 1:
            raise RawMeshScanError(
                f'IB hash "{h}" (from "{token}") is shared across {len(vb0s)} different '
                f'VB0 objects ({_fmt_candidates(sorted(vb0s))}) - common for sprite/quad '
                f'index buffers. Use a VB0 hash to pick the specific mesh.')
        return ib_calls  # only the draws that bind this IB (one component, or its repeats)

    if not vb_calls:
        raise RawMeshScanError(f'Hash "{h}" (from "{token}") is not a vertex buffer in the dump.')
    vb0s = {index.calls[c].vb0_hash for c in vb_calls}
    if len(vb0s) > 1:
        raise RawMeshScanError(
            f'VB hash "{h}" (from "{token}") is shared across {len(vb0s)} different VB0 '
            f'objects ({_fmt_candidates(sorted(vb0s))}); use the specific VB0 hash or an '
            f'IB hash to pick one.')
    vb0_hash = next(iter(vb0s))
    return list(index.calls_by_vb0.get(vb0_hash, []))  # whole object, draw order


def resolve_hashes(index: DumpIndex, raw_text: str) -> List[ResolvedComponent]:
    """Resolve a comma/newline-separated hash list to ordered unique components.

    Components are de-duplicated globally by draw signature (collapsing
    instanced repeats and overlapping inputs), preserving input order.
    """
    tokens = [t for t in (s.strip() for s in raw_text.replace('\n', ',').split(',')) if t]
    if not tokens:
        raise RawMeshScanError('No hashes provided.')

    components: List[ResolvedComponent] = []
    seen = set()

    for token in tokens:
        intended, h = _parse_token(token)
        if not h:
            continue
        for call_id in _component_calls(index, token, intended, h):
            info = index.calls[call_id]
            sig = info.signature
            if sig in seen:
                continue
            seen.add(sig)
            components.append(ResolvedComponent(
                call_id=call_id, vb0_hash=info.vb0_hash, ib_hash=info.ib_hash,
                ib_path=info.ib_path, vb_paths=dict(info.vb_paths),
                index_count=info.index_count, start_index=info.start_index,
                base_vertex=info.base_vertex, textures=list(info.textures), token=token,
            ))

    if not components:
        raise RawMeshScanError(
            'All provided hashes resolved to already-included draw calls; nothing new '
            'to extract.')

    return components
