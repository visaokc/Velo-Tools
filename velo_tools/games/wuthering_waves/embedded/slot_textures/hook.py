# Export hook: wraps IniMaker.build_from_template (same idempotent
# install()/remove() pattern as embedded/lod/export_hook.py; _wwmi_core
# untouched). Installed AFTER the LOD hook so this wrapper is outermost and
# post-processes whatever template the inner layers rendered (stock merged,
# stock per-component or the velo LOD fork - transform.py is anchor-based and
# template-agnostic).
#
# The inner render runs with with_checksum=False; the checksum stamp is
# re-applied here after the transformation so IniMaker.is_ini_edited keeps
# protecting user-edited mod.ini files from silent overwrites.
#
# Degrade policy: any SlotStyleDegrade (missing usage json, unknown template
# structure, cross-scene export, value collision) falls back to the untouched
# stock hash-style output with a console explanation - never a half result.

import traceback

from ..._wwmi_core.blender_export import ini_maker as _im_module
from ..._wwmi_core.migoto_io.blender_interface.utility import resolve_path

from . import generator
from . import transform

_INSTALLED = False
_ORIG_BUILD_FROM_TEMPLATE = None

# Last export's report lines (operators may surface them to the UI).
last_report = []


def install():
    global _INSTALLED, _ORIG_BUILD_FROM_TEMPLATE
    if _INSTALLED:
        return

    _ORIG_BUILD_FROM_TEMPLATE = _im_module.IniMaker.build_from_template

    def _wrapped_build_from_template(self, context, cfg, template_string=None, with_checksum=False):
        if (not getattr(cfg, 'velo_slot_style_textures', False)
                or getattr(cfg, 'use_custom_template', False)
                or getattr(cfg, 'custom_template_live_update', False)):
            return _ORIG_BUILD_FROM_TEMPLATE(self, context, cfg,
                                             template_string=template_string,
                                             with_checksum=with_checksum)

        result = _ORIG_BUILD_FROM_TEMPLATE(self, context, cfg,
                                           template_string=template_string,
                                           with_checksum=False)
        del last_report[:]
        try:
            source_folder = resolve_path(cfg.object_source_folder)
            if (source_folder / 'CrossSceneRouting.json').is_file():
                raise generator.SlotStyleDegrade(
                    'cross-scene exports are not supported yet')
            forms, load_warnings = generator.load_forms(source_folder)
            textures = [(texture.hash, f'ResourceTexture{index}')
                        for index, texture in enumerate(self.textures)]
            plan = generator.build_plan(forms, textures, load_warnings)
            result = transform.apply(result, plan)
            for warning in plan.warnings:
                _report(f'[SlotTextures] WARNING: {warning}')
            for tex_hash, section in plan.blind_zone:
                _report(f'[SlotTextures] WARNING: texture {tex_hash} not present in any '
                        f'form map - stock hash section [{section}] kept as fallback')
            _report(f'[SlotTextures] Slot-style texture layer applied: {plan.stats}')
        except generator.SlotStyleDegrade as e:
            _report(f'[SlotTextures] Falling back to hash-style textures: {e}')
        except Exception:
            traceback.print_exc()
            _report('[SlotTextures] Unexpected error - falling back to hash-style textures.')

        if with_checksum:
            result = _im_module.IniMaker.with_checksum(result)
        self.ini_string = result
        return result

    _wrapped_build_from_template._velo_slot_hook = True
    _im_module.IniMaker.build_from_template = _wrapped_build_from_template
    _INSTALLED = True


def remove():
    global _INSTALLED, _ORIG_BUILD_FROM_TEMPLATE
    if not _INSTALLED:
        return
    _im_module.IniMaker.build_from_template = _ORIG_BUILD_FROM_TEMPLATE
    _ORIG_BUILD_FROM_TEMPLATE = None
    _INSTALLED = False


def _report(message: str):
    print(message)
    last_report.append(message)
