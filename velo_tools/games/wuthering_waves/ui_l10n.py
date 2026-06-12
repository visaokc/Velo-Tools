"""Chinese UI text for the Velo WWMI bridge.

Consumed by the driver (__init__.py) which patches class bl_label /
bl_description and rebuilds property annotations BEFORE registration.
tool_mode / mod_skeleton_type texts live in the driver itself (they are
velo-owned re-definitions, not core patches) and are NOT listed here.
Technical terms (IB / VB / Hash / Merged / Per-Component / Frame Dump /
LOD / INI / ShapeKey / buffer ...) stay in English by project convention.
"""

# VTWW_Settings property name/description, keyed by property identifier.
WWMI_PROPERTY_TEXTS = {
    # Extract Frame Data
    "frame_dump_folder": ("Frame Dump 目录", "包含 Frame Dump 文件和 log.txt 的目录。"),
    "skip_small_textures": ("贴图过滤：跳过小贴图", "跳过低于指定大小的贴图文件。"),
    "skip_small_textures_size": ("最小大小 KB", "贴图文件小于该 KB 数时会被跳过；默认 256KB。"),
    "skip_jpg_textures": ("贴图过滤：跳过 .jpg", "跳过 .jpg 贴图；这类文件通常是渐变图或遮罩。"),
    "skip_same_slot_hash_textures": (
        "贴图过滤：跳过同槽同 Hash",
        "若某贴图的 Hash 在所有组件的同一槽位都出现则跳过。可能会过滤掉有用的贴图！",
    ),
    "skip_known_cubemap_textures": (
        "贴图过滤：跳过已知 Cubemap",
        "跳过 Hash 在已知 cubemap 列表中的贴图；这类贴图经常加载不正确。",
    ),
    "extract_output_folder": ("输出目录", "提取出的 WWMI 对象的写入目录。"),
    # Object Import
    "object_source_folder": ("对象源目录", "包含 WWMI 对象组件和贴图的目录。"),
    "color_storage": ("顶点色", "控制导入时如何保存和显示顶点色数据。"),
    "import_skeleton_type": ("骨架", "顶点组的处理方式。"),
    "skip_empty_vertex_groups": (
        "跳过空顶点组",
        "自动移除导入组件中没有权重的顶点组，使每个组件的顶点组列表只包含实际使用的项。",
    ),
    "mirror_mesh": (
        "镜像网格",
        "自动镜像网格以匹配游戏内左右方向；直接修改网格数据，不改变物体 Transform 的 Scale X。",
    ),
    # Mod Export
    "component_collection": ("组件集合", "包含 Component 0、Component_1 等 WWMI 组件对象的 Blender 集合。"),
    "mod_output_folder": ("Mod 输出目录", "写入 mod.ini、Meshes 和 Textures 的 Mod 目录。"),
    "apply_all_modifiers": ("应用所有修改器", "导出时在每个对象的临时副本上应用所有可见修改器。"),
    "copy_textures": ("复制贴图", "导出时把引用的贴图文件复制到 Mod 输出目录。"),
    "write_ini": ("写出 mod.ini", "导出时在输出目录写入新的 mod.ini。"),
    "comment_ini": ("写入注释", "在生成的 INI 中写入注释，便于阅读结构。"),
    "ignore_nested_collections": ("忽略嵌套集合", "启用后不会导出组件集合内部子集合中的对象。"),
    "ignore_hidden_collections": ("忽略隐藏集合", "启用后不会导出组件集合内部隐藏子集合中的对象。"),
    "ignore_hidden_objects": ("忽略隐藏对象", "启用后不会导出组件集合中的隐藏对象。"),
    "ignore_muted_shape_keys": ("忽略禁用形态键", "启用后不会导出未勾选的 ShapeKey。"),
    # Advanced
    "add_missing_vertex_groups": (
        "补齐缺失顶点组",
        "按顶点组编号补齐中间缺失项，例如存在 0 和 2 时补 1。",
    ),
    "fill_missing_mesh_data": (
        "补齐缺失网格数据",
        "自动生成缺失的 COLOR（填充 [0, 0.25, 0, 1.0]）、TEXCOORD.xy（空 UV）、"
        "TEXCOORD1.xy（空 UV）、TEXCOORD2.xy（复制 TEXCOORD.xy）和 TEXCOORD3.xy（正面投影）。",
    ),
    "unrestricted_custom_shape_keys": (
        "不受限自定义 ShapeKey",
        "允许给默认不带自定义 ShapeKey 的组件使用自定义 ShapeKey；会生成额外的 mod.ini 逻辑。",
    ),
    "skeleton_scale": ("骨架缩放", "在游戏中缩放模型（默认 1.0）；Per-Component 骨架不支持。"),
    "partial_export": (
        "部分导出",
        "高级用途：只导出选定 buffer；确认某些数据自上次导出未变化时可加快导出。"
        "会禁用 INI 生成和资源复制。",
    ),
    # Partial Export
    "export_index": ("Index Buffer", "导出索引 buffer，保存顶点与面的关联。"),
    "export_positions": ("Position Buffer", "导出位置 buffer，保存每个顶点坐标。"),
    "export_blends": ("Blend Buffer", "导出顶点组编号和权重 buffer。"),
    "export_vectors": ("Vector Buffer", "导出法线和切线 buffer。"),
    "export_colors": ("Color Buffer", "导出名为 COLOR 的顶点色 buffer。"),
    "export_texcoords": ("TexCoord Buffer", "导出 TEXCOORD UV 层 buffer。"),
    "export_shapekeys": ("ShapeKey Buffer", "导出 ShapeKey 相关 buffer。"),
    # Mod Info
    "mod_name": ("Mod 名称", "显示在通知和 Mod 管理器中的名称。"),
    "mod_author": ("作者", "显示在通知和 Mod 管理器中的作者名。"),
    "mod_desc": ("Mod 描述", "显示在通知和 Mod 管理器中的简短描述。"),
    "mod_link": ("Mod 链接", "显示在通知和 Mod 管理器中的网页链接。"),
    "mod_logo": (
        "Mod 图标",
        "512x512 的 .dds 图标贴图（BC7 SRGB），导出为 Textures/Logo.dds，显示在通知和 Mod 管理器中。",
    ),
    # Ini Template
    "use_custom_template": ("使用自定义模板", "使用指定的 jinja2 模板生成完整 mod.ini。"),
    "custom_template_live_update": ("模板实时更新", "控制 INI 模板实时生成线程是否运行。"),
    "custom_template_source": ("模板存储", "选择自定义 INI 模板的存储位置。"),
    "custom_template_path": (
        "模板文件",
        "mod.ini 模板文件路径。\n新建模板时可先从内置编辑器复制默认内容。",
    ),
    # Ini Toggles
    "use_ini_toggles": ("使用 INI 开关", "把配置好的 INI 开关逻辑写入 mod.ini。"),
}

