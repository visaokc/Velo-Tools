"""Material authoring actions with copy-on-write object-slot transactions."""
import json
from pathlib import Path

import bpy
import bpy.utils.previews

from . import model, nodes, hooks, groups
from ..i18n import iface_

_ENUM_ITEMS = []
_ENUM_STRINGS = {}
_PREVIEWS = None
_PREVIEW_ICONS = {}


def active_material(context):
    obj = context.active_object
    return obj.active_material if obj and obj.type == "MESH" else None


def source_context(context, obj, material, cache=None):
    game = context.scene.velo_tools.active_game
    cfg = getattr(context.scene, hooks.SETTINGS[game])
    comp = model.component_id(material.name) if material else None
    if comp is None:
        comp = model.component_id(obj.name) if obj else None
    catalog = {}
    if comp is not None and cfg.object_source_folder.strip():
        try:
            folder = Path(bpy.path.abspath(cfg.object_source_folder))
            key = (game, str(folder), comp)
            if cache is None:
                catalog = model.read_evidence(folder, comp)
            else:
                if key not in cache:
                    cache[key] = model.read_evidence(folder, comp)
                catalog = cache[key]
        except FileNotFoundError:
            # Node initialization also works before an extraction is configured.
            catalog = {}
    return game, comp, catalog


def _selected_slots(context, *, used_only=True):
    if context.mode != "OBJECT":
        raise ValueError(iface_("Switch to Object Mode to edit material assignments"))
    for obj in context.selected_objects:
        if obj.type != "MESH":
            continue
        if obj.library and not obj.override_library:
            raise ValueError(iface_("Linked objects cannot receive local material assignments"))
        used = {polygon.material_index for polygon in obj.data.polygons}
        for index, slot in enumerate(obj.material_slots):
            if slot.material and (not used_only or index in used):
                yield obj, index, slot.material


def _commit(plans, *, scene=None, group_state=None, keep_backups=True, prefer_data=False):
    """Preserve effective slots and remap explicit groups as one transaction.

    Local single-user DATA slots stay DATA. Shared meshes use OBJECT overrides
    to isolate other users without copying geometry. Naming may request DATA
    after it has explicitly made the mesh single-user.
    """
    plans = list(plans)
    scene = scene or bpy.context.scene
    previous_groups = groups.snapshot(scene)
    next_groups = groups.remap(previous_groups if group_state is None else group_state, plans)
    group_changes = [(scene, previous_groups, next_groups)]
    # Object slots are shared across scenes; copied material IDs must follow
    # the same object uses in every scene, without recruiting new members.
    if plans:
        for other_scene in bpy.data.scenes:
            if other_scene == scene or not getattr(other_scene, "material_texture_groups", ()):
                continue
            previous = groups.snapshot(other_scene)
            updated = groups.remap(previous, plans)
            if updated != previous:
                group_changes.append((other_scene, previous, updated))
    token_backups = {member[kind]: member[kind].get(groups._OBJECT_KEY)
                     for _owner, _previous, updated in group_changes
                     for group in updated for member in group["members"]
                     for kind in groups._KINDS if member[kind] is not None}
    object_cache = dict(groups._ID_CACHE)
    object_uids = dict(groups._ID_UIDS)
    object_names = dict(groups._ID_NAMES)
    changed, attempted_groups = [], []
    backups = {}
    try:
        # Validate the entire plan before a DATA assignment can affect another
        # slot's effective material. Shared mesh writes remain object-local.
        for obj, index, original, replacement in plans:
            if obj.material_slots[index].material != original:
                raise ValueError(iface_("The target material changed; reopen the texture picker"))
        for obj, index, original, replacement in plans:
            slot = obj.material_slots[index]
            link = slot.link
            data = obj.data
            single_data = data.library is None and data.users - int(data.use_fake_user) <= 1
            target_link = "DATA" if single_data and (prefer_data or link == "DATA") else "OBJECT"
            slot.link = target_link
            prior_target = slot.material
            changed.append((obj, index, link, target_link, original, prior_target))
            slot.material = replacement
            if link == "OBJECT" and target_link == "DATA":
                # Do not retain a hidden OBJECT reference after safe migration.
                slot.link = "OBJECT"
                slot.material = None
                slot.link = "DATA"
            if keep_backups and original is not None and original.library is None:
                backups.setdefault(original, original.use_fake_user)
                original.use_fake_user = True
        for owner, previous, updated in group_changes:
            if updated != previous:
                attempted_groups.append((owner, previous))
                groups.write(owner, updated)
    except Exception:
        for obj, index, link, target_link, original, prior_target in reversed(changed):
            slot = obj.material_slots[index]
            slot.link = target_link
            slot.material = prior_target
            slot.link = link
            if link != target_link:
                slot.material = original
        for material, fake_user in backups.items():
            material.use_fake_user = fake_user
        for owner, previous in reversed(attempted_groups):
            groups.restore(owner, previous)
        if attempted_groups:
            for obj, token in token_backups.items():
                if token is None:
                    if groups._OBJECT_KEY in obj:
                        del obj[groups._OBJECT_KEY]
                else:
                    obj[groups._OBJECT_KEY] = token
            groups._ID_CACHE.clear()
            groups._ID_CACHE.update(object_cache)
            groups._ID_UIDS.clear()
            groups._ID_UIDS.update(object_uids)
            groups._ID_NAMES.clear()
            groups._ID_NAMES.update(object_names)
        raise


