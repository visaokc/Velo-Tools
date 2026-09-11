"""Reversible driver hooks for finalized draw ranges and image delivery."""
from __future__ import annotations

from array import array
import json
from pathlib import Path

import bpy

from . import model, nodes, ini, batching
from ..i18n import iface_

_PATCHES = []
_EXPORT_PATCHES = []
_UV_PATCHES = []
SETTINGS = {"ENDFIELD": "VTEF_settings", "WUTHERING": "VTWW_settings"}


def enabled(cfg):
    return bool(getattr(cfg, "velo_auto_split_by_material", True)
                and getattr(cfg, "material_texture_overrides", False))


def validate_mode(cfg, game, scene=None):
    slot_name = "slot_style_textures" if game == "ENDFIELD" else "velo_slot_style_textures"
    if not getattr(cfg, slot_name, False):
        raise ValueError(iface_("Material textures require Slot style texture export"))
    if (getattr(cfg, "use_custom_template", False)
            or getattr(cfg, "custom_template_live_update", False)
            or getattr(cfg, "use_asset_name_matching", False)):
        raise ValueError(iface_("Material textures do not support custom templates or asset-name export"))
    if getattr(cfg, "partial_export", False):
        raise ValueError(iface_("Material textures require a full export with INI output"))
    if not getattr(cfg, "write_ini", True):
        raise ValueError(iface_("Material textures require a full export with INI output"))
    folder = Path(bpy.path.abspath(cfg.object_source_folder))
    if game == "WUTHERING" and any((folder / name).is_file() for name in ("CrossSceneManifest.json", "CrossSceneRouting.json")):
        raise ValueError(iface_("Material textures currently support single-source export; disable this option for Cross-Scene export"))
    if game == "ENDFIELD" and scene is not None and getattr(getattr(scene, "crossib_settings", None), "enabled", False):
        raise ValueError(iface_("Material textures currently require Cross-IB to be disabled"))


def resolve_material_images(material, game, component, folder, catalogs):
    images = nodes.connected_images(material)
    if not images:
        return ()
    data = model.unpack_sources(material)
    if data.get("game") != game:
        raise ValueError(iface_("Material source game differs from the active exporter"))
    if component not in catalogs:
        catalogs[component] = model.read_evidence(folder, component)
    current = catalogs[component]
    identities = {role: identity for role, image in images.items()
                  if (identity := model.texture_identity(image.filepath or image.name))}
    data = model.resolve_sources(game, component, current, data, identities)
    values = []
    for role, image in images.items():
        identity = data.get("bindings", {}).get(role, "")
        if model.inherits_game_source(data, role):
            continue
        if not identity or identity not in current:
            raise ValueError(iface_("{0}: choose the original texture for {1} in Material Tools").format(material.name, iface_(model.ROLES[role])))
        values.append((identity, image))
    return tuple(values)


def preflight_materials(context, cfg, game):
    """Reject known authoring errors before native temporary mesh allocation.

    Finalized geometry still receives the same validation in capture_merger;
    modifier-generated materials cannot be assumed to match authoring inputs.
    """
    from ..core.export.hook import _iter_export_meshes
    validate_mode(cfg, game, context.scene)
    folder = Path(bpy.path.abspath(cfg.object_source_folder))
    catalogs, seen, checked_images = {}, set(), set()
    payloads, payload_names = {}, {}
    for obj in _iter_export_meshes(context, cfg):
        component = model.component_id(obj.name)
        if component is None:
            continue
        used = {polygon.material_index for polygon in obj.data.polygons}
        for index, slot in enumerate(obj.material_slots):
            if index not in used or slot.material is None:
                continue
            target = model.component_id(slot.material.name)
            target = component if target is None else target
            key = slot.material.as_pointer(), target
            if key not in seen:
                seen.add(key)
                values = resolve_material_images(slot.material, game, target, folder, catalogs)
                for _identity, image in values:
                    pointer = image.as_pointer()
                    if pointer not in checked_images:
                        _resource, filename, content = _image_payload(image)
                        _register_payload(payloads, payload_names, filename, content)
                        checked_images.add(pointer)
    _validate_payload_destinations(Path(bpy.path.abspath(cfg.mod_output_folder)), payloads)


