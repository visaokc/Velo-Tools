"""Explicit scene-local texture memberships, independent of names and file paths."""
import uuid

import bpy
from bpy.app.handlers import persistent

from . import model, nodes
from ..i18n import iface_


_OBJECT_KEY = "material_sync_identity"
_ID_CACHE = {}
_ID_UIDS = {}
_ID_NAMES = {}
_KINDS = {"object": "objects", "material": "materials", "image": "images"}


def _remember_id(kind, token, value):
    key = (kind, token)
    _ID_CACHE[key] = value
    _ID_NAMES[key] = value.name
    if hasattr(value, "session_uid"):
        _ID_UIDS[key] = value.session_uid


def _resolve_id(kind, name, token):
    if not token:
        return None
    key = (kind, token)
    collection = getattr(bpy.data, _KINDS[kind])
    if key in _ID_CACHE:
        value = _ID_CACHE[key]
        try:
            if value and collection.get(value.name) == value and value.get(_OBJECT_KEY) == token:
                _remember_id(kind, token, value)
                return value
            return None
        except ReferenceError:
            return None
    # Runtime UIDs survive undo reallocations but are never written to disk.
    if key in _ID_UIDS:
        value = next((item for item in collection if getattr(item, "session_uid", None) == _ID_UIDS[key]), None)
    else:
        value = collection.get(_ID_NAMES.get(key, name)) or collection.get(name)
    if value is not None and value.get(_OBJECT_KEY) == token:
        _remember_id(kind, token, value)
        return value
    return None


class MATERIAL_PG_TextureMember(bpy.types.PropertyGroup):
    object_name: bpy.props.StringProperty()
    object_token: bpy.props.StringProperty()
    material_name: bpy.props.StringProperty()
    material_token: bpy.props.StringProperty()
    image_name: bpy.props.StringProperty()
    image_token: bpy.props.StringProperty()


class MATERIAL_PG_TextureGroup(bpy.types.PropertyGroup):
    identifier: bpy.props.StringProperty()
    role: bpy.props.StringProperty()
    game: bpy.props.StringProperty()
    members: bpy.props.CollectionProperty(type=MATERIAL_PG_TextureMember)


def same_member(member, obj, material):
    return member["object"] == obj and member["material"] == material


def snapshot(scene):
    result = []
    for group in getattr(scene, "material_texture_groups", ()):
        row = {"id": group.identifier, "role": group.role, "game": group.game, "members": []}
        for item in group.members:
            member = {}
            for kind in _KINDS:
                name, token = getattr(item, kind + "_name"), getattr(item, kind + "_token")
                member[kind] = _resolve_id(kind, name, token)
                member[kind + "_name"], member[kind + "_token"] = name, token
            row["members"].append(member)
        result.append(row)
    return result


def restore(scene, state):
    """Store weak identity references without introducing native scene ID edges.

    Native Scene -> Object/Material/Image custom pointers interfere with the
    exporter's evaluated-data cleanup. Tokens plus saved names are persistent;
    runtime identity caches handle renames without adopting duplicated IDs.
    """
    collection = scene.material_texture_groups
    collection.clear()
    for row in state:
        group = collection.add()
        group.identifier, group.role, group.game = row["id"], row["role"], row["game"]
        for member in row["members"]:
            item = group.members.add()
            for kind in _KINDS:
                value = member[kind]
                if value is not None:
                    token = value.get(_OBJECT_KEY, "")
                    key = (kind, token)
                    cached = _ID_CACHE.get(key)
                    if (not token or (cached is not None and cached != value)
                            or (key in _ID_UIDS and _ID_UIDS[key] != getattr(value, "session_uid", None))):
                        token = uuid.uuid4().hex
                        value[_OBJECT_KEY] = token
                    _remember_id(kind, token, value)
                    setattr(item, kind + "_name", value.name)
                    setattr(item, kind + "_token", token)
                else:
                    setattr(item, kind + "_name", member.get(kind + "_name", ""))
                    setattr(item, kind + "_token", member.get(kind + "_token", ""))


def write(scene, state):
    restore(scene, state)


def live_member(scene, group, member):
    """Manual rewiring is a local override, never an implicit broadcast."""
    obj, material = member["object"], member["material"]
    if (obj is None or material is None or obj.type != "MESH"
            or (member.get("image_token") and member["image"] is None)):
        return False
    if scene.objects.get(obj.name) != obj:
        return False
    if not any(slot.material == material for slot in obj.material_slots):
        return False
    try:
        if model.unpack_sources(material).get("game") != group["game"]:
            return False
        return nodes.role_image(material, group["role"]) == member["image"]
    except (ValueError, KeyError, ReferenceError):
        return False


def current_state(scene, owner=None):
    result = []
    for group in snapshot(scene):
        if group["role"] not in model.ROLES:
            continue
        if owner is not None and not any(same_member(member, *owner) for member in group["members"]):
            continue
        members = [member for member in group["members"] if live_member(scene, group, member)]
        if members:
            result.append({**group, "members": members})
    return result


