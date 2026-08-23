# Velo Tools 中文使用手册

> 适用版本：Velo Tools v1.6.2。本文以当前中文 UI 为准；`IB`、`VB`、`Hash`、`Merged`、`Per-Component`、`Frame Dump`、`LOD`、`INI`、`ShapeKey`、`DDS` 等技术标识保留原文。

Velo Tools 是面向 GIMI 生态 Mod 制作的 Blender 插件。它把通用网格与权重工具、终末地 EFMI 工作流、鸣潮 WWMI 工作流放在同一个 **Velo Tools** 面板中。

支持 Blender 3.6 及以上版本，主要开发和验证版本为 Blender 4.4。

英文手册：[Velo Tools User Manual](user-manual.en.md)。

## 目录

1. [开始使用](#1-开始使用)
2. [安装、更新与共存](#2-安装更新与共存)
3. [项目数据流](#3-项目数据流)
4. [通用工具](#4-通用工具)
5. [终末地 EFMI 全流程](#5-终末地-efmi-全流程)
6. [鸣潮 WWMI 单 IB 全流程](#6-鸣潮-wwmi-单-ib-全流程)
7. [鸣潮 WWMI 专项流程](#7-鸣潮-wwmi-专项流程)
8. [理解并验收导出结果](#8-理解并验收导出结果)
9. [按症状排障](#9-按症状排障)
10. [限制与术语](#10-限制与术语)

## 1. 开始使用

### 1.1 打开 Velo Tools

1. 在 Blender 的 3D 视图中按 `N`。
2. 打开右侧 **Velo Tools** 标签页。
3. 在面板顶部选择 **功能区**。
4. 选择 **游戏** 时，再用 **游戏** 下拉框选择 **终末地** 或 **鸣潮**。

四个功能区分别是：

| 功能区 | 用途 |
| --- | --- |
| **顶点组工具** | 顶点组批处理、名称映射、MMD 映射和可视化校对。 |
| **网格工具** | 材质拆分与合并、多物体雕刻、ShapeKey 聚合、材质集合归属。 |
| **权重工具** | 权重传递、镜像、供体规格化、平滑、组数限制和局部修复。 |
| **游戏** | EFMI 或 WWMI 的提取、导入、编辑和导出。 |

### 1.2 按目标选择工作流

| 目标 | 从哪里开始 |
| --- | --- |
| 制作终末地角色 Mod | **游戏 -> 终末地 -> 提取帧数据** |
| 制作鸣潮单场景角色 Mod | **游戏 -> 鸣潮 -> 提取帧数据** |
| 让一份鸣潮模型覆盖多个场景 IB | 先完成各 IB 提取，再用 **跨场景折叠合并** |
| 处理鸣潮远距离模型 | 先提取主对象，再用 **LOD 数据提取** |
| 应对鸣潮贴图流送或 Hash 变化场景 | 使用 **插槽风格贴图**，并按 Component 决定是否保留原生 Hash 风格 |
| 合并鸣潮多形态贴图证据 | 使用 **形态贴图合并**，必要时再找 **形态锚点** |
| 提取普通角色流程识别不到的鸣潮几何 | 使用 **原始网格工具** |
| 修复或迁移权重 | 使用 **权重工具** |

### 1.3 第一次导出的最短路径

1. 从目标场景取得有效的 Frame Dump。
2. 用 **提取帧数据** 生成对象源目录。
3. 用 **导入对象** 把对象源目录导入 Blender。
4. 编辑导入后自动建立的组件集合。
5. 回到相同游戏的 **导出 Mod**。
6. 选择同一个对象源目录和正确的组件集合。
7. 选择与导入方式匹配的骨架模式。
8. 设置独立的 **Mod 输出目录**，然后导出。
9. 先检查磁盘产物，再进入游戏验证。

不要把 Frame Dump、对象源目录、`.blend` 工程和最终 Mod 输出目录混成同一个目录。它们承担不同职责，分开保存更容易回退和重新提取。

## 2. 安装、更新与共存

### 2.1 安装

1. 从 [GitHub Releases](https://github.com/visaokc/Velo-Tools/releases) 下载 `velo_tools-<version>.zip`。
2. 打开 Blender 的 **Edit（编辑） -> Preferences（偏好设置） -> Add-ons（插件）**。
3. 点击 **Install from Disk...（从磁盘安装）**，选择下载的 zip。
4. 启用 **Velo-Tools**。
5. 回到 3D 视图，按 `N` 打开 **Velo Tools**。

安装时直接选择 release zip，不要先把 zip 解压后再选择内部目录。

### 2.2 更新

Velo Tools 使用宿主级更新器更新整个 `velo_tools/` 插件。不要单独更新插件内部的 EFMI 或 WWMI core。

更新入口：

- Velo Tools 检测到新版本时，面板顶部会出现更新提示。
- 在 **Preferences -> Add-ons -> Velo-Tools** 中可以手动检查并安装更新。

更新规则：

- 默认只显示稳定版 GitHub Release。
- 只有主动开启“接收预发布版本”时才显示 pre-release。
- 更新完成后重启 Blender。
- 源码目录联接或开发安装不要使用“立即更新”；该按钮面向普通 release zip 安装。
- 手动安装新版后，宿主更新器会清除等于或低于当前安装版本的旧缓存目标；插件重新载入后，不应继续显示同版本更新提示。

升级前建议备份正在编辑的 `.blend` 和自定义 INI 模板。更新插件不会替你更新旧 Frame Dump 或对象源目录中的游戏证据。

### 2.3 与独立 EFMI-Tools、WWMI-Tools 共存

Velo 内置使用独立命名空间的 EFMI 与 WWMI 分支，因此可以与独立安装的 EFMI-Tools 或 WWMI-Tools 共存。

请注意：

- **Velo Tools** 中的游戏面板由 Velo 自己更新。
- 独立插件由各自的更新方式维护。
- 不要把独立插件的文件直接覆盖进 Velo 的插件目录。
- 排障时先确认当前使用的是 **Velo Tools -> 游戏** 下的面板，而不是独立插件的同名面板。

## 3. 项目数据流

### 3.1 四类目录

| 数据 | 作用 | 是否可以重建 |
| --- | --- | --- |
| Frame Dump | 保存当次游戏绘制的资源证据和 `log.txt`。 | 需要回到目标运行状态重新抓取。 |
| 对象源目录 | 保存提取后的网格、Metadata、贴图使用数据和 sidecar。 | 可以从对应 Frame Dump 重新提取或合并。 |
| Blender 工程 | 保存实际编辑内容、集合、对象、权重和 ShapeKey。 | 应自行保存版本和备份。 |
| Mod 输出目录 | 保存 `mod.ini`、`Meshes`、`Textures` 等运行时产物。 | 可以从工程和对象源目录重新导出。 |

推荐数据流：

```text
Frame Dump
    -> 提取/合并
对象源目录
    -> 导入
Blender 工程
    -> 导出
Mod 输出目录
    -> 磁盘检查
游戏验证
```

### 3.2 什么是资源 Hash

Velo 不会读取 DDS 像素，再从图片内容计算游戏贴图 Hash。

Hash-style 导出使用的是提取证据中的资源 identity，来源包括 FrameAnalysis 文件名、贴图文件名中的 `t=<hash>`、`ShaderTextureUsage.json` 或同类 Metadata。

因此：

- 修改 DDS 颜色不会自动改变 INI 中的 Hash。
- 旧 Dump 没有记录的新运行时资源 identity，导出器不会猜测。
- 游戏更新或不同流送状态出现新 identity 时，应在问题状态下重新 Dump。
- 不要用“图片看起来相同”代替资源身份依据。

### 3.3 什么时候必须重新 Dump

遇到以下情况时，应优先更新证据，而不是继续手改生成的 INI：

- 游戏更新后原 Hash 不再命中。
- 只有某个场景、距离或冷进入状态贴图异常。
- 新形态或新 IB 没有出现在旧提取中。
- `ShaderTextureUsage.json` 缺失、过期或与当前对象源目录不一致。
- LOD、CrossIB 或跨场景流程缺少目标场景的绘制证据。

重新 Dump 后，应重新执行受影响的提取、LOD 匹配、形态贴图合并或跨场景合并，再从新的对象源目录导出。

### 3.4 输出目录的使用原则

普通完整导出应启用 **写出 mod.ini** 和 **复制贴图**。

**部分导出**只适合确认其它资源没有变化时更新指定 Buffer。它会跳过 INI 生成和资源复制，不能拿来创建一份全新的完整 Mod。

如果需要保留手工修改过的贴图，先备份输出目录。Velo 的 WWMI 跨场景贴图交付会保留已经存在的同名文件并只补缺失项，但备份仍是最可靠的版本管理方式。

## 4. 通用工具

通用工具不依赖具体游戏。它们可以在导入后、导出前整理对象，但不会替你判断游戏中的最终显示是否正确。

### 4.1 顶点组工具

**顶点组工具**用于批量整理顶点组，以及建立源网格与目标网格之间的名称映射。

常用流程：

1. 设置 **源物体** 和 **目标物体**。
2. 建立或载入映射表。
3. 点击 **从源物体补行**。
4. 点击 **按位置匹配→写入本表**。
5. 检查未匹配列表和 **可视化校对（重心连线）**。
6. 确认映射后执行改名。
7. 如果结果不正确，使用恢复原名功能回退。

映射表可以保存在 `.blend` 内的文本数据中，也可以导入或导出为外部文本。

批处理包括：

- 合并顶点组。
- 补齐顶点组编号空洞。
- 移除未使用顶点组。
- 移除全部顶点组。
- 按 MMD 映射或通用映射合并同目标组。

自动匹配只是候选结果。骨骼位置接近不等于语义相同，应用改名前应检查躯干、衣物、头发和左右侧骨骼是否被错误配对。

### 4.2 网格与材质工具

**网格工具**包含四组主要能力。

#### 多物体雕刻

- 创建临时合并雕刻对象。
- 把雕刻结果应用回原始对象。
- 在源对象带 ShapeKey 时使用对应的 ShapeKey 应用流程。

应用回源对象前，确认拓扑、对象顺序和源对象没有被意外替换。临时合并对象不是游戏导出的最终组件集合。

#### 材质工具

常用操作包括：

- 为选中对象添加 `Component` 前缀。
- 生成材质。
- 按材质拆分。
- 按贴图合并。
- 补齐缺失网格数据。
- 对带 ShapeKey 的对象应用修改器。
- 转换顶点色。

**选中物体生成材质球**右侧的纯图标开关用于开启改名自动同步。开关默认关闭，开启时使用 Blender 原生高亮按下状态，并随当前 Scene 保存。自动范围仅限当前游戏 **导出 Mod** 中设置的组件集合及其全部子集合：零材质槽或单材质槽物体在后续改名时会同步 mesh 与材质球名称；两个及以上材质槽的对象保持不变。新建、复制或导入对象首次出现时只建立名称基线，之后再次改名才会处理。

按贴图合并会改变对象组织方式。执行前保存工程副本，并在操作后确认材质槽、UV、ShapeKey 和 Component 归属。

#### 平滑法线八面体 UV

打开 **UV工具**，对需要处理的全部网格执行 **平滑法线-八面体UV**。该操作以每个网格的第一层 UV 建立切线空间，将平滑法线编码为八面体坐标，并写入 `TEXCOORD1.xy`。源网格必须先有第一层 UV；已有 `TEXCOORD1.xy` 会被替换，因此如其中保存了手工数据，应先备份并在处理后按目标 shader/导出约定验收。

#### 按材质分离所属集合

该面板会读取当前游戏 **导出 Mod** 中设置的组件集合，并预览按材质形成的最终分组。

推荐流程：

1. 先在目标游戏的 **导出 Mod** 中设置组件集合。
2. 打开 **按材质分离所属集合**。
3. 点击刷新，生成集合树和虚拟材质分组。
4. 选择材质分组，再指定目标集合。
5. 执行按材质或按贴图分离。
6. 回到 Outliner 检查真实对象与集合归属。

树中的集合是真实 Blender 集合；材质叶子是预览出的虚拟分组，不代表当前已经存在对应网格对象。

#### ShapeKey 聚合

**形态键聚合 (按集合)** 会扫描目标集合中的网格 ShapeKey，按名称聚合，并把同名 ShapeKey 的名称和值同步到相关网格。

- **强制刷新**重新扫描集合。
- 直接编辑列表名称会同步重命名所有同名 ShapeKey。
- 滑块会同步同名 ShapeKey 的值。
- 列表始终先按已有 `Deform N` 的数字升序排列，再将其它名称按大小写不敏感的自然 A-Z 排列；刷新和对象遍历顺序变化不会打乱位置。
- 左侧复选框用于选择参与**自动重命名**的可重命名项，默认全部不选；操作栏中的**全选/全不选**可切换全部可重命名项。该按钮不再伪装成列表表头，因此不依赖 UIList 内边距、滚动条宽度、主题、DPI 或 UI scale。
- 已有 `Deform N` 或 `Deform N 后缀` 的名称默认受保护，不会被自动修改。自动重命名把每个现有数字 ID 当作独立占位，并从最小可用正整数开始补空号；手动固定的高 ID 不会再把后续自动编号整体推到它之后。WWMI 模式还会读取所选对象源的 `Metadata.json`，保留所有原生 batch 范围，即使某些原生 ShapeKey 已从 Blender 删除也不会占用其 ID；对象源元数据缺失或无效时会直接取消，避免碰撞。EFMI 模式编号不读取、也不要求对象源目录。
- 自动生成的格式为 `Deform N <完整原名>`。刷新后勾选和活动行按名称保持；改名后会跟随新名称移动到数字排序区。
- 鼠标悬浮在 `xN` 上可查看包含该 ShapeKey 的全部网格对象。名称列优先获得侧边栏新增宽度，value 滑块最多占 8 个 Blender UI units；`xN` 会按当前最大位数保留等宽文本，拖动侧边栏时所有可见行保持统一列宽。

这是 Blender 侧的组织工具。EFMI 的运行时自定义 ShapeKey 导出仍需按第 5 章设置。

### 4.3 权重工具

**权重工具**用于把一个来源顶点组传递到目标网格，同时尽量保护目标已有权重结构。

#### 标准流程

1. 在 **工作对象** 中设置 **来源网格**、**目标网格** 和可选的 **目标骨架**。
2. 选择 **来源顶点组**。
3. 如需镜像，同时选择 **镜像顶点组** 或配置 **镜像映射组**。
4. 选择 **传递引擎**。
5. 确认 **承接组名** 和可选的 **镜像承接组名**。
6. 检查 **预计算供体（规格化时使用）**。
7. 设置平滑、规格化和每顶点组数限制。
8. 执行 **权重传递**。
9. 阅读 **结果**，再在 Weight Paint 或 Edit Mode 中检查。

传递引擎：

| 引擎 | 用途 |
| --- | --- |
| **Robust** | Velo 主路径，使用表面匹配和 inpaint 处理覆盖。 |
| **面插值传递** | 使用接近 Blender Data Transfer 的表面插值。 |

Robust 使用可选的 CPython 3.11 native dependencies。首次使用前，Velo 会先显示下载体积（约 51.5 MiB）和共享 cache 安装体积（约 173.3 MiB），经用户确认后再下载并校验 `scipy`、`libigl` 和 `robust-laplacian`。依赖安装在插件目录外的本地共享 cache，不会再进入普通 Velo 安装包、更新备份，也不会影响 EFMI、WWMI、ShapeKey 或 Cross-Scene 工作流；无需安装独立 Robust Weight Transfer 插件。

如果独立插件已经启用并完整加载了兼容依赖，Velo 会直接复用现有模块，不会把自己的私有依赖目录加入 `sys.path`；否则只临时从 Velo 私有目录加载依赖。因此两个插件可以共存。

#### 供体和规格化

供体用于在写入目标权重时重新分配容量。

- **自动供体数**是最大数量，不要求填满。
- 自动列出的供体是预览建议。
- 手动填写的供体是严格选择。
- 无效手动供体会在写入任何目标或镜像权重之前中止。
- 锁定的普通顶点组会作为受保护容量处理。

如果启用镜像传递，目标侧和镜像侧会分别使用已经验证的供体集合。不要依赖名称碰巧相似来判断左右关系；必要时建立手动镜像映射。

#### Robust 证据边界

Robust 路径会检查整个连通域是否存在正向来源证据。完全没有来源证据的独立网格岛不会被 Point inpaint 染成非零权重。

该保护减少明显污染，但不等于自动得到正确骨骼语义。传递后仍应检查接缝、裙摆、头发、左右侧和与身体分离的小岛。

#### 后处理与局部修复

- **启用平滑**可设置次数和强度，并默认避开 UV 接缝。
- **限制每顶点组数量**限制每个顶点保留的有效组数。
- **执行后规格化**在处理后重新规格化权重。
- 选中顶点修复可在 Edit Mode 中处理局部问题。
- 未解决顶点会保持选中，便于继续人工检查。

操作失败时，工具会尽量恢复原有 membership 和权重。即使报告成功，也应保存工程并检查 Deform 结果，而不是只看操作器状态。

## 5. 终末地 EFMI 全流程

在 **功能区 -> 游戏 -> 终末地** 中使用 EFMI 工作流。

### 5.1 标准流程概览

```text
提取帧数据
    -> 导入对象
    -> Blender 编辑
    -> 可选：提取 LOD / CrossIB / 自定义 ShapeKey
    -> 导出 Mod
```

EFMI 面板提供四种模式：

- **提取帧数据**
- **导入对象**
- **提取 LOD 数据**
- **导出 Mod**

### 5.2 提取帧数据

1. 设置有效的 **Frame Dump 目录**，其中应包含 `log.txt`。
2. 设置 **输出目录**。
3. 按需启用对象、Component 和贴图过滤。
4. 如需使用任一 Merged 模式，请用当前 Velo/EFMI Tools v0.6.2+ 重新提取，确认 Metadata v4 同时含有紧凑 authoring `components[*].vg_map` 与官方 MergedSkeleton 来源槽 `runtime_vg_map`。
5. 如需快速查看，可启用 **提取后导入 Blender**。
6. 执行提取。

常用过滤项：

- **对象过滤：跳过静态对象**
- **对象过滤：最少组件数**
- **对象过滤：最少贴图数**
- **对象过滤：资源 Hash**
- **组件过滤：黑名单 Hash**
- **贴图过滤：跳过小贴图**
- **贴图过滤：跳过 .jpg**

**容忍提取错误**会跳过单个失败对象并继续，不代表被跳过的对象已经正确提取。需要目标对象时，应查看日志并修复证据问题。

对象源目录通常包含组件 Buffer、`Metadata.json` 和 `TextureUsage.json`。Velo 将 current+previous 骨骼矩阵签名生成的紧凑统一编号写入 `components[*].vg_map`，并在 `runtime_vg_map` 中保留官方 MergedSkeleton runtime 使用的来源槽；不再生成独立映射 sidecar。

### 5.3 导入对象

1. 切换到 **导入对象**。
2. 设置 **对象源目录**。
3. 选择 **骨架**。
4. 按需设置 **顶点色**、**跳过空顶点组**、**镜像网格**。
5. 按需启用 **按组件创建子集合** 和 **导入贴图**。
6. 导入。

骨架模式：

| 模式 | 行为 |
| --- | --- |
| **Merged（统一顶点组）** | 把各 Component 的局部顶点组映射为紧凑统一编号；需要当前 Velo/EFMI Tools v0.6.2+ 生成的 Metadata v4。 |
| **Per-Component（部件独立）** | 保持每个 Component 的局部顶点组编号。 |

**按组件创建子集合**会创建 `C0`、`C1` 等子集合，并连接到导出集合。关闭后使用更接近上游 EFMI 的单集合导入方式。

**导入贴图**依据 `TextureUsage.json` 分配对象源目录中的 DDS。它只负责 Blender 材质预览，不改变游戏资源 Hash。

选择或实际导入 **Merged（统一顶点组）** 后，导出骨架模式会自动同步为 **Merged（合并骨架）**。只有明确需要“统一顶点组制作、Per-Component runtime”时，才在导出阶段手动改回 **Merged（统一顶点组）**。

### 5.4 Blender 编辑

编辑时保持以下约束：

- 不要随意删除导出需要的 Component 归属。
- 骨架模式和顶点组命名方式应与导入一致。
- Merged 项目必须保留对应的 Metadata v4；两类 Merged 均直接读取其中的 `components[*].vg_map`。
- 应用修改器后检查法线、UV、顶点色、权重和 ShapeKey。
- 如果使用嵌套集合，确认导出时没有启用会排除它们的选项。

### 5.5 EFMI LOD

1. 在游戏实际绘制目标 LOD 时取得 Frame Dump。
2. 切换到 **提取 LOD 数据**。
3. 设置 **LOD Frame Dump 目录** 和原对象源目录。
4. 选择 **体素匹配（确定性）** 或 **点云匹配（随机）**。
5. 先使用默认参数匹配。
6. 需要时再调整误差阈值、采样数、预过滤候选数和顶点组匹配候选数。
7. 执行提取，再检查 `Metadata.json` 和可选导入的 LOD 对象。

已有 LOD 数据默认受保护。只有明确希望替换时才启用 **允许覆盖 LOD 数据**。

如果启用 **低于阈值的 LOD 跳过**，低置信候选会被跳过，而不是中止整次提取。跳过并不等于匹配成功。

### 5.6 CrossIB

CrossIB 让左侧源对象或源集合借用目标 Component 的渲染管线。

流程：

1. 切换到 **导出 Mod**。
2. 打开 **Cross Index Buffer（跨 IB）**。
3. 启用 **启用跨 IB（CrossIB）**。
4. 如果对象源目录没有有效的 `CrossIB.json v2`，点击 **生成 / 重新生成 CrossIB.json v2**，选择一份当前 Frame Dump。
5. 添加物体映射或集合映射。
6. 为每行选择目标 Component。
7. 正常导出。

`CrossIB.json v2` 保存 Component 匹配、pass topology、透明性与 input compatibility 证据。只有启用 CrossIB 且至少存在一条 mapping 时，导出才会把公共 ShaderRegex 规则写入独立的 `CrossIBClassifier.ini`，并复制所需 HLSL；主 `mod.ini` 只保留当前 mod 的 CrossIB 路由、资源和 draw 逻辑。每份 classifier 的规则相同，高级用户可以全局只保留一份启用的副本，并删除或禁用其余重复文件，以减少重复匹配开销。未启用或没有 mapping 时不会输出这些规则和资产。classifier 按社区既有 200/201/202/203/204/205 分组语义匹配 shader，但 producer 不再使用 VS Hash；不再生成 `ShaderOverride.ini`，也不再合并多个场景的 VS Hash。旧 v1 必须用一份 dump 显式重建。

### 5.7 EFMI 自定义 ShapeKey

运行时自定义 ShapeKey 使用以下命名：

```text
Deform <编号> <名称>
```

例如：

```text
Deform 1 Smile
Deform 2 Blink
Deform12 CapeLift
```

流程：

1. 在当前 EFMI 组件集合的网格上创建或保留 ShapeKey。
2. 按 `Deform <编号> <名称>` 命名。
3. 在高级导出选项启用 **导出自定义 ShapeKey**。
4. 通常保持 **合并 Buffer 文件** 开启。
5. 展开 **显示已识别的 ShapeKey**，刷新并检查结果。
6. 修复冲突后导出。

同一物体内重复使用数字 Deform ID 会阻止导出，因为两个 delta payload 不能共用该物体的同一运行时 channel。原始 Blender 名称不再决定运行时 identity：同一名称可以使用不同 ID，同一 ID 也可以在不同 Component 中对应不同名称。

不符合 `Deform <编号> <名称>` 命名的普通 ShapeKey 不会生成运行时变量，而是按 Blender 中的当前值计算进导出 Basis；即使同一物体还包含标准 Deform ShapeKey，也不会因此被归零或丢失。标准 Deform ShapeKey 则保留用于提取运行时 delta。

每个导出 ID 使用稳定的数字变量，原始 Blender 名称只写入注释；多个 Component 为同一 ID 提供不同名称时，注释会按确定顺序合并去重：

```ini
; ShapeKey_12: CapeLift
global persist $ShapeKey_12 = 0.375
```

变量初始值继承导出时 Blender 中对应 ShapeKey 的值；插件只在计算 Basis 和运行时 delta 时临时将标准 Deform 归零。导出的基础 `VB0` 始终保持 Deform-neutral，Blender 中设定的值只用于初始化 `$ShapeKey_<编号>`，再由官方运行时应用一次。如果同一运行时 ID 在不同物体上的导出前值不一致，导出会停止，而不是选择一个含糊的全局默认值。

外部 INI 逻辑应引用 `$ShapeKey_<编号>`，不要再引用由 Blender 名称清理得到的旧变量名。

### 5.8 导出 Mod

1. 设置 **组件集合**、**对象源目录** 和 **Mod 输出目录**。
2. 选择骨架模式。Merged 导入默认同步为 **Merged（合并骨架）**；如需旧的统一顶点组回译 runtime，再手动选择 **Merged（统一顶点组）**。
3. 按需启用 **应用所有修改器**、**复制贴图**、**写出 mod.ini** 和 **写入注释**。
4. 检查隐藏对象、隐藏集合、嵌套集合和禁用 ShapeKey 的忽略选项。
5. 仅在确有需要时调整 **高级** 或 **部分导出**。
6. 点击 **导出 Mod**。

**Velo 兼容选项 -> 导出时自动按材质拆分**默认开启。一个已合并对象实际使用两个以上 `Component N` 前缀材质时，Velo 只在导出临时副本上按材质拆分，场景对象与 ShapeKey 不变。所有实际使用材质必须与对象的 Component 一致；冲突或材质名模式中的无前缀材质会阻止导出，并报告集合、对象和材质槽。无材质、单材质或仅有多个预览材质的对象仍按对象名导出。关闭该选项会完整保留旧导出行为。

EFMI 开放世界项目没有 LOD 数据时，只有启用 **允许无 LOD 导出** 才能继续；这种输出在开放世界可能无法正确加载。

三种导出模式的边界：

| 模式 | 导出结果 |
| --- | --- |
| **Merged（统一顶点组）** | Blender 使用紧凑统一编号；导出时按各 Component 的 `vg_map` 回译为局部编号，运行端仍为 Per-Component。 |
| **Per-Component（部件独立）** | 制作、Buffer 与运行端始终使用 Component 局部编号。 |
| **Merged（合并骨架）** | 通过 `runtime_vg_map` 输出统一编号，并使用 EFMI v1.4.1 官方按实例 MergedSkeleton runtime。 |

Velo 现在会在导入 LOD 时为每条 Component 记录写入 `present: true/false`：从 LOD Dump 实际匹配到的 Component 为存在，matcher 未命中后写入的主模型回退记录为缺失。执行 **Merged（合并骨架）** 导出时，缺失 Component 不会被选作 LOD 消费者的运行时骨骼来源。旧 Metadata 没有 `present` 时保持兼容并默认按 `true` 处理，等同 v1.6.2 以前的导出行为；只有已经确认某个 Component 在 LOD 中缺失时才手动添加 `"present": false`，也可使用当前版本重新导入 LOD 自动生成显式标记。

### 5.9 Mod 信息、INI 模板与 INI 开关

**Mod 信息**可填写名称、作者、描述、链接和图标。图标应为 512×512、BC7 SRGB 的 DDS，导出后位于 `Textures/Logo.dds`。

**INI 模板**支持：

- **内置编辑器**
- **外部文件**
- **模板实时更新**
- **编辑模板**
- **重置模板**

自定义模板会接管完整 `mod.ini` 生成。模板必须主动包含你需要的 Velo 扩展段；不要假设默认模板的后处理一定会自动合入自定义模板。

**INI 开关**用于建立变量、状态、对象和条件，也支持 JSON 导入导出。导入前确认“替换同名变量”和“导入前清空变量”的选择，避免覆盖现有配置。

### 5.10 EFMI 导出验收

磁盘检查：

- `mod.ini` 存在且不是旧文件。
- `Meshes` 中存在本次导出的 Buffer。
- 启用复制贴图时，`Textures` 中存在所需贴图。
- 使用 Merged 时，对象源目录必须保留当前 Velo 提取的 Metadata v4，其中同时含 `components[*].vg_map` 与 `runtime_vg_map`。
- 使用 LOD、CrossIB 或 ShapeKey 时，对应资源和 INI 段确实存在。

游戏检查：

- 近距离和开放世界状态都能加载。
- 权重、法线、UV、顶点色和 ShapeKey 正常。
- CrossIB 目标场景确实显示。
- 相同对象同屏时，所选骨架模式符合预期。

## 6. 鸣潮 WWMI 单 IB 全流程

在 **功能区 -> 游戏 -> 鸣潮** 中使用 WWMI 工作流。本章先说明单个对象源目录、单个主要 IB 的标准流程。

### 6.1 标准流程概览

```text
提取帧数据
    -> 导入对象
    -> Blender 编辑
    -> 导出 Mod
```

如果对象还需要 LOD、跨场景、多形态贴图或原始网格流程，先完成本章的基础概念，再阅读第 7 章。

### 6.2 提取帧数据

1. 切换到 **提取帧数据**。
2. 设置包含 `log.txt` 的 **Frame Dump 目录**。
3. 设置 **输出目录**。
4. 按需选择贴图过滤。
5. 执行提取。

常用过滤项：

- **贴图过滤：跳过小贴图**
- **最小大小 KB**
- **贴图过滤：跳过 .jpg**
- **贴图过滤：跳过已知 Cubemap**
- **贴图过滤：跳过同槽同 Hash**
- **贴图过滤：跳过 Dirty Slot**

其中 **贴图过滤：跳过 Dirty Slot** 会保留 `log.txt` 中明确执行过 `PSSetShaderResources` 的 fresh 槽位；service slot 若由前一 draw 继承，只有写入 draw 与消费 draw 都是彩色 pass、属于不同 `vb0` 对象、都有相同角色的 fresh `cb4` 证据，并且在 `cb5` 或 `cb6` 具有相同的 fresh 材质身份时，才会保留并在 STU 中标记为 `verified_inherited`。同一 `vb0` 内的 Component 状态延续、depth-only 继承和主材质槽继承即使仍绑定同角色资源，也不等于 shader 真正使用，因此仍按残留过滤。存在可用 log 证据时，提取阶段会同时按保留下来的 Component ownership 修正 DDS 文件名、`TextureUsage.json` 与 STU `filename`；没有可用 log 证据时保持 legacy 输出，不猜测删除。

其它“跳过”类过滤会减少输出，也可能移除后来需要的贴图证据。准备使用插槽风格贴图或多形态合并时，优先保留完整证据。

提取会生成 `ShaderTextureUsage.json`。该文件记录 Component、draw、shader pair、`ps-tN` 槽位、资源 Hash、新鲜度证据，以及 F8 转储已捕获的完整 Unreal `asset_path`，是资产名称匹配、插槽风格贴图和形态贴图合并的重要输入。

### 6.3 导入对象

1. 切换到 **导入对象**。
2. 设置 **对象源目录**。
3. 选择 **骨架**。
4. 按需设置 **顶点色**、**跳过空顶点组** 和 **镜像网格**。
5. 按需启用 **按组件创建子集合** 和 **导入贴图**。
6. 导入。

骨架模式：

| 模式 | 适用情况 |
| --- | --- |
| **Merged** | 需要统一顶点组列表、跨 Component 刷权重或骨架缩放。 |
| **Per-Component** | 直接按每个 Component 的局部顶点组编辑。 |

**按组件创建子集合**会建立 `C0`、`C1` 等子集合，并自动连接导出集合。关闭后使用更接近原版 WWMI 的单集合导入方式。

**导入贴图**只影响 Blender 材质预览。它依据对象源目录中的贴图使用数据分配贴图，不会修改运行时 Hash 或 slot 证据。

#### WWMI 数字编号 ↔ 原始骨骼名

**顶点组工具**中的 **WWMI 数字编号 ↔ 原始骨骼名** 面板默认折叠。需要把 WWMI 数字顶点组恢复为原始骨骼名或导入可编辑骨架时再展开：

1. 设置 **解包路径**和 WWMI **对象源目录**。
2. 默认先保留 **最低相似度 55%** 与 **体素大小 0.050**；证据不足时再调整。
3. 点击 **生成映射表**。Velo 会聚合解包目录中所有相关 `.uemodel` section，并结合 Component 体素证据与一对一蒙皮权重证据，不会只处理第一个模型文件。
4. 用 **将匹配结果保存至源目录**生成 `WWMI_MatchingResult.json`；其中同时保存映射和骨架快照，以后可直接从对象源恢复。
5. **切换至原始名字 / 切换至数字编号**只修改当前选中的网格；当前网格实际使用的编号若仍有歧义，会 fail closed。
6. **导入骨架**可从解包路径或已保存结果创建骨架；**一键为Mod网格绑骨**会处理 WWMI 导出面板中组件集合内的全部网格、切换原始名、导入骨架并绑定。`.L/.R` 后缀只应用于检测到的左右成对骨骼。

### 6.4 选择骨架模式

| 目标 | 推荐导入 | 推荐导出 |
| --- | --- | --- |
| 简单局部权重、武器或改贴图 | Per-Component | Per-Component |
| 需要统一顶点组编辑 | Merged | Merged 或 Per-Component (from Merged) |
| 想统一编辑、但运行时保持部件独立 | Merged | Per-Component (from Merged) |
| 跨场景且存在独立 Buffer/可编辑 IB | Merged | 优先验证 Per-Component (from Merged) |

WWMI 的 **Merged** 使用统一顶点组及其 Merged runtime；**Per-Component (from Merged)** 保留统一编号制作体验，但导出时严格回译为各 Component 的局部骨表。后者遇到当前 Component 无法表达的非零权重会直接停止，不会静默丢弃。

Per-Component 没有统一顶点组的自由度，也不支持自定义骨架缩放，但运行结构更接近部件局部骨架。

### 6.5 Blender 编辑

- 保留可识别的 Component 对象命名和集合归属。
- 不要把顶点刷到所选骨架模式不允许的组。
- 应用修改器后检查顶点顺序、法线、切线、UV、顶点色和 ShapeKey。
- 拆分对象后保持原 Component 前缀；例如同一 Component 的多个 draw 可使用 Blender 数字后缀。
- 如果使用嵌套集合，确认导出选项不会把目标对象忽略。

对象名既用于人类识别，也可能参与 draw 路由。不要依赖手工改 INI 来补救无法唯一对应的对象归属。

### 6.6 导出 Mod

1. 切换到 **导出 Mod**。
2. 设置 **组件集合**。
3. 设置与该工程对应的 **对象源目录**。
4. 设置独立的 **Mod 输出目录**。
5. 选择骨架：`Merged`、`Per-Component` 或 `Per-Component (from Merged)`。
6. 按需启用 **应用所有修改器** 和 **镜像网格**。
7. 普通完整导出保持 **复制贴图** 和 **写出 mod.ini** 开启。
8. 需要可读 INI 时启用 **写入注释**。
9. 检查所有忽略选项。
10. 点击 **导出 Mod**。

**Velo 兼容选项 -> 导出时自动按材质拆分**也适用于 WWMI 单 IB、跨场景和 `Per-Component (from Merged)`。规则与 EFMI 相同：只有实际使用至少两个、且都与对象同属一个 Component 的前缀材质才触发临时拆分；隐藏筛选仍由当前 WWMI 导出选项决定。材质归属冲突会 fail closed，而关闭开关不会执行校验或拆分。

**部分导出**会禁用 INI 生成和资源复制。除非你只想更新已确认变化的 Buffer，否则不要启用。

### 6.7 Mod 信息、INI 模板与 INI 开关

WWMI 的 **Mod 信息**、**INI 模板** 和 **INI 开关**与 EFMI 使用相同概念。

- **Mod 信息**填写名称、作者、描述、链接和 512×512 BC7 SRGB DDS 图标。
- **INI 模板**可以存于内置编辑器或外部文件，并可开启实时更新。
- **INI 开关**可以建立变量、状态、对象、热键和自定义条件，也支持 JSON 导入导出。

跨场景最终 INI 对整个 Mod 只生成一组无 `_ibN` 后缀的 `ResourceModName/Author/Desc/Link/Logo`。每个 owning IB 仍保留独立 object GUID 与 `CommandListRegisterMod_ibN` 注册生命周期，但全部引用同一组公共 Mod 信息。

启用 **使用自定义模板**后，模板接管完整 `mod.ini`。普通单 IB 仍可使用该功能；跨场景 direct compiler 不兼容任意 Jinja 自定义模板或实时更新，检测到任一选项会在写入输出前明确取消导出。

### 6.8 单 IB 导出验收

磁盘检查：

- `mod.ini`、`Meshes` 和需要的 `Textures` 存在。
- INI 中的 Component 数量和当前工程一致。
- 每个拆分对象仍有对应 draw。
- 启用 LOD、ShapeKey 或自定义开关时，对应段和文件存在。

游戏检查：

- 首次进入场景和切换角色后都能显示。
- 主体、描边、半透明层和贴图槽位没有互相污染。
- 近距离与 LOD 距离正常。
- 两种形态、展示界面和实际场景都按预期工作。

## 7. 鸣潮 WWMI 专项流程

### 7.1 Per-Component (from Merged)

**Per-Component (from Merged)** 允许用 Merged 的统一顶点组编辑，导出时再转换为 Per-Component 的局部顶点组结构。

适合：

- 需要统一骨架列表完成权重编辑。
- 想避免纯 Merged 同屏多个相同对象时的运行时暂停。
- 跨场景项目包含 body、独立 Buffer 拆分件或独立可编辑 IB。

流程：

1. 用 **Merged** 导入对象。
2. 在统一顶点组列表中编辑。
3. 导出时选择 **Per-Component (from Merged)**。
4. Velo 在临时副本中把统一编号转换为每个 Component 的局部编号。
5. 转换通过后执行 Per-Component 运行时导出。

如果任意顶点权重指向所属 Component 允许范围以外的骨骼，导出会中止。应修复权重或 Component 归属，不要删除报错对象来掩盖问题。

同一 Component 拆出的多个对象会保留各自 draw。导出器按原始对象身份映射 Blender 临时后缀，而不是把注释文本当作功能身份。

### 7.2 WWMI LOD 数据

1. 先完成主对象提取。
2. 在游戏实际绘制 LOD 网格时抓取 Frame Dump。
3. 打开 **LOD 数据提取**。
4. 设置 **LOD Frame Dump 目录** 和主对象源目录。
5. 先使用默认匹配参数。
6. 匹配失败时再调整过滤和高级参数。
7. 点击 **提取 LOD 数据**。
8. 从更新后的对象源目录正常导出。

常用参数：

- **组件过滤：最少顶点数**
- **对象过滤：黑名单 Hash**
- **几何匹配误差阈值**
- **匹配方法**
- **体素大小**
- **点云采样数**
- **预过滤候选数**
- **顶点组匹配候选数**
- **低于阈值的 LOD 跳过**
- **允许覆盖 LOD 数据**

LOD 支持 Merged、Per-Component，以及内部转换后的 Per-Component (from Merged)。启用自定义 INI 模板时，默认 LOD 模板注入会跳过；模板作者必须自行提供等价 LOD 逻辑。

跨场景对象源目录也可以携带 LOD 数据。改变来源 Dump、拆分对象或路由后，应重新匹配并重新导出。

### 7.3 跨场景折叠合并

**跨场景折叠合并**把一个基底提取目录和多个 IB 提取目录合成一个对象源目录，使一份 Blender 编辑结果覆盖多个场景。

#### 准备数据

- 一份完整、可作为编辑基底的提取目录。
- 每个额外场景各自的 IB 提取目录。
- 需要覆盖的场景都应有实际 Frame Dump 证据。

#### 选择 IB 角色

| 角色 | 行为 |
| --- | --- |
| **折入基底** | 兼容时复用基底编辑；格式或骨架不兼容时可自动使用独立 Buffer。 |
| **独立可编辑** | 作为独立几何和独立归属域导入、编辑并导出。 |
| **形态合并** | 与一条 **折入基底** 项具有相同 `vb0 hash` 时，只把该提取目录的 STU/贴图证据合入同一路由，不重复导入几何。可填写形态标签；留空时依次使用 `form2`、`form3`。 |

不要把“另一个形态”仅凭文件夹名称判断成独立可编辑。相同 `vb0 hash`、相同 Component/LOD/VG Metadata 的贴图形态可以显式选择 **形态合并**；不同几何或不同 Buffer 身份仍应使用 **独立可编辑**。Velo 对无法唯一匹配折叠目标或 Metadata 不一致的形态项 fail closed，不会自动降级成其它角色。

#### 合并和导出

1. 打开 **跨场景折叠合并**。
2. 设置 **基底**。
3. 添加每个 **IB 文件夹**，并选择角色。
   - 同一运行时 IB 的另一贴图形态：保留一条 **折入基底**，把其它同 `vb0 hash` 的提取目录设为 **形态合并**。
4. 设置 **输出**。
5. 点击 **合并跨场景**。
6. 确认输出包含 `CrossSceneManifest.json` schema v3。
7. 用 **导入对象** 导入这个合并对象源目录。
8. 编辑合并工程。
9. 用 **导出 Mod** 正常导出。

导出时以合并目录作为 **对象源目录**。不要再切回某个子 IB 的原始提取目录。

聚合根是唯一持久事实源，只包含：

```text
<聚合根>/
  Component N.fmt/.vb/.ib
  Metadata.json
  ShaderTextureUsage.json
  CrossSceneManifest.json
  *.dds
```

聚合根不生成、也不依赖 `scene_ibs/*`。只有旧 `CrossSceneRouting.json` schema v2 的目录会被直接拒绝；请用当前版本重新执行 **合并跨场景**，不要把旧子目录复制回来。

这些根文件各自拥有不同事实：根 `Metadata.json` 负责 base geometry、LOD、VG 与 canonical morph；根 `ShaderTextureUsage.json` 负责最终 global Component/slot/route texture evidence；顶层实际存在的 DDS 是实时贴图 inclusion catalog；`CrossSceneManifest.json` 只记录前三者无法推导的 runtime IB ownership 与 VG/LOD/fold/morph route，不复制根 Metadata 或 STU。

#### Component 与 IB 命名

最终跨场景 INI 中，带 Component 的节名使用聚合根的全局 Component 编号。

`_ibN` 只表示该节属于哪个 IB 的运行生命周期。若子 IB 的 local Component 编号映射为聚合根 Component N，最终名称使用 `ComponentN_ibK`，不再沿用原 local 编号。

该规则用于 TextureOverride、CommandList、Resource 和报告键。对象注释仍可保留精确的 Blender draw 名称。

跨场景导出只捕获一次原生导出成员，并直接编译最终 typed sections；不会生成子 INI、子 Meshes/Textures、slot contract，也不会再交给 assembler 合并。最终大阶段固定为：

```text
Mod State / Constants / Present
→ Mod Info
→ Draw Call Stacks Processing
→ Shading: Textures
→ Shape Keys
→ Buffer Resources
→ Autogenerated
```

#### 何时重新合并

以下变化需要重新执行 **合并跨场景**：

- 基底或任何子 IB 重新提取。
- IB 角色改变。
- Component 数量或 draw 拆分改变。
- 贴图证据、LOD 数据或形态证据更新。
- 游戏更新后资源 identity 改变。

### 7.4 WWMI 自定义 ShapeKey

高级导出选项中的 **导出自定义 ShapeKey** 默认开启，适用于普通单 IB 和跨场景导出。分类只看对象源 `Metadata.json`：每个 batch 连续管理 127 个 Deform ID，batch 内前 `shapekey_count` 个 ID 属于游戏原生 ShapeKey；范围外且实际包含非零位置 delta 的 ID 属于外置自定义 ShapeKey。Blender 名称后缀不参与分类。

- 开启：原生 ID 继续走游戏原有的 WWMI ShapeKey 路径；自定义 ID 从原生 Buffer 中剥离，并由独立 shader 只叠加到静态 Position。导出不会解绑游戏原生 `vb6`，因此原生变形仍由游戏原生 shader 正常合成。
- 关闭：自定义记录仍从原生 Buffer 中剥离，但不生成其变量、Buffer、shader 或 INI 逻辑，只保留原生 ShapeKey。
- 没有有效自定义 delta：即使开关开启，也不会生成空资源或额外逻辑。

每个实际导出的自定义 ID 会生成一个未钳制的持久变量，并在前一行用注释保留原始 Blender 名称；同一 ID 的不同名称会按确定顺序合并：

```ini
; ShapeKey_161: Smile
global persist $ShapeKey_161 = 0.375
```

变量初始值继承导出时 Blender 中对应 ShapeKey 的值。非标准 ShapeKey 按当前值计算进导出 Basis，不生成运行时变量。同一 Deform ID 跨 Component 或 IB 共用一个变量；如果不同物体为该 ID 提供了不同导出前值，导出会停止。可以设置负值或大于 `1.0` 的值；例如把 `$ShapeKey_164` 改为 `1.0` 后重新加载 INI，即可检查该形态。变量只按编号命名，Blender 后缀保留在注释中而不是标识符中。

`persist` 值可能被 `d3dx_user.ini` 中已经保存的值覆盖。修改 `mod.ini` 默认值后若没有生效，应同步更新或删除对应持久化值。包含自定义 ShapeKey 数据时必须执行完整导出；Partial Export 不会单独更新这一管线。

### 7.5 资产名称、插槽风格与原生 Hash 风格

Hash-style 与 slot-style 可以在同一个 Mod 中共存。资产名称匹配则是独立导出模式，与 slot-style 互斥。

| 路径 | 匹配依据 | 适用情况 |
| --- | --- | --- |
| 原生 Hash 风格 | 提取证据中的资源 Hash | Hash 稳定、原版 WWMI 路径已经可靠。 |
| 资产名称匹配 | F8 转储写入 STU 的完整 Unreal `asset_path`，导出时取 UObject 短名 | Runtime 已支持 `match_asset_name`，需要摆脱流送 Hash 变化但仍保留明确资产身份。 |
| 插槽风格贴图 | draw 范围内 `ps-tN` 的完整正向槽位格式组合 | Hash 可能漂移，但槽位布局有足够证据可安全区分。 |

#### 资产名称匹配

使用带 `TextureAssetManifest.jsonl` 的 F8 转储进行提取时，Velo 会先按原流程完成 DDS 过滤与准确命名，保持原有仅由保留 DDS 构成的 STU 记录集合，再只给提取目录中确实存在对应命名 DDS 的记录新增完整 `asset_path` 字段。未保留的观察项、无 Hash descriptor、已过滤贴图和文件缺失项不会作为额外 STU 记录输出，也不会出现空 `asset_path` 字段。形态合并也会先复制最终保留的 DDS 再执行同一判断；独立 IB 重编号和 Cross-Scene 聚合只原样保留这些已确认字段。

在 **导出 Mod -> Velo 兼容选项**中启用 **使用资产名称匹配**后，具备路径证据的原生 Hash override 会只输出：

```ini
match_asset_name = T_Example_D
```

资产名称 override 会走正常的 draw-scoped `CheckTextureOverride` pre/post 路径，并保持生成 INI 的旧版 Hash 门控：普通 single-IB 继续使用 `$object_detected`，Cross-Scene 使用 `$mod_enabled_ib0 || $mod_enabled_ib2`；不会拿 `$\WWMIv1\enable_mods` 冒充该门控。导出器不写 `match_priority`、完整路径，也不生成像素指纹。已知同名短名若对应不同完整路径会 fail closed；没有路径证据的记录继续保留原生 Hash。

#### 启用流程

1. 确认对象源目录包含最新 `ShaderTextureUsage.json`。
2. 在 **导出 Mod -> Velo 兼容选项**中启用 **插槽风格贴图**。
3. 点击 **列出组件**刷新 Component 列表。
4. 需要 slot-style 的 Component 保持勾选。
5. 需要原生 hash-style 的 Component 取消勾选。
6. 正常导出。

如果没有列出组件，所有符合条件的 Component 默认尝试 slot-style。为了明确控制混合导出，建议先刷新列表再逐项确认。

取消勾选是绝对选择：该 Component 不应再生成 slot setter，而是保留原生 Hash override。跨场景组装只会把它门控到所属 IB 的 object-detected 状态，不会额外创建 Component 生命周期变量。

#### slot-style 的安全边界

- 每个写入的 `ps-tN` 都必须出现在该分支完整、正向的 assignment signature 中。
- 同一贴图在同一 Component、同一形态的多个可安全区分 shader branch 中使用不同 service slot 时，各 branch 分别按自身条件和槽位写入，并使用完整槽位备份/恢复，不因多槽本身退回 Hash fallback。
- 多形态分支必须能由最终槽位证据区分。
- 证据弱、冲突或不完整时停止导出，不生成猜测条件。
- 同一槽位布局无法区分的 Component 应补充新 Dump，或取消该 Component 的 slot-style。
- 贴图事务会备份和恢复 `ps-t0..8`，避免状态泄漏到后续 draw。

slot-style 可以提高贴图流送场景下的重绑稳定性，但不能替代缺失的 draw 证据，也不能自动修复绑错资源的 DDS。

#### hash-style 的来源和限制

原生 Hash override 使用对象源目录记录的资源 identity。导出器不会根据 DDS 图像相似度或文件内容推断新的运行时 Hash。

当一个 Component 取消 slot-style 后：

- Velo 保留该 Component 的 Hash `ResourceTexture` 与 `TextureOverride`。
- 一对一关系明确时，两节会按原版 WWMI 的顺序相邻显示。
- 跨场景时，override 只在实际拥有该资源的 IB 检测状态下启用。
- 旧 Dump 没有的新 Hash 仍需通过新 Dump 获得。

### 7.6 形态贴图合并

多形态对象可以用 **形态贴图合并**把额外形态的贴图证据加入现有对象源目录，无需再次完整提取角色。

流程：

1. 为每个额外形态抓取 RAW Frame Dump。
2. 打开 **形态贴图合并**。
3. 设置 **形态 Frame Dump**。
4. 设置主 **对象源目录**。
5. 可选填写 **形态标签**。
6. 点击 **合并形态贴图数据**。
7. 对其它形态或同一形态的补充 Dump 重复。
8. 启用插槽风格贴图后导出。

对 `CrossSceneManifest.json` schema v3 聚合根，此操作只更新根 `ShaderTextureUsage.json`：fold route 的 local Component 证据会经 manifest 映射为 global Component，不会创建或更新任何子 STU。

重复使用同一形态标签会累积该形态的证据。只有真实共享同一 VB 的多形态 Component 才应进入同一个多形态 slot 域；独立可编辑或其它 VB 的 Component 应保持独立。

### 7.7 形态锚点

形态锚点是可选元数据，用于辅助确认当前形态。

支持：

- 8 位十六进制 `vb0` Hash。
- 16 位十六进制像素着色器 Hash。

不支持 `ib` Hash。它不参与此用途的 WWMI draw 匹配。

手动格式：

```text
hash:形态标签
```

多个锚点可以用逗号、空格或换行分隔。

查找流程：

1. 启用 **插槽风格贴图**。
2. 必要时启用 **formid 辅助判据**。
3. 设置 **基础形态 Dump**。
4. 添加额外 **形态 Dump** 和标签。
5. 点击 **查找形态锚点**。
6. 检查候选，再点击 **采用**。
7. 导出并验证形态切换。

`$form_id` 是辅助状态，不是默认贴图区分依据。优先使用 `vb0` 锚点，因为几何身份通常比 shader identity 更稳定。

如果恰好只有一个形态没有锚点，看门狗可以在一帧内没有其它锚点命中时用排除法推断。游戏更新后锚点可能过期，此时应刷新 Dump 并重新查找。

### 7.8 原始网格工具

**原始网格工具**用于普通角色 pose-chain 提取流程识别不到的非角色几何，例如特效层、场景网格或环境网格。

它有三个独立模式：

- **提取帧数据**
- **导入对象**
- **导出 Mod**

#### 提取帧数据

1. 设置 **Frame Dump 目录**。
2. 设置 **输出目录**。
3. 填写 **Hash 列表**。
4. 可选填写 **文件夹名** 和 **Position 元素**。
5. 按需选择贴图过滤。
6. 执行提取。

Hash 列表规则：

- `VB` Hash 提取整个 `VB0` 对象，并自动拆分 Component。
- `IB` Hash 只提取匹配的 draw 或 Component。
- 多个 Hash 用逗号分隔。

只有自动判断错误时才手动填写 **Position 元素**。该字段填写顶点布局语义，例如 `ATTRIBUTE0`，不是贴图槽位。

#### 导入对象

选择原始网格提取生成的整合目录。Velo 会创建可编辑网格，并把原始槽位字节保存在网格属性中。

这些属性是 **保真直通**的重要依据。不要在不了解用途时删除或重建全部自定义属性。

#### 导出 Mod

| 模式 | 行为 |
| --- | --- |
| **自动** | 拓扑不变时使用保真直通；拓扑变化时使用重建。 |
| **保真直通** | 只重编码编辑后的位置，其它顶点属性按原始字节透传；拒绝拓扑变化。 |
| **重建** | 允许改变拓扑，但非标准属性只能尽力重建，可能丢失或填默认值。 |

原始网格导出会生成独立的 plain 3dmigoto per-component override。每个 Component 使用自己的源 Hash 和原始 draw range；来源提取中有可用贴图时会复制贴图。

原始网格工具与内置 WWMI 角色导出核心隔离。不要把 Raw Mesh 整合目录当作普通蒙皮角色对象源目录。

## 8. 理解并验收导出结果

### 8.1 常见目录结构

完整 Mod 输出通常包含：

```text
mod.ini
Meshes/
Textures/
```

按功能还可能出现 shader、配置、图标或其它资源。是否存在某个文件夹不是唯一验收标准，应同时检查 INI 引用与实际文件是否对应。

### 8.2 阅读生成的 INI

Velo 会按功能整理最终 WWMI INI，同时保持运行时 override 的相对顺序。

常见类别：

- `Constants` / `Present`
- `TextureOverride` / `ShaderOverride`
- 贴图和 Buffer 的 `CommandList`
- 共享 Geometry
- `Resource`

分类整理只为了提高可读性，不应改变 draw transaction 的运行语义。

#### Hash 贴图对

关系唯一时，Hash 贴图按以下顺序相邻：

```ini
[ResourceTexture_...]
filename = Textures/...

[TextureOverride_...]
hash = ...
if $object_detected_ibN
    this = ResourceTexture_...
endif
```

`$object_detected_ibN` 表示所属 IB 已检测到。多 IB 共享同一资源时，只组合有明确所有权证据的 IB，不使用无边界的全局门控。

#### slot-style setter

slot-style setter 会直接写入：

```ini
ps-tN = ref ResourceTexture...
```

判断条件应包含每个实际写入槽位的完整正向格式签名。不要为了缩短 INI，手工删除看起来重复的条件。

### 8.3 为什么 `ResourceBypassPST0..8` 可以为空

这类节是可写的运行时 Resource 引用句柄，用来暂存 `ps-t0..8`，不是从磁盘加载的纹理，因此节体可以为空。

典型事务：

1. Backup 把当前 `ps-t0..8` 引用保存到 `ResourceBypassPST0..8`。
2. Trigger 和贴图 setter 临时改写槽位。
3. draw 执行。
4. Cleanup 和 Restore 恢复原引用。

只有存在真实 slot transaction 的 IB 才需要整组句柄。没有 caller 的生成组会被清理；缺组、重复节或没有 Backup/Restore 引用会在 audit 中报错。

### 8.4 全局 Component 与 `_ibN`

跨场景合并后，Component 编号以聚合根为准，确保同一个最终几何身份在 INI 中只有一种编号。

`_ibN` 是命名空间后缀，用来隔离不同 IB 的 object detection、LOD、资源事务和清理生命周期。它不是第二套 Component 编号。

因此，`ResourceTexture_C<global>_<hash>_ib<owner>` 表示“聚合根 Component、所属 IB”，不是子 IB 的 local Component 编号。

### 8.5 DDS 交付和保留规则

在 WWMI 跨场景完整导出且启用 **复制贴图**时：

- 聚合根顶层每个 DDS 文件名都应在最终 `Textures` 中存在。
- 每个实际根 DDS 必须使用 canonical `Components-... t=<hash>.dds` 名称，且 Component 集合与根 STU 对该 Hash 的最终 ownership 完全一致；runtime route evidence 不得扩大文件名集合，不一致时在写输出前失败关闭。
- 输出目录已经存在同名 DDS 时保留作者编辑内容，只补缺失文件。
- 输出中额外的 DDS 和非 DDS 工具文件不会因为补交付而删除。
- 同一 Hash 对应冲突内容或多个 canonical 根文件名时，导出在写入前失败关闭，而不是任选一份。
- 聚合根已经删除的 DDS 视为作者主动退出 catalog，不是缺失错误；它不会进入新 INI 的 Resource、assignment、fallback 或 restore。输出目录中的旧同名/额外 DDS 可以保留在磁盘上，但不会被新 INI 重新引用。

“保留已有文件”意味着旧测试贴图也会保留。重新使用输出目录前，应确认现有 DDS 就是你想发布的版本。

### 8.6 磁盘验收清单

- [ ] 导出操作没有报错。
- [ ] `mod.ini` 的修改时间对应本次导出。
- [ ] INI 没有大小写不敏感的重复 section header。
- [ ] 所有 `filename` 和 Resource 引用都能找到实际文件或实际节。
- [ ] 普通完整导出包含所需 Meshes 和 Textures。
- [ ] Hash-style Component 有对应 Resource/TextureOverride 对。
- [ ] slot-style Component 有 setter、Backup、Cleanup 和唯一 Restore 策略。
- [ ] 跨场景 Component 使用聚合根全局编号。
- [ ] 每个保留的 Blender 拆分对象都有对应 draw。
- [ ] 聚合根要求交付的 DDS 没有缺失。

静态 audit 与基于原始 FrameAnalysis 的 replay 只能证明 INI/资源引用和路由闭合，不能证明游戏已经加载替换结果；hole 导出改变 `drawindexed` 参数时，原始 capture 通常也不会包含这些新参数。跨场景候选必须完成真实游戏矩阵后才能验收。

### 8.7 游戏验收矩阵

不要只测试一个镜头。至少覆盖：

| 维度 | 建议测试 |
| --- | --- |
| 首次加载 | 冷进入目标场景，不先进入其它展示界面。 |
| 场景切换 | 场景 A -> 场景 B -> 场景 A。 |
| 角色切换 | 切走角色再切回。 |
| 展示界面 | 进入并退出角色展示或类似临时 shader 场景。 |
| 距离 | 近距离、LOD 距离和返回近距离。 |
| 形态 | 每个形态单独进入，并反复切换。 |
| 同屏对象 | 验证 Merged 或 Per-Component 的预期行为。 |
| 图层 | 主体、描边、半透明、特效层分别检查。 |

贴图验证应同时看颜色、法线方向、描边、透明度和切换后的持久性。只看到“有贴图”不能证明槽位绑定正确。

## 9. 按症状排障

### 9.1 看不到 EFMI 或 WWMI 面板

- 确认 Velo-Tools 已启用。
- 在 3D 视图按 `N`，打开 **Velo Tools**。
- 把 **功能区**切到 **游戏**。
- 在 **游戏**下拉框选择 **终末地**或 **鸣潮**。
- 确认没有误用独立插件的面板。

### 9.2 Merged 导入或导出失败

EFMI 两类 Merged 都需要当前 Velo/EFMI Tools v0.6.2+ 生成的 Metadata v4：`components[*].vg_map` 保存紧凑 authoring ID，`runtime_vg_map` 保存官方 MergedSkeleton 来源槽。旧 sidecar-only 对象源必须重新提取，不要复制其它对象的映射。

选择 `Merged（合并骨架）` 时还必须使用 EFMI v1.4.1+；自定义模板必须包含官方 MergedSkeleton contract，否则应改用内置模板或修复模板。

WWMI 应确认导入和导出的骨架模式一致。若使用 Per-Component (from Merged)，检查是否有顶点权重越过所属 Component 的允许骨骼范围。

### 9.3 Per-Component (from Merged) 的拆分对象消失或权重错误

- 保留正确的 `Component N` 前缀和拆分对象身份。
- 检查对象是否因隐藏、嵌套集合或禁用集合被导出选项忽略。
- 修复越界顶点组，不要只改 draw 注释。
- 跨场景项目重新合并后，再从新的聚合对象源目录导入和导出。

### 9.4 LOD 匹配不到

- 确认 Dump 时游戏确实在绘制 LOD 网格。
- 先降低过滤强度，再调整几何匹配阈值。
- 分别尝试体素和点云匹配。
- 增加预过滤候选数或顶点组匹配候选数会更慢。
- 旧 schema 或对象源变化后重新执行 LOD 提取。
- 使用自定义 INI 模板时确认模板包含 LOD 逻辑。

### 9.5 CrossIB.json 缺失或过期

CrossIB 应在面板中选择一份当前 Frame Dump，生成或覆盖 `CrossIB.json v2`。若 dump 不含源对象的任何 Component IB，Velo 会拒绝并保持原 JSON 不动。

不要为闪避、攻击、描边或残影逐场景累积 dump；这些 pass 由公共 ShaderRegex capability profile 覆盖。若未来 shader 结构真正超出 profile，应升级 Velo Tools 的公共规则，而不是给单个角色补 VS Hash。

### 9.6 插槽风格贴图导出失败

常见原因：

- `ShaderTextureUsage.json` 缺失或过期。
- 过滤掉了必需的 fresh slot 证据。
- 多个形态具有完全相同的槽位布局。
- 必需 DDS 或 Resource 缺失。
- 同一 Hash 存在冲突 payload。
- 某个 Component 更适合取消勾选，保留原生 Hash 风格。

不要通过删除条件让导出“通过”。应补充 Dump、修复对象源目录，或明确取消无法安全区分的 Component。

### 9.7 取消 slot-style 后仍看到 slot 写法

- 点击 **列出组件**刷新列表。
- 确认取消的是当前对象源目录中的正确 Component。
- 确认导出使用的是新的 `mod.ini`，而不是旧测试目录。
- 跨场景节名按聚合根全局 Component 编号显示，不要把子 IB 的 local 编号当作最终编号。

正确导出中，取消勾选的 Component 使用原生 Hash Resource/TextureOverride，不会保留该 Component 的 slot setter。

### 9.8 Hash-style Component 仍显示游戏原贴图

先区分两种情况：

1. INI 没有命中当前资源 identity。
2. 目标资源在冷进入时尚未流送到旧 Dump 记录的状态。

在实际失败状态下抓取新 Dump，再检查贴图文件名、FrameAnalysis 和 `ShaderTextureUsage.json`。Velo 不会从 DDS 内容推断缺失 Hash。

如果先进入另一个场景后贴图才生效，通常说明冷进入和后续流送状态的证据不同。应补充冷进入状态的 Dump，而不是把其它 Component 改成错误 slot。

### 9.9 整个模型变绿或贴图像法线

- 检查当前 diffuse DDS 是否本来就是绿色测试图。
- 检查 `ResourceTexture` 的 `filename` 是否指向正确 DDS。
- 检查 slot setter 是否把 normal 资源写进 diffuse 槽位。
- 检查 assignment signature 是否覆盖所有实际写入槽位。
- 检查 Restore 是否在目标 draw 后执行，而不是让状态泄漏到后续描边。

先确认资源和槽位，再判断是否是 shader 问题。绿色不自动等于法线污染。

### 9.10 形态切换后偶尔闪回原贴图

- 为每个形态补充 RAW Frame Dump。
- 用同一形态标签累积多次有效证据。
- 确认只有同 VB 的真实多形态 Component 共享形态域。
- 检查锚点是否过期。
- 槽位布局无法区分时，取消该 Component 的 slot-style。

### 9.11 `ResourceBypassPST*` 是空节

这是正常的运行时引用句柄。确认对应 IB 同时存在 Backup、slot transaction、Cleanup 和 Restore 即可。

只有空节而没有任何引用才是异常；不要因为节体为空就手工删除整组。

### 9.12 Textures 缺少 DDS

- 确认完整导出启用了 **复制贴图**。
- 确认聚合根顶层确实存在该文件。
- 检查导出是否因同 Hash 冲突而中止。
- 已有同名文件会被保留，不会被来源文件覆盖。
- 使用旧输出目录时检查文件是否被作者手工改名。

### 9.13 Raw Mesh 改拓扑后导出失败

**保真直通**要求拓扑不变。改拓扑后选择 **重建**，并接受非标准属性可能丢失或填默认值。

如果只修改顶点位置并希望保持其它字节完全不变，恢复原拓扑并使用 **保真直通**。

### 9.14 权重传递结果不自然

- 检查来源组是否有有效正权重证据。
- 检查来源和目标是否处于预期变形状态。
- 检查手动供体和镜像映射。
- 降低平滑强度或次数。
- 检查 UV 接缝和独立网格岛。
- 在 Weight Paint 中逐骨骼检查，不要只看总形状。

### 9.15 自定义模板后某项 Velo 功能消失

自定义模板接管完整 INI 生成。先关闭 **使用自定义模板**，用默认模板导出一次进行对照。

如果默认模板正常，应把需要的 LOD、ShapeKey、跨场景或其它扩展逻辑明确移植进自定义模板，而不是依赖导出器猜测模板意图。

## 10. 限制与术语

### 10.1 已知边界

- Velo Tools 不能替代有效 Frame Dump。
- Hash-style 不能命中源证据中不存在的新资源 identity。
- slot-style 不能安全区分完全相同且没有额外证据的槽位布局。
- 自动 LOD 匹配需要游戏真实绘制目标 LOD。
- CrossIB 和跨场景流程不能推断从未抓取的场景。
- Raw Mesh 重建无法保证所有非标准顶点属性无损。
- 权重工具不能替代最终人工权重检查。
- 自定义 INI 模板作者负责保留所需扩展逻辑。
- 静态导出成功不能替代游戏中的冷进入、切换、LOD 和多形态测试。

### 10.2 术语表

| 术语 | 含义 |
| --- | --- |
| Mod | 最终加载到游戏中的成品目录。 |
| Frame Dump / Dump | 3Dmigoto 抓取的当帧资源、draw 和日志证据。 |
| 对象源目录 | 从 Dump 提取或合并后，供 Blender 导入和再次导出的数据目录。 |
| 自包含聚合根 | 跨场景唯一持久对象源：聚合 Buffer、Metadata/STU、顶层 DDS 与 schema-v3 manifest，不依赖子 payload。 |
| canonical morph namespace | 聚合根稳定的 runtime ShapeKey ID 空间，也包含为真正来源独有 morph 确定性分配的新 ID。 |
| IB | Index Buffer，用于描述索引和 draw 范围身份。 |
| VB | Vertex Buffer，用于描述顶点数据和几何身份。 |
| Hash | 3Dmigoto 资源 identity，不是 DDS 像素内容的校验值。 |
| Component | EFMI/WWMI 的部件编号单位。 |
| draw | 一次绘制调用；同一 Component 可以包含多个 draw。 |
| Merged | 使用跨 Component 统一顶点组列表的骨架策略；EFMI 另分统一制作后回译和官方 MergedSkeleton runtime。 |
| Per-Component | 每个 Component 使用局部顶点组列表的骨架模式。 |
| Per-Component (from Merged) | 用 Merged 编辑，再导出为 Per-Component 运行结构。 |
| LOD | Level of Detail，按观察距离使用的不同几何层级。 |
| ShapeKey | Blender 形态键技术标识；当前 EFMI UI 也保留该技术名。 |
| Deform ID | WWMI ShapeKey 的数字身份；每个 batch 连续管理 127 个 ID。 |
| Native ShapeKey | 位于 Metadata 声明范围内、继续走 WWMI 原生 shader 的 Deform ID。 |
| External Custom ShapeKey | 超出 Metadata 原生范围且含有效 delta、由独立 shader 叠加的 Deform ID。 |
| INI | 3dmigoto Mod 的运行时配置文本。 |
| DDS | 常用贴图文件格式。 |
| Buffer | 保存位置、索引、权重、法线、UV、ShapeKey 等数据的二进制资源。 |
| sidecar | 与对象源目录配套的旁路证据文件，例如 `CrossIB.json v2`。 |
| CrossIB capability ABI | 由公共 classifier 产生、由各 mod 路由消费的社区兼容 EFMI shader 能力编号 200/201/202/203/204/205；规则 producer 不依赖 Hash。 |
| slot-style | 在目标 draw 范围内按 `ps-tN` 槽位证据重绑贴图。 |
| hash-style | 按游戏资源 Hash 匹配并替换贴图。 |
| assignment signature | 一个 slot 分支用于判断身份的完整正向槽位格式组合。 |
| owning IB | 对某节、资源或贴图事务拥有运行时生命周期的 IB。 |
| `_ibN` | 跨场景最终 INI 中用于隔离 owning IB 的命名空间后缀。 |
| fresh evidence | 当前 draw 明确覆盖得到的证据，而不是继承或残留状态。 |
| fail closed | 证据不足时停止导出，而不是生成猜测结果。 |

### 10.3 UI 名称约定

本文对普通功能优先使用当前中文 UI 名称，例如：

- **顶点组工具**
- **网格工具**
- **权重工具**
- **对象源目录**
- **组件集合**
- **Mod 输出目录**
- **跨场景折叠合并**
- **插槽风格贴图**
- **形态贴图合并**
- **形态锚点**
- **原始网格工具**

技术标识保持原文，避免把数据格式或运行时身份翻译成不一致的名称。
