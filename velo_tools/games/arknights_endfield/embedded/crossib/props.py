"""CrossIB property groups."""
import bpy
import re


_VELO_EXPORT_SUFFIX_RE = re.compile(r'__velo_export(?:\.\d{3})?$')


def _sanitize_source_name(name):
    text = (name or '').strip()
    return _VELO_EXPORT_SUFFIX_RE.sub('', text)


def _iter_collection_meshes(col):
    """Recursively iterate mesh objects in a collection (and its children)."""
    if col is None:
        return
    for obj in col.objects:
        if obj.type == 'MESH':
            yield obj
    for child in col.children:
        yield from _iter_collection_meshes(child)


_component_pattern = re.compile(r'.*component[_ -]*(\d+).*', re.IGNORECASE)


def parse_component_id(name):
    """Extract Component# index from an object name. Returns int or None."""
    m = _component_pattern.findall(name.lower())
    return int(m[0]) if m else None


class CrossIBMapping(bpy.types.PropertyGroup):
    """A single cross-IB mapping row.

    Source can be either a single mesh object or a whole collection (every mesh
    inside acts as its own provider). Legacy vs fields are kept for old .blend
    files, but export conditions are now inferred from the extracted source data.
    """

    source_kind: bpy.props.EnumProperty(
        name="Source Kind",
        items=[
            ('OBJECT', "Object", "Single mesh object as provider"),
            ('COLLECTION', "Collection", "Every mesh in a collection as providers"),
        ],
        default='OBJECT',
    )  # type: ignore

    source_object: bpy.props.PointerProperty(
        name="Source Object",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
    )  # type: ignore

    source_collection: bpy.props.PointerProperty(
        name="Source Collection",
        type=bpy.types.Collection,
    )  # type: ignore

    target_component: bpy.props.IntProperty(
        name="Target Component",
        default=0,
        min=0,
        max=15,
    )  # type: ignore

    # Legacy fields. They remain loadable for old scenes but are no longer used
    # by the CrossIB generator.
    vs_200: bpy.props.BoolProperty(name="vs200", default=True)  # type: ignore
    vs_201: bpy.props.BoolProperty(name="vs201", default=True)  # type: ignore
    vs_204: bpy.props.BoolProperty(name="vs204", default=True)  # type: ignore


class CrossIBSettings(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(
        name="Use Cross Index Buffer",
        description="Enable Cross-IB rendering on export",
        default=False,
    )  # type: ignore

    frame_dump_folder: bpy.props.StringProperty(
        name="帧 Dump 文件夹",
        description=(
            "手动指定帧 Dump 文件夹\n\n"
            "可选。留空时自动从模型源文件夹及其父级目录扫描原始帧转储文件；"
            "填写后只扫描该目录。"
        ),
        default="",
        subtype="DIR_PATH",
    )  # type: ignore

    mappings: bpy.props.CollectionProperty(type=CrossIBMapping)  # type: ignore


def resolve_source_names(mapping):
    """Return the list of provider object names for a single mapping row."""
    if mapping.source_kind == 'COLLECTION':
        if mapping.source_collection is None:
            return []
        return [_sanitize_source_name(o.name) for o in _iter_collection_meshes(mapping.source_collection)]
    if mapping.source_object is None:
        return []
    return [_sanitize_source_name(mapping.source_object.name)]
