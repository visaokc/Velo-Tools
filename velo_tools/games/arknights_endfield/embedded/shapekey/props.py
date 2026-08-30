"""Properties for the ShapeKey addon, attached to Scene.shapekey_settings."""
import re
import bpy


# ------------------------------------------------------------------ helpers
_COMPONENT_RE = re.compile(r'Component\s*(\d+)', re.IGNORECASE)


def extract_component_id(obj_name: str) -> int:
    """Pull the numeric Component id out of a Blender object name. -1 if none."""
    m = _COMPONENT_RE.search(obj_name or "")
    return int(m.group(1)) if m else -1


# --------------------------------------- click-to-jump update callback
def _on_detected_active_changed(self, context):
    """When user clicks a row in the detector list: jump to that object
    and activate the matching shape key. Header rows are no-ops."""
    items = self.detected_items
    idx = self.detected_active_index
    if not (0 <= idx < len(items)):
        return
    it = items[idx]
    if it.item_kind != "leaf":
        return
    obj = bpy.data.objects.get(it.object_name)
    if obj is None:
        return
    try:
        view_layer = context.view_layer
        for o in view_layer.objects:
            if o.select_get():
                o.select_set(False)
        if obj.name in view_layer.objects:
            obj.select_set(True)
            view_layer.objects.active = obj
        if obj.data and getattr(obj.data, "shape_keys", None):
            kb = obj.data.shape_keys.key_blocks
            sk_idx = kb.find(it.sk_name)
            if sk_idx >= 0:
                obj.active_shape_key_index = sk_idx
    except Exception as e:
        print(f"[ShapeKey] click-jump failed: {e}")


# ------------------------------------------------------------------ PropertyGroups
class ShapeKeyDetectedItem(bpy.types.PropertyGroup):
    """One row in the N-panel detector UIList. May be a group header
    ('header') or a real shape-key entry ('leaf')."""
    item_kind: bpy.props.StringProperty(default="leaf")        # type: ignore
    deform_num: bpy.props.IntProperty(default=0)               # type: ignore
    component_id: bpy.props.IntProperty(default=-1)            # type: ignore
    object_name: bpy.props.StringProperty(default="")          # type: ignore
    sk_name: bpy.props.StringProperty(default="")              # type: ignore
    name_part: bpy.props.StringProperty(default="")            # type: ignore
    group_size: bpy.props.IntProperty(default=0)               # type: ignore
    display: bpy.props.StringProperty(default="")              # type: ignore


class ShapeKeyGroupState(bpy.types.PropertyGroup):
    """Per Deform-num expand/collapse state inside the detector list."""
    deform_num: bpy.props.IntProperty(default=0)               # type: ignore
    expanded: bpy.props.BoolProperty(default=True)             # type: ignore


class ShapeKeySettings(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(
        name='Export custom shape keys',
        description=(
            "When enabled, shape keys named 'Deform <num> <name>' "
            "(whitespace optional, e.g. 'Deform 1 abc' or 'Deform1abc') "
            "will be baked as per-slot position deltas and exported alongside "
            "the mod, with auto-generated INI sections and a compute shader "
            "that blends them at runtime via $Freq_<name> globals.\n\n"
            "Two shape keys with different slot numbers but the SAME name will "
            "block the export with an error message."
        ),
        default=False,
    )  # type: ignore

    merge_buffers: bpy.props.BoolProperty(
        name='Merge Buffer file',
        description=(
            "Merge all shape-key delta + map files inside one Component into a "
            "single deltas buffer + a single lookup buffer. Massively reduces "
            "the number of files written to Meshes/. The default _VB0 / "
            "_Position_0 buffer is NEVER merged.\n\n"
            "ON  (default): 2 extra buf files per Component, regardless of how "
            "many Deform keys it has.\n"
            "OFF (legacy v0.2): per-slot delta + map files + 1 freq_indices "
            "file per Component."
        ),
        default=True,
    )  # type: ignore

    show_detector: bpy.props.BoolProperty(
        name='Show recognized shape keys',
        description='Expand / Collapse the real-time recognition list below.',
        default=True,
    )  # type: ignore

    detected_items: bpy.props.CollectionProperty(
        type=ShapeKeyDetectedItem,
    )  # type: ignore

    detected_active_index: bpy.props.IntProperty(
        default=-1,
        update=_on_detected_active_changed,
    )  # type: ignore

    group_states: bpy.props.CollectionProperty(
        type=ShapeKeyGroupState,
    )  # type: ignore

    detected_fingerprint: bpy.props.StringProperty(default="")  # type: ignore


def get_or_create_group_state(settings, deform_num: int) -> ShapeKeyGroupState:
    for st in settings.group_states:
        if st.deform_num == deform_num:
            return st
    st = settings.group_states.add()
    st.deform_num = deform_num
    st.expanded = True
    return st


def find_group_state(settings, deform_num: int):
    for st in settings.group_states:
        if st.deform_num == deform_num:
            return st
    return None