# Enum item label/description, keyed by (class name, property identifier) then
# item identifier. Identifiers / icons / numbers are never touched, so upstream
# reordering or behavioral changes survive the patch.
WWMI_ENUM_TEXTS = {
    ("VTWW_Settings", "color_storage"): {
        "LINEAR": ("Linear", "按真实线性颜色显示顶点色，并以完整 float 精度存入 color_attributes。"),
        "LEGACY": ("sRGB（旧版）", "按 sRGB 偏移显示顶点色，并以 8-bit float 精度存入旧 vertex_colors。"),
    },
    ("VTWW_Settings", "import_skeleton_type"): {
        "MERGED": (
            "Merged",
            "导入的网格使用统一顶点组列表，任意组件的任意顶点都可刷权重到任意骨骼。"
            "Mod 优点：易于刷权重、支持自定义骨架缩放、支持高级权重（例如长发刷到披风）。"
            "Mod 缺点：模型更新有 1 帧延迟；同屏出现多个相同被改对象时 mod 会暂停。"
            "建议用途：新手、权重复杂的角色或声骸 mod。",
        ),
        "COMPONENT": (
            "Per-Component",
            "导入的网格按组件拆分顶点组列表，每个顶点只能刷权重到其所属组件。"
            "Mod 优点：模型更新无 1 帧延迟、性能略好。"
            "Mod 缺点：难以刷权重、权重选项非常有限、不支持自定义骨架缩放。"
            "建议用途：武器 mod 和简单改贴图。",
        ),
    },
    ("VTWW_Settings", "custom_template_source"): {
        "INTERNAL": ("内置编辑器", "使用 Blender 文本编辑器中的文本作为自定义模板。"),
        "EXTERNAL": ("外部文件", "使用指定外部文件作为自定义模板。"),
    },
    ("ToggleVarStateCondition", "logic"): {
        "&&": ("AND", "当前条件和前一个条件都为 TRUE 时对象才显示；AND 会先于 OR 计算。"),
        "||": ("OR", "只要当前条件为 TRUE，对象即可显示；AND 会先于 OR 计算。"),
    },
    ("ToggleVarStateCondition", "type"): {
        "TOGGLE": ("开关变量", "绑定到已有 INI 开关变量。"),
        "EXTERNAL": ("自定义变量", "绑定到自定义 INI 变量。"),
    },
    ("ToggleVarStateCondition", "operator"): {
        "==": ("==", "变量必须等于指定值。"),
        "!=": ("!=", "变量必须不等于指定值。"),
        ">": (">", "变量必须大于指定值。"),
        "<": ("<", "变量必须小于指定值。"),
        ">=": (">=", "变量必须大于或等于指定值。"),
        "<=": ("<=", "变量必须小于或等于指定值。"),
    },
}

