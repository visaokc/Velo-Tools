"""Monkey-patch hooks that integrate CrossIB into the EFMI-Tools export
pipeline without modifying the base addon.

We hook two methods on the base addon's `IniMaker`:

  1. `build_from_template` — after the base renders the ini text, we run a
     string-level post-processor that wraps each provider's draw block in the
     `if vs == ...` conditional, appends consumer borrow blocks, and appends
     all cross-IB Resource declarations / CustomShaders / ShaderOverrides.

  2. `write` — after the base writes mod.ini to disk, we copy the four HLSL
     shader files into the mod's `hlsl/` subfolder.

Both patches are reversible — `remove_patches()` restores the originals.
"""
import os
import re
import shutil
import sys
import traceback
from pathlib import Path

import bpy

from . import generator


# Originals captured at install time so we can restore on disable.
# Map: id(cls) -> (cls, orig_build_from_template, orig_write, module_name)
_patched = {}
_install_timer = None

_SUPPORTED_EFMI_MODULE_PREFIXES = (
    "velo_tools.games.arknights_endfield._efmi_core.",
)


_HLSL_DIR = Path(__file__).parent / "hlsl"
_HEADER_RE = re.compile(r'^\s*\[CommandList_Draw_Component(\d+)\]\s*$')
_TEX_OVERRIDE_RE = re.compile(r'^\s*\[TextureOverride_Component(\d+)(?:_LOD\d+)?\]\s*$')
_DRAW_COMMENT_RE = re.compile(r'^\s*;\s*Draw\s+(.+?)\s*$', re.IGNORECASE)
_DRAW_CALL_RE = re.compile(r'^\s*(?:drawindexedinstanced\b|draw\s*=)', re.IGNORECASE)
_CHECK_TEX_RE = re.compile(r'^\s*CheckTextureOverride\s*=\s*ps-t(\d+)\s*$', re.IGNORECASE)

# Idempotency markers. Each injected CommandList body is wrapped between BODY
# markers; the appended velo resources between RES markers. EFMI regenerates and
# full-overwrites mod.ini from a fresh Jinja template every export (ini_maker.py),
# so injection always runs on pristine text and never nests in normal use. The
# BODY markers let injection DETECT already-injected content (only reachable if an
# already-injected string is fed back in) and skip it instead of nesting; the RES
# block is stripped (not duplicated) on such a re-injection. No pristine-body copy
# is embedded — EFMI's own `; SHA256 CHECKSUM:` footer (re-added after injection)
# covers tamper-detection, and the full-overwrite makes restore-and-reinject moot.
_CROSSIB_BODY_BEGIN = "; >>> VELO CrossIB body (auto-generated; do not edit) >>>"
_CROSSIB_BODY_END = "; <<< VELO CrossIB body end <<<"
_CROSSIB_RES_BEGIN = "; >>> VELO CrossIB resources (auto-generated; do not edit) >>>"
_CROSSIB_RES_END = "; <<< VELO CrossIB resources end <<<"
_SHADER_OVERRIDE_HEADER_RE = re.compile(r'^\s*\[ShaderOverrideCrossIB_\w+\]\s*$')
_CROSSIB_SHADER_INI_NAME = "ShaderOverride.ini"


# ───────────────────────── locate base addon ─────────────────────────

def _is_inimaker(cls):
    if not isinstance(cls, type):
        return False
    if cls.__name__ != "IniMaker":
        return False
    if not (hasattr(cls, "build_from_template") and hasattr(cls, "write")):
        return False
    # Must have both as functions defined on the class itself (or inherited),
    # and the build_from_template signature must accept (context, cfg, ...).
    bft = getattr(cls, "build_from_template", None)
    return callable(bft)


def _find_inimaker_classes():
    """Search sys.modules for EFMI-family `IniMaker` classes with the expected
    methods. Velo Tools must not patch unrelated exporters such as WWMI-Tools.
    """
    found = []
    seen_ids = set()
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        if not _is_supported_efmi_module(name):
            continue
        cls = getattr(mod, "IniMaker", None)
        if cls is None or not _is_inimaker(cls):
            continue
        if id(cls) in seen_ids:
            continue
        seen_ids.add(id(cls))
        found.append((name, cls))
    return found


