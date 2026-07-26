# Final WWMI INI sanitizer for generated artifact readability.
#
# This hook runs at the rendered INI boundary instead of renaming Blender data
# blocks mid-export. Material-route and MMD temp copies still use
# __velo_export internally for cleanup and legacy detection, but that marker is
# not part of the user-facing mod artifact. Stock numeric texture sections are
# also renamed from the hash already present in their generated DDS filename.

import re

_INSTALLED = False
_ORIG_BUILD_FROM_TEMPLATE = None
_IM_MODULE = None

_TEMP_OBJECT_SUFFIX_RE = re.compile(r"__velo_export(?:\.\d{3})?")
_DRAWVAR_SUFFIX_RE = re.compile(r"(\$(?:draw|obj)_[A-Za-z0-9_]*?)_velo_export\b")
_SECTION_NAME_RE = re.compile(r"^\[([^\]]+)\][ \t]*\r?$", re.MULTILINE)
_NUMERIC_TEXTURE_RESOURCE_RE = re.compile(
    r"^\[ResourceTexture(?P<index>\d+)\][ \t]*\r?$"
    r"(?P<body>.*?)(?=^\[[^\]]+\][ \t]*\r?$|\Z)",
    re.MULTILINE | re.DOTALL,
)
_TEXTURE_HASH_IN_FILENAME_RE = re.compile(
    r"^\s*filename\s*=\s*.*?\bt=(?P<hash>[0-9a-fA-F]{8})\b",
    re.MULTILINE,
)


def _readable_texture_section_names(text: str) -> str:
    candidates = []
    for match in _NUMERIC_TEXTURE_RESOURCE_RE.finditer(text):
        filename_match = _TEXTURE_HASH_IN_FILENAME_RE.search(match.group("body"))
        if filename_match is None:
            continue
        candidates.append(
            (match.group("index"), filename_match.group("hash").lower())
        )

    hash_counts = {}
    for _, texture_hash in candidates:
        hash_counts[texture_hash] = hash_counts.get(texture_hash, 0) + 1

    existing_names = {
        match.group(1).casefold() for match in _SECTION_NAME_RE.finditer(text)
    }
    renames = {}
    for index, texture_hash in candidates:
        if hash_counts[texture_hash] != 1:
            continue
        resource_target = f"ResourceTexture_{texture_hash}"
        override_target = f"TextureOverrideTexture_{texture_hash}"
        if (
            resource_target.casefold() in existing_names
            or override_target.casefold() in existing_names
        ):
            continue
        renames[f"ResourceTexture{index}"] = resource_target
        renames[f"TextureOverrideTexture{index}"] = override_target

    if not renames:
        return text

    token_re = re.compile(
        r"(?<![A-Za-z0-9_])("
        + "|".join(re.escape(name) for name in sorted(renames, key=len, reverse=True))
        + r")(?![A-Za-z0-9_])"
    )
    return token_re.sub(lambda match: renames[match.group(1)], text)


def sanitize_ini_text(text: str) -> str:
    """Apply final readability cleanup to rendered WWMI INI text."""
    result = _readable_texture_section_names(text)
    result = _TEMP_OBJECT_SUFFIX_RE.sub("", result)

    # Names that pass through format_ini_drawvar become e.g.
    # $draw_component_3_body_velo_export. Collapse that exact internal suffix
    # without touching arbitrary user metadata or custom prose containing Velo.
    while True:
        collapsed = _DRAWVAR_SUFFIX_RE.sub(r"\1", result)
        if collapsed == result:
            return result
        result = collapsed


def _ini_maker_module():
    global _IM_MODULE
    if _IM_MODULE is None:
        from .._wwmi_core.blender_export import ini_maker as module
        _IM_MODULE = module
    return _IM_MODULE


def install():
    global _INSTALLED, _ORIG_BUILD_FROM_TEMPLATE
    if _INSTALLED:
        return

    module = _ini_maker_module()
    _ORIG_BUILD_FROM_TEMPLATE = module.IniMaker.build_from_template

    def _wrapped_build_from_template(self, context, cfg, template_string=None, with_checksum=False):
        result = _ORIG_BUILD_FROM_TEMPLATE(
            self,
            context,
            cfg,
            template_string=template_string,
            with_checksum=False,
        )
        result = sanitize_ini_text(result)
        if with_checksum:
            result = module.IniMaker.with_checksum(result)
        self.ini_string = result
        return result

    _wrapped_build_from_template._velo_ini_sanitize_hook = True
    module.IniMaker.build_from_template = _wrapped_build_from_template
    _INSTALLED = True


def remove():
    global _INSTALLED, _ORIG_BUILD_FROM_TEMPLATE
    if not _INSTALLED:
        return
    module = _ini_maker_module()
    module.IniMaker.build_from_template = _ORIG_BUILD_FROM_TEMPLATE
    _ORIG_BUILD_FROM_TEMPLATE = None
    _INSTALLED = False