# Core panel / operator bl_label + bl_description, keyed by class name.
# The root panel is NOT listed (the driver already sets it to 鸣潮 WWMI).
WWMI_CLASS_TEXTS = {
    "VTWW_PT_SidePanelPartialExport": ("部分导出", None),
    "VTWW_PT_SidePanelAdvancedExport": ("高级", None),
    "VTWW_PT_SidePanelModInfo": ("Mod 信息", None),
    "VTWW_PT_SidePanelIniTemplate": ("INI 模板", None),
    "VTWW_PT_SidePanelExportFooter": ("导出", None),
    "VTWW_PT_TEXT_EDITOR_IniTemplate": ("INI 模板 - Velo Tools 鸣潮", None),
    "VTWW_Import": ("导入模型", "从提取目录导入 WWMI 对象。"),
    "VTWW_Export": ("导出 Mod", "将当前组件集合导出为 WWMI Mod。"),
    "VTWW_ExtractFrameData": ("从 Dump 提取模型", "从当前 Frame Dump 提取可用的 WWMI 对象。"),
    "VTWW_OpenIniTemplateEditor": ("编辑模板", "打开当前 INI 模板进行查看或编辑。"),
    "VTWW_IniTemplateEditor_ToggleLiveUpdates": (
        "启动 INI 更新",
        "切换 INI 模板实时更新；开启后每次编辑模板都会按当前设置导出并写出 mod.ini。",
    ),
    "VTWW_IniTemplateEditor_Reset": ("重置模板", "警告：该操作会把自定义模板重置为默认内容！"),
}

