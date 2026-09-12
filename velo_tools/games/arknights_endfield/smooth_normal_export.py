"""Enable the existing outline normal selector for explicitly opted-in COLOR data.

Component-scoped activation uses IniParams[197].x, not a material CB mutation.
The native RG decoder, width calculation and already-enabled selector are kept.
"""
from __future__ import annotations

import re


PARAM_INDEX = 197
PARAM_TOKEN = 314159
MARKER = "; Native smooth-normal COLOR activation"
_PATCHES = []

# Keep each observed material layout paired with its selector and width member.
# Follow the selected normal into screen-space extrusion, not just any CB read.
PATTERN = (
    r"(?ms)\A"
    r"(?=.*^dcl_constantbuffer CB2\[4\d{3}\], dynamicIndexed\n)"
    r"(?=.*^dcl_constantbuffer CB3\[(?:(?P<layout14>14)|(?P<layout15>15)|16)\], immediateIndexed\n)"
    r"(?=.*^dcl_input (?P<color>v[45])\.xy\n)"
    r"(?=.*^dcl_output_siv o0\.xyzw, position\n)(?=.*^dcl_output o7\.x\n)"
    r"(?P<prefix>.*?^lt (?P<flag>r\d+)\.(?P<lane>[xyzw]), "
    r"l\(0\.500000\), cb3\[(?(layout14)13|(?(layout15)14|15))\]\.x\n)"
    r"(?=dp2 (?P<length>(?P<length_reg>r\d+)\.(?P<length_lane>[xyzw])), "
    r"(?P=color)\.xyxx, (?P=color)\.xyxx\n"
    r"min (?P=length), (?P=length), l\(1\.000000\)\n"
    r"add (?P=length), -(?P=length), l\(1\.000000\)\n"
    r"sqrt (?P=length), (?P=length)\n"
    r"mul (?P<normal>r\d+)\.xyz, [^\n]+\n"
    r"mad (?P=normal)\.xyz, [^\n]+\n"
    r"mul (?P=normal)\.xyz, [^\n]+\n"
    r"mul (?P=normal)\.xyz, (?P=normal)\.xyzx, (?P=color)\.yyyy\n"
    r"mad (?P=normal)\.xyz, (?P=color)\.xxxx, [^\n]+, (?P=normal)\.xyzx\n"
    r"mad (?P=normal)\.xyz, (?P=length_reg)\.(?P=length_lane){4}, "
    r"(?P<base_normal>r\d+\.[xyzw]{4}), (?P=normal)\.xyzx\n"
    r"movc (?P<selected>r\d+)\.xyz, (?P=flag)\.(?P=lane){4}, "
    r"(?P=normal)\.xyzx, (?P=base_normal)\n"
    r"(?:[^\n]*\n){1,16}"
    r"mul (?P<projected>r\d+)\.[xyzw]{2}, (?P=selected)\.yyyy, cb0\[33\]\.[xyzw]{4}\n"
    r"mad (?P=projected)\.(?P<projected_mask>[xyzw]{2}), cb0\[32\]\.[xyzw]{4}, "
    r"(?P=selected)\.xxxx, (?P=projected)\.[xyzw]{4}\n"
    r"mad (?P=projected)\.(?P=projected_mask), cb0\[34\]\.[xyzw]{4}, "
    r"(?P=selected)\.zzzz, (?P=projected)\.[xyzw]{4}\n"
    r"dp2 (?P<projected_length>r\d+\.[xyzw]), "
    r"(?P<projected_vector>(?P=projected)\.[xyzw]{4}), (?P=projected_vector)\n"
    r"rsq (?P=projected_length), (?P=projected_length)\n"
    r"(?:[^\n]*\n){1,8}"
    r"mul (?P<width_reg>r\d+)\.(?P<width_lane>[xyzw]), r\d+\.[xyzw], "
    r"cb3\[(?(layout14)12|(?(layout15)13|14))\]\.x\n"
    r"mul (?P=projected)\.(?P=projected_mask), (?P=width_reg)\.(?P=width_lane){4}, "
    r"(?P=projected)\.[xyzw]{4}\n"
    r"(?:[^\n]*\n){1,6}"
    r"mul r\d+\.xy, (?P=projected_vector), l\(0\.005000, 0\.005000, 0\.000000, 0\.000000\)\n"
    r"(?:[^\n]*\n){1,20}(?:add|mad) o0\.xy, (?P=projected_vector), [^\n]+\n)"
)
REPLACEMENT = (
    r"${prefix}"
    r"ld_indexable(texture1d)(float,float,float,float) ${normal_gate}.xyzw, "
    rf"l({PARAM_INDEX}, 0, 0, 0), t120.xyzw\n"
    rf"eq ${{normal_gate}}.x, ${{normal_gate}}.x, l({PARAM_TOKEN}.000000)\n"
    r"or ${flag}.${lane}, ${flag}.${lane}, ${normal_gate}.x\n"
)
SHADER_BLOCK = (
    f"\n{MARKER}\n"
    "; Only the native selector is extended; COLOR and outline width are unchanged.\n"
    "[ShaderRegexSmoothNormalColor]\n"
    "shader_model = vs_5_0\n"
    "temps = normal_gate\n\n"
    "[ShaderRegexSmoothNormalColor.Pattern]\n" + PATTERN + "\n\n"
    "[ShaderRegexSmoothNormalColor.Pattern.Replace]\n" + REPLACEMENT + "\n\n"
    "[ShaderRegexSmoothNormalColor.InsertDeclarations]\n"
    "dcl_resource_texture1d (float,float,float,float) t120\n"
)

