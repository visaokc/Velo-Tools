# Velo Tools 用户手册（中文）

> 适用版本：Velo Tools v1.4.0。本文面向 Blender 用户与 mod 作者；Blender、GIMI、3Dmigoto、IB、VB、Hash、MERGED、Per-Component、Frame Dump、LOD、INI、ShapeKey 等技术名词保留原文。

## 目录

1. [快速引导](#快速引导)
2. [安装与更新](#安装与更新)
3. [界面与游戏切换](#界面与游戏切换)
4. [Shared tools](#shared-tools)
   - [顶点组工具与 general mapping](#顶点组工具与-general-mapping)
   - [网格工具](#网格工具)
   - [Weight Tools](#weight-tools)
5. [Arknights: Endfield / EFMI 工作流](#arknights-endfield--efmi-工作流)
   - [EFMI 提取、导入与导出](#efmi-提取导入与导出)
   - [CrossIB](#crossib)
   - [EFMI ShapeKey export](#efmi-shapekey-export)
6. [Wuthering Waves / WWMI 工作流](#wuthering-waves--wwmi-工作流)
   - [WWMI Extract / Import / Export](#wwmi-extract--import--export)
   - [Per-Component (from Merged)](#per-component-from-merged)
   - [LOD](#lod)
   - [cross-scene multi-IB](#cross-scene-multi-ib)
   - [slot-style texture export](#slot-style-texture-export)
   - [Merge Form Textures](#merge-form-textures)
   - [form anchors](#form-anchors)
   - [Raw Mesh](#raw-mesh)
7. [常见问题与限制](#常见问题与限制)

## 快速引导

1. 从 GitHub Releases 下载最新 `velo_tools-<version>.zip`。
2. 在 Blender 中通过 **Edit -> Preferences -> Add-ons -> Install from Disk...** 安装 zip。
3. 启用 **Velo-Tools**。
4. 在 3D Viewport 按 `N`，打开 **Velo Tools** sidebar tab。
5. 顶部功能区通常这样选择：
   - **顶点组工具**：vertex group 批处理、名称映射、MMD / unified 互转。
   - **网格工具**：material split / merge、shape-key aggregation、多物体 sculpt 辅助。
   - **权重工具**：Robust 权重传递、mirror、donor normalization、smoothing、group limiting。
   - **游戏**：进入 EFMI 或 WWMI 的具体提取、导入、导出流程。
6. 在 **游戏** tab 选择目标游戏：
   - **Arknights: Endfield / EFMI**：EFMI import/export、CrossIB、EFMI ShapeKey export。
   - **Wuthering Waves / WWMI**：WWMI extract/import/export、LOD、cross-scene multi-IB、slot-style texture export、Merge Form Textures、form anchors、Per-Component (from Merged)、Raw Mesh。

## 安装与更新

### 安装

1. 从 [GitHub Releases](https://github.com/visaokc/Velo-Tools/releases) 下载最新 `velo_tools-<version>.zip`。
2. Blender：**Edit -> Preferences -> Add-ons -> Install from Disk...**。
3. 选择下载的 zip。
4. 启用 **Velo-Tools**。
5. 在 3D Viewport 按 `N`，打开 **Velo Tools** tab。

### 更新

Velo Tools 使用宿主级 updater 更新整个 `velo_tools/` 插件，而不是让 EFMI / WWMI vendored core 自己更新。

- 默认只显示 stable GitHub Releases。
- 只有主动开启 pre-release 接收时，才会显示 beta / pre-release。
- 更新后重启 Blender。
- 开发期 junction / source-link 安装不要点击 updater 的立即更新；普通用户安装 release zip 时正常使用 updater。

Velo 内置的 EFMI / WWMI core 使用 Velo 自己的 namespace fork；如需更新，更新 Velo Tools 本身即可。

## 界面与游戏切换

Velo Tools 是一个 host add-on：同一个 Blender add-on 内承载通用工具、EFMI 工作流和 WWMI 工作流。

| 功能区 | 用途 |
| --- | --- |
| 顶点组工具 | vertex-group 操作、mapping table、可视化校对 |
| 网格工具 | material tools、split/merge helper、多物体 sculpt、shape-key aggregation |
| 权重工具 | Robust weight transfer、mirror transfer、donor normalization、smoothing、group limiting |
| 游戏 | EFMI / WWMI 的 game-specific mod workflow |

进入 **游戏** 功能区后，用 game dropdown 选择：

- **Arknights: Endfield / EFMI**
- **Wuthering Waves / WWMI**

只会显示当前游戏相关的面板，减少 EFMI 和 WWMI 设置混用。

## Shared tools

### 顶点组工具与 general mapping

顶点组工具适合在导出前整理 vertex groups。

常见任务：

1. 选择 source mesh 和 target mesh。
2. 建立或加载 mapping table。
3. 从 source object 填充行。
4. 按位置匹配写入 target rows。
5. 执行 source / target rename，或恢复原名。
6. 使用 overlay 和 unmatched list 检查映射质量。

mapping table 可以存入 `.blend` 的 Text block，也可以 import / export 为外部文件。

批处理操作包括：

- Merge vertex groups。
- Fill gaps in vertex groups。
- Remove unused vertex groups。
- Remove all vertex groups。

### 网格工具

网格工具用于 Blender 数据整理，通常在 game export 之前使用。

- **多物体 sculpt**：创建 merged sculpt object，把 sculpt 结果应用回源对象，也支持带 ShapeKey 的应用流程。
- **Material tools**：添加 `Component` 前缀、生成材质、按材质拆分、按贴图合并、补齐 mesh data、对带 ShapeKey 的对象应用 modifiers、转换 vertex colors。
- **Material routing**：预览当前 active game export collection 下的 material groups，把 material / texture 分组拆入目标 collections。
- **Shape-key aggregation**：扫描 collection 内 mesh ShapeKeys，按同名 key 聚合、改名并同步 value。

这里的 shape-key aggregation 是 Blender 组织工具；EFMI runtime ShapeKey export 是后面单独说明的游戏功能。

### Weight Tools

Weight Tools 用于把 source vertex group 安全转移到 target mesh，并尽量保留其它权重生态。

典型流程：

1. 设置 **Source Mesh**。
2. 选择 **Source Vertex Group**。
3. 可选设置 **Mirror Vertex Group**。
4. 设置 **Target Mesh** 和可选 **Target Armature**。
5. 选择 transfer engine：
   - **Robust**：surface matching + inpaint，是 Velo 默认主路径。
   - **Surface interpolation transfer**：Blender Data Transfer 风格的 surface interpolation。
6. 确认或手动覆盖 target group name。
7. 检查 donor groups。
8. 执行 **Weight Transfer**。
9. 查看 last report。

重要行为：

- Robust transfer 会使用 source-side weight context，避免把弱证据孤岛写成权重。
- donor groups 用于 normalization，是最大 donor set，不要求填满所有槽位。
- 手动 donor slots 是严格覆盖；自动 donor slots 是预览建议，直到你手动编辑才固定。
- mirror transfer 可解析 numeric 或 named mirror pairs，并能把手动 mirror mapping 持久化在 scene 中。
- locked ordinary groups 在 limit / normalize 时视为受保护容量。
- smoothing 是 seam-safe，可关闭。
- selected-vertex repair 可在 Edit Mode 中修复局部顶点，并把未解决顶点保持选中。

## Arknights: Endfield / EFMI 工作流

在 **Game -> Arknights: Endfield / EFMI** 下使用 EFMI 工作流。Velo 内置 vendored EFMI core，并在 host 层加入 Velo 扩展。

### EFMI 提取、导入与导出

常见 modes：

- **Extract Frame Data**
- **Import Object**
- **Extract LOD Data**
- **Export Mod**

常见 EFMI export 字段：

- Component collection。
- Object source folder。
- Mod output folder。
- Export skeleton mode。
- Mirror mesh。
- Apply modifiers。
- Copy textures。
- Write `mod.ini`。
- Ignore nested / hidden collections 或 hidden objects。
- Ignore muted shape keys。
- Add missing vertex groups。
- Fill missing mesh data。
- Allow export without LODs。

提取时使用有效 Frame Dump folder 和 output folder。过滤选项可跳过 static objects、小贴图、`.jpg` 贴图、低于阈值的 objects / components 或指定 resource hashes。

### CrossIB

CrossIB 让一个 component 跨 index buffer 借用另一个 component 的 rendering pipeline。

基本流程：

1. 切到 **Game -> Arknights: Endfield / EFMI**。
2. mode 设为 **Export Mod**。
3. 打开 **Cross Index Buffer / CrossIB**。
4. 启用 **Use Cross Index Buffer**。
5. 如果 object source folder 中没有 sidecar data，从 Frame Dump 生成或合并 `CrossIB.json`。
6. 添加 mapping：
   - **Object mapping**：单个 mesh object 作为 provider。
   - **Collection mapping**：collection 内每个 mesh 都可作为 provider。
7. 设置每行 target component。
8. 正常导出。

注意：

- 已存在 `CrossIB.json` 和 `ShaderOverride.ini` 时会直接消费。
- 可从更多 scene dumps 累积 sidecar evidence。
- overwrite / rebuild 只在你明确要重算时使用。

### EFMI ShapeKey export

EFMI ShapeKey export 是导出期功能，用于把 Blender 中的 custom ShapeKeys 写入 mod。

命名规则：

```text
Deform <slot> <name>
```

示例：

```text
Deform 1 Smile
Deform 2 Blink
Deform12 CapeLift
```

流程：

1. 在 active EFMI component collection 内的 meshes 上保留或创建 ShapeKeys。
2. 按 `Deform <number> <name>` 命名。
3. 在 EFMI advanced export options 中启用 **Export custom shape keys**。
4. 除非需要 legacy per-slot buffers，否则保持 **Merge Buffer Files** 开启。
5. 检查 detected shape-key list。
6. 修复 naming conflicts 后导出。

会阻止导出的情况：

- 同一 object 上重复 `(slot, name)`。
- 同一个 name 被分配到不同 Deform slots。
- 同一 Deform slot 在不同 components 上对应不同 names。
- sanitized INI-safe variable names 发生冲突。

## Wuthering Waves / WWMI 工作流

在 **Game -> Wuthering Waves / WWMI** 下使用 WWMI 工作流。Velo 内置 namespaced WWMI fork，可与 standalone WWMI-Tools 共存。

### WWMI Extract / Import / Export

#### Extract Objects From Dump

用于把 WWMI Frame Dump 转成 object source folder。

常见选项：

- Frame Dump folder。
- Output folder。
- Skip small textures。
- Minimum texture size。
- Skip `.jpg` textures。
- Skip known cubemap textures。
- Skip same-slot hash textures。
- 有 log freshness evidence 时跳过 dirty / inherited slot records。

提取会写出 slot-style texture export 需要的 texture usage data。

#### Import Object

用于把 object source folder 导入 Blender。

关键字段：

- Object source folder。
- Vertex color storage。
- Import skeleton type：
  - **Merged**
  - **Per-Component**
- Import as component sub-collections。
- Import textures。
- Skip empty vertex groups。
- Mirror mesh。

Velo import extras：

- **Import as component sub-collections** 会创建 `C0`、`C1` ... 子集合，并自动连接 export collection。
- **Import textures** 在 texture usage data 可用时，为 imported meshes 分配 diffuse / source textures。
- 关闭这些选项可复现更接近 stock 的 single-collection、no-material import。

#### Export Mod

用于把编辑后的 Blender collection 导出为 WWMI mod。

常见字段：

- Component collection。
- Object source folder。
- Mod output folder。
- Skeleton：
  - **Merged**
  - **Per-Component**
  - **Per-Component (from Merged)**
- Mirror mesh。
- Apply all modifiers。
- Copy textures。
- Write `mod.ini`。
- Comment `mod.ini`。
- Ignore nested collections。
- Ignore hidden collections。
- Ignore hidden objects。
- Ignore muted shape keys。
- Partial export options。

普通完整导出时，通常保持 `write_ini` 与 `copy_textures` 开启。

### Per-Component (from Merged)

**Per-Component (from Merged)** 是 WWMI 的 Velo export mode。

适合你想：

- 用 **Merged** skeleton 和 unified vertex-group list 编辑。
- 导出 **Per-Component** runtime mod。
- 避免某些情况下 pure Merged runtime 的缺点。
- 让 Velo 在导出时把 unified vertex groups remap 回 component-local groups。

行为：

- 你按 Merged authoring style 导入和编辑。
- 导出时 Velo 将 unified vertex groups 转回 component-local IDs。
- 如果某顶点权重指向 owning component 允许范围之外的 bones，导出会给出 actionable error。
- 对 cross-scene 项目，这是 body、own-buffer、editable-IB 输出的推荐验证路径。

### LOD

WWMI LOD workflow 会把 LOD frame dump 匹配到已提取 object source folder，再在导出时写出 LOD-aware sections。

流程：

1. 先完成 main object extract / import。
2. 在目标实际以 LOD 距离渲染时抓取 Frame Dump。
3. 打开 **LOD Data Extraction**。
4. 设置 LOD Frame Dump folder 和 Object source folder。
5. 必要时调整 minimum component vertex count、object hash blacklist、geometry match threshold。
6. 匹配失败时再调整 advanced matching method、voxel size、sample size、candidate counts 等。
7. 执行 **Extract LOD Data**。
8. 正常导出。

注意：

- 只有在游戏实际绘制 LOD mesh 时抓取的 dump 才可靠。
- 未启用 overwrite 时，已有 LOD data 会被保护。
- cross-scene merged exports 可以携带 LOD data。

### cross-scene multi-IB

cross-scene multi-IB 把一个 base extraction 和多个 IB-specific extractions 合成一个 editable source folder。

适用于同一对象存在多个 scene-specific IB，而你希望一份编辑结果覆盖多个场景的情况。

流程：

1. 准备 base extracted object folder。
2. 准备一个或多个 additional IB folders。
3. 打开 **Cross-Scene Merge**。
4. 设置 base folder。
5. 添加每个 IB folder，并选择角色：
   - **Fold**：折入 base；兼容时编辑 base 即覆盖该 scene。
   - **Editable**：作为独立 editable geometry 导入，通常用于独立形态或独立 ownership domain。
6. 设置 merge output folder。
7. 执行 merge，生成 merged object 与 `CrossSceneRouting.json`。
8. 用 **Import Object** 导入 merged output folder。
9. 编辑 merged Blender collection。
10. 用 **Export Mod** 正常导出。

重要概念：

- merged folder 是 cross-scene project 的 authoring source。
- 最终导出在 merged output stage 应用 stock-like output options。
- FoldHost routing 会为每个 actual draw 保持一个 canonical draw owner。
- slot-style texture export 支持此路径。
- 许多 cross-scene authoring 场景推荐使用 Per-Component (from Merged)。
- 如果 source captures 或 routing assumptions 改变，应重新 merge。

### slot-style texture export

slot-style texture export 是 WWMI / Velo 功能，用 draw-scope `ps-tN` slot rebinding 替代 texture-hash matching。

入口：**Export Mod -> Velo compatibility options -> Slot-style textures**。

适用原因：

- texture Hash 会随 streaming residency / mip state 改变。
- slot-style 在 component draw scope 内绑定 mod textures。
- 条件基于正向 `ps-tN` DXGI format-family layout evidence，而不是 shader Hash。
- 输出设计上保守、可 audit。

典型流程：

1. 用较新 Velo Tools extract，确保 `ShaderTextureUsage.json` 存在。
2. Import 并编辑对象。
3. 在 Export Mod 中启用 **Slot-style textures**。
4. 可选 refresh component list。
5. 需要 slot-style 的 components 保持勾选。
6. 需要回退 component-scoped hash style 的 components 取消勾选。
7. 正常导出。

行为和限制：

- 如果没有 component list，默认所有 eligible components 走 slot-style。
- 取消勾选的 components 使用 component-scoped hash fallback。
- unsupported ambiguous components 会 fail closed，而不是输出 unsafe hash / probe logic。
- same-layout multi-form components 需要更好的 slot evidence 或 component exclusion。
- slot command lists 使用直接 `ps-tN = ref ResourceTexture...` assignments。
- cross-scene slot-style export 会 audit resource sections、DDS existence、format-family matches 和 command-list structure。
- exporter 会围绕 texture-triggered draw transactions 备份/恢复 `ps-t0..8`，避免 lazy resource state 泄漏。

slot-style 适合解决 texture streaming 稳定性问题，但不能神奇区分 runtime slot layout 完全相同的 components。

### Merge Form Textures

多形态 WWMI 对象使用 **Merge Form Textures** 合并额外 form 的 texture evidence。

流程：

1. 每个额外 form 抓一份 RAW Frame Dump。
2. 打开 **Merge Form Textures**。
3. 设置 Form Frame Dump、Object source folder、可选 form label。
4. 执行 **Merge Form Textures**。
5. 对其它 forms 或同一 form 的补充 captures 重复。
6. 启用 slot-style export 后正常导出。

注意：

- 额外 form 不需要第二次完整 object extraction。
- merge 会读取 RAW Frame Dump 并更新 object folder 的 texture usage data。
- 重复使用同一 form label 可补充同一 form 的证据。
- 输出存储在 object source folder 的 texture usage data 中，export 时使用。
- 只有真实 same-VB multi-form components 应进入 multi-form slot domain；独立 editable / other-VB components 保持 single-form domain。

### form anchors

form anchors 是可选的 WWMI metadata，用于 form tracking。

支持格式：

- **8 hex characters**：dump filename 中的 `vb0` hash。
- **16 hex characters**：pixel shader hash。

不要使用 `ib` hash 作为 form anchor；它不参与此用途的 WWMI draw matching。

手动格式：

```text
hash:formLabel
```

多个 anchors 可用逗号、空格或换行分隔。

示例：

```text
1234abcd:base
89abcdef:form2
0123456789abcdef:form3
```

Anchor finder 流程：

1. 启用 slot-style export。
2. 必要时启用 optional form-id auxiliary / anchor section。
3. 设置 base form dump。
4. 添加 extra form dump rows 和 labels。
5. 执行 **Find Form Anchors**。
6. 检查 candidates。
7. 对有用 candidates 点击 **Apply**。
8. 导出。

限制：

- `$form_id` 是 optional auxiliary state，不是默认 texture discriminator。
- 如果某 component 的 slot-layout evidence 完全不可区分，它不能单独拯救该 component；这类 component 应排除 slot-style 或补充证据。
- 优先使用 `vb0` anchors，因为 geometry identity 通常比 shader identity 更稳定。
- 如果恰好只有一个 form 没有 anchor，watchdog 可在一帧中没有 anchored form 出现时用排除法推断。

### Raw Mesh

Raw Mesh 是 WWMI / Velo 子工具，面向 stock pose-chain extractor 识别不到的 non-character geometry，例如 VFX-layer、scene 或 environment meshes。

它有独立 modes：

- **Extract**
- **Import**
- **Export**

#### Raw Mesh Extract

字段：

- Frame Dump folder。
- Output folder。
- Hash list。
- 可选 output folder name。
- 可选 Position element override。
- Texture filters。

Hash list 行为：

- `VB` hash 会提取整个 `VB0` object 并自动 split components。
- `IB` hash 只提取匹配 draw / component。
- 多个 hashes 可用逗号分隔。

#### Raw Mesh Import

导入 Raw Mesh Extract 生成的 consolidated folder。

Velo 会创建 editable mesh objects，并把 raw per-slot bytes 保存在 mesh attributes 中。

#### Raw Mesh Export

导出字段：

- Component collection。
- Mod output folder。
- Export mode：
  - **Auto**
  - **Faithful**
  - **Rebuild**

模式：

- **Faithful**：topology 必须不变；只重新编码编辑过的 positions，其它 vertex attributes byte-for-byte passthrough。
- **Rebuild**：允许 topology changes / remesh，但非标准 attributes 是 best-effort，可能有损。
- **Auto**：topology 未变时用 Faithful，否则用 Rebuild。

限制：

- Raw Mesh 不是普通 skinned-character workflow。
- Faithful mode 拒绝 topology changes。
- Rebuild mode 可能丢失或 zero-fill Blender 无法干净表达的 attributes。
- 此工具刻意隔离于 vendored WWMI core。

## 常见问题与限制

### 可以同时安装 standalone EFMI-Tools 或 WWMI-Tools 吗？

可以。Velo 内置 namespaced EFMI / WWMI forks，避免注册同一 upstream updater / operator IDs。Velo Tools 自己用 Velo updater 更新。

### 推荐 Blender 版本？

Blender 3.6+；主要测试版本是 Blender 4.4。

### 为什么看不到 EFMI 或 WWMI 面板？

打开 **Velo Tools -> Game**，再选择目标游戏。game-specific panels 只在 Game tab 和匹配 game active 时显示。

### WWMI skeleton mode 怎么选？

- **Merged**：需要 unrestricted unified authoring / runtime behavior。
- **Per-Component**：直接按 component-local groups authoring。
- **Per-Component (from Merged)**：想用 Merged-style editing，但最终导出 Per-Component runtime。

### Per-Component (from Merged) 为什么导出失败？

通常是 mesh 权重指向 owning component 允许范围之外的 vertex-group / bone。修复权重，或把它移动到有效 component-local groups。

### slot-style texture export 为什么失败？

常见原因：

- 缺少或过期的 `ShaderTextureUsage.json`。
- component 的多个 forms 使用相同 slot layout，无法在不依赖 texture identity 的情况下区分。
- 必需 DDS / resource 缺失。
- 某 component 应该取消 slot component list 勾选，改走 component-scoped hash fallback。

### 所有 mod 都应该启用 slot-style textures 吗？

不需要。遇到 texture streaming / hash churn，或 cross-scene slot-aware output 需要稳定 texture rebinding 时再启用。stock hash-style export 仍是默认路径。

### form anchors 可以用 `ib` hashes 吗？

不可以。用 `vb0` hashes 或 pixel shader hashes；`ib` hashes 不适合作为 form anchors。

### LOD extraction 匹配不上怎么办？

确认 Frame Dump 是在游戏实际绘制 LOD mesh 时抓取的；再调整 geometry matcher threshold、voxel size 或 candidate counts。

### CrossIB 缺 scene evidence 怎么办？

从 additional Frame Dumps 生成或累积 sidecar data。CrossIB 可以把新 scene shader evidence 合并进已有 sidecars。

### Raw Mesh 可以改 topology 吗？

可以，但需要 **Rebuild** mode，且 non-standard attributes 可能有损。只移动 positions 并想保持 byte-preserving output 时用 **Faithful**。

### Weight Tools 能完全替代手刷权重吗？

不能。Weight Tools 加速 transfer、mirroring、normalization、smoothing 和 repair，但最终仍应在 Blender 中检查结果。

### 手册需要本机路径吗？

不需要。公开手册只使用 object source folder、Frame Dump folder、mod output folder 等通用名称。
