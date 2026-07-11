# Velo Tools User Manual

Velo Tools is a Blender add-on for GIMI-ecosystem mod authoring. It combines
shared Blender-side helpers with game-specific workflows for:

- **Arknights: Endfield** through a vendored EFMI workflow plus Velo extensions.
- **Wuthering Waves** through a vendored WWMI workflow plus Velo extensions.

Velo Tools requires Blender 3.6+ and is developed/tested primarily on Blender 4.4.

## Table of Contents

1. [Quick Guide](#quick-guide)
2. [Install and Update](#install-and-update)
3. [Opening Velo Tools and Selecting a Game](#opening-velo-tools-and-selecting-a-game)
4. [Shared Tools](#shared-tools)
   - [Vertex Group Tools and General Mapping](#vertex-group-tools-and-general-mapping)
   - [Mesh Tools](#mesh-tools)
   - [Weight Tools](#weight-tools)
5. [Arknights: Endfield / EFMI Workflow](#arknights-endfield--efmi-workflow)
   - [EFMI Import, Extract and Export](#efmi-import-extract-and-export)
   - [CrossIB](#crossib)
   - [EFMI ShapeKey Export](#efmi-shapekey-export)
6. [Wuthering Waves / WWMI Workflow](#wuthering-waves--wwmi-workflow)
   - [WWMI Extract / Import / Export](#wwmi-extract--import--export)
   - [Per-Component (from Merged)](#per-component-from-merged)
   - [LOD](#lod)
   - [Cross-Scene Multi-IB](#cross-scene-multi-ib)
   - [Slot-Style Texture Export](#slot-style-texture-export)
   - [Merge Form Textures](#merge-form-textures)
   - [Form Anchors](#form-anchors)
   - [Raw Mesh](#raw-mesh)
7. [FAQ and Limits](#faq-and-limits)

## Quick Guide

1. Download the latest `velo_tools-<version>.zip` from the GitHub Releases page.
2. In Blender, install the zip with **Edit -> Preferences -> Add-ons -> Install from Disk...**.
3. Enable **Velo-Tools**.
4. Press `N` in the 3D Viewport and open the **Velo Tools** sidebar tab.
5. Use the top function tabs:
   - **Vertex Group Tools** for name mapping and batch vertex-group operations.
   - **Mesh Tools** for material splitting/merging, shape-key aggregation and multi-object sculpt helpers.
   - **Weight Tools** for robust weight transfer, mirroring, donor normalization, smoothing and group limiting.
   - **Game** for EFMI or WWMI workflows.
6. In the **Game** tab, choose the target game:
   - **Arknights: Endfield / EFMI** for EFMI import/export, CrossIB and EFMI ShapeKey export.
   - **Wuthering Waves / WWMI** for WWMI extraction/import/export, LOD, cross-scene multi-IB, slot-style texture export, Merge Form Textures, form anchors, Per-Component (from Merged) and Raw Mesh.

## Install and Update

### Install

1. Download the latest release zip named like `velo_tools-<version>.zip`.
2. In Blender, open **Edit -> Preferences -> Add-ons**.
3. Click **Install from Disk...** and select the zip.
4. Enable **Velo-Tools**.
5. Open **View3D -> Sidebar (`N`) -> Velo Tools**.

### Update

Velo Tools includes its own host-level updater. Open **Edit -> Preferences -> Add-ons -> Velo-Tools** and use the update panel.

- Stable releases are shown by default.
- Enable pre-release updates only if you intentionally want beta/pre-release builds.
- Restart Blender after an update.
- Do not use the updater from a development junction/source-link install.

The EFMI and WWMI cores inside Velo Tools are vendored/forked under Velo namespaces.
Their upstream per-game updaters are neutralized; update Velo Tools itself instead
of updating the embedded cores separately.

## Opening Velo Tools and Selecting a Game

Open the 3D Viewport sidebar with `N`, then select the **Velo Tools** tab.

The main panel has four function areas:

| Area | Purpose |
| --- | --- |
| Vertex Group Tools | Vertex-group operations, mapping tables and visual mapping checks. |
| Mesh Tools | Material tools, split/merge helpers, multi-object sculpt helpers and shape-key aggregation. |
| Weight Tools | Robust weight transfer, mirror transfer, donor normalization, smoothing and group limiting. |
| Game | EFMI / WWMI game-specific mod workflows. |

When **Game** is active, use the game dropdown to choose:

- **Arknights: Endfield / EFMI**
- **Wuthering Waves / WWMI**

Only the selected game's panels are shown.

## Shared Tools

### Vertex Group Tools and General Mapping

Use this area when you need to rename, map, inspect or clean vertex groups before export.

Common tasks:

1. Select a source mesh and target mesh.
2. Build or load a mapping table.
3. Fill rows from the source object.
4. Run position-based matching into the table.
5. Apply source/target renaming or restore original names.
6. Use the overlay/unmatched list to inspect mapping quality.

The mapping table can be stored as an internal Blender text block or imported/exported
as a file.

Batch vertex-group operations include:

- Merge vertex groups.
- Fill gaps in vertex groups.
- Remove unused vertex groups.
- Remove all vertex groups.

### Mesh Tools

Mesh Tools are shared helpers used before game export.

Main panels:

- **Multi-object sculpt**: create a merged sculpt object, apply sculpt edits back and apply sculpt edits with shape keys.
- **Material tools**: add a `Component` prefix, generate materials, split by material, merge by texture, fill missing mesh data, apply modifiers on objects with shape keys and convert vertex colors.
- **Material routing**: preview material groups under the active game export collection, split by material or texture into collections and assign virtual material groups to target collections.
- **Shape-key aggregation**: scan mesh shape keys in a collection, rename them into `Deform N <name>` order and synchronize values across meshes that share the same shape-key name.

Use shape-key aggregation for Blender-side organization. EFMI runtime ShapeKey export
is a separate game-specific feature described below.

### Weight Tools

Weight Tools are for transferring one source vertex group onto a target mesh while
preserving the rest of the weight ecosystem.

Typical flow:

1. Set **Source Mesh**.
2. Choose **Source Vertex Group**.
3. Optionally choose a **Mirror Vertex Group**.
4. Set **Target Mesh** and optional **Target Armature**.
5. Pick the transfer engine:
   - **Robust**: surface matching plus inpaint; the default Velo path.
   - **Surface interpolation transfer**: Blender Data Transfer style surface interpolation.
6. Confirm or override the target group names.
7. Review donor groups used for normalization.
8. Run **Weight Transfer**.
9. Review the last report.

Important behaviors:

- Robust transfer uses source-side weight context to avoid writing weak, non-authoritative isolated weights.
- Donor groups are used during normalization and act as a maximum donor set, not a requirement to fill every slot.
- Manual donor slots are strict.
- Auto-filled donor slots are preview choices until you edit them.
- Mirror transfer can resolve numeric or named mirror pairs and can persist manual mirror mappings in the scene.
- Locked ordinary groups are treated as protected capacity during limit/normalize.
- Smoothing is seam-safe and can be disabled.
- Selected-vertex repair works in Edit Mode and leaves unresolved vertices selected.

## Arknights: Endfield / EFMI Workflow

Velo Tools embeds an EFMI workflow under the Velo Tools game tab. Choose
**Game -> Arknights: Endfield / EFMI**.

### EFMI Import, Extract and Export

The EFMI panel provides the usual EFMI modes, localized and hosted inside Velo Tools:

- **Extract Frame Data**
- **Import Object**
- **Extract LOD Data**
- **Export Mod**

Common EFMI export fields include:

- Component collection.
- Object source folder.
- Mod output folder.
- Export skeleton mode.
- Mirror mesh.
- Apply modifiers.
- Copy textures.
- Write `mod.ini`.
- Ignore nested/hidden collections or hidden objects.
- Ignore muted shape keys.
- Add missing vertex groups.
- Fill missing mesh data.
- Allow export without LODs.

For extraction, use a valid Frame Dump folder and an output folder. Optional filters
can skip static objects, small textures, `.jpg` textures, objects/components below
thresholds or selected resource hashes.

### CrossIB

CrossIB lets one component borrow another component's rendering pipeline across index buffers.

Use it from the EFMI export workflow:

1. Switch to **Game -> Arknights: Endfield / EFMI**.
2. Set mode to **Export Mod**.
3. Open **Cross Index Buffer / CrossIB**.
4. Enable **Use Cross Index Buffer**.
5. Generate or merge `CrossIB.json` from a Frame Dump if the object source folder does not already contain sidecar data.
6. Add mappings:
   - **Object mapping**: one mesh object acts as a provider.
   - **Collection mapping**: every mesh in the collection can act as a provider.
7. Set the target component for each mapping.
8. Export normally.

Notes:

- `CrossIB.json` and `ShaderOverride.ini` sidecars are consumed directly when present.
- Additional scene dumps can be accumulated into the same sidecar data.
- Use overwrite/rebuild only when you intentionally want to recompute from scratch.

### EFMI ShapeKey Export

EFMI ShapeKey export is an export-time feature for custom shape keys.

Basic naming rule:

```text
Deform <slot> <name>
```

Examples:

```text
Deform 1 Smile
Deform 2 Blink
Deform12 CapeLift
```

Workflow:

1. Put shape keys on meshes inside the active EFMI component collection.
2. Name keys as `Deform <number> <name>`.
3. In EFMI advanced export options, enable **Export custom shape keys**.
4. Keep **Merge Buffer Files** enabled unless you need legacy per-slot buffers.
5. Check the detected shape-key list.
6. Fix naming conflicts before export.
7. Export normally.

Limits:

- Duplicate `(slot, name)` entries on one object block export.
- The same name assigned to different Deform slots blocks export.
- The same Deform slot mapped to different names across components blocks export.
- Export variable names are sanitized to INI-safe ASCII identifiers; collisions after sanitization block export.
- Merged ShapeKey export supports many slots, but every channel must map to one shape key.

## Wuthering Waves / WWMI Workflow

Choose **Game -> Wuthering Waves / WWMI**.

Velo Tools embeds a namespaced WWMI workflow and adds Velo-only helper panels. It
can coexist with a standalone WWMI-Tools install because Velo uses its own fork namespace.

### WWMI Extract / Import / Export

#### Extract Objects From Dump

Use this mode to convert a WWMI Frame Dump into an object source folder.

Fields/options include:

- Frame Dump folder.
- Output folder.
- Skip small textures.
- Minimum texture size.
- Skip `.jpg` textures.
- Skip known cubemap textures.
- Skip same-slot hash textures.
- Skip dirty/inherited slot records when log freshness evidence is available.

Extraction also writes texture usage data used by slot-style texture export.

#### Import Object

Use this mode to import an extracted object source folder into Blender.

Key fields/options:

- Object source folder.
- Vertex color storage.
- Import skeleton type:
  - **Merged**
  - **Per-Component**
- Import as component sub-collections.
- Import textures.
- Skip empty vertex groups.
- Mirror mesh.

Velo import extras:

- **Import as component sub-collections** creates `C0`, `C1`, ... sub-collections under the imported object collection and wires the export collection automatically.
- **Import textures** assigns diffuse/source textures to imported meshes when texture usage data is available.
- Turning these options off reproduces a more stock single-collection, no-material import behavior.

#### Export Mod

Use this mode to export the edited Blender collection as a WWMI mod.

Main fields/options:

- Component collection.
- Object source folder.
- Mod output folder.
- Skeleton:
  - **Merged**
  - **Per-Component**
  - **Per-Component (from Merged)**
- Mirror mesh.
- Apply all modifiers.
- Copy textures.
- Write `mod.ini`.
- Comment `mod.ini`.
- Ignore nested collections.
- Ignore hidden collections.
- Ignore hidden objects.
- Ignore muted shape keys.
- Partial export options for advanced buffer-only exports.

For ordinary full exports, keep `write_ini` and `copy_textures` enabled.

### Per-Component (from Merged)

**Per-Component (from Merged)** is a Velo export mode for WWMI.

Use it when you want to:

- Edit with a **Merged** skeleton and unified vertex-group list.
- Export a **Per-Component** runtime mod.
- Avoid the runtime downsides of pure Merged mode in cases where Per-Component output is preferable.
- Let Velo remap unified vertex groups back to component-local groups during export.

Behavior:

- You import/edit in a Merged authoring style.
- On export, Velo remaps unified vertex groups into component-local IDs.
- If a vertex is weighted to bones outside the owning component's allowed range, export fails with an actionable error.
- For cross-scene projects, this mode is the validated path for edited body, own-buffer and editable-IB outputs.

Use normal **Merged** if you intentionally need unrestricted unified weighting at
runtime. Use normal **Per-Component** if you authored directly in component-local groups.

### LOD

The WWMI LOD workflow adds LOD data to an already extracted object source folder,
then emits LOD-aware export sections during mod export.

Workflow:

1. Extract/import the main object first.
2. Capture a Frame Dump while the target is rendered at LOD distance.
3. Open **LOD Data Extraction**.
4. Set:
   - LOD Frame Dump folder.
   - Object source folder.
5. Tune filters if needed:
   - Minimum component vertex count.
   - Object hash blacklist.
   - Geometry match threshold.
6. Use advanced settings only when matching fails:
   - Matching method: voxel or point cloud.
   - Voxel size / sample size.
   - Prefilter candidate count.
   - Vertex-group matcher candidate count.
   - Allow overwrite.
   - Skip LODs below threshold.
7. Click **Extract LOD Data**.
8. Export normally.

Notes:

- Capture the LOD dump when the game is actually drawing the LOD mesh.
- Existing LOD data is protected unless overwrite is enabled.
- Cross-scene exports can carry LOD data through the merged workflow.

### Cross-Scene Multi-IB

Cross-scene multi-IB merges one base extraction plus additional IB-specific
extractions into a single editable source folder.

Use it when a character/object has multiple scene-specific IBs and you want one
edited model to work across them.

Workflow:

1. Prepare a base extracted object folder.
2. Prepare one or more additional IB folders.
3. Open **Cross-Scene Merge**.
4. Set the base folder.
5. Add each IB folder and choose its role:
   - **Fold**: fold into the base; editing the base covers that scene where compatible.
   - **Editable**: import as independently editable geometry, usually when it is a separate form or ownership domain.
6. Set the merge output folder.
7. Click **Merge Cross-Scene**.
8. Import the merged output folder with **Import Object**.
9. Edit the merged Blender collection.
10. Export normally with **Export Mod**.

The export hook detects `CrossSceneRouting.json` in the object source folder and
automatically builds the final multi-scene mod.

Important notes:

- The merged folder is the authoring source for the cross-scene project.
- The final export applies stock-like output options at the final merged output stage.
- FoldHost routing keeps a single canonical draw owner for each actual draw.
- Slot-style texture export is supported in this path.
- Per-Component (from Merged) is recommended for many cross-scene authoring cases.
- If you change source captures or routing assumptions, rerun the merge step.

### Slot-Style Texture Export

Slot-style texture export is a WWMI/Velo feature that replaces texture-hash
matching with draw-scope `ps-tN` slot rebinding.

Enable it in **Export Mod -> Velo compatibility options -> Slot-style textures**.

Why use it:

- Game texture hashes can change across streaming residency/mip states.
- Slot-style export binds mod textures inside component draw scopes.
- Conditions are based on positive `ps-tN` DXGI format-family layout evidence, not shader hashes.
- The output is designed to be conservative and auditable.

Typical workflow:

1. Extract the object with recent Velo Tools so `ShaderTextureUsage.json` exists.
2. Import and edit the object.
3. In Export Mod, enable **Slot-style textures**.
4. Optionally refresh the component list.
5. Leave components checked if they should use slot-style export.
6. Uncheck components that should retain native hash-style replacement.
7. Export normally.

Behavior and limits:

- By default, if no component list is populated, all eligible components are treated as slot-style.
- Unchecked components retain native hash-style replacement, gated by the owning `$object_detected_ibN` state in cross-scene output.
- Hash-style `ResourceTexture` and `TextureOverride` sections are shown as adjacent native-order pairs when the relationship is unambiguous.
- Empty `ResourceBypassPST0..8` sections are intentional runtime backup handles for `ps-t0..8` when that IB has a slot transaction; unused generated groups are removed and rejected by audit.
- Final cross-scene component-bearing section names use merged-root component ids; `_ibN` identifies the owning IB namespace.
- Unsupported ambiguous components fail closed instead of producing unsafe hash/probe logic.
- Same-layout multi-form components need better slot evidence or component exclusion.
- Slot command lists use direct `ps-tN = ref ResourceTexture...` assignments.
- Cross-scene slot-style export audits resource sections, DDS existence, format-family matches and command-list structure.
- Pixel shader resources `ps-t0..8` are backed up/restored around texture-triggered draw transactions to avoid lazy resource state leaking across shader transitions.

Use slot-style for texture streaming robustness. Do not use it as a magic fix for
components whose runtime state cannot be distinguished by slot layout.

### Merge Form Textures

Use **Merge Form Textures** for multi-form WWMI characters or objects whose forms
bind different texture sets.

Workflow:

1. Capture one RAW Frame Dump per extra form.
2. Open **Merge Form Textures**.
3. Set:
   - Form Frame Dump.
   - Object source folder.
   - Optional form label.
4. Click **Merge Form Textures**.
5. Repeat for additional forms or additional captures of the same form.
6. Enable slot-style export and export normally.

Notes:

- You do not need to perform a second full object extraction for the extra form.
- The merge reads the RAW Frame Dump and updates the object folder's texture usage data.
- Reusing a form label can add more evidence for the same form.
- The output is stored in the object source folder's texture usage data and used at export time.
- Only actual same-VB multi-form components should become multi-form slot domains; separate editable/other-VB components remain single-form domains.

### Form Anchors

Form anchors are optional WWMI metadata for form tracking.

Use them only when you understand which form-exclusive draw reliably identifies a form.

Supported anchor formats:

- **8 hex characters**: a `vb0` hash from dump filenames.
- **16 hex characters**: a pixel shader hash.

Do not use an `ib` hash as a form anchor; it does not participate in WWMI draw
matching for this purpose.

Manual format:

```text
hash:formLabel
```

Multiple anchors can be separated by commas, spaces or newlines.

Examples:

```text
1234abcd:base
89abcdef:form2
0123456789abcdef:form3
```

Anchor finder workflow:

1. Enable slot-style export.
2. Enable the optional form-id auxiliary/anchor section when needed.
3. Set the base form dump.
4. Add extra form dump rows and labels.
5. Click **Find Form Anchors**.
6. Review candidates.
7. Click **Apply** on useful candidates.
8. Export.

Important limits:

- `$form_id` is optional auxiliary state, not the default texture discriminator.
- It cannot rescue a component whose slot-layout evidence is completely indistinguishable unless that component is excluded from slot-style export.
- Prefer `vb0` anchors when possible because geometry identity is generally more stable than shader identity.
- If exactly one form is unanchored, the watchdog can infer it by elimination when no anchored form appears in a frame.
- If anchors become stale after a game update, refresh dumps and re-run the finder.

### Raw Mesh

Raw Mesh is a WWMI/Velo sub-tool for non-character geometry such as VFX-layer or
scene/environment meshes that the stock pose-chain extractor may not detect.

It has its own panel and modes:

- **Extract**
- **Import**
- **Export**

#### Raw Mesh Extract

Fields:

- Frame Dump folder.
- Output folder.
- Hash list.
- Optional output folder name.
- Optional Position element override.
- Texture filters.

Hash list behavior:

- A `VB` hash pulls the whole `VB0` object and auto-splits components.
- An `IB` hash pulls the matching draw/component.
- Multiple hashes can be comma-separated.

#### Raw Mesh Import

Import the consolidated folder produced by Raw Mesh Extract.

Velo creates editable mesh objects while preserving raw per-slot bytes as mesh attributes.

#### Raw Mesh Export

Export fields:

- Component collection.
- Mod output folder.
- Export mode:
  - **Auto**
  - **Faithful**
  - **Rebuild**

Modes:

- **Faithful**: topology must stay unchanged; only edited positions are re-encoded, and other vertex attributes pass through byte-for-byte.
- **Rebuild**: allows topology changes/remeshing, but non-standard attributes are best-effort and can be lossy.
- **Auto**: uses Faithful when topology is unchanged, otherwise Rebuild.

Output:

- Independent plain 3dmigoto per-component overrides.
- Each component matches its own source hash and original draw range.
- Textures are copied when available from the source extraction.

Limits:

- Raw Mesh is not the normal skinned-character workflow.
- Faithful mode refuses topology changes.
- Rebuild mode can lose or zero-fill attributes that Blender cannot represent cleanly.
- This tool is intentionally isolated from the vendored WWMI core.

## FAQ and Limits

### Can I install Velo Tools together with standalone EFMI-Tools or WWMI-Tools?

Yes. Velo embeds namespaced EFMI/WWMI forks and avoids registering the same upstream
updater/operator IDs. Use the Velo Tools updater for Velo Tools itself.

### Which Blender version should I use?

Blender 3.6+ is required. Blender 4.4 is the primary tested version.

### Why do I not see EFMI or WWMI panels?

Open **Velo Tools -> Game**, then choose the game from the dropdown. Game-specific
panels are only shown when the Game tab and the matching game are active.

### Which WWMI skeleton mode should I use?

- Use **Merged** for unrestricted unified authoring/runtime behavior.
- Use **Per-Component** for direct component-local authoring.
- Use **Per-Component (from Merged)** when you want Merged-style editing but Per-Component runtime output.

### Why did Per-Component (from Merged) export fail?

Most likely a mesh contains weights outside the owning component's allowed
vertex-group/bone range. Fix the weights or move those weights to valid
component-local groups.

### Why did slot-style texture export fail?

Slot-style export is conservative. Common causes:

- Missing or stale `ShaderTextureUsage.json`.
- A component's forms share the same slot layout and cannot be distinguished without texture identity.
- A required DDS/resource is missing.
- A component should retain native hash-style replacement and be unchecked in the slot component list.

### Should I enable slot-style textures for every mod?

No. Use it when texture streaming/hash churn is a problem or when you need
cross-scene slot-aware exports. Stock hash-style export remains the default and
is still useful.

### Can form anchors use `ib` hashes?

No. Use `vb0` hashes or pixel shader hashes. `ib` hashes do not work as form anchors.

### Why is my LOD extraction not matching?

Make sure the Frame Dump was captured while the game was actually rendering the
LOD mesh. Then tune the geometry matcher threshold, voxel size or candidate counts.

### Why did CrossIB miss a scene?

Generate or accumulate sidecar data from additional Frame Dumps. CrossIB can merge
new scene shader evidence into existing sidecars.

### Can I edit topology in Raw Mesh?

Yes, but only with **Rebuild** mode, and non-standard attributes may be lossy. Use
**Faithful** when you only edit positions and want byte-preserving output.

### Can Weight Tools replace manual weight painting?

No. Weight Tools accelerates transfer, mirroring, normalization, smoothing and
repair, but you should still inspect the result in Blender.

### Are private/local paths required in this manual?

No. Use your own project folders. The manual intentionally uses generic names such
as object source folder, Frame Dump folder and mod output folder.
