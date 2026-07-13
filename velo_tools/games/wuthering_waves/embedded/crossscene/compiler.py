"""Direct WWMI cross-scene compiler for schema-v3 aggregate roots."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


_HEADER_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_LOCAL_REF_RE = re.compile(
    r"(?:run\s*=|ref\s+|checktextureoverride\s*=|this\s*=)\s*"
    r"((?:CommandList|Resource|TextureOverride)"
    r"(?:\\[A-Za-z0-9_.\\-]+|[A-Za-z0-9_.-]+))",
    re.I,
)
_FILENAME_RE = re.compile(r"^\s*filename\s*=\s*(\S+)\s*$", re.I | re.M)


class CrossSceneCompileError(RuntimeError):
    pass


@dataclass
class IniSectionIR:
    name: str
    lines: List[str]


@dataclass
class CrossSceneIR:
    preamble: List[str] = field(default_factory=list)
    sections: List[IniSectionIR] = field(default_factory=list)

    @classmethod
    def parse(cls, text: str) -> "CrossSceneIR":
        result = cls()
        current = None
        for line in text.splitlines():
            match = _HEADER_RE.match(line)
            if match:
                current = IniSectionIR(match.group(1), [])
                result.sections.append(current)
            elif current is None:
                result.preamble.append(line)
            else:
                current.lines.append(line)
        return result

    def extend_text(self, text: str) -> None:
        fragment = self.parse(text)
        self.sections.extend(fragment.sections)

    def get(self, name: str) -> IniSectionIR:
        key = name.casefold()
        matches = [section for section in self.sections
                   if section.name.casefold() == key]
        if len(matches) != 1:
            raise CrossSceneCompileError(
                f"expected exactly one [{name}] section, found {len(matches)}")
        return matches[0]

    def render(self) -> str:
        lines = list(self.preamble)
        for section in self.sections:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"[{section.name}]")
            lines.extend(section.lines)
        return "\n".join(lines).rstrip() + "\n"

    def assert_unique(self) -> None:
        seen: Dict[str, str] = {}
        duplicates = []
        for section in self.sections:
            key = section.name.casefold()
            if key in seen:
                duplicates.append(f"[{seen[key]}] / [{section.name}]")
            else:
                seen[key] = section.name
        if duplicates:
            raise CrossSceneCompileError(
                "case-insensitive duplicate INI sections: " + ", ".join(duplicates))


@dataclass(frozen=True)
class CompilerSettings:
    context: Any
    cfg: Any
    root: Path
    selection: Any


@dataclass(frozen=True)
class OutputGates:
    partial_export: bool
    write_ini: bool
    copy_textures: bool
    logo_source: Optional[Path] = None


@dataclass
class CompiledMod:
    ini_text: str
    buffers: Dict[str, Any]
    textures: Tuple[Any, ...]
    slot_plan: Any = None
    section_routes: Dict[str, str] = field(default_factory=dict)
    report: Dict[str, Any] = field(default_factory=dict)


def _template_source(skeleton_mode: str, comment_ini: bool) -> str:
    from ..lod import export_hook
    path = export_hook._TEMPLATE_PATHS[skeleton_mode]
    raw = path.read_text(encoding="utf-8")
    if comment_ini:
        return raw
    return "".join(
        line + "\n" for line in raw.split("\n")
        if not line.strip().startswith("{{note")
    )


def _remove_texture_loop(source: str) -> str:
    start = source.find("{%- for texture in textures %}")
    if start < 0:
        return source
    resource = source.find("[ResourceTexture{{ loop.index0 }}]", start)
    if resource < 0:
        return source
    end = source.find("{%- endfor %}", resource)
    if end < 0:
        return source
    end += len("{%- endfor %}")
    return source[:start] + source[end:]


def _unit_fragment_source(source: str, skeleton_mode: str) -> str:
    macro = source[:source.find("\n", source.find("macro note")) + 1]
    if skeleton_mode == "MERGED":
        first = source.find("[CommandListUpdateMergedSkeleton]")
        first_end = source.find("[ResourceModName]", first)
        second = source.find("[TextureOverrideMarkBoneDataCB]", first_end)
        if min(first, first_end, second) < 0:
            raise CrossSceneCompileError("MERGED unit template anchors are missing")
        fragment = source[first:first_end] + "\n" + source[second:]
    else:
        first = source.find("[CommandListTriggerResourceOverrides]")
        if first < 0:
            raise CrossSceneCompileError("COMPONENT unit template anchors are missing")
        fragment = source[first:]
    return macro + _remove_texture_loop(fragment)


def specialize_unit_template(source: str, suffix: str) -> str:
    """Assign child section/resource identities before Jinja rendering."""
    placeholders: Dict[str, str] = {}

    def protect(value: str) -> str:
        key = f"__VELO_XS_TOKEN_{len(placeholders)}__"
        placeholders[key] = value
        return key

    dynamic = re.compile(
        r"((?:CommandList|Resource|TextureOverride)[A-Za-z0-9_]*"
        r"(?:\{\{[^{}]+\}\}[A-Za-z0-9_]*)+)"
    )

    def dynamic_repl(match: re.Match[str]) -> str:
        value = match.group(1)
        if "Component" in value:
            value = re.sub(
                r"\{\{\s*loop\.index0\s*\}\}",
                "{{ component_ids[loop.index0] }}",
                value,
            )
        return protect(value + suffix)

    source = dynamic.sub(dynamic_repl, source)
    source = source.replace(
        "$merge_status_id_{{ loop.index0 }}",
        protect("$merge_status_id_{{ component_ids[loop.index0] }}"),
    )
    for stem in ("offset", "count"):
        token = f"$shapekey_vertex_{stem}_batch{{{{ loop.index0 }}}}"
        source = source.replace(token, protect(token + suffix))
    source = source.replace("$mesh_vertex_count", "$mesh_vertex_count" + suffix)
    source = source.replace("$shapekey_vertex_count", "$shapekey_vertex_count" + suffix)
    source = source.replace(
        "filename = Meshes/{{ buffer_name }}.buf",
        "filename = Meshes/{{ buffer_name }}" + suffix + ".buf",
    )

    globals_kept = {"CommandListTriggerResourceOverrides"}
    static = re.compile(
        r"(?<![A-Za-z0-9_\\])"
        r"((?:CommandList|Resource|TextureOverride)[A-Za-z0-9_]+)"
        r"(?![A-Za-z0-9_\\{])"
    )

    def static_repl(match: re.Match[str]) -> str:
        value = match.group(1)
        return value if value in globals_kept else value + suffix

    source = static.sub(static_repl, source)
    for key, value in placeholders.items():
        source = source.replace(key, value)
    return source


def _render(source: str, maker: Any, **extra: Any) -> str:
    from ..._wwmi_core.libs.jinja2 import Template
    values = dict(vars(maker))
    values.update(extra)
    rendered = Template(source).render(values)
    return "".join(
        line + "\n" for line in rendered.split("\n")
        if not line.strip().startswith(";DEL")
    )


def _maker(unit: Any, cfg: Any, textures: Sequence[Any], logo_source: Path) -> Any:
    from ..._wwmi_core.blender_export.ini_maker import IniMaker
    from ..._wwmi_core.blender_export.metadata_collector import ModInfo, Version
    from ..._wwmi_core.blender_export.text_formatter import TextFormatter
    return IniMaker(
        cfg=cfg,
        mod_info=ModInfo(
            wwmi_tools_version=Version(str(cfg.wwmi_tools_version)),
            required_wwmi_version=Version(str(cfg.required_wwmi_version)),
            mod_name=str(cfg.mod_name),
            mod_author=str(cfg.mod_author),
            mod_desc=str(cfg.mod_desc),
            mod_link=str(cfg.mod_link),
            mod_logo=logo_source,
        ),
        extracted_object=unit.extracted_object,
        merged_object=unit.merged_object,
        buffers=unit.buffers,
        textures=list(textures),
        comment_code=bool(cfg.comment_ini),
        unrestricted_custom_shape_keys=bool(cfg.unrestricted_custom_shape_keys),
        skeleton_scale=float(cfg.skeleton_scale),
        formatter=TextFormatter(),
    )


def _normalized_cfg(cfg: Any) -> Any:
    mode = ("COMPONENT" if cfg.mod_skeleton_type == "COMPONENT_FROM_MERGED"
            else cfg.mod_skeleton_type)

    class Proxy:
        def __getattr__(self, name):
            if name == "mod_skeleton_type":
                return mode
            return getattr(cfg, name)

    return Proxy()


def _root_textures(root: Path, cfg: Any) -> Tuple[Any, ...]:
    from ..._wwmi_core.blender_export.texture_collector import Texture
    from .texture_delivery import build_delivery_inventory

    excluded = ({'af26db30', '1320a071', '10d7937d', '87505b2b',
                 'e5df00a8', 'ec2fecec', 'd313d349'}
                if bool(cfg.skip_known_cubemap_textures) else set())
    inventory = build_delivery_inventory(root, ())
    return tuple(
        Texture(
            hash=item.texture_hash or "",
            path=item.path,
            filename=item.name,
        )
        for item in inventory.root_files
        if item.texture_hash not in excluded
    )


def _ini_textures(textures: Sequence[Any]) -> Tuple[Any, ...]:
    by_hash = {}
    for texture in textures:
        texture_hash = str(texture.hash).strip().lower()
        if re.fullmatch(r"[0-9a-f]{8}", texture_hash):
            by_hash.setdefault(texture_hash, texture)
    return tuple(by_hash[key] for key in sorted(by_hash))


def _unit_has_geometry(unit: Any) -> bool:
    return bool(unit.buffers) and int(unit.merged_object.index_count) > 0


def _empty_override(name: str, vb_hash: str, first: int, count: int,
                    reason: str) -> IniSectionIR:
    return IniSectionIR(name, [
        f"hash = {vb_hash}",
        f"match_first_index = {first}",
        f"match_index_count = {count}",
        "$object_detected = 1",
        "if $mod_enabled",
        "    handling = skip",
        f"    ; Draw skipped: {reason}",
        "endif",
    ])


def _empty_body_ir(rendered: str, body: Any) -> CrossSceneIR:
    """Keep native common sections and replace geometry with empty skips."""
    source = CrossSceneIR.parse(rendered)
    exact = {
        "constants",
        "present",
        "commandlistregistermod",
        "commandlistprocesstoggles",
        "commandlisttriggerresourceoverrides",
    }
    prefixes = (
        "keyswap",
        "resourcemod",
        "resourcetexture",
        "textureoverridetexture",
    )
    sections = [
        section for section in source.sections
        if section.name.casefold() in exact
        or section.name.casefold().startswith(prefixes)
    ]
    result = CrossSceneIR(list(source.preamble), sections)
    present = result.get("Present")
    present.lines = [
        line for line in present.lines
        if not re.search(
            r"run\s*=\s*CommandList(?:InitializeBlendRemaps|UpdateMergedSkeleton)\s*$",
            line,
            re.I,
        )
    ]
    existing = {section.name.casefold() for section in result.sections}
    vb_hash = str(body.plan.manifest_entry.get("vb0_hash") or body.plan.ib_hash)
    components = (body.plan.manifest_entry.get("native_metadata") or {}).get(
        "components") or []
    for local_id, global_id in body.plan.component_map:
        component = components[local_id]
        name = f"TextureOverrideComponent{global_id}"
        if name.casefold() not in existing:
            result.sections.append(_empty_override(
                name,
                vb_hash,
                int(component.get("index_offset", 0)),
                int(component.get("index_count", 0)),
                "component excluded by ExportSelection",
            ))
            existing.add(name.casefold())
    return result


def _append_empty_unit_routes(ir: CrossSceneIR, unit: Any) -> None:
    """Emit missing native full/LOD skips for filtered unit components."""
    existing = {section.name.casefold() for section in ir.sections}
    empty = set(unit.plan.empty_local_components)
    metadata = unit.plan.manifest_entry.get("native_metadata") or {}
    components = metadata.get("components") or []
    vb_hash = str(unit.plan.manifest_entry.get("vb0_hash") or unit.plan.ib_hash)
    for local_id, global_id in unit.plan.component_map:
        if local_id not in empty:
            continue
        component = components[local_id]
        full_name = f"TextureOverrideComponent{global_id}{unit.plan.suffix}"
        if full_name.casefold() not in existing:
            ir.sections.append(_empty_override(
                full_name,
                vb_hash,
                int(component.get("index_offset", 0)),
                int(component.get("index_count", 0)),
                "component excluded by ExportSelection",
            ))
            existing.add(full_name.casefold())
        for level, lod in enumerate(component.get("lods") or [], start=1):
            lod_hash = str(lod.get("vb0_hash") or lod.get("lod_object_name") or "")
            if not lod_hash:
                continue
            lod_name = (
                f"TextureOverrideComponent{global_id}LOD{level}{unit.plan.suffix}")
            if lod_name.casefold() in existing:
                continue
            ir.sections.append(_empty_override(
                lod_name,
                lod_hash,
                int(lod.get("index_offset", 0)),
                int(lod.get("index_count", 0)),
                "LOD component excluded by ExportSelection",
            ))
            existing.add(lod_name.casefold())


def _append_child_globals(ir: CrossSceneIR, units: Sequence[Any], cfg: Any) -> None:
    constants = ir.get("Constants")
    existing = {line.strip().casefold() for line in constants.lines}

    def add(line: str) -> None:
        key = line.strip().casefold()
        if key not in existing:
            constants.lines.append(line)
            existing.add(key)

    active_units = [unit for unit in units if _unit_has_geometry(unit)]
    if any(getattr(unit.extracted_object, "velo_lods", None)
           for unit in active_units):
        add("global $lod_level = 0")
    formatter = None
    if bool(cfg.use_ini_toggles):
        from ..._wwmi_core.blender_export.text_formatter import TextFormatter
        formatter = TextFormatter()
    for unit in units[1:]:
        if not _unit_has_geometry(unit):
            continue
        suffix = unit.plan.suffix
        add(f"global $mesh_vertex_count{suffix} = {unit.merged_object.vertex_count}")
        add(f"global $shapekey_vertex_count{suffix} = "
            f"{unit.merged_object.shapekeys.vertex_count}")
        for index, batch in enumerate(unit.merged_object.shapekeys.batches):
            add(f"global $shapekey_vertex_offset_batch{index}{suffix} = "
                f"{batch.vertex_offset}")
            add(f"global $shapekey_vertex_count_batch{index}{suffix} = "
                f"{batch.vertex_count}")
        for _local_id, global_id in unit.plan.component_map:
            add(f"global $merge_status_id_{global_id} = 0")
        if formatter is not None:
            for component in unit.merged_object.components:
                for obj in component.objects:
                    add(f"global {formatter.format_ini_drawvar(obj.name)} = 1")


def _append_child_present(ir: CrossSceneIR, units: Sequence[Any], mode: str) -> None:
    present = ir.get("Present")
    for unit in units[1:]:
        if not _unit_has_geometry(unit):
            continue
        suffix = unit.plan.suffix
        lines = ["", f"; Cross-scene resource domain {unit.plan.resource_domain}",
                 "if $object_detected && $mod_enabled"]
        if unit.merged_object.blend_remap_count > 0:
            lines.append(f"    run = CommandListInitializeBlendRemaps{suffix}")
        if mode == "MERGED":
            lines.append(f"    run = CommandListUpdateMergedSkeleton{suffix}")
        lines.append("endif")
        present.lines.extend(lines)


def _buffer_bytes(buffer: Any) -> bytes:
    if isinstance(buffer, bytes):
        return buffer
    if isinstance(buffer, bytearray):
        return bytes(buffer)
    getter = getattr(buffer, "get_bytes", None)
    if getter is None:
        raise CrossSceneCompileError(f"unsupported buffer value {type(buffer)!r}")
    return getter()


def _collect_unit_buffers(units: Sequence[Any]) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    for unit in units:
        for name, buffer in unit.buffers.items():
            filename = f"{name}{unit.plan.suffix}.buf"
            if filename.casefold() in {value.casefold() for value in output}:
                raise CrossSceneCompileError(f"duplicate buffer filename {filename}")
            output[filename] = buffer
    return output


def _component_draws(body: Any, component_id: int) -> List[Any]:
    if component_id < 0 or component_id >= len(body.merged_object.components):
        return []
    return list(body.merged_object.components[component_id].objects)


def _fold_morph_buffers(body: Any, entry: Mapping[str, Any],
                        component_ids: Iterable[int]) -> Tuple[Dict[str, bytes], List[int]]:
    import numpy as np
    from .fold import _iter_key_entries

    shapes = (entry.get("native_metadata") or {}).get("shapekeys") or {}
    runtime = entry.get("runtime_morphs") or {}
    batches = shapes.get("batches") or []
    if not batches or not runtime:
        return {}, []
    required = ("ShapeKeyOffset", "ShapeKeyVertexId", "ShapeKeyVertexOffset", "Index")
    if any(name not in body.buffers for name in required):
        return {}, []

    body_offsets = np.frombuffer(
        _buffer_bytes(body.buffers["ShapeKeyOffset"]), dtype=np.uint32)
    body_vids = np.frombuffer(
        _buffer_bytes(body.buffers["ShapeKeyVertexId"]), dtype=np.uint32)
    offset_blob = _buffer_bytes(body.buffers["ShapeKeyVertexOffset"])
    stride = len(offset_blob) // max(1, len(body_vids))
    offset_rows = np.frombuffer(offset_blob, dtype=np.uint8).reshape(-1, stride)
    indices = np.frombuffer(_buffer_bytes(body.buffers["Index"]), dtype=np.uint32)
    ranges = []
    for component_id in component_ids:
        for obj in _component_draws(body, component_id):
            ranges.append(indices[obj.index_offset:obj.index_offset + obj.index_count])
    allowed = np.unique(np.concatenate(ranges)) if ranges else np.empty(0, np.uint32)
    canonical = {key: (lo, hi) for key, lo, hi in _iter_key_entries(body_offsets)}

    output_offsets: List[int] = []
    output_vids: List[int] = []
    output_rows = bytearray()
    batch_counts = []
    for batch_id in range(len(batches)):
        cumulative = 0
        batch_table = [0]
        for slot in range(127):
            local_id = batch_id * 127 + slot
            record = runtime.get(str(local_id)) or {}
            canonical_id = record.get("canonical_id")
            key_range = canonical.get(int(canonical_id)) if canonical_id is not None else None
            if key_range is not None and not record.get("empty"):
                vids = body_vids[key_range[0]:key_range[1]]
                keep = np.isin(vids, allowed)
                rows = offset_rows[key_range[0]:key_range[1]][keep]
                scale = float(record.get("scale") or 1.0)
                if abs(scale - 1.0) > 0.000001 and len(rows):
                    rows = rows.copy()
                    position = rows[:, :6].copy().view(np.float16) * np.float16(scale)
                    rows[:, :6] = position.view(np.uint8).reshape(len(rows), 6)
                kept_vids = vids[keep]
                if len(kept_vids) != len(set(int(value) for value in kept_vids)):
                    raise CrossSceneCompileError(
                        f"fold {entry['ib_hash']} ShapeKey {local_id} contains duplicate vertex ids")
                output_vids.extend(int(value) for value in kept_vids)
                output_rows.extend(rows.tobytes())
                cumulative += len(kept_vids)
            batch_table.append(cumulative)
        output_offsets.extend(batch_table)
        batch_counts.append(cumulative)

    tag = str(entry["ib_hash"])
    return {
        f"ShapeKeyOffset_{tag}.buf": np.asarray(output_offsets, np.uint32).tobytes(),
        f"ShapeKeyVertexId_{tag}.buf": np.asarray(output_vids, np.uint32).tobytes(),
        f"ShapeKeyVertexOffset_{tag}.buf": bytes(output_rows),
    }, batch_counts


def _fold_morph_ini(entry: Mapping[str, Any], batch_counts: Sequence[int],
                    unrestricted: bool, merged: bool) -> str:
    shapes = (entry.get("native_metadata") or {}).get("shapekeys") or {}
    if not batch_counts:
        return ""
    tag = str(entry["ib_hash"])
    batches = shapes.get("batches") or []
    lines = []
    offset = 0
    for index, count in enumerate(batch_counts):
        lines.extend([
            f";VELO_CONST global $shapekey_vertex_offset_batch{index}_{tag} = {offset}",
            f";VELO_CONST global $shapekey_vertex_count_batch{index}_{tag} = {count}",
        ])
        offset += count
    lines.extend([
        f"[TextureOverrideShapeKeyOffsets_{tag}]",
        f"hash = {shapes.get('offsets_hash', '')}",
        "match_priority = 0",
        "override_byte_stride = 24",
        "override_vertex_count = $mesh_vertex_count",
        "",
        f"[TextureOverrideShapeKeyScale_{tag}]",
        f"hash = {shapes.get('scale_hash', '')}",
        "match_priority = 0",
        "override_byte_stride = 4",
        "override_vertex_count = $mesh_vertex_count",
        "",
        f"[CommandListSetupShapeKeysBatch_{tag}]",
    ])
    for index, batch in enumerate(batches):
        lines.extend([
            f"$\\WWMIv1\\shapekey_checksum_batch{index} = {batch.get('checksum', 0)}",
            f"$\\WWMIv1\\shapekey_vertex_offset_original_batch{index} = "
            f"{batch.get('vertex_offset', 0)}",
            f"$\\WWMIv1\\shapekey_vertex_offset_custom_batch{index} = "
            f"$shapekey_vertex_offset_batch{index}_{tag}",
        ])
    lines.extend([
        f"cs-t33 = ResourceShapeKeyOffsetBuffer_{tag}",
        "cs-u5 = ResourceCustomShapeKeyValuesRW",
        "cs-u6 = ResourceShapeKeyCBRW",
        "run = CustomShader\\WWMIv1\\ShapeKeyBatchOverrider",
        "",
        f"[CommandListLoadShapeKeysBatch_{tag}]",
    ])
    for index, batch in enumerate(batches):
        lines.extend([
            f"$\\WWMIv1\\shapekey_dispatch_size_y_original_batch{index} = "
            f"{batch.get('dispatch_y', 0)}",
            f"$\\WWMIv1\\shapekey_vertex_count_batch{index} = "
            f"$shapekey_vertex_count_batch{index}_{tag}",
        ])
    lines.extend([
        f"cs-t0 = ResourceShapeKeyVertexIdBuffer_{tag}",
        f"cs-t1 = ResourceShapeKeyVertexOffsetBuffer_{tag}",
        "cs-u6 = ResourceShapeKeyCBRW",
        "run = CommandList\\WWMIv1\\LoadShapeKeysBatch",
        "",
        f"[TextureOverrideShapeKeyLoaderCallback_{tag}]",
        f"hash = {shapes.get('offsets_hash', '')}",
        "match_priority = 0",
        "if $mod_enabled",
        "    if cs == 3381.3333" + (" && ResourceMergedSkeleton !== null" if merged else ""),
        "        handling = skip",
        f"        run = CommandListSetupShapeKeysBatch_{tag}",
        f"        run = CommandListLoadShapeKeysBatch_{tag}",
        "    endif",
        "endif",
        "",
        f"[CommandListMultiplyShapeKeys_{tag}]",
        "$\\WWMIv1\\custom_vertex_count = $mesh_vertex_count",
        "run = CustomShader\\WWMIv1\\ShapeKeyMultiplier",
        "",
        f"[TextureOverrideShapeKeyMultiplierCallback_{tag}]",
        f"hash = {shapes.get('offsets_hash', '')}",
        "match_priority = 0",
        "if $mod_enabled",
        "    if cs == 3381.4444" + (" && ResourceMergedSkeleton !== null" if merged else ""),
        "        handling = skip",
        f"        run = CommandListMultiplyShapeKeys_{tag}",
    ])
    if unrestricted:
        lines.append("        run = CommandListApplyShapeKeys")
    lines.extend([
        "    endif",
        "endif",
        "",
        f"[ResourceShapeKeyOffsetBuffer_{tag}]",
        "type = Buffer",
        "format = DXGI_FORMAT_R32G32B32A32_UINT",
        "stride = 16",
        f"filename = Meshes/ShapeKeyOffset_{tag}.buf",
        "",
        f"[ResourceShapeKeyVertexIdBuffer_{tag}]",
        "type = Buffer",
        "format = DXGI_FORMAT_R32_UINT",
        "stride = 4",
        f"filename = Meshes/ShapeKeyVertexId_{tag}.buf",
        "",
        f"[ResourceShapeKeyVertexOffsetBuffer_{tag}]",
        "type = Buffer",
        "format = DXGI_FORMAT_R16_FLOAT",
        "stride = 2",
        f"filename = Meshes/ShapeKeyVertexOffset_{tag}.buf",
    ])
    return "\n".join(lines) + "\n"


def _append_fold_units(ir: CrossSceneIR, body: Any, manifest: Mapping[str, Any],
                       cfg: Any, buffers: Dict[str, Any],
                       section_routes: Dict[str, str]) -> None:
    merged = str(getattr(body.merged_object.skeleton_type, "name", "")) == "Merged"
    formatter = None
    if bool(cfg.use_ini_toggles):
        from ..._wwmi_core.blender_export.text_formatter import TextFormatter
        formatter = TextFormatter()
    constants = ir.get("Constants")
    for entry in manifest.get("runtime_ibs") or []:
        if entry.get("kind") != "fold":
            continue
        tag = str(entry["ib_hash"])
        component_map = {
            int(local): int(global_id)
            for local, global_id in (entry.get("component_map") or {}).items()
        }
        morph_buffers, batch_counts = _fold_morph_buffers(
            body, entry, component_map.values())
        buffers.update(morph_buffers)
        morph_ini = _fold_morph_ini(
            entry, batch_counts,
            bool(cfg.unrestricted_custom_shape_keys), merged)
        morph_constants = []
        morph_lines = []
        for line in morph_ini.splitlines():
            if line.startswith(";VELO_CONST "):
                morph_constants.append(line[len(";VELO_CONST "):])
            else:
                morph_lines.append(line)
        constants.lines.extend(morph_constants)
        if morph_lines:
            ir.extend_text("\n".join(morph_lines) + "\n")

        native_components = (entry.get("native_metadata") or {}).get("components") or []
        remaps = ((entry.get("fold_route") or {}).get("vg_remap") or {})
        for local_id, global_id in sorted(component_map.items()):
            native = native_components[local_id]
            draws = _component_draws(body, global_id)
            host_name = f"TextureOverride_FoldHost_{tag}_C{local_id}"
            draw_name = f"CommandListDrawComponent{global_id}_fold_{tag}"
            lines = [
                f"hash = {tag}",
                f"match_first_index = {int(native.get('index_offset', 0))}",
                f"match_index_count = {int(native.get('index_count', 0))}",
                "$object_detected = 1",
                "if $mod_enabled",
            ]
            if not draws:
                lines.extend([
                    "    handling = skip",
                    "    ; Draw skipped: component excluded by ExportSelection",
                    "endif",
                ])
                ir.sections.append(IniSectionIR(host_name, lines))
                continue
            if merged:
                base_meta = body.extracted_object.components[global_id]
                vg_offset = int(base_meta.vg_offset)
                vg_count = int(base_meta.vg_count)
                remap = remaps.get(str(global_id))
                if remap:
                    from .fold import _merge_offset_shift
                    shift, vg_count = _merge_offset_shift(remap)
                    vg_offset += shift
                lines.extend([
                    f"    if $merge_status_id_{global_id} != 2",
                    f"        $\\WWMIv1\\vg_offset = {vg_offset}",
                    f"        $\\WWMIv1\\vg_count = {vg_count}",
                    f"        $merge_status_id = $merge_status_id_{global_id}",
                    "        run = CommandListMergeSkeleton",
                    f"        $merge_status_id_{global_id} = $merge_status_id",
                    "    endif",
                    "    if ResourceMergedSkeleton !== null",
                    "        handling = skip",
                ])
                component = body.merged_object.components[global_id]
                if component.blend_remap_vg_count > 0:
                    lines.extend([
                        f"        ResourceBlendBufferOverride = ref "
                        f"ResourceRemappedBlendBufferComponent{global_id}",
                        f"        ResourceMergedSkeletonOverride = ref "
                        f"ResourceRemappedSkeletonComponent{global_id}",
                        f"        ResourceExtraMergedSkeletonOverride = ref "
                        f"ResourceExtraRemappedSkeletonComponent{global_id}",
                    ])
                lines.extend([f"        run = {draw_name}", "    endif", "endif"])
            else:
                lines.extend(["    handling = skip", f"    run = {draw_name}", "endif"])
            ir.sections.append(IniSectionIR(host_name, lines))

            shared_override = "CommandListOverrideSharedResources"
            remap = remaps.get(str(global_id))
            if remap and not merged:
                import numpy as np
                blend = np.frombuffer(_buffer_bytes(body.buffers["Blend"]), np.uint8)
                blend = blend.reshape(-1, 16).copy()
                index_data = np.frombuffer(_buffer_bytes(body.buffers["Index"]), np.uint32)
                vertex_parts = [
                    index_data[obj.index_offset:obj.index_offset + obj.index_count]
                    for obj in draws
                ]
                vertices = np.unique(np.concatenate(vertex_parts))
                table = {int(key): int(value) for key, value in remap.items()}
                for vertex in vertices:
                    for seat in range(8):
                        if blend[vertex, 8 + seat] and int(blend[vertex, seat]) in table:
                            blend[vertex, seat] = table[int(blend[vertex, seat])]
                filename = f"Blend_fold_{tag}_C{global_id}.buf"
                buffers[filename] = blend.tobytes()
                shared_override = f"CommandListOverrideSharedResources_fold_{tag}_C{global_id}"
                ir.sections.append(IniSectionIR(shared_override, [
                    "run = CommandListOverrideSharedResources",
                    f"vb4 = ResourceBlendBuffer_fold_{tag}_C{global_id}",
                ]))
                ir.sections.append(IniSectionIR(
                    f"ResourceBlendBuffer_fold_{tag}_C{global_id}", [
                        "type = Buffer", "format = DXGI_FORMAT_R8_UINT", "stride = 16",
                        f"filename = Meshes/{filename}",
                    ]))
            draw_lines = [
                "run = CommandListTriggerResourceOverrides",
                f"run = {shared_override}",
            ]
            for obj in draws:
                draw_lines.append(f"; Draw {obj.name}")
                if formatter is not None:
                    draw_lines.extend([
                        f"if {formatter.format_ini_drawvar(obj.name)}",
                        f"    drawindexed = {obj.index_count}, {obj.index_offset}, 0",
                        "endif",
                    ])
                else:
                    draw_lines.append(
                        f"drawindexed = {obj.index_count}, {obj.index_offset}, 0")
            draw_lines.append("run = CommandListCleanupSharedResources")
            ir.sections.append(IniSectionIR(draw_name, draw_lines))
            section_routes[draw_name.casefold()] = tag


def _service_slot_drift_hashes_from_usage(usage: Mapping[str, Any],
                                          form_entries: Iterable[Any]) -> set[str]:
    slots_by_hash: Dict[str, set[Tuple[int, ...]]] = {}

    def accumulate(components: Any) -> None:
        if not isinstance(components, Mapping):
            return
        for comp_pairs in components.values():
            if not isinstance(comp_pairs, Mapping):
                continue
            for ps_map in comp_pairs.values():
                if not isinstance(ps_map, Mapping):
                    continue
                for pair_map in ps_map.values():
                    if not isinstance(pair_map, Mapping):
                        continue
                    row_slots: Dict[str, set[int]] = {}
                    for slot_name, record in pair_map.items():
                        match = re.fullmatch(r"ps-t(\d+)", str(slot_name))
                        if not match or int(match.group(1)) < 5:
                            continue
                        tex_hash = (record.get("hash")
                                    if isinstance(record, Mapping) else record)
                        if tex_hash:
                            row_slots.setdefault(
                                str(tex_hash).lower(), set()).add(int(match.group(1)))
                    for tex_hash, slots in row_slots.items():
                        slots_by_hash.setdefault(tex_hash, set()).add(
                            tuple(sorted(slots)))

    accumulate(usage)
    for entry in form_entries:
        if isinstance(entry, Mapping):
            accumulate(entry.get("components") or {})
    return {
        tex_hash for tex_hash, slot_sets in slots_by_hash.items()
        if len(slot_sets) > 1
        and len({slot for slots in slot_sets for slot in slots}) > 1
    }


def _slot_plan(context: Any, root: Path, cfg: Any, selection: Any,
               manifest: Mapping[str, Any], textures: Sequence[Any]) -> Any:
    if (not bool(getattr(cfg, "velo_slot_style_textures", False))
            or not textures or not selection.objects):
        return None
    from ..slot_textures import dds_meta, generator, hook, stu_metadata
    usage = json.loads((root / "ShaderTextureUsage.json").read_text(encoding="utf-8"))
    freshness = []
    pass_depth = []
    forms, texture_info, warnings = generator.load_forms_from_usage(
        usage, freshness_out=freshness, pass_depth_out=pass_depth)
    if not texture_info:
        for texture in textures:
            meta = dds_meta.read_dds_meta(texture.path)
            if meta is not None and meta.format:
                texture_info[texture.hash] = {
                    "format": meta.format, "width": meta.width, "height": meta.height}
    route_context = {
        str(entry["ib_hash"]).lower(): {
            int(value) for value in (entry.get("component_map") or {}).values()
        }
        for entry in manifest.get("runtime_ibs") or []
    }
    local_audit = generator.build_local_discriminator_audit_from_usage(
        usage, route_context=route_context)
    selected = set(selection.selected_component_ids)
    if selection.slot_eligible_components is not None:
        selected.intersection_update(selection.slot_eligible_components)
    component_ranges = {
        component_id: (int(component.get("index_offset", 0)),
                       int(component.get("index_count", 0)))
        for component_id, component in enumerate(
            manifest["base"]["native_metadata"].get("components") or [])
    }
    for entry in manifest.get("runtime_ibs") or []:
        components = (entry.get("native_metadata") or {}).get("components") or []
        for local_raw, global_raw in (entry.get("component_map") or {}).items():
            local_id, global_id = int(local_raw), int(global_raw)
            component = components[local_id]
            component_ranges.setdefault(
                global_id,
                (int(component.get("index_offset", 0)),
                 int(component.get("index_count", 0))),
            )
    lod_ranges: Dict[int, Dict[int, Tuple[int, int]]] = {}
    for global_id, component in enumerate(
            manifest["base"]["native_metadata"].get("components") or []):
        for level, lod in enumerate(component.get("lods") or [], start=1):
            lod_ranges.setdefault(level, {})[global_id] = (
                int(lod.get("index_offset", 0)), int(lod.get("index_count", 0)))
    anchors = []
    slot_cfg = getattr(getattr(context, "scene", None), "vtww_slot_settings", None)
    formid_aux = bool(slot_cfg and getattr(slot_cfg, "formid_auxiliary_gate", False))
    if formid_aux:
        anchors = hook._parse_form_anchors(context, forms, warnings)
        if not anchors:
            labels = {str(label).strip().lower(): index
                      for index, (label, _form) in enumerate(forms, start=1)}
            for label, anchor_hash in stu_metadata.collect_anchor_pairs(usage):
                form_id = labels.get(str(label).strip().lower())
                if form_id is not None:
                    anchors.append((anchor_hash, form_id))
    resources = [(texture.hash, f"ResourceTexture{index}")
                 for index, texture in enumerate(textures)]
    plan_args = dict(
        component_ranges=component_ranges,
        lod_ranges=lod_ranges,
        freshness=freshness,
        pass_depth=pass_depth,
        manual_anchors=anchors,
        local_form_discriminator=True,
        local_discriminator_audit=local_audit,
        formid_auxiliary_anchors=anchors,
        volatile_assignment_hashes=_service_slot_drift_hashes_from_usage(
            usage, stu_metadata.form_entries(usage)),
        texture_hash_allowlist={texture.hash for texture in textures},
    )
    root_catalog_fallback = set()
    try:
        plan = generator.build_plan(
            forms, resources, texture_info, warnings,
            slot_eligible_components=selected,
            **plan_args,
        )
    except generator.LocalDiscriminatorConflict as exc:
        root_catalog_fallback.update(exc.components)
        selected.difference_update(exc.components)
        plan = generator.build_plan(
            forms, resources, texture_info, warnings,
            slot_eligible_components=selected,
            **plan_args,
        )
    plan.root_catalog_hash_fallback_components = tuple(
        sorted(root_catalog_fallback))
    issues = (list(getattr(plan, "unsafe_fallback", None) or [])
              + list(getattr(plan, "slot_unrepresented", None) or []))
    if issues:
        raise CrossSceneCompileError(generator._format_slot_unrepresented(issues))
    return plan


def _clone_route_format_tags(text: str, manifest: Mapping[str, Any], plan: Any) -> str:
    if plan is None:
        return text
    ir = CrossSceneIR.parse(text)
    source_sections = list(ir.sections)
    format_sources: Dict[int, List[Tuple[str, IniSectionIR]]] = {}
    for section in source_sections:
        match = re.fullmatch(r"TextureOverrideComponent(\d+)(.+)", section.name)
        if match is None:
            continue
        if not any(line.strip().lower().startswith("match_format")
                   for line in section.lines):
            continue
        format_sources.setdefault(int(match.group(1)), []).append(
            (match.group(2), section))

    def clone(source: IniSectionIR, name: str, first: int, count: int) -> None:
        lines = []
        for line in source.lines:
            stripped = line.strip().lower()
            if stripped.startswith("match_first_index"):
                lines.append(f"match_first_index = {first}")
            elif stripped.startswith("match_index_count"):
                lines.append(f"match_index_count = {count}")
            else:
                lines.append(line)
        ir.sections.append(IniSectionIR(name, lines))

    for entry in manifest.get("runtime_ibs") or []:
        tag = str(entry["ib_hash"])
        metadata = entry.get("native_metadata") or {}
        components = metadata.get("components") or []
        for local_raw, global_raw in (entry.get("component_map") or {}).items():
            local_id, global_id = int(local_raw), int(global_raw)
            route_lists = getattr(plan, "component_route_lists", None) or {}
            component_lists = getattr(plan, "component_list_names", None) or {}
            if global_id not in route_lists and global_id not in component_lists:
                continue
            component = components[local_id]
            for suffix, section in format_sources.get(global_id, []):
                full_name = (
                    f"TextureOverrideRouteFormat_{tag}_C{local_id}_Base_{suffix}")
                clone(
                    section,
                    full_name,
                    int(component.get("index_offset", 0)),
                    int(component.get("index_count", 0)),
                )
                for level, lod in enumerate(component.get("lods") or [], start=1):
                    clone(
                        section,
                        f"TextureOverrideRouteFormat_{tag}_C{local_id}_L{level}_{suffix}",
                        int(lod.get("index_offset", 0)),
                        int(lod.get("index_count", 0)),
                    )
    ir.assert_unique()
    return ir.render()


def _add_scoped_setter_aliases(plan: Any,
                               section_routes: Mapping[str, str]) -> None:
    additions = []
    seen = {str(name).casefold() for name in plan.restore_contract}
    for section_name, route in section_routes.items():
        suffix_match = re.search(r"(_ib\d+)$", section_name, re.I)
        component_match = re.search(r"component(\d+)", section_name, re.I)
        if suffix_match is None or component_match is None:
            continue
        component_id = int(component_match.group(1))
        route_lists = (plan.component_route_lists or {}).get(component_id, {})
        setter = route_lists.get(str(route).lower())
        if setter is None:
            setter = plan.component_list_names.get(component_id)
        if setter is None:
            continue
        alias = setter + suffix_match.group(1)
        if alias.casefold() in seen:
            continue
        seen.add(alias.casefold())
        additions.extend(["", f"[{alias}]", f"run = {setter}"])
        policy = plan.restore_contract.get(setter)
        if policy is not None:
            plan.restore_contract[alias] = dict(policy)
        branch = plan.branch_contract.get(setter)
        if branch is not None:
            plan.branch_contract[alias] = dict(branch)
    if additions:
        plan.block_text = plan.block_text.rstrip() + "\n" + "\n".join(additions) + "\n"


def _validate_compiled(text: str, buffers: Mapping[str, Any],
                       textures: Sequence[Any], logo: Optional[Path]) -> Dict[str, Any]:
    ir = CrossSceneIR.parse(text)
    ir.assert_unique()
    section_names = {section.name.casefold() for section in ir.sections}
    dangling = []
    for reference in _LOCAL_REF_RE.findall(text):
        if "\\" in reference:
            continue
        if reference.casefold() not in section_names:
            dangling.append(reference)
    available = {f"Meshes/{name}".casefold() for name in buffers}
    available.update(f"Textures/{texture.filename}".casefold()
                     for texture in textures)
    if logo is not None and logo.is_file():
        available.add("textures/logo.dds")
    missing = sorted({name for name in _FILENAME_RE.findall(text)
                      if name.casefold() not in available})
    if dangling or missing:
        raise CrossSceneCompileError(
            "compiled INI is not closed: dangling=%s missing=%s"
            % (sorted(set(dangling)), missing))
    return {
        "sections": len(ir.sections),
        "dangling": [],
        "missing": [],
        "sound": True,
    }


def compile_cross_scene(units: Sequence[Any], manifest: Mapping[str, Any],
                        settings: CompilerSettings) -> CompiledMod:
    """Compile final INI, buffers and texture delivery directly from memory units."""
    cfg = settings.cfg
    if bool(getattr(cfg, "use_custom_template", False)):
        raise CrossSceneCompileError(
            "跨场景导出不支持任意自定义 Jinja INI 模板；请关闭“使用自定义模板”后重试。")
    if bool(getattr(cfg, "custom_template_live_update", False)):
        raise CrossSceneCompileError(
            "跨场景导出不支持实时更新 INI 模板；请关闭实时更新后重试。")
    if not units or units[0].plan.kind != "body":
        raise CrossSceneCompileError("first ExportUnit must be the body resource domain")
    root = Path(settings.root)
    if bool(getattr(cfg, "partial_export", False)):
        buffers = _collect_unit_buffers(units)
        return CompiledMod(
            ini_text="",
            buffers=buffers,
            textures=(),
            report={
                "roles": ["body"] + [str(entry["ib_hash"])
                                      for entry in manifest.get("runtime_ibs") or []],
                "ib_count": 1 + len(manifest.get("runtime_ibs") or []),
                "slot_style": False,
                "no_child_exports": True,
                "sections": 0,
                "dangling": [],
                "missing": [],
                "sound": True,
            },
        )
    cfg_view = _normalized_cfg(cfg)
    mode = cfg_view.mod_skeleton_type
    textures = (_root_textures(root, cfg)
                if settings.selection.objects else ())
    ini_textures = _ini_textures(textures)
    try:
        from ..._wwmi_core.migoto_io.blender_interface.utility import resolve_path
        logo_source = resolve_path(cfg.mod_logo)
    except Exception:
        logo_source = Path(str(getattr(cfg, "mod_logo", "")))

    template = _template_source(mode, bool(cfg.comment_ini))
    body = units[0]
    body_text = _render(
        template, _maker(body, cfg_view, ini_textures, logo_source))
    ir = (CrossSceneIR.parse(body_text) if _unit_has_geometry(body)
          else _empty_body_ir(body_text, body))
    section_routes: Dict[str, str] = {}
    for section in ir.sections:
        if re.search(r"CommandListDrawComponent\d+$", section.name, re.I):
            section_routes[section.name.casefold()] = "base"

    fragment_source = _unit_fragment_source(template, mode)
    for unit in units[1:]:
        if not _unit_has_geometry(unit):
            continue
        specialized = specialize_unit_template(fragment_source, unit.plan.suffix)
        component_ids = [global_id for _local_id, global_id in unit.plan.component_map]
        fragment = _render(
            specialized,
            _maker(unit, cfg_view, (), logo_source),
            component_ids=component_ids,
        )
        fragment_ir = CrossSceneIR.parse(fragment)
        fragment_ir.sections = [
            section for section in fragment_ir.sections
            if section.name.casefold() != "commandlisttriggerresourceoverrides"
        ]
        for section in fragment_ir.sections:
            match = re.search(r"CommandListDrawComponent(\d+)", section.name, re.I)
            if match:
                section_routes[section.name.casefold()] = unit.plan.ib_hash.lower()
        ir.sections.extend(fragment_ir.sections)

    for unit in units:
        _append_empty_unit_routes(ir, unit)

    _append_child_globals(ir, units, cfg)
    _append_child_present(ir, units, mode)
    buffers = _collect_unit_buffers(units)
    _append_fold_units(ir, body, manifest, cfg, buffers, section_routes)
    ir.assert_unique()
    text = ir.render()

    plan = _slot_plan(
        settings.context, root, cfg, settings.selection, manifest, ini_textures)
    if plan is not None:
        from ..slot_textures import transform
        _add_scoped_setter_aliases(plan, section_routes)
        text = transform.apply(text, plan, section_routes=section_routes)
        text = _clone_route_format_tags(text, manifest, plan)

    from ..._wwmi_core.blender_export.ini_maker import IniMaker
    text = IniMaker.with_checksum(text)
    report = _validate_compiled(text, buffers, textures, logo_source)
    report.update({
        "roles": ["body"] + [str(entry["ib_hash"])
                              for entry in manifest.get("runtime_ibs") or []],
        "ib_count": 1 + len(manifest.get("runtime_ibs") or []),
        "slot_style": plan is not None,
        "no_child_exports": True,
    })
    if plan is not None:
        report.update({
            "slot_branch_expectations": dict(plan.branch_contract),
            "slot_restore_contract": dict(plan.restore_contract),
            "slot_component_route_lists": dict(plan.component_route_lists),
            "slot_root_catalog_hash_fallback_components": list(
                getattr(plan, "root_catalog_hash_fallback_components", ())),
            "tex_slot": sorted(plan.covered_resource_indices),
            "tex_blindzone": list(plan.blind_zone),
        })
    return CompiledMod(text, buffers, textures, plan, section_routes, report)


def _safe_meshes_path(output: Path) -> Path:
    output = output.resolve()
    meshes = (output / "Meshes").resolve()
    if meshes.parent != output:
        raise CrossSceneCompileError("refusing to rebuild Meshes outside output root")
    return meshes


def _write_ini_with_backup(path: Path, text: str) -> None:
    from ..._wwmi_core.blender_export.ini_maker import IniMaker
    if path.is_file() and IniMaker.is_ini_edited(path):
        stamp = datetime.now().strftime("%Y-%m-%d %H-%M-%S")
        path.rename(path.with_name(f"{path.name} {stamp}.BAK"))
    path.write_text(text, encoding="utf-8")


def write_compiled_mod(compiled: CompiledMod, output: Path | str,
                       gates: OutputGates) -> Dict[str, Any]:
    """Apply native output gates after compilation; never creates child outputs."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    meshes = _safe_meshes_path(output)
    if not gates.partial_export:
        if meshes.exists():
            shutil.rmtree(meshes)
        meshes.mkdir(parents=True, exist_ok=True)
    else:
        meshes.mkdir(parents=True, exist_ok=True)
    for filename, buffer in compiled.buffers.items():
        (meshes / filename).write_bytes(_buffer_bytes(buffer))

    ini_written = False
    textures_written = False
    if not gates.partial_export:
        if gates.copy_textures:
            texture_dir = output / "Textures"
            texture_dir.mkdir(parents=True, exist_ok=True)
            for texture in compiled.textures:
                target = texture_dir / texture.filename
                if not target.exists():
                    shutil.copy2(texture.path, target)
            if gates.logo_source is not None and gates.logo_source.is_file():
                logo = texture_dir / "Logo.dds"
                if not logo.exists():
                    shutil.copy2(gates.logo_source, logo)
            textures_written = True
        if gates.write_ini:
            _write_ini_with_backup(output / "mod.ini", compiled.ini_text)
            ini_written = True
    report = dict(compiled.report)
    report.update({
        "final_ini_written": ini_written,
        "final_textures_written": textures_written,
        "buffers_written": sorted(compiled.buffers),
        "root_dds_files": len(compiled.textures),
        "sound": bool(compiled.report.get("sound", True)),
    })
    return report