def _is_supported_efmi_module(module_name: str) -> bool:
    return any(module_name.startswith(prefix) for prefix in _SUPPORTED_EFMI_MODULE_PREFIXES)


# ───────────────────────── injection logic ─────────────────────────

def _split_sections(text):
    """Split ini text into [(header_line_or_None, [body_lines...]), ...]."""
    lines = text.split("\n")
    sections = []
    cur_header = None
    cur_body = []
    for line in lines:
        stripped = line.lstrip()
        is_header = (
            stripped.startswith("[")
            and "]" in stripped
            and not stripped.startswith(";")
        )
        if is_header:
            if cur_header is not None or cur_body:
                sections.append((cur_header, cur_body))
            cur_header = line
            cur_body = []
        else:
            cur_body.append(line)
    sections.append((cur_header, cur_body))
    return sections


def _join_sections(sections):
    out = []
    for header, body in sections:
        if header is not None:
            out.append(header)
        out.extend(body)
    return "\n".join(out)


def _wrap_injected_body(new_body):
    """Wrap an injected CommandList body between BODY markers so a later
    injection can DETECT it as already-injected and skip it (avoiding nested
    `if vs` blocks). No pristine-body copy is embedded: EFMI full-overwrites
    mod.ini from a fresh template every export, so injection always runs on
    pristine text and never needs to restore the original."""
    return [_CROSSIB_BODY_BEGIN] + list(new_body) + [_CROSSIB_BODY_END]


def _body_is_injected(body):
    """True if this CommandList body already carries a velo CrossIB injection
    (detected by the BODY begin marker)."""
    return any(line.strip() == _CROSSIB_BODY_BEGIN for line in body)


def _strip_injected_resources(ini_text):
    """Remove any previously appended velo CrossIB resource block(s) so they are
    regenerated rather than duplicated on re-injection."""
    out = []
    skipping = False
    for line in ini_text.split("\n"):
        stripped = line.strip()
        if stripped == _CROSSIB_RES_BEGIN:
            skipping = True
            continue
        if stripped == _CROSSIB_RES_END:
            skipping = False
            continue
        if not skipping:
            out.append(line)
    return "\n".join(out)


def split_out_shader_override_ini(ini_text):
    """Pull the global `[ShaderOverrideCrossIB_*]` sections out of the appended
    velo resource block into a standalone ini string, leaving the remaining
    resources (CustomShaders/Resources) wrapped in RES markers inside mod.ini.

    Returns (mod_ini_text, shader_override_ini_text). shader text is "" when
    there are no shader-override sections (e.g. no cross-IB mappings)."""
    lines = ini_text.split("\n")
    try:
        begin = lines.index(_CROSSIB_RES_BEGIN)
        end = lines.index(_CROSSIB_RES_END)
    except ValueError:
        return ini_text, ""
    res_sections = _split_sections("\n".join(lines[begin + 1:end]))
    kept, shader = [], []
    for header, body in res_sections:
        if header is not None and _SHADER_OVERRIDE_HEADER_RE.match(header):
            shader.append((header, body))
        else:
            kept.append((header, body))
    mod_lines = lines[:begin + 1] + _join_sections(kept).split("\n") + lines[end:]
    shader_text = _join_sections(shader).strip("\n")
    return "\n".join(mod_lines), shader_text


def _find_draw_region(body):
    """Return (start_idx, end_idx) for the contiguous draw block in a CommandList
    body, or None if no `; Draw ` marker is found.

    A draw region begins at the first `; Draw ...` comment and extends through
    every subsequent line that is part of a draw (`drawindexedinstanced`,
    `draw = from_caller`, draw-local comments, ini toggle `if $...` / `endif`
    wrappers, additional `; Draw ...` comments, or blank lines that separate
    consecutive draw groups).
    """
    start = None
    for i, line in enumerate(body):
        if line.lstrip().startswith("; Draw "):
            start = i
            break
    if start is None:
        return None

    end = start
    depth = 0
    i = start
    while i < len(body):
        s = body[i].lstrip()
        if s.startswith("; Draw "):
            end = i
        elif s.startswith(";"):
            end = i
        elif _DRAW_CALL_RE.match(s):
            end = i
        elif s.startswith("if ") and i > start:
            depth += 1
            end = i
        elif s.startswith("endif") and depth > 0:
            depth -= 1
            end = i
        elif s == "":
            # Allow blank line only if a draw line follows
            j = i + 1
            while j < len(body) and body[j].strip() == "":
                j += 1
            if j < len(body):
                ns = body[j].lstrip()
                if ns.startswith("; Draw ") or _DRAW_CALL_RE.match(ns):
                    i = j
                    continue
            break
        else:
            break
        i += 1
    return (start, end)


