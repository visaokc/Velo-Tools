# Game-level constants for the WWMI slot-style texture layer (v3, zero
# shader-hash architecture — see docs/adr/0007).
#
# Replacement is driven by slot+format combination conditions compatible with
# the XQFA WWMI-Tools fork (github.com/XQFAAAA/WWMI-Tools/tree/xqfa): fuzzy
# TextureOverride sections tag every texture of a DXGI format family with a
# deterministic filter_index derived from the format-name prefix, and the
# component draw command lists branch on the tags of the bound ps-t slots.
# No ShaderOverride / ShaderRegex sections are emitted at all — shader hashes
# churn on every game update, formats and slot layouts do not, and format
# tags are residency-invariant (every streaming mip level of a texture shares
# the format family), which is what makes the scheme immune to streaming.
#
# Character-level data (slot maps, formats, form sets) is NEVER
# hardcoded — generator.py derives everything from ShaderTextureUsage.json
# (+ its extra_forms key) and the source-folder texture files at export time.
#
# Generated section/variable names are brand-free (project rule since the LOD
# round). Sections and ini variables are namespaced per ini file by 3DMigoto,
# so generic names cannot collide across mods.
#
# filter_index namespaces (ecosystem contracts, ADR 0007):
#
#   83.{ascii}                    format-family tags, IDENTICAL to the XQFA
#                                 fork derivation: prefix letters of the DXGI
#                                 format name mapped to their ascii codes
#                                 (BC7 -> 83.666755). filter_index is global
#                                 per resource, so velo mods MUST use the same
#                                 values as XQFA-exported mods or whichever
#                                 loses the section-priority race goes blind.
#   [2,000,000 .. 15,999,981)     optional shader-anchor marks used only for
#                                 user-provided form anchors.
#
# Known co-tenants kept clear of: WWMI core 3381.7777 (bone CB), RabbitFX
# 1718.x / 1719.x, hand-made mod values around 3381.x.

# ----------------------------------------------------- emitted names --------

VAR_FORM = "$form_id"
CMDLIST_SET_TEXTURES = "CommandListSetTexturesComponent{component_id}"
LOCAL_FORM_DISCRIMINATOR_KEY = "local_form_discriminator"
LOCAL_FORM_DISCRIMINATOR_SCHEMA = 4
LOCAL_COMPONENT_SOURCES_KEY = "_velo_local_component_sources"
FORM_COMPONENT_MODE_KEY = "form_component_mode"
LEGACY_FORM_COMPONENT_MODE_KEYS = ("_velo_form_component_mode",)
FORM_ANCHORS_KEY = "form_anchors"
# USER-SPECIFIED form anchors (detection only, optional): a form-exclusive
# resource (a vb0 — geometry never streams, so the latch is instant and far
# more version-stable than shaders) or shader latches $form_id the moment
# the form draws. Field facts (v3.5): this WWMI 3dmigoto fork matches draws
# by VERTEX BUFFER hash only — ib hashes are logged in dumps but never
# matched (exhaustive cross-check: every ini hash in the install shows up
# as a vb in dumps); and a buffer-hash section needs match_first_index to
# enter the per-draw command-list path at all. A stale anchor never fires
# and the texture latches take over; no binding condition ever references
# an anchor.
SEC_RESOURCE_ANCHOR = "TextureOverrideFormAnchor{anchor_hash}"
SEC_SHADER_ANCHOR = "ShaderOverrideFormAnchor{anchor_hash}"
# Anchor heartbeat + per-frame watchdog. Anchored forms' exclusive parts draw
# every frame, so a whole frame without a heartbeat pins the unanchored/default
# form by elimination. Stale or absent trusted anchors therefore fail closed to
# form1 instead of leaving a previous scene's form state latched.
VAR_ANCHOR_SEEN = "$anchor_seen"
FORM_ANCHOR_VB0_KEY = "form_anchor_vb0_hash"
FORM_ANCHOR_LABEL_KEY = "form_anchor_label"
FORM_ANCHOR_SOURCE_KEY = "form_anchor_source"
FORM_ANCHOR_RANK_KEY = "form_anchor_rank"
FORM_ANCHOR_SOURCE_TRUSTED = "trusted_anchor_finder"
SEC_FORMAT_TAG = "TextureOverrideComponent{component_id}{format_name}"
SEC_FORMAT_TAG_LOD = "TextureOverrideLod{level}Component{component_id}{format_name}"

# Fuzzy format-tag sections use the same match_priority as the XQFA fork (the
# value is part of the cross-mod contract: equal-value duplicate sections must
# tie, not fight).
FORMAT_TAG_PRIORITY = 100