def capture_merger(merger, cfg, game):
    """Snapshot file bindings and triangle runs before Join removes source IDs."""
    validate_mode(cfg, game, merger.context.scene)
    folder = Path(bpy.path.abspath(cfg.object_source_folder))
    catalogs = {}
    for index, component in enumerate(merger.components):
        comp = getattr(component, "id", index)
        for temp in component.objects:
            obj = temp.object
            by_slot = {}
            indices = array("i", [0]) * len(obj.data.polygons)
            obj.data.polygons.foreach_get("material_index", indices)
            used_slots = set(indices)
            for slot_id, slot in enumerate(obj.material_slots):
                if slot_id not in used_slots:
                    continue
                material = slot.material
                by_slot[slot_id] = resolve_material_images(material, game, comp, folder, catalogs)
            segments = []
            for polygon_index, slot_id in enumerate(indices):
                bindings = by_slot.get(slot_id, ())
                signature = tuple((identity, image.as_pointer()) for identity, image in bindings)
                if segments and segments[-1][3] == signature:
                    segments[-1][0] += 3
                else:
                    segments.append([3, temp.index_offset + polygon_index * 3, bindings, signature])
            temp.material_draw_segments = tuple((count, offset, values) for count, offset, values, _ in segments)
            temp.material_component_id = comp


def _image_source_basename(image):
    """Recover the file basename without Blender's datablock duplicate suffix."""
    source_name = str(getattr(image, "filepath_raw", "") or image.filepath or image.name)
    basename = source_name.replace("\\", "/").rsplit("/", 1)[-1]
    while Path(basename).suffix.lower() not in ini.MATERIAL_IMAGE_EXTENSIONS:
        stem, separator, duplicate = basename.rpartition(".")
        if not separator or len(duplicate) != 3 or not duplicate.isdigit():
            break
        basename = stem
    return basename


def _image_payload(image):
    if image.source != "FILE" or image.is_dirty:
        raise ValueError(iface_("{0}: save and reload the image before export; generated, tiled, animated, and unsaved images are not exported").format(image.name))
    basename = _image_source_basename(image)
    suffix = Path(basename).suffix.lower()
    if suffix not in ini.MATERIAL_IMAGE_EXTENSIONS:
        raise ValueError(iface_("Unsupported material image file type: {0}").format(suffix))
    filename = f"Textures/{basename}"
    try:
        resource = ini.material_resource_name(filename)
    except ini.BindingError as exc:
        raise ValueError(iface_(exc.message).format(*exc.values)) from exc
    content = bytes(image.packed_file.data) if image.packed_file else Path(
        bpy.path.abspath(image.filepath, library=image.library)).read_bytes()
    if not content:
        raise ValueError(iface_("Empty material image: {0}").format(image.name))
    return resource, filename, content


def _register_payload(payloads, payload_names, filename, content):
    """Register one exact basename and reject Windows-equivalent collisions."""
    folded = filename.casefold()
    prior_name = payload_names.get(folded)
    if prior_name is not None:
        if prior_name != filename or payloads[prior_name] != content:
            raise ValueError(iface_("Material image output conflicts with an existing file: {0}").format(filename))
        return
    payload_names[folded] = filename
    payloads[filename] = content


def _validate_payload_destinations(root, payloads):
    """Verify append-only delivery and exact casing before any payload write."""
    existing = set()
    for filename, content in payloads.items():
        destination = root / filename
        matches = ([entry for entry in destination.parent.iterdir()
                    if entry.name.casefold() == destination.name.casefold()]
                   if destination.parent.is_dir() else [])
        if not matches:
            continue
        if (len(matches) != 1 or matches[0].name != destination.name
                or not matches[0].is_file() or matches[0].read_bytes() != content):
            raise ValueError(iface_("Material image output conflicts with an existing file: {0}").format(str(destination)))
        existing.add(filename)
    return existing


