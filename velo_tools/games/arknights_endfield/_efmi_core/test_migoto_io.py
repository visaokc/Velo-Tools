import time
import numpy
import hashlib
import copy
import shutil
import struct
import json

# # import imageio.v3 as iio
# # import numpy as np

# from dataclasses import dataclass, field
# from pathlib import Path

# from migoto_model.migoto_format import MigotoFormat
# from migoto_model.log_model.log_model import FrameDumpLog
# from migoto_model.types import SlotType, ShaderType, ResourceSlot
# from migoto_model.frame_model.calls import ShaderCall
# from migoto_model.frame_model.frame_model import DumpModel, ParseDumpModelConfig
# from migoto_model.frame_model.resources import Resource, ConstantBuffer, IndexBuffer, VertexBuffer, ResourceStorage
# from migoto_model.frame_model.api_calls.draw_calls import DrawCall, DrawIndexedInstanced
# from data_model.byte_buffer import BufferLayout, BufferSemantic, AbstractSemantic, Semantic, NumpyBuffer
# from data_model.dxgi_format import DXGIFormat
# from migoto_model.migoto_mesh import MigotoMesh
# from extract_frame_data.metadata_format import ExtractedObject, ExtractedObjectComponent, ExtractedObjectShapeKeys, ObjectRotation


# from extract_frame_data.extract_frame_data import extract_frame_data

# @dataclass
# class SemanticVertexData:
#     semantic: BufferSemantic
#     layout: BufferLayout
#     resource: VertexBuffer


# @dataclass
# class RawComponent:
#     vertex_offset: int
#     vertex_count: int
#     shader_calls: list[ShaderCall] = field(default_factory=list)

#     semantic_remap = {
#         BufferSemantic(
#             AbstractSemantic(Semantic.Normal, 0), format=DXGIFormat.R32_FLOAT, input_slot=0
#         ): BufferSemantic(
#             AbstractSemantic(Semantic.EncodedData, 0), format=DXGIFormat.R32_UINT, input_slot=0
#         ),
#         BufferSemantic(
#             AbstractSemantic(Semantic.TexCoord, 2), format=DXGIFormat.R8G8B8A8_SNORM, input_slot=1
#         ): BufferSemantic(
#             AbstractSemantic(Semantic.Color, 0), format=DXGIFormat.R8G8B8A8_SNORM, input_slot=1
#         ),
#         BufferSemantic(
#             AbstractSemantic(Semantic.TexCoord, 4), format=DXGIFormat.R8G8B8A8_SNORM, input_slot=1
#         ): BufferSemantic(
#             AbstractSemantic(Semantic.Color, 0), format=DXGIFormat.R8G8B8A8_SNORM, input_slot=1
#         ),
#         BufferSemantic(
#             AbstractSemantic(Semantic.TexCoord, 3), format=DXGIFormat.R8G8B8A8_SNORM, input_slot=2
#         ): BufferSemantic(
#             AbstractSemantic(Semantic.Color, 2), format=DXGIFormat.R8G8B8A8_SNORM, input_slot=2
#         ),
#         BufferSemantic(
#             AbstractSemantic(Semantic.TexCoord, 4), format=DXGIFormat.R8G8B8A8_SNORM, input_slot=2
#         ): BufferSemantic(
#             AbstractSemantic(Semantic.Color, 1), format=DXGIFormat.R8G8B8A8_SNORM, input_slot=2
#         ),
#     }

#     vb_data_import_slots = [0, 1, 2]

#     def collect_index_data(self) -> IndexBuffer:

#         component_ib_data: IndexBuffer | None = None

#         for shader_call in self.shader_calls:

#             ib = shader_call.resources.get_by_slot("ib")

#             if not isinstance(ib, IndexBuffer):
#                 raise ValueError(f"{ib} is not an IndexBuffer")

#             if component_ib_data is None:
#                 component_ib_data = ib
#             elif ib.pointer != component_ib_data.pointer:
#                 raise ValueError(f"IB mismatch across draw calls")

#         return component_ib_data

#     def collect_vertex_data(self) -> list[SemanticVertexData]:

