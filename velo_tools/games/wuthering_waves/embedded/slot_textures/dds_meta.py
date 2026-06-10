# Minimal DDS header reader (pure python, no bpy).
#
# v3: also normalizes every header flavor to a canonical DXGI format NAME
# (the vocabulary of constants.DXGI_FORMAT_NAMES), because formats became the
# core matching key of the slot-style layer: combination conditions compare
# the format-family filter_index of bound slots. The same reader serves
# extraction (formats recorded into ShaderTextureUsage.json from the dump
# files) and export (legacy-schema fallback reading the model folder files).

import struct

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

# DX10 header dxgiFormat index -> canonical name (texture-relevant subset of
# the D3D spec ordering; index 1 = R32G32B32A32_TYPELESS).
from . import constants

_DXGI_BY_INDEX = {i + 1: name for i, name in enumerate(constants.DXGI_FORMAT_NAMES)}

# Legacy fourCC -> canonical DXGI name (DirectXTex-compatible mapping; the
# UNORM member stands for the family — conditions work at prefix granularity).
_FOURCC_TO_DXGI = {
    b'DXT1': 'BC1_UNORM',
    b'DXT2': 'BC2_UNORM',
    b'DXT3': 'BC2_UNORM',
    b'DXT4': 'BC3_UNORM',
    b'DXT5': 'BC3_UNORM',
    b'ATI1': 'BC4_UNORM',
    b'BC4U': 'BC4_UNORM',
    b'BC4S': 'BC4_SNORM',
    b'ATI2': 'BC5_UNORM',
    b'BC5U': 'BC5_UNORM',
    b'BC5S': 'BC5_SNORM',
    b'RGBG': 'R8G8_B8G8_UNORM',
    b'GRGB': 'G8R8_G8B8_UNORM',
}

_DDPF_ALPHAPIXELS = 0x1
_DDPF_FOURCC = 0x4
_DDPF_RGB = 0x40
_DDPF_LUMINANCE = 0x20000


def _legacy_mask_format(pf_flags: int, bits: int, masks: Tuple[int, int, int, int]) -> Optional[str]:
    """Legacy (non-fourCC) pixel format -> canonical DXGI name via bit masks.
    Covers the flavors 3DMigoto frame-analysis dumps actually write."""
    r, g, b, a = masks
    if pf_flags & _DDPF_LUMINANCE:
        if bits == 8 and r == 0xff:
            return 'R8_UNORM'
        if bits == 16 and r == 0xffff:
            return 'R16_UNORM'
        return None
    if pf_flags & _DDPF_RGB:
        if bits == 32:
            if (r, g, b) == (0x00ff0000, 0x0000ff00, 0x000000ff):
                return 'B8G8R8A8_UNORM' if a else 'B8G8R8X8_UNORM'
            if (r, g, b) == (0x000000ff, 0x0000ff00, 0x00ff0000):
                return 'R8G8B8A8_UNORM'
            if (r, g, b) == (0x3ff, 0xffc00, 0x3ff00000):
                return 'R10G10B10A2_UNORM'
        if bits == 16:
            if (r, g, b) == (0xf800, 0x07e0, 0x001f):
                return 'B5G6R5_UNORM'
            if (r, g, b, a) == (0x7c00, 0x03e0, 0x001f, 0x8000):
                return 'B5G5R5A1_UNORM'
        if bits == 8 and r == 0xff:
            return 'R8_UNORM'
    return None


@dataclass(frozen=True)
class DdsMeta:
    width: int
    height: int
    mips: int
    format: str  # canonical DXGI name, or '' when unrecognized

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
    pf_flags = struct.unpack_from('<I', head, 80)[0]
    four_cc = head[84:88]
    fmt = None
    if (pf_flags & _DDPF_FOURCC) or four_cc not in (b'\x00\x00\x00\x00',):
        if four_cc == b'DX10' and len(head) >= 132:
            dxgi = struct.unpack_from('<I', head, 128)[0]
            fmt = _DXGI_BY_INDEX.get(dxgi)
        else:
            fmt = _FOURCC_TO_DXGI.get(four_cc)
    if fmt is None:
        bits = struct.unpack_from('<I', head, 88)[0]
        masks = struct.unpack_from('<IIII', head, 92)
        fmt = _legacy_mask_format(pf_flags, bits, masks)
    return DdsMeta(width=width, height=height, mips=max(mips, 1), format=fmt or '')


def read_for_textures(textures: Iterable[Tuple[str, "Path"]]) -> Dict[str, DdsMeta]:
    """(texture hash, file path) pairs -> {hash: DdsMeta} for readable files."""
    out: Dict[str, DdsMeta] = {}
    for tex_hash, path in textures:
        meta = read_dds_meta(path)
        if meta is not None:
            out[tex_hash] = meta
    return out
