"""Coordinate the schema-v3 WWMI cross-scene direct compiler."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional


def build_cross_scene_mod(
        context: Any,
        cfg: Any,
        base_collection: Any,
        merged_folder: str | Path,
        out_folder: str | Path,
        hole: bool = True,
        workdir: Optional[str | Path] = None,
        hole_frac: int = 35,
        excluded_buffers: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Compile and write one cross-scene mod without child export artifacts."""
    from ..._wwmi_core.migoto_io.blender_interface.utility import resolve_path
    from ..slot_textures import hook as slot_hook
    from .compiler import (
        CompilerSettings,
        OutputGates,
        compile_cross_scene,
        write_compiled_mod,
    )
    from .export_units import build_export_units
    from .manifest import load_cross_scene_root
    from .selection import capture_export_selection

    del workdir
    root_path = Path(merged_folder)
    root = load_cross_scene_root(root_path)
    selection = capture_export_selection(
        context,
        cfg,
        base_collection,
        slot_eligible_components=slot_hook.read_global_eligible(context),
    )
    units = build_export_units(
        context,
        cfg,
        selection,
        root,
        excluded_buffers=tuple(excluded_buffers or ()),
        hole=bool(hole),
        hole_frac=int(hole_frac),
    )
    compiled = compile_cross_scene(
        units,
        root,
        CompilerSettings(
            context=context,
            cfg=cfg,
            root=root_path,
            selection=selection,
        ),
    )
    try:
        logo_source = resolve_path(cfg.mod_logo)
    except Exception:
        logo_source = Path(str(getattr(cfg, "mod_logo", "")))
    report = write_compiled_mod(
        compiled,
        out_folder,
        OutputGates(
            partial_export=bool(cfg.partial_export),
            write_ini=bool(cfg.write_ini),
            copy_textures=bool(getattr(cfg, "copy_textures", True)),
            logo_source=logo_source,
        ),
    )
    report["selection"] = {
        "objects": [item.name for item in selection.objects],
        "components": sorted(selection.selected_component_ids),
        "ignore_hidden_objects": selection.ignore_hidden_objects,
        "ignore_hidden_collections": selection.ignore_hidden_collections,
        "ignore_nested_collections": selection.ignore_nested_collections,
    }
    report["export_units"] = [
        {
            "identity": unit.plan.identity,
            "resource_domain": unit.plan.resource_domain,
            "components": list(unit.plan.component_map),
            "empty_local_components": list(unit.plan.empty_local_components),
        }
        for unit in units
    ]
    audit_skipped = not report.get("final_ini_written", False)
    report["static_audit"] = {
        "skipped": audit_skipped,
        "reason": ("final mod.ini intentionally not written" if audit_skipped else ""),
        "sections": int(report.get("sections", 0)),
        "dangling": list(report.get("dangling") or ()),
        "missing": list(report.get("missing") or ()),
        "errors": [],
    }
    report["static_audit_errors"] = []
    return report
