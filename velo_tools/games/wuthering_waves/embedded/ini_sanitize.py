# Final WWMI INI sanitizer for Velo-owned temporary export labels.
#
# This hook runs at the rendered INI boundary instead of renaming Blender data
# blocks mid-export. Material-route and MMD temp copies still use
# __velo_export internally for cleanup and legacy detection, but that marker is
# not part of the user-facing mod artifact.

import re

_INSTALLED = False
_ORIG_BUILD_FROM_TEMPLATE = None
_IM_MODULE = None

_TEMP_OBJECT_SUFFIX_RE = re.compile(r"__velo_export(?:\.\d{3})?")
_DRAWVAR_SUFFIX_RE = re.compile(r"(\$(?:draw|obj)_[A-Za-z0-9_]*?)_velo_export\b")


def sanitize_ini_text(text: str) -> str:
    """Remove only Velo-owned temporary export labels from rendered INI text."""
    result = _TEMP_OBJECT_SUFFIX_RE.sub("", text)

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
