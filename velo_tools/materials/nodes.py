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
    if image.source == "FILE" and image.filepath and not image.packed_file:
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


def _old_diffuse(material):
    if material.use_nodes and material.node_tree:
        nodes = material.node_tree.nodes
        for node in nodes:
            if node.type == "TEX_IMAGE" and node.name.lower() == "mmd_base_tex" and node.image:
                return node
        outputs = [n for n in nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output]
        for output in outputs:
            for link in output.inputs["Surface"].links:
                shader = link.from_node
                if shader.type == "BSDF_PRINCIPLED":
                    socket = shader.inputs["Base Color"]
                    while socket.is_linked:
                        source = socket.links[0].from_node
                        if source.type == "TEX_IMAGE":
                            return source
                        if source.type != "REROUTE":
                            break
                        socket = source.inputs[0]
        images = [n for n in nodes if n.type == "TEX_IMAGE" and n.image]
        if len(images) == 1:
            return images[0]
    return None


def initialize_copy(material, game, component, catalog):
    """Return an unattached copy; callers commit object slots transactionally."""
    result = material.copy()
    try:
        result.use_nodes = True
        tree = result.node_tree
        diffuse = _old_diffuse(result)
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
        result[model.DATA_KEY] = model.pack_sources(
            game, component, catalog, model.infer_bindings(catalog, game, identity))
        data = model.unpack_sources(result)
        data["confirmed"] = ["DIFFUSE"] if identity and identity in catalog else []
        result[model.DATA_KEY] = json.dumps(data)
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