def _split_draw_entries(draw_lines):
    """Split a slice of draw-region lines into ordered entries.

    Each entry is a dict { 'name': str|None, 'lines': [str, ...] }.

    A new entry begins on every `; Draw <name>` comment. Any draw call or
    other lines appearing before the first Draw comment go into a leading entry
    with name=None (preserved as-is, routed unconditionally outside the if block
    since we cannot tell which sub-mesh they belong to).
    """
    entries = []
    cur = {"name": None, "lines": []}
    for line in draw_lines:
        m = _DRAW_COMMENT_RE.match(line)
        if m:
            if cur["lines"]:
                entries.append(cur)
            cur = {"name": m.group(1).strip(), "lines": [line]}
        else:
            cur["lines"].append(line)
    if cur["lines"]:
        entries.append(cur)
    return entries


def _process_section_body(body, comp_id, providers_map, consumers_map):
    in_providers = comp_id in providers_map
    is_consumer = comp_id in consumers_map
    if not in_providers and not is_consumer:
        return body

    region = _find_draw_region(body)
    if region is None:
        return body
    start, end = region

    new_body = list(body[:start])

    if in_providers:
        info = providers_map[comp_id]
        # CrossIB v1.5 layout for provider/consumer-self CommandLists:
        #   for each mapping_block (one per user-mapping that uses this
        #   component as provider; OR a single self-extract block for a
        #   consumer-only component):
        #       <header>     ; opens `if vs == ...`, runs Extract/Record/Redirect
        #       <draws>      ; this mapping's sub-meshes (4-space indented)
        #       endif
        #   ; --- Non-borrowed sub-meshes (drawn in every pass) ---
        #   <leftover draws unindented>
        #
        # Each draw entry is routed by its `; Draw <name>` comment into the
        # first mapping_block whose borrowed_objs contains its name. Entries
        # whose name is in NO mapping_block fall through to the non-borrowed
        # group (drawn unconditionally; fixes the v1.3 "black sub-mesh" bug).
        entries = _split_draw_entries(body[start:end + 1])
        block_draws = [[] for _ in info["mapping_blocks"]]
        outside = []
        for entry in entries:
            placed = False
            for i, mb in enumerate(info["mapping_blocks"]):
                if entry["name"] is not None and entry["name"] in mb["borrowed_objs"]:
                    block_draws[i].extend(entry["lines"])
                    placed = True
                    break
            if not placed:
                outside.extend(entry["lines"])

        # CrossIB v1.6: merge mapping_blocks that share the same header
        # (i.e. identical vs filter + identical Extract/Record/Redirect setup
        # — which for a given provider means identical vs_cond). Multiple
        # user-mappings that pick the same vs filter on the same provider
        # collapse into ONE `if vs == ...` block whose body lists all
        # borrowed sub-meshes back-to-back. Order of first appearance is
        # preserved so the ini stays diff-friendly.
        merged = []  # list[(header, [draw_lines...])]
        header_index = {}
        for i, mb in enumerate(info["mapping_blocks"]):
            h = mb["header"]
            if h in header_index:
                merged[header_index[h]][1].extend(block_draws[i])
            else:
                header_index[h] = len(merged)
                merged.append((h, list(block_draws[i])))

        if info.get("is_consumer_only"):
            new_body.append(
                "; Cross-IB: Consumer self-extract \u2014 record own CB1+bones so the "
                "color-pass borrow block can preserve this consumer's transform"
            )
        else:
            new_body.append(
                "; Cross-IB: Provider \u2014 extract/record bones, draw borrowed mesh INSIDE this if (mappings sharing the same vs filter are merged)"
            )
        for header, draws in merged:
            new_body.extend(header.split("\n"))
            for ln in draws:
                new_body.append(("    " + ln) if ln.strip() else ln)
            new_body.append("endif")
        if outside:
            new_body.append("; --- Non-borrowed sub-meshes (drawn in every pass) ---")
            new_body.extend(outside)
    else:
        new_body.extend(body[start:end + 1])

    if is_consumer:
        new_body.extend(consumers_map[comp_id].split("\n"))

    rest = list(body[end + 1:])
    trailing_blanks = []
    while rest and rest[-1].strip() == "":
        trailing_blanks.insert(0, rest.pop())
    new_body.extend(rest)
    new_body.append("post vs-cb1 = null")
    new_body.append("post vs-t0 = null")
    new_body.append("post cs-t2 = null")
    new_body.extend(trailing_blanks)
    return _wrap_injected_body(new_body)


