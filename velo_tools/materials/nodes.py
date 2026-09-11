"""Persistent shader groups and conservative semantic image connections."""
import json
import os
from pathlib import Path

import bpy

from . import model
from ..i18n import iface_


def assignment_node(material):
    if material is None or not material.use_nodes or not material.node_tree:
        return None
    found = [node for node in material.node_tree.nodes
             if node.type == "GROUP" and node.get(model.NODE_KEY) == model.SCHEMA]
    if len(found) > 1:
        raise ValueError(iface_("More than one texture assignment node in this material"))
    return found[0] if found else None


def image_from_socket(socket):
    """Only direct image/reroute chains have unambiguous file export semantics."""
    seen = set()
    while socket and socket.is_linked:
        link = socket.links[0]
        node = link.from_node
        if node.as_pointer() in seen:
            raise ValueError(iface_("Cyclic texture connection"))
        seen.add(node.as_pointer())
        if node.type == "TEX_IMAGE" and node.image is not None:
            if link.from_socket.name != "Color":
                raise ValueError(iface_("Connect the image Color output to a texture input"))
            return node.image
        if node.type != "REROUTE":
            raise ValueError(iface_("Texture inputs support Image Texture nodes and reroutes; bake procedural maps first"))
        socket = node.inputs[0]
    return None


def connected_images(material):
    node = assignment_node(material)
    if not node:
        return {}
    output = next((n for n in material.node_tree.nodes
                   if n.type == "OUTPUT_MATERIAL" and n.is_active_output), None)
    if output is None or not any(link.from_node == node for link in output.inputs["Surface"].links):
        raise ValueError(iface_("Connect the texture assignment Shader output to the active Material Output"))
    return {role: image for role, label in model.ROLES.items()
            if (image := image_from_socket(node.inputs[label])) is not None}


def image_key(image):
    if image is None:
        return None
    if image.source == "FILE" and image.filepath and not image.is_dirty:
        return os.path.normcase(str(Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()))
    return ("image", image.as_pointer())


def _new_interface(tree, name, direction, socket_type):
    if hasattr(tree, "interface"):
        return tree.interface.new_socket(name=name, in_out=direction, socket_type=socket_type)
    return (tree.inputs if direction == "INPUT" else tree.outputs).new(socket_type, name)


def shader_group():
    for tree in bpy.data.node_groups:
        if tree.get(model.NODE_KEY) == model.SCHEMA:
            return tree
    tree = bpy.data.node_groups.new("Material Texture Inputs", "ShaderNodeTree")
    for role, label in model.ROLES.items():
        socket = _new_interface(tree, label, "INPUT", "NodeSocketColor")
        socket.description = iface_(label)
        socket.default_value = ((0.8, 0.8, 0.8, 1) if role == "DIFFUSE" else
                                (0.5, 0.5, 1, 1) if role == "NORMAL" else (0, 0, 0, 1))
    alpha = _new_interface(tree, "Alpha", "INPUT", "NodeSocketFloat")
    alpha.default_value = 1
    _new_interface(tree, "Shader", "OUTPUT", "NodeSocketShader")
    inputs = tree.nodes.new("NodeGroupInput")
    inputs.location = (-400, 0)
    bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
    normal = tree.nodes.new("ShaderNodeNormalMap")
    normal.location = (-180, -250)
    output = tree.nodes.new("NodeGroupOutput")
    output.location = (330, 0)
    tree.links.new(inputs.outputs["Diffuse"], bsdf.inputs["Base Color"])
    tree.links.new(inputs.outputs["Alpha"], bsdf.inputs["Alpha"])
    tree.links.new(inputs.outputs["Normal"], normal.inputs["Color"])
    tree.links.new(normal.outputs["Normal"], bsdf.inputs["Normal"])
    emission = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
    if emission is not None:
        tree.links.new(inputs.outputs["Emission"], emission)
    tree.links.new(bsdf.outputs["BSDF"], output.inputs["Shader"])
    tree[model.NODE_KEY] = model.SCHEMA
    return tree


def _group_socket(sockets, reference):
    return next((socket for socket in sockets if socket.identifier == reference.identifier),
                sockets.get(reference.name))


