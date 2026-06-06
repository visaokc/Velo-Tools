"""网格相关操作: 生成同名材质球 / 按贴图合并 / 按材质拆分 (含形态键清理)。

设计原则:
- 不改动顶点组匹配相关代码 (operators.py)
- 只在选中物体上工作; 没有选中物体一律拒绝执行
- 拆分后的清理仅删除 *未使用* 的材质槽和 *零位移* 的形态键, 避免误伤
- 跳过名字含 ".placeholder" 的 MMD 驱动占位网格
"""

import re

import bpy
import numpy as np
from bpy.app.handlers import persistent


# ---------------------------------------------------------------------------
# 公共
# ---------------------------------------------------------------------------

_EPS = 1e-6
_COMPONENT_PREFIX_RE = re.compile(r"^\s*component[_ -]*(\d+)(?:\s+|[_ -]+)?", re.IGNORECASE)
_COMPONENT_ANY_RE = re.compile(r"component[_ -]*(\d+)", re.IGNORECASE)
_COMPONENT_COLLECTION_RE = re.compile(r"^C(\d+)(?:\.\d+)?$", re.IGNORECASE)
_COMPONENT_COLLECTION_KEY = "velo_component_id"
_PRESERVE_EMPTY_COLLECTION_KEY = "velo_preserve_empty_collection"
_EXPORT_TEMP_SUFFIX = "__velo_export"
_ROUTE_AUTO_SIG = {}
_ROUTE_AUTO_REFRESHING = [False]


def is_real_mesh(obj):
    """是否真正可操作的网格 (排除 MMD .placeholder 驱动占位)。"""
    if obj is None or obj.type != 'MESH':
        return False
    name = obj.name or ""
    if ".placeholder" in name.lower():
        return False
    if obj.data is not None and ".placeholder" in (obj.data.name or "").lower():
        return False
    return True


def _selected_meshes(context):
    return [o for o in context.selected_objects if is_real_mesh(o)]


def _ensure_object_mode(context):
    if context.mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode='OBJECT')
        except RuntimeError:
            pass


def _safe_object_pointer(obj):
    if obj is None:
        return 0
    try:
        return int(obj.as_pointer())
    except Exception:
        return 0


def _remove_zero_user_mesh(mesh):
    if mesh is None:
        return False
    try:
        if mesh.users != 0 or bool(getattr(mesh, "use_fake_user", False)):
            return False
    except Exception:
        return False
    try:
        bpy.data.meshes.remove(mesh)
        return True
    except Exception:
        return False


def _remove_unlinked_object(obj):
    if obj is None:
        return False
    try:
        if tuple(getattr(obj, "users_collection", ())) or tuple(getattr(obj, "users_scene", ())):
            return False
    except Exception:
        return False
    mesh = None
    try:
        if getattr(obj, "type", None) == 'MESH':
            mesh = obj.data
    except Exception:
        mesh = None
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:
        return False
    _remove_zero_user_mesh(mesh)
    return True


def _claim_object_name(obj, name):
    if obj is None or not name:
        return False
    if obj.name == name:
        return True
    conflict = bpy.data.objects.get(name)
    if conflict is not None and conflict is not obj:
        _remove_unlinked_object(conflict)
    try:
        obj.name = name
    except Exception:
        return False
    return obj.name == name


def _claim_mesh_name(mesh, name):
    if mesh is None or not name:
        return False
    if mesh.name == name:
        return True
    conflict = bpy.data.meshes.get(name)
    if conflict is not None and conflict is not mesh:
        _remove_zero_user_mesh(conflict)
    try:
        mesh.name = name
    except Exception:
        return False
    return mesh.name == name


def _reserve_split_target_names(targets):
    meshes = [obj for obj in targets if getattr(obj, "type", None) == 'MESH']
    meshes.sort(key=lambda obj: obj.name)
    for index, obj in enumerate(meshes):
        try:
            obj.name = f"__velo_split_obj_{index:04d}"
        except Exception:
            pass
        data = getattr(obj, "data", None)
        if data is None:
            continue
        try:
            if data.users == 1:
                data.name = f"__velo_split_mesh_{index:04d}"
        except Exception:
            pass


def _normalize_weight_object_name(name):
    return (name or "").rstrip("+").strip()


def _find_weight_object_by_name_hint(name):
    normalized = _normalize_weight_object_name(name)
    if not normalized:
        return None
    candidates = [
        obj
        for obj in bpy.data.objects
        if is_real_mesh(obj) and _normalize_weight_object_name(obj.name) == normalized
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda obj: (obj.name != normalized, len(obj.name), obj.name))
    return candidates[0]


def _snapshot_weight_tool_objects(context):
    settings = getattr(getattr(context, "scene", None), "velo_weight_tools", None)
    if settings is None:
        return None
    source = getattr(settings, "source_object", None)
    target = getattr(settings, "target_object", None)
    return {
        "settings": settings,
        "source": {
            "object": source,
            "name": getattr(source, "name", "") if source is not None else "",
            "pointer": _safe_object_pointer(source),
        },
        "target": {
            "object": target,
            "name": getattr(target, "name", "") if target is not None else "",
            "pointer": _safe_object_pointer(target),
        },
    }


def _resolve_weight_tool_object(entry, replacement_map=None):
    replacement_map = replacement_map or {}
    replacement = replacement_map.get(entry.get("pointer", 0))
    if is_real_mesh(replacement):
        return replacement
    name = entry.get("name", "")
    if name:
        by_name = bpy.data.objects.get(name)
        if is_real_mesh(by_name):
            return by_name
        hinted = _find_weight_object_by_name_hint(name)
        if hinted is not None:
            return hinted
    original = entry.get("object")
    if is_real_mesh(original):
        return original
    return None


def _restore_weight_tool_objects(context, snapshot, *, replacement_map=None):
    if not snapshot:
        return
    settings = snapshot.get("settings")
    if settings is None:
        return
    source = _resolve_weight_tool_object(snapshot["source"], replacement_map=replacement_map)
    if source is not None and getattr(settings, "source_object", None) is not source:
        settings.source_object = source
    target = _resolve_weight_tool_object(snapshot["target"], replacement_map=replacement_map)
    if target is not None and getattr(settings, "target_object", None) is not target:
        settings.target_object = target


def _strip_component_prefix(name):
    text = (name or "").strip()
    if not text:
        return text
    stripped = _COMPONENT_PREFIX_RE.sub("", text, count=1).strip()
    return stripped or text


def is_export_temp_object(obj):
    if obj is None:
        return False
    if getattr(obj, "get", None) and obj.get("velo_export_temp"):
        return True
    return ((getattr(obj, "name", "") or "").endswith(_EXPORT_TEMP_SUFFIX))


def _mark_export_temp_object(obj, enabled=True):
    if obj is None or not getattr(obj, "get", None):
        return obj
    if enabled:
        obj["velo_export_temp"] = True
    elif "velo_export_temp" in obj:
        del obj["velo_export_temp"]
    return obj


def _with_export_temp_suffix(obj, base_name):
    base_text = (base_name or "").strip()
    if not base_text:
        return base_text
    if is_export_temp_object(obj):
        return f"{base_text}{_EXPORT_TEMP_SUFFIX}"
    return base_text


class VELO_MaterialRouteItem(bpy.types.PropertyGroup):
    component_id: bpy.props.IntProperty(default=-1)  # type: ignore
    material_name: bpy.props.StringProperty(default="")  # type: ignore
    target_collection: bpy.props.PointerProperty(type=bpy.types.Collection)  # type: ignore


class VELO_MaterialRouteCollectionState(bpy.types.PropertyGroup):
    collection_name: bpy.props.StringProperty(default="")  # type: ignore
    expanded: bpy.props.BoolProperty(default=True)  # type: ignore