def _discard(materials):
    for material in materials:
        if material.users == 0:
            bpy.data.materials.remove(material)


class MATERIAL_OT_initialize(bpy.types.Operator):
    bl_idname = "material_tools.initialize"
    bl_label = "Initialize Selected Materials"
    bl_description = "Replace selected MMD or Blender shaders with semantic texture inputs, preserving diffuse UV wiring and keeping original materials as backups"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        copies, plans, cache = {}, [], {}
        initialized, refreshed = 0, 0
        try:
            entries = list(_selected_slots(context))
            witnesses = _mapping_witnesses(context, list(_selected_slots(context, used_only=False)), cache)
            for obj, index, material in entries:
                game, comp, catalog = source_context(context, obj, material, cache)
                key = (material.as_pointer(), game, comp)
                if key not in copies:
                    existing = nodes.assignment_node(material)
                    if existing:
                        cfg = getattr(context.scene, hooks.SETTINGS[game])
                        if comp is None or not cfg.object_source_folder.strip() or not (
                                Path(bpy.path.abspath(cfg.object_source_folder)) / "ShaderTextureUsage.json").is_file():
                            continue
                    image_key = nodes.image_key(nodes.diffuse_image(material))
                    value = _resolved_mapping(material, game, comp, catalog, witnesses.get(image_key))
                    if existing and value == model.unpack_sources(material):
                        continue
                    copy = material.copy() if existing else nodes.initialize_copy(material, game, comp, catalog)
                    copies[key] = copy
                    copy[model.DATA_KEY] = json.dumps(value, sort_keys=True)
                    initialized += not existing
                    refreshed += bool(existing)
                plans.append((obj, index, material, copies[key]))
            _commit(plans)
        except Exception as exc:
            _discard(copies.values())
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("Initialized {0} materials, refreshed {1} mappings; originals retained as backups").format(initialized, refreshed))
        return {"FINISHED"}



def _image_identities(images):
    return {role: identity for role, image in images.items()
            if (identity := model.texture_identity(image.filepath or image.name))}


def _mapping_witnesses(context, entries, cache):
    """Find original identities on all selected slots using each diffuse image."""
    result = {}
    for obj, _index, material in entries:
        diffuse = nodes.diffuse_image(material)
        if diffuse is None:
            continue
        game, comp, catalog = source_context(context, obj, material, cache)
        data = model.unpack_sources(material)
        bindings = data.get("bindings", {}) if data.get("game") == game else {}
        row = result.setdefault(nodes.image_key(diffuse), {})
        identity = model.texture_identity(diffuse.filepath or diffuse.name)
        if identity in catalog and "DIFFUSE" not in data.get("replacement_roles", ()):
            row.setdefault("DIFFUSE", set()).add(identity)
        for role, identity in bindings.items():
            if identity in catalog:
                row.setdefault(role, set()).add(identity)
    return result


