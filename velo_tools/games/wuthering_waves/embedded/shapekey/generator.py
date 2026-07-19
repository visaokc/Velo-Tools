"""Generate independent custom ShapeKey INI sections and runtime assets."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List, Mapping, Sequence, Tuple

from .planner import ShapeKeyPlanError

if TYPE_CHECKING:
    from .runtime import DomainShapeKeyPlan


HLSL_FILENAMES = (
    "ExternalShapeKeyStructured.hlsl",
)
OBSOLETE_HLSL_FILENAMES = (
    "ExternalShapeKeyFlat.hlsl",
)
_HLSL_DIR = Path(__file__).parent / "hlsl"
_SECTION_RE = re.compile(r"(?m)^\[([^\]\r\n]+)\]\s*$")
_CHECKSUM_RE = re.compile(r"(?m)^; SHA256 CHECKSUM: [0-9a-fA-F]{64}\s*$")


def _state_name(kind: str, suffix: str) -> str:
    return f"$external_shape_{kind}{suffix}"


def control_constant_lines(
        channels: Mapping[int, int],
        domain_suffixes: Iterable[str],
) -> List[str]:
    if not channels:
        return []
    lines = []
    lines.append("global $external_shape_active = 0")
    for shape_id, _channel in sorted(channels.items(), key=lambda item: item[1]):
        lines.append(f"global persist $ShapeKey_{shape_id} = 0.0")
    for suffix in domain_suffixes:
        lines.append(f"global {_state_name('applied', suffix)} = 0")
    return lines


def control_present_lines(
        channels: Mapping[int, int],
        domain_suffixes: Iterable[str],
) -> List[str]:
    if not channels:
        return []
    lines = []
    lines.append("post $external_shape_active = 0")
    for shape_id, channel in sorted(channels.items(), key=lambda item: item[1]):
        lines.extend([
            f"post x{100 + channel} = $ShapeKey_{shape_id}",
            f"if $ShapeKey_{shape_id} != 0",
            "    post $external_shape_active = 1",
            "endif",
        ])
    for suffix in domain_suffixes:
        lines.append(f"post {_state_name('applied', suffix)} = 0")
    return lines


def domain_section_specs(
        plan: DomainShapeKeyPlan,
        *,
        suffix: str,
        mesh_vertex_variable: str,
        position_filename: str,
) -> Tuple[Tuple[str, Tuple[str, ...], str], ...]:
    """Return (section name, lines, role) tuples for one resource domain."""
    specs = []
    applied = _state_name("applied", suffix)
    position = f"ResourcePositionBuffer{suffix}"
    external_base = f"ResourceExternalShapeKeyBase{suffix}"
    external_position = f"ResourceExternalShapeKeyedPosition{suffix}"

    if not plan.has_external:
        return ()

    apply_lines = [
        f"run = CustomShaderExternalShapeKeyStructured{suffix}",
        f"{applied} = 1",
    ]
    shader_cleanup = (
        f"cs-t50 = ResourceExternalShapeKeyVertexOffsetBuffer{suffix}",
        f"cs-t51 = ResourceExternalShapeKeyRecordChannelBuffer{suffix}",
        f"cs-t52 = ResourceExternalShapeKeyRecordDeltaBuffer{suffix}",
        f"cs-u6 = copy {external_base}",
        f"{external_position} = ref cs-u6",
        f"dispatch = {mesh_vertex_variable}/64+1, 1, 1",
        "cs-t50 = null",
        "cs-t51 = null",
        "cs-t52 = null",
        "cs-u6 = null",
    )
    specs.extend([
        (f"CommandListApplyExternalShapeKeys{suffix}", tuple(apply_lines),
         "shape_command"),
        (f"CustomShaderExternalShapeKeyStructured{suffix}", (
            "cs = hlsl/ExternalShapeKeyStructured.hlsl",
            *shader_cleanup,
        ), "shape_command"),
        (external_base, (
            "type = Buffer",
            "stride = 12",
            f"filename = {position_filename}",
        ), "buffer"),
        (external_position, (), "buffer"),
    ])

    resolve = []
    if plan.has_external:
        resolve.extend([
            "if $external_shape_active == 1",
            f"    if {applied} == 0",
            f"        run = CommandListApplyExternalShapeKeys{suffix}",
            "    endif",
            f"    vb0 = {external_position}",
        ])
        resolve.extend([
            "else",
            f"    vb0 = {position}",
            "endif",
        ])
    if resolve:
        specs.append((f"CommandListResolveShapeKeys{suffix}", tuple(resolve),
                      "shape_command"))
    return tuple(specs)


def _section_span(text: str, name: str) -> Tuple[int, int]:
    matches = list(_SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1).casefold() != name.casefold():
            continue
        return match.start(), matches[index + 1].start() if index + 1 < len(matches) else len(text)
    raise ShapeKeyPlanError(
        f"Custom INI template is missing required section [{name}]")


def _append_section_lines(text: str, name: str, lines: Sequence[str]) -> str:
    start, end = _section_span(text, name)
    body = text[start:end].rstrip() + "\n"
    insertion = "\n".join(lines).rstrip() + "\n"
    return text[:start] + body + insertion + "\n" + text[end:]


def _insert_before_marker(text: str, marker: str, insertion: str) -> str:
    if text.count(marker) != 1:
        raise ShapeKeyPlanError(
            f"Custom INI template is missing unique marker: {marker}")
    return text.replace(marker, insertion.rstrip() + "\n\n" + marker, 1)


def _replace_shared_position(text: str, suffix: str) -> str:
    name = f"CommandListOverrideSharedResources{suffix}"
    start, end = _section_span(text, name)
    body = text[start:end]
    pattern = re.compile(
        rf"(?im)^[ \t]*vb0[ \t]*=[ \t]*(?:ref[ \t]+)?Resource(?:ShapeKeyed)?Position(?:Buffer)?{re.escape(suffix)}[ \t]*$")
    body, count = pattern.subn(
        f"run = CommandListResolveShapeKeys{suffix}", body, count=1)
    if count != 1:
        raise ShapeKeyPlanError(
            f"[{name}] does not contain the expected vb0 position binding")
    return text[:start] + body + text[end:]


def _validate_channel_range(text: str, channels: Mapping[int, int]) -> None:
    if not channels:
        return
    occupied = {
        int(match.group(1))
        for match in re.finditer(r"(?im)^\s*(?:post\s+)?x(\d+)\s*=", text)
    }
    required = {100 + channel for channel in channels.values()}
    conflicts = sorted(occupied & required)
    if conflicts:
        raise ShapeKeyPlanError(
            "Custom INI template already assigns reserved ShapeKey IniParams: "
            + ", ".join(f"x{value}" for value in conflicts))


def _resource_filename(text: str, name: str) -> str:
    start, end = _section_span(text, name)
    match = re.search(r"(?im)^\s*filename\s*=\s*(.+?)\s*$", text[start:end])
    if match is None:
        raise ShapeKeyPlanError(
            f"Custom INI template section [{name}] has no filename")
    return match.group(1)


def validate_channel_lines(
        lines: Iterable[str], channels: Mapping[int, int]) -> None:
    _validate_channel_range("\n".join(lines), channels)


def inject_single_ib_ini(
        text: str,
        plan: DomainShapeKeyPlan,
        channels: Mapping[int, int],
        *,
        mesh_vertex_count: int,
) -> str:
    """Inject the independent pipeline into a rendered stock/LOD INI."""
    text = _CHECKSUM_RE.sub("", text).rstrip() + "\n"
    if not plan.has_external:
        return text
    _validate_channel_range(text, channels)
    suffix = ""
    constants = control_constant_lines(channels, (suffix,))
    present = control_present_lines(channels, (suffix,))
    text = _append_section_lines(text, "Constants", constants)
    text = _append_section_lines(text, "Present", present)
    text = _replace_shared_position(text, suffix)

    specs = domain_section_specs(
        plan,
        suffix=suffix,
        mesh_vertex_variable="$mesh_vertex_count",
        position_filename=_resource_filename(text, "ResourcePositionBuffer"),
    )
    existing = {match.group(1).casefold() for match in _SECTION_RE.finditer(text)}
    duplicate = [name for name, _lines, _role in specs if name.casefold() in existing]
    if duplicate:
        raise ShapeKeyPlanError(
            "Custom INI template conflicts with generated ShapeKey sections: "
            + ", ".join(duplicate))
    command_block = ["; External Custom Shape Keys -------------------------", ""]
    resource_block = [
        "; Resources: External Custom Shape Keys -------------------------", ""]
    for name, lines, role in specs:
        target = resource_block if role == "buffer" else command_block
        target.append(f"[{name}]")
        target.extend(lines)
        target.append("")
    text = _insert_before_marker(
        text,
        "; Resources: Shape Keys Override -------------------------",
        "\n".join(command_block),
    )
    text = _insert_before_marker(
        text,
        "; Resources: Skeleton Override -------------------------",
        "\n".join(resource_block),
    )
    return text


def write_hlsl_assets(output_root: Path, active: bool) -> None:
    target = Path(output_root) / "hlsl"
    if active:
        target.mkdir(parents=True, exist_ok=True)
        for filename in HLSL_FILENAMES:
            shutil.copyfile(_HLSL_DIR / filename, target / filename)
    for filename in (*HLSL_FILENAMES, *OBSOLETE_HLSL_FILENAMES):
        if active and filename in HLSL_FILENAMES:
            continue
        path = target / filename
        if path.is_file():
            path.unlink()
    if target.is_dir() and not any(target.iterdir()):
        target.rmdir()