def _diffuse_reference(material):
    """Trace only active base-color paths, including nested shader groups."""
    if not material or not material.use_nodes or not material.node_tree:
        return None
    tree = material.node_tree
    assignment = assignment_node(material)
    if assignment:
        socket = assignment.inputs["Diffuse"]
        seen = set()
        while socket.is_linked:
            if socket.as_pointer() in seen:
                return None
            seen.add(socket.as_pointer())
            link = socket.links[0]
            if link.from_node.type == "TEX_IMAGE" and link.from_node.image:
                return link.from_node, ()
            if link.from_node.type != "REROUTE":
                return None
            socket = link.from_node.inputs[0]
        return None
    mmd = next((node for node in tree.nodes if node.type == "TEX_IMAGE"
                and node.name.lower() == "mmd_base_tex" and node.image), None)
    if mmd:
        return mmd, ()
    found, visited = [], set()

    def input_links(socket, stack, mode):
        if socket:
            for link in socket.links:
                output(link.from_socket, stack, mode)

    def output(socket, stack, mode):
        key = (socket.as_pointer(), tuple(group.as_pointer() for group in stack), mode)
        if key in visited or len(visited) >= 512:
            return
        visited.add(key)
        node = socket.node
        if node.type == "GROUP" and node.node_tree:
            if node in stack:
                return
            out = next((item for item in node.node_tree.nodes
                        if item.type == "GROUP_OUTPUT" and item.is_active_output), None)
            if out:
                input_links(_group_socket(out.inputs, socket), stack + (node,), mode)
        elif node.type == "GROUP_INPUT" and stack:
            input_links(_group_socket(stack[-1].inputs, socket), stack[:-1], mode)
        elif node.type == "REROUTE":
            input_links(node.inputs[0], stack, mode)
        elif mode == "shader":
            color = node.inputs.get("Base Color") if node.type == "BSDF_PRINCIPLED" else (
                node.inputs.get("Color") if node.type == "BSDF_DIFFUSE" else None)
            if color:
                input_links(color, stack, "color")
            elif node.type in {"MIX_SHADER", "ADD_SHADER"}:
                for value in node.inputs:
                    if value.type == "SHADER":
                        input_links(value, stack, mode)
        elif node.type == "TEX_IMAGE" and node.image and socket.name == "Color":
            found.append((node, stack))
        else:
            # Do not mistake a factor, roughness or normal-map input for diffuse.
            names = {"Color", "Color1", "Color2", "A", "B", "Image"}
            for value in node.inputs:
                if value.name in names and value.type == "RGBA" and not value.is_unavailable:
                    input_links(value, stack, "color")

    for node in tree.nodes:
        if node.type == "OUTPUT_MATERIAL" and node.is_active_output:
            input_links(node.inputs.get("Surface"), (), "shader")
    if not found:
        images = [node for node in tree.nodes if node.type == "TEX_IMAGE" and node.image]
        return (images[0], ()) if len(images) == 1 else None
    keys = {image_key(node.image) for node, _stack in found}
    return found[0] if len(keys) == 1 else None


def _old_diffuse(material):
    found = _diffuse_reference(material)
    return found[0] if found else None


def diffuse_image(material):
    found = _diffuse_reference(material)
    return found[0].image if found else None