def build_material_layer(maker, text, cfg, game):
    validate_mode(cfg, game, getattr(maker, "scene", None))
    draws, resources, payloads, payload_names, images = [], {}, {}, {}, {}
    # Allocate section names from the complete file set, not reordered draws.
    for component in maker.merged_object.components:
        for temp in component.objects:
            for _count, _offset, values in getattr(temp, "material_draw_segments", ()):
                for _identity, image in values:
                    pointer = image.as_pointer()
                    if pointer not in images:
                        payload = _image_payload(image)
                        _register_payload(payloads, payload_names, payload[1], payload[2])
                        images[pointer] = payload
    resource_names = ini.allocate_material_resource_names(payloads)
    for index, component in enumerate(maker.merged_object.components):
        for temp in component.objects:
            raw = getattr(temp, "material_draw_segments", ())
            if not raw or not any(values for _, _, values in raw):
                continue
            segments = []
            for count, offset, values in raw:
                replacements = {}
                for identity, image in values:
                    _base, filename, _content = images[image.as_pointer()]
                    resource = resource_names[filename]
                    if identity in replacements and replacements[identity] != resource:
                        raise ValueError(iface_("Two semantic inputs replace the same original texture differently"))
                    replacements[identity] = resource
                    resources[resource] = filename
                segments.append(ini.Segment(count, offset, tuple(sorted(replacements.items()))))
            draws.append(ini.Draw(getattr(temp, "material_component_id", index),
                                  temp.index_count, temp.index_offset, tuple(segments)))
    resource_map = ini.source_resources(text, [(str(texture.hash).lower(), texture.filename)
                                               for texture in maker.textures])
    if draws and not getattr(cfg, "copy_textures", True):
        raise ValueError(iface_("Enable texture copying when exporting material textures"))
    result, stats = ini.transform(text, draws, resource_map, resources,
                                  batch_draws=getattr(cfg, "material_texture_batching", True))
    maker.material_texture_payloads = payloads
    maker.material_texture_report = stats
    print("[MaterialTextures]", game, stats)
    return result


