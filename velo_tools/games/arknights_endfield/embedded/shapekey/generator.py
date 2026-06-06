"""Generate INI sections to inject for ShapeKey export (v0.2 / TheHerta3 architecture).

Fundamental difference from v0.1
-------------------
v0.1 hand-built a UAV with `_VB0_Original + copy_desc + bind_flags`; in Endfield
this path repeatedly blew up the mesh in practice. v0.2 switches to TheHerta3's
production-proven three-stage form:

    [Resource_Component<cid>_Position_0]        <- pristine, reuses Meshes/VB0.buf
    cs-u5 = copy Resource_Component<cid>_Position_0
    Resource_Component<cid>_VB0 = ref cs-u5

When compiling the shader, use struct{float3 position; uint normal;} to keep the normal bit pattern.

Resource naming reference table
---------------
    Resource_Component<cid>_Position_0              (stride=16, file=Component<cid>_VB0.buf)
    Resource_Component<cid>_Position_S<slot>_delta  (stride=12)
    Resource_Component<cid>_Position_S<slot>_map    (stride=4, format=R32_SINT)
    Resource_Component<cid>_Position_FreqIndices    (stride=4)
    [CustomShader_ShapeKeyAnim_C<cid>]              (one per component)

IniParams index
---------------
Each shape key name occupies one freq channel:
    name #0 -> x100 = $Freq_<name0>    (IniParams[100].x)
    name #1 -> x101 = $Freq_<name1>
    ...
Export-slot note: Blender Deform numbers are source ids. The baker maps them
to exported shader slots, then channel_idx == shader_slot drives x100+channel.
"""


import re


MAX_SLOTS = 24
MERGED_MAX_SLOTS = 128
THREADS_PER_GROUP = 64
ACTIVE_SLOT_SUM_PER_LINE = 15


# ---------------------------------------------------------------- helpers

VAR_PREFIX = "Shape_"

_MULTI_UNDERSCORE_RE = re.compile(r"_+")
_ACTIVE_SLOT_BLOCK_RE = re.compile(
    r"// VELO_ACTIVE_SHAPE_SLOTS_BEGIN.*?// VELO_ACTIVE_SHAPE_SLOTS_END",
    re.DOTALL,
)


def _is_ascii_identifier(raw: str) -> bool:
    """True iff raw is non-empty pure ASCII letters/digits/underscores."""
    if not raw:
        return False
    for ch in raw:
        if not (ch.isascii() and (ch.isalnum() or ch == "_")):
            return False
    return True


def _has_ascii_letters(raw: str) -> bool:
    for ch in raw or "":
        if ch.isascii() and ch.isalpha():
            return True
    return False


def _capitalize_ascii_word(raw: str) -> str:
    if not raw:
        return raw
    first = raw[0]
    if first.isascii() and first.isalpha() and first.islower():
        return first.upper() + raw[1:]
    return raw


def _salvage_ascii_identifier(raw: str) -> str:
    """Try to salvage a readable ASCII identifier from mixed names.

    Rules:
      - Pure Chinese / no English letters -> give up and let caller fall back.
      - Keep ASCII letters/digits/underscores.
      - Spaces become word boundaries; alpha words after spaces are capitalized
        and separated with a single underscore.
      - Digits after spaces attach to the previous token (`Down 1` -> `Down1`).
      - Other illegal characters are ignored, but still mark a word boundary so
        the next ASCII word starts in uppercase (`rope-down` -> `RopeDown`).
    """
    if not _has_ascii_letters(raw):
        return ""

    out = []
    pending_boundary = None

    for ch in raw or "":
        if ch.isascii() and ch.isalpha():
            if not out:
                out.append(ch.upper() if ch.islower() else ch)
            elif pending_boundary == "space":
                if out[-1] != "_":
                    out.append("_")
                out.append(ch.upper() if ch.islower() else ch)
            elif pending_boundary == "separator":
                out.append(ch.upper() if ch.islower() else ch)
            else:
                out.append(ch)
            pending_boundary = None
            continue

        if ch.isascii() and ch.isdigit():
            out.append(ch)
            pending_boundary = None
            continue

        if ch == "_":
            if out and out[-1] != "_":
                out.append("_")
            pending_boundary = None
            continue

        if ch.isspace():
            if out:
                pending_boundary = "space"
            continue

        if out:
            pending_boundary = "separator"

    safe = "".join(out).strip("_")
    safe = _MULTI_UNDERSCORE_RE.sub("_", safe)
    return safe


def _safe_shape_identifier(name: str, slot: int) -> str:
    if _is_ascii_identifier(name):
        safe = name
    else:
        safe = _salvage_ascii_identifier(name)
        if not safe:
            safe = f"Key{int(slot)}"
    if safe and safe[0].isdigit():
        safe = "_" + safe
    return safe