class VELO_MaterialRouteSettings(bpy.types.PropertyGroup):
    active_collection: bpy.props.PointerProperty(type=bpy.types.Collection)  # type: ignore
    route_items: bpy.props.CollectionProperty(type=VELO_MaterialRouteItem)  # type: ignore
    active_route_index: bpy.props.IntProperty(default=0)  # type: ignore
    collection_states: bpy.props.CollectionProperty(type=VELO_MaterialRouteCollectionState)  # type: ignore


def _route_item_key(item):
    if item is None:
        return None
    return (int(getattr(item, "component_id", -1)), getattr(item, "material_name", ""))


def get_export_component_settings(scene):
    """当前游戏（velo_tools.active_game）的导出设置对象；经游戏注册表解析。"""
    try:
        from ..games import registry as _registry
        desc = _registry.get_active_descriptor(scene)
    except Exception:
        desc = None
    if desc is not None:
        return desc.settings(scene)
    return getattr(scene, "VTEF_settings", None)


def get_active_game_display(scene):
    """当前游戏显示名（游戏中性文案用）；回退「终末地」。"""
    try:
        from ..games import registry as _registry
        desc = _registry.get_active_descriptor(scene)
        if desc is not None:
            return desc.display_name
    except Exception:
        pass
    return "终末地"


def get_export_component_root(scene):
    cfg = get_export_component_settings(scene)
    if cfg is None:
        return None
    return getattr(cfg, "component_collection", None)


def get_material_route_settings(scene):
    return getattr(scene, "velo_material_route_tools", None)


def get_collection_state(route_settings, collection, *, create=False):
    if route_settings is None or collection is None:
        return None
    key = collection.name
    for item in route_settings.collection_states:
        if item.collection_name == key:
            return item
    if not create:
        return None
    item = route_settings.collection_states.add()
    item.collection_name = key
    item.expanded = True
    return item


def should_preserve_empty_collection(collection):
    return bool(collection is not None and getattr(collection, "get", None) and collection.get(_PRESERVE_EMPTY_COLLECTION_KEY))


def mark_preserve_empty_collection(collection, enabled=True):
    if collection is None:
        return None
    if enabled:
        collection[_PRESERVE_EMPTY_COLLECTION_KEY] = True
    elif _PRESERVE_EMPTY_COLLECTION_KEY in collection:
        del collection[_PRESERVE_EMPTY_COLLECTION_KEY]
    return collection


def _mark_component_collection(collection, component_id):
    if collection is None:
        return None
    try:
        collection[_COMPONENT_COLLECTION_KEY] = int(component_id)
    except Exception:
        pass
    return mark_preserve_empty_collection(collection)


def _find_component_collection(root, component_id):
    if root is None:
        return None
    wanted = int(component_id)
    for child in root.children:
        child_component_id = _component_id_from_collection(child)
        if child_component_id == wanted:
            return child
    return None


def _get_effective_target_collection(root, route_item, *, create=False):
    if root is None:
        return None
    target = getattr(route_item, "target_collection", None) if route_item is not None else None
    if target is not None and _collection_is_descendant(root, target):
        return target
    component_id = int(getattr(route_item, "component_id", -1)) if route_item is not None else -1
    if component_id >= 0:
        if create:
            return _ensure_component_collection(root, component_id)
        return _find_component_collection(root, component_id) or root
    return root


def get_active_material_route_item(route_settings):
    if route_settings is None or len(route_settings.route_items) == 0:
        return None
    index = int(getattr(route_settings, "active_route_index", 0))
    if 0 <= index < len(route_settings.route_items):
        return route_settings.route_items[index]
    return None


def iter_collection_virtual_items(route_settings, root, collection):
    if route_settings is None or root is None or collection is None:
        return []
    items = []
    for item in route_settings.route_items:
        if _get_effective_target_collection(root, item, create=False) == collection:
            items.append(item)
    return sorted(
        items,
        key=lambda candidate: (
            int(getattr(candidate, "component_id", -1) < 0),
            int(getattr(candidate, "component_id", -1)),
            _strip_component_prefix(getattr(candidate, "material_name", "")).lower(),
        ),
    )


def _collection_is_descendant(root, collection):
    if root is None or collection is None:
        return False
    return collection == root or collection in root.children_recursive


def _collection_is_visible_in_view_layer(collection, context=None):
    if collection is None:
        return False
    if context is None:
        context = bpy.context

    def search(layer_collection):
        if layer_collection.collection == collection:
            return (not layer_collection.exclude) and (not layer_collection.hide_viewport)
        for child in layer_collection.children:
            result = search(child)
            if result is not None:
                return result
        return None

    return bool(search(context.view_layer.layer_collection))


def _component_collection_name(component_id):
    return f"C{int(component_id)}"


def _find_direct_child_collection(parent, name):
    if parent is None:
        return None
    for child in parent.children:
        if child.name == name:
            return child
    return None


def _find_descendant_collection(root, name):
    if root is None:
        return None
    if root.name == name:
        return root
    for child in root.children_recursive:
        if child.name == name:
            return child
    return None


def _ensure_child_collection(parent, name):
    existing = _find_direct_child_collection(parent, name)
    if existing is not None:
        return mark_preserve_empty_collection(existing)
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return mark_preserve_empty_collection(collection)


def _ensure_component_collection(root, component_id):
    existing = _find_component_collection(root, component_id)
    if existing is not None:
        return _mark_component_collection(existing, component_id)
    collection = _ensure_child_collection(root, _component_collection_name(component_id))
    return _mark_component_collection(collection, component_id)


def _ensure_nested_export_enabled(scene):
    cfg = get_export_component_settings(scene)
    if cfg is not None and getattr(cfg, "ignore_nested_collections", True):
        cfg.ignore_nested_collections = False


def _sole_material_name(obj):
    if obj is None or obj.type != 'MESH' or len(obj.material_slots) != 1:
        return ""
    mat = obj.material_slots[0].material
    return (getattr(mat, "name", "") or "").strip() if mat is not None else ""


def _component_id_from_collection_name(name):
    match = _COMPONENT_COLLECTION_RE.match((name or "").strip())
    return int(match.group(1)) if match else None


def _component_id_from_collection(collection):
    if collection is None:
        return None
    raw = collection.get(_COMPONENT_COLLECTION_KEY) if getattr(collection, "get", None) else None
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return _component_id_from_collection_name(getattr(collection, "name", ""))


def _component_id_from_name(name):
    match = _COMPONENT_ANY_RE.search((name or "").strip())
    return int(match.group(1)) if match else None


def _component_id_from_object(obj, root=None):
    if obj is None:
        return None
    raw = obj.get("velo_component_id") if hasattr(obj, "get") else None
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    component_id = _component_id_from_name(obj.name or "")
    if component_id is not None:
        return component_id
    if root is not None:
        for collection in getattr(obj, "users_collection", ()):
            if not _collection_is_descendant(root, collection):
                continue
            component_id = _component_id_from_collection(collection)
            if component_id is not None:
                return component_id
    return None


def describe_material_route_item(item):
    component_id = int(getattr(item, "component_id", -1))
    material_name = _strip_component_prefix(getattr(item, "material_name", "")).strip()
    if component_id >= 0:
        return f"Component {component_id} {material_name}".strip()
    return material_name or "未识别材质"


def _collect_route_pairs(root):
    pairs = set()
    if root is None:
        return []
    for obj in sorted(root.all_objects, key=lambda candidate: candidate.name):
        if not is_real_mesh(obj):
            continue
        for slot in obj.material_slots:
            mat = getattr(slot, "material", None)
            if mat is None or not getattr(mat, "name", ""):
                continue
            component_id = _component_id_from_name(mat.name)
            if component_id is None:
                component_id = _component_id_from_object(obj, root)
            component_key = int(component_id) if component_id is not None else -1
            pairs.add((component_key, mat.name))
    return sorted(pairs, key=lambda item: (item[0] < 0, item[0], item[1].lower()))