def _install_game(game, merger_cls, maker_cls, cfg_type, operator_cls, exporter_cls):
    original_stats = merger_cls.finalize_temp_objects_stats
    original_build = maker_cls.build_from_template
    original_write = maker_cls.write
    original_execute = operator_cls.execute
    original_verify = exporter_cls.verify_config
    cfg_type.material_texture_overrides = bpy.props.BoolProperty(
        name="Use Material Textures",
        description="Use semantic texture inputs for each material draw in Slot export; requires automatic material splitting. Original materials are preserved",
        default=False)

    cfg_type.material_texture_batching = bpy.props.BoolProperty(
        name="Group Draws by Texture",
        description="Stably group compatible temporary objects before index ranges and buffers are built, reducing repeated texture commands without renaming source objects. Disable to retain the previous export order",
        default=True)

    def finalize_temp_objects_stats(self):
        cfg = getattr(self.context.scene, SETTINGS[game])
        if enabled(cfg) and getattr(cfg, "material_texture_batching", True):
            validate_mode(cfg, game, self.context.scene)
            batching.prepare_merger(self, cfg, game, resolve_material_images, _image_payload)
        original_stats(self)
        if enabled(cfg):
            capture_merger(self, cfg, game)
        else:
            for component in self.components:
                for temp in component.objects:
                    if hasattr(temp, "material_draw_segments"):
                        del temp.material_draw_segments

    def build_from_template(self, context, cfg, template_string=None, with_checksum=False):
        self.material_texture_payloads = {}
        if not enabled(cfg):
            return original_build(self, context, cfg, template_string=template_string, with_checksum=with_checksum)
        validate_mode(cfg, game, context.scene)
        result = original_build(self, context, cfg, template_string=template_string, with_checksum=False)
        try:
            result = build_material_layer(self, result, cfg, game)
        except ini.BindingError as exc:
            raise ValueError(iface_(exc.message).format(*exc.values)) from exc
        if with_checksum:
            result = maker_cls.with_checksum(result)
        self.ini_string = result
        return result

    def write(self, ini_string=None, ini_path=None):
        payloads = getattr(self, "material_texture_payloads", {})
        root = Path(ini_path).parent if ini_path is not None else Path(bpy.path.abspath(self.cfg.mod_output_folder))
        created = []
        try:
            existing = _validate_payload_destinations(root, payloads)
            pending = [(root / filename, content) for filename, content in payloads.items()
                       if filename not in existing]
            for destination, content in pending:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as stream:
                    created.append(destination)
                    stream.write(content)
            return original_write(self, ini_string=ini_string, ini_path=ini_path)
        except Exception:
            for path in reversed(created):
                path.unlink(missing_ok=True)
            raise

    def execute(self, context):
        cfg = getattr(context.scene, SETTINGS[game])
        if enabled(cfg):
            try:
                validate_mode(cfg, game, context.scene)
            except Exception as exc:
                self.report({"ERROR"}, iface_("Material operation failed: {0}").format(exc))
                return {"CANCELLED"}
        return original_execute(self, context)

    def verify_config(self):
        original_verify(self)
        if enabled(self.cfg):
            preflight_materials(self.context, self.cfg, game)

    exporter_cls.verify_config = verify_config
    _EXPORT_PATCHES.append((exporter_cls, original_verify))
    merger_cls.finalize_temp_objects_stats = finalize_temp_objects_stats
    maker_cls.build_from_template = build_from_template
    maker_cls.write = write
    operator_cls.execute = execute
    _PATCHES.append((merger_cls, maker_cls, cfg_type, operator_cls, original_stats, original_build, original_write, original_execute))


def install():
    if _PATCHES:
        return
    from ..games.arknights_endfield._efmi_core.blender_export.blender_export import ObjectMergerEFMI, ModExporter as FirstExporter
    from ..games.arknights_endfield._efmi_core.blender_export.ini_maker import IniMaker as FirstMaker
    from ..games.arknights_endfield._efmi_core.addon.settings import VTEF_Settings
    from ..games.wuthering_waves._wwmi_core.blender_export.blender_export import ObjectMergerWWMI, ModExporter as SecondExporter
    from ..games.wuthering_waves._wwmi_core.blender_export.ini_maker import IniMaker as SecondMaker
    from ..games.wuthering_waves._wwmi_core.addon.settings import VTWW_Settings
    from ..games.arknights_endfield._efmi_core.addon.ui import VTEF_Export
    from ..games.wuthering_waves._wwmi_core.addon.ui import VTWW_Export
    _install_game("ENDFIELD", ObjectMergerEFMI, FirstMaker, VTEF_Settings, VTEF_Export, FirstExporter)
    _install_game("WUTHERING", ObjectMergerWWMI, SecondMaker, VTWW_Settings, VTWW_Export, SecondExporter)
    from ..games.wuthering_waves._wwmi_core.blender_export import blender_export as second_module
    _UV_PATCHES.append((second_module, second_module.copy_uv_layer))
    second_module.copy_uv_layer = batching.copy_uv_layer_snapshot


def remove():
    for module, copy_uv in reversed(_UV_PATCHES):
        module.copy_uv_layer = copy_uv
    _UV_PATCHES.clear()
    for exporter, verify in reversed(_EXPORT_PATCHES):
        exporter.verify_config = verify
    _EXPORT_PATCHES.clear()
    for merger, maker, cfg_type, operator, stats, build, write, execute in reversed(_PATCHES):
        merger.finalize_temp_objects_stats = stats
        maker.build_from_template = build
        maker.write = write
        operator.execute = execute
        del cfg_type.material_texture_batching
        del cfg_type.material_texture_overrides
    _PATCHES.clear()
