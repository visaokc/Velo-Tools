"""Canonical WWMI LOD support implemented in the Velo driver layer.

The active runtime keeps vertex Blend IDs stable across LODs. Per-LOD map
buffers select native palette sources and scatter them into persistent
canonical skeleton slots before the custom draw. The former stateless
per-LOD Blend implementation is isolated in ``lod_legacy_pending_delete``.
"""