def _ensure_extra_ps_slot_checks(body, comp_id, extra_ps_slots_map=None):
    if not extra_ps_slots_map:
        return body

    desired_slots = [int(slot) for slot in extra_ps_slots_map.get(comp_id, [])]
    if not desired_slots:
        return body

    existing_slots = set()
    for line in body:
        match = _CHECK_TEX_RE.match(line)
        if match:
            existing_slots.add(int(match.group(1)))

    missing_slots = [slot for slot in desired_slots if slot not in existing_slots]
    if not missing_slots:
        return body

    new_body = []
    inserted = False
    for line in body:
        new_body.append(line)
        if not inserted and line.strip() == r"run = CommandList\EFMIv1\OverrideTextures":
            indent = line[:len(line) - len(line.lstrip())]
            for slot in missing_slots:
                new_body.append(f"{indent}CheckTextureOverride = ps-t{slot}")
            inserted = True

    if not inserted:
        new_body = [f"CheckTextureOverride = ps-t{slot}" for slot in missing_slots] + list(body)

    return new_body


def _build_synthetic_consumer_section_body(comp_id, providers_map, consumers_map, extra_ps_slots_map=None):
    info = providers_map.get(comp_id, {})
    body = [r"run = CommandList\EFMIv1\OverrideTextures"]

    if info.get("mapping_blocks"):
        body.append(
            "; Cross-IB: Consumer self-extract — record own CB1+bones so the "
            "color-pass borrow block can preserve this consumer's transform"
        )
        for mapping_block in info["mapping_blocks"]:
            body.extend(mapping_block["header"].split("\n"))
            body.append("endif")

    if comp_id in consumers_map:
        body.extend(consumers_map[comp_id].split("\n"))

    body.append("post vs-cb1 = null")
    body.append("post vs-t0 = null")
    body.append("post cs-t2 = null")
    body.append("")
    return _ensure_extra_ps_slot_checks(body, comp_id, extra_ps_slots_map)


def _enable_textureoverride_commandlist(body, comp_id):
    run_line = f"run = CommandList_Draw_Component{comp_id}"
    commented_run_line = f"; {run_line}"

    new_body = []
    has_run = False
    insert_after = None

    for line in body:
        stripped = line.strip()
        if stripped == '; Draw skipped: No matching custom components found':
            continue
        if stripped == commented_run_line:
            if not has_run:
                indent = line[:len(line) - len(line.lstrip())]
                new_body.append(f"{indent}{run_line}")
                has_run = True
            continue
        if stripped == run_line:
            has_run = True
        new_body.append(line)
        if stripped == 'handling = skip':
            insert_after = len(new_body)

    if not has_run and insert_after is not None:
        handling_line = new_body[insert_after - 1]
        indent = handling_line[:len(handling_line) - len(handling_line.lstrip())] or '    '
        new_body.insert(insert_after, f"{indent}{run_line}")

    return new_body


