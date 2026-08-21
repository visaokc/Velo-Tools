"""Chinese UI text for the Velo Endfield bridge."""

VTEF_TOOL_MODE_ITEMS = [
    ("EXPORT_MOD", "导出 Mod", "将所选集合导出为 EFMI mod"),
    ("IMPORT_OBJECT", "导入对象", "从提取目录导入 .fmt/.ib/.vb 模型"),
    ("EXTRACT_LOD_DATA", "提取 LOD 数据", "从开放世界 Frame Dump 中提取 LOD 并写入 Metadata.json"),
    ("EXTRACT_FRAME_DATA", "提取帧数据", "从 Frame Dump 中提取 EFMI 对象"),
]

IMPORT_SKELETON_ITEMS = [
    (
        "MERGED",
        "Merged（统一顶点组）",
        "使用 Metadata v4 中由骨骼矩阵生成的跨组件统一编号导入；可选择合并重复骨骼。",
    ),
    (
        "COMPONENT",
        "Per-Component（部件独立）",
        "保持每个组件自己的局部顶点组编号。",
    ),
]

MOD_SKELETON_ITEMS = [
    (
        "MERGED",
        "Merged（统一顶点组）",
        "Blender 内使用统一顶点组编辑，导出时按各 Component 的 vg_map 回译成本地编号并使用 Per-Component runtime。",
    ),
    (
        "COMPONENT",
        "Per-Component（部件独立）",
        "按组件局部顶点组编号导出。",
    ),
    (
        "MERGED_SKELETON",
        "Merged（合并骨架）",
        "使用 EFMI v1.4.1 Merged Skeleton runtime，以统一编号导出，支持跨组件权重、骨架缩放与多实例。",
    ),
]