def _shape_var_name(name: str, slot: int) -> str:
    """Build the global INI variable basename for a shape key.

    Rule:
      - If the Blender name part (after 'Deform <num>') is pure ASCII
        identifier-friendly, keep it: Shape_<name>
      - If the name is mixed Chinese/English or contains illegal characters,
        salvage the ASCII part into a readable identifier.
      - If there is no usable ASCII content (pure Chinese / pure symbols / ...),
        fall back to the deterministic placeholder Shape_Key<slot>.
    """
    return f"{VAR_PREFIX}{_safe_shape_identifier(name, slot)}"


def _validate_export_var_collisions(bake_results):
    """Abort if two different slots would collapse into the same $Shape_* var."""
    var_to_meta = {}
    collisions = []

    for r in bake_results:
        var = _shape_var_name(r["name"], r["slot"])
        meta = var_to_meta.setdefault(var, {"slots": set(), "names": set()})
        meta["slots"].add(int(r["slot"]))
        meta["names"].add(r["name"])

    for var, meta in sorted(var_to_meta.items()):
        if len(meta["slots"]) > 1:
            collisions.append((var, sorted(meta["slots"]), sorted(meta["names"])))

    if not collisions:
        return

    lines = ["形态键导出命名冲突，无法生成 ini。以下形态键在清洗后落成了同一个变量名："]
    for var, slots, names in collisions:
        lines.append(
            f"  - ${var} 同时对应槽位 {slots}，原始名字: {', '.join(repr(name) for name in names)}"
        )
    lines.append("修复方法：调整这些形态键的英文部分，使每个槽位导出的变量名唯一。")
    raise RuntimeError("\n".join(lines))


def _validate_channel_assignments(bake_results):
    """Abort if two different shape keys are assigned to one IniParams slot."""
    channel_to_meta = {}
    conflicts = []
    out_of_range = []

    for r in bake_results:
        ch = int(r["channel_idx"])
        if not (0 <= ch < 128):
            out_of_range.append((ch, int(r["slot"]), r["name"]))
            continue
        meta = (int(r["slot"]), r["name"])
        prev = channel_to_meta.setdefault(ch, meta)
        if prev != meta:
            conflicts.append((ch, prev, meta))

    if out_of_range:
        details = ", ".join(
            f"x{100 + ch}/Deform {slot} '{name}'"
            for ch, slot, name in out_of_range
        )
        raise RuntimeError(
            "ShapeKey channel index is outside the merged HLSL/IniParams "
            f"capacity: {details}"
        )

    if conflicts:
        details = ", ".join(
            f"x{100 + ch}: Deform {a[0]} '{a[1]}' vs Deform {b[0]} '{b[1]}'"
            for ch, a, b in conflicts
        )
        raise RuntimeError(
            "ShapeKey export channel conflict. The dense Deform slot map must "
            f"assign one shape key per IniParams channel: {details}"
        )


def _dispatch_group_count(vertex_count):
    vcount = int(vertex_count or 1)
    if vcount < 1:
        vcount = 1
    return (vcount + THREADS_PER_GROUP - 1) // THREADS_PER_GROUP


def collect_active_shader_slots(bake_results, merge_buffers=None):
    """Return active dense shader slots, sorted and deduplicated."""
    active = set()
    for r in bake_results:
        if merge_buffers is not None and bool(r.get("merge_buffers", False)) != bool(merge_buffers):
            continue
        if int(r.get("active_count", 0) or 0) <= 0:
            continue
        slot = int(r.get("shader_slot", r.get("channel_idx")))
        if not (0 <= slot < MERGED_MAX_SLOTS):
            raise RuntimeError(
                f"ShapeKey active shader slot {slot} is outside merged capacity "
                f"{MERGED_MAX_SLOTS}."
            )
        active.add(slot)
    return sorted(active)


def _build_active_slot_sum_lines(active_slots):
    slots = [int(s) for s in active_slots]
    if not slots:
        return ["$ShapeKeyOptActive = 0"]

    lines = []
    for idx in range(0, len(slots), ACTIVE_SLOT_SUM_PER_LINE):
        chunk = slots[idx:idx + ACTIVE_SLOT_SUM_PER_LINE]
        expr = " + ".join(f"x{100 + slot}" for slot in chunk)
        if idx == 0:
            lines.append(f"$ShapeKeyOptActive = {expr}")
        else:
            lines.append(f"$ShapeKeyOptActive = $ShapeKeyOptActive + {expr}")
    return lines