#         component_vb_data: dict[AbstractSemantic, SemanticVertexData] = {}
#         component_vb_data_usage: dict[tuple[str, int], BufferSemantic] = {}

#         for shader_call in self.shader_calls:

#             for resource_slot, resource in shader_call.resources.vertex_buffers.items():
#                 if not isinstance(resource, VertexBuffer):
#                     raise ValueError(f"{resource} is not an VertexBuffer")
#                 if not resource.data_descriptor:
#                     continue

#                 resource.load_format(from_file=True)

#                 if resource_slot.slot_id not in self.vb_data_import_slots:
#                     continue

#                 vb_layout = BufferLayout(
#                     semantics=resource.migoto_format.vb_layout.get_elements_in_slot(resource_slot.slot_id),
#                     auto_offsets=False,
#                     auto_stride=False,
#                 )
#                 vb_layout.stride = resource.migoto_format.stride
#                 if self.semantic_remap:
#                     vb_layout.remap_semantics(self.semantic_remap)
#                 vb_layout.sort()
#                 vb_layout.remove_data_views()
#                 vb_layout.dedupe_semantics()

#                 for buffer_semantic in vb_layout.semantics:
#                     vb_data = component_vb_data.get(buffer_semantic.abstract, None)

#                     # Ensure consistent buffer semantic for current buffer region across component's draw calls
#                     # If same resource pointer + semantic byte offset is mapped differently, semantic_remap must be used
#                     sematic_key = (resource.pointer, buffer_semantic.offset)
#                     resource_buffer_semantic = component_vb_data_usage.get(sematic_key, None)
#                     if resource_buffer_semantic is not None:
#                         if buffer_semantic.abstract != resource_buffer_semantic.abstract:
#                             raise ValueError(
#                                 f"Ambiguous buffer semantics across draw calls (missing remap): {buffer_semantic} vs {resource_buffer_semantic}")
#                         if buffer_semantic.stride != resource_buffer_semantic.stride:
#                             raise ValueError(
#                                 f"Inconsistent buffer semantics stride across draw calls: {buffer_semantic} vs {resource_buffer_semantic}")

#                     if vb_data is None:
#                         component_vb_data_usage[sematic_key] = buffer_semantic
#                         component_vb_data[buffer_semantic.abstract] = SemanticVertexData(buffer_semantic, vb_layout, resource)
#                     else:
#                         if vb_data.resource.pointer != resource.pointer:
#                             raise ValueError(
#                                 f"Inconsistent resource for semantic {buffer_semantic.abstract} across draw calls (missing remap?): {vb_data.resource.pointer} vs {resource.pointer}")

#         return list(component_vb_data.values())

#     def build_index_buffer(self) -> IndexBuffer:
#         index_buffer = self.collect_index_data()
#         index_buffer.build_numpy_buffer()
#         return index_buffer

#     def build_vertex_buffer(self) -> NumpyBuffer:

#         component_vertex_data = self.collect_vertex_data()

#         layout = BufferLayout([])

#         for vb_data in component_vertex_data:

#             layout.add_element(copy.deepcopy(vb_data.semantic))

#             if vb_data.resource.buffer is None:
#                 migoto_format = copy.deepcopy(vb_data.resource.migoto_format)
#                 migoto_format.vb_layout = vb_data.layout
#                 vb_data.layout.fill_missing_semantics()
#                 vb_data.resource.build_numpy_buffer(migoto_format=migoto_format)

#         vertex_buffer = NumpyBuffer(layout, size=self.vertex_count)

#         for vb_data in component_vertex_data:
#             vertex_buffer.import_data(data=vb_data.resource.buffer, semantic_converters={}, format_converters={})

#         return vertex_buffer

#     def collect_resources(self, slot_type: SlotType, skip_implicit: bool = True) -> dict[ResourceSlot, list[Resource]]:
#         resources: dict[ResourceSlot, list[Resource]] = {}
#         for shader_call in self.shader_calls:
#             if skip_implicit:
#                 resources_storage = shader_call.resources
#             else:
#                 resources_storage = shader_call.model_resources
#             for slot, resource in resources_storage.get_slot_index(slot_type).items():
#                 slot_resources = resources.get(slot, [])
#                 if not slot_resources:
#                     resources[slot] = slot_resources
#                 slot_resources.append(resource)
#         return resources