def inject_cross_ib(ini_text, settings, extracted_object, merged_object, buffers, extra_ps_slots_map=None, source_folder=None, frame_dump_folder=None, cfg=None, context=None):
    providers_map, consumers_map, resources_str = generator.build_cross_ib(
        settings, extracted_object, buffers, merged_object,
        source_folder=source_folder,
        frame_dump_folder=frame_dump_folder,
        cfg=cfg,
        context=context,
    )
    # Idempotency: drop any previously appended velo resource block, and (in the
    # loop below) skip any CommandList body that is already injected. EFMI
    # overwrites mod.ini from a fresh template each export, so this only guards a
    # fed-back already-injected ini — re-injection never nests `if vs` blocks.
    ini_text = _strip_injected_resources(ini_text)
    sections = _split_sections(ini_text)
    if not providers_map and not consumers_map:
        return _join_sections(sections)

    existing_commandlists = set()
    for header, _body in sections:
        if header is None:
            continue
        match = _HEADER_RE.match(header)
        if match:
            existing_commandlists.add(int(match.group(1)))

    synthetic_consumer_ids = sorted(
        comp_id
        for comp_id, info in providers_map.items()
        if info.get("is_consumer_only") and comp_id not in existing_commandlists
    )

    out_sections = []
    for header, body in sections:
        if header is not None:
            m = _HEADER_RE.match(header)
            if m:
                comp_id = int(m.group(1))
                # Skip an already-injected body (only reachable if an injected
                # string is fed back in) so we never nest `if vs` blocks.
                if not _body_is_injected(body):
                    body = _process_section_body(body, comp_id, providers_map, consumers_map)
                    body = _ensure_extra_ps_slot_checks(body, comp_id, extra_ps_slots_map)
            else:
                texture_match = _TEX_OVERRIDE_RE.match(header)
                if texture_match:
                    comp_id = int(texture_match.group(1))
                    if comp_id in synthetic_consumer_ids:
                        body = _enable_textureoverride_commandlist(body, comp_id)
        out_sections.append((header, body))

    for comp_id in synthetic_consumer_ids:
        out_sections.append(
            (
                f"[CommandList_Draw_Component{comp_id}]",
                _build_synthetic_consumer_section_body(comp_id, providers_map, consumers_map, extra_ps_slots_map),
            )
        )

    result = _join_sections(out_sections)
    if resources_str:
        if not result.endswith("\n"):
            result += "\n"
        result += (
            _CROSSIB_RES_BEGIN + "\n"
            + resources_str.rstrip("\n") + "\n"
            + _CROSSIB_RES_END + "\n"
        )
    return result


# ─────────────────── legacy vb3 = vb0 helper ───────────────────
#
# EFMI 0.4.3 intentionally binds the base draw stack with
# `vb3 = ref Resource_Component*_VB0`. Do not run this helper on the full
# upstream ini text. Velo CrossIB still emits `vb3 = vb0` inside injected
# borrow blocks after rebinding provider `vb0`; that is handled in generator.py.

_VB3_RE = re.compile(r'(?im)^\s*vb3\s*=')


def _ensure_vb3(body):
    """Return (new_body, inserted_count) — insert `vb3 = vb0` right before the
    first `; Draw ...` line if no `vb3 = ...` assignment exists anywhere
    earlier in this section body."""
    region = _find_draw_region(body)
    if region is None:
        return body, 0
    start, _end = region
    # Search the pre-draw portion for any existing vb3 assignment.
    for line in body[:start]:
        if _VB3_RE.match(line):
            return body, 0
    indent = ""
    if start < len(body):
        leading = body[start]
        indent = leading[:len(leading) - len(leading.lstrip())]
    new_body = list(body[:start]) + [f"{indent}vb3 = vb0"] + list(body[start:])
    return new_body, 1


def inject_vb3_fix(ini_text):
    """Walk every `[CommandList_Draw_Component#]` section and ensure each one
    sets `vb3 = vb0` before the draw call. Returns the (possibly) modified
    ini text."""
    sections = _split_sections(ini_text)
    total_inserted = 0
    out_sections = []
    for header, body in sections:
        if header is not None and _HEADER_RE.match(header):
            body, inserted = _ensure_vb3(body)
            total_inserted += inserted
        out_sections.append((header, body))
    if total_inserted > 0:
        print(f"[CrossIB] vb3=vb0 fix: injected into {total_inserted} component section(s).")
    else:
        print("[CrossIB] vb3=vb0 fix: nothing to do (base addon already emits it, or no components found).")
    return _join_sections(out_sections)