def _format_hlsl_slot_array(active_slots):
    slots = [int(s) for s in active_slots]
    count = len(slots)
    array_size = "ACTIVE_SHAPE_SLOT_COUNT" if slots else "1"
    if slots:
        rows = []
        for idx in range(0, len(slots), 16):
            rows.append("    " + ", ".join(str(s) for s in slots[idx:idx + 16]))
        body = ",\n".join(rows)
    else:
        body = "    0"
    return "\n".join([
        "// VELO_ACTIVE_SHAPE_SLOTS_BEGIN",
        f"#define ACTIVE_SHAPE_SLOT_COUNT {count}",
        f"static const int ACTIVE_SHAPE_SLOTS[{array_size}] = {{",
        body,
        "};",
        "// VELO_ACTIVE_SHAPE_SLOTS_END",
    ])


def render_merged_hlsl(template_text, active_slots):
    slots = sorted({int(s) for s in active_slots})
    for slot in slots:
        if not (0 <= slot < MERGED_MAX_SLOTS):
            raise RuntimeError(
                f"ShapeKey merged HLSL active slot {slot} is outside capacity "
                f"{MERGED_MAX_SLOTS}."
            )
    block = _format_hlsl_slot_array(slots)
    rendered, count = _ACTIVE_SLOT_BLOCK_RE.subn(block, template_text, count=1)
    if count != 1:
        raise RuntimeError(
            "ShapeKey merged HLSL template is missing the active slot marker block."
        )
    return rendered


# ---------------------------------------------------------------- resources

def build_resource_sections(bake_results):
    """Emit Resource sections ordered by (component_id, slot).

    The first result of each component decides whether that component uses
    merged mode or per-file mode (the baker guarantees all results of the same
    component are consistent).
    """
    out = []
    seen_components = set()
    by_component = {}
    for r in bake_results:
        by_component.setdefault(r["component_id"], []).append(r)

    for cid in sorted(by_component.keys()):
        results = sorted(by_component[cid], key=lambda x: x["slot"])
        merged = bool(results[0].get("merge_buffers", False))

        # Position_0 (pristine) — needed by both modes, reuses the VB0.buf already written by EFMI
        out.append(f"[Resource_Component{cid}_Position_0]")
        out.append("type = Buffer")
        out.append("stride = 16")
        out.append(f"filename = Meshes/Component{cid}_VB0.buf")
        out.append("")

        if merged:
            out.append(f"[Resource_Component{cid}_Position_deltas]")
            out.append("type = Buffer")
            out.append("stride = 12")
            out.append(f"filename = Meshes/Component{cid}_Position_deltas.buf")
            out.append("")

            out.append(f"[Resource_Component{cid}_Position_lookup]")
            out.append("type = Buffer")
            out.append("format = R32_SINT")
            out.append(f"filename = Meshes/Component{cid}_Position_lookup.buf")
            out.append("")
        else:
            out.append(f"[Resource_Component{cid}_Position_FreqIndices]")
            out.append("type = Buffer")
            out.append("stride = 4")
            out.append(f"filename = Meshes/Component{cid}_Position_freq_indices.buf")
            out.append("")

            for r in results:
                slot = r["slot"]
                out.append(f"[Resource_Component{cid}_Position_S{slot}_delta]")
                out.append("type = Buffer")
                out.append("stride = 12")
                out.append(f"filename = Meshes/{r['delta_filename']}")
                out.append("")

                out.append(f"[Resource_Component{cid}_Position_S{slot}_map]")
                out.append("type = Buffer")
                out.append("format = R32_SINT")
                out.append(f"filename = Meshes/{r['map_filename']}")
                out.append("")
    return out


# ---------------------------------------------------------------- compute shader block