def _flatten_image_branch(reference, target_tree):
    """Copy a nested image/vector branch without mutating shared node groups."""
    source, stack = reference
    copied = {}

    def copy_output(socket, groups):
        node = socket.node
        if node.type == "GROUP" and node.node_tree:
            if node in groups:
                raise ValueError(iface_("Cyclic texture connection"))
            out = next((item for item in node.node_tree.nodes if item.type == "GROUP_OUTPUT" and item.is_active_output), None)
            inner = _group_socket(out.inputs, socket) if out else None
            if inner and inner.is_linked:
                return copy_output(inner.links[0].from_socket, groups + (node,))
            raise ValueError(iface_("Bake the nested diffuse vector input before initializing this material"))
        if node.type == "GROUP_INPUT" and groups:
            outer = _group_socket(groups[-1].inputs, socket)
            if outer and outer.is_linked:
                return copy_output(outer.links[0].from_socket, groups[:-1])
            raise ValueError(iface_("Bake the nested diffuse vector input before initializing this material"))
        key = (node.as_pointer(), tuple(group.as_pointer() for group in groups))
        if key not in copied:
            clone = target_tree.nodes.new(node.bl_idname)
            copied[key] = clone
            for prop in node.bl_rna.properties:
                if prop.is_readonly or prop.identifier in {"rna_type", "name", "parent", "select", "location"}:
                    continue
                if prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
                    continue
                try:
                    setattr(clone, prop.identifier, getattr(node, prop.identifier))
                except (AttributeError, TypeError):
                    pass
            if node.type == "TEX_IMAGE":
                clone.image = node.image
            for old_socket, new_socket in zip(node.inputs, clone.inputs):
                if hasattr(old_socket, "default_value"):
                    new_socket.default_value = old_socket.default_value
                if old_socket.is_linked:
                    target_tree.links.new(copy_output(old_socket.links[0].from_socket, groups), new_socket)
        output_index = list(node.outputs).index(socket)
        return copied[key].outputs[output_index]

    return copy_output(source.outputs["Color"], stack).node



def initialize_copy(material, game, component, catalog):
    """Return an unattached copy; callers commit object slots transactionally."""
    result = material.copy()
    try:
        result.use_nodes = True
        tree = result.node_tree
        reference = _diffuse_reference(result)
        diffuse = reference[0] if reference else None
        if reference and reference[1]:
            diffuse = _flatten_image_branch(reference, tree)
        shader = next((node for node in tree.nodes if node.type == "BSDF_PRINCIPLED"), None)
        base_color = tuple(shader.inputs["Base Color"].default_value) if shader else tuple(material.diffuse_color)
        alpha = shader.inputs["Alpha"].default_value if shader else material.diffuse_color[3]
        use_alpha = bool(shader and diffuse and any(link.from_node == diffuse
                         and link.from_socket.name == "Alpha" for link in shader.inputs["Alpha"].links))
        # Preserve the diffuse image's complete UV/vector subgraph, not its shader.
        keep = set()
        def upstream(node):
            if node.as_pointer() in keep:
                return
            keep.add(node.as_pointer())
            for socket in node.inputs:
                for link in socket.links:
                    upstream(link.from_node)
        if diffuse:
            upstream(diffuse)
        for node in list(tree.nodes):
            if node.as_pointer() not in keep:
                tree.nodes.remove(node)
        assignment = tree.nodes.new("ShaderNodeGroup")
        assignment.node_tree = shader_group()
        assignment[model.NODE_KEY] = model.SCHEMA
        assignment.label = iface_("Texture Assignment")
        assignment.width = 260
        assignment.location = (0, 0)
        assignment.inputs["Diffuse"].default_value = base_color
        assignment.inputs["Alpha"].default_value = alpha
        output = tree.nodes.new("ShaderNodeOutputMaterial")
        output.location = (360, 0)
        tree.links.new(assignment.outputs["Shader"], output.inputs["Surface"])
        identity = ""
        if diffuse and diffuse.image:
            tree.links.new(diffuse.outputs["Color"], assignment.inputs["Diffuse"])
            diffuse.location = (-340, 0)
            diffuse.name = "Diffuse Image"
            diffuse.label = iface_("Diffuse")
            if use_alpha:
                tree.links.new(diffuse.outputs["Alpha"], assignment.inputs["Alpha"])
            identity = model.texture_identity(diffuse.image.filepath or diffuse.image.name)
        data = model.resolve_sources(game, component, catalog,
                                     image_identities={"DIFFUSE": identity} if identity else {})
        result[model.DATA_KEY] = json.dumps(data, sort_keys=True)
        result["material_source_backup"] = material.name
        return result
    except Exception:
        bpy.data.materials.remove(result)
        raise


def connect_image(material, role, image):
    node = assignment_node(material)
    if node is None:
        raise ValueError(iface_("Initialize this material first"))
    socket = node.inputs[model.ROLES[role]]
    for link in list(socket.links):
        material.node_tree.links.remove(link)
    if image is None:
        return
    texture = material.node_tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.label = iface_(model.ROLES[role])
    texture.location = (-360, -list(model.ROLES).index(role) * 280)
    material.node_tree.links.new(texture.outputs["Color"], socket)
