# mod.ini text surgery for the LOD export hook. Pure functions, no bpy.
#
# Three anchored edits on the stock-generated mod.ini:
#   1. global $velo_lod* declarations right after the [Constants] header
#      (3dmigoto only accepts `global` inside [Constants])
#   2. run = CommandListVeloLod{L}FrameReset right after the
#      `run = CommandListUpdateMergedSkeleton` line inside [Present]
#   3. the velo LOD override block appended before the checksum stamp,
#      then the `; SHA256 CHECKSUM:` line re-stamped (same math as
#      IniMaker.with_checksum, re-implemented here because importing
#      IniMaker would transitively pull bpy into unit tests)
#
# The generated sections mirror the stock merged.ini.j2 structures 1:1
# (SkeletonMerger / SkeletonRemapper conventions, component override shape),
# only renamed with the VeloLod prefix and driven by the LOD object's hashes
# and index/VG ranges.

import hashlib


CHECKSUM_PREFIX = '; SHA256 CHECKSUM: '
CONSTANTS_ANCHOR = '[Constants]'
PRESENT_ANCHOR = 'run = CommandListUpdateMergedSkeleton'


class IniAnchorError(Exception):
    pass


def strip_checksum(ini_text: str):
    lines = ini_text.splitlines(keepends=True)
    if not lines or not lines[-1].strip().startswith(CHECKSUM_PREFIX):
        raise IniAnchorError('mod.ini is missing the SHA256 checksum stamp')
    return ''.join(lines[:-1])


def stamp_checksum(ini_text: str) -> str:
    # Same math as IniMaker.with_checksum.
    body = ini_text.strip() + '\n'
    sha256 = hashlib.sha256(body.encode('utf-8')).hexdigest()
    return body + CHECKSUM_PREFIX + sha256 + '\n'


def insert_after_line(ini_text: str, anchor: str, insert_lines: list) -> str:
    """Inserts lines right after the first line whose stripped content equals anchor.

    Inserted lines inherit the anchor line's leading whitespace.
    """
    lines = ini_text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.strip() == anchor:
            indent = line[:len(line) - len(line.lstrip())]
            insertion = [indent + text + '\n' for text in insert_lines]
            return ''.join(lines[:i + 1] + insertion + lines[i + 1:])
    raise IniAnchorError(f'mod.ini is missing the anchor line `{anchor}`')


def _constants_lines(plan) -> list:
    level = plan['level']
    lines = [f'global $velo_lod{level}_merge_status = 0']
    for component in plan['components']:
        lines.append(f"global $velo_lod{level}_ms_{component['index']} = 0")
    return lines


def _frame_reset(plan) -> list:
    level = plan['level']
    out = [f'[CommandListVeloLod{level}FrameReset]']
    for component in plan['components']:
        out.append(f"$velo_lod{level}_ms_{component['index']} = 0")
    out.append(f'ResourceVeloLod{level}MergedSkeleton = copy ResourceVeloLod{level}MergedSkeletonRW')
    out.append(f'ResourceVeloLod{level}ExtraMergedSkeleton = copy ResourceVeloLod{level}ExtraMergedSkeletonRW')
    if plan['has_compaction']:
        out.append(f'run = CommandListVeloLod{level}RemapSkeleton')
    return out


def _merge_skeleton(plan, skeleton_scale) -> list:
    level = plan['level']
    return [
        f'[CommandListVeloLod{level}MergeSkeleton]',
        f'if $velo_lod{level}_merge_status == 0',
        '    if vs-cb4 == 3381.7777',
        '        cs-cb8 = ref vs-cb4',
        f'        cs-u6 = ResourceVeloLod{level}MergedSkeletonRW',
        f'        $\\WWMIv1\\custom_mesh_scale = {skeleton_scale:.2f}',
        '        run = CustomShader\\WWMIv1\\SkeletonMerger',
        f'        $velo_lod{level}_merge_status = 1',
        '    endif',
        'endif',
        f'if $velo_lod{level}_merge_status == 1',
        '    if vs-cb4 == 3381.7777 && vs-cb3 == 3381.7777',
        '        cs-cb8 = ref vs-cb3',
        f'        cs-u6 = ResourceVeloLod{level}ExtraMergedSkeletonRW',
        f'        $\\WWMIv1\\custom_mesh_scale = {skeleton_scale:.2f}',
        '        run = CustomShader\\WWMIv1\\SkeletonMerger',
        f'        $velo_lod{level}_merge_status = 2',
        '    endif',
        'endif',
    ]