def _collection_depth_within_root(root, collection):
    if root is None or collection is None or not _collection_is_descendant(root, collection):
        return -1
    depth = 0
    current = collection
    while current is not None and current != root:
        current = _find_parent_collection(root, current)
        if current is None:
            return -1
        depth += 1
    return depth


def _pick_object_route_collection(root, obj):
    if root is None or obj is None:
        return None
    collections = [
        collection for collection in getattr(obj, "users_collection", ())
        if _collection_is_descendant(root, collection)
    ]
    if not collections:
        return None
    if len(collections) == 1:
        return collections[0]

    depth_map = {}
    for collection in collections:
        depth_map.setdefault(_collection_depth_within_root(root, collection), []).append(collection)
    best_depth = max(depth_map)
    best = depth_map.get(best_depth, [])
    if len(best) == 1:
        return best[0]
    return None


def _actual_route_pairs_for_object(root, obj):
    if root is None or obj is None or not is_real_mesh(obj):
        return []

    materials = []
    texture_keys = []
    component_ids = []
    for slot in obj.material_slots:
        mat = getattr(slot, "material", None)
        if mat is None or not getattr(mat, "name", ""):
            continue
        materials.append(mat)
        texture_keys.append(frozenset(_image_keys_of_material(mat)))
        component_id = _component_id_from_name(mat.name)
        if component_id is None:
            component_id = _component_id_from_object(obj, root)
        component_ids.append(component_id)

    if not materials:
        return []

    if len(materials) > 1:
        if any(not key for key in texture_keys):
            return []
        normalized = {tuple(sorted(key)) for key in texture_keys}
        if len(normalized) != 1:
            return []
        normalized_components = {
            int(component_id)
            for component_id in component_ids
            if component_id is not None
        }
        if len(normalized_components) != 1 or any(component_id is None for component_id in component_ids):
            return []

    pairs = []
    seen = set()
    for mat, component_id in zip(materials, component_ids):
        key = (int(component_id) if component_id is not None else -1, mat.name)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


def _collect_actual_route_targets(root):
    actual_targets = {}
    ambiguous_pairs = set()
    if root is None:
        return actual_targets

    for obj in sorted(root.all_objects, key=lambda candidate: candidate.name):
        if not is_real_mesh(obj) or is_export_temp_object(obj):
            continue
        route_pairs = _actual_route_pairs_for_object(root, obj)
        if not route_pairs:
            continue
        target_collection = _pick_object_route_collection(root, obj)
        if target_collection is None:
            continue
        for key in route_pairs:
            existing = actual_targets.get(key)
            if existing is None:
                actual_targets[key] = target_collection
                continue
            if existing != target_collection:
                ambiguous_pairs.add(key)

    for key in ambiguous_pairs:
        actual_targets.pop(key, None)
    return actual_targets


def _route_tree_signature(root, route_pairs=None, actual_targets=None):
    if root is None:
        return None
    if route_pairs is None:
        route_pairs = _collect_route_pairs(root)
    if actual_targets is None:
        actual_targets = _collect_actual_route_targets(root)
    collection_names = tuple(sorted(collection.name for collection in root.children_recursive))
    actual_entries = tuple(
        sorted(
            (component_id, material_name, target.name)
            for (component_id, material_name), target in actual_targets.items()
            if target is not None
        )
    )
    return (root.name, collection_names, tuple(route_pairs), actual_entries)


def _route_scene_sig_key(scene):
    if scene is None:
        return None
    try:
        return scene.as_pointer()
    except Exception:
        return id(scene)


def _depsgraph_may_affect_route_tree(root, depsgraph):
    if root is None or depsgraph is None:
        return False
    for update in getattr(depsgraph, "updates", ()):
        update_id = getattr(update, "id", None)
        if update_id is None:
            continue
        if isinstance(update_id, bpy.types.Scene):
            return True
        if isinstance(update_id, bpy.types.Material):
            return True
        if isinstance(update_id, bpy.types.Collection):
            if update_id == root or _collection_is_descendant(root, update_id):
                return True
            continue
        if isinstance(update_id, bpy.types.Object):
            if is_real_mesh(update_id) and any(
                _collection_is_descendant(root, collection)
                for collection in getattr(update_id, "users_collection", ())
            ):
                return True
            continue
        if isinstance(update_id, bpy.types.Mesh):
            return True
    return False


def refresh_material_route_items(scene, *, route_pairs=None, actual_targets=None, apply_actual_targets=True):
    route_settings = get_material_route_settings(scene)
    root = get_export_component_root(scene)
    scene_key = _route_scene_sig_key(scene)
    if route_settings is None:
        return 0
    active_key = _route_item_key(get_active_material_route_item(route_settings))
    old_targets = {
        (item.component_id, item.material_name): item.target_collection
        for item in route_settings.route_items
    }
    route_settings.route_items.clear()
    route_settings.active_route_index = 0
    if root is None:
        route_settings.active_collection = None
        if scene_key is not None:
            _ROUTE_AUTO_SIG.pop(scene_key, None)
        return 0
    for collection in root.children_recursive:
        mark_preserve_empty_collection(collection)
    if route_pairs is None:
        route_pairs = _collect_route_pairs(root)
    signature_targets = actual_targets
    if apply_actual_targets:
        if actual_targets is None:
            actual_targets = _collect_actual_route_targets(root)
        signature_targets = actual_targets
    else:
        if signature_targets is None:
            signature_targets = _collect_actual_route_targets(root)
        actual_targets = {}
    active_index = 0
    for component_id, material_name in route_pairs:
        if component_id >= 0:
            _ensure_component_collection(root, component_id)
        item = route_settings.route_items.add()
        item.component_id = int(component_id)
        item.material_name = material_name
        target = actual_targets.get((component_id, material_name))
        if target is None:
            target = old_targets.get((component_id, material_name))
        if target is not None and _collection_is_descendant(root, target):
            item.target_collection = target
        if active_key == (component_id, material_name):
            active_index = len(route_settings.route_items) - 1
    if route_settings.active_collection is None or not _collection_is_descendant(root, route_settings.active_collection):
        route_settings.active_collection = root
    if len(route_settings.route_items) > 0:
        route_settings.active_route_index = active_index
    if scene_key is not None:
        _ROUTE_AUTO_SIG[scene_key] = _route_tree_signature(root, route_pairs=route_pairs, actual_targets=signature_targets)
    return len(route_settings.route_items)


@persistent
def _route_depsgraph_handler(scene, depsgraph):
    if _ROUTE_AUTO_REFRESHING[0]:
        return
    route_settings = get_material_route_settings(scene)
    root = get_export_component_root(scene)
    scene_key = _route_scene_sig_key(scene)
    if route_settings is None or root is None:
        if scene_key is not None:
            _ROUTE_AUTO_SIG.pop(scene_key, None)
        return
    if not _depsgraph_may_affect_route_tree(root, depsgraph):
        return

    route_pairs = _collect_route_pairs(root)
    actual_targets = _collect_actual_route_targets(root)
    signature = _route_tree_signature(root, route_pairs=route_pairs, actual_targets=actual_targets)
    if signature == _ROUTE_AUTO_SIG.get(scene_key):
        return

    _ROUTE_AUTO_REFRESHING[0] = True
    try:
        refresh_material_route_items(scene, route_pairs=route_pairs, actual_targets=actual_targets)
    finally:
        _ROUTE_AUTO_REFRESHING[0] = False


def _find_route_item(route_settings, component_id, material_name):
    if route_settings is None or not material_name:
        return None
    for item in route_settings.route_items:
        if item.component_id == int(component_id) and item.material_name == material_name:
            return item
    return None