VTEF_PROPERTY_TEXTS = {
    "tool_mode": ("模式", "切换当前终末地 EFMI/Velo 工具功能。"),
    "frame_dump_folder": ("Frame Dump 目录", "包含 Frame Dump 文件和 log.txt 的目录。"),
    "extract_output_folder": ("输出目录", "提取出的 EFMI 对象、贴图和 Metadata.json 的写入目录。"),
    "import_extracted_objects": ("提取后导入 Blender", "提取完成后自动把对象导入 Blender，便于快速浏览 Dump。"),
    "tolerate_extraction_errors": ("容忍提取错误", "提取时遇到单个对象错误仍继续处理，其错误对象会被跳过。"),
    "verbose_logging": ("详细日志", "向控制台输出更详细的调试信息。"),
    "skip_static_objects": ("对象过滤：跳过静态对象", "跳过没有权重数据的静态对象，例如场景物件。"),
    "skip_object_min_component_count_enabled": ("对象过滤：最少组件数", "启用后跳过组件数量少于指定值的对象。"),
    "skip_object_min_component_count": ("最少组件数", "对象至少需要包含多少个组件才会被提取。"),
    "skip_object_min_texture_count_enabled": ("对象过滤：最少贴图数", "启用后跳过贴图数量少于指定值的对象。"),
    "skip_object_min_texture_count": ("最少贴图数", "对象至少需要引用多少张贴图才会被提取。"),
    "skip_object_resource_hashes_enabled": ("对象过滤：资源 Hash", "启用后只保留包含指定 IB/VB/贴图等资源 Hash 的对象。"),
    "skip_object_resource_hashes": ("资源 Hash", "用于筛选对象的资源 Hash，支持用逗号、分号或空格分隔。"),
    "skip_draw_resource_hashes_enabled": ("组件过滤：黑名单 Hash", "启用后在提取对象时跳过包含指定资源 Hash 的组件。"),
    "skip_draw_resource_hashes": ("黑名单 Hash", "要从普通帧提取中排除的组件资源 Hash，支持用逗号、分号或空格分隔。"),
    "skip_small_textures": ("贴图过滤：跳过小贴图", "跳过低于指定大小的贴图文件。"),
    "skip_small_textures_size": ("最小大小 KB", "贴图文件小于该 KB 数时会被跳过。"),
    "skip_jpg_textures": ("贴图过滤：跳过 .jpg", "跳过 .jpg 贴图；这类文件通常是渐变图或遮罩。"),
    "object_source_folder": ("对象源目录", "包含组件 .fmt/.ib/.vb、Metadata.json、TextureUsage.json 的 EFMI 对象目录。"),
    "color_storage": ("顶点色", "控制导入时如何保存和显示 COLOR 顶点色数据。"),
    "import_skeleton_type": ("骨架", "顶点组命名方式。Merged 使用 Metadata v4 内置 vg_map；Per-Component 使用各组件局部编号。"),
    "skip_empty_vertex_groups": ("跳过空顶点组", "导入 Merged 对象时自动移除没有任何权重的顶点组。"),
    "dedupe_bones": ("合并重复骨骼", "把矩阵完全相同的骨骼重映射到同一统一顶点组。LOD 几何不等价时应关闭。"),
    "mirror_mesh": ("镜像网格", "自动镜像网格以匹配游戏内左右方向；直接修改网格数据，不改变物体 Transform 的 Scale X。"),
    "lod_frame_dump_folder": ("LOD Frame Dump 目录", "存放开放世界 LOD Frame Dump 的目录；需要包含 log.txt。"),
    "allow_lod_overwrite": ("允许覆盖 LOD 数据", "允许在 LOD 对象名或顶点数冲突时替换 Metadata.json 中已有的组件 LOD 数据。"),
    "geo_matcher_method": ("匹配方法", "选择用于自动查找 LOD 网格的几何匹配算法。"),
    "geo_matcher_voxel_error_threshold": ("几何匹配误差阈值", "LOD 对象通过体素匹配所需的相似度百分比。"),
    "geo_matcher_error_threshold": ("几何匹配误差阈值", "LOD 对象通过点云匹配所需的相似度百分比。"),
    "import_matched_lod_objects": ("导入匹配到的 LOD", "把自动匹配到的 LOD 网格导入 Blender，方便人工检查。"),
    "skip_lods_below_error_threshold": ("低于阈值的 LOD 跳过", "相似度低于几何匹配误差阈值时跳过该 LOD，而不是中断整个提取流程。"),
    "geo_matcher_sensivity": ("几何匹配灵敏度", "控制原始距离值如何映射到最终相似度百分比。"),
    "skip_component_below_vertex_count_enabled": ("组件过滤：最少顶点数", "启用后从 LOD 匹配候选中排除顶点数过少的组件。"),
    "skip_component_below_vertex_count": ("最少顶点数", "LOD 候选组件低于该顶点数时会被排除。"),
    "skip_component_hashes_enabled": ("组件过滤：黑名单 Hash", "启用后从 LOD 匹配候选中排除指定 IB Hash 的组件。"),
    "skip_component_hashes": ("黑名单 Hash", "要从 LOD 匹配候选中排除的 IB Hash，支持用逗号、分号或空格分隔。"),
    "geo_matcher_voxel_size": ("体素大小", "体素匹配时的网格划分大小；数值越小精度越高、计算越慢。"),
    "geo_matcher_sample_size": ("点云采样数", "点云匹配时从网格表面采样的点数。"),
    "geo_matcher_prefilter_voxel_size": ("预过滤体素大小", "LOD 候选预过滤阶段使用的体素大小。"),
    "geo_matcher_prefilter_sample_size": ("预过滤采样数", "LOD 候选预过滤阶段使用的点云采样数。"),
    "geo_matcher_prefilter_candidates_count": ("预过滤候选数", "进入精确几何匹配的候选数量；数量越多越慢。"),
    "vg_matcher_candidates_count": ("顶点组匹配候选数", "按顶点组重心距离预选的候选数量。"),
    "component_collection": ("组件集合", "包含 Component 0、Component_1 等 EFMI 组件对象的 Blender 集合。"),
    "mod_output_folder": ("Mod 输出目录", "写入 mod.ini、Meshes 和 Textures 的 Mod 目录。"),
    "mod_skeleton_type": ("骨架", "选择统一顶点组回译、原生 Per-Component 或 EFMI v1.4.1 官方 Merged Skeleton。"),
    "apply_all_modifiers": ("应用所有修改器", "导出时在临时副本上应用所有可见修改器。"),
    "copy_textures": ("复制贴图", "导出时把引用的贴图文件复制到 Mod 输出目录。"),
    "write_ini": ("写出 mod.ini", "导出时在输出目录写入新的 mod.ini。"),
    "comment_ini": ("写入注释", "在生成的 INI 中写入注释，便于阅读结构。"),
    "ignore_nested_collections": ("忽略嵌套集合", "启用后不会导出组件集合内部子集合中的对象。"),
    "ignore_hidden_collections": ("忽略隐藏集合", "启用后不会导出组件集合内部隐藏子集合中的对象。"),
    "ignore_hidden_objects": ("忽略隐藏对象", "启用后不会导出组件集合中的隐藏对象。"),
    "ignore_muted_shape_keys": ("忽略禁用形态键", "启用后不会导出未勾选的 ShapeKey。"),
    "allow_export_without_lods": ("允许无 LOD 导出", "允许 Metadata.json 没有 LOD 数据时仍导出；开放世界中可能无法正确加载。"),
    "add_missing_vertex_groups": ("补齐缺失顶点组", "按顶点组编号补齐中间缺失项，例如存在 0 和 2 时补 1。"),
    "fill_missing_mesh_data": ("补齐缺失网格数据", "自动生成缺失的 COLOR 黑色顶点色和空 TEXCOORD.xy。"),
    "max_instance_count": ("最大实例数", "Merged Skeleton 支持的同对象实例上限；每个实例会按统一骨骼数预留 VRAM。"),
    "use_spatial_identification": ("空间身份识别", "用位置验证组件归属，避免误改多个对象共享的阴影等组件；仅支持带权重对象。"),
    "spatial_identification_threshold": ("空间识别组件阈值", "形成新空间身份前必须出现的组件数量；应高于非唯一网格数且不高于 LOD1+ 中使用的 LOD0 网格数。"),
    "skeleton_scale": ("骨架缩放", "在游戏中缩放模型；Per-Component 骨架不支持。"),
    "partial_export": ("部分导出", "高级用途：只导出选定 buffer，跳过 INI 生成和资源复制。"),
    "export_index": ("Index Buffer", "导出索引 buffer，保存顶点与面的关联。"),
    "export_positions": ("Position Buffer", "导出位置 buffer，保存每个顶点坐标。"),
    "export_blends": ("Blend Buffer", "导出骨骼编号和权重 buffer。"),
    "export_vectors": ("Vector Buffer", "导出法线和切线 buffer。"),
    "export_colors": ("Color Buffer", "导出 COLOR 顶点色 buffer。"),
    "export_texcoords": ("TexCoord Buffer", "导出 UV 和 COLOR1 顶点色 buffer。"),
    "export_shapekeys": ("ShapeKey Buffer", "导出 ShapeKey 相关 buffer。"),
    "mod_name": ("Mod 名称", "显示在通知和 Mod 管理器中的名称。"),
    "mod_author": ("作者", "显示在通知和 Mod 管理器中的作者名。"),
    "mod_desc": ("Mod 描述", "显示在通知和 Mod 管理器中的简短描述。"),
    "mod_link": ("Mod 链接", "显示在通知和 Mod 管理器中的网页链接。"),
    "mod_logo": ("Mod 图标", "512x512 的 .dds 图标贴图（BC7 SRGB），导出为 Textures/Logo.dds。"),
    "use_custom_template": ("使用自定义模板", "使用指定的 jinja2 模板生成完整 mod.ini。"),
    "custom_template_live_update": ("模板实时更新", "控制 INI 模板实时生成线程是否运行。"),
    "custom_template_source": ("模板存储", "选择自定义 INI 模板的存储位置。"),
    "custom_template_path": ("模板文件", "外部 mod.ini 模板文件路径。新建模板时可先从内置编辑器复制默认内容。"),
    "use_ini_toggles": ("使用 INI 开关", "把配置好的 INI 开关逻辑写入 mod.ini。"),
    "import_as_component_collections": ("按组件创建子集合", "导入模型时在对象父集合下创建 C0/C1/... 子集合；关闭后沿用上游 EFMI 的单集合导入。"),
    "extract_components_filter": ("组件过滤", "可选组件范围，例如 0-8 或 0,1,5-7。过滤后输出仍连续编号为 Component 0..N。"),
    "import_texture": ("导入贴图", "导入模型后依据 TextureUsage.json 给网格指定源目录内的 .dds 贴图。"),
}