def _resolved_mapping(material, game, comp, catalog, witnesses=None, images=None, replacement_roles=()):
    if images is None:
        images = nodes.connected_images(material) if nodes.assignment_node(material) else {}
        diffuse = nodes.diffuse_image(material)
        if diffuse:
            images["DIFFUSE"] = diffuse
    identities = {role: identity for role, identity in _image_identities(images).items() if role not in replacement_roles}
    value = model.resolve_sources(game, comp, catalog, model.unpack_sources(material), identities, witnesses)
    if replacement_roles:
        value["replacement_roles"] = sorted(set(value.get("replacement_roles", ())) | set(replacement_roles))
    return value


def _mapping_value(context, material):
    cache = {}
    game, comp, catalog = source_context(context, context.active_object, material, cache)
    cfg = getattr(context.scene, hooks.SETTINGS[game])
    if comp is None or not cfg.object_source_folder.strip() or not (
            Path(bpy.path.abspath(cfg.object_source_folder)) / "ShaderTextureUsage.json").is_file():
        raise ValueError(iface_("Set an original source folder and Component name before mapping textures"))
    entries = list(_selected_slots(context, used_only=False))
    witnesses = _mapping_witnesses(context, entries, cache)
    key = nodes.image_key(nodes.diffuse_image(material))
    value = _resolved_mapping(material, game, comp, catalog, witnesses.get(key))
    return value


def _refresh_mapping(context, material):
    from .group_ui import edit_source
    value = _mapping_value(context, material)
    edit_source(context, context.active_object, material, value)
    return value


class MATERIAL_OT_refresh_sources(bpy.types.Operator):
    bl_idname = "material_tools.refresh_sources"
    bl_label = "Refresh Source Mapping"
    bl_description = "Re-read retained source images and selected diffuse-image matches; apply unique mappings immediately, preserve manual edits, and omit removed originals"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        material = active_material(context)
        try:
            if not nodes.assignment_node(material):
                raise ValueError(iface_("Initialize this material first"))
            value = _refresh_mapping(context, material)
        except Exception as exc:
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("Source mapping updated: {0} assigned, {1} removed originals ignored").format(
            len(value["bindings"]), len(value["omitted"])))
        return {"FINISHED"}



def _retain_enum_items(items):
    """Blender borrows enum string pointers; retain them until unregistration."""
    return [tuple(_ENUM_STRINGS.setdefault(value, value) if isinstance(value, str) else value
                  for value in item) for item in items]


def _source_items(self, context):
    global _ENUM_ITEMS
    material = bpy.data.materials.get(getattr(self, "material_name", "")) or (
        active_material(context) if context is not None else None)
    cached = getattr(self, "catalog_json", "")
    data = json.loads(cached) if cached else (model.unpack_sources(material) if material else {})
    items = [("NONE", iface_("Keep Game Texture"), iface_("Do not override this texture role"), "X", 0),
             ("UNASSIGNED", iface_("Unassigned"), iface_("Clear the original mapping and allow automatic matching on the next refresh"), "LOOP_BACK", 1)]
    role = getattr(self, "role", "")
    catalog = data.get("catalog", {})
    game = data.get("game", "ENDFIELD")
    preferred = data.get("bindings", {}).get(role)
    entries = list(enumerate(catalog.items(), 2))
    entries.sort(key=lambda item: (item[1][0] != preferred,
        role not in model.role_hints(item[1][1], game), item[0]))
    for number, (identity, record) in entries:
        name = Path(record["names"][0]).name if record.get("names") else identity
        hints = " / ".join(iface_(model.ROLES[key]) for key in model.ROLES
                           if key in model.role_hints(record, game))
        description = " / ".join(record.get("formats", ())) + " | " + identity
        if hints:
            description = iface_("Possible role: {0}").format(hints) + " | " + description
        items.append((identity, name, description,
                      _PREVIEW_ICONS.get((material.name if material else "", identity), 0), number))
    _ENUM_ITEMS = _retain_enum_items(items)
    return _ENUM_ITEMS


