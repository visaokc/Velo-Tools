"""Smooth-normal octahedral UV generation for selected mesh objects."""

import math

import bpy
import mathutils
from mathutils import Vector


def unit_vector_to_octahedron(normal):
    """Project a unit vector onto an octahedral plane."""
    normal = normal.copy()
    if normal.length_squared <= 1e-10:
        return Vector((0.0, 0.0))
    normal.normalize()

    l1_norm = abs(normal.x) + abs(normal.y) + abs(normal.z)
    if l1_norm < 1e-10:
        return Vector((0.0, 0.0))

    x = normal.x / l1_norm
    y = normal.y / l1_norm
    if normal.z < 0:
        x, y = (
            (1.0 - abs(y)) * math.copysign(1.0, x),
            (1.0 - abs(x)) * math.copysign(1.0, y),
        )
    return Vector((x, y))


def calculate_smooth_normals(mesh):
    """Calculate angle-weighted smooth normals for every mesh vertex."""
    vertex_normals = {
        vertex.index: Vector((0.0, 0.0, 0.0))
        for vertex in mesh.vertices
    }
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
            vertex_normals[vertex.index] += polygon.normal * weight

    for normal in vertex_normals.values():
        if normal.length > 1e-6:
            normal.normalize()
    return vertex_normals


class MESH_TOOLS_OT_smooth_normals_octahedral_uv(bpy.types.Operator):
    bl_idname = "mesh_tools.smooth_normals_octahedral_uv"
    bl_label = "平滑法线-八面体UV"
    bl_description = (
        "对所有选中物体\n"
        "平滑法线在切线空间的坐标，投射八面体展开平面\n"
        "存储在TEXCOORD1\n"
        "为了计算切线空间，必须要有一个正常展开的UV"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(obj.type == 'MESH' for obj in context.selected_objects)

    def execute(self, context):
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        processed_count = 0
        for obj in context.selected_objects:
            if self.process_object(obj):
                processed_count += 1

        context.view_layer.update()
        if processed_count == 0:
            self.report({'WARNING'}, "没有处理任何网格物体，请确保选中了网格物体")
            return {'CANCELLED'}

        self.report({'INFO'}, f"切线空间八面体UV映射完成！共处理 {processed_count} 个网格物体")
        return {'FINISHED'}

    @staticmethod
    def process_object(obj):
        if obj.type != 'MESH':
            return False

        mesh = obj.data
        if mesh.uv_layers:
            mesh.uv_layers.active_index = 0
        else:
            mesh.uv_layers.new(name="UVMap")

        smooth_normals = calculate_smooth_normals(mesh)
        mesh.calc_tangents()

        target_name = "TEXCOORD1.xy"
        target_uv = mesh.uv_layers.get(target_name)
        if target_uv is None:
            target_uv = mesh.uv_layers.new(name=target_name)

        for polygon in mesh.polygons:
            for loop_index in polygon.loop_indices:
                loop = mesh.loops[loop_index]
                tangent_matrix = mathutils.Matrix((
                    loop.tangent,
                    loop.bitangent,
                    loop.normal,
                )).transposed()
                try:
                    tangent_normal = tangent_matrix.inverted() @ smooth_normals[loop.vertex_index]
                    tangent_normal.normalize()
                except ValueError:
                    tangent_normal = Vector((0.0, 0.0, 1.0))

                octahedral = unit_vector_to_octahedron(tangent_normal)
                target_uv.data[loop_index].uv = (octahedral.x, octahedral.y + 1.0)

        mesh.free_tangents()
        return True


_classes = (MESH_TOOLS_OT_smooth_normals_octahedral_uv,)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