VTEF_ENUM_ITEMS = {
    "color_storage": [
        ("LINEAR", "Linear", "按真实线性颜色显示顶点色，并以完整 float 精度存入 color_attributes。"),
        ("LEGACY", "sRGB（旧版）", "按 sRGB 偏移显示顶点色，并以 8-bit float 精度存入旧 vertex_colors。"),
    ],
    "geo_matcher_method": [
        ("VOXEL", "体素匹配（确定性）", "把网格采样为指定大小的体素网格进行匹配。"),
        ("POINT_CLOUD", "点云匹配（随机）", "按三角面面积在网格表面均匀随机采样点进行匹配。"),
    ],
    "custom_template_source": [
        ("INTERNAL", "内置编辑器", "使用 Blender 文本编辑器中的文本作为自定义模板。"),
        ("EXTERNAL", "外部文件", "使用指定外部文件作为自定义模板。"),
    ],
}

VTEF_CLASS_TEXTS = {
    "VTEF_PT_SidePanelAdvancedLodsExtraction": ("高级", None),
    "VTEF_PT_SidePanelLodsExtractionFooter": ("提取", None),
    "VTEF_PT_SidePanelPartialExport": ("部分导出", None),
    "VTEF_PT_SidePanelAdvancedExport": ("高级", None),
    "VTEF_PT_SidePanelModInfo": ("Mod 信息", None),
    "VTEF_PT_SidePanelIniTemplate": ("INI 模板", None),
    "VTEF_PT_SidePanelExportFooter": ("导出", None),
    "VTEF_Import": ("导入模型", "从提取目录导入 EFMI 对象。"),
    "VTEF_Export": ("导出 Mod", "将当前组件集合导出为 EFMI Mod。"),
    "VTEF_ExtractFrameData": ("从 Dump 提取模型", "从当前 Frame Dump 提取可用的 EFMI 对象。"),
    "VTEF_ImportLODData": ("从 Dump 提取 LOD", "从 LOD Frame Dump 匹配并写入 LOD 数据。"),
    "VTEF_OpenIniTemplateEditor": ("编辑模板", "打开当前 INI 模板进行查看或编辑。"),
    "VTEF_IniTemplateEditor_ToggleLiveUpdates": ("启动 INI 更新", "切换外部 INI 模板的实时更新。"),
    "VTEF_IniTemplateEditor_Reset": ("重置模板", "把内置 INI 模板重置为默认内容。"),
    "VTEF_PT_TEXT_EDITOR_IniTemplate": ("INI 模板 - Velo Tools 终末地", None),
}

