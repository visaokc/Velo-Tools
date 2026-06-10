# Game-level constants for the WWMI slot-style texture layer.
#
# Everything in this file is WUTHERING WAVES PIPELINE knowledge (same nature as
# WWMI core's own 3381.7777 bone-CB filter_index) and is shared by every mod
# exported with the slot-style option. Character-level data (PS hashes, slot
# maps, sentinel texture hashes, form sets) is NEVER hardcoded — it is derived
# from ShaderTextureUsage(.Forms).json at export time by generator.py.
#
# filter_index namespace (ADR 0006). All values are integers exactly
# representable in float32 (< 16,777,216) and deterministic, so any two mods
# independently derive the SAME value for the same shader/texture and duplicate
# marker sections coexist without conflicts:
#
#   1,999,801                      structural family tag (ShaderRegex, fixed)
#   [2,000,000 .. 15,999,981)      per-PS tags:      2,000,000 + hash64 % 13,999,981
#   [16,200,000 .. 16,699,979)     sentinel marks:  16,200,000 + hash32 % 499,979
#
# Known co-tenants kept clear of: WWMI core 3381.7777 (bone CB), hand-made mod
# values around 3381.x, RabbitFX 1718.x / 1719.x. Competition with NON-velo
# tools tagging the same shaders with different values is an ecosystem-wide
# limitation (ShaderOverride filter_index is global per shader) — velo cannot
# fix that, only avoid clashing values among its own exports.

SECTION_PREFIX = "VeloSlot"

FAMILY_TAG_VALUE = 1999801

_PS_TAG_BASE = 2000000
_PS_TAG_MOD = 13999981  # prime

_SENTINEL_BASE = 16200000
_SENTINEL_MOD = 499979  # prime


def ps_tag_value(ps_hash: str) -> int:
    """Deterministic filter_index for a pixel shader hash (16 hex chars)."""
    return _PS_TAG_BASE + int(ps_hash, 16) % _PS_TAG_MOD


def sentinel_value(texture_hash: str) -> int:
    """Deterministic filter_index for a sentinel texture hash (8 hex chars)."""
    return _SENTINEL_BASE + int(texture_hash, 16) % _SENTINEL_MOD


# Structural classification of the character material shader family by DXBC
# shape (RabbitFX-proven approach, match-only ShaderRegex without a Replace
# block). Catches material-pass variants that exist in no dump (e.g. the
# ~0.5s menu-open transition pipeline), so they need no hash enumeration.
# May go stale on a big game update that reshapes the material shaders; the
# per-PS tags keep working regardless (they outrank ShaderRegex), only the
# unknown-variant fallback coverage degrades.
FAMILY_REGEX_SECTIONS = """\
[ShaderRegex{prefix}MaterialGBuffer]
shader_model = ps_4_0 ps_5_0
filter_index = {family}

[ShaderRegex{prefix}MaterialGBuffer.Pattern]
dcl_constantbuffer\\hCB4\\[\\d{{3}}\\].*\\n(?:dcl.+\\n)*?(?:dcl_output\\ho\\d\\.xyzw\\n){{7}}

[ShaderRegex{prefix}MaterialForward]
shader_model = ps_4_0 ps_5_0
filter_index = {family}

[ShaderRegex{prefix}MaterialForward.Pattern]
dcl_constantbuffer\\hCB6\\[\\d+\\].*\\n(?:dcl.+\\n)*?dcl_output\\ho0\\.xyzw\\n(?!dcl_output)
""".format(prefix=SECTION_PREFIX, family=FAMILY_TAG_VALUE)

# Slots considered "main material textures" when classifying pairs. A pair is
# material-class when >= MATERIAL_MIN_MODDED of its modded slots sit in
# MAIN_SLOTS (face pairs mod only t1, screen-space pairs only t0, outline
# pairs only t7 — none qualify).
MAIN_SLOTS = (0, 1, 2, 3)
MATERIAL_MIN_MODDED = 2

# Slot used by the negative sentinel guard of the structural fallback branch
# (screen-space / outline / face binding families pin distinctive non-modded
# textures there, verified per-draw in the 2026-06-10 transition hold-log).
SENTINEL_SLOT = 2

# Cap the number of sentinel guards per fallback condition to keep the emitted
# ini condition readable; most frequent sentinels win.
MAX_SENTINELS = 6

# Sidecar filename written by form_merge.py next to Metadata.json.
FORMS_SIDECAR_FILENAME = "ShaderTextureUsageForms.json"

# Base per-pair maps produced by the extraction patch (_shader_texture_usage).
BASE_USAGE_FILENAME = "ShaderTextureUsage.json"
