"""WWMI driver 层挂载：抽取时额外产出一份完整的 ``ShaderTextureUsage.json``。

键路径： ``"Component {id}" -> "vs=<hash>-ps=<hash>" -> "ps-tN" -> "<texture hash>"`` 。

背景（bug）：vendored WWMI-Tools 1.7.3 的
``_wwmi_core/extract_frame_data/component_builder.py`` 中 ``MeshObject.build_component``
按 ``get_slot_hash()`` 建贴图字典，会把同一 component 内「同 (slot, 贴图hash)、但来自不同
(vs, ps) shader pair」的描述符塌缩成一条，丢失第二个 pair 的 slot→贴图归属（贴图文件不丢，
丢的是 pair 归属）。现有 ``TextureUsage.json`` 因此对多-pair 槽位不完整，且根本没有产出
``ShaderTextureUsage.json``。

修法（Approach C，挂载式、绝不改 ``_wwmi_core``）：
1. 捕获：包装 ``MeshObject.build_components``，在原函数跑完后把每个 component 的**完整** per-draw
   贴图描述符列表（``draw_data.textures``，塌缩前）存进侧信道 ``_CAPTURE``，键为 ``vb0_hash``
   （= 输出文件夹名 = ``write_objects`` 里的 ``object_hash``，是 build 期↔write 期唯一稳定关联键）。
   **不改 ``component.textures``** → 原管线（过滤、文件抽取、TextureUsage.json）逐字节不变。
2. 产出：包装模块级 ``write_objects``（``extract_frame_data`` 内由裸全局名调用，改写模块属性即生效），
   先调原函数（照常写全部产物），再以**与原函数完全一致**的 object_name/目录/缺-shapekey 跳过逻辑
   遍历，逐 component 用「原管线过滤后的存活 slot_hash 集」作为收录判据，从侧信道里取回所有同
   slot_hash 的描述符并按 (vs,ps) pair 分组，写出 ``ShaderTextureUsage.json``。

scope 边界：
- 修复「同一 component 内 同 (slot,hash) 不同 (vs,ps) pair」的 per-pair 丢失（bug 本体）。
- **不**恢复「同 slot 不同 hash」被上游字典在塌缩处丢掉的次要 pair —— 原 TextureUsage.json 与
  文件抽取同样不含它，强行恢复需重跑过滤、反而会改变被抽取的贴图文件（回归），故不做。

幂等可逆：``_INSTALLED`` 守卫；``uninstall_patches()`` 复原两处原函数并清空侧信道。
"""

import json
from collections import OrderedDict
from pathlib import Path

from ._wwmi_core.extract_frame_data import component_builder as _cb_module
from ._wwmi_core.extract_frame_data import extract_frame_data as _efd_module
from ._wwmi_core.migoto_io.dump_parser.filename_parser import ShaderType

_INSTALLED = False
_ORIG_BUILD_COMPONENTS = None
_ORIG_WRITE_OBJECTS = None
# vb0_hash -> 按 component 顺序排列的「完整描述符 list」列表（component_id 即下标）
_CAPTURE = {}


def _pair_key(descriptor):
    """从描述符的 shader 引用里取出 (vs, ps) 组成稳定的 pair 键 ``"vs=<hash>-ps=<hash>"``。

    按 ``ShaderRef.type`` 选取（不靠 ``.shaders`` 的解析顺序，该顺序未被强制）；某一级缺失时回退
    为 ``vs=?`` / ``ps=?``，保证不抛 ``StopIteration`` 且键自描述、可排序。
    """
    vs = next((s for s in descriptor.shaders if s.type is ShaderType.Vertex), None)
    ps = next((s for s in descriptor.shaders if s.type is ShaderType.Pixel), None)
    vs_part = vs.raw if vs is not None else "vs=?"
    ps_part = ps.raw if ps is not None else "ps=?"
    return f"{vs_part}-{ps_part}"


