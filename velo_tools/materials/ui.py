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


def source_context(context, obj, material):
    game = context.scene.velo_tools.active_game
    cfg = getattr(context.scene, hooks.SETTINGS[game])
    comp = model.component_id(material.name) if material else None
    if comp is None:
        comp = model.component_id(obj.name)
    catalog = {}
    if comp is not None and cfg.object_source_folder.strip():
        try:
            catalog = model.read_evidence(Path(bpy.path.abspath(cfg.object_source_folder)), comp)
        except FileNotFoundError:
            # Node initialization also works before an extraction is configured.
            catalog = {}
    return game, comp, catalog


def _selected_slots(context):
    if context.mode != "OBJECT":
        raise ValueError(iface_("Switch to Object Mode to edit material assignments"))
    for obj in context.selected_objects:
        if obj.type != "MESH":
            continue
        if obj.library and not obj.override_library:
            raise ValueError(iface_("Linked objects cannot receive local material assignments"))
        used = {polygon.material_index for polygon in obj.data.polygons}
        for index, slot in enumerate(obj.material_slots):
            if slot.material and index in used:
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
        copies, plans = {}, []
        try:
            for obj, index, material in _selected_slots(context):
                if nodes.assignment_node(material):
                    continue
                game, comp, catalog = source_context(context, obj, material)
                key = (material.as_pointer(), game, comp)
                if key not in copies:
                    copies[key] = nodes.initialize_copy(material, game, comp, catalog)
                plans.append((obj, index, material, copies[key]))
            _commit(plans)
        except Exception as exc:
            _discard(copies.values())
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("Initialized {0} materials; originals retained as backups").format(len(copies)))
        return {"FINISHED"}


class MATERIAL_OT_refresh_sources(bpy.types.Operator):
    bl_idname = "material_tools.refresh_sources"
    bl_label = "Refresh Source Mapping"
    bl_description = "Read original extraction evidence for the active material; keep valid choices and leave ambiguous texture roles unresolved"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        material = active_material(context)
        try:
            if not nodes.assignment_node(material):
                raise ValueError(iface_("Initialize this material first"))
            game, comp, catalog = source_context(context, context.active_object, material)
            if not catalog:
                raise ValueError(iface_("Set an original source folder and Component name before mapping textures"))
            old = model.unpack_sources(material)
            diffuse = nodes.connected_images(material).get("DIFFUSE")
            identity = old.get("bindings", {}).get("DIFFUSE", "") or (model.texture_identity(diffuse.filepath or diffuse.name) if diffuse else "")
            bindings = model.infer_bindings(catalog, game, identity)
            bindings.update({role: source for role, source in old.get("bindings", {}).items()
                             if source in catalog and old.get("game") == game})
            value = json.loads(model.pack_sources(game, comp, catalog, bindings))
            value["confirmed"] = [role for role in old.get("confirmed", ())
                                  if old.get("game") == game and old.get("bindings", {}).get(role) == bindings.get(role)]
            material[model.DATA_KEY] = json.dumps(value)
        except Exception as exc:
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("Source mapping refreshed; review format-based suggestions"))
        return {"FINISHED"}


class MATERIAL_OT_confirm_sources(bpy.types.Operator):
    bl_idname = "material_tools.confirm_sources"
    bl_label = "Confirm Suggested Mapping"
    bl_description = "Confirm that the displayed original textures have the indicated semantic roles; file formats alone do not prove their meaning"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        material = active_material(context)
        if material is None or not nodes.assignment_node(material):
            return {"CANCELLED"}
        value = model.unpack_sources(material)
        value["confirmed"] = list(value.get("bindings", {}))
        material[model.DATA_KEY] = json.dumps(value)
        self.report({"INFO"}, iface_("Original texture mapping confirmed"))
        return {"FINISHED"}