# @dataclass
# class RawObject:
#     id: str
#     components: dict[tuple[str, int, int], RawComponent] = field(default_factory=dict)

#     def sort_components(self):
#         self.components = dict(sorted(self.components.items()))

#     def register_shader_call(self, shader_call: ShaderCall):
#         ib = shader_call.resources.get_by_slot(ResourceSlot(ShaderType.Any, SlotType.IndexBuffer, 0))
#         ib.build_numpy_buffer()

#         vertex_indices = ib.buffer.get_field(Semantic.Index).flatten()

#         vertex_offset = int(min(vertex_indices))
#         vertex_count = int(max(vertex_indices) - vertex_offset + 1)

#         draw_key = (ib.hash, vertex_offset, vertex_count)

#         component = self.components.get(draw_key, None)

#         if component is None:
#             component = RawComponent(
#                 vertex_offset=vertex_offset,
#                 vertex_count=vertex_count
#             )
#             self.components[draw_key] = component
#             # self.sort_components()

#         component.shader_calls.append(shader_call)



# @dataclass
# class TextureFilter:
#     exclude_extensions: list[str]
#     exclude_hashes: list[str]
#     min_file_size: int

#     def is_valid_texture(self, texture: Resource) -> bool:
#         if texture.bin_path_deduped is None:
#             return False

#         # Exclude texture with ignored extension
#         if texture.bin_path_deduped.suffix[1:] in self.exclude_extensions:
#             return False

#         # Exclude textures with specified hashes
#         if self.exclude_hashes:
#             if texture.hash in self.exclude_hashes:
#                 return False

#         # Exclude texture below minimal file size
#         if self.min_file_size != 0:
#             file_size = Path(texture.bin_path_deduped).stat().st_size
#             if file_size < self.min_file_size:
#                 return False

#         # Exclude non-square textures
#         if texture.bin_path_deduped.suffix == '.dds':
#             width, height = self.get_dds_dimensions(texture.bin_path_deduped)
#             if width != height:
#                 return False

#         return True

#     @staticmethod
#     def get_dds_dimensions(path: Path) -> tuple[int, int]:
#         with open(path, 'rb') as f:
#             header = f.read(128)

#         if header[:4] != b'DDS ':
#             raise ValueError('Not a DDS file')

#         height, width = struct.unpack_from("<II", header, 12)

#         return width, height


# @dataclass
# class MigotoComponent:
#     mesh: MigotoMesh
#     textures: dict[ResourceSlot, list[Resource]]
#     raw_data: RawComponent | None = None

#     @classmethod
#     def from_raw_component(cls, raw_component: RawComponent) -> "MigotoComponent":

#         index_buffer = raw_component.build_index_buffer()

#         mesh = MigotoMesh.from_numpy_buffers(
#             index_buffer=index_buffer.buffer,
#             vertex_buffer=raw_component.build_vertex_buffer(),
#             topology=index_buffer.data_descriptor.topology,
#         )

#         component = cls(
#             mesh=mesh,
#             textures=raw_component.collect_resources(SlotType.Texture),
#             raw_data=raw_component,
#         )

#         return component


# @dataclass
# class MigotoObject:
#     id: str
#     components: list[MigotoComponent] = field(default_factory=list)

#     def get_textures(self, texture_filter: TextureFilter):
#         object_textures, components_usage, slot_usage = {}, {}, {}

#         for component_id, component in enumerate(self.components):
#             for slot, textures in component.textures.items():
#                 for texture in textures:

#                     if not texture_filter.is_valid_texture(texture):
#                         continue

#                     cached_texture = object_textures.get(texture.hash, None)
#                     if cached_texture is not None:
#                         if cached_texture.bin_path_deduped != texture.bin_path_deduped:
#                             raise ValueError(f"Texture {texture.hash} deduped path mismatch: {cached_texture.bin_path_deduped} != {texture.bin_path_deduped}")
#                     object_textures[texture.hash] = texture

#                     usage = components_usage.get(texture.hash, [])
#                     if not usage:
#                         components_usage[texture.hash] = usage
#                     usage.append(component_id)

