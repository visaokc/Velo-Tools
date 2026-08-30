"""Bone signature deduplication helpers for skinned mesh components.

Algorithm derived from ssice-a/3dmigoto_bone_merge v0.3.0
(see project README "Algorithm attribution" section).

The three lower-level helpers (``_read_three_rows_from_blob``,
``_build_bone_signature_from_blob``) are ports of the upstream functions.

``_read_palette_bases_from_instance_config`` is adapted to take an explicit
``first_constant`` argument because 3DMigoto dumps the entire constant
buffer to disk while the shader sees a sub-window starting at
``first_constant``. The instance-config register c5 is therefore at byte offset
``(first_constant + 5) * 16`` inside the dumped ``.buf`` file, instead of
the bare ``5 * 16`` used by ssice-a's pre-sliced dumper.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Dict, Tuple


GLOBAL_RESERVED_ROWS = 3


_VS_CB1_HEADER_RE = re.compile(
    r"^(?P<call>\d+)\s+VSSetConstantBuffers1\(StartSlot:(?P<start>\d+),"
)
_CB_INFO_RE = re.compile(
    r"^\s*(?P<slot>\d+):\s+resource=\S+\s+hash=[0-9a-fA-F]+\s+first_constant=(?P<first>\d+)\s+num_constants=(?P<count>\d+)\s*$"
)


_log_first_constant_cache: Dict[Tuple[str, int, int], Dict[Tuple[int, int], int]] = {}


def parse_vs_cb_first_constants(log_path: str) -> Dict[Tuple[int, int], int]:
    """Scan a 3DMigoto frame dump ``log.txt`` and return a mapping of
    ``(call_id_int, slot_int) -> first_constant`` for ``VSSetConstantBuffers1``
    bindings.

    Later bindings within the same draw call overwrite earlier ones.
    """
    path = Path(log_path)
    if not path.is_file():
        return {}
    stat = path.stat()
    cache_key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))
    cached = _log_first_constant_cache.get(cache_key)
    if cached is not None:
        return cached

    result: Dict[Tuple[int, int], int] = {}
    pending_call = None
    with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip("\n")
            header_match = _VS_CB1_HEADER_RE.match(line)
            if header_match:
                pending_call = int(header_match.group("call"))
                continue
            if pending_call is None:
                continue
            info_match = _CB_INFO_RE.match(line)
            if info_match:
                slot_id = int(info_match.group("slot"))
                first_constant = int(info_match.group("first"))
                result[(pending_call, slot_id)] = first_constant
                continue
            if line and not line.startswith(" "):
                pending_call = None

    _log_first_constant_cache[cache_key] = result
    return result


def _read_palette_bases_from_instance_config(
    instance_config_path: str,
    first_constant: int,
) -> Tuple[int, int]:
    """Read ``(current_palette_base, previous_palette_base)`` from instance config
    register c5, accounting for ``first_constant`` provided by the
    ``VSSetConstantBuffers1`` call.
    """
    byte_offset = (int(first_constant) + 5) * 16
    with open(instance_config_path, "rb") as fh:
        fh.seek(byte_offset)
        row_bytes = fh.read(16)
    if len(row_bytes) != 16:
        raise ValueError(
            f"instance-config buffer too small at offset {byte_offset}: {instance_config_path}"
        )
    x_value, y_value, _z_value, _w_value = struct.unpack("<4I", row_bytes)
    return x_value, y_value


def _build_bone_signature_from_blob(
    vs_t0_blob: bytes,
    total_rows: int,
    current_base: int,
    previous_base: int,
    local_bone: int,
) -> bytes:
    current_row = current_base + GLOBAL_RESERVED_ROWS + local_bone * 3
    previous_row = previous_base + GLOBAL_RESERVED_ROWS + local_bone * 3
    current_blob = _read_three_rows_from_blob(vs_t0_blob, current_row, total_rows)
    previous_blob = _read_three_rows_from_blob(vs_t0_blob, previous_row, total_rows)
    return current_blob + previous_blob


def _read_three_rows_from_blob(vs_t0_blob: bytes, row_index: int, total_rows: int) -> bytes:
    total_rows = int(total_rows)
    row_index = int(row_index)
    if total_rows <= 0 or len(vs_t0_blob) != total_rows * 16:
        raise ValueError("vs-t0 buffer size does not match its declared row count")
    if row_index < 0 or row_index + 2 >= total_rows:
        raise ValueError(
            f"vs-t0 bone rows {row_index}..{row_index + 2} exceed row range 0..{total_rows - 1}"
        )
    blobs = []
    for row_offset in range(3):
        source_row = row_index + row_offset
        byte_offset = source_row * 16
        row_blob = vs_t0_blob[byte_offset : byte_offset + 16]
        if len(row_blob) != 16:
            raise ValueError(f"vs-t0 buffer too small for row {source_row}")
        blobs.append(row_blob)
    return b"".join(blobs)
