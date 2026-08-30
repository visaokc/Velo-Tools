
from velo_tools.i18n import iface_
# Velo import extras for WWMI (velo-owned, zero core edits), the EFMI parity:
#   - inject_settings(): import_as_component_collections / import_texture
#     BoolProperties injected into VTWW_Settings (EFMI-aligned texts and
#     defaults; snapshot-restored, same constraint as slot_textures).
#   - install(): wraps VTWW_Import.execute -- after a successful import the
#     new mesh objects are regrouped into C{n} sub-collections under the main
#     collection (EFMI does this inside its forked core; WWMI core stays
#     untouched, so it is a post-import reorg here), with the EFMI export
#     linkage (component_collection assigned, ignore_nested_collections
#     lowered so nested sub-collections still export); then the optional
#     TextureUsage.json-based texture auto-assign runs on the new objects.
#     Also replaces draw_menu_import_object for the EFMI-style layout
#     (velo box + texture toggle ABOVE the import button; an append-wrap
#     would land below it). Original method restored on remove().

import re
import traceback

import bpy

from . import import_textures as _imp_tex

_MISSING = object()
_orig_annotations = {}
_orig_import_execute = None
_orig_draw_menu_import_object = None

# Correct component-id pattern; core's own r'.*component[ -_]*([0-9]+).*' is
# buggy for multi-digit ids (see embedded/crossscene/patch.py).
_COMPONENT_RE = re.compile(r"component[ _-]*([0-9]+)", re.IGNORECASE)


def _zh(text: str) -> str:
    try:
        return text.encode("cp1252").decode("utf-8")
    except UnicodeError:
        return text


# ------------------------------------------------------- settings injection --

def inject_settings():
    """Adds the two import options to VTWW_Settings. Must run BEFORE the
    vendored settings class is registered (same constraint as slot_textures)."""
    from .._wwmi_core.addon import settings as _wsettings

    ann = _wsettings.VTWW_Settings.__annotations__
    props = {
        "import_as_component_collections": bpy.props.BoolProperty(
            name=_zh('Create sub-collection by component'),
            description=_zh('When importing the model, create C0/C1/... sub-collections under the parent collection of objects; when closed, continue to use the single collection import of upstream WWMI.'),
            default=True,
        ),
        "import_texture": bpy.props.BoolProperty(
            name=_zh('Import Texture'),
            description=_zh('After importing the model, assign the .dds texture in the source directory to the mesh according to TextureUsage.json.'),
            default=True,
        ),
    }
    for name, prop in props.items():
        _orig_annotations[name] = ann.get(name, _MISSING)
        ann[name] = prop


def restore_settings():
    global _orig_annotations
    from .._wwmi_core.addon import settings as _wsettings

    ann = _wsettings.VTWW_Settings.__annotations__
    for name, old in _orig_annotations.items():
        if old is _MISSING:
            ann.pop(name, None)
        else:
            ann[name] = old
    _orig_annotations = {}


# --------------------------------------------------------- import wrap ------

def _find_main_collection(existing_col_names, new_objects):
    new_cols = [col for col in bpy.data.collections if col.name not in existing_col_names]
    if len(new_cols) == 1:
        return new_cols[0]
    for col in new_cols:
        if any(obj.name in col.objects for obj in new_objects):
            return col
    return None


def _reorganize_into_component_collections(main_col, new_objects):
    # Group objects by component id first, then create the C{n} sub-collections
    # in numeric order. The child-link order is what the outliner shows (link
    # order, not alphabetical), so sorting here yields C0, C1, ... C9, C10, C11
    # instead of the lexicographic C0, C1, C10, C11, C2 ... that the import's
    # os.listdir iteration order would otherwise produce.
    groups = {}
    for obj in new_objects:
        match = _COMPONENT_RE.search(obj.name)
        if not match:
            continue  # unparseable name stays in the main collection
        groups.setdefault(int(match.group(1)), []).append(obj)
    for component_id in sorted(groups):
        sub = bpy.data.collections.new(f"C{component_id}")
        main_col.children.link(sub)
        for obj in groups[component_id]:
            if obj.name in main_col.objects:
                main_col.objects.unlink(obj)
            if obj.name not in sub.objects:
                sub.objects.link(obj)


def _prefill_form_anchors_from_stu(context, cfg):
    slot_cfg = getattr(context.scene, "vtww_slot_settings", None)
    if slot_cfg is None or str(slot_cfg.form_anchors or '').strip():
        return []
    if not str(getattr(cfg, "object_source_folder", "") or "").strip():
        return []
    from .._wwmi_core.migoto_io.blender_interface.utility import resolve_path
    from .slot_textures import form_merge

    pairs = form_merge.read_trusted_form_anchors(resolve_path(cfg.object_source_folder))
    if not pairs:
        return []
    slot_cfg.form_anchors = ", ".join(f"{anchor_hash}:{label}"
                                      for label, anchor_hash in pairs)
    return pairs


