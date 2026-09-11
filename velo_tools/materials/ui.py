"""Material authoring actions with copy-on-write object-slot transactions."""
import json
from pathlib import Path

import bpy
import bpy.utils.previews

from . import model, nodes, hooks
from ..i18n import iface_

_ENUM_ITEMS = []
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


def _commit(plans):
    """Object-linked slots isolate unselected users of a shared mesh/material."""
    changed = []
    backups = {}
    try:
        for obj, index, original, replacement in plans:
            slot = obj.material_slots[index]
            changed.append((obj, index, slot.link, original))
            slot.link = "OBJECT"
            slot.material = replacement
            if original.library is None:
                backups.setdefault(original, original.use_fake_user)
                original.use_fake_user = True
    except Exception:
        for obj, index, link, original in reversed(changed):
            slot = obj.material_slots[index]
            slot.material = original
            slot.link = link
        for material, fake_user in backups.items():
            material.use_fake_user = fake_user
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
        if identity in catalog:
            row.setdefault("DIFFUSE", set()).add(identity)
        for role, identity in bindings.items():
            if identity in catalog:
                row.setdefault(role, set()).add(identity)
    return result


def _resolved_mapping(material, game, comp, catalog, witnesses=None, images=None):
    if images is None:
        images = nodes.connected_images(material) if nodes.assignment_node(material) else {}
        diffuse = nodes.diffuse_image(material)
        if diffuse:
            images["DIFFUSE"] = diffuse
    return model.resolve_sources(game, comp, catalog, model.unpack_sources(material),
                                 _image_identities(images), witnesses)


def _refresh_mapping(context, material):
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
    material[model.DATA_KEY] = json.dumps(value, sort_keys=True)
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



def _source_items(self, context):
    global _ENUM_ITEMS
    material = bpy.data.materials.get(self.material_name) or active_material(context)
    data = model.unpack_sources(material) if material else {}
    items = [("NONE", iface_("Keep Game Texture"), iface_("Do not override this texture role"), "X", 0)]
    for number, (identity, record) in enumerate(data.get("catalog", {}).items(), 1):
        name = Path(record["names"][0]).name if record.get("names") else identity
        items.append((identity, name, " / ".join(record.get("formats", ())) + " | " + identity,
                      _PREVIEW_ICONS.get((material.name, identity), 0), number))
    _ENUM_ITEMS = items
    return _ENUM_ITEMS


def load_source_previews(context, material):
    """Generate thumbnails only on explicit picker invocation, never panel redraw."""
    global _PREVIEWS
    if _PREVIEWS is None:
        _PREVIEWS = bpy.utils.previews.new()
    data = model.unpack_sources(material)
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
    role: bpy.props.StringProperty(options={"HIDDEN"})
    source: bpy.props.EnumProperty(name="Original Texture", items=_source_items)
    bl_property = "source"

    def invoke(self, context, event):
        material = active_material(context)
        if material is None:
            return {"CANCELLED"}
        self.material_name = material.name
        try:
            data = _refresh_mapping(context, material)
        except Exception as exc:
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        load_source_previews(context, material)
        self.source = data.get("bindings", {}).get(self.role, "NONE")
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        self.layout.label(text=iface_(model.ROLES.get(self.role, "Original Texture")))
        self.layout.template_icon_view(self, "source", show_labels=True, scale=5.0)
        self.layout.prop(self, "source")
        self.layout.label(text="Choose the original image, not the replacement", icon="INFO")

    def execute(self, context):
        material = bpy.data.materials.get(self.material_name)
        if material is None or self.role not in model.ROLES:
            return {"CANCELLED"}
        data = model.unpack_sources(material)
        if self.source != "NONE" and self.source not in data.get("catalog", {}):
            return {"CANCELLED"}
        data.setdefault("manual", dict(data.get("bindings", {})))[self.role] = (
            "" if self.source == "NONE" else self.source)
        game, comp, catalog = source_context(context, context.active_object, material)
        diffuse = nodes.diffuse_image(material)
        value = model.resolve_sources(game, comp, catalog, data,
                                      _image_identities({"DIFFUSE": diffuse}) if diffuse else {})
        material[model.DATA_KEY] = json.dumps(value, sort_keys=True)
        return {"FINISHED"}


def _propagation_source(context, entries):
    """Prefer the active configured material; infer a unique selected donor otherwise."""
    active = active_material(context)
    if active and nodes.assignment_node(active):
        images = nodes.connected_images(active)
        if images.get("DIFFUSE") and any(role != "DIFFUSE" for role in images):
            return active, images
    donors = {}
    for _obj, _index, material in entries:
        if not nodes.assignment_node(material):
            continue
        images = nodes.connected_images(material)
        if not images.get("DIFFUSE") or not any(role != "DIFFUSE" for role in images):
            continue
        signature = tuple(sorted((role, repr(nodes.image_key(image))) for role, image in images.items()))
        donors.setdefault(signature, (material, images))
    if len(donors) == 1:
        return next(iter(donors.values()))
    if donors:
        raise ValueError(iface_("Several configured materials are selected; make the intended source material active"))
    raise ValueError(iface_("Connect a diffuse image and at least one other texture on a source material before propagation"))


class MATERIAL_OT_propagate(bpy.types.Operator):
    bl_idname = "material_tools.propagate"
    bl_label = "Propagate by Same Diffuse"
    bl_description = "Find the source and all matching diffuse images across selected material slots, copy other maps and automatically resolve each Component's original identities; the active configured material takes priority"
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
            source, images = _propagation_source(context, entries)
            diffuse = images["DIFFUSE"]
            key_diffuse = nodes.image_key(diffuse)
            witness_entries = list(_selected_slots(context, used_only=False))
            if not any(material == source for _obj, _index, material in witness_entries):
                witness_entries.append((context.active_object, -1, source))
            witnesses = _mapping_witnesses(context, witness_entries, cache).get(key_diffuse, {})
            wanted = {role: image for role, image in images.items() if role != "DIFFUSE"}
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
                                          {**target_images, **additions})
                additions = {role: image for role, image in additions.items()
                             if not model.inherits_game_source(value, role)}
                mapping_changed = existing and old != value
                if not additions and not mapping_changed:
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
            _commit(plans)
        except Exception as exc:
            _discard(copies.values())
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("Source {0}: matched {1} materials, connected {2} maps, refreshed {3} mappings, {4} unresolved roles").format(
            source.name, len(matched), filled, mapped, unresolved))
        if not copies:
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
                operator = box.operator("material_tools.choose_source", text=name, icon="IMAGE_DATA")
                operator.role = role
                if model.inherits_game_source(data, role):
                    box.label(text="Keep Game Texture", icon="INFO")
                socket = assignment.inputs[label]
                if socket.is_linked:
                    image = nodes.image_from_socket(socket)
                    if image:
                        box.label(text=image.name, icon="TEXTURE")
            layout.label(text="Packed maps are exported unchanged; preview is approximate", icon="INFO")
        except Exception as exc:
            layout.label(text=str(exc), icon="ERROR")


_CLASSES = (MATERIAL_OT_initialize, MATERIAL_OT_refresh_sources,
            MATERIAL_OT_choose_source, MATERIAL_OT_propagate, MATERIAL_PT_tools)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global _PREVIEWS
    if _PREVIEWS is not None:
        bpy.utils.previews.remove(_PREVIEWS)
        _PREVIEWS = None
    _PREVIEW_ICONS.clear()
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
    _ENUM_ITEMS.clear()
