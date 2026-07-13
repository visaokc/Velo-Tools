"""WWMI schema-v3 cross-scene merge and direct-export driver.

The producer emits one self-contained aggregate root with
``CrossSceneManifest.json``. The export hook captures one native selection,
builds owning-IB ``ExportUnit`` objects in memory, and compiles the final INI,
buffers, and root-DDS delivery without child mods or an assembler pass.

The vendored ``_wwmi_core`` remains unchanged.
"""