def install():
    global _orig_import_execute
    if _orig_import_execute is None:
        from .._wwmi_core.addon import ui as _wui

        _orig_import_execute = _wui.VTWW_Import.execute

        def execute_with_velo_extras(self, context):
            cfg = context.scene.VTWW_settings
            existing_obj_names = {obj.name for obj in bpy.data.objects}
            existing_col_names = {col.name for col in bpy.data.collections}
            result = _orig_import_execute(self, context)
            new_objects = [
                obj for obj in bpy.data.objects
                if obj.name not in existing_obj_names and getattr(obj, "type", None) == "MESH"
            ]
            if not new_objects:
                return result
            try:
                main_col = _find_main_collection(existing_col_names, new_objects)
                if main_col is not None:
                    use_sub_cols = getattr(cfg, "import_as_component_collections", False)
                    if use_sub_cols:
                        _reorganize_into_component_collections(main_col, new_objects)
                    cfg.component_collection = main_col
                    # WWMI export ignores nested collections by default; lower
                    # the flag so the C{n} sub-collections still export.
                    cfg.ignore_nested_collections = not use_sub_cols
            except Exception as exc:
                traceback.print_exc()
                self.report({'WARNING'}, iface_('Failed to create sub-collection by component: {0}').format(exc))
            try:
                filled = _prefill_form_anchors_from_stu(context, cfg)
                if filled:
                    self.report(
                        {'INFO'},
                        iface_('Pre-filled {0} form anchors from STU').format(len(filled)))
            except Exception as exc:
                traceback.print_exc()
                self.report({'WARNING'}, iface_('Pre-fill morph anchor failed: {0}').format(exc))
            if getattr(cfg, "import_texture", False):
                try:
                    summary = _imp_tex.assign_textures(cfg.object_source_folder, objects=new_objects)
                except Exception as exc:
                    traceback.print_exc()
                    self.report({'WARNING'}, iface_('Failed to import texture: {0}').format(exc))
                    return result
                parts = []
                if summary.assigned:
                    parts.append(f"已为 {summary.assigned} 个导入网格指定贴图")
                if summary.bare:
                    parts.append(f"{summary.bare} 个仅创建材质（漫反射贴图不在文件夹）")
                if parts:
                    self.report({'INFO'}, iface_(str("，".join(parts))))
            return result

        _wui.VTWW_Import.execute = execute_with_velo_extras
        print("[velo.import-extras] patched VTWW_Import.execute (component sub-collections + texture assign)")

    _patch_import_menu()


def remove():
    global _orig_import_execute
    _restore_import_menu()
    if _orig_import_execute is None:
        return
    from .._wwmi_core.addon import ui as _wui

    _wui.VTWW_Import.execute = _orig_import_execute
    _orig_import_execute = None


# --------------------------------------------------------- import menu ------

def _patch_import_menu():
    global _orig_draw_menu_import_object
    if _orig_draw_menu_import_object is not None:
        return
    from .._wwmi_core.addon import ui as _wui

    _orig_draw_menu_import_object = _wui.VTWW_PT_SIDEBAR.draw_menu_import_object

    def draw_menu_import_object(self, context):
        # Core layout (addon/ui.py draw_menu_import_object) + the velo box and
        # the texture toggle above the import button, matching EFMI. Property
        # texts come from the localized annotations, no text= overrides.
        cfg = context.scene.VTWW_settings
        layout = self.layout

        layout.row()

        row = _wui.add_row_with_error_handler(layout, cfg, 'object_source_folder')
        row.prop(cfg, 'object_source_folder')

        layout.row().prop(cfg, 'color_storage')
        layout.row().prop(cfg, 'import_skeleton_type')

        # EFMI layout parity: the velo box sits right under the skeleton
        # dropdown, before the remaining import toggles.
        if hasattr(cfg, "import_as_component_collections"):
            box = layout.box()
            box.label(text=iface_('Velo Compatibility Options'), icon="TOOL_SETTINGS")
            box.prop(cfg, "import_as_component_collections")

        if cfg.import_skeleton_type == 'MERGED':
            layout.row().prop(cfg, 'skip_empty_vertex_groups')
        layout.row().prop(cfg, 'mirror_mesh')

        if hasattr(cfg, "import_texture"):
            layout.row().prop(cfg, "import_texture")

        layout.row()

        layout.row().operator(_wui.VTWW_Import.bl_idname)

    _wui.VTWW_PT_SIDEBAR.draw_menu_import_object = draw_menu_import_object


def _restore_import_menu():
    global _orig_draw_menu_import_object
    if _orig_draw_menu_import_object is None:
        return
    from .._wwmi_core.addon import ui as _wui

    _wui.VTWW_PT_SIDEBAR.draw_menu_import_object = _orig_draw_menu_import_object
    _orig_draw_menu_import_object = None