def _remap_skeleton(plan) -> list:
    level = plan['level']
    out = [
        f'[CommandListVeloLod{level}RemapSkeleton]',
        f'ResourceVeloLod{level}RemappedSkeletonRW = copy ResourceVeloLod{level}MergedSkeletonRW',
        f'ResourceVeloLod{level}ExtraRemappedSkeletonRW = copy ResourceVeloLod{level}ExtraMergedSkeletonRW',
        f'ResourceVeloLod{level}MergedSkeletonRemap = copy ResourceVeloLod{level}MergedSkeletonRW',
        f'ResourceVeloLod{level}ExtraMergedSkeletonRemap = copy ResourceVeloLod{level}ExtraMergedSkeletonRW',
        f'cs-t37 = ResourceVeloLod{level}BlendRemapForwardBuffer',
    ]
    for component in plan['components']:
        compaction = component.get('compaction')
        if not compaction:
            continue
        c = component['index']
        out.extend([
            f"$\\WWMIv1\\blend_remap_id = {compaction['remap_id']}",
            f"$\\WWMIv1\\vg_count = {compaction['vg_count']}",
            f'cs-t38 = ResourceVeloLod{level}MergedSkeletonRemap',
            f'cs-u5 = ResourceVeloLod{level}RemappedSkeletonRW',
            'run = CustomShader\\WWMIv1\\SkeletonRemapper',
            f'ResourceVeloLod{level}RemappedSkeletonComponent{c} = copy ResourceVeloLod{level}RemappedSkeletonRW',
            f'cs-t38 = ResourceVeloLod{level}ExtraMergedSkeletonRemap',
            f'cs-u5 = ResourceVeloLod{level}ExtraRemappedSkeletonRW',
            'run = CustomShader\\WWMIv1\\SkeletonRemapper',
            f'ResourceVeloLod{level}ExtraRemappedSkeletonComponent{c} = copy ResourceVeloLod{level}ExtraRemappedSkeletonRW',
        ])
    return out


def _override_command_list(plan, component, shared) -> list:
    level = plan['level']
    compaction = component.get('compaction')
    if compaction:
        c = component['index']
        name = f'[CommandListVeloLod{level}OverrideC{c}]'
        blend = f'vb4 = ResourceVeloLod{level}BlendC{c}Buffer'
        skeleton = f'ResourceVeloLod{level}RemappedSkeletonComponent{c}'
        extra_skeleton = f'ResourceVeloLod{level}ExtraRemappedSkeletonComponent{c}'
    else:
        name = f'[CommandListVeloLod{level}OverrideShared]'
        blend = f'vb4 = ResourceVeloLod{level}BlendBuffer'
        skeleton = f'ResourceVeloLod{level}MergedSkeleton'
        extra_skeleton = f'ResourceVeloLod{level}ExtraMergedSkeleton'

    out = [
        name,
        'ResourceVeloLodBypassVB0 = ref vb0',
        'ib = ResourceIndexBuffer',
        'vb0 = ResourcePositionBuffer',
        'vb1 = ResourceVectorBuffer',
        'vb2 = ResourceTexcoordBuffer',
    ]
    if shared['color_present']:
        out.append('vb3 = ResourceColorBuffer')
    out.extend([
        blend,
        'if vs-cb4 == 3381.7777',
        f'    vs-cb4 = ref {skeleton}',
        '    if vs-cb3 == 3381.7777',
        f'        vs-cb3 = ref {extra_skeleton}',
        '    endif',
        'elif vs-cb3 == 3381.7777',
        f'    vs-cb3 = ref {skeleton}',
        'endif',
    ])
    return out


def _component_section(plan, component) -> list:
    level = plan['level']
    c = component['index']
    out = [
        f'[TextureOverrideVeloLod{level}Component{c}]',
        f"hash = {plan['vb0_hash']}",
        f"match_first_index = {component['index_offset']}",
        f"match_index_count = {component['index_count']}",
        '$object_detected = 1',
        'if $mod_enabled',
        f'    if $velo_lod{level}_ms_{c} != 2',
        f"        $\\WWMIv1\\vg_offset = {component['vg_offset']}",
        f"        $\\WWMIv1\\vg_count = {component['vg_count']}",
        f'        $velo_lod{level}_merge_status = $velo_lod{level}_ms_{c}',
        f'        run = CommandListVeloLod{level}MergeSkeleton',
        f'        $velo_lod{level}_ms_{c} = $velo_lod{level}_merge_status',
        '    endif',
        f'    if ResourceVeloLod{level}MergedSkeleton !== null',
        '        handling = skip',
    ]
    if component['draws']:
        suffix = f"C{c}" if component.get('compaction') else 'Shared'
        out.extend([
            '        run = CommandListTriggerResourceOverrides',
            f'        run = CommandListVeloLod{level}Override{suffix}',
        ])
        for index_count, index_offset in component['draws']:
            out.append(f'        drawindexed = {index_count}, {index_offset}, 0')
        out.append('        run = CommandListVeloLodCleanup')
    out.extend([
        '    endif',
        'endif',
    ])
    return out


