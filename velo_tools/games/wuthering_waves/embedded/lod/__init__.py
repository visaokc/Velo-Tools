# WWMI LOD support (velo driver layer).
#
# Ports the EFMI "Extract LOD Data" capability to Wuthering Waves:
#   - extract.py    matches a LOD frame dump against an extracted object and
#                   persists per-component LOD data into Metadata.json
#   - export_hook.py per-draw stateless LOD export: injects one BlendLOD{n}
#                   buffer per LOD level (component-local 8-bit ids, native
#                   bone constant buffers untouched) and switches the ini
#                   generation to the velo fork template (templates/), which
#                   factors draws into shared command lists and emits the LOD
#                   override sections
#
# Nothing in this package modifies _wwmi_core sources; the vendored core is
# only called (extraction pipeline, buffer parsing, IniMaker's public
# template_string parameter) or wrapped (ModExporter / IniMaker methods).