#                     component_name = f"Component {component_id}"
#                     component_slots_usage = slot_usage.get(component_name, {})
#                     if not component_slots_usage:
#                         slot_usage[component_name] = component_slots_usage

#                     slot_str = slot.__str__()
#                     texture_slot_usage = component_slots_usage.get(slot_str, [])
#                     if not texture_slot_usage:
#                         component_slots_usage[slot_str] = texture_slot_usage

#                     tokens = [texture.hash]

#                     for shader_type, shader_hash in texture.usage_descriptor.shaders.items():
#                         tokens.append(f"{shader_type.value}={shader_hash}")

#                     tokens.append(f"{texture.usage_descriptor.call_id:06d}")

#                     if texture.data_descriptor.data_format:
#                         tokens.append(f"{texture.data_descriptor.data_format}")

#                     texture_slot_usage.append("-".join(tokens))

#         return object_textures, components_usage, slot_usage

#     def get_metadata(self) -> ExtractedObject:
#         return ExtractedObject(
#             ib_hash=None,
#             vb0_hash=None,
#             cb4_hash=None,
#             vertex_count=sum([component.mesh.format.vertex_count for component in self.components]),
#             index_count=sum([component.mesh.format.index_count for component in self.components]),
#             # ObjectRotation(90, 0, 0) if mesh.object_id.startswith('Factory')
#             rotation=ObjectRotation(0, 0, 0),
#             components=[
#                 ExtractedObjectComponent(
#                     ib_hash=component.raw_data.shader_calls[0].resources.get_by_slot("ib").hash,
#                     vb0_hash=component.raw_data.shader_calls[0].resources.get_by_slot("vb0").hash,
#                     vertex_offset=0,
#                     vertex_count=component.mesh.format.vertex_count,
#                     index_offset=0,
#                     index_count=component.mesh.format.index_count,
#                     vg_offset=0,
#                     vg_count=0,
#                     vg_map={},
#                     lods=[],
#                 ) for component in self.components
#             ],
#             shapekeys=ExtractedObjectShapeKeys(),
#             export_format={},
#         )

#     def export(self, folder_path: Path, texture_filter: TextureFilter) -> None:
#         folder_path.mkdir(parents=True, exist_ok=True)

#         for component_id, component in enumerate(self.components):
#             component.mesh.export_as_migoto_raw_buffers(folder_path, f"Component {component_id}")

#         object_textures, components_usage, slot_usage = self.get_textures(texture_filter=texture_filter)

#         with open(folder_path / f'TextureUsage.json', "w") as f:
#             f.write(json.dumps(slot_usage, indent=4))

#         with open(folder_path / f'Metadata.json', "w") as f:
#             f.write(self.get_metadata().as_json())

#         for texture_hash, texture in object_textures.items():

#             filename = f"Components-{'-'.join(map(str, sorted(list(set(components_usage.get(texture_hash))))))}"
#             filename += f" t={texture_hash}"
#             if texture.data_descriptor.data_format:
#                 filename += f" {texture.data_descriptor.data_format}"
#             filename += texture.bin_path_deduped.suffix

#             output_path = folder_path / filename

#             # if deduped_path.suffix == ".dds":
#             #     img = iio.imread(deduped_path)  # shape: (H, W, 4)
#             #     # Compute per-channel mean
#             #     mean_rgba = img.mean(axis=(0, 1))
#             #     print(1)

#             shutil.copyfile(texture.bin_path_deduped, output_path)




# @dataclass
# class ObjectExtractor:
#     model: DumpModel
#     objects: dict[str, RawObject] = field(default_factory=dict)
#     extracted_objects: dict[str, MigotoObject] = field(default_factory=dict)

#     def collect_calls(self) -> list[ShaderCall]:
#         calls = []

#         for shader_call in self.model.calls:

#             if not isinstance(shader_call.draw_call, DrawIndexedInstanced):
#                 continue

#             buffers = shader_call.resources

#             vb0 = buffers.get_by_slot(ResourceSlot(ShaderType.Any, SlotType.VertexBuffer, 0))
#             if vb0 is None:
#                 continue
#             if not isinstance(vb0, VertexBuffer):
#                 raise ValueError

