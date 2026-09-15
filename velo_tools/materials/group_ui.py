"""Actual-image path controls and transactional, explicit group operations."""
import json
from pathlib import Path

import bpy

from . import model, nodes, groups, ui
from ..i18n import iface_


def _path_getter(role):
    def get(self):
        try:
            image = nodes.role_image(self.id_data, role)
            return image.filepath if image else ""
        except (ValueError, KeyError, ReferenceError):
            return ""
    return get


class MATERIAL_PG_TexturePaths(bpy.types.PropertyGroup):
    pass


for _role in model.ROLES:
    MATERIAL_PG_TexturePaths.__annotations__ = getattr(MATERIAL_PG_TexturePaths, "__annotations__", {})
    MATERIAL_PG_TexturePaths.__annotations__[_role.lower()] = bpy.props.StringProperty(
        name="Texture File Path",
        description="Actual connected image path; use the adjacent button to replace images without changing unrelated image users",
        get=_path_getter(_role))


def _target(context, operator):
    if context.mode != "OBJECT":
        raise ValueError(iface_("Switch to Object Mode to edit material assignments"))
    name = getattr(operator, "object_name", "")
    obj = bpy.data.objects.get(name) if name else context.active_object
    if obj is None or obj.type != "MESH" or context.scene.objects.get(obj.name) != obj:
        raise ValueError(iface_("Select a mesh material"))
    name = getattr(operator, "material_name", "")
    material = bpy.data.materials.get(name) if name else obj.active_material
    if material is None or not any(slot.material == material for slot in obj.material_slots):
        raise ValueError(iface_("The target material changed; reopen the texture picker"))
    if getattr(operator, "role", "") not in model.ROLES:
        raise ValueError(iface_("Unknown texture role"))
    if not nodes.assignment_node(material):
        raise ValueError(iface_("Initialize this material first"))
    return obj, material


def _editable(obj):
    if obj.library and not obj.override_library:
        raise ValueError(iface_("Linked objects cannot receive local material assignments"))


def edit_source(context, obj, material, data):
    """A local source edit changes neither sibling users nor texture memberships."""
    _editable(obj)
    if data == model.unpack_sources(material):
        return
    replacement = material.copy()
    try:
        replacement[model.DATA_KEY] = json.dumps(data, sort_keys=True)
        plans = [(obj, index, material, replacement) for index, slot in enumerate(obj.material_slots)
                 if slot.material == material]
        ui._commit(plans, scene=context.scene, keep_backups=False)
    except Exception:
        ui._discard([replacement])
        raise


def replace_texture(context, obj, material, role, filepath, expected_group=""):
    """Load a new Image ID, stage all member copies, then commit once."""
    state = groups.snapshot(context.scene)
    group = groups.find(groups.current_state(context.scene), obj, material, role)
    if (expected_group == "LOCAL" and group is not None) or (
            expected_group not in ("", "LOCAL") and (group is None or group["id"] != expected_group)):
        raise ValueError(iface_("The sync group changed; reopen the texture picker"))
    members = group["members"] if group else [{"object": obj, "material": material}]
    for member in members:
        _editable(member["object"])
    raw_path = str(filepath).strip()
    if not raw_path:
        raise ValueError(iface_("Choose an existing image file"))
    path = Path(bpy.path.abspath(raw_path))
    if not path.is_file():
        raise ValueError(iface_("Image file not found: {0}").format(raw_path))
    if path.suffix.lower() not in model.IMAGE_EXTENSIONS:
        raise ValueError(iface_("Unsupported material image file type: {0}").format(path.suffix))
    image, copies, plans = None, {}, []
    try:
        # Never mutate a shared Image.filepath or reload another material's Image.
        image = bpy.data.images.load(str(path), check_existing=False)
        if image.source != "FILE" or min(image.size) <= 0:
            raise ValueError(iface_("Could not load the selected image"))
        image.colorspace_settings.name = "Non-Color" if role in {"NORMAL", "PACKED_PBR", "FTM", "MASK"} else "sRGB"
        if raw_path.startswith("//") and bpy.data.is_saved:
            image.filepath = raw_path
        for member in members:
            target, original = member["object"], member["material"]
            key = original.as_pointer()
            if key not in copies:
                replacement = original.copy()
                copies[key] = replacement
                nodes.connect_image(replacement, role, image)
                data = model.pin_original(model.unpack_sources(original), role)
                replacement[model.DATA_KEY] = json.dumps(data, sort_keys=True)
            for index, slot in enumerate(target.material_slots):
                if slot.material == original:
                    plans.append((target, index, original, copies[key]))
        if group:
            state = groups.replace_expected(state, group["id"], image, members=members)
        ui._commit(plans, scene=context.scene, group_state=state, keep_backups=False)
    except Exception:
        ui._discard(copies.values())
        if image is not None and image.users == 0:
            bpy.data.images.remove(image)
        raise
    return len(copies), len({member["object"].as_pointer() for member in members})