def _source_items(self, context):
    global _ENUM_ITEMS
    material = bpy.data.materials.get(self.material_name) or active_material(context)
    data = model.unpack_sources(material) if material else {}
    items = [("NONE", iface_("Unassigned"), iface_("Leave this role unresolved"), "X", 0)]
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
    files = {model.texture_identity(path.name): path for path in folder.iterdir()
             if path.is_file() and path.suffix.lower() in {".dds", ".png", ".jpg", ".jpeg", ".tga", ".bmp"}}
    for identity in data.get("catalog", {}):
        path = files.get(identity)
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
        load_source_previews(context, material)
        data = model.unpack_sources(material)
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
        if self.source == "NONE":
            data.get("bindings", {}).pop(self.role, None)
        elif self.source in data.get("catalog", {}):
            data.setdefault("bindings", {})[self.role] = self.source
        else:
            return {"CANCELLED"}
        if self.role == "DIFFUSE" and self.source != "NONE":
            suggestions = model.infer_bindings(data.get("catalog", {}), data.get("game"), self.source)
            for role, identity in suggestions.items():
                if role not in data.get("confirmed", ()) and role != "DIFFUSE":
                    data.setdefault("bindings", {})[role] = identity
        data["confirmed"] = sorted(set(data.get("confirmed", ())) | {self.role})
        material[model.DATA_KEY] = json.dumps(data)
        return {"FINISHED"}


class MATERIAL_OT_propagate(bpy.types.Operator):
    bl_idname = "material_tools.propagate"
    bl_label = "Propagate by Same Diffuse"
    bl_description = "Copy non-diffuse image inputs from the active material to selected materials with the exact same diffuse image; resolve original mappings independently for each Component"
    bl_options = {"REGISTER", "UNDO"}
    overwrite: bpy.props.BoolProperty(
        name="Replace Existing Connections",
        description="Replace populated non-diffuse inputs; otherwise only fill empty inputs",
        default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        copies, plans = {}, []
        try:
            source = active_material(context)
            images = nodes.connected_images(source)
            diffuse = images.get("DIFFUSE")
            if diffuse is None:
                raise ValueError(iface_("The active material needs a connected diffuse image"))
            wanted = {role: image for role, image in images.items() if role != "DIFFUSE"}
            for obj, index, material in _selected_slots(context):
                if material == source:
                    continue
                existing = nodes.assignment_node(material)
                target_images = nodes.connected_images(material) if existing else {}
                old = nodes._old_diffuse(material) if not existing else None
                target_diffuse = target_images.get("DIFFUSE") if existing else (old.image if old else None)
                if nodes.image_key(target_diffuse) != nodes.image_key(diffuse):
                    continue
                additions = {role: image for role, image in wanted.items()
                             if (self.overwrite or role not in target_images)
                             and nodes.image_key(target_images.get(role)) != nodes.image_key(image)}
                if not additions:
                    continue
                game, comp, catalog = source_context(context, obj, material)
                key = (material.as_pointer(), game, comp)
                if key not in copies:
                    copy = material.copy() if existing else nodes.initialize_copy(material, game, comp, catalog)
                    copies[key] = copy
                    for role, image in additions.items():
                        nodes.connect_image(copy, role, image)
                plans.append((obj, index, material, copies[key]))
            _commit(plans)
        except Exception as exc:
            _discard(copies.values())
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("Updated {0} matching materials; review each target source mapping").format(len(copies)))
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
                layout.label(text="Source evidence missing; refresh mapping before export", icon="ERROR")
            layout.label(text="Formats are hints; verify original textures", icon="INFO")
            layout.operator("material_tools.confirm_sources", icon="CHECKMARK")
            for role, label in model.ROLES.items():
                if role == "FTM" and data.get("game") == "ENDFIELD":
                    continue
                if role == "PACKED_PBR" and data.get("game") == "WUTHERING":
                    continue
                source = data.get("bindings", {}).get(role, "")
                record = data.get("catalog", {}).get(source, {})
                box = layout.box()
                row = box.row()
                row.alert = assignment.inputs[label].is_linked and (not source or role not in data.get("confirmed", ()))
                row.label(text=iface_(label))
                name = Path(record["names"][0]).name if record.get("names") else (source or iface_("Unassigned"))
                operator = box.operator("material_tools.choose_source", text=name, icon="IMAGE_DATA")
                operator.role = role
                if source and role not in data.get("confirmed", ()):
                    box.label(text="Suggestion: confirmation required", icon="QUESTION")
                socket = assignment.inputs[label]
                if socket.is_linked:
                    image = nodes.image_from_socket(socket)
                    if image:
                        box.label(text=image.name, icon="TEXTURE")
            layout.label(text="Packed maps are exported unchanged; preview is approximate", icon="INFO")
        except Exception as exc:
            layout.label(text=str(exc), icon="ERROR")


_CLASSES = (MATERIAL_OT_initialize, MATERIAL_OT_refresh_sources, MATERIAL_OT_confirm_sources,
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