#             if not vb0.data_descriptor:
#                 continue

#             # if vb0.data_descriptor.byte_offset:
#             #     continue

#             vs_t0 = buffers.get_by_slot(ResourceSlot(ShaderType.Vertex, SlotType.Texture, 0))
#             if vs_t0 is None:
#                 continue

#             vs_cb0 = buffers.get_by_slot(ResourceSlot(ShaderType.Vertex, SlotType.ConstantBuffer, 0))
#             if vs_cb0 is None:
#                 continue

#             calls.append(shader_call)

#         return calls

#     @staticmethod
#     def get_object_id(shader_call: ShaderCall) -> str:
#         vs_cb0 = shader_call.resources.get_by_slot(ResourceSlot(ShaderType.Vertex, SlotType.ConstantBuffer, 0))

#         vs_cb0.build_numpy_buffer(MigotoFormat(vb_layout=BufferLayout([
#             BufferSemantic(AbstractSemantic(Semantic.RawData, 0), DXGIFormat.R32_FLOAT, input_slot=0),
#         ])))

#         offset = vs_cb0.first_constant * 4

#         data = vs_cb0.buffer.get_field(0)

#         fragment = data[offset:offset + 16]

#         # Compute 64-bit hash
#         h = hashlib.blake2b(fragment.view(numpy.uint8), digest_size=8)
#         fragment_hash = int.from_bytes(h.digest(), 'little')

#         object_id = f"{fragment_hash:016x}"

#         return object_id

#     def register_shader_call(self, shader_call: ShaderCall):
#         object_id = self.get_object_id(shader_call)

#         extracted_object = self.objects.get(object_id, None)

#         if extracted_object is None:
#             extracted_object = RawObject(
#                 id=object_id
#             )
#             self.objects[object_id] = extracted_object

#         extracted_object.register_shader_call(shader_call)

#     def run(self):
#         shader_calls = self.collect_calls()

#         for shader_call in shader_calls:
#             self.register_shader_call(shader_call)

#         for raw_object in self.objects.values():

#             extracted_object = MigotoObject(
#                 id=raw_object.id,
#             )
#             self.extracted_objects[raw_object.id] = extracted_object

#             for component in raw_object.components.values():

#                 extracted_component = MigotoComponent.from_raw_component(component)

#                 extracted_object.components.append(extracted_component)

#         texture_filter = TextureFilter(
#             exclude_extensions=["jpg", "buf"],
#             exclude_hashes=[],
#             min_file_size=256*1024,
#         )

#         for object_id, extracted_object in self.extracted_objects.items():
#             output_path = Path(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\Extracted Objects")
#             output_path /= object_id
#             extracted_object.export(output_path, texture_filter)

# if __name__ == "__main__":

#     def get_region_bytes(buf_path, offset, count):
#         with open(buf_path, "rb") as f:
#             buf_bytes = f.read()
#         return buf_bytes[offset:offset+count]

#     def get_ib_txt_bytes(txt_path):
#         with open(txt_path, "r") as f:
#             migoto_format = MigotoFormat.from_txt_file(f)
#             f.seek(0)
#             buffer = NumpyBuffer(migoto_format.ib_layout, size=int(migoto_format.index_count / 3))
#             buffer.import_txt_data(f.read(), remapped_semantics=None, is_ib=True)
#         return buffer.get_bytes()

#     def get_vb_txt_bytes(txt_path):
#         with open(txt_path, "r") as f:
#             migoto_format = MigotoFormat.from_txt_file(f)
#             f.seek(0)
#             buffer = NumpyBuffer(migoto_format.ib_layout, size=int(migoto_format.index_count / 3))
#             buffer.import_txt_data(f.read(), remapped_semantics=None, is_ib=False)
#         return buffer.get_bytes()