_HEADER = re.compile(r"^\s*\[([^\]]+)\]\s*(?:;.*)?$")
_OWNER = re.compile(r"^\s*ib\s*=\s*(?:ref(?:erence)?\s+)?Resource_Component(\d+)_IB\s*(?:;.*)?$", re.I)
_COLOR_BINDING = re.compile(r"^\s*vb1\s*=\s*(?:ref(?:erence)?\s+)?(Resource_Component\d+_VB1(?:_LOD\d+)?)\s*(?:;.*)?$", re.I)
_DRAW = re.compile(r"^(\s*)(drawindexed(?:instanced)?)\s*=\s*([^;]+?)(\s*;.*)?$", re.I)
_CHECKSUM = re.compile(r"^; SHA256 CHECKSUM: [0-9a-f]+\s*$", re.M | re.I)


def _compatible_layout(buffer):
    layout = getattr(buffer, "layout", None)
    if layout is None or layout.stride not in (12, 20):
        return False
    fields = [(element.get_name(), element.format.value, element.offset)
              for element in layout.semantics]
    if layout.stride == 12:
        return fields == [("TEXCOORD.xy", "R32G32_FLOAT", 0),
                          ("COLOR", "R8G8B8A8_SNORM", 8)]
    # An optional second UV or preserved field precedes COLOR in padded streams.
    return (len(fields) == 3
            and fields[0] == ("TEXCOORD.xy", "R32G32_FLOAT", 0)
            and fields[1] in (("UNKNOWN", "R32G32_UINT", 8),
                              ("TEXCOORD1.xy", "R32G32_FLOAT", 8))
            and fields[2] == ("COLOR", "R8G8B8A8_SNORM", 16))


def _has_color_data(mesh):
    import numpy as np

    layer = mesh.color_attributes.get("COLOR")
    if layer is None or layer.domain != "CORNER" or not len(layer.data):
        return False
    values = np.empty(len(layer.data) * 4, dtype=np.float32)
    layer.data.foreach_get("color", values)
    rg = values.reshape((-1, 4))[:, :2]
    # Test what survives SNORM8 export, including negative generated normals.
    return bool(np.any(np.isfinite(rg) & (np.abs(rg) >= 0.5 / 127.0)))