def _move_object_within_root(obj, root, destination):
    if obj is None or root is None or destination is None:
        return False
    unlink_targets = [
        collection for collection in getattr(obj, "users_collection", ())
        if _collection_is_descendant(root, collection)
    ]
    if len(unlink_targets) == 1 and unlink_targets[0] == destination:
        return False
    changed = False
    for collection in unlink_targets:
        try:
            collection.objects.unlink(obj)
            changed = True
        except Exception:
            pass
    if destination not in getattr(obj, "users_collection", ()):
        destination.objects.link(obj)
        changed = True
    return changed


def _rename_to_component_material(obj, component_id):
    if obj.type != 'MESH':
        return False
    material_name = _strip_component_prefix(_sole_material_name(obj))
    if not material_name:
        return False
    if component_id is None:
        return _rename_to_sole_material(obj)
    base = _with_export_temp_suffix(obj, f"Component {int(component_id)} {material_name}")
    _claim_object_name(obj, base)
    obj["velo_component_id"] = int(component_id)
    if obj.data is not None:
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        _claim_mesh_name(obj.data, base)
    return True


def _route_meshes_to_component_sets(scene, meshes, *, refresh_after=True, preserve_object_names=False):
    route_settings = get_material_route_settings(scene)
    root = get_export_component_root(scene)
    if root is None:
        raise ValueError(f"请先在{get_active_game_display(scene)} Export Mod 中指定部件集合")
    _ensure_nested_export_enabled(scene)
    created_collections = set()
    missing_component = []
    routed_count = 0
    for obj in meshes:
        if not is_real_mesh(obj):
            continue
        material_name = _sole_material_name(obj)
        component_id = _component_id_from_name(material_name)
        if component_id is None:
            component_id = _component_id_from_object(obj, root)
        if component_id is None:
            missing_component.append(obj.name)
            continue
        destination = _ensure_component_collection(root, component_id)
        created_collections.add(destination.name)
        route_item = _find_route_item(route_settings, component_id, material_name)
        mapped_collection = _get_effective_target_collection(root, route_item, create=True)
        if mapped_collection is not None:
            destination = mapped_collection
        _move_object_within_root(obj, root, destination)
        obj["velo_component_id"] = int(component_id)
        if not (preserve_object_names and not is_export_temp_object(obj)):
            _rename_to_component_material(obj, component_id)
        routed_count += 1
    if refresh_after:
        refresh_material_route_items(scene)
    return {
        "routed_count": routed_count,
        "created_collections": len(created_collections),
        "missing_component": missing_component,
    }


def _remove_temp_object(obj):
    if obj is None:
        return
    try:
        mesh = obj.data if getattr(obj, "type", None) == 'MESH' else None
    except Exception:
        mesh = None
    try:
        _mark_export_temp_object(obj, enabled=False)
    except Exception:
        pass
    try:
        bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:
        return
    try:
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    except Exception:
        pass


def _cleanup_join_orphan_meshes(meshes):
    seen = set()
    for mesh in meshes:
        if mesh is None:
            continue
        key = id(mesh)
        if key in seen:
            continue
        seen.add(key)
        _remove_zero_user_mesh(mesh)


def prepare_material_route_export(context):
    scene = context.scene
    route_settings = get_material_route_settings(scene)
    root = get_export_component_root(scene)
    cfg = get_export_component_settings(scene)
    ignore_hidden_objects = bool(getattr(cfg, "ignore_hidden_objects", False))
    ignore_hidden_collections = bool(getattr(cfg, "ignore_hidden_collections", False))
    if route_settings is None or root is None:
        return None

    refresh_material_route_items(scene, apply_actual_targets=False)
    state = {
        "root": root,
        "original_links": [],
        "owned_clones": [],
        "created_objects": [],
    }
    working_meshes = []
    passthrough_meshes = []

    try:
        for obj in list(root.all_objects):
            if not is_real_mesh(obj):
                continue

            if ignore_hidden_objects and bool(getattr(obj, "hide_get", None) and obj.hide_get()):
                continue

            original_links = [
                collection for collection in getattr(obj, "users_collection", ())
                if _collection_is_descendant(root, collection)
            ]
            if ignore_hidden_collections:
                original_links = [
                    collection for collection in original_links
                    if _collection_is_visible_in_view_layer(collection, context)
                ]
            if not original_links:
                continue

            if is_export_temp_object(obj):
                _mark_export_temp_object(obj)
                working_meshes.append(obj)
                continue

            was_hidden = bool(getattr(obj, "hide_get", None) and obj.hide_get())
            if was_hidden and not ignore_hidden_objects:
                obj.hide_set(False)

            if len(obj.material_slots) <= 1:
                state["original_links"].append((obj, tuple(original_links), was_hidden))
                passthrough_meshes.append(obj)
                continue

            clone = obj.copy()
            if obj.data is not None:
                clone.data = obj.data.copy()
                clone.data.name = f"{obj.data.name}{_EXPORT_TEMP_SUFFIX}"
            clone.name = f"{obj.name}{_EXPORT_TEMP_SUFFIX}"
            _mark_export_temp_object(clone)
            if was_hidden and not ignore_hidden_objects:
                clone.hide_set(False)

            for collection in original_links:
                collection.objects.link(clone)
            for collection in original_links:
                collection.objects.unlink(obj)

            state["original_links"].append((obj, tuple(original_links), was_hidden))
            state["owned_clones"].append(clone)
            working_meshes.append(clone)

        if not working_meshes and not passthrough_meshes:
            return None

        targets = list(passthrough_meshes)
        if working_meshes:
            threshold = float(getattr(getattr(scene, "velo_tools", None), "shapekey_cleanup_threshold", _EPS))
            stats = _split_meshes_by_material(
                context,
                working_meshes,
                threshold=threshold,
                preserve_component_prefix=True,
            )
            split_targets = list(stats["targets"])
            for obj in split_targets:
                _mark_export_temp_object(obj)
            state["created_objects"] = [obj for obj in split_targets if obj not in working_meshes]
            targets.extend(split_targets)
        _route_meshes_to_component_sets(scene, targets, refresh_after=False, preserve_object_names=True)
        return state
    except Exception:
        restore_material_route_export(state)
        raise


def restore_material_route_export(state):
    if not state:
        return

    root = state.get("root")

    seen = set()
    for obj in list(state.get("created_objects", ())):
        key = id(obj)
        if key in seen:
            continue
        seen.add(key)
        _remove_temp_object(obj)
    for obj in list(state.get("owned_clones", ())):
        key = id(obj)
        if key in seen:
            continue
        seen.add(key)
        _remove_temp_object(obj)

    for obj, collections, was_hidden in state.get("original_links", ()): 
        for collection in list(getattr(obj, "users_collection", ())):
            if collection in collections:
                continue
            if root is not None and _collection_is_descendant(root, collection):
                try:
                    collection.objects.unlink(obj)
                except Exception:
                    pass
        for collection in collections:
            try:
                if obj.name not in collection.objects:
                    collection.objects.link(obj)
            except Exception:
                pass
        try:
            obj.hide_set(bool(was_hidden))
        except Exception:
            pass


def _find_parent_collection(root, collection):
    if root is None or collection is None or root == collection:
        return None
    stack = [root]
    while stack:
        parent = stack.pop()
        for child in parent.children:
            if child == collection:
                return parent
            stack.append(child)
    return None


def _iter_collection_tree(collection):
    if collection is None:
        return []
    items = [collection]
    for child in collection.children:
        items.extend(_iter_collection_tree(child))
    return items


def _remove_collection_tree(collection):
    if collection is None:
        return 0
    removed = 0
    for child in list(collection.children):
        removed += _remove_collection_tree(child)
    bpy.data.collections.remove(collection, do_unlink=True)
    return removed + 1


