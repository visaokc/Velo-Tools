"""Helpers for importing matched LOD meshes for visual inspection."""

from __future__ import annotations


_ORIGINAL_IMPORT_LODS = None
_PATCHED_EXTRACT_MODULE = None


def import_matched_lod_object_per_component(
    context,
    cfg,
    collection_name: str,
    lod_object,
    importer,
    *import_args,
    **import_kwargs,
):
    """Import matched LOD debug meshes without requiring Merged VG sidecars."""
    had_import_mode = hasattr(cfg, "import_skeleton_type")
    original_import_mode = getattr(cfg, "import_skeleton_type", None)
    cfg.import_skeleton_type = "COMPONENT"
    try:
        return importer(context, cfg, collection_name, lod_object, *import_args, **import_kwargs)
    finally:
        if had_import_mode:
            cfg.import_skeleton_type = original_import_mode
        else:
            try:
                delattr(cfg, "import_skeleton_type")
            except AttributeError:
                pass


def wrap_import_lods(original_import_lods, extract_module):
    """Wrap EFMI import_lods so only matched LOD debug mesh import uses Per-Component."""

    def import_lods_with_per_component_debug_import(context, cfg, full_object, lod_object, matched_components):
        if not getattr(cfg, "import_matched_lod_objects", False):
            return original_import_lods(context, cfg, full_object, lod_object, matched_components)

        original_import_object = extract_module.import_object

        def import_object_per_component(import_context, import_cfg, collection_name, import_lod_object, *args, **kwargs):
            return import_matched_lod_object_per_component(
                import_context,
                import_cfg,
                collection_name,
                import_lod_object,
                original_import_object,
                *args,
                **kwargs,
            )

        extract_module.import_object = import_object_per_component
        try:
            return original_import_lods(context, cfg, full_object, lod_object, matched_components)
        finally:
            extract_module.import_object = original_import_object

    return import_lods_with_per_component_debug_import


def install_patches(extract_module=None) -> None:
    global _ORIGINAL_IMPORT_LODS
    global _PATCHED_EXTRACT_MODULE
    if _ORIGINAL_IMPORT_LODS is not None:
        return
    if extract_module is None:
        from ._efmi_core.extract_frame_data import extract_frame_data as extract_module

    _ORIGINAL_IMPORT_LODS = extract_module.import_lods
    _PATCHED_EXTRACT_MODULE = extract_module
    extract_module.import_lods = wrap_import_lods(_ORIGINAL_IMPORT_LODS, extract_module)


def uninstall_patches() -> None:
    global _ORIGINAL_IMPORT_LODS
    global _PATCHED_EXTRACT_MODULE
    if _ORIGINAL_IMPORT_LODS is None or _PATCHED_EXTRACT_MODULE is None:
        return
    _PATCHED_EXTRACT_MODULE.import_lods = _ORIGINAL_IMPORT_LODS
    _ORIGINAL_IMPORT_LODS = None
    _PATCHED_EXTRACT_MODULE = None
