"""Enable the existing outline normal selector for exported COLOR data.

The shared draw-scoped ABI uses IniParams[197].x, not a material CB mutation.
The native RG decoder, width calculation and already-enabled selector are kept.
"""
from __future__ import annotations

import re


PARAM_INDEX = 197
PARAM_TOKEN = 314159
MARKER = "; Native smooth-normal COLOR activation"
_PATCHES = []

# Match the decoder and its outline projection, not character/shader hashes.
# Register allocation and the dynamic skinning palette size may vary.
PATTERN = (
    r"(?ms)\A"
    r"(?=.*^dcl_constantbuffer CB2\[4\d{3}\], dynamicIndexed\n)"
    r"(?=.*^dcl_constantbuffer CB3\[16\], immediateIndexed\n)"
    r"(?=.*^dcl_input v4\.xy\n)(?=.*^dcl_output o7\.x\n)"
    r"(?P<prefix>.*?^lt (?P<flag>r\d+)\.(?P<lane>[xyzw]), "
    r"l\(0\.500000\), cb3\[15\]\.x\n)"
    r"(?=dp2 (?P<length>r\d+\.[xyzw]), v4\.xyxx, v4\.xyxx\n"
    r"min (?P=length), (?P=length), l\(1\.000000\)\n"
    r"add (?P=length), -(?P=length), l\(1\.000000\)\n"
    r"sqrt (?P=length), (?P=length)\n"
    r"mul (?P<normal>r\d+)\.xyz, [^\n]+\n"
    r"mad (?P=normal)\.xyz, [^\n]+\n"
    r"mul (?P=normal)\.xyz, [^\n]+\n"
    r"mul (?P=normal)\.xyz, (?P=normal)\.xyzx, v4\.yyyy\n"
    r"mad (?P=normal)\.xyz, v4\.xxxx, [^\n]+, (?P=normal)\.xyzx\n"
    r"mad (?P=normal)\.xyz, [^\n]+, (?P=normal)\.xyzx\n"
    r"movc r\d+\.xyz, (?P=flag)\.(?P=lane){4}, (?P=normal)\.xyzx, [^\n]+\n)"
    r"(?=(?:[^\n]*\n){1,64}mul r\d+\.[xyzw], r\d+\.[xyzw], cb3\[14\]\.x\n)"
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
    if layout is None or layout.stride != 12:
        return False
    fields = [(element.get_name(), element.format.value, element.offset)
              for element in layout.semantics]
    return fields == [("TEXCOORD.xy", "R32G32_FLOAT", 0),
                      ("COLOR", "R8G8B8A8_SNORM", 8)]


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


def collect_ranges(maker):
    """Use temporary-object evidence and final buffer layouts, never source edits."""
    result = {}
    for component_id, component in enumerate(maker.merged_object.components):
        if maker.extracted_object.components[component_id].cpu_posed:
            continue
        base = f"Component{component_id}_VB1"
        buffers = [value for name, value in maker.buffers.items()
                   if name == base or name.startswith(base + "_LOD")]
        if not any(map(_compatible_layout, buffers)):
            continue
        ranges = tuple((temp.index_offset, temp.index_offset + temp.index_count)
                       for temp in component.objects
                       if getattr(temp, "smooth_normal_color", False) and temp.index_count > 0)
        if ranges:
            result[component_id] = ranges
    return result


def transform_ini(text, ranges, color_buffers=None):
    """Bracket final draws, including material segments and borrowed provider IBs."""
    if not ranges or MARKER in text:
        return text, 0
    if color_buffers is None:
        color_buffers = {f"Resource_Component{index}_VB1" for index in ranges}
    color_buffers = {name.lower() for name in color_buffers}
    owners, stack = {None}, []
    active, ordinal, section = False, 0, -1
    replacements, bindings, touched = {}, {}, set()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        header = _HEADER.match(line)
        if header:
            active = bool(re.fullmatch(r"CommandList_Draw_Component\d+", header[1], re.I))
            owners, stack, ordinal = {None}, [], 0
            section = index
        if not active:
            continue
        command = line.strip().split(";", 1)[0].strip().lower()
        if command.startswith("if "):
            stack.append((set(owners), set(), False))
        elif command.startswith("else if ") or command.startswith("elif ") or command == "else":
            if stack:
                entry, branches, has_else = stack[-1]
                branches.update(owners)
                stack[-1] = (entry, branches, has_else or command == "else")
                owners = set(entry)
        elif command == "endif" and stack:
            entry, branches, has_else = stack.pop()
            owners |= branches
            if not has_else:
                owners |= entry
        owner = _OWNER.match(line)
        if owner:
            owners = {int(owner[1])}
        elif re.match(r"^ib\s*=", command):
            owners = {None}
        if re.match(r"^vb1\s*=", command):
            binding = _COLOR_BINDING.match(line)
            value = PARAM_TOKEN if binding and binding[1].lower() in color_buffers else 0
            indent = line[:len(line) - len(line.lstrip())]
            bindings[index] = (section, f"{indent}$smooth_normal_layout = {value}")
        draw = _DRAW.match(line)
        if draw and len(owners) == 1:
            component_id = next(iter(owners))
            args = [arg.strip() for arg in draw[3].split(",")]
            instanced = draw[2].lower() == "drawindexedinstanced"
            count_index, offset_index, base_index = 0, (2 if instanced else 1), (3 if instanced else 2)
            if (len(args) == (5 if instanced else 3)
                    and args[count_index].isdigit() and args[offset_index].isdigit()
                    and args[base_index] == "0"):
                count, start = int(args[count_index]), int(args[offset_index])
                if count > 0 and any(lo <= start and start + count <= hi
                                     for lo, hi in ranges.get(component_id, ())):
                    indent = draw[1]
                    saved = f"$smooth_normal_saved_{ordinal}"
                    replacements[index] = (f"{indent}local {saved} = x{PARAM_INDEX}",
                                           f"{indent}x{PARAM_INDEX} = $smooth_normal_layout", line,
                                           f"{indent}x{PARAM_INDEX} = {saved}")
                    touched.add(section)
                    ordinal += 1
    if not replacements:
        return text, 0
    output = []
    for index, line in enumerate(lines):
        output.extend(replacements.get(index, (line,)))
        if index in touched:
            output.append("local $smooth_normal_layout = 0")
        if index in bindings and bindings[index][0] in touched:
            output.append(bindings[index][1])
    # The outer exporter restores its standard checksum after all postprocessors.
    result = _CHECKSUM.sub("", "\n".join(output)).rstrip() + "\n" + SHADER_BLOCK
    return result, len(replacements)


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
                temp.smooth_normal_color = _has_color_data(temp.object.data)

    def build_mod_ini(self):
        result = original_build(self)
        # User-authored/live templates remain under their owner's control.
        if (getattr(self.cfg, "use_custom_template", False)
                or getattr(self.cfg, "custom_template_live_update", False)):
            return result
        maker = self.ini
        color_buffers = {"Resource_" + name for name, buffer in maker.buffers.items()
                         if _compatible_layout(buffer)}
        text, draws = transform_ini(maker.ini_string, collect_ranges(maker), color_buffers)
        if draws:
            maker.ini_string = maker.with_checksum(text)
            print(f"[SmoothNormalColor] Native selector activation added to {draws} draw(s)")
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