INI_TOGGLE_CLASS_TEXTS = {
    "VTEF_PT_SidePanelIniToggles": ("INI 开关", None),
    "VTEF_CollapseToggleVars": ("折叠变量", "折叠列表中所有开关变量，并退出编辑状态。"),
    "VTEF_ExpandToggleVars": ("展开变量", "展开列表中所有开关变量，并进入编辑状态。"),
    "VTEF_AddToggleVar": ("添加开关变量", "添加一个用于控制对象可见性的 INI 开关变量。"),
    "VTEF_RemoveToggleVar": ("删除开关变量", "从 INI 开关列表中删除该变量。"),
    "VTEF_MoveToggleVar": ("移动开关变量", "调整该开关变量在列表中的顺序。"),
    "VTEF_EditToggleVar": ("编辑变量", "配置变量热键和默认状态。"),
    "VTEF_AddVarState": ("添加状态", "为该变量添加一个新状态；每个状态可控制多个对象。"),
    "VTEF_RemoveVarState": ("删除状态", "从该 INI 开关变量中删除当前状态。"),
    "VTEF_MoveToggleVarState": ("移动状态", "调整当前状态在变量状态列表中的顺序。"),
    "VTEF_AddVarStateObject": ("添加状态对象", "把对象加入当前状态，使它按该状态条件显示。"),
    "VTEF_RemoveVarStateObject": ("删除状态对象", "从当前状态中移除该对象。"),
    "VTEF_EditVarStateObject": ("编辑条件", "打开当前对象的自定义显示条件窗口。"),
    "VTEF_AddCondition": ("添加条件", "添加一个新的自定义显示条件。"),
    "VTEF_RemoveCondition": ("删除条件", "删除当前自定义显示条件。"),
    "VTEF_OpenIniTogglesImportExportEditor": ("打开 INI 开关导入导出", "打开文本编辑器窗口，用于导入或导出 INI 开关变量。"),
    "VTEF_ExportIniToggles": ("导出 INI 开关", "把当前 INI 开关变量导出为可再次导入的 JSON 文本。"),
    "VTEF_ImportIniToggles": ("导入 INI 开关", "从当前文本文件中的 JSON 导入 INI 开关变量。"),
    "VTEF_PT_TEXT_EDITOR_IniToggles": ("INI 开关 - Velo Tools", None),
}