# ───────────────────────── patched methods ─────────────────────────

def _patched_build_from_template(self, context, cfg, template_string=None, with_checksum=False):
    entry = _patched.get(id(type(self)))
    orig = entry[1] if entry else None
    if orig is None:
        # Fallback: shouldn't happen, but be defensive.
        return None
    # Always render without checksum so we can post-process, then re-add it.
    result = orig(self, context, cfg, template_string=template_string, with_checksum=False)

    settings = getattr(bpy.context.scene, "crossib_settings", None)
    if settings is not None and settings.enabled:
        if len(settings.mappings) > 0:
            print(f"[CrossIB] Injecting {len(settings.mappings)} mapping(s) into mod.ini...")
            try:
                source_folder = getattr(self.cfg, "object_source_folder", None)
                if source_folder:
                    source_folder = bpy.path.abspath(source_folder)
                frame_dump_folder = getattr(settings, "frame_dump_folder", "")
                if frame_dump_folder:
                    frame_dump_folder = bpy.path.abspath(frame_dump_folder)
                    print(f"[CrossIB] Using manual frame dump folder: {frame_dump_folder}")
                else:
                    frame_dump_folder = None
                    print("[CrossIB] Using automatic frame dump discovery from source folder.")
                # Consume baked CrossIB.json/ShaderOverride.ini if present; else
                # generate them from the frame dump (old-folder fallback) so this
                # and every later export is scan-free.
                from .sidecar import ensure_crossib_sidecars, load_crossib_json
                if load_crossib_json(source_folder) is None:
                    if ensure_crossib_sidecars(source_folder, frame_dump_folder):
                        print("[CrossIB] Generated CrossIB.json + ShaderOverride.ini from frame dump.")
                    else:
                        print("[CrossIB] No CrossIB.json sidecar and no frame dump set; "
                              "falling back to a live frame-dump scan. Pick a frame dump "
                              "folder (CrossIB panel) to bake sidecars for scan-free re-exports.")
                result = inject_cross_ib(
                    result, settings,
                    getattr(self, "extracted_object", None),
                    getattr(self, "merged_object", None),
                    getattr(self, "buffers", None),
                    getattr(self, "component_extra_ps_slots", None),
                    source_folder,
                    frame_dump_folder,
                    self.cfg,
                    context,
                )
                print("[CrossIB] Injection done.")
            except Exception:
                print("[CrossIB] Injection failed:")
                traceback.print_exc()
            # Move the global ShaderOverride blocks into a standalone ini
            # (written next to mod.ini by _patched_write). mod.ini keeps only the
            # per-component logic + non-shader resources.
            try:
                result, self._crossib_shader_ini = split_out_shader_override_ini(result)
            except Exception:
                print("[CrossIB] ShaderOverride split failed:")
                traceback.print_exc()
                self._crossib_shader_ini = ""
        else:
            print("[CrossIB] No mappings; leaving EFMI 0.4.3 base draw stack unchanged.")
            self._crossib_shader_ini = ""
    elif settings is None:
        print("[CrossIB] WARNING: Scene.crossib_settings missing at export time.")
    elif not settings.enabled:
        print("[CrossIB] Skipped (Use Cross Index Buffer is OFF).")

    if with_checksum and hasattr(self, "with_checksum"):
        try:
            result = self.with_checksum(result)
        except Exception:
            print("[CrossIB] Checksum re-add failed:")
            traceback.print_exc()

    self.ini_string = result
    return result


