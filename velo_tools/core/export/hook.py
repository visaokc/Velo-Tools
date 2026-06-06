"""V0.1.6: vendored EFMI 导出算子 monkey-patch。

设计目标：用户点击各自插件原本的「导出 Mod」按钮时，自动完成 MMD 源物体
的预处理（改名 + 删特殊 + 删空 VG），但**不修改源数据**。

实现：仅当 MMD 源物体本来就在当前导出适配器选中的 `component_collection`
范围内时，才克隆该物体（独立 mesh datablock）并临时替代源参与导出；finally
把源重新 link 回原集合，删除克隆物体与克隆 mesh。
"""
from __future__ import annotations

import json
import re
import sys
import traceback
from pathlib import Path

import bpy

from . import preexport as _pe


# {id(cls): (cls, orig_execute, src_label)}
_PATCHED = {}

_COMPONENT_PATTERN = re.compile(r'.*component[_ -]*(\d+).*', re.IGNORECASE)


def _find_class(class_name: str):
    """在 sys.modules 中找出 bpy.types.Operator 子类。"""
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        cls = getattr(mod, class_name, None)
        if cls is None or not isinstance(cls, type):
            continue
        if not hasattr(cls, "execute"):
            continue
        return cls
    return None


def _get_collections_holding(obj):
    """返回所有直接包含 obj 的 bpy.data.collections，外加 scene collections。"""
    cols = []
    for c in bpy.data.collections:
        try:
            if obj.name in c.objects and c.objects[obj.name] is obj:
                cols.append(c)
        except Exception:
            pass
    for scene in bpy.data.scenes:
        sc = scene.collection
        try:
            if obj.name in sc.objects and sc.objects[obj.name] is obj:
                cols.append(sc)
        except Exception:
            pass
    return cols


def _collection_is_visible_in_view_layer(collection, context=None):
    if collection is None:
        return False
    context = context or bpy.context

    def search(layer_collection):
        if layer_collection.collection == collection:
            return not layer_collection.exclude and not layer_collection.hide_viewport
        for child in layer_collection.children:
            result = search(child)
            if result is not None:
                return result
        return None

    return bool(search(context.view_layer.layer_collection))


def _collection_is_descendant_or_same(root, collection):
    if root is None or collection is None:
        return False
    if root == collection:
        return True
    try:
        return collection in getattr(root, "children_recursive", ())
    except Exception:
        return False


def _object_is_in_export_scope(obj, root_collection):
    if obj is None or root_collection is None:
        return False
    return any(
        _collection_is_descendant_or_same(root_collection, collection)
        for collection in getattr(obj, "users_collection", ())
    )


def _iter_export_meshes(context, cfg):
    root = getattr(cfg, "component_collection", None) if cfg is not None else None
    if root is None:
        return []

    recursive = not bool(getattr(cfg, "ignore_nested_collections", False))
    ignore_hidden_objects = bool(getattr(cfg, "ignore_hidden_objects", False))
    ignore_hidden_collections = bool(getattr(cfg, "ignore_hidden_collections", False))

    objects = root.all_objects if recursive else root.objects
    meshes = []
    for obj in objects:
        if getattr(obj, "type", None) != 'MESH':
            continue
        if obj.name.startswith('TEMP_'):
            continue
        if ignore_hidden_objects and bool(getattr(obj, "hide_get", None) and obj.hide_get()):
            continue
        scoped_collections = [
            collection
            for collection in getattr(obj, "users_collection", ())
            if _collection_is_descendant_or_same(root, collection)
        ]
        if not scoped_collections:
            continue
        if ignore_hidden_collections and not any(
            _collection_is_visible_in_view_layer(collection, context)
            for collection in scoped_collections
        ):
            continue
        meshes.append(obj)
    return meshes


def _collect_export_component_ids(context, cfg):
    component_ids = set()
    for obj in _iter_export_meshes(context, cfg):
        match = _COMPONENT_PATTERN.findall((obj.name or '').lower())
        if not match:
            continue
        component_ids.add(int(match[0]))
    return sorted(component_ids)