INI_TOGGLE_PROPERTY_TEXTS = {
    "IniToggles": {
        "replace_vars_on_import": ("导入时替换同名变量", "导入 INI 开关时，用导入内容替换已有同名变量；关闭时跳过重复项。"),
        "clear_vars_on_import": ("导入前清空变量", "导入 INI 开关前先删除当前已有的全部变量。"),
        "hide_empty_states": ("隐藏无对象的 -1 状态", "隐藏没有对象的 -1 空状态，减少 UI 占用空间。"),
        "hide_default_conditions": ("隐藏默认条件", "隐藏自动生成的默认条件，减少 UI 占用空间。"),
    },
    "ToggleVar": {
        "default_state": ("默认状态", "该开关变量在初始化时使用的状态值。"),
        "hotkeys": ("热键", "用于在多个状态之间切换的按键；组合键用空格分隔，多组热键用分号分隔。"),
        "ui_expanded": ("展开变量", "进入或退出该变量的编辑状态。"),
    },
    "ToggleVarStateCondition": {
        "logic": ("逻辑", "控制多个条件如何共同影响对象显示。"),
        "type": ("类型", "选择绑定已有 INI 开关变量，或绑定自定义 INI 变量。"),
        "var": ("变量", "要与状态值比较的变量。自定义变量名前加 $ 可避免自动格式化为 $swapvar_name。"),
        "operator": ("比较", "控制变量和值如何比较后才算条件成立。"),
        "state": ("状态值", "用于和变量进行比较的状态值。"),
    },
    "ToggleVarStateObject": {
        "object": ("对象", "在默认条件下，所选对象只会在变量等于当前状态值时显示。"),
    },
}

INI_TOGGLE_ENUM_ITEMS = {
    ("ToggleVarStateCondition", "logic"): [
        ("&&", "AND", "当前条件和前一个条件都为 TRUE 时对象才显示；AND 会先于 OR 计算。"),
        ("||", "OR", "只要当前条件为 TRUE，对象即可显示；AND 会先于 OR 计算。"),
    ],
    ("ToggleVarStateCondition", "type"): [
        ("TOGGLE", "开关变量", "绑定到已有 INI 开关变量。"),
        ("EXTERNAL", "自定义变量", "绑定到自定义 INI 变量。"),
    ],
    ("ToggleVarStateCondition", "operator"): [
        ("==", "==", "变量必须等于指定值。"),
        ("!=", "!=", "变量必须不等于指定值。"),
        (">", ">", "变量必须大于指定值。"),
        ("<", "<", "变量必须小于指定值。"),
        (">=", ">=", "变量必须大于或等于指定值。"),
        ("<=", "<=", "变量必须小于或等于指定值。"),
    ],
}

CROSSIB_PROPERTY_TEXTS = {
    "CrossIBMapping": {
        "source_kind": ("源类型", "选择单个源物体或整个源集合来提供跨 IB 几何。"),
        "source_object": ("源物体", "作为 provider 的单个 mesh 物体。"),
        "source_collection": ("源集合", "集合内每个 mesh 都会作为 provider。"),
        "target_component": ("目标部件", "使用源几何重画到哪个目标 Component。"),
    },
    "CrossIBSettings": {
        "enabled": ("启用跨 IB（CrossIB）", "导出时启用 CrossIB 渲染，把源几何接入目标部件的渲染管线。"),
        "frame_dump_folder": ("帧 Dump 文件夹", "可选。手动指定原始帧 Dump；留空时从模型源目录及父级自动扫描。"),
    },
}

CROSSIB_ENUM_ITEMS = {
    ("CrossIBMapping", "source_kind"): [
        ("OBJECT", "物体", "使用单个 mesh 物体作为 provider。"),
        ("COLLECTION", "集合", "使用集合内所有 mesh 作为 provider。"),
    ],
}