def _clear_crossib_collection_mappings(scene, removed_collections):
    settings = getattr(scene, "crossib_settings", None)
    if settings is None or len(getattr(settings, "mappings", ())) == 0:
        return 0
    removed_set = set(removed_collections)
    removed = 0
    for index in range(len(settings.mappings) - 1, -1, -1):
        mapping = settings.mappings[index]
        if getattr(mapping, "source_kind", "") != 'COLLECTION':
            continue
        if getattr(mapping, "source_collection", None) in removed_set:
            settings.mappings.remove(index)
            removed += 1
    return removed


def _image_keys_of_material(mat):
    """收集材质里 IMAGE_TEXTURE 节点的图像名集合 (用作分组键)。"""
    keys = set()
    if mat is None or not mat.use_nodes or mat.node_tree is None:
        return keys
    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image is not None:
            keys.add(node.image.name)
    return keys


def _image_keys_of_object(obj):
    """对象使用的所有材质里, 出现过的图像名 (frozenset, 用作合并分组键)。"""
    keys = set()
    for slot in obj.material_slots:
        keys |= _image_keys_of_material(slot.material)
    return frozenset(keys)


def _texture_route_key_of_object(obj):
    image_keys = tuple(sorted(_image_keys_of_object(obj)))
    if image_keys:
        return ("IMAGE",) + image_keys
    material_name = _sole_material_name(obj)
    if material_name:
        return ("MATERIAL", material_name)
    return ("OBJECT", getattr(obj, "name", ""))


def _merge_meshes_by_texture_groups(context, meshes, root):
    _ensure_object_mode(context)
    groups = {}
    for obj in sorted(meshes, key=lambda candidate: candidate.name):
        if not is_real_mesh(obj):
            continue
        destination = _pick_object_route_collection(root, obj)
        if destination is None:
            continue
        component_id = _component_id_from_object(obj, root)
        key = (
            int(component_id) if component_id is not None else -1,
            destination.name,
            _texture_route_key_of_object(obj),
        )
        groups.setdefault(key, []).append(obj)

    merged_groups = 0
    merged_objects = 0
    results = []
    replacement_map = {}
    for (component_id, _destination_name, _texture_key), group in groups.items():
        if len(group) < 2:
            results.extend(group)
            continue
        group_pointers = [_safe_object_pointer(obj) for obj in group]
        group_meshes = [getattr(obj, "data", None) for obj in group]
        bpy.ops.object.select_all(action='DESELECT')
        for obj in group:
            obj.select_set(True)
        context.view_layer.objects.active = group[0]
        try:
            bpy.ops.object.join()
        except RuntimeError:
            results.extend(group)
            continue
        _cleanup_join_orphan_meshes(group_meshes)
        merged = context.view_layer.objects.active or group[0]
        if component_id >= 0:
            merged["velo_component_id"] = int(component_id)
        for pointer in group_pointers:
            if pointer:
                replacement_map[pointer] = merged
        results.append(merged)
        merged_groups += 1
        merged_objects += len(group)

    bpy.ops.object.select_all(action='DESELECT')
    for obj in results:
        try:
            obj.select_set(True)
        except Exception:
            pass

    return {
        "targets": results,
        "merged_groups": merged_groups,
        "merged_objects": merged_objects,
        "result_count": len(results),
        "replacement_map": replacement_map,
    }


# ---------------------------------------------------------------------------
# 1) 为选中物体添加 Component 前缀
# ---------------------------------------------------------------------------