def load_source_previews(context, material, data=None):
    """Generate thumbnails only on explicit picker invocation, never panel redraw."""
    global _PREVIEWS
    if _PREVIEWS is None:
        _PREVIEWS = bpy.utils.previews.new()
    data = data if data is not None else model.unpack_sources(material)
    cfg = getattr(context.scene, hooks.SETTINGS.get(data.get("game"), "VTEF_settings"))
    folder = Path(bpy.path.abspath(cfg.object_source_folder))
    if not folder.is_dir():
        return
    for identity, record in data.get("catalog", {}).items():
        path = next((folder / name for name in record.get("files", ())
                     if (folder / name).is_file()), None)
        if path is None:
            continue
        key = str(path) + str(path.stat().st_mtime_ns)
        try:
            preview = _PREVIEWS.get(key) or _PREVIEWS.load(key, str(path), "IMAGE")
            _PREVIEW_ICONS[(material.name, identity)] = preview.icon_id
        except (RuntimeError, OSError):
            # A missing thumbnail must not prevent explicit source selection.
            _PREVIEW_ICONS[(material.name, identity)] = 0


class MATERIAL_OT_choose_source(bpy.types.Operator):
    bl_idname = "material_tools.choose_source"
    bl_label = "Choose Original Texture"
    bl_description = "Choose the original extracted image for this semantic role; no shader slot number is required"
    bl_options = {"REGISTER", "UNDO"}
    material_name: bpy.props.StringProperty(options={"HIDDEN"})
    object_name: bpy.props.StringProperty(options={"HIDDEN"})
    role: bpy.props.StringProperty(options={"HIDDEN"})
    catalog_json: bpy.props.StringProperty(options={"HIDDEN"})
    source: bpy.props.EnumProperty(name="Original Texture", items=_source_items)
    bl_property = "source"

    def invoke(self, context, event):
        material = active_material(context)
        if material is None:
            return {"CANCELLED"}
        self.material_name = material.name
        self.object_name = context.active_object.name
        try:
            data = _mapping_value(context, material)
        except Exception as exc:
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.catalog_json = json.dumps(data)
        load_source_previews(context, material, data)
        self.source = data.get("bindings", {}).get(self.role,
            "NONE" if model.inherits_game_source(data, self.role) else "UNASSIGNED")
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        self.layout.label(text=iface_(model.ROLES.get(self.role, "Original Texture")))
        self.layout.template_icon_view(self, "source", show_labels=True, scale=5.0)
        self.layout.prop(self, "source")
        self.layout.label(text="Choose the original image, not the replacement", icon="INFO")

    def execute(self, context):
        from .group_ui import edit_source, _target
        try:
            obj, material = _target(context, self)
            data = model.unpack_sources(material)
            if self.source == "UNASSIGNED":
                value = model.clear_source(data, self.role)
            else:
                game, comp, catalog = source_context(context, obj, material)
                if self.source != "NONE" and self.source not in catalog:
                    raise ValueError(iface_("The selected original is no longer in the source folder"))
                data.setdefault("manual", dict(data.get("bindings", {})))[self.role] = (
                    "" if self.source == "NONE" else self.source)
                diffuse = nodes.diffuse_image(material)
                value = model.resolve_sources(game, comp, catalog, data,
                    _image_identities({"DIFFUSE": diffuse}) if diffuse else {})
            edit_source(context, obj, material, value)
        except Exception as exc:
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


def _propagation_source(context, entries):
    """Prefer the active configured material; infer a unique selected donor otherwise."""
    active = active_material(context)
    if active and any(obj == context.active_object and mat == active for obj, _slot, mat in entries) and nodes.assignment_node(active):
        images = nodes.connected_images(active)
        if images.get("DIFFUSE"):
            return active, images
    donors = {}
    for _obj, _index, material in entries:
        if not nodes.assignment_node(material):
            continue
        images = nodes.connected_images(material)
        if not images.get("DIFFUSE"):
            continue
        signature = tuple(sorted((role, repr(nodes.image_key(image))) for role, image in images.items()))
        donors.setdefault(signature, (material, images))
    if len(donors) == 1:
        return next(iter(donors.values()))
    if donors:
        raise ValueError(iface_("Several configured materials are selected; make the intended source material active"))
    raise ValueError(iface_("Initialize a source material and connect its diffuse image before propagation"))