# ----------------------------------------------------- filter values --------

_PS_MARK_BASE = 2000000
_PS_MARK_MOD = 13999981  # prime


def ps_mark_value(ps_hash: str) -> int:
    """Deterministic filter_index for a DETECTION-ONLY pixel shader mark
    (16 hex chars). Shader hashes churn on every game update, so these only
    feed the zero-latency form-switch fast path: a dead section makes its
    check constant-false and detection falls back to the texture latches —
    the binding layer never references shader hashes."""
    return _PS_MARK_BASE + int(ps_hash, 16) % _PS_MARK_MOD


def format_prefix(format_name: str) -> str:
    """DXGI format name -> family prefix, XQFA derivation (name.split('_')[0])."""
    return format_name.split('_')[0]


def format_filter_index(format_name: str) -> float:
    """Format-family filter_index, byte-compatible with the XQFA fork:
    float("83." + concatenated ascii codes of the prefix letters)."""
    prefix = format_prefix(format_name)
    return float('83.' + ''.join(str(ord(c)) for c in prefix))


# Full DXGI_FORMAT name vocabulary (indices 1..99 of the D3D spec; the
# DXGI_FORMAT_ prefix is stripped, matching both 3DMigoto's match_format
# parser and the XQFA fork's DXGIFormatIndex enum). Used to enumerate the
# same-prefix family members a fuzzy tag section set must cover.
DXGI_FORMAT_NAMES = (
    'R32G32B32A32_TYPELESS', 'R32G32B32A32_FLOAT', 'R32G32B32A32_UINT',
    'R32G32B32A32_SINT', 'R32G32B32_TYPELESS', 'R32G32B32_FLOAT',
    'R32G32B32_UINT', 'R32G32B32_SINT', 'R16G16B16A16_TYPELESS',
    'R16G16B16A16_FLOAT', 'R16G16B16A16_UNORM', 'R16G16B16A16_UINT',
    'R16G16B16A16_SNORM', 'R16G16B16A16_SINT', 'R32G32_TYPELESS',
    'R32G32_FLOAT', 'R32G32_UINT', 'R32G32_SINT', 'R32G8X24_TYPELESS',
    'D32_FLOAT_S8X24_UINT', 'R32_FLOAT_X8X24_TYPELESS',
    'X32_TYPELESS_G8X24_UINT', 'R10G10B10A2_TYPELESS', 'R10G10B10A2_UNORM',
    'R10G10B10A2_UINT', 'R11G11B10_FLOAT', 'R8G8B8A8_TYPELESS',
    'R8G8B8A8_UNORM', 'R8G8B8A8_UNORM_SRGB', 'R8G8B8A8_UINT',
    'R8G8B8A8_SNORM', 'R8G8B8A8_SINT', 'R16G16_TYPELESS', 'R16G16_FLOAT',
    'R16G16_UNORM', 'R16G16_UINT', 'R16G16_SNORM', 'R16G16_SINT',
    'R32_TYPELESS', 'D32_FLOAT', 'R32_FLOAT', 'R32_UINT', 'R32_SINT',
    'R24G8_TYPELESS', 'D24_UNORM_S8_UINT', 'R24_UNORM_X8_TYPELESS',
    'X24_TYPELESS_G8_UINT', 'R8G8_TYPELESS', 'R8G8_UNORM', 'R8G8_UINT',
    'R8G8_SNORM', 'R8G8_SINT', 'R16_TYPELESS', 'R16_FLOAT', 'D16_UNORM',
    'R16_UNORM', 'R16_UINT', 'R16_SNORM', 'R16_SINT', 'R8_TYPELESS',
    'R8_UNORM', 'R8_UINT', 'R8_SNORM', 'R8_SINT', 'A8_UNORM', 'R1_UNORM',
    'R9G9B9E5_SHAREDEXP', 'R8G8_B8G8_UNORM', 'G8R8_G8B8_UNORM',
    'BC1_TYPELESS', 'BC1_UNORM', 'BC1_UNORM_SRGB', 'BC2_TYPELESS',
    'BC2_UNORM', 'BC2_UNORM_SRGB', 'BC3_TYPELESS', 'BC3_UNORM',
    'BC3_UNORM_SRGB', 'BC4_TYPELESS', 'BC4_UNORM', 'BC4_SNORM',
    'BC5_TYPELESS', 'BC5_UNORM', 'BC5_SNORM', 'B5G6R5_UNORM',
    'B5G5R5A1_UNORM', 'B8G8R8A8_UNORM', 'B8G8R8X8_UNORM',
    'R10G10B10_XR_BIAS_A2_UNORM', 'B8G8R8A8_TYPELESS', 'B8G8R8A8_UNORM_SRGB',
    'B8G8R8X8_TYPELESS', 'B8G8R8X8_UNORM_SRGB', 'BC6H_TYPELESS', 'BC6H_UF16',
    'BC6H_SF16', 'BC7_TYPELESS', 'BC7_UNORM', 'BC7_UNORM_SRGB',
)