def find(state, obj, material, role):
    matches = [group for group in state if group["role"] == role
               and any(same_member(member, obj, material) for member in group["members"])]
    if len(matches) > 1:
        raise ValueError(iface_("Conflicting texture sync memberships"))
    return matches[0] if matches else None


def remap(state, plans):
    """Move only explicitly replaced object/material uses, not their other users."""
    replacements = {(obj.as_pointer(), original.as_pointer()): replacement
                    for obj, _index, original, replacement in plans if original is not None}
    return [{**group, "members": [
        {**member, "material": replacements.get(
            (member["object"].as_pointer(), member["material"].as_pointer()), member["material"])}
        if member["object"] and member["material"] else dict(member)
        for member in group["members"]]}
        for group in state]


def detach(state, obj, material, role):
    result = []
    for group in state:
        members = [dict(member) for member in group["members"]
                   if group["role"] != role or not same_member(member, obj, material)]
        if members:
            result.append({**group, "members": members})
    return result


def link_selected(state, source, entries):
    """Link matching roles only. Existing members outside selection are not recruited.

    Empty, inferred inputs can share a group before the first replacement image
    is assigned. An independent populated target is not linked by fill-only mode.
    """
    obj, material = source
    source_data = model.unpack_sources(material)
    game = source_data.get("game", "")
    images = nodes.connected_images(material)
    linked = 0
    for role in model.ROLES:
        if model.inherits_game_source(source_data, role):
            continue
        image = images.get(role)
        if image is None and role not in source_data.get("bindings", {}):
            continue
        candidates, seen = [], set()
        for target_obj, _index, target in entries:
            key = (target_obj.as_pointer(), target.as_pointer())
            if key in seen or not nodes.assignment_node(target):
                continue
            seen.add(key)
            data = model.unpack_sources(target)
            if data.get("game") != game or model.inherits_game_source(data, role):
                continue
            target_image = nodes.role_image(target, role)
            if nodes.image_key(target_image) != nodes.image_key(image):
                continue
            if image is None and role not in data.get("bindings", {}):
                continue
            candidates.append({"object": target_obj, "material": target, "image": target_image})
        if not candidates:
            continue
        previous = find(state, obj, material, role)
        identifier = previous["id"] if previous else uuid.uuid4().hex
        for member in candidates:
            existing = find(state, member["object"], member["material"], role)
            linked += existing is None or existing["id"] != identifier
            state = detach(state, member["object"], member["material"], role)
        group = next((row for row in state if row["id"] == identifier), None)
        if group is None:
            group = {"id": identifier, "role": role, "game": game, "members": []}
            state.append(group)
        group["members"].extend(candidates)
    return state, linked


def replace_expected(state, identifier, image, *, members=None):
    """Update participating members without erasing unrelated local-edit history."""
    selected = None if members is None else {
        (member["object"].as_pointer(), member["material"].as_pointer()) for member in members}
    return [{**group, "members": [
        {**member, "image": image} if selected is None or (
            member["object"] is not None and member["material"] is not None
            and (member["object"].as_pointer(), member["material"].as_pointer()) in selected)
        else dict(member) for member in group["members"]]}
        if group["id"] == identifier else group for group in state]


@persistent
def _save_names(_unused):
    for scene in bpy.data.scenes:
        for group in getattr(scene, "material_texture_groups", ()):
            for item in group.members:
                for kind in _KINDS:
                    value = _resolve_id(kind, getattr(item, kind + "_name"), getattr(item, kind + "_token"))
                    if value is not None and getattr(item, kind + "_name") != value.name:
                        setattr(item, kind + "_name", value.name)
                    elif value is None and getattr(item, kind + "_token"):
                        # A deleted member must not be replaced by a same-name clone
                        # after the runtime identity cache is cleared on reload.
                        setattr(item, kind + "_name", "")


@persistent
def _clear_cache(_unused):
    _ID_CACHE.clear()
    _ID_UIDS.clear()
    _ID_NAMES.clear()


@persistent
def _undo_cache(_unused):
    _ID_CACHE.clear()


_CLASSES = (MATERIAL_PG_TextureMember, MATERIAL_PG_TextureGroup)
_HANDLERS = ((bpy.app.handlers.save_pre, _save_names),
             (bpy.app.handlers.load_post, _clear_cache),
             (bpy.app.handlers.undo_post, _undo_cache),
             (bpy.app.handlers.redo_post, _undo_cache))


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.material_texture_groups = bpy.props.CollectionProperty(type=MATERIAL_PG_TextureGroup)
    for handlers, callback in _HANDLERS:
        if callback not in handlers:
            handlers.append(callback)


def unregister():
    # Flush names while the RNA collection is still registered.
    if hasattr(bpy.data, "scenes"):
        _save_names(None)
    for handlers, callback in _HANDLERS:
        if callback in handlers:
            handlers.remove(callback)
    _clear_cache(None)
    del bpy.types.Scene.material_texture_groups
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