def _patched_write(self, ini_string=None, ini_path=None):
    entry = _patched.get(id(type(self)))
    orig = entry[2] if entry else None
    if orig is None:
        return None
    out = orig(self, ini_string=ini_string, ini_path=ini_path)

    settings = getattr(bpy.context.scene, "crossib_settings", None)
    if settings is None or not settings.enabled or len(settings.mappings) == 0:
        return out

    try:
        if ini_path is None:
            mod_folder = Path(os.path.expandvars(os.path.expanduser(str(self.cfg.mod_output_folder))))
            ini_path = mod_folder / "mod.ini"
        hlsl_dst = Path(ini_path).parent / "hlsl"
        hlsl_dst.mkdir(parents=True, exist_ok=True)
        for src in _HLSL_DIR.glob("*.hlsl"):
            dst = hlsl_dst / src.name
            shutil.copy(src, dst)
            print(f"[CrossIB] Copied {src.name} → {dst}")

        # Standalone ShaderOverride.ini in the mod folder (3DMigoto auto-loads
        # every .ini in the mod tree, so no include is needed). Prefer copying
        # the extract-time ShaderOverride.ini verbatim (same name); else write the
        # split-derived content (equivalent); else drop a stale file.
        shader_path = Path(ini_path).parent / _CROSSIB_SHADER_INI_NAME
        baked = None
        try:
            from .sidecar import _resolve_source, SHADER_OVERRIDE_INI_NAME
            src = getattr(self.cfg, "object_source_folder", "")
            if src:
                cand = _resolve_source(bpy.path.abspath(src)) / SHADER_OVERRIDE_INI_NAME
                if cand.is_file():
                    baked = cand
        except Exception:
            baked = None
        shader_ini = getattr(self, "_crossib_shader_ini", "")
        if baked is not None:
            shutil.copy(baked, shader_path)
            print(f"[CrossIB] Copied {baked.name} → {shader_path}")
        elif shader_ini.strip():
            text = shader_ini if shader_ini.endswith("\n") else shader_ini + "\n"
            with open(shader_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"[CrossIB] Wrote {shader_path.name} "
                  f"({shader_ini.count('[ShaderOverrideCrossIB')} override block(s)).")
        elif shader_path.exists():
            shader_path.unlink()
            print(f"[CrossIB] Removed stale {shader_path.name}.")
    except Exception:
        print("[CrossIB] HLSL copy failed:")
        traceback.print_exc()

    return out


# ───────────────────────── install / remove ─────────────────────────

def _do_install():
    """Patch every IniMaker class found in sys.modules. Returns True if at
    least one class is patched (cumulatively over time)."""
    new_count = 0
    for name, cls in _find_inimaker_classes():
        if id(cls) in _patched:
            continue
        _patched[id(cls)] = (
            cls,
            cls.build_from_template,
            cls.write,
            name,
        )
        cls.build_from_template = _patched_build_from_template
        cls.write = _patched_write
        new_count += 1
        print(f"[CrossIB] Patched IniMaker in module: {name}")
    return len(_patched) > 0


def _retry_install():
    """Timer callback: keep scanning for new IniMaker classes (e.g. lazy-loaded
    modules) until we've tried for ~30s. Always patches additional classes if
    they appear later."""
    global _install_timer
    _do_install()
    _install_timer = (_install_timer or 0) + 1
    if _install_timer > 60:  # ~30s of retries (0.5s each)
        if not _patched:
            print("[CrossIB] WARNING: gave up looking for any IniMaker class. "
                  "Make sure EFMI-Tools is installed and enabled, then "
                  "disable+re-enable CrossIB.")
        _install_timer = None
        return None
    return 0.5


def install_patches():
    global _install_timer
    _do_install()
    # Always run the timer for a while to catch IniMaker classes from addons
    # that load AFTER us (e.g. when CrossIB is enabled before EFMI-Tools).
    _install_timer = 0
    if not bpy.app.timers.is_registered(_retry_install):
        bpy.app.timers.register(_retry_install, first_interval=0.5)


def remove_patches():
    global _install_timer
    if bpy.app.timers.is_registered(_retry_install):
        try:
            bpy.app.timers.unregister(_retry_install)
        except ValueError:
            pass
    _install_timer = None
    for cls_id, (cls, orig_bft, orig_w, name) in list(_patched.items()):
        try:
            cls.build_from_template = orig_bft
            cls.write = orig_w
            print(f"[CrossIB] Restored IniMaker in module: {name}")
        except Exception:
            traceback.print_exc()
    _patched.clear()
    print("[CrossIB] Patches removed.")