CROSSIB_CLASS_TEXTS = {
    "CROSSIB_PT_Panel": ("Cross Index Buffer（跨 IB）", None),
    "CROSSIB_OT_AddMapping": ("添加跨 IB 映射", "添加一个 CrossIB 源到目标部件的映射。"),
    "CROSSIB_OT_RemoveMapping": ("删除跨 IB 映射", "删除当前 CrossIB 映射。"),
}

SHAPEKEY_PROPERTY_TEXTS = {
    "ShapeKeySettings": {
        "enabled": (
            "导出自定义 ShapeKey",
            "启用后，为 EFMI v0.6.2 官方 ShapeKey 导出添加持久控制变量；位置差值与运行时处理由官方核心负责。",
        ),
        "merge_buffers": (
            "合并 Buffer 文件",
            "把同一 Component 内的 ShapeKey delta/map 合并到更少的 buffer 中，减少 Meshes 目录文件数量。",
        ),
        "show_detector": ("显示已识别的 ShapeKey", "展开或折叠下方的实时识别列表。"),
    },
}

SHAPEKEY_CLASS_TEXTS = {
    "SHAPEKEY_OT_ToggleGroup": ("折叠/展开分组", "折叠或展开检测列表中的 Deform 分组。"),
    "SHAPEKEY_OT_RefreshDetected": ("刷新已识别 ShapeKey", "重新扫描当前组件集合中的 Deform ShapeKey。"),
}

TOOLBOX_CLASS_TEXTS = {
    "VTEF_MergeVertexGroups": (
        "合并同名顶点组",
        "把选中物体中小数后缀前名称相同的顶点组合并，例如把 7、7.1、7.3 合并到同一组。",
    ),
    "VTEF_FillGapsInVertexGroups": (
        "补齐顶点组编号缺口",
        "为选中物体补齐缺失的数字顶点组并按编号排序，例如有 0、4、2 时补齐 1、3。",
    ),
    "VTEF_RemoveUnusedVertexGroups": (
        "移除未使用顶点组",
        "从选中物体移除没有权重的顶点组。",
    ),
    "VTEF_RemoveAllVertexGroups": (
        "移除所有顶点组",
        "从选中物体移除全部顶点组。",
    ),
    "VTEF_ApplyModifierForObjectWithShapeKeysOperator": (
        "应用带 ShapeKey 的修改器",
        "为带 ShapeKey 的物体应用选中的修改器并从修改器堆栈移除，用于绕过 Blender 不允许直接应用这类修改器的限制。",
    ),
    "VTEF_CreateMergedObject": (
        "创建合并雕刻物体",
        "把选中物体临时合并为一个用于多物体雕刻的对象。完成前不要在原始物体上增删顶点。",
    ),
    "VTEF_ApplyMergedObjectSculpt": (
        "回写合并雕刻",
        "把合并雕刻物体上的顶点位置变化回写到原始物体。",
    ),
    "VTEF_ApplyMergedObjectSculptWithShapekeys": (
        "回写雕刻到形态键",
        "把合并雕刻物体上的顶点位置变化回写到原始物体，并把位置差值应用到所有 ShapeKey。",
    ),
    "VTEF_ConvertVertexColors": (
        "顶点色转为 Linear",
        "把选中物体的 COLOR 和 COLOR1 顶点色层迁移到新的 Linear 存储方式。",
    ),
    "VTEF_FillMissingMeshData": (
        "补齐缺失网格数据",
        "为选中物体生成缺失的 COLOR 黑色顶点色和空 TEXCOORD.xy 数据。",
    ),
}

TOOLBOX_PROPERTY_TEXTS = {
    "VTEF_ApplyModifierForObjectWithShapeKeysOperator": {
        "disable_armatures": (
            "不包含骨架变形",
            "应用修改器时跳过 Armature 变形，避免把骨架姿态烘焙进带 ShapeKey 的网格。",
        ),
    },
}

TOOLBOX_DIALOG_TEXTS = {
    "VTEF_ApplyModifierForObjectWithShapeKeysOperator": {
        "no_modifier_selected": "未选择任何修改器。",
        "animation_warning": (
            "警告：",
            "              该物体的 ShapeKey 包含动画数据",
            "              （例如驱动器、关键帧等）。",
            "              这些数据会在应用修改器时丢失。",
        ),
    },
}