def _resources(plan, shared) -> list:
    level = plan['level']
    out = [
        f'[ResourceVeloLod{level}MergedSkeleton]',
        '',
        f'[ResourceVeloLod{level}ExtraMergedSkeleton]',
        '',
        f'[ResourceVeloLod{level}MergedSkeletonRW]',
        'type = RWBuffer',
        'format = R32G32B32A32_FLOAT',
        'array = 1536',
        '',
        f'[ResourceVeloLod{level}ExtraMergedSkeletonRW]',
        'type = RWBuffer',
        'format = R32G32B32A32_FLOAT',
        'array = 1536',
    ]

    if any(component['draws'] and not component.get('compaction') for component in plan['components']):
        out.extend([
            '',
            f'[ResourceVeloLod{level}BlendBuffer]',
            'type = Buffer',
            'format = R8_UINT',
            f"stride = {shared['blend_stride']}",
            f'filename = Meshes/VeloLod{level}Blend.buf',
        ])

    for component in plan['components']:
        if not component.get('compaction'):
            continue
        c = component['index']
        out.extend([
            '',
            f'[ResourceVeloLod{level}BlendC{c}Buffer]',
            'type = Buffer',
            'format = R8_UINT',
            f"stride = {shared['blend_stride']}",
            f'filename = Meshes/VeloLod{level}BlendC{c}.buf',
        ])

    if plan['has_compaction']:
        out.extend([
            '',
            f'[ResourceVeloLod{level}MergedSkeletonRemap]',
            '',
            f'[ResourceVeloLod{level}ExtraMergedSkeletonRemap]',
            '',
            f'[ResourceVeloLod{level}RemappedSkeletonRW]',
            '',
            f'[ResourceVeloLod{level}ExtraRemappedSkeletonRW]',
        ])
        for component in plan['components']:
            if not component.get('compaction'):
                continue
            c = component['index']
            out.extend([
                '',
                f'[ResourceVeloLod{level}RemappedSkeletonComponent{c}]',
                '',
                f'[ResourceVeloLod{level}ExtraRemappedSkeletonComponent{c}]',
            ])
        out.extend([
            '',
            f'[ResourceVeloLod{level}BlendRemapForwardBuffer]',
            'type = Buffer',
            'format = R16_UINT',
            f'filename = Meshes/VeloLod{level}BlendRemapForward.buf',
        ])

    if plan['cb4_hash'] and plan['cb4_hash'] != shared['full_cb4_hash']:
        out.extend([
            '',
            f'[TextureOverrideVeloLod{level}MarkBoneDataCB]',
            f"hash = {plan['cb4_hash']}",
            'match_priority = 0',
            'filter_index = 3381.7777',
        ])

    return out


def build_lod_block(plan, shared, include_shared_sections: bool) -> str:
    level = plan['level']
    chunks = []

    chunks.append([f"; >>> Velo Tools LOD{level} override: lod object {plan['lod_object_name']} >>>"])

    if include_shared_sections:
        chunks.append(['[ResourceVeloLodBypassVB0]'])
        chunks.append([
            '[CommandListVeloLodCleanup]',
            'vb0 = ref ResourceVeloLodBypassVB0',
        ])

    chunks.append(_frame_reset(plan))
    chunks.append(_merge_skeleton(plan, shared['skeleton_scale']))
    if plan['has_compaction']:
        chunks.append(_remap_skeleton(plan))

    emitted_shared_override = False
    for component in plan['components']:
        if not component['draws']:
            continue
        if component.get('compaction'):
            chunks.append(_override_command_list(plan, component, shared))
        elif not emitted_shared_override:
            chunks.append(_override_command_list(plan, component, shared))
            emitted_shared_override = True

    for component in plan['components']:
        chunks.append(_component_section(plan, component))

    chunks.append(_resources(plan, shared))

    chunks.append([f'; <<< Velo Tools LOD{level} override <<<'])

    return '\n\n'.join('\n'.join(chunk) for chunk in chunks) + '\n'


def append_lod_overrides(ini_text: str, lod_plans: list, shared: dict) -> str:
    """Applies all three anchored edits and returns the re-stamped ini text.

    Raises IniAnchorError when the stock anchors are missing (custom template).
    """
    body = strip_checksum(ini_text)

    constants = []
    present_runs = []
    for plan in lod_plans:
        constants.extend(_constants_lines(plan))
        present_runs.append(f"run = CommandListVeloLod{plan['level']}FrameReset")

    body = insert_after_line(body, CONSTANTS_ANCHOR, constants)
    body = insert_after_line(body, PRESENT_ANCHOR, present_runs)

    blocks = []
    for i, plan in enumerate(lod_plans):
        blocks.append(build_lod_block(plan, shared, include_shared_sections=(i == 0)))

    body = body.rstrip() + '\n\n' + '\n'.join(blocks)

    return stamp_checksum(body)