class MATERIAL_OT_replace_texture(bpy.types.Operator):
    bl_idname = "material_tools.replace_texture"
    bl_label = "Replace Texture"
    bl_description = "Replace this role on all explicitly linked members, or only the current material when independent; preserve original mappings and UV wiring"
    bl_options = {"REGISTER", "UNDO"}
    role: bpy.props.StringProperty(options={"HIDDEN"})
    object_name: bpy.props.StringProperty(options={"HIDDEN"})
    material_name: bpy.props.StringProperty(options={"HIDDEN"})
    group_id: bpy.props.StringProperty(options={"HIDDEN"})
    filepath: bpy.props.StringProperty(name="Image File", subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.dds;*.png;*.jpg;*.jpeg;*.tga;*.bmp", options={"HIDDEN"})

    def invoke(self, context, event):
        try:
            obj, material = _target(context, self)
            self.object_name, self.material_name = obj.name, material.name
            group = groups.find(groups.current_state(context.scene), obj, material, self.role)
            self.group_id = group["id"] if group else "LOCAL"
            image = nodes.role_image(material, self.role)
            if image and image.filepath:
                self.filepath = bpy.path.abspath(image.filepath, library=image.library)
        except Exception as exc:
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            obj, material = _target(context, self)
            materials, objects = replace_texture(context, obj, material, self.role, self.filepath, self.group_id)
        except Exception as exc:
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("Replaced {0} on {1} materials across {2} objects").format(
            iface_(model.ROLES[self.role]), materials, objects))
        return {"FINISHED"}


class MATERIAL_OT_detach_texture(bpy.types.Operator):
    bl_idname = "material_tools.detach_texture"
    bl_label = "Leave This Texture Sync"
    bl_description = "Remove only this object's current material role from batch synchronization; keep its image connection, original mapping and every other member unchanged"
    bl_options = {"REGISTER", "UNDO"}
    role: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        try:
            obj, material = _target(context, self)
            state = groups.detach(groups.snapshot(context.scene), obj, material, self.role)
            ui._commit([], scene=context.scene, group_state=state)
        except Exception as exc:
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("This texture role is independent; image connections and original mappings were kept"))
        return {"FINISHED"}


class MATERIAL_OT_clear_source(bpy.types.Operator):
    bl_idname = "material_tools.clear_source"
    bl_label = "Clear Original Mapping"
    bl_description = "Return this role to Unassigned, clearing manual opt-out too; keep image connections and sync membership. The next refresh may infer the original again"
    bl_options = {"REGISTER", "UNDO"}
    role: bpy.props.StringProperty(options={"HIDDEN"})

    def execute(self, context):
        try:
            obj, material = _target(context, self)
            edit_source(context, obj, material, model.clear_source(model.unpack_sources(material), self.role))
        except Exception as exc:
            self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, iface_("Original mapping cleared; refresh to infer it again. Image connections were kept"))
        return {"FINISHED"}


class MATERIAL_OT_texture_members(bpy.types.Operator):
    bl_idname = "material_tools.texture_members"
    bl_label = "Texture Sync Members"
    bl_description = "Show the explicit objects and materials affected by this role's batch replacement, excluding independent node edits"
    role: bpy.props.StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=650)

    def draw(self, context):
        obj, material = _target(context, self)
        group = groups.find(groups.current_state(context.scene), obj, material, self.role)
        members = group["members"] if group else [{"object": obj, "material": material}]
        self.layout.label(text=iface_("{0} material uses in this sync group").format(len(members)))
        for member in members[:64]:
            self.layout.label(text=member["object"].name + " / " + member["material"].name, icon="MATERIAL")
        if len(members) > 64:
            self.layout.label(text=iface_("{0} additional material uses").format(len(members) - 64))

    def execute(self, context):
        return {"FINISHED"}


def draw_role(layout, context, material, role, state):
    obj = context.active_object
    group = groups.find(state, obj, material, role)
    row = layout.row(align=True)
    readonly = row.row(align=True)
    readonly.enabled = False
    readonly.prop(material.material_texture_paths, role.lower(), text="")
    operator = row.operator("material_tools.replace_texture", text="", icon="FILE_FOLDER")
    operator.role = role
    operator.object_name, operator.material_name = obj.name, material.name
    image = nodes.role_image(material, role)
    if image is None:
        layout.label(text="No replacement image connected", icon="INFO")
    elif image.packed_file:
        layout.label(text="Packed image; the field shows its recorded file path", icon="PACKAGE")
    elif image.is_dirty:
        layout.label(text="Image has unsaved changes", icon="ERROR")
    if group:
        row = layout.row(align=True)
        count = len({member["material"].as_pointer() for member in group["members"]})
        operator = row.operator("material_tools.texture_members", text=iface_("Synced: {0} materials").format(count), icon="LINKED")
        operator.role = role
        operator = row.operator("material_tools.detach_texture", text="", icon="UNLINKED")
        operator.role = role
    else:
        layout.label(text="Independent texture", icon="UNLINKED")


_CLASSES = (MATERIAL_PG_TexturePaths, MATERIAL_OT_replace_texture, MATERIAL_OT_detach_texture,
            MATERIAL_OT_clear_source, MATERIAL_OT_texture_members)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Material.material_texture_paths = bpy.props.PointerProperty(type=MATERIAL_PG_TexturePaths)


def unregister():
    del bpy.types.Material.material_texture_paths
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
