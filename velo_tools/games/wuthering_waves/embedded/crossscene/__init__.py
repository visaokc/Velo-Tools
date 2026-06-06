"""WWMI cross-scene multi-IB merge -- export consumer side.

The producer (``games/wuthering_waves/xscene_merge.py``) emits a merged extraction
folder + ``CrossSceneRouting.json``. The user imports that folder, edits a single mesh,
and exports. This package handles the export phase: read JSON -> derive a buffer for each
dungeon IB from its native host -> namespace-merge into one mod that covers all scenes.

- ``assembler``: namespace-merge N independent per-IB WWMI mods + dedupe textures by hash
  + self-check (ported from the verified standalone prototype ``merge_inis.py``,
  in-process, no subprocess).
- ``orchestrator``: derivation orchestration (import host -> transfer edit deltas/holes ->
  stock export -> fold clothing -> call assembler); the verified ``xscene_onemesh_build.py``
  pipeline landed into velo, driven by JSON.
- ``patch`` (M2 wrap-up, later): monkey-patch ``VTWW_Export.execute`` to take the
  cross-scene branch when JSON is detected.

Never modify ``_wwmi_core``.
"""
