"""Temporary, removable bind-flag fallback for Merged Skeleton exports.

The upstream resource-reference graph can miss SRV flags depending on INI
namespace/parse order. Work only on rendered output, never the vendored core.
A complete explicit SRV/UAV declaration is authoritative and passes through
unchanged. A version bump or RWStructuredBuffer type alone proves nothing:
resource type and bind capabilities are separate in the loader.

Retirement: audit the integrated stock template and run the export/lifecycle
regressions without this wrapper. Once every supported upstream path supplies
the capabilities, delete this module and its import/install/remove call sites.
An upstream runtime-only fix needs independent loader verification; never
silently retire this fallback based on a remote release or guessed version.
"""
from __future__ import annotations

import re
from pathlib import Path

_SECTION_NAME = "ResourceMergedSkeletonDataRW"
_REQUIRED_FLAGS = ("shader_resource", "unordered_access")
_FLAG_NAMES = frozenset({
    "vertex_buffer", "index_buffer", "constant_buffer", "shader_resource",
    "stream_output", "render_target", "depth_stencil", "unordered_access",
    "decoder", "video_encoder",
})
_SECTION_RE = re.compile(r"^[ \t]*\[([^\]]+)\][ \t]*(?:;.*)?$", re.IGNORECASE)
_STOCK_REFERENCE_RE = re.compile(
    r"^[ \t]*Resource\\EFMIv1\\(?:Output_MergedSkeleton|OutputMergedSkeleton_Template)"
    r"[ \t]*=[ \t]*(?:ref|reference)[ \t]+ResourceMergedSkeletonDataRW[ \t]*(?:;.*)?$",
    re.IGNORECASE | re.MULTILINE,
)
_INSTALLED = False
_ORIGINAL_BUILD_FROM_TEMPLATE = None
_WRAPPER = None
_ACTIVATION = None
_VENDOR_DEFAULT_SUPPORT = "unknown"


class MergedSkeletonBindFlagsError(ValueError):
    """The target resource is ambiguous or cannot safely accept the fallback."""


def _without_eol(line: str) -> tuple[str, str]:
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def _active_assignment(line: str) -> tuple[str, str] | None:
    active = _without_eol(line)[0].split(";", 1)[0]
    if "=" not in active:
        return None
    key, value = active.split("=", 1)
    key = key.strip().casefold()
    return (key, value.strip()) if key else None


def _section_bounds(lines: list[str]) -> tuple[int, int] | None:
    headers, starts = [], []
    for index, line in enumerate(lines):
        body = _without_eol(line)[0]
        match = _SECTION_RE.match(body.lstrip("\ufeff") if index == 0 else body)
        if match:
            headers.append(index)
            if match.group(1).strip().casefold() == _SECTION_NAME.casefold():
                starts.append(index)
    if len(starts) > 1:
        raise MergedSkeletonBindFlagsError("Duplicate Merged Skeleton resource sections")
    if not starts:
        return None
    start = starts[0]
    return start, next((index for index in headers if index > start), len(lines))


def _section_state(lines: list[str], start: int, end: int) -> tuple[str, list[int], set[str]]:
    resource_type, type_lines, bind_lines, flags = "", [], [], set()
    for index in range(start + 1, end):
        assignment = _active_assignment(lines[index])
        if assignment is None:
            continue
        key, value = assignment
        if key == "type":
            type_lines.append(index)
            resource_type = value.casefold()
        elif key == "bind_flags":
            bind_lines.append(index)
            # The loader takes whitespace-separated flag names, not expressions.
            words = set(value.casefold().split())
            if not words.issubset(_FLAG_NAMES) or "constant_buffer" in words:
                raise MergedSkeletonBindFlagsError("Unsupported Merged Skeleton bind flags")
            flags.update(words)
    if len(type_lines) > 1 or len(bind_lines) > 1:
        raise MergedSkeletonBindFlagsError("Duplicate Merged Skeleton resource declarations")
    return resource_type, bind_lines, flags


def detect_official_support(text: str) -> str:
    """Inspect unmodified template/output capabilities; do not infer from type."""
    lines = text.splitlines(keepends=True)
    bounds = _section_bounds(lines)
    if bounds is None:
        return "not_applicable"
    start, end = bounds
    if any("{%" in line for line in lines[start + 1:end]):
        # Conditional Jinja must be rendered for the requested mode first.
        return "unknown"
    _kind, _bind_lines, flags = _section_state(lines, start, end)
    return "official_explicit_bind_flags" if set(_REQUIRED_FLAGS) <= flags else "missing"


def _extend_bind_flags_line(line: str, missing: list[str]) -> str:
    body, eol = _without_eol(line)
    active, separator, comment = body.partition(";")
    prefix, raw_value = active.split("=", 1)
    leading = raw_value[:len(raw_value) - len(raw_value.lstrip())] or " "
    trailing = raw_value[len(raw_value.rstrip()):]
    value = raw_value.strip()
    updated = (value + " " if value else "") + " ".join(missing)
    return prefix + "=" + leading + updated + trailing + separator + comment + eol