class MATERIAL_OT_propagate(bpy.types.Operator):
    bl_idname = "material_tools.propagate"
    bl_label = "Propagate by Same Diffuse"
    bl_description = "Find the source and all matching diffuse images across selected material slots, copy other maps, resolve each Component's original identities and remember per-role sync groups; the active configured material takes priority"
    bl_options = {"REGISTER", "UNDO"}
    overwrite: bpy.props.BoolProperty(
        name="Replace Existing Connections",
        description="Replace populated non-diffuse inputs; otherwise only fill empty inputs. Source mappings are refreshed in both modes",
        default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        copies, plans, cache = {}, [], {}
        matched, filled, mapped, unresolved = set(), 0, 0, 0
        try:
            entries = list(_selected_slots(context))
            state = groups.current_state(context.scene)
            source, images = _propagation_source(context, entries)
            diffuse = images["DIFFUSE"]
            key_diffuse = nodes.image_key(diffuse)
            witness_entries = list(_selected_slots(context, used_only=False))
            if not any(material == source for _obj, _index, material in witness_entries):
                witness_entries.append((context.active_object, -1, source))
            witnesses = _mapping_witnesses(context, witness_entries, cache).get(key_diffuse, {})
            wanted = {role: image for role, image in images.items() if role != "DIFFUSE"}
            replacement_roles = set(model.unpack_sources(source).get("replacement_roles", ()))
            for obj, index, material in entries:
                if nodes.image_key(nodes.diffuse_image(material)) != key_diffuse:
                    continue
                game, comp, catalog = source_context(context, obj, material, cache)
                key = (material.as_pointer(), game, comp)
                matched.add(key)
                if key in copies:
                    plans.append((obj, index, material, copies[key]))
                    continue
                existing = nodes.assignment_node(material)
                target_images = nodes.connected_images(material) if existing else {"DIFFUSE": nodes.diffuse_image(material)}
                additions = {role: image for role, image in wanted.items()
                             if (self.overwrite or role not in target_images)
                             and nodes.image_key(target_images.get(role)) != nodes.image_key(image)}
                old = model.unpack_sources(material)
                value = _resolved_mapping(material, game, comp, catalog, witnesses,
                                          {**target_images, **additions},
                                          replacement_roles & ({"DIFFUSE"} | additions.keys()))
                additions = {role: image for role, image in additions.items()
                             if not model.inherits_game_source(value, role)}
                mapping_changed = existing and old != value
                if not additions and not mapping_changed and existing:
                    continue
                copy = material.copy() if existing else nodes.initialize_copy(material, game, comp, catalog)
                copies[key] = copy
                copy[model.DATA_KEY] = json.dumps(value, sort_keys=True)
                for role, image in additions.items():
                    nodes.connect_image(copy, role, image)
                filled += len(additions)
                mapped += bool(mapping_changed or not existing)
                unresolved += sum(role not in value["bindings"] and not model.inherits_game_source(value, role)
                                  for role in {**target_images, **additions})
                plans.append((obj, index, material, copy))
            remapped = {(obj.as_pointer(), original.as_pointer()): replacement
                        for obj, _index, original, replacement in plans}
            final_entries = [(obj, index, remapped.get((obj.as_pointer(), mat.as_pointer()), mat))
                             for obj, index, mat in entries
                             if nodes.image_key(nodes.diffuse_image(mat)) == key_diffuse]
            source_entry = next(((obj, mat) for obj, index, mat in final_entries
                                 if obj == context.active_object and entries
                                 and any(old == source and original_obj == obj and old_index == index
                                         for original_obj, old_index, old in entries)), None)
            if source_entry is None:
                source_entry = next(((obj, remapped.get((obj.as_pointer(), source.as_pointer()), source))
                                     for obj, _index, mat in entries if mat == source), None)
            linked = 0
            if source_entry is not None:
                state, linked = groups.link_selected(groups.remap(state, plans), source_entry, final_entries)
            _commit(plans, scene=context.scene, group_state=state)
        except Exception as exc:
            _discard(copies.values())
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("Source {0}: matched {1} materials, connected {2} maps, refreshed {3} mappings, {4} unresolved roles").format(
            source.name, len(matched), filled, mapped, unresolved))
        if linked:
            self.report({"INFO"}, iface_("Linked {0} texture roles; replace a group from any member").format(linked))
        if not copies and not linked:
            target_matches = [item for item in matched if item[0] != source.as_pointer()]
            if not target_matches:
                self.report({"WARNING"}, iface_("No selected target uses the source diffuse image"))
            else:
                self.report({"INFO"}, iface_("Matching materials are already up to date; enable Replace Existing Connections to replace populated inputs"))
        return {"FINISHED"}



