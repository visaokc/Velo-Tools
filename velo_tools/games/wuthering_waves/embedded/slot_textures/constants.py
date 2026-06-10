# Game-level constants for the WWMI slot-style texture layer.
#
# Everything in this file is WUTHERING WAVES PIPELINE knowledge (same nature as
# WWMI core's own 3381.7777 bone-CB filter_index) and is shared by every mod
# exported with the slot-style option. Character-level data (PS hashes, slot
# maps, sentinel texture hashes, form sets) is NEVER hardcoded — it is derived
# from ShaderTextureUsage.json (+ its extra_forms key) at export time by
# generator.py, plus DDS descriptors read live from the source folder files.
#
# Generated section/variable names are intentionally brand-free (project rule
# since the LOD round: descriptive names only inside emitted mod.ini content).
# Both section names and ini variables are namespaced per ini file by 3DMigoto,
# so generic names cannot collide across mods.
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

# ----------------------------------------------------- emitted names --------

VAR_FORM = "$form_id"
CMDLIST_SET_TEXTURES = "CommandListSetTexturesComponent{component_id}"
CMDLIST_RESTORE = "CommandListRestoreTextures"
CMDLIST_DETECT_FORM = "CommandListDetectForm"
RES_BACKUP = "ResourceTextureBackupT{slot}"
SEC_PS_MARK = "ShaderOverrideMarkPs{ps_hash}"
SEC_TEX_MARK = "TextureOverrideMarkTexture{texture_hash}"
SEC_REGEX_GBUFFER = "ShaderRegexMaterialGBuffer"
SEC_REGEX_FORWARD = "ShaderRegexMaterialForward"

# ----------------------------------------------------- filter values --------

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
# block). Catches material-pass variants that exist in no dump — its hit on
# the menu-open transition pipeline is field-proven by the hand-converted
# reference mod. May go stale on a big game update that reshapes the material
# shaders; the per-PS tags keep working regardless (they outrank ShaderRegex),
# only the unknown-variant fallback coverage degrades.
FAMILY_REGEX_SECTIONS = """\
[{gbuffer}]
shader_model = ps_4_0 ps_5_0
filter_index = {family}

[{gbuffer}.Pattern]
dcl_constantbuffer\\hCB4\\[\\d{{3}}\\].*\\n(?:dcl.+\\n)*?(?:dcl_output\\ho\\d\\.xyzw\\n){{7}}

[{forward}]
shader_model = ps_4_0 ps_5_0
filter_index = {family}

[{forward}.Pattern]
dcl_constantbuffer\\hCB6\\[\\d+\\].*\\n(?:dcl.+\\n)*?dcl_output\\ho0\\.xyzw\\n(?!dcl_output)
""".format(gbuffer=SEC_REGEX_GBUFFER, forward=SEC_REGEX_FORWARD, family=FAMILY_TAG_VALUE)

# --------------------------------------------------- classification ---------

# A pair is material-class when its slot map carries ALL of MAIN_SLOTS — a
# structural slot-set fingerprint, independent of which textures the author
# kept in the folder (the membership-based rule broke under unpruned texture
# sets: screen-space/face/outline pairs became "material" and emptied the
# safe-intersection fallback maps). Verified across both AMS form dumps:
# true material pairs always bind t0..t3; screen-space family never binds t1;
# face/outline families never bind t0.
MAIN_SLOTS = (0, 1, 2, 3)

# Optional belt on top of the slot-set fingerprint: when the DDS descriptor of
# a MAIN_SLOTS texture is known (file present in the source folder), material
# pairs must look like character textures (square; dump-extracted files carry
# only the base mip level, so a mip-count requirement is NOT usable). Unknown
# descriptors never block.
MATERIAL_REQUIRE_SQUARE = True

# Slot used by the negative sentinel guard of the structural fallback branch
# (screen-space / outline / face binding families pin distinctive textures
# there, verified per-draw in the 2026-06-10 transition hold-log).
SENTINEL_SLOT = 2

# Cap the number of sentinel guards per fallback condition to keep the emitted
# ini condition readable; most frequent sentinels win.
MAX_SENTINELS = 6

# Single data file: base form maps live in the top-level "Component N" keys,
# extra forms under the reserved top-level key below (preserved across
# re-extraction by the _shader_texture_usage patch).
BASE_USAGE_FILENAME = "ShaderTextureUsage.json"
EXTRA_FORMS_KEY = "extra_forms"
# Pre-v2 sidecar (auto-migrated into the single file, then deleted).
LEGACY_SIDECAR_FILENAME = "ShaderTextureUsageForms.json"