INI_TOGGLE_CLASS_TEXTS = {
    "VTWW_PT_SidePanelIniToggles": ("INI 开关", None),
    "VTWW_PT_TEXT_EDITOR_IniToggles": ("INI 开关 - Velo Tools 鸣潮", None),
    "VTWW_OT_CollapseToggleVars": ("折叠变量", "折叠列表中所有开关变量，并退出编辑状态。"),
    "VTWW_OT_ExpandToggleVars": ("展开变量", "展开列表中所有开关变量，并进入编辑状态。"),
    "VTWW_OT_AddToggleVar": ("添加开关变量", "添加一个用于控制对象可见性的 INI 开关变量。"),
    "VTWW_OT_RemoveToggleVar": ("删除开关变量", "从 INI 开关列表中删除该变量。"),
    "VTWW_OT_MoveToggleVar": ("移动开关变量", "调整该开关变量在列表中的顺序。"),
    "VTWW_OT_EditToggleVar": ("编辑变量", "配置变量热键和默认状态。"),
    "VTWW_OT_AddToggleVarState": ("添加状态", "为该变量添加一个新状态；每个状态可控制多个对象。"),
    "VTWW_OT_RemoveToggleVarState": ("删除状态", "从该 INI 开关变量中删除当前状态。"),
    "VTWW_OT_MoveToggleVarState": ("移动状态", "调整当前状态在变量状态列表中的顺序。"),
    "VTWW_OT_AddToggleVarStateObject": ("添加状态对象", "把对象加入当前状态，使它按该状态条件显示。"),
    "VTWW_OT_RemoveToggleVarStateObject": ("删除状态对象", "从当前状态中移除该对象。"),
    "VTWW_OT_EditVarStateObject": ("编辑条件", "打开当前对象的自定义显示条件窗口。"),
    "VTWW_OT_AddToggleVarStateObjectCondition": ("添加条件", "添加一个新的自定义显示条件。"),
    "VTWW_OT_RemoveToggleVarStateObjectCondition": ("删除条件", "删除当前自定义显示条件。"),
    "VTWW_OpenIniTogglesImportExportEditor": (
        "打开 INI 开关导入导出",
        "打开文本编辑器窗口，用于导入或导出 INI 开关变量。",
    ),
    "VTWW_ExportIniToggles": ("导出 INI 开关", "把当前 INI 开关变量导出为可再次导入的 JSON 文本。"),
    "VTWW_ImportIniToggles": ("导入 INI 开关", "从当前文本文件中的 JSON 导入 INI 开关变量。"),
}

# Ini Toggles PropertyGroup texts, keyed by class name then property identifier.
INI_TOGGLE_PROPERTY_TEXTS = {
    "IniToggles": {
        "replace_vars_on_import": (
            "导入时替换同名变量",
            "导入 INI 开关时，用导入内容替换已有同名变量；关闭时跳过重复项。",
        ),
        "clear_vars_on_import": ("导入前清空变量", "导入 INI 开关前先删除当前已有的全部变量。"),
        "hide_empty_states": ("隐藏无对象的 -1 状态", "隐藏没有对象的 -1 空状态，减少 UI 占用空间。"),
        "hide_default_conditions": ("隐藏默认条件", "隐藏自动生成的默认条件，减少 UI 占用空间。"),
    },
    "ToggleVar": {
        "default_state": ("默认状态", "该开关变量在初始化时使用的状态值。"),
        "hotkeys": (
            "热键",
            "用于在多个状态之间切换的按键；组合键用空格分隔，多组热键用分号分隔。",
        ),
        "ui_expanded": ("展开变量", "进入或退出该变量的编辑状态。"),
    },
    "ToggleVarStateCondition": {
        "logic": ("逻辑", "控制多个条件如何共同影响对象显示。"),
        "type": ("类型", "选择绑定已有 INI 开关变量，或绑定自定义 INI 变量。"),
        "var": (
            "变量",
            "要与状态值比较的变量。自定义变量名前加 $ 可避免自动格式化为 $swapvar_name。",
        ),
        "operator": ("比较", "控制变量和值如何比较后才算条件成立。"),
        "state": ("状态值", "用于和变量进行比较的状态值。"),
    },
    "ToggleVarStateObject": {
        "object": ("对象", "在默认条件下，所选对象只会在变量等于当前状态值时显示。"),
    },
}
