# Minimal DDS header reader (pure python, no bpy).
#
# Slot-style export reads texture descriptors LIVE from the files present in
# the object source folder (user decision: no need to pre-capture formats into
# the usage json — deleted textures are exactly the ones that never need a
# descriptor). Used as a belt on top of the slot-set material fingerprint.

import struct

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple


@dataclass(frozen=True)
class DdsMeta:
    width: int
    height: int
    mips: int
    format: str  # fourCC string or "DXGI<n>" for DX10 headers

    @property
    def is_square(self) -> bool:
        # NOTE: frame-dump extracted .dds files carry only the base mip level
        # (no mip chain), so a mips>1 requirement would demote every real
        # character texture - squareness is the only reliable belt here.
        return self.width == self.height


def read_dds_meta(path) -> Optional[DdsMeta]:
    try:
        with open(path, 'rb') as f:
            head = f.read(148)
    except OSError:
        return None
    # 'DDS ' magic + 124-byte DDS_HEADER.
    if len(head) < 128 or head[:4] != b'DDS ':
        return None
    height, width = struct.unpack_from('<II', head, 12)
    mips = struct.unpack_from('<I', head, 28)[0]
    four_cc = head[84:88]
    if four_cc == b'DX10' and len(head) >= 132:
        dxgi = struct.unpack_from('<I', head, 128)[0]
        fmt = f'DXGI{dxgi}'
    else:
        fmt = four_cc.decode('ascii', errors='replace').strip('\x00').strip() or 'RGB'
    return DdsMeta(width=width, height=height, mips=max(mips, 1), format=fmt)


def read_for_textures(textures: Iterable[Tuple[str, "Path"]]) -> Dict[str, DdsMeta]:
    """(texture hash, file path) pairs -> {hash: DdsMeta} for readable files."""
    out: Dict[str, DdsMeta] = {}
    for tex_hash, path in textures:
        meta = read_dds_meta(path)
        if meta is not None:
            out[tex_hash] = meta
    return out
