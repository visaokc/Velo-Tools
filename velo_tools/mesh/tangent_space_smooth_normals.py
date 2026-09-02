"""Generate Endfield-compatible tangent-space smooth-normal attributes."""

import math

import bpy
from mathutils import Vector

from velo_tools.i18n import iface_


UV_ATTRIBUTE_NAME = "TEXCOORD4.xy"
COLOR_ATTRIBUTE_NAME = "COLOR"


def _position_key(vertex):
    return tuple(vertex.co)


def calculate_welded_smooth_normals(mesh):
    """Calculate angle-weighted normals shared by exact-position vertices."""
    accumulated = {}
    for polygon in mesh.polygons:
        vertices = [mesh.vertices[index] for index in polygon.vertices]
        for index, vertex in enumerate(vertices):
            next_edge = vertices[(index + 1) % len(vertices)].co - vertex.co
            previous_edge = vertices[(index - 1) % len(vertices)].co - vertex.co
            if next_edge.length > 1e-6 and previous_edge.length > 1e-6:
                next_edge.normalize()
                previous_edge.normalize()
                weight = math.acos(max(-1.0, min(1.0, next_edge.dot(previous_edge))))
            else:
                weight = 0.0
            key = _position_key(vertex)
            accumulated[key] = accumulated.get(key, Vector((0.0, 0.0, 0.0))) + polygon.normal * weight

    smooth_normals = {}
    for key, normal in accumulated.items():
        if normal.length > 1e-6:
            normal.normalize()
        smooth_normals[key] = normal
    return smooth_normals


def _source_uv_layer(mesh):
    source = mesh.uv_layers.get("TEXCOORD.xy")
    if source is not None:
        return source
    return next((layer for layer in mesh.uv_layers if layer.name != UV_ATTRIBUTE_NAME), None)


def _activate_source_uv(mesh, source_uv):
    for index, layer in enumerate(mesh.uv_layers):
        if layer == source_uv:
            mesh.uv_layers.active_index = index
            return


def _calculate_loop_data(mesh):
    source_uv = _source_uv_layer(mesh)
    if source_uv is None:
        raise ValueError("A regular UV map is required to calculate tangent space")

    smooth_normals = calculate_welded_smooth_normals(mesh)
    mesh.calc_tangents(uvmap=source_uv.name)
    result = []
    for loop in mesh.loops:
        smooth_normal = smooth_normals.get(_position_key(mesh.vertices[loop.vertex_index]), loop.normal)
        result.append((
            max(-1.0, min(1.0, smooth_normal.dot(loop.tangent))),
            max(-1.0, min(1.0, -smooth_normal.dot(loop.bitangent))),
        ))
    mesh.free_tangents()
    return result


def generate_uv_data(mesh):
    source_uv = _source_uv_layer(mesh)
    loop_data = _calculate_loop_data(mesh)
    target = mesh.uv_layers.get(UV_ATTRIBUTE_NAME)
    if target is None:
        target = mesh.uv_layers.new(name=UV_ATTRIBUTE_NAME)
    for loop_index, value in enumerate(loop_data):
        target.data[loop_index].uv = (value[0], 1.0 - value[1])
    _activate_source_uv(mesh, source_uv)


def generate_color_data(mesh):
    source_uv = _source_uv_layer(mesh)
    loop_data = _calculate_loop_data(mesh)
    target = mesh.color_attributes.get(COLOR_ATTRIBUTE_NAME)
    if target is not None and (target.domain != 'CORNER' or target.data_type != 'FLOAT_COLOR'):
        mesh.color_attributes.remove(target)
        target = None
    if target is None:
        target = mesh.color_attributes.new(
            name=COLOR_ATTRIBUTE_NAME,
            type='FLOAT_COLOR',
            domain='CORNER',
        )
    for loop_index, value in enumerate(loop_data):
        target.data[loop_index].color = (value[0], value[1], 0.0, 0.0)
    _activate_source_uv(mesh, source_uv)


class _GenerateSmoothNormalDataBase:
    bl_options = {'REGISTER', 'UNDO'}

    generator = None

    @classmethod
    def poll(cls, context):
        return any(obj.type == 'MESH' for obj in context.selected_objects)

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        processed = 0
        failures = []
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            try:
                self.generator(obj.data)
                processed += 1
            except ValueError as exc:
                failures.append(f"{obj.name}: {iface_(str(exc))}")

        context.view_layer.update()
        if processed == 0:
            if failures:
                self.report({'WARNING'}, iface_('Could not generate data: {0}').format(failures[0]))
            else:
                self.report({'WARNING'}, iface_('No mesh objects processed, please make sure mesh objects are selected'))
            return {'CANCELLED'}

        if failures:
            self.report({'WARNING'}, iface_('Generated data for {0} mesh objects; skipped {1}').format(processed, len(failures)))
        else:
            self.report({'INFO'}, iface_('Generated tangent-space smooth-normal data for {0} mesh objects').format(processed))
        return {'FINISHED'}


class MESH_TOOLS_OT_generate_smooth_normal_uv(_GenerateSmoothNormalDataBase, bpy.types.Operator):
    bl_idname = "mesh_tools.generate_smooth_normal_uv"
    bl_label = "Generate Smooth Normal TEXCOORD4"
    bl_description = (
        "Generate Endfield tangent-space smooth-normal X/Y data and store it in TEXCOORD4.xy"
    )

    generator = staticmethod(generate_uv_data)


class MESH_TOOLS_OT_generate_smooth_normal_color(_GenerateSmoothNormalDataBase, bpy.types.Operator):
    bl_idname = "mesh_tools.generate_smooth_normal_color"
    bl_label = "Generate Smooth Normal COLOR"
    bl_description = (
        "Generate Endfield tangent-space smooth-normal X/Y data in COLOR R/G; only use it when the target COLOR semantic stores smooth normals"
    )

    generator = staticmethod(generate_color_data)


_classes = (
    MESH_TOOLS_OT_generate_smooth_normal_uv,
    MESH_TOOLS_OT_generate_smooth_normal_color,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