#     # good_ib_buf_path = r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\FrameAnalysis-2026-03-13-043745\000020-ib=3d9e52b8(9a09f1f0)-vs=617db42150841836-ps=d7bb9dd57f5b70c6.buf"
#     # good_ib_byte_offset = 2792640
#     # good_ib_index_count = 32772
#     #
#     # good_ib_region_bytes = get_region_bytes(good_ib_buf_path, good_ib_byte_offset, good_ib_index_count * 2)
#     # good_ib_buf_hash = migoto_hash(0, good_ib_region_bytes)
#     #
#     # good_ib_txt_path = r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\FrameAnalysis-2026-03-13-043745\000020-ib=3d9e52b8(9a09f1f0)-vs=617db42150841836-ps=d7bb9dd57f5b70c6.txt"
#     #
#     # good_ib_txt_bytes = get_ib_txt_bytes(good_ib_txt_path)
#     # good_ib_txt_hash = migoto_hash(0, good_ib_txt_bytes)
#     #
#     # assert good_ib_region_bytes == good_ib_txt_bytes

#     # t = time.time()
#     #
#     # with open(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\FrameAnalysis-2026-03-03-022444\log.txt", 'r', encoding='utf-8') as f:
#     #     log = FrameDumpLog.from_text(f.read(), skip_migoto_lines=True)
#     #
#     # print(f'Time spent on DumpLog: {time.time()-t:.2f}s')
#     #
#     # t = time.time()
#     #
#     # cfg = ParseDumpModelConfig()
#     # cfg.shader_call_config.command_config.skip_commands = {
#     #     'Begin', 'End', 'Map', 'Unmap', 'GetData', 'GetType', 'RSSetViewports', 'RSSetScissorRects', 'RSSetState',
#     #     'OMGetRenderTargets', 'OMSetDepthStencilState', 'OMSetBlendState',
#     #     'ClearRenderTargetView', 'ClearDepthStencilView', 'IASetInputLayout'
#     # }
#     # cfg.shader_call_config.command_config.skip_stage_commands = {
#     #     'GetSamplers', 'SetSamplers', 'GetShader'
#     # }
#     #
#     # model = DumpModel.from_frame_dump_log(log, cfg)
#     #
#     # model.execute_commands()
#     #
#     # print(f'Time spent on DumpModel: {time.time()-t:.2f}s')
#     #
#     # t = time.time()
#     #
#     # extractor = ObjectExtractor(model=model)
#     #
#     # extractor.run()
#     #
#     # print(f'Time spent on ObjectExtractor: {time.time()-t:.2f}s')

#     # bad_ib_buf_path = r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\FrameAnalysis-2026-03-13-042146\000020-ib=7ff8820a(9a09f1f0)-vs=617db42150841836-ps=d7bb9dd57f5b70c6.buf"
#     # bad_ib_byte_offset = 2787952
#     # bad_ib_index_count = 32772
#     #
#     # bad_ib_region_bytes = get_region_bytes(bad_ib_buf_path, bad_ib_byte_offset, bad_ib_index_count * 2)
#     # bad_ib_buf_hash = migoto_hash(0, bad_ib_region_bytes)
#     #
#     # bad_ib_txt_path = r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\FrameAnalysis-2026-03-13-042146\000020-ib=7ff8820a(9a09f1f0)-vs=617db42150841836-ps=d7bb9dd57f5b70c6.txt"
#     # bad_ib_txt_bytes = get_ib_txt_bytes(bad_ib_txt_path)
#     # bad_ib_txt_hash = migoto_hash(0, bad_ib_txt_bytes)
#     #
#     # assert bad_ib_region_bytes == bad_ib_txt_bytes
#     #
#     # assert good_ib_region_bytes == bad_ib_region_bytes
#     # C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\BadGilbertaLod0Dump\000447-ib=e8b8a8db(9a09f1f0)-vs=617db42150841836-ps=d7bb9dd57f5b70c6.txt
#     # C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\GoodGilbertaLod0Dump\000139-ib=e8b8a8db(9a09f1f0)-vs=617db42150841836-ps=d7bb9dd57f5b70c6.txt
#     #
#     # good_vb_buf_path = r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\GoodGilbertaLod0Dump\000139-vb0=81611888(9a09f1f0)-vs=617db42150841836-ps=d7bb9dd57f5b70c6.buf"
#     # good_vb_txt_path = r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\GoodGilbertaLod0Dump\000139-vb0=81611888(9a09f1f0)-vs=617db42150841836-ps=d7bb9dd57f5b70c6.txt"
#     # good_vb_byte_offset = 9025488
#     # good_vb_byte_stride = 16
#     # good_vb_index_count = 51567
#     # good_vb_size = good_vb_byte_stride * int(good_vb_index_count / 3)
#     #
#     # bad_vb_buf_path = r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\BadGilbertaLod0Dump\000447-vb0=ae77596c(9a09f1f0)-vs=617db42150841836-ps=d7bb9dd57f5b70c6.buf"
#     # bad_vb_txt_path = r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\BadGilbertaLod0Dump\000447-vb0=ae77596c(9a09f1f0)-vs=617db42150841836-ps=d7bb9dd57f5b70c6.txt"
#     # bad_vb_byte_offset = 4614576
#     # bad_vb_byte_stride = 16
#     # bad_vb_index_count = 51567
#     # bad_vb_size = bad_vb_byte_stride * int(bad_vb_index_count / 3)
#     #
#     # good_vb_region_bytes = get_region_bytes(good_vb_buf_path, good_vb_byte_offset, good_vb_size)
#     # good_vb_buf_hash = migoto_hash(0, good_vb_region_bytes)
#     #
#     # bad_vb_region_bytes = get_region_bytes(bad_vb_buf_path, bad_vb_byte_offset, bad_vb_size)
#     # bad_vb_buf_hash = migoto_hash(0, bad_vb_region_bytes)


