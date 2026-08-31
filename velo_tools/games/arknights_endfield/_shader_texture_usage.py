"""EFMI driver-layer patch for writing ``ShaderTextureUsage.json``.

The vendored EFMI extractor already retains every filtered texture resource in
``TexturesDescriptor``.  This patch wraps the final exporter, lets the original
pipeline write its normal artifacts first, then serializes the same surviving
resources into the WWMI-compatible nested STU shape:

    Component -> vertex shader -> pixel shader -> ps-tN -> texture record

When ``log.txt`` exposes usable ``PSSetShaderResources`` evidence, schema v4
marks fresh slots and the default-on Dirty Slot filter removes inherited stale
records from STU, TextureUsage.json, texture ownership, and extracted files.
Without usable evidence the original output is retained rather than guessed.

The vendored core remains untouched, and install/remove are idempotent.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from ._efmi_core.migoto_io.object_extractor.migoto_object import (
    migoto_object_exporter as _exporter_module,
)
from ._efmi_core.migoto_io.object_extractor.migoto_object.textures_descriptor import (
    TextureFilter,
)
from ._efmi_core.migoto_io.migoto_model.types import ShaderType
from . import _log_freshness


_INSTALLED = False
_ORIG_EXPORT = None
_ASSET_PATH_MANIFEST = "TextureAssetManifest.jsonl"


class AssetPathManifestError(ValueError):
    pass


def _skip_dirty_slot_enabled() -> bool:
    try:
        import bpy  # type: ignore
        cfg = getattr(getattr(bpy.context, "scene", None), "VTEF_settings", None)
        if cfg is None or not hasattr(cfg, "skip_slot_residual_textures"):
            return True
        return bool(cfg.skip_slot_residual_textures)
    except Exception:
        return True


def _manifest_root(texture) -> Path | None:
    raw_source_path = getattr(texture, "bin_path", None)
    if not raw_source_path:
        return None
    source_path = Path(raw_source_path)
    for parent in (source_path.parent, *source_path.parents):
        if (parent / _ASSET_PATH_MANIFEST).is_file():
            return parent
    return None


def _load_asset_paths(dump_root: Path | None) -> dict[str, str]:
    if dump_root is None:
        return {}
    manifest_path = dump_root / _ASSET_PATH_MANIFEST
    if not manifest_path.is_file():
        return {}
    result = {}
    for line_number, line in enumerate(
            manifest_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AssetPathManifestError(
                f"{_ASSET_PATH_MANIFEST}:{line_number} is not valid JSON"
            ) from exc
        dump_name = str(record.get("dump_name") or "").strip()
        asset_path = str(record.get("asset_path") or "").strip()
        if not dump_name or not asset_path:
            continue
        key = Path(dump_name).stem.casefold()
        previous = result.get(key)
        if previous is not None and previous != asset_path:
            raise AssetPathManifestError(
                f"{_ASSET_PATH_MANIFEST} maps {Path(dump_name).stem} to "
                "conflicting asset paths"
            )
        result[key] = asset_path
    return result


def _shader_keys(texture) -> tuple[str, str]:
    usage_descriptor = getattr(texture, "usage_descriptor", None)
    shaders = getattr(usage_descriptor, "shaders", {}) or {}
    vs_hash = shaders.get(ShaderType.Vertex)
    ps_hash = shaders.get(ShaderType.Pixel)
    return (
        f"vs={vs_hash}" if vs_hash else "vs=?",
        f"ps={ps_hash}" if ps_hash else "ps=?",
    )


def _texture_filename(
        texture_hash: str,
        texture,
        textures_descriptor,
        component_ids=None,
) -> str:
    if component_ids is None:
        component_ids = textures_descriptor.components_usage.get(texture_hash, ())
    component_ids = sorted(set(map(int, component_ids)))
    filename = f"Components-{'-'.join(map(str, component_ids))} t={texture_hash}"
    data_format = getattr(texture.data_descriptor, "data_format", None)
    if data_format:
        format_name = str(data_format)
        encoding = format_name.split("_")[0]
        colorspace = "sRGB" if format_name.endswith("SRGB") else "Linear"
        filename += f" {encoding}-{colorspace}"
    return filename + texture.bin_path_deduped.suffix


def _texture_size(texture) -> tuple[int, int]:
    path = Path(texture.bin_path_deduped)
    try:
        if path.suffix.lower() == ".dds":
            return TextureFilter.get_dds_dimensions(path)
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            return TextureFilter.get_jpg_dimensions(path)
    except (OSError, ValueError):
        pass
    return 0, 0


def build_shader_texture_usage(
        migoto_object,
        textures_descriptor,
        output_folder: str | Path | None = None,
) -> OrderedDict:
    usage = OrderedDict()
    output_folder = Path(output_folder) if output_folder is not None else None
    manifest_cache = {}
    asset_paths_by_hash = {}
    retained_textures = list(textures_descriptor.textures.values())
    first_texture = next(iter(retained_textures), None)
    dump_root = _log_freshness.find_dump_root(
        getattr(first_texture, "bin_path", None)) if first_texture else None
    evidence = (
        _log_freshness.parse_log_freshness(dump_root)
        if dump_root is not None else None
    )
    skip_dirty_slot = _skip_dirty_slot_enabled()
    if evidence is not None:
        usage["version"] = 4
    elif retained_textures and skip_dirty_slot:
        print(
            "[velo efmi-stu] Skip Dirty Slot is enabled but no usable "
            "log.txt freshness evidence was found; legacy STU kept unfiltered"
        )
    skipped_dirty_slots = 0
    for component_id, component in enumerate(migoto_object.components):
        seats = {}
        pair_depth_only = {}
        for slot, textures in component.textures.items():
            slot_key = str(slot)
            if not slot_key.startswith("ps-t"):
                continue
            for texture in textures:
                retained = textures_descriptor.textures.get(texture.hash)
                if retained is None or retained.bin_path_deduped != texture.bin_path_deduped:
                    continue
                vs_key, ps_key = _shader_keys(texture)
                width, height = _texture_size(texture)
                data_format = getattr(texture.data_descriptor, "data_format", None)
                filename = _texture_filename(
                    texture.hash, texture, textures_descriptor)
                record = OrderedDict((
                    ("filename", filename),
                    ("hash", texture.hash),
                    ("format", str(data_format) if data_format else ""),
                    ("width", width),
                    ("height", height),
                ))
                manifest_root = _manifest_root(texture)
                if manifest_root not in manifest_cache:
                    manifest_cache[manifest_root] = _load_asset_paths(manifest_root)
                source_path = Path(getattr(texture, "bin_path", "") or "")
                asset_path = manifest_cache[manifest_root].get(
                    source_path.stem.casefold(), "")
                if asset_path:
                    previous = asset_paths_by_hash.get(texture.hash)
                    if previous is not None and previous != asset_path:
                        raise AssetPathManifestError(
                            f"Texture Hash {texture.hash} maps to conflicting "
                            "Unreal asset paths in this extraction"
                        )
                    asset_paths_by_hash[texture.hash] = asset_path
                    if output_folder is not None and (output_folder / filename).is_file():
                        record["asset_path"] = asset_path
                usage_descriptor = getattr(texture, "usage_descriptor", None)
                call_id = getattr(usage_descriptor, "call_id", None)
                fresh = None
                if evidence is not None:
                    fresh = bool(
                        call_id is not None
                        and _log_freshness.slot_is_fresh(
                            evidence,
                            call_id,
                            slot.slot_id,
                            texture.hash,
                            getattr(usage_descriptor, "original_hash", None),
                        )
                    )
                    color_rt = _log_freshness.call_has_color_rt(evidence, call_id)
                    depth_only = color_rt is False
                    previous_depth = pair_depth_only.get((vs_key, ps_key))
                    pair_depth_only[(vs_key, ps_key)] = (
                        depth_only
                        if previous_depth is None
                        else previous_depth and depth_only
                    )
                    if skip_dirty_slot and not fresh:
                        skipped_dirty_slots += 1
                        continue
                slot_map = seats.setdefault((vs_key, ps_key), {})
                current = slot_map.get(slot_key)
                if current is None or fresh is None:
                    slot_map[slot_key] = [record, fresh]
                elif current[0]["hash"] == record["hash"]:
                    current[1] = bool(current[1]) or fresh
                elif fresh or not current[1]:
                    slot_map[slot_key] = [record, fresh]

        component_out = OrderedDict()
        for vs_key in sorted({key[0] for key in seats}):
            vs_out = OrderedDict()
            for ps_key in sorted(key[1] for key in seats if key[0] == vs_key):
                ps_out = OrderedDict()
                for slot_key in sorted(seats[(vs_key, ps_key)]):
                    record, fresh = seats[(vs_key, ps_key)][slot_key]
                    if fresh is not None:
                        record = OrderedDict(record)
                        record["fresh"] = bool(fresh)
                    ps_out[slot_key] = record
                if evidence is not None:
                    ps_out["depth_only"] = bool(
                        pair_depth_only.get((vs_key, ps_key), False)
                    )
                vs_out[ps_key] = ps_out
            component_out[vs_key] = vs_out
        component_out["form_component_mode"] = "single"
        usage[f"Component {component_id}"] = component_out
    if skipped_dirty_slots:
        print(
            f"[velo efmi-stu] Skip Dirty Slot removed {skipped_dirty_slots} "
            "stale-inherited slot record(s)"
        )
    return usage


def _iter_texture_records(usage):
    for component_key, component in usage.items():
        if not str(component_key).startswith("Component ") or not isinstance(component, dict):
            continue
        component_id = str(component_key).split()[-1]
        for vs_key, vs_block in component.items():
            if not str(vs_key).startswith("vs=") or not isinstance(vs_block, dict):
                continue
            for ps_key, ps_block in vs_block.items():
                if not str(ps_key).startswith("ps=") or not isinstance(ps_block, dict):
                    continue
                for slot_key, record in ps_block.items():
                    if str(slot_key).startswith("ps-t") and isinstance(record, dict):
                        yield component_id, vs_key, ps_key, slot_key, record


def _synchronize_filtered_outputs(folder_path, usage, textures_descriptor) -> None:
    if not _skip_dirty_slot_enabled() or usage.get("version") != 4:
        return
    folder_path = Path(folder_path)
    allowed_usage = {}
    emitted_hash_components = {}
    for component_id, vs_key, ps_key, slot_key, record in _iter_texture_records(usage):
        texture_hash = str(record.get("hash") or "")
        if not texture_hash:
            continue
        component_key = f"Component {component_id}"
        allowed_usage.setdefault(component_key, {}).setdefault(slot_key, set()).add(
            (texture_hash, vs_key, ps_key)
        )
        emitted_hash_components.setdefault(texture_hash, set()).add(component_id)

    texture_usage_path = folder_path / "TextureUsage.json"
    try:
        texture_usage = json.loads(texture_usage_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        texture_usage = None
    if isinstance(texture_usage, dict):
        for component_key, component_block in texture_usage.items():
            if not isinstance(component_block, dict):
                continue
            component_allowed = allowed_usage.get(component_key, {})
            for slot_key in list(component_block):
                entries = component_block.get(slot_key)
                if not isinstance(entries, list):
                    continue
                allowed = component_allowed.get(slot_key, set())
                kept = []
                for entry in entries:
                    parts = str(entry).split("-")
                    if any(
                            parts and parts[0] == texture_hash
                            and vs_key in parts and ps_key in parts
                            for texture_hash, vs_key, ps_key in allowed):
                        kept.append(entry)
                if kept:
                    component_block[slot_key] = kept
                else:
                    del component_block[slot_key]
        texture_usage_path.write_text(
            json.dumps(texture_usage, indent=4), encoding="utf-8"
        )

    final_filenames = {}
    for texture_hash, texture in textures_descriptor.textures.items():
        stock_filename = _texture_filename(
            texture_hash, texture, textures_descriptor
        )
        stock_path = folder_path / stock_filename
        emitted_components = emitted_hash_components.get(texture_hash, set())
        if not emitted_components:
            stock_path.unlink(missing_ok=True)
            continue
        final_filename = _texture_filename(
            texture_hash,
            texture,
            textures_descriptor,
            component_ids=emitted_components,
        )
        final_filenames[texture_hash] = final_filename
        final_path = folder_path / final_filename
        if stock_path != final_path and stock_path.is_file():
            stock_path.replace(final_path)

    for _, _, _, _, record in _iter_texture_records(usage):
        final_filename = final_filenames.get(record.get("hash"))
        if final_filename:
            record["filename"] = final_filename


def _wrapped_export(self, folder_path, migoto_object, textures_descriptor) -> None:
    _ORIG_EXPORT(self, folder_path, migoto_object, textures_descriptor)
    usage = build_shader_texture_usage(
        migoto_object, textures_descriptor, output_folder=folder_path)
    _synchronize_filtered_outputs(folder_path, usage, textures_descriptor)
    output_path = Path(folder_path) / "ShaderTextureUsage.json"
    output_path.write_text(json.dumps(usage, indent=4), encoding="utf-8")


def install_patches() -> None:
    global _INSTALLED, _ORIG_EXPORT
    if _INSTALLED:
        return
    _ORIG_EXPORT = _exporter_module.ObjectExporter.export
    _exporter_module.ObjectExporter.export = _wrapped_export
    _INSTALLED = True


def uninstall_patches() -> None:
    global _INSTALLED, _ORIG_EXPORT
    if not _INSTALLED:
        return
    _exporter_module.ObjectExporter.export = _ORIG_EXPORT
    _ORIG_EXPORT = None
    _INSTALLED = False