class MATERIAL_PT_tools(bpy.types.Panel):
    bl_label = "Material Tools"
    bl_idname = "MATERIAL_PT_tools"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Velo Tools"
    bl_parent_id = "VELO_PT_main"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return getattr(context.scene.velo_tools, "active_tab", "") == "MATERIAL"

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene.velo_tools, "active_game")
        layout.operator("material_tools.initialize", icon="NODE_MATERIAL")
        layout.operator("material_tools.propagate", icon="MATERIAL")
        layout.label(text="Propagation uses the active configured material or a unique selected source", icon="INFO")
        material = active_material(context)
        if material is None:
            layout.label(text="Select a mesh material", icon="INFO")
            return
        layout.label(text=material.name, icon="MATERIAL")
        try:
            assignment = nodes.assignment_node(material)
            if assignment is None:
                return
            data = model.unpack_sources(material)
            state = groups.current_state(context.scene, owner=(context.active_object, material))
            from . import group_ui
            layout.prop(material, "material_texture_preserve_order")
            layout.operator("material_tools.refresh_sources", icon="FILE_REFRESH")
            if not data.get("catalog"):
                layout.label(text="No retained source images; check the source folder or keep game textures", icon="INFO")
            layout.label(text="Mappings apply automatically; edit incorrect sources directly", icon="INFO")
            layout.label(text="Removed source files are excluded from mapping", icon="INFO")
            for role, label in model.ROLES.items():
                if role == "FTM" and data.get("game") == "ENDFIELD":
                    continue
                if role == "PACKED_PBR" and data.get("game") == "WUTHERING":
                    continue
                source = data.get("bindings", {}).get(role, "")
                record = data.get("catalog", {}).get(source, {})
                box = layout.box()
                row = box.row()
                row.alert = assignment.inputs[label].is_linked and not source and not model.inherits_game_source(data, role)
                row.label(text=iface_(label))
                name = Path(record["names"][0]).name if record.get("names") else (
                    iface_("Keep Game Texture") if model.inherits_game_source(data, role) else (source or iface_("Unassigned")))
                mapping_row = box.row(align=True)
                operator = mapping_row.operator("material_tools.choose_source", text=name, icon="IMAGE_DATA")
                operator.role = role
                operator = mapping_row.operator("material_tools.clear_source", text="", icon="LOOP_BACK")
                operator.role = role
                if model.inherits_game_source(data, role):
                    box.label(text="Keep Game Texture", icon="INFO")
                elif not source:
                    count = sum(role in model.role_hints(record, data.get("game", "ENDFIELD"))
                                for record in data.get("catalog", {}).values())
                    if count > 1:
                        box.label(text=iface_("Multiple originals match this role ({0}); choose an original").format(count), icon="INFO")
                group_ui.draw_role(box, context, material, role, state)
            layout.label(text="Packed maps are exported unchanged; preview is approximate", icon="INFO")
        except Exception as exc:
            layout.label(text=str(exc), icon="ERROR")


_CLASSES = (MATERIAL_OT_initialize, MATERIAL_OT_refresh_sources,
            MATERIAL_OT_choose_source, MATERIAL_OT_propagate, MATERIAL_PT_tools)


def register():
    bpy.types.Material.material_texture_preserve_order = bpy.props.BoolProperty(
        name="Preserve Draw Order",
        description="Keep this material as a sorting barrier during texture batching; use for transparent or other order-dependent game passes. Texture assignment and synchronization remain enabled",
        default=False)
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global _PREVIEWS
    del bpy.types.Material.material_texture_preserve_order
    if _PREVIEWS is not None:
        bpy.utils.previews.remove(_PREVIEWS)
        _PREVIEWS = None
    _PREVIEW_ICONS.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _ENUM_ITEMS.clear()
    _ENUM_STRINGS.clear()