def _wrapped_build_components(self, vb_layout, shapekeys):
    """捕获：原函数跑完后把每个 component 的完整 per-draw 贴图描述符存入侧信道。"""
    _ORIG_BUILD_COMPONENTS(self, vb_layout, shapekeys)
    # 原函数内 verify() 已设 self.vb0_hash，且 components_data 已按 vertex_offset 排序，
    # 与 self.components / 后续 ComponentData / component_id 同序。覆盖写：每轮 build 先于 write，天然新鲜。
    _CAPTURE[self.vb0_hash] = [list(cd.draw_data.textures) for cd in self.components_data]


def _wrapped_write_objects(output_directory, objects, allow_missing_shapekeys=False):
    """产出：原函数照常写全部产物后，再额外写 ShaderTextureUsage.json（不改原产物）。"""
    _ORIG_WRITE_OBJECTS(output_directory, objects, allow_missing_shapekeys)
    try:
        output_directory = Path(output_directory)
        for object_hash, object_data in objects.items():
            # 与原 write_objects 完全一致的 object_name / 缺-shapekey 跳过逻辑。
            object_name = object_hash
            if object_data.shapekeys.offsets_hash and not object_data.shapekeys.shapekey_offsets:
                if allow_missing_shapekeys:
                    object_name += '_MISSING_SHAPEKEYS'
                else:
                    continue

            object_directory = output_directory / object_name
            per_object = _CAPTURE.get(object_hash)

            shader_texture_usage = OrderedDict()
            for component_id, component in enumerate(object_data.components):
                # 原管线过滤后存活的 slot_hash（贴图文件该不该收录，完全由原管线决定）。
                surviving = {t.get_slot_hash() for t in component.textures}
                full = per_object[component_id] if (per_object and component_id < len(per_object)) else []

                pairs = {}
                for desc in full:
                    if desc.get_slot_hash() not in surviving:
                        continue
                    # 单 draw call 一个 ps-tN 只绑一张贴图 → (pair, slot) 唯一一条，值用标量 hash。
                    pairs.setdefault(_pair_key(desc), {})[desc.get_slot()] = desc.hash

                # 确定性排序：pair 键排序、slot 键排序，便于 diff。
                component_out = OrderedDict()
                for pair in sorted(pairs):
                    component_out[pair] = OrderedDict(sorted(pairs[pair].items()))
                shader_texture_usage[f'Component {component_id}'] = component_out

            with open(object_directory / 'ShaderTextureUsage.json', "w") as f:
                f.write(json.dumps(shader_texture_usage, indent=4))
    finally:
        # 约束侧信道生命周期（兼防跨轮残留）。下一轮 build 会重新填充。
        _CAPTURE.clear()


def install_patches():
    global _INSTALLED, _ORIG_BUILD_COMPONENTS, _ORIG_WRITE_OBJECTS
    if _INSTALLED:
        return
    _ORIG_BUILD_COMPONENTS = _cb_module.MeshObject.build_components
    _ORIG_WRITE_OBJECTS = _efd_module.write_objects
    _cb_module.MeshObject.build_components = _wrapped_build_components
    _efd_module.write_objects = _wrapped_write_objects
    _INSTALLED = True


def uninstall_patches():
    global _INSTALLED, _ORIG_BUILD_COMPONENTS, _ORIG_WRITE_OBJECTS
    if not _INSTALLED:
        return
    try:
        if _ORIG_BUILD_COMPONENTS is not None:
            _cb_module.MeshObject.build_components = _ORIG_BUILD_COMPONENTS
        if _ORIG_WRITE_OBJECTS is not None:
            _efd_module.write_objects = _ORIG_WRITE_OBJECTS
    finally:
        _ORIG_BUILD_COMPONENTS = None
        _ORIG_WRITE_OBJECTS = None
        _CAPTURE.clear()
        _INSTALLED = False