def _requests_color_activation(mesh):
    # Native COLOR can contain nonzero RG even when the game disables its use.
    # Data presence alone must never override the original material selector.
    return (bool(getattr(mesh, "smooth_normal_color_enabled", False))
            and _has_color_data(mesh))


def collect_ranges(maker):
    """Cover a component when an opted-in exported part has usable normal RG."""
    result = {}
    for component_id, component in enumerate(maker.merged_object.components):
        if maker.extracted_object.components[component_id].cpu_posed:
            continue
        base = f"Component{component_id}_VB1"
        buffers = [value for name, value in maker.buffers.items()
                   if name == base or name.startswith(base + "_LOD")]
        if not any(map(_compatible_layout, buffers)):
            continue
        if any(getattr(temp, "smooth_normal_color", False) and temp.index_count > 0
               for temp in component.objects):
            ranges = sorted(
                (temp.index_offset, temp.index_offset + temp.index_count)
                for temp in component.objects if temp.index_count > 0)
            merged = []
            for lo, hi in ranges:
                if merged and lo <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(hi, merged[-1][1]))
                else:
                    merged.append((lo, hi))
            result[component_id] = tuple(merged)
    return result


def transform_ini(text, ranges, color_buffers=None, *, preserved_calls=()):
    """Save once per component command list; switch only at actual draw boundaries."""
    if not ranges or MARKER in text:
        return text, 0
    if color_buffers is None:
        color_buffers = {f"Resource_Component{index}_VB1" for index in ranges}
    color_buffers = {name.lower() for name in color_buffers}
    owners, stack, gates, bound = {None}, [], {"saved"}, {False}
    active, ordinal, section = False, 0, -1
    replacements, bindings, touched, ends, invalidations = {}, {}, set(), {}, {}
    uniform, fully_bound, gate_starts, opaque_calls, control_starts = {}, {}, {}, {}, []
    lines = text.splitlines()
    safe_calls = _state_preserving_calls(lines, preserved_calls)
    binding_calls = _state_preserving_calls(lines, preserved_calls, allow_draws=True)
    for index, line in enumerate(lines):
        header = _HEADER.match(line)
        if header:
            if section >= 0:
                ends[section] = index
            active = bool(re.fullmatch(r"CommandList_Draw_Component\d+", header[1], re.I))
            owners, stack, gates, bound = {None}, [], {"saved"}, {False}
            section = index
            uniform[section], fully_bound[section] = True, True
            opaque_calls[section], control_starts = [], []
        if not active:
            continue
        command = line.strip().split(";", 1)[0].strip().lower()
        if command.startswith("if "):
            stack.append((set(owners), set(), set(gates), set(), set(bound), set(), False))
            control_starts.append(index)
        elif command.startswith("else if ") or command.startswith("elif ") or command == "else":
            if stack:
                entry, branches, entry_gates, branch_gates, entry_bound, branch_bound, has_else = stack[-1]
                branches.update(owners)
                branch_gates.update(gates)
                branch_bound.update(bound)
                stack[-1] = (entry, branches, entry_gates, branch_gates, entry_bound, branch_bound,
                             has_else or command == "else")
                owners = set(entry)
                gates = set(entry_gates)
                bound = set(entry_bound)
        elif command == "endif" and stack:
            control_starts.pop()
            entry, branches, entry_gates, branch_gates, entry_bound, branch_bound, has_else = stack.pop()
            owners |= branches
            gates |= branch_gates
            bound |= branch_bound
            if not has_else:
                owners |= entry
                gates |= entry_gates
                bound |= entry_bound
        owner = _OWNER.match(line)
        if owner:
            owners = {int(owner[1])}
        elif re.match(r"^ib\s*=", command):
            owners = {None}
        if re.match(r"^vb1\s*=", command):
            gate_starts.setdefault(section, control_starts[0] if control_starts else index)
            binding = _COLOR_BINDING.match(line)
            value = PARAM_TOKEN if binding and binding[1].lower() in color_buffers else 0
            indent = line[:len(line) - len(line.lstrip())]
            bindings[index] = (section, f"{indent}$smooth_normal_layout = {value}")
            bound = {value == PARAM_TOKEN}
            # A previous assignment copied the old layout value, not the variable.
            if "layout" in gates:
                gates = (gates - {"layout"}) | {"stale"}
        draw = _DRAW.match(line)
        eligible = False
        if draw and len(owners) == 1:
            component_id = next(iter(owners))
            args = [arg.strip() for arg in draw[3].split(",")]
            instanced = draw[2].lower() == "drawindexedinstanced"
            count_index, offset_index, base_index = 0, (2 if instanced else 1), (3 if instanced else 2)
            if (len(args) == (5 if instanced else 3)
                    and args[count_index].isdigit() and args[offset_index].isdigit()
                    and args[base_index] == "0"):
                count, start = int(args[count_index]), int(args[offset_index])
                eligible = True in bound and count > 0 and any(
                    lo <= start and start + count <= hi for lo, hi in ranges.get(component_id, ()))
                if eligible:
                    touched.add(section)
                    ordinal += 1
        any_draw = bool(re.match(r"draw\w*\s*=", command))
        call = re.match(r"run\s*=\s*(.+)", command)
        opaque_call = call and call[1] not in safe_calls
        if any_draw or opaque_call:
            desired = "layout" if eligible else "saved"
            if gates != {desired}:
                value = "$smooth_normal_layout" if eligible else "$smooth_normal_saved"
                indent = line[:len(line) - len(line.lstrip())]
                replacements[index] = (section, f"{indent}x{PARAM_INDEX} = {value}")
            gates = {desired}
            if any_draw:
                uniform[section] &= eligible
                fully_bound[section] &= bound == {True}
            if opaque_call:
                opaque_calls[section].append(index)
                if call[1] not in binding_calls:
                    invalidations[index] = section
                    # Unknown helpers may rebind geometry. Require fresh evidence.
                    owners, bound, gates = {None}, {False}, {"unknown"}
        elif command == "return":
            # Early exits must restore the caller just like the normal section end.
            replacements[index] = (section, f"{line[:len(line)-len(line.lstrip())]}x{PARAM_INDEX} = $smooth_normal_saved")
            gates = {"saved"}
            uniform[section] = False
    ends[section] = len(lines)
    if not touched:
        return text, 0
    for start in touched:
        uniform[start] &= all(index < gate_starts.get(start, start) for index in opaque_calls[start])
    hoisted = {}
    for start in touched:
        values = {assignment.rsplit(" = ", 1)[1] for owner, assignment in bindings.values()
                  if owner == start}
        if uniform[start] and fully_bound[start] and len(values) == 1:
            hoisted[start] = (gate_starts[start], next(iter(values)))
    shared_assignments = {index: value for index, value in hoisted.values()}
    initializers = {gate_starts[start] for start in touched if uniform[start] and not fully_bound[start]}
    closing = {ends[start] for start in touched}
    output = []
    for index, line in enumerate(lines):
        if index in closing:
            output.extend((f"x{PARAM_INDEX} = $smooth_normal_saved", ""))
        if index in initializers:
            output.append(f"x{PARAM_INDEX} = 0")
        if index in shared_assignments:
            output.append(f"x{PARAM_INDEX} = {shared_assignments[index]}")
        if (index in replacements and replacements[index][0] in touched
                and not uniform[replacements[index][0]]):
            output.append(replacements[index][1])
        output.append(line)
        if index in invalidations and invalidations[index] in touched and not uniform[invalidations[index]]:
            indent = line[:len(line) - len(line.lstrip())]
            output.append(f"{indent}$smooth_normal_layout = 0")
        if index in touched:
            output.append(f"local $smooth_normal_saved = x{PARAM_INDEX}")
            if not uniform[index]:
                output.append("local $smooth_normal_layout = 0")
        if index in bindings and bindings[index][0] in touched and bindings[index][0] not in hoisted:
            assignment = bindings[index][1]
            if uniform[bindings[index][0]]:
                assignment = assignment.replace("$smooth_normal_layout", f"x{PARAM_INDEX}")
            output.append(assignment)
    if len(lines) in closing:
        output.append(f"x{PARAM_INDEX} = $smooth_normal_saved")
    # The outer exporter restores its standard checksum after all postprocessors.
    result = _CHECKSUM.sub("", "\n".join(output)).rstrip() + "\n" + SHADER_BLOCK
    return result, ordinal