def same_prefix_formats(format_name: str) -> list:
    """All DXGI format names sharing the family prefix (XQFA
    get_same_prefix_formats parity, e.g. BC7 -> the three BC7_* names).
    Fuzzy tag sections are emitted for every member so both typed and
    typeless-created game resources receive the family tag."""
    prefix = format_prefix(format_name) + '_'
    return [n for n in DXGI_FORMAT_NAMES if n.startswith(prefix)]


# emitted_format_members() below mirrors the XQFA fork's
# DXGIFormat.to_typeless() VERBATIM (wwmi-tools/migoto_io/data_model/
# dxgi_format.py on the xqfa branch, confirmed against source 2026-06-17):
#
#     def to_typeless(self):
#         prefix = self.name.split('_', 1)[0]
#         if prefix == 'R8':
#             return self
#         typeless_name = f'{prefix}_TYPELESS'
#         try:
#             return DXGIFormatIndex[typeless_name]
#         except KeyError:
#             return self
#
# R8 is the ONE hardcoded exception: WuWa binds R8 as a concrete typed format
# (legacy L8 -> R8_UNORM, structurally non-typeless), so R8_TYPELESS would
# never match it. Every other family normalizes to <prefix>_TYPELESS when that
# variant exists, else keeps its recorded member (A8/R1/B5G6R5 have no TYPELESS
# sibling). The dropped typed members never match a live resource and only
# inflate the fuzzy match_format section count (in-game frame drops).
#
# This set is a CROSS-MOD CONTRACT, not a local heuristic: the format-family
# filter_index values are shared with XQFA-exported mods, so the exception set
# must MIRROR upstream to_typeless. Keep it == the xqfa fork's set (currently
# R8-only). Do NOT add prefixes on local guesses -- B8G8R8A8 / R16 / R8G8 are
# NOT excepted upstream (they normalize to *_TYPELESS); extend only in lockstep
# with the xqfa fork.
TYPED_AT_MATCH_PREFIXES = frozenset({'R8'})


def emitted_format_members(format_name: str) -> list:
    """The match_format member(s) to emit for one recorded format -- a single
    member, versus same_prefix_formats' full family. Default: <prefix>_TYPELESS
    (covers the family and every streaming mip level). A TYPED_AT_MATCH_PREFIXES
    format keeps its recorded typed member instead; a format whose family has no
    _TYPELESS variant (A8, R1, B5G6R5, ...) also falls back to the recorded
    member. The family filter_index is unchanged either way, so this only drops
    dead sections and never alters a tag value (XQFA cross-mod contract intact).
    Empty/unrecognised format -> [] (same as same_prefix_formats(''))."""
    if not format_name:
        return []
    prefix = format_prefix(format_name)
    if prefix in TYPED_AT_MATCH_PREFIXES:
        return [format_name]
    typeless = prefix + '_TYPELESS'
    return [typeless] if typeless in DXGI_FORMAT_NAMES else [format_name]


# --------------------------------------------------- classification ---------

# A pair is material-class when its slot map carries ALL of MAIN_SLOTS — a
# structural slot-set fingerprint, independent of which textures the author
# kept in the folder. v3 uses it only for marker/ordering heuristics (which
# pairs anchor form detection, which set wins a same-signature conflict);
# replacement branches themselves are emitted for every pair.
MAIN_SLOTS = (0, 1, 2, 3)
SERVICE_SLOTS = (5, 6, 7, 8)
LOCAL_DISCRIMINATOR_SLOTS = tuple(range(9))

# Optional belt on top of the slot-set fingerprint: when a MAIN_SLOTS texture
# descriptor is known, material pairs must look like character textures
# (square; dump-extracted files carry only the base mip level, so a mip-count
# requirement is NOT usable). Unknown descriptors never block.
MATERIAL_REQUIRE_SQUARE = True

# Single data file: base form maps live in the top-level "Component N" keys,
# extra forms under the reserved top-level key below (preserved across
# re-extraction by the _shader_texture_usage patch).
BASE_USAGE_FILENAME = "ShaderTextureUsage.json"
EXTRA_FORMS_KEY = "extra_forms"
# Pre-v2 sidecar (auto-migrated into the single file, then deleted).
LEGACY_SIDECAR_FILENAME = "ShaderTextureUsageForms.json"
