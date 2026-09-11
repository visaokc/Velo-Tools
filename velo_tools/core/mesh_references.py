"""Persistent exact-name mesh references, independent of object identity."""

import traceback

import bpy
from bpy.app.handlers import persistent


_reference_sets = {}
_resolved_states = {}
_refreshing = False
_initialization_pending = False
_pending_pickers = {}


def requested_name(settings, field):
    value = settings.get(field + "_name")
    if value is not None:
        return value
    # Read legacy pointers until register/load migration has frozen their names.
    legacy = settings.get(field)
    try:
        return legacy.name if legacy is not None and legacy.type == 'MESH' else ""
    except ReferenceError:
        return ""


def resolve(settings, field):
    name = requested_name(settings, field)
    obj = bpy.data.objects.get(name) if name else None
    return obj if obj is not None and obj.type == 'MESH' else None


def object_property(field):
    """Keep Python callers compatible while resolving every read by exact name."""
    def get(settings):
        return resolve(settings, field)

    def set(settings, obj):
        if obj is not None and not isinstance(obj, bpy.types.Object):
            raise TypeError("Expected a Blender object or None")
        setattr(settings, field + "_name", obj.name if obj is not None else "")

    return property(get, set)


def search_callback(poll):
    def search(settings, context, edit_text):
        query = edit_text.casefold()
        return [obj.name for obj in bpy.data.objects
                if poll(settings, obj) and query in obj.name.casefold()]

    return search


def _state(settings, field):
    obj = resolve(settings, field)
    return requested_name(settings, field), obj.as_pointer() if obj else 0


def update_callback(field, callback):
    def update(settings, context):
        if field in settings:
            del settings[field]
        _resolved_states[(settings.as_pointer(), field)] = _state(settings, field)
        callback(settings, context)

    return update


def register_reference_set(owner, fields, on_resolution_changed):
    _reference_sets[owner] = tuple(fields), on_resolution_changed
    settings_type = bpy.types.Scene.bl_rna.properties[owner].fixed_type
    settings_class = bpy.types.PropertyGroup.bl_rna_get_subclass_py(settings_type.identifier)
    for field in fields:
        name_prop = settings_type.properties[field + "_name"]
        setattr(settings_class, field + "_picker", bpy.props.PointerProperty(
            name=name_prop.name,
            description=name_prop.description,
            type=bpy.types.Object,
            poll=_picker_poll,
            update=_picker_update(field),
            options={'SKIP_SAVE'},
        ))


def _picker_poll(_settings, obj):
    return obj is not None and obj.type == 'MESH'


def _picker_update(field):
    def update(settings, _context):
        picker = field + "_picker"
        obj = getattr(settings, picker)
        if obj is None:
            return
        try:
            if _picker_poll(settings, obj):
                setattr(settings, field + "_name", obj.name)
        finally:
            # Native eyedroppers read the pointer back after update to confirm success.
            # Release only on the next UI tick, not inside that readback transaction.
            _pending_pickers[(settings.as_pointer(), picker)] = settings
            if not bpy.app.timers.is_registered(_clear_pickers):
                bpy.app.timers.register(_clear_pickers, first_interval=0.0)
    return update


def _clear_pickers():
    pending = tuple(_pending_pickers.items())
    _pending_pickers.clear()
    for (_pointer, field), settings in pending:
        try:
            setattr(settings, field, None)
        except (ReferenceError, AttributeError):
            pass
    return None


def unregister_reference_set(owner):
    _reference_sets.pop(owner, None)
    _resolved_states.clear()


def migrate_scene(scene):
    """Freeze existing selections once, without changing meshes or mapping tables."""
    for owner, (fields, _callback) in _reference_sets.items():
        settings = getattr(scene, owner, None)
        if settings is None:
            continue
        for field in fields:
            if field not in settings:
                continue
            name_field = field + "_name"
            if name_field not in settings:
                settings[name_field] = requested_name(settings, field)
            del settings[field]


def refresh_scene(scene, *, notify=True):
    """Refresh dependent UI only when a saved name resolves to a different mesh."""
    global _refreshing
    if _refreshing:
        return
    _refreshing = True
    try:
        for owner, (fields, callback) in _reference_sets.items():
            settings = getattr(scene, owner, None)
            if settings is None:
                continue
            changed = []
            for field in fields:
                key = settings.as_pointer(), field
                state = _state(settings, field)
                previous = _resolved_states.get(key, state)
                _resolved_states[key] = state
                if previous != state:
                    changed.append(field)
            if notify and changed:
                with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
                    callback(settings, bpy.context, changed)
                    for area in getattr(bpy.context.screen, "areas", ()):
                        area.tag_redraw()
    finally:
        _refreshing = False


def draw_reference(layout, settings, field, *, text=None):
    from bpy.app.translations import pgettext_iface as iface_

    row = layout.row(align=True)
    name = requested_name(settings, field)
    value_row = row.row(align=True)
    value_row.alert = bool(name and resolve(settings, field) is None)
    # Empty fields expose the native eyedropper. Filled fields show actual saved
    # text with a clear action, never a gray placeholder or a second picker.
    label = text if text is not None else iface_(settings.bl_rna.properties[field + "_name"].name)
    display_field = field + ("_name" if name else "_picker")
    value_row.prop(settings, display_field, text=label,
                   icon='OUTLINER_OB_MESH', translate=False)
    if name:
        clear = row.operator("wm.context_set_string", text="", icon='X')
        clear.data_path = f"scene.{settings.path_from_id()}.{field}_name"
        clear.value = ""


@persistent
def _load_post(_unused):
    global _initialization_pending
    _resolved_states.clear()
    for scene in bpy.data.scenes:
        migrate_scene(scene)
        refresh_scene(scene, notify=False)
    _initialization_pending = False


def _finish_registration():
    if _initialization_pending:
        _load_post(None)
    return None


@persistent
def _depsgraph_update_post(scene, _depsgraph):
    try:
        _finish_registration()
        refresh_scene(scene)
    except (ReferenceError, RuntimeError):
        traceback.print_exc()


@persistent
def _history_post(_unused):
    for scene in bpy.data.scenes:
        refresh_scene(scene)


_handlers = (
    ("load_post", _load_post),
    ("depsgraph_update_post", _depsgraph_update_post),
    ("undo_post", _history_post),
    ("redo_post", _history_post),
)


def register():
    global _initialization_pending
    _initialization_pending = True
    for name, callback in _handlers:
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            handlers.insert(0, callback)
    # Native addon enabling temporarily replaces bpy.data with restricted data.
    if hasattr(bpy.data, "scenes"):
        _finish_registration()
    elif not bpy.app.timers.is_registered(_finish_registration):
        bpy.app.timers.register(_finish_registration, first_interval=0.0)


def unregister():
    global _initialization_pending
    _initialization_pending = False
    if bpy.app.timers.is_registered(_clear_pickers):
        bpy.app.timers.unregister(_clear_pickers)
    _clear_pickers()
    if bpy.app.timers.is_registered(_finish_registration):
        bpy.app.timers.unregister(_finish_registration)
    for name, callback in _handlers:
        handlers = getattr(bpy.app.handlers, name)
        if callback in handlers:
            handlers.remove(callback)
    _resolved_states.clear()