def _state_preserving_calls(lines, preserved_calls=(), *, allow_draws=False):
    """Prove local helper effects from their bodies, not names or shader paths.

    Capture shaders may draw without rebinding IB/VB1. Their draws still need
    the caller's gate restored, but do not erase the surrounding buffer proof.
    """
    sections, current = {}, None
    for line in lines:
        header = _HEADER.match(line)
        if header:
            current = header[1].lower()
            sections[current] = []
        elif current is not None:
            sections[current].append(line.strip().split(";", 1)[0].strip().lower())
    safe, pending = {name.casefold() for name in preserved_calls}, dict(sections)
    while pending:
        added = set()
        for name, commands in pending.items():
            effects = rf"(?:ib|vb1|x{PARAM_INDEX})" if allow_draws else rf"(?:draw\w*|ib|vb1|x{PARAM_INDEX})"
            if any(re.match(rf"(?:post\s+)?{effects}\s*=", line) for line in commands):
                continue
            calls = [match[1] for line in commands if (match := re.match(r"(?:post\s+)?run\s*=\s*(.+)", line))]
            if all(target in safe for target in calls):
                added.add(name)
        if not added:
            break
        safe |= added
        for name in added:
            del pending[name]
    return safe


def install():
    if _PATCHES:
        return
    from ._efmi_core.blender_export.blender_export import ModExporter, ObjectMergerEFMI

    original_stats = ObjectMergerEFMI.finalize_temp_objects_stats
    original_build = ModExporter.build_mod_ini

    def finalize_temp_objects_stats(self):
        original_stats(self)
        for component in self.components:
            for temp in component.objects:
                temp.smooth_normal_color = _requests_color_activation(temp.object.data)

    def build_mod_ini(self):
        from ...core.export.ini_effects import preserved_graphics_calls
        result = original_build(self)
        # User-authored/live templates remain under their owner's control.
        if (getattr(self.cfg, "use_custom_template", False)
                or getattr(self.cfg, "custom_template_live_update", False)):
            return result
        maker = self.ini
        ranges = collect_ranges(maker)
        color_buffers = {"Resource_" + name for name, buffer in maker.buffers.items()
                         if (match := re.fullmatch(r"Component(\d+)_VB1(?:_LOD\d+)?", name))
                         and int(match[1]) in ranges and _compatible_layout(buffer)}
        text, draws = transform_ini(maker.ini_string, ranges, color_buffers,
                                    preserved_calls=preserved_graphics_calls(maker.ini_string))
        if draws:
            maker.ini_string = maker.with_checksum(text)
            print(f"[SmoothNormalColor] Component-scoped native activation covers {draws} custom draw(s)")
        return result

    build_mod_ini._smooth_normal_color_hook = True
    ObjectMergerEFMI.finalize_temp_objects_stats = finalize_temp_objects_stats
    ModExporter.build_mod_ini = build_mod_ini
    _PATCHES.extend(((ObjectMergerEFMI, "finalize_temp_objects_stats", original_stats),
                     (ModExporter, "build_mod_ini", original_build)))


def remove():
    for cls, name, original in reversed(_PATCHES):
        setattr(cls, name, original)
    _PATCHES.clear()
