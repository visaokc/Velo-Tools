# WWMI LOD support (velo driver layer).
#
# Ports the EFMI "Extract LOD Data" capability to Wuthering Waves:
#   - extract.py    matches a LOD frame dump against an extracted object and
#                   persists per-component LOD data into Metadata.json
#   - export_hook.py appends LOD override sections + per-LOD remapped blend
#                   buffers to the stock mod export (single-mod LOD)
#
# Nothing in this package modifies _wwmi_core sources; the vendored core is
# only called (extraction pipeline, buffer parsing) or wrapped (ModExporter).
