# Settings for the Velo raw-mesh tool (own PropertyGroup, isolated from
# VTWW_Settings). Labels are Simplified Chinese; technical terms (Hash/IB/VB/
# Component/MERGED...) stay English, per the WWMI-area l10n convention.

import bpy


class VELO_RawMesh_Settings(bpy.types.PropertyGroup):
    tool_mode: bpy.props.EnumProperty(
        name="模式",
        items=[
            ('EXTRACT', "提取", "按 Hash 从 Dump 提取特效/场景网格到整合文件夹"),
            ('IMPORT', "导入", "把整合文件夹导入 Blender 编辑（保留全部顶点属性）"),
            ('EXPORT', "导出", "把编辑后的网格导出为可用 mod（per-component）"),
        ],
        default='EXTRACT',
    )

    # --- Extract ---
    frame_dump_folder: bpy.props.StringProperty(
        name="Frame Dump 目录",
        description="包含 Frame Dump 文件和 log.txt 的目录",
        default='', subtype='DIR_PATH',
    )
    output_folder: bpy.props.StringProperty(
        name="输出目录",
        description="提取出的整合网格文件夹的父目录",
        default='', subtype='DIR_PATH',
    )
    hashes: bpy.props.StringProperty(
        name="Hash 列表",
        description=("要提取的 IB/VB Hash，用逗号分隔（如 vb0=358cdfe4, ib=ce56ef1a）。"
                     "VB Hash 取整个 VB0 对象（自动分 component）；IB Hash 取它锁定的那一个 component"),
        default='',
    )
    folder_name: bpy.props.StringProperty(
        name="文件夹名",
        description="输出整合文件夹的名字（留空则按首个 VB0 Hash 自动命名）",
        default='',
    )
    position_override: bpy.props.StringProperty(
        name="Position 元素",
        description=("可选：手动指定哪个顶点元素作为 Position（如 ATTRIBUTE0）。"
                     "留空则自动判定（slot0/offset0 的 3/4 分量 float）"),
        default='',
    )
    skip_jpg: bpy.props.BoolProperty(
        name="跳过 .jpg 贴图", description="提取时忽略 .jpg 扩展名的贴图", default=False,
    )
    skip_small: bpy.props.BoolProperty(
        name="跳过小贴图", description="提取时忽略小于指定大小的贴图", default=False,
    )
    skip_small_kb: bpy.props.IntProperty(
        name="小贴图阈值 (KB)", description="低于该 KB 的贴图被跳过", default=256, min=0,
    )

    # --- Import (Phase 2) ---
    import_folder: bpy.props.StringProperty(
        name="整合文件夹",
        description="要导入的、由本工具提取出的整合网格文件夹",
        default='', subtype='DIR_PATH',
    )

    # --- Export (Phase 3) ---
    export_collection: bpy.props.PointerProperty(
        name="导出集合",
        description="包含本工具导入的 raw-mesh 对象的集合",
        type=bpy.types.Collection,
    )
    mod_output_folder: bpy.props.StringProperty(
        name="Mod 输出目录",
        description="导出生成的 mod 文件夹",
        default='', subtype='DIR_PATH',
    )
    export_mode: bpy.props.EnumProperty(
        name="导出模式",
        items=[
            ('AUTO', "自动", "未改拓扑走 Faithful（字节保真），改了拓扑走 Rebuild"),
            ('FAITHFUL', "保真直通", "原始字节直通；仅回写编辑过的 Position（不可改拓扑）"),
            ('REBUILD', "重建", "按布局重建；标准语义从 Blender 取，其余重算/填默认（可改拓扑，有损）"),
        ],
        default='AUTO',
    )


def register():
    bpy.utils.register_class(VELO_RawMesh_Settings)
    bpy.types.Scene.velo_raw_mesh_settings = bpy.props.PointerProperty(type=VELO_RawMesh_Settings)


def unregister():
    if hasattr(bpy.types.Scene, 'velo_raw_mesh_settings'):
        try:
            del bpy.types.Scene.velo_raw_mesh_settings
        except Exception:
            pass
    try:
        bpy.utils.unregister_class(VELO_RawMesh_Settings)
    except Exception:
        pass