def _validate_merged_export_preconditions(context, settings_attr: str):
    cfg = getattr(context.scene, settings_attr, None)
    if cfg is None or getattr(cfg, "mod_skeleton_type", None) != 'MERGED':
        return None

    export_component_ids = _collect_export_component_ids(context, cfg)
    if not export_component_ids:
        return None

    source_folder = getattr(cfg, "object_source_folder", "") or ""
    if not source_folder:
        return None

    source_path = Path(bpy.path.abspath(source_folder))
    metadata_path = source_path / 'Metadata.json'
    vgmap_path = source_path / 'VertexGroupMap.json'
    if not metadata_path.is_file():
        return None

    try:
        payload = json.loads(metadata_path.read_text(encoding='utf-8'))
    except Exception:
        return None

    try:
        vg_payload = json.loads(vgmap_path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        labels = ', '.join(f'C{component_id}' for component_id in export_component_ids)
        return (
            f"\u5f53\u524d\u5bfc\u51fa\u6a21\u5f0f\u4e3a Merged\uff0c\u4f46\u6e90\u6587\u4ef6\u5939\u7f3a\u5c11 VertexGroupMap.json\uff0c\u65e0\u6cd5\u6821\u9a8c {labels} \u7684\u7edf\u4e00\u9876\u70b9\u7ec4\u6620\u5c04\u3002"
            "\u8bf7\u91cd\u65b0\u63d0\u53d6\u3001\u6267\u884c\u663e\u5f0f\u65e7 Metadata \u8f6c\u6362\uff0c\u6216\u6539\u7528 Per-Component \u5bfc\u51fa\u3002"
        )
    except Exception as exc:
        return f"\u8bfb\u53d6 VertexGroupMap.json \u5931\u8d25\uff1a{exc}"

    components = payload.get('components') or []
    vg_components = vg_payload.get('components') or []
    if len(vg_components) != len(components):
        return (
            f"VertexGroupMap.json \u7684\u7ec4\u4ef6\u6570\u91cf ({len(vg_components)}) \u4e0e Metadata.json ({len(components)}) \u4e0d\u4e00\u81f4\u3002"
            "\u8bf7\u91cd\u65b0\u751f\u6210 VertexGroupMap.json\u3002"
        )

    try:
        from ...games.arknights_endfield import cpuposed_compat as _cpuposed_compat

        invalid_components = _cpuposed_compat.empty_vgmap_components_requiring_geometry(
            components,
            vg_components,
            export_component_ids,
        )
    except Exception:
        invalid_components = []
        for component_id in export_component_ids:
            if component_id < 0 or component_id >= len(vg_components):
                continue
            component = vg_components[component_id] or {}
            vg_map = component.get('vg_map') if isinstance(component, dict) else None
            metadata_component = components[component_id] if component_id < len(components) else {}
            cpu_posed = bool(metadata_component.get('cpu_posed', False)) if isinstance(metadata_component, dict) else False
            if not vg_map and not cpu_posed:
                invalid_components.append(component_id)

    if not invalid_components:
        return None

    labels = ', '.join(f'C{component_id}' for component_id in invalid_components)
    return (
        f"\u5f53\u524d\u5bfc\u51fa\u6a21\u5f0f\u4e3a Merged\uff0c\u4f46\u6e90\u6587\u4ef6\u5939 VertexGroupMap.json \u4e2d {labels} \u7684 vg_map \u4e3a\u7a7a\u3002"
        "\u8bf7\u91cd\u65b0\u751f\u6210 VertexGroupMap.json\uff0c\u6216\u6539\u7528 Per-Component \u5bfc\u51fa\u3002"
    )


def _get_export_state(context, settings_attr: str):
    """构造 swap 状态：返回 None 表示无需处理。"""
    ef = getattr(context.scene, "velo_endfield", None)
    if ef is None:
        return None
    obj = getattr(ef, "mmd_source_object", None)
    if obj is None or obj.type != 'MESH':
        return None
    profile = getattr(ef, "mmd_profile", None)

    cfg = getattr(context.scene, settings_attr, None)
    target_col = getattr(cfg, "component_collection", None) if cfg is not None else None
    ignore_hidden_objects = bool(getattr(cfg, "ignore_hidden_objects", False)) if cfg is not None else False
    ignore_hidden_collections = bool(getattr(cfg, "ignore_hidden_collections", False)) if cfg is not None else False

    if target_col is None or not _object_is_in_export_scope(obj, target_col):
        return None

    if ignore_hidden_objects and bool(getattr(obj, "hide_get", None) and obj.hide_get()):
        return None

    cols = _get_collections_holding(obj)
    if not cols:
        return None

    export_cols = [c for c in cols if _collection_is_descendant_or_same(target_col, c)]
    if not export_cols:
        return None

    if ignore_hidden_collections:
        export_cols = [c for c in export_cols if _collection_is_visible_in_view_layer(c, context)]
        if not export_cols:
            return None

    # 克隆物体 + 独立 mesh
    clone = obj.copy()
    clone.data = obj.data.copy()
    clone.name = f"{obj.name}__velo_export"
    clone.data.name = f"{obj.data.name}__velo_export"

    # 把克隆 link 到与源相同的所有集合（保持父子组织一致）
    linked_to = []
    for c in export_cols:
        try:
            if clone.name not in c.objects:
                c.objects.link(clone)
                linked_to.append(c)
        except Exception:
            traceback.print_exc()

    # 把源从所有集合 unlink（让导出器看不到源，只看到克隆）
    unlinked_from = []
    for c in export_cols:
        try:
            if obj.name in c.objects:
                c.objects.unlink(obj)
                unlinked_from.append(c)
        except Exception:
            traceback.print_exc()

    # 在克隆上跑预处理
    try:
        _pe.apply_mmd_pre_export(clone, profile)
    except Exception:
        traceback.print_exc()

    return {
        "orig": obj,
        "clone": clone,
        "linked_to": linked_to,
        "unlinked_from": unlinked_from,
    }


def _restore_export_state(state):
    if not state:
        return
    obj = state["orig"]
    clone = state["clone"]
    # 把源 re-link 回原集合
    for c in state["unlinked_from"]:
        try:
            if obj.name not in c.objects:
                c.objects.link(obj)
        except Exception:
            traceback.print_exc()
    # 解除克隆链接 + 删除克隆 + 克隆 mesh
    for c in state["linked_to"]:
        try:
            if clone.name in c.objects:
                c.objects.unlink(clone)
        except Exception:
            pass
    try:
        mesh = clone.data
        bpy.data.objects.remove(clone, do_unlink=True)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    except Exception:
        traceback.print_exc()


def _make_patched_execute(orig_execute, settings_attr: str):
    def patched(self, context):
        state = None
        mesh_state = None
        validation_error = _validate_merged_export_preconditions(context, settings_attr)
        if validation_error:
            print(f"[velo.export-hook] {validation_error}")
            try:
                self.report({'ERROR'}, validation_error)
            except Exception:
                pass
            return {'CANCELLED'}
        try:
            state = _get_export_state(context, settings_attr)
        except Exception:
            traceback.print_exc()
        try:
            from ...mesh import operators as _mesh_ops

            mesh_state = _mesh_ops.prepare_material_route_export(context)
        except Exception:
            traceback.print_exc()
        try:
            return orig_execute(self, context)
        finally:
            try:
                from ...mesh import operators as _mesh_ops

                _mesh_ops.restore_material_route_export(mesh_state)
            except Exception:
                traceback.print_exc()
            try:
                _restore_export_state(state)
            except Exception:
                traceback.print_exc()

    return patched


def install_export_hook():
    # 目标从游戏注册表推导（单一真相源），每个游戏一条：导出算子类名 + 其 settings 属性。
    # 幂等：已 patch 的算子类跳过。各 game 驱动注册描述符后调用本函数即可接入。
    try:
        from ...games import registry as _registry
        targets = [(d.export_op_class, d.settings_attr, d.adapter_key) for d in _registry.all_descriptors()]
    except Exception:
        targets = []
    for class_name, settings_attr, key in targets:
        cls = _find_class(class_name)
        if cls is None:
            print(f"[velo.export-hook] {class_name} not found, skip ({key} not loaded)")
            continue
        if id(cls) in _PATCHED:
            continue
        _PATCHED[id(cls)] = (cls, cls.execute, class_name)
        cls.execute = _make_patched_execute(cls.execute, settings_attr)
        print(f"[velo.export-hook] patched {class_name} (cfg={settings_attr})")


def remove_export_hook():
    for cls_id, (cls, orig, label) in list(_PATCHED.items()):
        try:
            cls.execute = orig
            print(f"[velo.export-hook] restored {label}")
        except Exception:
            traceback.print_exc()
    _PATCHED.clear()