def ensure_merged_skeleton_bind_flags(text: str) -> tuple[str, str]:
    """Patch only the exact target resource; complete templates are unchanged."""
    lines = text.splitlines(keepends=True)
    bounds = _section_bounds(lines)
    if bounds is None:
        if _STOCK_REFERENCE_RE.search(text):
            raise MergedSkeletonBindFlagsError("Missing Merged Skeleton resource section")
        return text, "not_applicable"
    start, end = bounds
    _kind, bind_lines, flags = _section_state(lines, start, end)
    if set(_REQUIRED_FLAGS) <= flags:
        return text, "official_explicit_bind_flags"
    missing = [flag for flag in _REQUIRED_FLAGS if flag not in flags]
    if bind_lines:
        index = bind_lines[0]
        lines[index] = _extend_bind_flags_line(lines[index], missing)
        return "".join(lines), "extended"
    eol = _without_eol(lines[start])[1] or ("\r\n" if "\r\n" in text else "\n")
    insertion = start + 1
    for index in range(start + 1, end):
        assignment = _active_assignment(lines[index])
        if assignment and assignment[0] in {"type", "format", "stride", "array"}:
            insertion = index + 1
    suffix = "" if insertion == len(lines) and not text.endswith(("\r", "\n")) else eol
    if insertion and not lines[insertion - 1].endswith(("\r", "\n")):
        lines[insertion - 1] += eol
    lines.insert(insertion, f"bind_flags = {' '.join(_REQUIRED_FLAGS)}{suffix}")
    return "".join(lines), "injected"


def apply_compatibility(text: str, skeleton_mode: str) -> tuple[str, str]:
    if skeleton_mode != "MERGED_SKELETON":
        return text, "not_applicable"
    return ensure_merged_skeleton_bind_flags(text)


def _effective_skeleton_mode(cfg) -> str:
    """Recover the request while unified_vg_export uses MERGED internally."""
    mode = getattr(cfg, "mod_skeleton_type", "")
    if mode == "MERGED_SKELETON":
        return mode
    try:
        if cfg.get("_compact_vg_merged_skeleton_export", False):
            return "MERGED_SKELETON"
    except (AttributeError, ReferenceError, TypeError):
        pass
    return mode


def _vendored_default_support(module) -> str:
    template = Path(module.__file__).resolve().parent.parent / "templates" / "mod.ini.j2"
    try:
        return detect_official_support(template.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, MergedSkeletonBindFlagsError):
        return "unknown"


def install() -> None:
    """Install after game-local transforms; preserve outer wrappers on removal."""
    global _INSTALLED, _ORIGINAL_BUILD_FROM_TEMPLATE, _VENDOR_DEFAULT_SUPPORT
    global _WRAPPER, _ACTIVATION
    if _INSTALLED:
        return
    from ._efmi_core.blender_export import ini_maker as module
    from ...i18n import iface_

    original = module.IniMaker.build_from_template
    activation = {"enabled": True}

    def wrapped(self, context, cfg, template_string=None, with_checksum=False):
        if not activation["enabled"] or _effective_skeleton_mode(cfg) != "MERGED_SKELETON":
            return original(self, context, cfg, template_string=template_string,
                            with_checksum=with_checksum)
        result = original(self, context, cfg, template_string=template_string, with_checksum=False)
        try:
            result, action = ensure_merged_skeleton_bind_flags(result)
        except MergedSkeletonBindFlagsError as exc:
            raise ValueError(iface_(str(exc))) from exc
        self.merged_skeleton_bind_flags_status = action
        if action in {"injected", "extended"}:
            print(f"[MergedSkeletonBindFlags] {action}: SRV/UAV capabilities completed")
        if with_checksum:
            result = module.IniMaker.with_checksum(result)
        self.ini_string = result
        return result

    wrapped._merged_skeleton_bind_flags_hook = True
    wrapped._merged_skeleton_bind_flags_inner = original
    _ORIGINAL_BUILD_FROM_TEMPLATE, _WRAPPER, _ACTIVATION = original, wrapped, activation
    _VENDOR_DEFAULT_SUPPORT = _vendored_default_support(module)
    module.IniMaker.build_from_template = wrapped
    _INSTALLED = True
    print(f"[MergedSkeletonBindFlags] Integrated template: {_VENDOR_DEFAULT_SUPPORT}; per-export capability check enabled")


def remove() -> None:
    """Disable retained wrapper references without clobbering another owner."""
    global _INSTALLED, _ORIGINAL_BUILD_FROM_TEMPLATE, _VENDOR_DEFAULT_SUPPORT
    global _WRAPPER, _ACTIVATION
    if not _INSTALLED:
        return
    from ._efmi_core.blender_export import ini_maker as module
    _ACTIVATION["enabled"] = False
    if module.IniMaker.build_from_template is _WRAPPER:
        module.IniMaker.build_from_template = _ORIGINAL_BUILD_FROM_TEMPLATE
    _ORIGINAL_BUILD_FROM_TEMPLATE = _WRAPPER = _ACTIVATION = None
    _VENDOR_DEFAULT_SUPPORT, _INSTALLED = "unknown", False
