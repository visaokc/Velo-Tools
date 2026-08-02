# Velo Tools User Manual

This manual covers Velo Tools 1.5.3. Velo Tools hosts shared Blender helpers and namespaced EFMI and WWMI workflows in one add-on.

Chinese reader: [Velo Tools 中文使用手册](user-manual.zh-CN.md).

## Table of Contents

1. [Start Here](#start-here)
2. [Install, Update, and Coexistence](#install-update-and-coexistence)
3. [Project Data Flow](#project-data-flow)
4. [Shared Tools](#shared-tools)
5. [EFMI End-to-End](#efmi-end-to-end)
6. [WWMI Single-IB](#wwmi-single-ib)
7. [WWMI Extended](#wwmi-extended)
8. [Read and Validate Output](#read-and-validate-output)
9. [Troubleshooting by Symptom](#troubleshooting-by-symptom)
10. [Limits and Glossary](#limits-and-glossary)

## Start Here

### Requirements

- Blender 3.6 or newer is required.
- Blender 4.4 is the primary development and test version.
- A working game-side GIMI environment is required to capture Frame Dumps and load the exported mod.
- Velo Tools does not replace the game-side loader or teach capture hotkeys.

Use a fresh Frame Dump for any workflow that depends on current runtime Hash, shader, texture-slot, or LOD evidence.

### Find the UI

Install and enable **Velo-Tools**, then open the 3D Viewport sidebar with `N`. Select the **Velo Tools** tab.

The add-on uses Simplified Chinese UI labels. This manual shows the exact Chinese label first, followed by its English meaning.

| Exact UI label | English meaning | Use it for |
| --- | --- | --- |
| **顶点组工具** | Vertex Group Tools | Batch vertex-group work and name mapping |
| **网格工具** | Mesh Tools | Materials, collection routing, sculpt helpers, and ShapeKey aggregation |
| **权重工具** | Weight Tools | Transfer, mirror, normalize, smooth, and repair weights |
| **游戏** | Game | EFMI and WWMI game workflows |

Inside **游戏 (Game)**, choose **终末地 (Arknights: Endfield)** or **鸣潮 (Wuthering Waves)**.

### Choose the Correct Workflow

| Goal | Workflow |
| --- | --- |
| Rename or compare vertex groups | **顶点组工具 (Vertex Group Tools)** |
| Prepare materials, collections, or shared ShapeKeys | **网格工具 (Mesh Tools)** |
| Transfer or repair weights | **权重工具 (Weight Tools)** |
| Make a normal Endfield character mod | [EFMI End-to-End](#efmi-end-to-end) |
| Reuse EFMI geometry through another component pass | [EFMI CrossIB](#efmi-crossib) |
| Export custom EFMI runtime ShapeKeys | [EFMI Custom ShapeKey Export](#efmi-custom-shapekey-export) |
| Make a normal single-IB WWMI character mod | [WWMI Single-IB](#wwmi-single-ib) |
| Add WWMI distance LODs | [WWMI LOD](#wwmi-lod) |
| Support several WWMI scene-specific IBs | [WWMI Cross-Scene Multi-IB](#wwmi-cross-scene-multi-ib) |
| Handle WWMI texture streaming or Hash-change cases | [WWMI Texture Strategy](#wwmi-texture-strategy) |
| Edit WWMI VFX, scene, or non-character geometry | [WWMI Raw Mesh](#wwmi-raw-mesh) |

EFMI **CrossIB** and WWMI **cross-scene multi-IB** are different systems. CrossIB borrows an EFMI component pass. WWMI cross-scene merges several extracted scene routes into one authoring source.

## Install, Update, and Coexistence

### Install a Release

1. Open the [GitHub Releases](https://github.com/visaokc/Velo-Tools/releases) page.
2. Download the release asset named `velo_tools-<version>.zip`.
3. In Blender, open **Edit -> Preferences -> Add-ons**.
4. Click **Install from Disk...** and select the zip without extracting it.
5. Enable **Velo-Tools**.
6. Restart Blender if the panels do not appear immediately.

Use the release asset, not GitHub's automatically generated source archive. The release asset has the installable add-on layout.

### Update Velo Tools

Open **Edit -> Preferences -> Add-ons -> Velo-Tools** and use the Velo updater.

- Stable releases are shown by default.
- Enable pre-release updates only when you deliberately want a test build.
- Restart Blender after the updater finishes.
- Update Velo Tools as one add-on. Do not update its embedded EFMI or WWMI cores separately.
- A manual install clears cached update targets that are equal to or older than the installed version, so an old same-version banner should disappear after the add-on reloads.

> **Source-link warning:** never run the updater from a developer junction or source-link installation. The updater replaces files in the install directory, which would be the linked repository.

### Coexistence with Standalone Tools

Velo Tools uses its own namespaced EFMI and WWMI forks. It can coexist with standalone EFMI-Tools and WWMI-Tools.

The embedded per-game updaters are disabled. Use the Velo host-level updater for the Velo copy and each standalone tool's own updater for its separate copy.

If Blender shows duplicate Velo installations, disable the stale copy and keep one `velo_tools` add-on directory.

## Project Data Flow

### The Five Working Stages

1. **Frame Dump**: runtime capture containing buffers, textures, draw calls, and usually `log.txt`.
2. **Object source folder**: extracted components, metadata, textures, and Velo sidecars.
3. **Merged source folder**: optional WWMI cross-scene authoring root.
4. **Blender component collection**: editable meshes selected in the export panel.
5. **Mod output folder**: generated `mod.ini`, `Meshes`, `Textures`, and optional support files.

Keep the Frame Dump, object source, and mod output as separate stages. Do not use the mod output as the object source.

For WWMI cross-scene work, the merged source folder becomes the object source. Import and export from that root rather than from one of its child IB folders.

### Generated Data Files

| File | Producer | Consumer and purpose |
| --- | --- | --- |
| `Metadata.json` | EFMI/WWMI extraction | Import/export geometry, component, skeleton, and LOD metadata |
| `TextureUsage.json` | EFMI/WWMI extraction | Basic texture attribution and import-time material assignment |
| `ShaderTextureUsage.json` | Velo WWMI extraction | Shader-pair, `ps-tN`, format, freshness, form, and captured Unreal asset-path evidence |
| `VertexGroupMap.json` | Velo EFMI extraction | Unified-to-component-local vertex-group translation for EFMI Merged mode |
| `CrossIB.json` | Velo EFMI extraction or CrossIB panel | EFMI Component match, pass topology, transparency, and input-compatibility evidence consumed by CrossIB export |
| `CrossSceneManifest.json` | Velo WWMI cross-scene merge | Runtime IB ownership and component/VG/LOD/fold/morph routing not derivable from root Metadata/STU |

Treat these files as generated contracts. Re-run the producer when captures or routing change instead of manually guessing missing identities.

### Freshness Matters

Runtime resource identities can change after a game update. Texture streaming can also expose identities that were absent from an older capture.

Velo reads resource identity from FrameAnalysis evidence, STU metadata, and names such as `t=<hash>.dds`. It does not derive a game texture Hash from DDS bytes or image similarity.

If the current game uses an identity that is absent from the selected source evidence, capture a fresh dump and rebuild the affected object source.

## Shared Tools

### Vertex-Group Operations and General Mapping

Open **顶点组工具 (Vertex Group Tools)**.

The **顶点组操作 (Vertex Group Operations)** panel provides batch merge, gap filling, unused-group removal, and all-group removal.

Save the `.blend` before destructive cleanup. **Remove all vertex groups** is not a matching operation and removes the entire group set.

Use **通用顶点组映射 (General Vertex-Group Mapping)** when source and target group names differ.

1. Choose **源物体 (Source Object)** and **目标物体 (Target Object)**.
2. Optionally choose an armature if renaming should include bones.
3. Click **从源物体补行 (Fill Rows from Source)**.
4. Click **按位置匹配→写入本表 (Match by Position -> Write to Table)**.
5. Review distances and unresolved rows.
6. Apply the required source or target rename action.
7. Export the table or sync it to a Blender text block if it must travel with the `.blend`.

Enable **通用映射 - 可视化校对（重心连线） (General Mapping Visual Check)** to inspect centroid links. Use **未匹配列表 (Unmatched List)** to resolve rows instead of accepting a partial table blindly.

The optional maximum-distance threshold rejects weak matches. Start without an arbitrary large threshold, inspect the overlay, then set a meaningful project-scale limit.

### Mesh Preparation

Open **网格工具 (Mesh Tools)**.

#### Multi-Object Sculpt

Use **多物体雕刻 (Multi-Object Sculpt)** to create a temporary merged sculpt object, sculpt across seams, then apply the result back.

Choose the ShapeKey-aware apply action when the source objects contain ShapeKeys. Use the ordinary apply action only when the result does not need ShapeKey preservation.

#### Material Tools

The **材质工具 (Material Tools)** panel can:

- add a `Component N` prefix to selected objects;
- create same-name materials;
- split meshes by material;
- merge selected meshes by texture;
- fill missing mesh data;
- apply modifiers on objects with ShapeKeys;
- convert vertex colors.

The icon-only toggle beside **选中物体生成材质球 (Create Materials for Selected Objects)** enables automatic same-name material synchronization. It is off by default and uses Blender's highlighted toggle state while enabled. The setting is saved with the scene and only watches mesh objects inside the current game's **组件集合 (Component Collection)** and its child collections. Renaming a mesh with zero or one material slot synchronizes its mesh and material names; meshes with two or more material slots are left unchanged. Newly created, duplicated, or imported objects are first recorded without modification and are processed only after a later rename.

Set **形态键清理阈值 (ShapeKey Cleanup Threshold)** before split/merge operations when tiny ShapeKey deltas should be discarded.

#### Material-to-Collection Routing

Use **按材质分离所属集合 (Route Material Splits to Collections)** after selecting the game export component collection.

1. Click **刷新材质分组 (Refresh Material Groups)**.
2. Inspect the collection tree and virtual material leaves.
3. Add real child collections where needed.
4. Assign virtual leaves to their destination collections.
5. Run split by material or split by texture.

Collection rows are real Blender collections. Leaf rows are a preview of final material grouping, not current mesh objects.

These operations may disable **忽略嵌套集合 (Ignore Nested Collections)** so newly routed child collections remain exportable. Re-check that option before export.

#### Shared ShapeKey Aggregation

Use **形态键聚合 (按集合) (ShapeKey Aggregation by Collection)** to scan equal-name ShapeKeys across meshes and synchronize their names and values. The list is deterministic: existing `Deform N` entries appear first in numeric order, followed by all other names in case-insensitive natural A-Z order. Widening the sidebar gives the extra space to the name field; the value slider is capped at 8 Blender UI units, and all visible rows switch to the new column layout together during a resize.

The left checkbox selects entries for **自动重命名 (Auto Rename)**. All entries start unchecked; use the native **全选/全不选 (Select All/Clear All)** action beside the refresh and auto-rename actions to toggle every currently renameable entry. It deliberately sits outside the scrolling list instead of imitating a column header, so its placement does not depend on UIList padding, scrollbar width, theme, DPI, or UI scale. Existing `Deform N` names are protected by default. Auto Rename treats every existing numeric ID as an occupied pin and fills the lowest free positive ID, preserving the full original name as the suffix; a manually pinned high ID therefore does not force later automatic names above it. In WWMI mode, the selected object source `Metadata.json` also reserves every native batch range, including native keys deleted from Blender. Missing or invalid WWMI source metadata cancels Auto Rename instead of risking a native-ID collision. EFMI mode does not read or require an object source for numbering. Hover over `xN` to see every mesh object that contains that ShapeKey. Contributor labels reserve equal digit width so mixed one- and multi-digit counts keep every value slider aligned. Refreshing or automatic rescanning preserves the checked rows and active row by name.

This is a Blender organization tool. It is not the EFMI runtime ShapeKey exporter described later.

### Weight Tools

Open **权重工具 (Weight Tools)**.

#### Recommended Transfer Flow

1. In **工作对象 (Working Objects)**, choose the source mesh and source vertex group.
2. Choose a mirror source group if both sides should be transferred.
3. Choose the target mesh and optional target armature.
4. In **传递设置 (Transfer Settings)**, choose an engine.
5. Confirm the target group, mirror target, and donor preview.
6. Configure smoothing, group limits, and normalization in **后处理 (Postprocess)**.
7. Click **执行权重传递 (Run Weight Transfer)**.
8. Open or copy the last report, then test deformation in Pose Mode.

#### Choose an Engine

- **Robust** performs surface matching followed by inpaint. It is the default Velo path.
- **面插值传递 (Surface Interpolation Transfer)** uses Blender Data Transfer with `POLYINTERP_NEAREST`.

Official Windows builds include the CPython 3.11 native packages required by Robust. You do not need to install the standalone Robust Weight Transfer add-on. Velo packages only `scipy`, `libigl`, and `robust-laplacian`, not that add-on's code, UI, operators, or sample assets.

If the standalone add-on is already enabled and has loaded a complete compatible dependency set, Velo reuses those modules without adding its private dependency directory to `sys.path`. Otherwise Velo loads its own private copies temporarily, so the two add-ons can coexist.

Robust uses a whole-island positive-evidence gate. A disconnected target island with no positive source evidence remains zero instead of receiving invented inpaint weights.

Use **高级 (Advanced)** only when geometry requires different distance, normal-angle, normal-flip, inpaint, evaluated-mesh, or dilation settings.

#### Target and Donor Rules

With **手动指定承接组 (Specify Target Group Manually)** off, Velo resolves the target through the active MMD mapping. Turn it on only when you need an explicit override.

The donor count is a maximum from 1 to 6. Automatic selection does not add weak groups merely to fill every slot.

Manual donor and mirror-donor choices are strict. Velo validates them before writing target weights. A failed operation restores the target's previous weight memberships.

Locked ordinary groups are protected capacity during limit and normalization. The new target group is preserved first; eligible donors are compressed into the remaining capacity.

#### Mirror, Merge, and Repair

Use **镜像映射组 (Mirror Mapping Groups)** to store manual left/right pairs for component objects and to mirror the active group from the authoritative side.

Use **权重组转移 (Weight-Group Transfer)** to move one group's weights into another or to merge mapping rows that share one target.

In Edit Mode, **按比例规格化选中顶点 (Normalize Selected Vertices Proportionally)** repairs only selected vertices. It does not create weights in groups that were absent.

Seam-safe smoothing blocks propagation across UV seams. **限制每顶点组数量 (Limit Groups per Vertex)** excludes locked and Velo-special groups from editable candidates.

Weight Tools accelerate authoring; they do not replace deformation review. Inspect joints, seams, mirrored areas, and previously disconnected islands before export.

## EFMI End-to-End

Choose **游戏 (Game) -> 终末地 (Arknights: Endfield)**.

### 1. Extract an EFMI Object Source

Set **模式 (Mode)** to **提取帧数据 (Extract Frame Data)**.

1. Select a valid **Frame Dump 目录 (Frame Dump Folder)**.
2. Select an **输出目录 (Output Folder)**.
3. Configure object, component, and texture filters.
4. Configure the Velo compatibility options.
5. Run **从 Dump 提取模型 (Extract Model from Dump)**.

Important Velo extraction options:

- **生成 VertexGroupMap.json** is required for later Merged import/export.
- **生成 CrossIB.json** writes the v2 Component-match and transparency evidence used by later CrossIB exports.
- **组件过滤 (Component Filter)** accepts ranges such as `0-8` or `0,1,5-7`.

Filtered EFMI output is renumbered continuously as `Component 0..N`. Do not assume the output component number is still the capture's original ordinal.

**提取后导入 Blender (Import After Extraction)** is convenient for inspection. **容忍提取错误 (Tolerate Extraction Errors)** skips failed objects, so review the report before using the result as a production source.

### 2. Import the EFMI Object

Set **模式 (Mode)** to **导入对象 (Import Object)**.

1. Select the extracted **对象源目录 (Object Source Folder)**.
2. Choose vertex-color storage.
3. Choose the import skeleton mode.
4. Enable component sub-collections and texture import as needed.
5. Run **导入模型 (Import Model)**.

| Import mode | Use it when | Contract |
| --- | --- | --- |
| **Merged（统一顶点组）** | You want unified cross-component bone names | Requires `VertexGroupMap.json` |
| **Per-Component（部件独立）** | You want component-local vertex groups | Uses each component's local numbering |

**按组件创建子集合 (Create Component Sub-Collections)** creates `C0`, `C1`, and later children. It also keeps nested collection export enabled.

**导入贴图 (Import Textures)** reads `TextureUsage.json` and assigns source DDS textures where they exist.

### 3. Edit and Export EFMI

Edit the imported component collection. Keep recognizable `Component N` object identity and review vertex groups before export.

Set **模式 (Mode)** to **导出 Mod (Export Mod)**.

1. Select the component collection.
2. Select the same object source folder used for import.
3. Select the mod output folder.
4. Choose the matching export skeleton mode.
5. Keep texture copy and `mod.ini` writing enabled for a complete export.
6. Configure optional CrossIB or custom ShapeKey features.
7. Run **导出 Mod (Export Mod)**.

**Velo 兼容选项 (Velo Compatibility) -> 导出时自动按材质拆分 (Auto Split by Material on Export)** is enabled by default. If one joined object actually uses at least two `Component N`-prefixed materials, Velo separates only the export copy and keeps the scene object and ShapeKeys unchanged. Every used material in this mode must match the object's Component; conflicts report the collection, object, and material slot and stop export. Objects with no material, one material, or only unprefixed preview materials retain native object-name behavior. Turning the option off restores the complete legacy path without validation or splitting.

EFMI Merged export uses `VertexGroupMap.json` to translate unified authoring names back to component-local runtime numbering.

If an old project stored its map only inside `Metadata.json`, run **从旧 Metadata 转换 VertexGroupMap (Convert VertexGroupMap from Legacy Metadata)** before Merged export.

> **Nested collection warning:** if component objects are inside `C0/C1/...` children, enabling **忽略嵌套集合 (Ignore Nested Collections)** can produce an empty export.

### EFMI LOD

EFMI LOD data is separate from `VertexGroupMap.json`.

1. Extract the base object first.
2. Capture a dump while the game is drawing the desired LOD.
3. Set **模式 (Mode)** to **提取 LOD 数据 (Extract LOD Data)**.
4. Select the LOD dump and existing object source folder.
5. Tune geometry filters only if the default match fails.
6. Run **从 Dump 提取 LOD (Extract LOD from Dump)**, then export normally.

**允许无 LOD 导出 (Allow Export without LODs)** bypasses the metadata requirement. In open-world use, a mod without required LOD data may fail to load correctly at distance.

### EFMI CrossIB

Use **Cross Index Buffer（跨 IB） (Cross Index Buffer)** when selected source geometry must be drawn through another EFMI component's rendering pass.

The mapping direction is `source object or collection -> target component`:

- the left side supplies the provider geometry;
- the right-side target component is the consumer pass that borrows and draws it.

Workflow:

1. Enter EFMI **导出 Mod (Export Mod)** with partial export off.
2. Expand **Cross Index Buffer（跨 IB）**.
3. Enable **启用跨 IB（CrossIB） (Enable CrossIB)**.
4. Confirm `CrossIB.json v2` exists in the object source.
5. Add an object mapping or collection mapping.
6. Choose the target component for every mapping.
7. Export normally.

If the evidence file is missing, invalid, or still uses schema v1, use **生成 / 重新生成 CrossIB.json v2** and select one current Frame Dump. This replaces the JSON; it does not merge shader evidence from multiple scenes.

When CrossIB is enabled and at least one mapping exists, export writes the common ShaderRegex rules to a separate `CrossIBClassifier.ini`; the main `mod.ini` retains only that mod's CrossIB routing, resources, and draw logic. The classifier uses the community-compatible 200/201/202/203/204/205 capability ABI with the same role meanings as the legacy Hash groups, but it does not use VS Hash assignments. Because every generated classifier has the same rules, advanced users may keep one enabled copy globally and remove or disable the duplicates to reduce repeated matching work. Export removes stale CrossIB rules and HLSL assets when the feature is disabled or has no mappings. Normal VS Hash changes therefore do not require additional scene dumps.

If a selected dump does not contain the target character, Velo refuses it and leaves the existing `CrossIB.json` unchanged.

### EFMI Custom ShapeKey Export

This export-time feature is separate from shared ShapeKey aggregation.

Name runtime ShapeKeys with this contract:

```text
Deform <slot> <name>
```

Whitespace is optional. These are all valid:

```text
Deform 1 Smile
Deform2Blink
deform 12 CapeLift
```

Workflow:

1. Put the ShapeKeys on meshes inside the EFMI component collection.
2. Open EFMI export **高级 (Advanced)**.
3. Enable **导出自定义 ShapeKey (Export Custom ShapeKeys)**.
4. Keep **合并 Buffer 文件 (Merge Buffer Files)** enabled for normal use.
5. Review the live detected list and click a row to jump to its object.
6. Fix every conflict, then export.

Export is blocked when one object contains duplicate numeric Deform IDs, because two delta payloads cannot share one runtime channel on the same object. The original Blender names do not define the runtime identity: the same name may use different IDs, and one ID may have different names across components.

Every exported ID uses a stable numeric global and keeps its source name only as a comment. When several components contribute different names to one ID, the comment merges the distinct names deterministically:

```ini
; ShapeKey_12: CapeLift
global persist $ShapeKey_12 = 0.0
```

With buffer merging on, each component receives two extra merged buffer files regardless of its Deform-key count. The default position buffer is never merged.

With buffer merging off, Velo writes legacy per-slot delta, lookup, and frequency-index files.

External INI logic should refer to `$ShapeKey_<slot>`, not a cleaned form of the Blender name.

## WWMI Single-IB

Choose **游戏 (Game) -> 鸣潮 (Wuthering Waves)**.

### 1. Extract a WWMI Object Source

Set **模式 (Mode)** to **提取帧数据 (Extract Frame Data)**.

1. Select a Frame Dump containing the target at the required distance and form.
2. Select an output folder.
3. Configure texture filters.
4. Run **从 Dump 提取模型 (Extract Model from Dump)**.

Velo writes `ShaderTextureUsage.json` for slot-style texture work. When Dirty Slot filtering has usable log evidence, it also normalizes the extracted DDS Component filename and `TextureUsage.json` ownership to the surviving evidence.

**贴图过滤：跳过 Dirty Slot (Skip Dirty Slots)** keeps slots with explicit `PSSetShaderResources` evidence from `log.txt`. Inherited service-slot bindings are also kept only when writer and consumer are color passes from different `vb0` objects, have fresh matching character `cb4` evidence, and share a fresh material identity at `cb5` or `cb6`. Same-`vb0` Component persistence, depth-only inheritance, and inherited main-material slots remain excluded because a bound residual resource does not prove that the shader consumed it. If no usable log evidence exists, Velo preserves legacy STU records instead of guessing deletions.

Filters such as small texture, `.jpg`, known cubemap, and same-slot same-Hash can remove useful assets. Change defaults only when you understand the capture.

### 2. Import the WWMI Object

Set **模式 (Mode)** to **导入对象 (Import Object)**.

1. Select the extracted object source folder.
2. Choose vertex-color storage.
3. Choose **Merged** or **Per-Component** import.
4. Keep component sub-collections on for organized `C0/C1/...` authoring.
5. Enable texture import if you want source DDS previews.
6. Run **导入模型 (Import Model)**.

Texture import prefers `ShaderTextureUsage.json` and falls back to `TextureUsage.json`.

### 3. Choose a WWMI Skeleton Strategy

Import offers **Merged** and **Per-Component**. Export adds **Per-Component (from Merged)**.

| Strategy | Authoring | Runtime and limits |
| --- | --- | --- |
| **Merged** | Unified vertex-group list | Easy cross-component weights and skeleton scale; one-frame update delay; pauses when several identical targets are on screen |
| **Per-Component** | Component-local groups | No one-frame delay; simpler runtime; restricted weights; no custom skeleton scale |
| **Per-Component (from Merged)** | Import and edit as Merged | Exports component-local runtime buffers after translating unified groups |

Use **Per-Component (from Merged)** when you want Merged authoring but Per-Component runtime behavior.

The translation is strict. A component with nonzero weight on a bone outside its allowed component map fails export. Move or clear that weight instead of bypassing the error.

For a normal Per-Component project, import and export as Per-Component. Do not select **from Merged** for data that was authored with local group names.

### 4. Edit and Export WWMI

Set **模式 (Mode)** to **导出 Mod (Export Mod)**.

1. Select the component collection.
2. Select the same object source folder used for import.
3. Select the mod output folder.
4. Select the intended export skeleton strategy.
5. Keep **复制贴图 (Copy Textures)** and **写出 mod.ini (Write mod.ini)** enabled.
6. Keep **写入注释 (Comment INI)** enabled when human-readable output matters.
7. Configure any Velo compatibility options.
8. Export and review Blender's final status.

**导出时自动按材质拆分 (Auto Split by Material on Export)** follows the same rules for WWMI single-IB, Cross-Scene, and **Per-Component (from Merged)** exports. Current WWMI hidden-object and collection options remain authoritative. Material ownership conflicts fail closed; disabling the option performs neither validation nor partitioning.

The complete path writes `mod.ini`, `Meshes`, and `Textures`.

**部分导出 (Partial Export)** is an advanced buffer-only path. It disables INI generation and resource copying, so its output is not a complete standalone mod.

The default WWMI texture path is native Hash-style replacement. Slot-style is optional and described in [WWMI Texture Strategy](#wwmi-texture-strategy).

## WWMI Extended

### WWMI LOD

The WWMI LOD tool adds distance geometry to an existing object source.

1. Extract the base object first.
2. Capture a Frame Dump while the game is actually drawing the target LOD.
3. Open **LOD 数据提取 (LOD Data Extraction)**.
4. Select the LOD dump and existing object source folder.
5. Set component and Hash filters if needed.
6. Run **提取 LOD 数据 (Extract LOD Data)**.
7. Export the mod normally.

Existing LOD data is protected unless overwrite is enabled.

Start with the default voxel matcher. If matching fails, tune the error threshold, voxel size, prefilter candidates, or switch to point-cloud matching.

LOD evidence can flow through a WWMI cross-scene merged source. Capture each LOD while that geometry is visible, not at the main model's distance.

### WWMI Cross-Scene Multi-IB

Use **跨场景折叠合并 (Cross-Scene Fold Merge)** when one target uses different IB routes in different scenes.

Prepare one base extracted folder and one extracted folder for every additional scene IB.

#### Choose an IB Role

| Role | Meaning |
| --- | --- |
| **折入基底 (Fold into Base)** | The base authoring geometry represents this route |
| **独立可编辑 (Editable)** | The route owns separate geometry, form, or authoring identity |
| **形态合并 (Merge Form)** | An extracted folder with the same `vb0 hash` contributes only its STU and texture-form evidence to one Fold route; it does not add duplicate geometry. An empty label becomes `form2`, `form3`, and so on. |

`Fold` does not mean every route is forced into one physical buffer. Velo redirects compatible data and automatically keeps an own-buffer path when format or skeleton evidence requires it.

`Editable` becomes independently editable geometry with its own component identity in the merged source.

Use `Merge Form` only when the selected extract has the same `vb0 hash` and identical Component/LOD/VG Metadata as exactly one Fold row. Velo fails closed on a missing, ambiguous, or structurally different target instead of silently changing roles.

#### Merge and Author

1. Set the base extracted folder.
2. Add each additional IB folder.
3. Assign `Fold`, `Editable`, or `Merge Form` to every row. For two texture forms of one runtime IB, keep one row as `Fold` and mark the other same-`vb0` extract as `Merge Form`.
4. Select a new merge output folder.
5. Run **合并跨场景 (Merge Cross-Scene)**.
6. Import the merged output folder with normal WWMI import.
7. Edit the resulting component collection.
8. Export with normal WWMI export.

The merged root contains `CrossSceneManifest.json` schema v3. Its presence activates the cross-scene direct compiler automatically.

The merged root is self-contained and is the only persistent source of truth:

```text
<merged-root>/
  Component N.fmt/.vb/.ib
  Metadata.json
  ShaderTextureUsage.json
  CrossSceneManifest.json
  *.dds
```

It does not contain or require `scene_ibs/*`. Always keep this root as **对象源目录 (Object Source Folder)**; do not switch back to one of the original extraction folders.

The files have separate authority. Root `Metadata.json` owns base geometry, LOD, VG, and canonical morph data. Root `ShaderTextureUsage.json` owns final global Component/slot/route texture evidence. The actual top-level DDS files are the live texture inclusion catalog. `CrossSceneManifest.json` contains only the remaining runtime IB ownership and routing facts; it does not copy root Metadata or STU.

Legacy roots containing only `CrossSceneRouting.json` schema v2 are rejected. Re-run **合并跨场景 (Merge Cross-Scene)** with the current Velo version instead of copying old child folders forward.

Re-run the merge when source captures, roles, split objects, or routing assumptions change.

**Per-Component (from Merged)** is the recommended cross-scene path when authoring uses unified groups but runtime output should remain component-local.

Final cross-scene component names use merged-root global component IDs. A suffix such as `_ib1` is only the owning IB namespace; it is not a second local component number.

Cross-scene export captures the native selection once and compiles final typed sections directly. It does not create child INIs, child Meshes/Textures folders, slot contracts, or an assembler merge. The final phase order is:

```text
Mod State / Constants / Present
→ Mod Info
→ Draw Call Stacks Processing
→ Shading: Textures
→ Shape Keys
→ Buffer Resources
→ Autogenerated
```

### WWMI Custom ShapeKey Export

**导出自定义 ShapeKey (Export Custom ShapeKeys)** is enabled by default for both ordinary single-IB and Cross-Scene export. Classification comes only from the object source `Metadata.json`: each batch owns 127 consecutive Deform IDs, the leading `shapekey_count` IDs are game-native, and range-external IDs with a nonzero position delta are external custom ShapeKeys. Blender name suffixes do not affect classification.

- **Enabled:** native IDs remain on the game's native WWMI ShapeKey path. Custom records are removed from the native buffers and an independent shader adds them only to the static Position buffer; the game-native `vb6` binding remains untouched so native deformation is composed by the original game shader.
- **Disabled:** custom records are still removed from the native buffers, but no custom variables, buffers, shaders, or INI logic are emitted.
- **No effective custom delta:** no empty external resources or runtime logic are emitted, even when the option is enabled.

Every effective custom ID receives one unclamped persistent variable, preceded by a comment containing the original Blender name or deterministically merged names:

```ini
; ShapeKey_161: Smile
global persist $ShapeKey_161 = 0.0
```

The same ID shares one variable across components and IB domains. Negative values and values above `1.0` are allowed. Variable names use only the numeric ID; Blender suffixes are preserved as comments rather than identifiers.

An already stored value in `d3dx_user.ini` can override the default edited in `mod.ini`. Update or remove the matching persisted value when changing defaults. ShapeKey data changes require a complete export; Partial Export cannot update this pipeline independently.

### WWMI Texture Strategy

#### Native Hash-Style

Hash-style replacement uses the captured texture resource identity. It is the default when **插槽风格贴图 (Slot-Style Textures)** is off.

Use Hash-style when the target identities are stable and no texture-streaming fallback is observed.

Hash-style cannot infer an identity missing from the source. A visually identical DDS can still have a different runtime Hash.

#### Asset-Name Matching

When an F8 frame dump contains `TextureAssetManifest.jsonl`, extraction first completes its normal DDS filtering and naming, retains the original DDS-backed `ShaderTextureUsage.json` record set, and adds the full Unreal Object Path as an optional `asset_path` field only to records backed by a named DDS that actually exists in the extracted folder. Unretained observations, hashless descriptors, filtered textures, and missing files are not emitted as extra STU records. Form merges apply the same rule after copying their retained DDS files; editable-IB remapping and Cross-Scene aggregation preserve the confirmed field.

Enable **使用资产名称匹配 (Use Asset-Name Matching)** under WWMI export **Velo 兼容选项 (Velo Compatibility Options)** to convert eligible native Hash overrides to:

```ini
match_asset_name = T_Example_D
```

Asset-name overrides use the normal draw-scoped `CheckTextureOverride` pre/post path. Their gate follows the generated legacy Hash section: ordinary single-IB exports retain `$object_detected`, while Cross-Scene exports use `$mod_enabled_ib0 || $mod_enabled_ib2`. `$\WWMIv1\enable_mods` is not used as a substitute. The exporter does not emit `match_priority`, the full path, or a pixel fingerprint. Known duplicate short names that refer to different full paths fail closed; records without captured path evidence remain on their native Hash path. Asset-name matching and slot-style are mutually exclusive export modes.

#### Slot-Style

Slot-style replacement binds mod resources directly to `ps-tN` slots inside component draw transactions.

Enable **插槽风格贴图 (Slot-Style Textures)** under WWMI export **Velo 兼容选项 (Velo Compatibility Options)**.

Requirements:

- a recent `ShaderTextureUsage.json`;
- recorded DDS format metadata;
- enough fresh slot-layout evidence to distinguish every emitted branch;
- every referenced mod DDS present in the object source or output.

Velo emits only branches with a complete positive assignment signature. Every assigned slot must also appear as usable positive format-family evidence. When one texture occupies different service slots in separate, safely distinguishable shader branches of the same Component and form, Velo emits one conditional assignment per observed branch and uses full slot backup/restore instead of collapsing the texture to Hash fallback.

Ambiguous or incomplete evidence fails closed. Velo does not emit broad fallback probes merely to make export succeed.

#### Mix Hash and Slot by Component

Hash-style and slot-style can coexist in one mod.

1. Enable slot-style.
2. Click **列出组件 (List Components)**.
3. Leave components checked when they should use slot-style.
4. Uncheck a component when it must retain native Hash-style replacement.

An unchecked component is an absolute opt-out. Velo must not force it back into slot-style because routing or provenance happens to be available.

In cross-scene output, an opted-out Hash override is gated by its owning `$object_detected_ibN`. Shared identities OR only the proven owner IB flags.

Velo does not add a separate component runtime gate. Legacy `$component_hash_fallback_*` variables are invalid and rejected by audit.

#### Slot Transactions and Restore Safety

Velo backs up `ps-t0..8`, binds the selected resources, draws, cleans up, then restores the previous resource state.

Selective no-restore is derived only when final branch evidence proves one unique displaced slot. Otherwise Velo uses a full restore.

This prevents a later outline or body draw from matching stale textures while still preserving a slot only when the runtime transition requires it.

### Multi-Form Texture Evidence

Use **形态贴图合并 (Merge Form Textures)** when one WWMI target has several forms with different texture bindings.

1. Capture a near-distance RAW Frame Dump for one extra form while its textures are fully bound.
2. Select that dump and the object source folder.
3. Enter a stable form label or let Velo assign one.
4. Run **合并形态贴图数据 (Merge Form Texture Data)**.
5. Repeat for every extra form.

You do not need to perform another full object extraction for an extra form.

Reuse the same form label with dumps from other distances to accumulate that form's additional streaming identities. Use the label `base` when evidence belongs to the base form.

The merge updates `ShaderTextureUsage.json` and copies required form textures into the object source.

For a schema-v3 cross-scene root, the same operation writes only the root `ShaderTextureUsage.json`: manifest component maps translate fold-route local evidence into global Components. It never creates or updates child STU files.

Only real same-VB form variants should share a form domain. Separate editable or different-VB geometry stays in its own domain.

### Optional Form Anchors

**formid 辅助判据 (formid Auxiliary Criterion)** is off by default. It may narrow a branch that is already safe by local slot-layout evidence.

It cannot rescue a component whose forms have indistinguishable slot layouts.

Supported manual anchor identities:

- 8 hexadecimal characters: a `vb0` Hash;
- 16 hexadecimal characters: a pixel shader Hash.

An `ib` Hash is not a valid form anchor. A vertex shader Hash has the same reliability problem and is not accepted as a supported manual format.

Use `hash:formLabel`, separated by commas, spaces, or newlines.

The anchor finder compares the base dump with extra-form dump rows. It lists character geometry confirmed by character constant-buffer and body evidence.

VFX or UI anchors may require manual in-game `vb0` confirmation.

If exactly one form has no anchor, the watchdog can identify it by elimination when no anchored form appears during the frame.

After a game update, refresh stale anchors from new dumps. Do not convert an unrelated `ib` value merely because it is visible in a filename.

### WWMI Raw Mesh

Use **原始网格工具 (Raw Mesh Tools)** for VFX, scene, environment, or static geometry that the normal skinned-character pose chain does not detect.

Do not use Raw Mesh as a substitute for the standard character workflow.

#### Extract

1. Set **模式 (Mode)** to **提取帧数据 (Extract Frame Data)**.
2. Select a Frame Dump and output parent folder.
3. Enter comma- or newline-separated VB/IB Hash values.
4. Leave **Position 元素 (Position Element)** empty unless automatic detection fails.
5. Run **按 Hash 提取网格 (Extract Mesh by Hash)**.

A VB Hash selects the whole `VB0` object and all draw calls sharing it. An IB Hash selects the matching draw/component.

If one Hash resolves across several objects, Velo refuses the ambiguous request. Use a specific `VB0` or IB identity.

#### Import

Set Raw Mesh mode to **导入对象 (Import Object)** and import the consolidated folder.

Velo exposes the selected Position element as editable geometry and preserves raw per-slot bytes as mesh attributes and object metadata.

#### Export

Select the imported collection, a mod output folder, and one mode:

| Mode | Topology | Attribute behavior |
| --- | --- | --- |
| **自动 (Auto)** | Detects change | Faithful if unchanged; Rebuild otherwise |
| **保真直通 (Faithful)** | Must remain unchanged | Re-encodes Position; passes other stored bytes through |
| **重建 (Rebuild)** | May change | Rebuilds layout; non-standard attributes are best-effort and lossy |

Faithful refuses a changed index count. Rebuild may zero-fill or lose attributes that Blender cannot represent cleanly.

Raw Mesh output uses independent plain 3dmigoto overrides. Each component matches its own source Hash and original draw range.

## Read and Validate Output

### Advanced Export Panels

EFMI and WWMI expose advanced panels for metadata and generated INI behavior.

#### Mod Info

**Mod 信息 (Mod Info)** can set the mod name, author, description, link, and logo.

The logo must be a 512x512 `.dds` using BC7 SRGB. It is exported as `Textures/Logo.dds`.

Cross-scene output contains one unsuffixed `ResourceModName/Author/Desc/Link/Logo` set for the whole mod. Each owning IB keeps its own registration lifecycle and object GUID, but every `CommandListRegisterMod_ibN` references that same shared Mod Info payload.

#### INI Template

**INI 模板 (INI Template)** can replace the complete generated `mod.ini` with a custom Jinja2 template stored in Blender or an external file.

Use the default template unless you maintain the full runtime contract yourself. A stale custom template can omit resources or logic introduced by a newer Velo release.

Live template update rewrites `mod.ini` as the template changes. Point it only at a disposable or intended output, and stop live update before changing projects.

Cross-scene export does not support arbitrary custom Jinja templates or live template update. It rejects either option before writing output because the direct compiler owns the complete multi-IB INI contract. Ordinary single-IB template behavior is unchanged.

#### INI Toggles

**INI 开关 (INI Toggles)** generates state variables, hotkeys, object visibility states, and optional custom conditions.

- Use spaces for keys in one combination.
- Use semicolons to separate several hotkey combinations.
- `AND` conditions evaluate before `OR` conditions.
- Import/export uses JSON text; choose replace or clear behavior deliberately.

Validate every state in game. A syntactically valid toggle can still target the wrong object or conflict with custom INI logic.

#### Partial Export

**部分导出 (Partial Export)** selects individual buffer classes. It intentionally skips complete INI and resource delivery.

Use it only to update an existing mod whose unchanged files are already present. Do not distribute a partial export as a complete package.

### Expected Full-Export Layout

```text
<mod-output>/
  mod.ini
  Meshes/
  Textures/
```

Optional features can add shader, toggle, or logo resources.

### Reading a Velo WWMI INI

Velo sorts generated sections by function without changing match-bearing override order.

When one texture resource maps to one Hash override, the output places its `ResourceTexture` immediately before the matching `TextureOverride`, matching native WWMI reading order.

Shared or ambiguous resources are not moved into a false one-to-one pair.

Cross-scene component-bearing names use the merged/global component ID. `_ibN` identifies the owning IB namespace.

Geometry is extracted into a shared command list only when at least two callers reuse the identical body. Single-use draw bodies stay inline.

Sections named `ResourceBypassPST0..8` are intentionally empty dynamic reference handles. Backup lists assign the current `ps-t0..8` resources into them before a slot transaction.

Do not delete those empty sections. Velo removes generated handle groups with no transaction and rejects malformed or unreferenced groups during audit.

### Texture Delivery and Author Edits

For a full cross-scene texture export, every top-level merge-root DDS filename must exist in the final `Textures` folder.

Each existing root DDS must use a canonical `Components-... t=<hash>.dds` name whose Component set exactly matches that Hash's final root-STU ownership. Velo fails before writing output if the filename widens or narrows that set. Runtime route evidence does not widen the canonical filename.

Velo copies only missing files. If the output already contains a same-name author-edited DDS, Velo preserves it.

Output-only DDS files and non-DDS tools are not deleted.

A DDS deleted from the aggregate root is an intentional catalog opt-out, not a missing-file error. It is excluded from every newly compiled Resource, assignment, fallback, and restore plan. An older copy may remain in the output folder, but the regenerated INI does not reference it.

If one Hash has conflicting payloads or multiple canonical root filenames, export fails before overwriting the output.

Turning **复制贴图 (Copy Textures)** off, or using partial export, intentionally skips final texture delivery.

### Static and Runtime Validation

After export:

1. Read Blender's final status message.
2. Treat a cross-scene self-check or static-audit warning as a failed export.
3. Confirm `mod.ini` exists for a full export.
4. Confirm every referenced Mesh, Texture, and shader file exists.
5. Test the mod in every supported runtime state.

For a normal character, test initial load, character switch, menu/showcase re-entry, and an F10 reload.

For cross-scene work, test every merged scene from a cold entry and after scene switching.

For multi-form work, test every form, repeated switches, and streamed near/far states.

For LOD work, test the model at each intended distance.

For mixed Hash/slot work, verify both slot-enabled and opted-out components. An opted-out component must remain on its native Hash path.

Static audit and captured-frame replay verify INI/resource closure, but they do not prove that the game loaded the replacement. This is especially true when hole export changes `drawindexed` parameters that are absent from the original capture. Complete the runtime matrix in the actual game before accepting a cross-scene build.

## Troubleshooting by Symptom

### The EFMI or WWMI Panels Are Missing

Open the 3D Viewport sidebar, choose **Velo Tools**, select **游戏 (Game)**, then choose **终末地** or **鸣潮**.

If the tab is absent, confirm **Velo-Tools** is enabled and restart Blender.

### The Updater Fails or Tries to Modify Source Files

Do not use the updater from a junction/source-link install. Remove the development link or install a release asset into a normal add-on directory first.

### Import Succeeds but Textures Are Missing

Confirm texture import was enabled and the object source still contains its DDS files.

EFMI uses `TextureUsage.json`. WWMI prefers `ShaderTextureUsage.json` and falls back to `TextureUsage.json`.

### Export Finds No Component Objects

Confirm the correct component collection is selected.

If meshes are in `C0/C1/...` child collections, disable **忽略嵌套集合 (Ignore Nested Collections)**.

Also check hidden collection, hidden object, and muted ShapeKey filters.

### EFMI Merged Import or Export Reports a Missing Map

Generate `VertexGroupMap.json` during extraction. For an older source, use the legacy Metadata conversion action.

`VertexGroupMap.json` is not LOD data; extract LOD data separately when required.

### Per-Component (from Merged) Rejects Stray Weights

The reported group maps outside that component's allowed local bone set. Move the weight to a valid bone or clear it to zero.

Do not rename the error away. Per-Component runtime buffers cannot express that cross-component weight.

### A PFM Split Object Disappears

Keep exact `Component N` identity in object names and export from the merged source collection. Rebuild cross-scene routing after changing split ownership.

Check nested/hidden filters before assuming the draw was removed by runtime logic.

### A WWMI Texture Stays Native

For Hash-style, compare the runtime identity with the selected dump. A new streaming identity requires fresh dump evidence.

For slot-style, confirm the component is checked, STU is current, and export did not report an ambiguous branch.

Entering another scene first can warm streamed resources and hide a stale capture problem. Always test cold entry as well as hot scene switching.

### A Replaced Texture Looks Green or Like a Normal Map

First verify that the diffuse Resource points to the intended DDS rather than a normal-map Hash or slot.

Then inspect the complete slot assignment signature and restore transaction. Do not fix the symptom by hardcoding one component or slot.

### Slot-Style Export Aborts

Common causes are:

- missing or stale `ShaderTextureUsage.json`;
- missing DDS format metadata;
- a required assignment slot absent from the positive signature;
- indistinguishable form layouts;
- a missing DDS/resource;
- malformed cross-scene routing.

Refresh the dump/STU or uncheck only the affected component so it uses native Hash-style. Do not weaken the slot signature or add an unproven fallback branch.

### Form Switching Flickers Back to Native Textures

Capture near and far RAW dumps for that form and merge them under the same label.

Confirm every same-VB form is represented. Use anchors only as an auxiliary gate after slot-layout branches are already safe.

### Cross-Scene Export Misses a Scene

Confirm that scene's extracted IB folder is present in the merge and has the correct `Fold` or `Editable` role.

Re-run the merge and keep the merged root as the object source during import and export.

If the root contains only `CrossSceneRouting.json`, it is schema v2 and must be re-merged. Do not restore an old `scene_ibs` folder as a workaround.

### Cross-Scene Output Is Missing a Root DDS

Confirm **复制贴图 (Copy Textures)** is enabled and partial export is off.

If export reports a same-Hash payload conflict, resolve the conflicting source files. Do not overwrite one payload arbitrarily.

### CrossIB.json Is Missing or Obsolete

Select one current Frame Dump through **生成 / 重新生成 CrossIB.json v2**. Velo rejects a dump that contains none of the source object's Component IBs and leaves the previous JSON unchanged.

Do not accumulate separate dodge, attack, outline, or afterimage dumps. Those passes are covered by the common ShaderRegex capability classifier. If a future shader structure genuinely exceeds the common profile, update Velo Tools rather than adding per-character VS Hashes.

### LOD Matching Fails

Capture while the game draws the desired LOD, not the main mesh.

Then adjust the geometry error threshold, voxel/sample size, prefilter candidates, or matcher method. Enable overwrite only when replacing existing LOD evidence deliberately.

### EFMI ShapeKey Export Is Blocked

Inspect the detected list for duplicate numeric Deform IDs on the same object. Assign each exported ShapeKey on that object a unique ID. Different names may share an ID across components, and the same name may use different IDs. Non-Deform ShapeKeys are ignored rather than exported.

### Weight Transfer Fails Before Writing

Check manual target, donor, mirror target, and mirror-donor selections. Manual choices are strict and are validated before mutation.

If a disconnected island stays zero, it has no positive source evidence. Adjust the source geometry or matching settings instead of forcing inpaint.

### Raw Mesh Reports an Ambiguous Hash

Use the specific `VB0` Hash for one object or the specific IB Hash for one draw.

A generic VB identity shared by several objects is deliberately rejected.

### Raw Mesh Faithful Export Rejects Topology

Faithful requires the original topology and index count. Undo the topology edit or choose Rebuild and accept the non-standard attribute loss.

### The INI Contains Empty `ResourceBypassPST` Sections

They are required slot-transaction backup handles. Their values are assigned at runtime, so an empty declaration is correct.

## Limits and Glossary

### Limits

- Velo cannot infer game resource identity from texture pixels.
- A stale dump cannot describe Hash or shader identities introduced later.
- Slot-style export cannot safely separate forms with identical observable slot layouts.
- Form anchors cannot replace missing slot evidence.
- Per-Component runtime output cannot express arbitrary cross-component weights.
- Raw Mesh Rebuild cannot preserve every non-standard vertex attribute.
- Weight transfer still requires visual deformation review.
- Custom INI templates remain the author's responsibility.

### Glossary

| Term | Meaning |
| --- | --- |
| **Frame Dump** | Runtime capture used as extraction and matching evidence |
| **Object source folder** | Extracted authoring input consumed by import and export |
| **Component** | One logical mesh/draw partition in EFMI or WWMI metadata |
| **VB / VB0** | Vertex buffer / primary vertex-buffer identity |
| **IB** | Index-buffer identity and draw-index source |
| **Hash-style** | Texture replacement matched by captured resource Hash |
| **Slot-style** | Texture replacement rebound to `ps-tN` inside a draw transaction |
| **STU** | `ShaderTextureUsage.json`, Velo's WWMI shader/slot evidence file |
| **Merged** | Unified vertex-group authoring/runtime strategy |
| **Per-Component** | Component-local vertex-group runtime strategy |
| **PFM** | Per-Component (from Merged): unified authoring, local runtime output |
| **Fold** | WWMI scene route authored from the base geometry |
| **Editable** | WWMI scene route with independent editable geometry |
| **CrossIB** | EFMI provider geometry drawn through a target component pass |
| **CrossIB capability ABI** | Hash-free community-compatible EFMI shader roles 200/201/202/203/204/205 produced by the shared classifier and consumed by each mod's routing |
| **Cross-scene** | WWMI merge of several scene-specific IB routes |
| **Self-contained aggregate root** | The only persistent cross-scene source: aggregate buffers, metadata/STU, top-level DDS, and schema-v3 manifest, with no child payload dependency |
| **Canonical morph namespace** | The aggregate root's stable runtime ShapeKey ID space, including deterministically assigned IDs for proven source-only morphs |
| **Deform ID** | A numeric WWMI ShapeKey identity; each batch owns 127 consecutive IDs |
| **Native ShapeKey** | A Metadata-owned Deform ID that remains on the WWMI native shader path |
| **External Custom ShapeKey** | A range-external Deform ID with an effective delta, added by the independent shader path |
| **Form anchor** | Optional `vb0` or pixel-shader identity used to narrow a safe form branch |
| **Raw Mesh** | WWMI path for non-character geometry with preserved raw slot bytes |
| **Owning IB namespace** | `_ibN` suffix and lifecycle state isolating one cross-scene route |