def build_compute_shaders(bake_results, components_meta):
    """Output one CustomShader block per component. The two modes each use a different hlsl file."""
    out = []
    by_component = {}
    for r in bake_results:
        by_component.setdefault(r["component_id"], []).append(r)

    for cid in sorted(by_component.keys()):
        results = sorted(by_component[cid], key=lambda x: x["slot"])
        merged = bool(results[0].get("merge_buffers", False))
        vcount = components_meta.get(cid, {}).get("vertex_count", 0) or 1
        dispatch_groups = _dispatch_group_count(vcount)

        out.append(f"[CustomShader_ShapeKeyAnim_C{cid}]")

        if merged:
            out.append(f"cs-t51 = copy Resource_Component{cid}_Position_deltas")
            out.append(f"cs-t52 = copy Resource_Component{cid}_Position_lookup")
            out.append("cs = hlsl/shapekey_blend_merged.hlsl")
            out.append(f"cs-u5 = copy Resource_Component{cid}_Position_0")
            out.append(f"Resource_Component{cid}_VB0 = ref cs-u5")
            out.append(f"Dispatch = {dispatch_groups}, 1, 1")
            out.append("cs-u5 = null")
            out.append("cs-t51 = null")
            out.append("cs-t52 = null")
        else:
            t_regs_null = []
            for r in results:
                s = r["shader_slot"]
                if not (0 <= s < MAX_SLOTS):
                    continue
                delta_reg = 51 + s
                map_reg   = 75 + s
                out.append(f"cs-t{delta_reg} = copy Resource_Component{cid}_Position_S{r['slot']}_delta")
                out.append(f"cs-t{map_reg} = copy Resource_Component{cid}_Position_S{r['slot']}_map")
                t_regs_null.append(delta_reg)
                t_regs_null.append(map_reg)
            out.append(f"cs-t99 = copy Resource_Component{cid}_Position_FreqIndices")
            out.append("cs = hlsl/shapekey_blend.hlsl")
            out.append(f"cs-u5 = copy Resource_Component{cid}_Position_0")
            out.append(f"Resource_Component{cid}_VB0 = ref cs-u5")
            out.append(f"Dispatch = {dispatch_groups}, 1, 1")
            out.append("cs-u5 = null")
            for reg in sorted(set(t_regs_null)):
                out.append(f"cs-t{reg} = null")
            out.append("cs-t99 = null")
        out.append("")
    return out


# ---------------------------------------------------------------- constants

def build_constants_lines(bake_results):
    """Append to [Constants]: declare the global $Shape_ variable for each shape key name.

    Use 'global persist' so the user-tuned intensity is retained after the next
    game launch / mod reload. Non-persistent variables are reset to their initial
    value by 3DMigoto on reload.
    """
    if not bake_results:
        return []
    _validate_export_var_collisions(bake_results)
    seen = []
    for r in bake_results:
        var = _shape_var_name(r["name"], r["slot"])
        if var not in seen:
            seen.append(var)
    out = [
        "",
        "; --- ShapeKey: runtime optimization gate ---",
        "global $ShapeKeyOptEnable = 1",
        "global $ShapeKeyOptActive = 0",
        "global $ShapeKeyOptWasActive = 0",
        "",
        "; --- ShapeKey: per-name intensity globals (0.0 = off, 1.0 = full) ---",
    ]
    for var in seen:
        out.append(f"global persist ${var} = 0.0")
    return out


# ---------------------------------------------------------------- present

def build_present_lines(bake_results):
    """Append to [Present]:
        1. xN = $Shape_<name>   push the weight into IniParams[100+N].x
        2. post run = CustomShader_ShapeKeyAnim_C<cid>  dispatch compute
    """
    if not bake_results:
        return []
    _validate_export_var_collisions(bake_results)
    _validate_channel_assignments(bake_results)
    out = ["", "; --- ShapeKey: push weights into IniParams ---"]

    # Collect (channel_idx -> (name, slot)), deduplicate, then output sorted by channel_idx
    channel_to_meta = {}
    for r in bake_results:
        ch = int(r["channel_idx"])
        if ch not in channel_to_meta:
            channel_to_meta[ch] = (r["name"], r["slot"])
    for ch in sorted(channel_to_meta.keys()):
        name, slot = channel_to_meta[ch]
        out.append(f"x{100 + ch} = ${_shape_var_name(name, slot)}")

    active_slots = collect_active_shader_slots(bake_results)
    out.append("; --- ShapeKey: skip compute while all weights are inactive ---")
    out.extend(_build_active_slot_sum_lines(active_slots))
    out.append("if $ShapeKeyOptActive > 0")
    out.append("    $ShapeKeyOptActive = 1")
    out.append("endif")
    out.append("; --- ShapeKey: dispatch compute blend ---")
    out.append("if $ShapeKeyOptEnable == 0 || $ShapeKeyOptActive == 1 || $ShapeKeyOptWasActive == 1")
    for cid in sorted({r["component_id"] for r in bake_results}):
        out.append(f"    post run = CustomShader_ShapeKeyAnim_C{cid}")
    out.append("endif")
    out.append("post $ShapeKeyOptWasActive = $ShapeKeyOptActive")
    return out


# ---------------------------------------------------------------- top-level

def build_full_ini_appendix(bake_results, components_meta):
    if not bake_results:
        return ""
    lines = ["",
             "; ============================================================",
             "; ShapeKey (v0.2, TheHerta3 architecture) — auto-generated",
             "; ============================================================",
             ""]
    lines.extend(build_resource_sections(bake_results))
    lines.extend(build_compute_shaders(bake_results, components_meta))
    return "\n".join(lines)