class VELO_OT_add_component_prefix(bpy.types.Operator):
    bl_idname = "velo.add_component_prefix"
    bl_label = "为选中物体添加 Component 前缀"
    bl_description = (
        "给所有选中的网格物体添加或更新 `Component N` 前缀, "
        "方便 EFMI 导出时识别部件归属"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        meshes = _selected_meshes(context)
        if not meshes:
            self.report({'WARNING'}, "请先选择至少一个网格物体")
            return {'CANCELLED'}

        settings = getattr(context.scene, "velo_tools", None)
        component_id = int(getattr(settings, "mesh_component_prefix_id", 0)) if settings else 0
        prefix = f"Component {component_id}"

        renamed = 0
        unchanged = 0
        for obj in meshes:
            base_name = _strip_component_prefix(obj.name)
            new_name = f"{prefix} {base_name}" if base_name else prefix
            if obj.name == new_name:
                unchanged += 1
                continue
            obj.name = new_name
            renamed += 1

        self.report({'INFO'}, f"完成: 改名 {renamed} 个物体, 跳过 {unchanged} 个")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# 2) 选中物体生成同名材质球
# ---------------------------------------------------------------------------

class VELO_OT_gen_material_for_selected(bpy.types.Operator):
    bl_idname = "velo.gen_material_for_selected"
    bl_label = "选中物体生成同名材质球"
    bl_description = (
        "对每个选中网格: 单用户化 mesh, 把 mesh 改名为物体名, "
        "若无材质则新建同名材质, 若已有材质且共享则单用户化并改名"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        meshes = _selected_meshes(context)
        if not meshes:
            self.report({'WARNING'}, "请先选择至少一个网格物体")
            return {'CANCELLED'}

        def _claim_name(collection, target_block, name):
            """让所选物体的数据块独占目标名字。
            如果已有同名数据块占着这个名字，先把它强制改名腾位（让位者带
            `__velo_displaced__` 前缀，避免 Blender 自动追加 `.001`）。
            返回是否成功设置（target_block.name == name）。"""
            existing = collection.get(name)
            if existing is target_block:
                return True
            if existing is not None:
                removed = False
                if isinstance(existing, bpy.types.Mesh):
                    removed = _remove_zero_user_mesh(existing)
                elif isinstance(existing, bpy.types.Material):
                    try:
                        if existing.users == 0 and not bool(getattr(existing, "use_fake_user", False)):
                            bpy.data.materials.remove(existing)
                            removed = True
                    except Exception:
                        removed = False
                if not removed:
                    existing.name = f"__velo_displaced__{name}"
            target_block.name = name
            return target_block.name == name

        processed = 0
        for obj in meshes:
            obj_name = obj.name
            # 单用户化 mesh，确保改名只影响当前物体
            if obj.data.users > 1:
                obj.data = obj.data.copy()
            _claim_name(bpy.data.meshes, obj.data, obj_name)
            mats = obj.data.materials
            if len(mats) == 0:
                # 优先复用同名材质球；没有再新建
                mat = bpy.data.materials.get(obj_name)
                if mat is None:
                    mat = bpy.data.materials.new(name=obj_name)
                    # new() 在重名时会追加 .001，所以再抢一次名字
                    _claim_name(bpy.data.materials, mat, obj_name)
                mats.append(mat)
            else:
                mat = mats[0]
                if mat.users > 1:
                    mat = mat.copy()
                    mats[0] = mat
                _claim_name(bpy.data.materials, mat, obj_name)
            processed += 1

        self.report({'INFO'}, f"完成: 处理 {processed} 个物体")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# 3) 选中物体按贴图合并
# ---------------------------------------------------------------------------

class VELO_OT_merge_by_texture(bpy.types.Operator):
    bl_idname = "velo.merge_by_texture"
    bl_label = "选中物体按贴图合并"
    bl_description = (
        "把选中网格按 (使用的图像集合) 分组, 同组合并为一个物体; "
        "材质槽保持原样不会被合并/丢弃"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return sum(1 for o in context.selected_objects if o.type == 'MESH') >= 2

    def execute(self, context):
        _ensure_object_mode(context)
        meshes = _selected_meshes(context)
        if len(meshes) < 2:
            self.report({'WARNING'}, "至少选择 2 个网格物体")
            return {'CANCELLED'}

        weight_snapshot = _snapshot_weight_tool_objects(context)

        groups = {}  # frozenset(image_names) -> [obj, ...]
        skipped = 0
        for obj in meshes:
            key = _image_keys_of_object(obj)
            if not key:
                # 没有任何图像 -> 跳过, 避免把无贴图物体一锅端
                skipped += 1
                continue
            groups.setdefault(key, []).append(obj)

        merged_groups = 0
        merged_objs = 0
        replacement_map = {}
        for key, group in groups.items():
            if len(group) < 2:
                continue
            group_pointers = [_safe_object_pointer(obj) for obj in group]
            group_meshes = [getattr(obj, "data", None) for obj in group]
            # 选中本组并设置 active
            bpy.ops.object.select_all(action='DESELECT')
            for o in group:
                o.select_set(True)
            context.view_layer.objects.active = group[0]
            try:
                bpy.ops.object.join()
            except RuntimeError as e:
                self.report({'WARNING'}, f"合并失败 (图像 {sorted(key)}): {e}")
                continue
            _cleanup_join_orphan_meshes(group_meshes)
            merged = context.view_layer.objects.active or group[0]
            for pointer in group_pointers:
                if pointer:
                    replacement_map[pointer] = merged
            merged_groups += 1
            merged_objs += len(group)

        _restore_weight_tool_objects(context, weight_snapshot, replacement_map=replacement_map)

        msg = f"按贴图合并完成: {merged_groups} 组 / {merged_objs} 个物体"
        if skipped:
            msg += f"; 跳过无贴图 {skipped} 个"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# 4) 按材质拆分网格 (拆分后单材质 + 清理零位移形态键)
# ---------------------------------------------------------------------------

def _used_material_indices(obj):
    used = set()
    if obj.type != 'MESH':
        return used
    n = len(obj.data.polygons)
    if n == 0:
        return used
    arr = np.empty(n, dtype=np.int32)
    obj.data.polygons.foreach_get("material_index", arr)
    used.update(int(x) for x in np.unique(arr))
    return used


def _trim_to_used_material(obj):
    """删除未使用的材质槽 (直接 API, 不走 ops, 避免撤销栈/上下文切换)。"""
    if obj.type != 'MESH':
        return 0
    used = _used_material_indices(obj)
    n = len(obj.material_slots)
    if not used or n <= 1:
        return 0
    removed = 0
    for i in range(n - 1, -1, -1):
        if i not in used:
            try:
                obj.data.materials.pop(index=i)
                removed += 1
            except Exception:
                pass
    return removed


def _clean_unused_shape_keys(obj, threshold=_EPS):
    """删除所有 *最大位移 ≤ threshold* 的形态键 (Basis 不动)。

    threshold 单位为物体局部坐标 (米)。骨骼烘焙到形态键常残留 1e-4 量级的微位移,
    Blender 自带的"清理形态键"只删严格为 0 的, 这里允许放宽阈值兜底清理。
    使用 numpy + foreach_get 批量计算, 直接调用 obj.shape_key_remove (无 ops 开销)。
    """
    if obj.type != 'MESH':
        return 0
    sk = obj.data.shape_keys
    if not sk or len(sk.key_blocks) <= 1:
        return 0
    n = len(sk.key_blocks[0].data)
    if n == 0:
        return 0
    base_co = np.empty(n * 3, dtype=np.float32)
    sk.key_blocks[0].data.foreach_get("co", base_co)
    eps2 = float(threshold) * float(threshold)
    to_remove = []
    for kb in list(sk.key_blocks)[1:]:
        co = np.empty(n * 3, dtype=np.float32)
        kb.data.foreach_get("co", co)
        diff = co - base_co
        # 每个顶点 (dx,dy,dz) 的平方距离, 取最大值
        d2 = (diff * diff).reshape(-1, 3).sum(axis=1).max()
        if d2 <= eps2:
            to_remove.append(kb)
    for kb in to_remove:
        try:
            obj.shape_key_remove(kb)
        except Exception:
            pass
    return len(to_remove)


def _rename_to_sole_material(obj):
    """若对象只剩一个材质槽, 则把物体名 / mesh 名 / 材质名统一为该材质名。

    返回 True 表示进行了改名。与"选中物体生成同名材质球"按钮的同步规则一致,
    只是这里以 *材质名* 为基准 (拆分后每个子物体唯一对应一种材质)。
    """
    if obj.type != 'MESH':
        return False
    slots = obj.material_slots
    if len(slots) != 1:
        return False
    mat = slots[0].material
    if mat is None:
        return False
    base = _with_export_temp_suffix(obj, mat.name)
    _claim_object_name(obj, base)
    # mesh 数据若被多对象共享则单用户化, 再改名
    if obj.data is not None:
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        _claim_mesh_name(obj.data, base)
    return True


def _split_meshes_by_material(context, sources, *, threshold, preserve_component_prefix=False):
    _ensure_object_mode(context)
    before_objs = set(context.scene.objects)
    split_sources = 0
    skipped_single = 0
    for src in sources:
        if len(src.material_slots) <= 1:
            skipped_single += 1
            continue
        bpy.ops.object.select_all(action='DESELECT')
        src.select_set(True)
        context.view_layer.objects.active = src
        try:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.separate(type='MATERIAL')
            split_sources += 1
        finally:
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError:
                pass

    targets = set(sources)
    for obj in context.scene.objects:
        if obj not in before_objs and is_real_mesh(obj):
            targets.add(obj)

    cleaned_slots = 0
    cleaned_keys = 0
    renamed = 0
    _reserve_split_target_names(targets)
    for obj in targets:
        if obj.type != 'MESH':
            continue
        cleaned_slots += _trim_to_used_material(obj)
        cleaned_keys += _clean_unused_shape_keys(obj, threshold=threshold)
        sk = obj.data.shape_keys if obj.data else None
        if sk and len(sk.key_blocks) > 1:
            try:
                ctx_override = {
                    'object': obj, 'active_object': obj,
                    'selected_objects': [obj], 'selected_editable_objects': [obj],
                }
                if hasattr(bpy.ops.object, "shape_key_clean"):
                    bpy.ops.object.shape_key_clean(ctx_override)
            except Exception:
                pass
        sk = obj.data.shape_keys if obj.data else None
        if sk and len(sk.key_blocks) <= 1:
            try:
                obj.shape_key_clear()
                cleaned_keys += 1
            except Exception:
                pass
        if preserve_component_prefix:
            if _rename_to_component_material(obj, _component_id_from_object(obj)):
                renamed += 1
        else:
            if _rename_to_sole_material(obj):
                renamed += 1

    bpy.ops.object.select_all(action='DESELECT')
    for obj in targets:
        try:
            obj.select_set(True)
        except Exception:
            pass

    return {
        "targets": targets,
        "cleaned_slots": cleaned_slots,
        "cleaned_keys": cleaned_keys,
        "renamed": renamed,
        "split_sources": split_sources,
        "skipped_single": skipped_single,
    }


class VELO_OT_route_refresh_materials(bpy.types.Operator):
    bl_idname = "velo.route_refresh_materials"
    bl_label = "刷新材质分组"
    bl_description = "重新扫描当前终末地导出部件集合里的材质名；若场景里已有拆分后的单材质部件，也会按它们当前所在真实集合同步虚拟材质分组树"
    bl_options = {'REGISTER'}

    def execute(self, context):
        count = refresh_material_route_items(context.scene)
        self.report({'INFO'}, f"已同步 {count} 个材质分组")
        return {'FINISHED'}


class VELO_OT_route_set_active_collection(bpy.types.Operator):
    bl_idname = "velo.route_set_active_collection"
    bl_label = "选择集合"
    bl_description = "将该集合设为材质归纳面板的当前集合"
    bl_options = {'INTERNAL'}

    collection_name: bpy.props.StringProperty(default="")  # type: ignore

    def execute(self, context):
        root = get_export_component_root(context.scene)
        route_settings = get_material_route_settings(context.scene)
        if root is None or route_settings is None:
            return {'CANCELLED'}
        collection = _find_descendant_collection(root, self.collection_name)
        if collection is None:
            return {'CANCELLED'}
        route_settings.active_collection = collection
        return {'FINISHED'}


class VELO_OT_route_select_material_item(bpy.types.Operator):
    bl_idname = "velo.route_select_material_item"
    bl_label = "选择材质分组"
    bl_options = {'INTERNAL'}

    item_index: bpy.props.IntProperty(default=-1)  # type: ignore

    def execute(self, context):
        root = get_export_component_root(context.scene)
        route_settings = get_material_route_settings(context.scene)
        if root is None or route_settings is None:
            return {'CANCELLED'}
        if not (0 <= self.item_index < len(route_settings.route_items)):
            return {'CANCELLED'}
        route_settings.active_route_index = self.item_index
        item = route_settings.route_items[self.item_index]
        target = _get_effective_target_collection(root, item, create=False)
        if target is not None:
            route_settings.active_collection = target
        return {'FINISHED'}


class VELO_OT_route_assign_item_to_collection(bpy.types.Operator):
    bl_idname = "velo.route_assign_item_to_collection"
    bl_label = "移动材质分组"
    bl_description = "把当前选中的虚拟材质分组归到指定集合"
    bl_options = {'REGISTER', 'UNDO'}

    collection_name: bpy.props.StringProperty(default="")  # type: ignore
    item_index: bpy.props.IntProperty(default=-1)  # type: ignore

    def execute(self, context):
        root = get_export_component_root(context.scene)
        route_settings = get_material_route_settings(context.scene)
        if root is None or route_settings is None:
            return {'CANCELLED'}
        collection = _find_descendant_collection(root, self.collection_name)
        if collection is None:
            self.report({'ERROR'}, "目标集合不存在")
            return {'CANCELLED'}
        item = None
        index = self.item_index
        if 0 <= index < len(route_settings.route_items):
            item = route_settings.route_items[index]
        else:
            item = get_active_material_route_item(route_settings)
            index = int(getattr(route_settings, "active_route_index", -1))
        if item is None:
            self.report({'ERROR'}, "请先选择一个材质分组")
            return {'CANCELLED'}
        item.target_collection = collection
        route_settings.active_collection = collection
        if 0 <= index < len(route_settings.route_items):
            route_settings.active_route_index = index
        return {'FINISHED'}


class VELO_OT_route_toggle_collection(bpy.types.Operator):
    bl_idname = "velo.route_toggle_collection"
    bl_label = "展开/折叠集合"
    bl_options = {'INTERNAL'}

    collection_name: bpy.props.StringProperty(default="")  # type: ignore

    def execute(self, context):
        root = get_export_component_root(context.scene)
        route_settings = get_material_route_settings(context.scene)
        if root is None or route_settings is None:
            return {'CANCELLED'}
        collection = _find_descendant_collection(root, self.collection_name)
        if collection is None:
            return {'CANCELLED'}
        state = get_collection_state(route_settings, collection, create=True)
        state.expanded = not state.expanded
        return {'FINISHED'}


class VELO_OT_route_add_child_collection(bpy.types.Operator):
    bl_idname = "velo.route_add_child_collection"
    bl_label = "新建子集合"
    bl_description = "在当前选中的归纳集合下创建新的子集合，可用于 CrossIB 按集合映射"
    bl_options = {'REGISTER', 'UNDO'}

    parent_name: bpy.props.StringProperty(default="")  # type: ignore
    child_name: bpy.props.StringProperty(name="子集合名称", default="新集合")  # type: ignore

    def invoke(self, context, event):
        route_settings = get_material_route_settings(context.scene)
        if route_settings is not None and route_settings.active_collection is not None:
            if not self.parent_name:
                self.parent_name = route_settings.active_collection.name
            if self.child_name == "新集合":
                self.child_name = f"{self.parent_name}-子集合"
        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        root = get_export_component_root(context.scene)
        route_settings = get_material_route_settings(context.scene)
        if root is None or route_settings is None:
            self.report({'ERROR'}, f"请先在{get_active_game_display(context.scene)} Export Mod 中指定部件集合")
            return {'CANCELLED'}
        parent = _find_descendant_collection(root, self.parent_name) if self.parent_name else route_settings.active_collection
        if parent is None or not _collection_is_descendant(root, parent):
            self.report({'ERROR'}, "当前集合无效，请先在集合树中选择父集合")
            return {'CANCELLED'}
        name = (self.child_name or "").strip()
        if not name:
            self.report({'ERROR'}, "子集合名称不能为空")
            return {'CANCELLED'}
        _ensure_nested_export_enabled(context.scene)
        created = _ensure_child_collection(parent, name)
        route_settings.active_collection = created
        refresh_material_route_items(context.scene, apply_actual_targets=False)
        self.report({'INFO'}, f"已创建子集合 {created.name}")
        return {'FINISHED'}


class VELO_OT_route_remove_child_collection(bpy.types.Operator):
    bl_idname = "velo.route_remove_child_collection"
    bl_label = "移除子集合"
    bl_description = "移除当前选中的子集合，并把虚拟材质分组回收到父集合"
    bl_options = {'REGISTER', 'UNDO'}

    collection_name: bpy.props.StringProperty(default="")  # type: ignore

    def execute(self, context):
        root = get_export_component_root(context.scene)
        route_settings = get_material_route_settings(context.scene)
        if root is None or route_settings is None:
            self.report({'ERROR'}, f"请先在{get_active_game_display(context.scene)} Export Mod 中指定部件集合")
            return {'CANCELLED'}
        collection = _find_descendant_collection(root, self.collection_name) if self.collection_name else route_settings.active_collection
        if collection is None or collection == root:
            self.report({'ERROR'}, "根集合不能在这里移除")
            return {'CANCELLED'}
        parent = _find_parent_collection(root, collection)
        if parent is None:
            self.report({'ERROR'}, "未找到父集合")
            return {'CANCELLED'}
        if any(is_real_mesh(obj) for obj in collection.all_objects):
            self.report({'ERROR'}, "该集合下仍有真实网格；请先在大纲中整理真实对象后再移除")
            return {'CANCELLED'}

        removed_tree = set(_iter_collection_tree(collection))
        for item in route_settings.route_items:
            target = _get_effective_target_collection(root, item, create=False)
            if target in removed_tree:
                item.target_collection = parent

        cleared_mappings = _clear_crossib_collection_mappings(context.scene, removed_tree)
        removed_count = _remove_collection_tree(collection)
        route_settings.active_collection = parent
        refresh_material_route_items(context.scene, apply_actual_targets=False)

        bits = [f"已移除 {removed_count} 个集合"]
        if cleared_mappings:
            bits.append(f"同时移除了 {cleared_mappings} 条 CrossIB 集合映射")
        self.report({'INFO'}, "；".join(bits))
        return {'FINISHED'}


class VELO_OT_split_by_material_to_collections(bpy.types.Operator):
    bl_idname = "velo.split_by_material_to_collections"
    bl_label = "按材质分离并归纳到集合"
    bl_description = (
        "手动对当前导出部件集合内选中的网格按材质拆分；"
        "单材质网格会直接跳过；拆分后的单材质部件会自动保留 Component 前缀并归入当前材质树指定的集合"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root = get_export_component_root(context.scene)
        if root is None:
            return False
        return any(is_real_mesh(obj) and _collection_is_descendant(root, obj.users_collection[0]) for obj in context.selected_objects if obj.users_collection)

    def execute(self, context):
        root = get_export_component_root(context.scene)
        if root is None:
            self.report({'ERROR'}, f"请先在{get_active_game_display(context.scene)} Export Mod 中指定部件集合")
            return {'CANCELLED'}
        refresh_material_route_items(context.scene, apply_actual_targets=False)
        sources = [
            obj for obj in _selected_meshes(context)
            if any(_collection_is_descendant(root, collection) for collection in getattr(obj, 'users_collection', ()))
        ]
        if not sources:
            self.report({'WARNING'}, "请先选择部件集合内至少一个网格物体")
            return {'CANCELLED'}
        weight_snapshot = _snapshot_weight_tool_objects(context)
        _ROUTE_AUTO_REFRESHING[0] = True
        try:
            if not any(len(obj.material_slots) > 1 for obj in sources):
                result = _route_meshes_to_component_sets(context.scene, sources, refresh_after=False)
                missing = result["missing_component"]
                bits = [
                    "全部单材质，未执行分离",
                    f"归纳 {result['routed_count']} 个",
                    f"创建/复用 {result['created_collections']} 个集合",
                ]
                if missing:
                    bits.append(f"跳过 {len(missing)} 个未识别 Component 的结果")
                return_bits = bits
            else:
                stats = _split_meshes_by_material(
                    context,
                    sources,
                    threshold=float(getattr(context.scene.velo_tools, "shapekey_cleanup_threshold", _EPS)),
                    preserve_component_prefix=True,
                )
                result = _route_meshes_to_component_sets(context.scene, list(stats["targets"]), refresh_after=False)
                missing = result["missing_component"]
                bits = [
                    f"处理 {len(stats['targets'])} 个结果物体",
                    f"实际拆分 {stats['split_sources']} 个多材质物体",
                    f"单材质跳过 {stats['skipped_single']} 个",
                    f"归纳 {result['routed_count']} 个",
                    f"清理材质槽 {stats['cleaned_slots']}",
                    f"清理形态键 {stats['cleaned_keys']}",
                ]
                if missing:
                    bits.append(f"跳过 {len(missing)} 个未识别 Component 的结果")
                return_bits = bits
        finally:
            _ROUTE_AUTO_REFRESHING[0] = False

        refresh_material_route_items(context.scene)
        _restore_weight_tool_objects(context, weight_snapshot)
        self.report({'INFO'}, "；".join(return_bits))
        return {'FINISHED'}


class VELO_OT_split_by_texture_to_collections(bpy.types.Operator):
    bl_idname = "velo.split_by_texture_to_collections"
    bl_label = "按贴图分离并归纳到集合"
    bl_description = (
        "手动对当前导出部件集合内选中的网格先按材质拆开，再按贴图重组；"
        "同贴图的材质会尽量保留在同一个结果物体里，并按当前材质树归纳到集合"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        root = get_export_component_root(context.scene)
        if root is None:
            return False
        return any(is_real_mesh(obj) and _collection_is_descendant(root, obj.users_collection[0]) for obj in context.selected_objects if obj.users_collection)

    def execute(self, context):
        root = get_export_component_root(context.scene)
        if root is None:
            self.report({'ERROR'}, f"请先在{get_active_game_display(context.scene)} Export Mod 中指定部件集合")
            return {'CANCELLED'}
        refresh_material_route_items(context.scene, apply_actual_targets=False)
        sources = [
            obj for obj in _selected_meshes(context)
            if any(_collection_is_descendant(root, collection) for collection in getattr(obj, 'users_collection', ()))
        ]
        if not sources:
            self.report({'WARNING'}, "请先选择部件集合内至少一个网格物体")
            return {'CANCELLED'}

        weight_snapshot = _snapshot_weight_tool_objects(context)
        merged = {"replacement_map": {}}

        _ROUTE_AUTO_REFRESHING[0] = True
        try:
            stats = {
                "targets": set(sources),
                "split_sources": 0,
                "skipped_single": len(sources),
                "cleaned_slots": 0,
                "cleaned_keys": 0,
            }
            if any(len(obj.material_slots) > 1 for obj in sources):
                stats = _split_meshes_by_material(
                    context,
                    sources,
                    threshold=float(getattr(context.scene.velo_tools, "shapekey_cleanup_threshold", _EPS)),
                    preserve_component_prefix=True,
                )

            result = _route_meshes_to_component_sets(context.scene, list(stats["targets"]), refresh_after=False)
            merged = _merge_meshes_by_texture_groups(context, list(stats["targets"]), root)
        finally:
            _ROUTE_AUTO_REFRESHING[0] = False

        refresh_material_route_items(context.scene)
        _restore_weight_tool_objects(context, weight_snapshot, replacement_map=merged.get("replacement_map"))

        missing = result["missing_component"]
        bits = [
            f"拆分候选 {len(stats['targets'])} 个",
            f"实际拆分 {stats['split_sources']} 个多材质物体",
            f"单材质跳过 {stats['skipped_single']} 个",
            f"归纳 {result['routed_count']} 个",
            f"按贴图重组 {merged['merged_groups']} 组 / {merged['merged_objects']} 个",
            f"最终结果 {merged['result_count']} 个",
            f"清理材质槽 {stats['cleaned_slots']}",
            f"清理形态键 {stats['cleaned_keys']}",
        ]
        if missing:
            bits.append(f"跳过 {len(missing)} 个未识别 Component 的结果")
        self.report({'INFO'}, "；".join(bits))
        return {'FINISHED'}


class VELO_OT_split_by_material(bpy.types.Operator):
    bl_idname = "velo.split_by_material"
    bl_label = "按材质拆分网格"
    bl_description = (
        "对每个选中网格: 进入编辑模式按材质拆分, "
        "拆分后的每个子物体只保留实际使用的材质槽, "
        "并清理零位移的形态键"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def execute(self, context):
        sources = _selected_meshes(context)
        if not sources:
            self.report({'WARNING'}, "请先选择至少一个网格物体")
            return {'CANCELLED'}
        stats = _split_meshes_by_material(
            context,
            sources,
            threshold=float(getattr(context.scene.velo_tools, "shapekey_cleanup_threshold", _EPS)),
            preserve_component_prefix=False,
        )

        self.report(
            {'INFO'},
            f"按材质拆分完成: 结果 {len(stats['targets'])} 个; "
            f"清理材质槽 {stats['cleaned_slots']}, 清理形态键 {stats['cleaned_keys']}, 同步命名 {stats['renamed']}",
        )
        return {'FINISHED'}


_classes = (
    VELO_MaterialRouteItem,
    VELO_MaterialRouteCollectionState,
    VELO_MaterialRouteSettings,
    VELO_OT_add_component_prefix,
    VELO_OT_gen_material_for_selected,
    VELO_OT_merge_by_texture,
    VELO_OT_route_refresh_materials,
    VELO_OT_route_set_active_collection,
    VELO_OT_route_select_material_item,
    VELO_OT_route_assign_item_to_collection,
    VELO_OT_route_toggle_collection,
    VELO_OT_route_add_child_collection,
    VELO_OT_route_remove_child_collection,
    VELO_OT_split_by_material_to_collections,
    VELO_OT_split_by_texture_to_collections,
    VELO_OT_split_by_material,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.velo_material_route_tools = bpy.props.PointerProperty(type=VELO_MaterialRouteSettings)
    if _route_depsgraph_handler not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_route_depsgraph_handler)


def unregister():
    if _route_depsgraph_handler in bpy.app.handlers.depsgraph_update_post:
        try:
            bpy.app.handlers.depsgraph_update_post.remove(_route_depsgraph_handler)
        except ValueError:
            pass
    _ROUTE_AUTO_SIG.clear()
    if hasattr(bpy.types.Scene, "velo_material_route_tools"):
        del bpy.types.Scene.velo_material_route_tools
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