#     t = time.time()

#     dump_path = Path(r"C:\Games\XXMI Launcher\Importers\EFMI\VTEF_DEV\!DUMPS\Endmin Open World Dump 1.1")

#     with open(dump_path / "log.txt", 'r', encoding='utf-8') as f:
#         log = FrameDumpLog.from_text(f.read(), skip_migoto_lines=True)

#     print(f'Time spent on DumpLog: {time.time()-t:.2f}s')

#     t = time.time()

#     cfg = ParseDumpModelConfig(
#         dump_path=dump_path,
#     )
#     cfg.shader_call_config.command_config.skip_commands = {
#         'Begin', 'End', 'Map', 'Unmap', 'GetData', 'GetType', 'RSSetViewports', 'RSSetScissorRects', 'RSSetState',
#         'OMGetRenderTargets', 'OMSetDepthStencilState', 'OMSetBlendState',
#         'ClearRenderTargetView', 'ClearDepthStencilView', 'IASetInputLayout'
#     }
#     cfg.shader_call_config.command_config.skip_stage_commands = {
#         'GetSamplers', 'SetSamplers', 'GetShader'
#     }

#     model = DumpModel.from_frame_dump_log(log, cfg)
#     model.execute_commands()

#     t = time.time()

#     extractor = ObjectExtractor(model=model)

#     extractor.run()

#     test = []
#     for obj in extractor.objects.values():
#         if len(obj.components) < 2:
#             continue
#         test.append(obj)


#     print(f'Time spent on ObjectExtractor: {time.time()-t:.2f}s')



#     #
#     # counts = {}
#     #
#     # for shader_call in model.calls:
#     #
#     #     if not isinstance(shader_call.draw_call, DrawIndexedInstanced):
#     #         continue
#     #
#     #     ib = shader_call.resources.get_by_slot(ResourceSlot(ShaderType.Any, SlotType.IndexBuffer, 0))
#     #
#     #     if not ib:
#     #         ib = shader_call.model_resources.get_by_slot(ResourceSlot(ShaderType.Any, SlotType.IndexBuffer, 0))
#     #         if not ib:
#     #             continue
#     #
#     #     try:
#     #         buffer = ib.build_numpy_buffer(from_txt=True)
#     #     except Exception as e:
#     #         continue
#     #
#     #     vertex_count = buffer.get_field(0).max()
#     #
#     #     print(f"{shader_call.draw_call.index_count}: vertex_count")
#     #
#     #     old_count = counts.get(vertex_count, 10000000)
#     #     if vertex_count < old_count:
#     #         counts[shader_call.draw_call.index_count] = vertex_count
#     #
#     #     print(1)
#     #
#     # counts = dict(sorted(counts.items(), key=lambda item: item[1]))

#     print(0)