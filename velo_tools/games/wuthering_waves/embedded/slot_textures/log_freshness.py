"""Binding-freshness evidence extracted from a 3dmigoto FrameAnalysis log.txt.

Problem (ADR 0007 rev 12): frame analysis dumps EVERY bound slot for every
analysed draw call, including state inherited from earlier draws. A texture
that was never set for a draw still produces a dump file for it, and such
"stale inheritance" records poison the slot-style evidence chain (phantom
pairs / phantom same-signature conflicts). The log.txt, however, records the
actual API stream: only slots written by ``PSSetShaderResources`` under a
draw's call-id prefix are FRESH for that draw.

This module is a pure-python, dependency-free single-pass parser producing:
  * per call id: the set of ps-t slots freshly written before the draw, with
    the hash that was written (``None`` = explicitly set to null, ``''`` =
    fresh but no hash printed);
  * per call id: whether any color render target was bound at draw time
    (running OM state machine; depth-only passes have none).

Failure philosophy: every ambiguity fails toward "not fresh" (service side),
never toward evidence; unparseable / unsupported logs yield ``None`` so the
caller falls back to legacy (no-flags) behavior instead of writing wrong
flags.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

# Payload classifiers (payload = log line after the 'NNNNNN ' call-id prefix).
_PSSET_RE = re.compile(r'^PSSetShaderResources\(StartSlot:(\d+), NumViews:(\d+)')
_OMSET_RE = re.compile(r'^OMSetRenderTargets\(NumViews:(\d+)')
_OMSET_UAV_RE = re.compile(r'^OMSetRenderTargetsAndUnorderedAccessViews\(NumRTVs:(-?\d+)')
_DRAW_RE = re.compile(r'^(?:DrawIndexedInstancedIndirect|DrawInstancedIndirect|'
                      r'DrawIndexedInstanced|DrawInstanced|DrawIndexed|DrawAuto|Draw)\(')
# Indented sub-lines: '       0: view=0x... resource=0x... hash=xxxxxxxx'
# (depth views use a 'D:' prefix and are intentionally not matched here).
_SUB_SLOT_RE = re.compile(r'^\s+(\d+): ')
_SUB_HASH_RE = re.compile(r'hash=([0-9a-f]+)')
# vs constant-buffer set events: the character's bone/skin cbs live at
# vs-cb3/cb4 and so does the view cb; the anchor finder needs the FRESH
# binding (dump state at these slots is inheritance, same problem as textures).
_VSCB_RE = re.compile(r'^VSSetConstantBuffers\(StartSlot:(\d+), NumBuffers:(\d+)')
# Deferred-context execution makes prefix-scoped freshness unsound -> bail.
_BAIL_RE = re.compile(r'^(?:ExecuteCommandList|FinishCommandList)\(')


@dataclass
class CallEvidence:
    """Freshness facts for one analysed call id."""
    # slot id -> last hash freshly written under this call's prefix;
    # None = slot explicitly set to null; '' = fresh but hash unknown.
    fresh_ps_slots: Dict[int, Optional[str]] = field(default_factory=dict)
    # Color-RT state snapshotted at the call's draw line (False = depth-only).
    has_color_rt: bool = False
    drawn: bool = False


@dataclass
class LogEvidence:
    # call ids are 6-char zero-padded strings, matching dump filename prefixes
    # (ResourceDescriptor.call_id) and FrameDumpLog keying.
    calls: Dict[str, CallEvidence] = field(default_factory=dict)
    ps_event_count: int = 0


def _norm_call_id(call_id) -> str:
    return str(call_id).zfill(6)


def parse_log_freshness(dump_path) -> Optional[LogEvidence]:
    """Parse ``log.txt`` under ``dump_path`` (a dump dir or a direct file path).

    Returns ``None`` (never raises) when the log is missing/unreadable, uses a
    grammar without PSSetShaderResources events, or contains deferred-context
    execution.
    """
    try:
        path = Path(dump_path)
        if path.is_dir():
            path = path / 'log.txt'
        if not path.is_file():
            return None
        with open(path, encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except OSError:
        return None

    evidence = LogEvidence()
    calls = evidence.calls

    color_rt_count = 0      # running OM state, persists across call boundaries
    # Pending multi-line event waiting for its indented sub-lines:
    # ('ps', call_id, start, count, seen_slots) or ('om', n_color_sublines)
    pending = None

    def call_entry(call_id: str) -> CallEvidence:
        entry = calls.get(call_id)
        if entry is None:
            entry = CallEvidence()
            calls[call_id] = entry
        return entry

    def finalize_pending():
        nonlocal pending, color_rt_count
        if pending is None:
            return
        if pending[0] == 'ps':
            _, call_id, start, count, seen = pending
            slots = call_entry(call_id).fresh_ps_slots
            for slot in range(start, start + count):
                if slot not in seen:
                    slots[slot] = None  # covered by the window, no view line = null bind
        else:  # 'om'
            color_rt_count = pending[1]
        pending = None

    for line in lines:
        if line[:6].isdigit() and line[6:7] == ' ':
            finalize_pending()
            call_id = line[:6]
            payload = line[7:]

            m = _PSSET_RE.match(payload)
            if m is not None:
                evidence.ps_event_count += 1
                pending = ('ps', call_id, int(m.group(1)), int(m.group(2)), set())
                continue
            if _DRAW_RE.match(payload) is not None:
                entry = call_entry(call_id)
                entry.has_color_rt = color_rt_count > 0
                entry.drawn = True
                continue
            m = _OMSET_RE.match(payload)
            if m is not None:
                if int(m.group(1)) == 0:
                    color_rt_count = 0
                else:
                    pending = ('om', 0)  # count digit-prefixed color sub-lines
                continue
            m = _OMSET_UAV_RE.match(payload)
            if m is not None:
                num_rtvs = int(m.group(1))
                if num_rtvs == 0:
                    color_rt_count = 0
                elif num_rtvs > 0:
                    pending = ('om', 0)
                # NumRTVs:-1 = D3D11_KEEP_RENDER_TARGETS_AND_DEPTH_STENCIL
                continue
            if payload.startswith('ClearState('):
                color_rt_count = 0
                call_entry(call_id).fresh_ps_slots.clear()
                continue
            if _BAIL_RE.match(payload) is not None:
                return None
            continue

        # Indented sub-line (or non-call noise): attach to the pending event.
        if pending is None:
            continue
        m = _SUB_SLOT_RE.match(line)
        if m is None:
            continue  # 'D:' depth views, ': view=' clear lines, hex data, etc.
        if pending[0] == 'ps':
            slot = int(m.group(1))
            _, call_id, start, count, seen = pending
            if start <= slot < start + count:
                hash_m = _SUB_HASH_RE.search(line)
                call_entry(call_id).fresh_ps_slots[slot] = (
                    hash_m.group(1) if hash_m is not None else '')
                seen.add(slot)
        else:  # 'om'
            pending = ('om', pending[1] + 1)

    finalize_pending()

    if evidence.ps_event_count == 0:
        return None  # unsupported log variant: no set events -> no evidence
    return evidence


def parse_fresh_vs_cb(dump_path) -> Dict[str, Dict[int, str]]:
    """Per call id, the vs constant-buffer slots FRESHLY bound via
    ``VSSetConstantBuffers`` under that call's prefix -> ``{slot: hash}``.

    Same rationale as ``parse_log_freshness``: a frame dump records every
    inherited cb slot, so the dumped vs-cb3/cb4 of a scene prop drawn after the
    character carries the character's skin cb by inheritance. Only the log's
    API stream shows the ACTUAL fresh binding. Returns ``{}`` (never raises) on
    a missing/unreadable log so callers degrade to no cb signal.
    """
    out: Dict[str, Dict[int, str]] = {}
    try:
        path = Path(dump_path)
        if path.is_dir():
            path = path / 'log.txt'
        if not path.is_file():
            return out
        with open(path, encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
    except OSError:
        return out

    current: Optional[str] = None  # call id while inside a VSSetConstantBuffers
    for line in lines:
        if line[:6].isdigit() and line[6:7] == ' ':
            current = line[:6] if _VSCB_RE.match(line[7:]) else None
            continue
        if current is None:
            continue
        slot_m = _SUB_SLOT_RE.match(line)
        if slot_m is None:
            continue
        hash_m = _SUB_HASH_RE.search(line)
        if hash_m is not None:
            out.setdefault(current, {})[int(slot_m.group(1))] = hash_m.group(1)
    return out


def slot_is_fresh(evidence: LogEvidence, call_id, slot_id: int,
                  tex_hash: Optional[str], old_hash: Optional[str] = None) -> bool:
    """True iff ``slot_id`` was freshly written under ``call_id`` with a hash
    matching the dumped descriptor (``old_hash`` covers texture_hash=1 dumps,
    ``''`` covers hash-less log variants). Any mismatch or absence -> False."""
    call = evidence.calls.get(_norm_call_id(call_id))
    if call is None:
        return False
    value = call.fresh_ps_slots.get(slot_id, False)
    if value is False or value is None:
        return False
    if value == '':
        return True
    return value == tex_hash or (old_hash is not None and value == old_hash)


def call_has_color_rt(evidence: LogEvidence, call_id) -> Optional[bool]:
    """Color-RT state at the call's draw; None when the call was never seen
    drawing in the log."""
    call = evidence.calls.get(_norm_call_id(call_id))
    if call is None or not call.drawn:
        return None
    return call.has_color_rt


def find_dump_root(start_path) -> Optional[Path]:
    """Walk up from a dump resource file to the directory holding log.txt
    (dump root); descriptors may live in the root or a 'deduped' subfolder."""
    try:
        node = Path(start_path)
        if node.is_file():
            node = node.parent
        for _ in range(3):
            if (node / 'log.txt').is_file():
                return node
            if node.parent == node:
                break
            node = node.parent
    except OSError:
        pass
    return None
