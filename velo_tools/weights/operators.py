from __future__ import annotations

from velo_tools.i18n import iface_

import contextlib
import threading
import textwrap

import bpy
from bpy.props import EnumProperty, IntProperty, StringProperty

from . import algorithms as _algo
from . import native_dependencies as _native_deps
from . import props as _props
from . import runtime as _runtime


def _invalidate_weight_overlay_caches(context=None):
    try:
        from .. import overlay as _overlay
        if hasattr(_overlay, "invalidate_cache"):
            _overlay.invalidate_cache()
        if hasattr(_overlay, "invalidate_mmd_cache"):
            _overlay.invalidate_mmd_cache()
    except Exception:
        pass
    try:
        from ..general_mapping import overlay as _general_overlay
        if hasattr(_general_overlay, "invalidate_cache"):
            _general_overlay.invalidate_cache()
    except Exception:
        pass
    try:
        screen = getattr(context or bpy.context, "screen", None)
        if screen is not None:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
    except Exception:
        pass


_native_install_job = None


def native_install_status():
    if _native_deps.is_installed():
        return "installed", ""
    if _native_install_job is not None:
        if _native_install_job["thread"].is_alive():
            return "running", ""
        if _native_install_job["error"]:
            return "error", _native_install_job["error"]
    return "missing", ""


def _poll_native_install():
    if _native_install_job is not None and _native_install_job["thread"].is_alive():
        return 0.25
    _invalidate_weight_overlay_caches()
    return None


class VELO_OT_weight_install_native_dependencies(bpy.types.Operator):
    bl_idname = "velo.weight_install_native_dependencies"
    bl_label = 'Install Robust dependencies'
    bl_description = 'Download and verify optional native dependencies required by Robust Weight Tools'
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        size_mib = _native_deps.download_size_bytes() / (1024 * 1024)
        installed_mib = _native_deps.installed_size_bytes() / (1024 * 1024)
        col = self.layout.column(align=True)
        col.label(text=iface_('Will download about {0:.1f} MiB of scipy, libigl, and robust-laplacian.').format(size_mib))
        col.label(text=iface_('After installation, the shared cache occupies about {0:.1f} MiB.').format(installed_mib))
        col.label(text='Shared cache does not enter the Velo Tools plugin directory or update backup.')

    def execute(self, context):
        global _native_install_job
        status, _error = native_install_status()
        if status == "installed":
            self.report({'INFO'}, iface_('Robust dependencies installed'))
            return {'CANCELLED'}
        if status == "running":
            self.report({'INFO'}, iface_('Robust dependencies are downloading in the background'))
            return {'CANCELLED'}

        job = {"thread": None, "error": ""}

        def worker():
            try:
                _native_deps.install()
            except Exception as exc:
                job["error"] = str(exc)

        job["thread"] = threading.Thread(
            target=worker,
            name="velo-native-dependencies",
            daemon=True,
        )
        _native_install_job = job
        job["thread"].start()
        bpy.app.timers.register(_poll_native_install, first_interval=0.25)
        self.report({'INFO'}, iface_('Robust dependencies have started downloading in the background'))
        return {'FINISHED'}


def _last_report_text(context):
    settings = getattr(getattr(context, "scene", None), "velo_weight_tools", None)
    return (getattr(settings, "last_report", "") or "").strip()


def _report_summary(text, limit=88):
    cleaned = (text or "").replace("\n", " ").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _report_wrapped_lines(text, width=78):
    cleaned = (text or "").replace("\r", "").strip()
    if not cleaned:
        return ["暂无结果"]
    lines = []
    for chunk in cleaned.split("\n"):
        wrapped = textwrap.wrap(chunk, width=width, break_long_words=False, break_on_hyphens=False)
        if wrapped:
            lines.extend(wrapped)
        else:
            lines.append("")
    return lines


def _format_weight_evidence(value):
    value = float(value or 0.0)
    if value <= 0.0:
        return "0"
    if value < 0.001:
        return f"{value:.2g}"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _first_existing_group(obj, names):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return None
    for name in names:
        cleaned = (name or "").strip()
        if not cleaned:
            continue
        group = obj.vertex_groups.get(cleaned)
        if group is not None:
            return group
    return None


def _bone_depth(armature, names):
    if armature is None or getattr(armature, "type", None) != 'ARMATURE':
        return None
    for name in names:
        bone = armature.data.bones.get((name or "").strip())
        if bone is None:
            continue
        depth = 0
        parent = bone.parent
        while parent is not None:
            depth += 1
            parent = parent.parent
        return depth
    return None


def _group_weight_total(obj, group):
    try:
        return float(_algo.read_group_weights(obj, group).sum())
    except Exception:
        return 0.0


def _remove_group_if_empty(obj, group):
    if obj is None or getattr(obj, "type", None) != 'MESH' or group is None:
        return False
    current = obj.vertex_groups.get(getattr(group, "name", ""))
    if current is None:
        return False
    if _algo.count_group_weights(obj, current) > 0:
        return False
    obj.vertex_groups.remove(current)
    return True


def _snapshot_group(snapshots, obj, group):
    if obj is None or group is None:
        return
    key = group.name
    if key in snapshots:
        return
    snapshots[key] = _algo.snapshot_group_memberships(obj, group)


def _snapshot_group_locks(obj):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return {}
    return {group.name: bool(group.lock_weight) for group in obj.vertex_groups}


def _restore_group_locks(obj, snapshots):
    if obj is None or not snapshots:
        return
    for group_name, locked in snapshots.items():
        group = obj.vertex_groups.get(group_name)
        if group is not None:
            group.lock_weight = bool(locked)


def _restore_group_snapshots(obj, snapshots):
    if obj is None:
        return
    for group_name, memberships in snapshots.items():
        group = obj.vertex_groups.get(group_name)
        if group is None:
            continue
        try:
            _algo.restore_group_memberships(obj, group, memberships)
        except Exception:
            pass


def _remove_created_groups(obj, group_names):
    if obj is None:
        return
    for group_name in reversed(list(group_names)):
        group = obj.vertex_groups.get(group_name)
        if group is None:
            continue
        try:
            obj.vertex_groups.remove(group)
        except Exception:
            pass


def _selected_edit_vertex_indices(obj):
    import bmesh
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    selected = {vert.index for vert in bm.verts if vert.select}
    for edge in bm.edges:
        if edge.select:
            selected.update(vert.index for vert in edge.verts)
    for face in bm.faces:
        if face.select:
            selected.update(vert.index for vert in face.verts)
    return sorted(selected)


def _set_object_vertex_selection(obj, vertex_indices):
    selected = {int(index) for index in vertex_indices or ()}
    for polygon in getattr(obj.data, "polygons", ()):
        polygon.select = False
    for edge in getattr(obj.data, "edges", ()):
        edge.select = False
    for vertex in obj.data.vertices:
        vertex.select = vertex.index in selected
    update = getattr(obj.data, "update", None)
    if callable(update):
        update()


def _set_edit_vertex_selection(obj, vertex_indices):
    import bmesh
    selected = {int(index) for index in vertex_indices or ()}
    bm = bmesh.from_edit_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    for face in bm.faces:
        face.select_set(False)
    for edge in bm.edges:
        edge.select_set(False)
    for vert in bm.verts:
        vert.select_set(vert.index in selected)
    bmesh.update_edit_mesh(obj.data)


def _snapshot_editable_groups(snapshots, obj):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        return
    for group in obj.vertex_groups:
        if group.lock_weight or _algo.is_special_vg_name(group.name):
            continue
        _snapshot_group(snapshots, obj, group)


def _ensure_editable_group(obj, group_name):
    cleaned = (group_name or "").strip()
    if not cleaned:
        raise ValueError("顶点组名为空")
    group = obj.vertex_groups.get(cleaned)
    created = False
    if group is None:
        group = obj.vertex_groups.new(name=cleaned)
        created = True
    if group is None:
        raise RuntimeError(f"无法在 '{obj.name}' 上创建顶点组 '{cleaned}'")
    if group.lock_weight:
        raise ValueError(f"顶点组 '{obj.name}/{group.name}' 已锁定，不能写入")
    return group, created


def _validate_mirror_donor_count(donors, mirror_donors):
    expected = len(donors or ())
    actual = len(mirror_donors or ())
    if actual != expected:
        raise ValueError(f"镜像规格化需要匹配实际 {expected} 个镜像供体，但只找到 {actual} 个")


def _select_donors_for_group(
    context,
    settings,
    target,
    group,
    source_name,
    *,
    configured_names=None,
    focus_weights=None,
    exclude_names=None,
    include_locked_candidates=False,
    rank_all=False,
):
    donor_count = _props.donor_count_value(settings)
    configured_names = list(configured_names or [])
    if configured_names:
        return _algo.select_auto_donors(
            target,
            group,
            donor_count,
            exclude_group_names=exclude_names,
            preferred_names=configured_names,
            strict_preferred=True,
            include_locked_candidates=include_locked_candidates,
        )
    preferred_donors = _algo.semantic_auto_donor_names(
        context,
        settings,
        source_name,
        group,
        count=donor_count,
    )
    semantic_groups = [target.vertex_groups.get(name) for name in preferred_donors]
    semantic_groups = [candidate for candidate in semantic_groups if candidate is not None]
    preferred_side = _algo.infer_donor_side(
        target,
        group,
        focus_weights=focus_weights,
        candidate_groups=semantic_groups,
    )
    return _algo.select_auto_donors(
        target,
        group,
        donor_count,
        exclude_group_names=exclude_names,
        preferred_names=preferred_donors,
        strict_preferred=False,
        focus_weights=focus_weights,
        preferred_side=preferred_side,
        include_locked_candidates=include_locked_candidates,
        rank_all=rank_all,
    )


def _select_activity_donors(context, settings, obj, group, *, exclude_names=None):
    donor_count = _props.donor_count_value(settings)
    focus_weights = _algo.read_group_weights(obj, group)
    preferred_side = _algo.infer_donor_side(obj, group, focus_weights=focus_weights)
    return _algo.select_auto_donors(
        obj,
        group,
        donor_count,
        exclude_group_names=exclude_names,
        preferred_names=[],
        strict_preferred=False,
        focus_weights=focus_weights,
        preferred_side=preferred_side,
    )


def _row_source_names(row, mode):
    if mode == 'MMD':
        return [
            getattr(row, "current_source_name", ""),
            getattr(row, "mmd_name", ""),
            getattr(row, "unified_name", ""),
        ]
    return [
        getattr(row, "current_source_name", ""),
        getattr(row, "source_name", ""),
        getattr(row, "target_name", ""),
    ]


def _prune_disconnected_zero_rows(profile, obj, *, mode):
    if profile is None or obj is None or getattr(obj, "type", None) != 'MESH':
        return 0
    target_attr = "unified_name" if mode == 'MMD' else "target_name"
    removed = 0
    for idx in range(len(profile.rows) - 1, -1, -1):
        row = profile.rows[idx]
        if (getattr(row, target_attr, "") or "").strip():
            continue
        group = _first_existing_group(obj, _row_source_names(row, mode))
        if group is not None and _algo.count_group_weights(obj, group) > 0:
            continue
        profile.rows.remove(idx)
        removed += 1
    profile.active_row_index = max(0, min(profile.active_row_index, len(profile.rows) - 1)) if profile.rows else 0
    return removed


def _mapping_merge_groups(obj, armature, grouped_rows, *, mode):
    if obj is None or getattr(obj, "type", None) != 'MESH':
        raise ValueError("映射来源网格无效")
    merged_groups = 0
    disconnected_rows = 0
    moved_vertices = 0
    removed_groups = 0
    changed_rows = []
    for target_name, entries in grouped_rows.items():
        resolved = []
        for row_index, row, source_names in entries:
            group = _first_existing_group(obj, source_names)
            if group is None:
                continue
            resolved.append({
                "row_index": row_index,
                "row": row,
                "names": source_names,
                "group": group,
                "depth": _bone_depth(armature, source_names),
                "total": _group_weight_total(obj, group),
            })
        unique_indices = {item["group"].index for item in resolved}
        if len(unique_indices) <= 1:
            continue
        resolved.sort(key=lambda item: (
            item["depth"] if item["depth"] is not None else 999999,
            item["row_index"],
        ))
        keeper = resolved[0]
        keeper_group = keeper["group"]
        if keeper_group.lock_weight:
            raise ValueError(f"保留来源组 '{obj.name}/{keeper_group.name}' 已锁定，不能写入")
        merged_weights = _algo.read_group_weights(obj, keeper_group).copy()
        redundant = []
        seen_redundant = set()
        for item in resolved[1:]:
            group = item["group"]
            if group.index == keeper_group.index or group.index in seen_redundant:
                changed_rows.append(item["row"])
                continue
            if group.lock_weight:
                raise ValueError(f"冗余来源组 '{obj.name}/{group.name}' 已锁定，不能清空")
            weights = _algo.read_group_weights(obj, group)
            moved_vertices += int((weights > 0.00001).sum())
            merged_weights += weights
            redundant.append(group)
            seen_redundant.add(group.index)
            changed_rows.append(item["row"])
        if not redundant:
            continue
        _algo.write_group_weights(obj, keeper_group, merged_weights)
        for group in redundant:
            _algo.clear_vertex_group(obj, group)
            merged_groups += 1
            if _remove_group_if_empty(obj, group):
                removed_groups += 1
    if mode == 'MMD':
        for row in changed_rows:
            if (row.unified_name or "").strip():
                row.unified_name = ""
                disconnected_rows += 1
    else:
        for row in changed_rows:
            if (row.target_name or "").strip():
                row.target_name = ""
                disconnected_rows += 1
    return merged_groups, disconnected_rows, moved_vertices, removed_groups


class VELO_OT_weight_refresh_groups(bpy.types.Operator):
    bl_idname = "velo.weight_refresh_groups"
    bl_label = 'Refresh Source Groups'
    bl_description = 'Rescan candidate vertex groups on the source mesh that can be used as weight sources.'
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = context.scene.velo_weight_tools
        _props.refresh_source_vg_names(settings)
        _props.sync_target_group_name(settings, context)
        return {'FINISHED'}


class VELO_OT_weight_show_last_report(bpy.types.Operator):
    bl_idname = "velo.weight_show_last_report"
    bl_label = 'View full results'
    bl_description = 'View the full results or error information of the most recent weight transfer'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return bool(_last_report_text(context))

    @classmethod
    def description(cls, context, properties):
        return _last_report_text(context) or cls.bl_description

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=760)

    def draw(self, context):
        layout = self.layout
        for line in _report_wrapped_lines(_last_report_text(context)):
            layout.label(text=line, icon='BLANK1')
        layout.separator()
        layout.operator("velo.weight_copy_last_report", icon='COPYDOWN')

    def execute(self, context):
        return {'FINISHED'}


class VELO_OT_weight_copy_last_report(bpy.types.Operator):
    bl_idname = "velo.weight_copy_last_report"
    bl_label = 'Copy full result'
    bl_description = 'Copy the full result of the most recent weight transfer to the clipboard for sending debugging information'
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return bool(_last_report_text(context))

    def execute(self, context):
        text = _last_report_text(context)
        context.window_manager.clipboard = text
        self.report({'INFO'}, iface_('Copied complete results to clipboard'))
        return {'FINISHED'}


class VELO_OT_weight_cycle_donor_count(bpy.types.Operator):
    bl_idname = "velo.weight_cycle_donor_count"
    bl_label = 'Toggle automatic donor count'
    bl_description = 'Switch the number of automatic donors between 1 and 6'
    bl_options = {'INTERNAL'}

    direction: EnumProperty(
        name='Direction',
        items=[
            ('PREV', 'Previous', 'Switch to a smaller number of donors'),
            ('NEXT', 'Next', 'Switch to a larger number of donors'),
        ],
        default='NEXT',
    )

    @classmethod
    def poll(cls, context):
        return getattr(getattr(context, "scene", None), "velo_weight_tools", None) is not None

    def execute(self, context):
        settings = context.scene.velo_weight_tools
        current = _props.donor_count_value(settings)
        if self.direction == 'PREV':
            current = max(1, current - 1)
        else:
            current = min(6, current + 1)
        settings.donor_count = current
        return {'FINISHED'}


class VELO_OT_weight_mirror_mapping_add(bpy.types.Operator):
    bl_idname = "velo.weight_mirror_mapping_add"
    bl_label = 'Add a mirrored mapping'
    bl_description = 'Add a pair of vertex groups to the scene-level manual mirror mapping table'
    bl_options = {'REGISTER', 'UNDO'}

    left_group: StringProperty(default="")
    right_group: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        return getattr(getattr(context, "scene", None), "velo_weight_tools", None) is not None

    def execute(self, context):
        settings = context.scene.velo_weight_tools
        left = (self.left_group or "").strip()
        right = (self.right_group or "").strip()
        try:
            if not left and not right:
                item = settings.mirror_mappings.add()
                item.left_group = ""
                item.right_group = ""
                settings.active_mirror_mapping_index = len(settings.mirror_mappings) - 1
                settings.last_report = "已新增空白镜像映射"
                self.report({'INFO'}, iface_(str(settings.last_report)))
                return {'FINISHED'}
            if not left or not right:
                raise ValueError("请同时指定左右两个镜像顶点组")
            if left == right:
                raise ValueError("镜像映射两侧不能是同一个顶点组")
            created = _algo.add_mirror_mapping(settings, left, right)
            _props.sync_mirror_group(settings, context)
            _props.sync_donor_preview(settings, context)
            settings.last_report = f"镜像映射 {'新增' if created else '已存在'}: {left} ↔ {right}"
            self.report({'INFO'}, iface_(str(settings.last_report)))
            return {'FINISHED'}
        except Exception as exc:
            settings.last_report = str(exc)
            self.report({'ERROR'}, iface_(str(str(exc))))
            return {'CANCELLED'}


class VELO_OT_weight_mirror_mapping_remove(bpy.types.Operator):
    bl_idname = "velo.weight_mirror_mapping_remove"
    bl_label = 'Remove mirror mapping'
    bl_description = 'Remove a pair of vertex groups from the scene-level manual mirror mapping table.'
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return getattr(getattr(context, "scene", None), "velo_weight_tools", None) is not None

    def execute(self, context):
        settings = context.scene.velo_weight_tools
        if self.index < 0 or self.index >= len(settings.mirror_mappings):
            self.report({'ERROR'}, iface_('Mirror Mapping Index Invalid'))
            return {'CANCELLED'}
        settings.mirror_mappings.remove(self.index)
        settings.active_mirror_mapping_index = max(0, min(settings.active_mirror_mapping_index, len(settings.mirror_mappings) - 1))
        _props.sync_mirror_group(settings, context)
        _props.sync_donor_preview(settings, context)
        settings.last_report = "已移除镜像映射"
        self.report({'INFO'}, iface_(str(settings.last_report)))
        return {'FINISHED'}


class VELO_OT_weight_mirror_active_group(bpy.types.Operator):
    bl_idname = "velo.weight_mirror_active_group"
    bl_label = 'Mirror Weight'
    bl_description = 'Use the current active vertex group as the authoritative side, mirror the weights to the other side, and perform normalization'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(getattr(context, "scene", None), "velo_weight_tools", None)
        active = getattr(context, "active_object", None)
        return settings is not None and active is not None and active.type == 'MESH'

    def execute(self, context):
        _runtime.reset_overlay_pick_runtime(context)
        settings = context.scene.velo_weight_tools
        obj = context.active_object
        snapshots = {}
        created_groups = []
        created_bones = []
        mirror_flag_stack = contextlib.ExitStack()
        try:
            _algo.validate_velo_mirror_scope(context, obj)
            mirror_flag_stack.enter_context(_algo.suppress_native_mirror_flags(obj))
            active_group = obj.vertex_groups.active
            if active_group is None:
                raise ValueError("请先在活动对象上选择一个活动顶点组")
            if active_group.lock_weight:
                raise ValueError(f"当前活动顶点组 '{obj.name}/{active_group.name}' 已锁定，不能作为权威侧")
            if _algo.is_special_vg_name(active_group.name):
                raise ValueError(f"当前活动顶点组 '{obj.name}/{active_group.name}' 是 Velo 特殊组，不能镜像")
            resolution = _algo.resolve_mirror_group(context, settings, obj, active_group.name)
            if not resolution.mirror_name:
                raise ValueError(resolution.reason or f"未找到 '{active_group.name}' 的镜像顶点组")
            mirror_group, mirror_created = _ensure_editable_group(obj, resolution.mirror_name)
            if mirror_group.index == active_group.index:
                raise ValueError("镜像顶点组不能与权威顶点组相同")
            if mirror_created:
                created_groups.append(mirror_group.name)
            else:
                _snapshot_group(snapshots, obj, mirror_group)
            _snapshot_group(snapshots, obj, active_group)

            donors = []
            mirror_donors = []
            if settings.normalize_after:
                donors = _select_activity_donors(
                    context,
                    settings,
                    obj,
                    active_group,
                    exclude_names=[mirror_group.name],
                )
                mirror_donors = _algo.mirrored_donor_groups(
                    context,
                    settings,
                    obj,
                    donors,
                    exclude_names=[active_group.name, mirror_group.name],
                )
                _validate_mirror_donor_count(donors, mirror_donors)
                for group in donors + mirror_donors:
                    _snapshot_group(snapshots, obj, group)

            mirror_stats = _algo.mirror_group_weights(obj, active_group, mirror_group)
            if settings.limit_groups_enable:
                _snapshot_editable_groups(snapshots, obj)
                _algo.apply_limit_groups(obj, settings)
            normalized = False
            if settings.normalize_after:
                normalized = _algo.normalize_authority_groups_with_donors(
                    obj,
                    [active_group, mirror_group],
                    donors + mirror_donors,
                )
            if settings.create_bone_if_missing:
                if _algo.ensure_group_bone(context, settings, obj, mirror_group.name):
                    created_bones.append(mirror_group.name)
            _algo.ensure_numeric_export_compatible(obj, active_group.name)
            _algo.ensure_numeric_export_compatible(obj, mirror_group.name)
            _algo.add_mirror_mapping(settings, active_group.name, mirror_group.name)
            _props.sync_mirror_group(settings, context)
            _props.sync_donor_preview(settings, context)
        except Exception as exc:
            _restore_group_snapshots(obj, snapshots)
            _remove_created_groups(obj, created_groups)
            for bone_name in created_bones:
                try:
                    _algo.remove_armature_bone(context, _algo.resolve_armature_object(settings), bone_name)
                except Exception:
                    pass
            settings.last_report = str(exc)
            self.report({'ERROR'}, iface_(str(str(exc))))
            _invalidate_weight_overlay_caches(context)
            return {'CANCELLED'}
        finally:
            mirror_flag_stack.close()

        bits = [
            f"镜像权重 {active_group.name} -> {mirror_group.name}",
            f"匹配 {mirror_stats.get('matched_vertices', 0)} 点",
        ]
        if mirror_stats.get("coincident_buckets", 0):
            bits.append(f"重合点组 {mirror_stats['coincident_buckets']}")
        if settings.limit_groups_enable:
            bits.append("limit")
        if normalized:
            bits.append("normalize")
        if donors:
            bits.append(f"供体 {len(donors)}/{_props.donor_count_value(settings)} " + ", ".join(group.name for group in donors))
        if mirror_donors:
            bits.append(f"镜像供体 {len(mirror_donors)}/{_props.donor_count_value(settings)} " + ", ".join(group.name for group in mirror_donors))
        settings.last_report = "；".join(bits)
        self.report({'INFO'}, iface_(str(settings.last_report)))
        _invalidate_weight_overlay_caches(context)
        return {'FINISHED'}


class VELO_OT_weight_merge_groups(bpy.types.Operator):
    bl_idname = "velo.weight_merge_groups"
    bl_label = 'Execute weight group transfer'
    bl_description = "Add the weights of the currently selected mesh's left-side source group to the right-side target group, then clear the left-side source group"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(getattr(context, "scene", None), "velo_weight_tools", None)
        active = getattr(context, "active_object", None)
        return settings is not None and active is not None and active.type == 'MESH'

    def execute(self, context):
        _runtime.reset_overlay_pick_runtime(context)
        settings = context.scene.velo_weight_tools
        obj = context.active_object
        source_name = (getattr(settings, "merge_source_group", "") or "").strip()
        target_name = (getattr(settings, "merge_target_group", "") or "").strip()
        try:
            if obj is None or obj.type != 'MESH':
                raise ValueError("请先选中一个网格物体")
            if not source_name or not target_name:
                raise ValueError("请先选择左侧来源组和右侧目标组")
            if source_name == target_name:
                raise ValueError("左侧来源组和右侧目标组不能相同")
            source_group = _algo.validate_source_group(obj, source_name)
            target_group = obj.vertex_groups.get(target_name)
            if target_group is None:
                raise ValueError(f"当前物体 '{obj.name}' 不存在目标组 '{target_name}'")
            if target_group.lock_weight:
                raise ValueError(f"目标组 '{obj.name}/{target_group.name}' 已锁定，不能写入")

            source_weights = _algo.read_group_weights(obj, source_group)
            target_weights = _algo.read_group_weights(obj, target_group)
            merged_weights = source_weights + target_weights
            moved_vertices = int((source_weights > 0.00001).sum())
            saturated_vertices = int((merged_weights > 1.0).sum())

            _algo.write_group_weights(obj, target_group, merged_weights)
            _algo.clear_vertex_group(obj, source_group)

            bits = [f"同网格转移 {source_group.name} -> {target_group.name}", f"{moved_vertices} 点"]
            if saturated_vertices > 0:
                bits.append(f"{saturated_vertices} 点写入时截到 1.0")
            settings.last_report = "；".join(bits)
            self.report({'INFO'}, iface_(str(settings.last_report)))
        except Exception as exc:
            settings.last_report = str(exc)
            self.report({'ERROR'}, iface_(str(str(exc))))
            _invalidate_weight_overlay_caches(context)
            return {'CANCELLED'}

        _invalidate_weight_overlay_caches(context)
        return {'FINISHED'}


class VELO_OT_weight_merge_mapping_families(bpy.types.Operator):
    bl_idname = "velo.weight_merge_mapping_families"
    bl_label = 'Merge weights for the same target according to the mapping'
    bl_description = 'Merge multiple source weights in the mapping table pointing to the same target into the higher-level parent source group, and disconnect redundant mapping lines'
    bl_options = {'REGISTER', 'UNDO'}

    mapping_mode: EnumProperty(
        name='Mapping Type',
        items=[
            ('MMD', "MMD", 'Use Arknights: Endfield MMD mapping table'),
            ('GENERAL', 'General', 'Use the generic vertex group mapping table'),
        ],
        default='MMD',
    )

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return scene is not None and getattr(scene, "velo_weight_tools", None) is not None

    def execute(self, context):
        _runtime.reset_overlay_pick_runtime(context)
        settings = context.scene.velo_weight_tools
        try:
            if self.mapping_mode == 'MMD':
                ef = getattr(context.scene, "velo_endfield", None)
                profile = getattr(ef, "mmd_profile", None) if ef is not None else None
                source_obj = getattr(ef, "mmd_source_object", None) if ef is not None else None
                armature = getattr(ef, "mmd_armature_object", None) if ef is not None else None
                if profile is None:
                    raise ValueError("未找到 MMD 映射表")
                grouped = {}
                for row_index, row in enumerate(profile.rows):
                    target_name = (row.unified_name or "").strip()
                    if not target_name:
                        continue
                    names = [
                        getattr(row, "current_source_name", ""),
                        getattr(row, "mmd_name", ""),
                        getattr(row, "unified_name", ""),
                    ]
                    grouped.setdefault(target_name, []).append((row_index, row, names))
                from ..games.arknights_endfield import props as _mmd_props
                from ..core.mapping import operators as _mmd_ops
                prev = _mmd_props._suspend_mmd_row_update
                _mmd_props._suspend_mmd_row_update = True
                try:
                    merged_groups, disconnected_rows, moved_vertices, removed_groups = _mapping_merge_groups(
                        source_obj,
                        armature,
                        grouped,
                        mode='MMD',
                    )
                    removed_rows = _prune_disconnected_zero_rows(profile, source_obj, mode='MMD')
                finally:
                    _mmd_props._suspend_mmd_row_update = prev
                _mmd_ops._autosync_to_text(ef)
            else:
                gm = getattr(context.scene, "velo_general_mapping", None)
                profile = getattr(gm, "profile", None) if gm is not None else None
                source_obj = getattr(gm, "source_object", None) if gm is not None else None
                armature = getattr(gm, "armature_object", None) if gm is not None else None
                if profile is None:
                    raise ValueError("未找到通用映射表")
                grouped = {}
                for row_index, row in enumerate(profile.rows):
                    target_name = (row.target_name or "").strip()
                    if not target_name:
                        continue
                    names = [
                        getattr(row, "current_source_name", ""),
                        getattr(row, "source_name", ""),
                        getattr(row, "target_name", ""),
                    ]
                    grouped.setdefault(target_name, []).append((row_index, row, names))
                from ..general_mapping import props as _general_props
                from ..general_mapping import operators as _general_ops
                prev = _general_props._suspend_general_row_update
                _general_props._suspend_general_row_update = True
                try:
                    merged_groups, disconnected_rows, moved_vertices, removed_groups = _mapping_merge_groups(
                        source_obj,
                        armature,
                        grouped,
                        mode='GENERAL',
                    )
                    removed_rows = _prune_disconnected_zero_rows(profile, source_obj, mode='GENERAL')
                finally:
                    _general_props._suspend_general_row_update = prev
                _general_ops.autosync_general_to_text(gm)
                _general_ops.refresh_general_runtime(gm, context)
            bits = [
                f"映射合并({self.mapping_mode})",
                f"合并来源组 {merged_groups}",
                f"断开冗余行 {disconnected_rows}",
                f"转移顶点 {moved_vertices}",
            ]
            if removed_groups > 0:
                bits.append(f"清理空源组 {removed_groups}")
            if removed_rows > 0:
                bits.append(f"删除空行 {removed_rows}")
            settings.last_report = "；".join(bits)
            self.report({'INFO'}, iface_(str(settings.last_report)))
        except Exception as exc:
            settings.last_report = str(exc)
            self.report({'ERROR'}, iface_(str(str(exc))))
            _invalidate_weight_overlay_caches(context)
            return {'CANCELLED'}
        _invalidate_weight_overlay_caches(context)
        return {'FINISHED'}


class VELO_OT_weight_normalize_selected_vertices(bpy.types.Operator):
    bl_idname = "velo.weight_normalize_selected_vertices"
    bl_label = 'Normalize selected vertices by proportion'
    bl_description = 'Only process vertices selected in the current edit mode; trim unlocked normal weights according to the current maximum group count and then normalize, without creating new vertex group weights.'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return getattr(getattr(context, "scene", None), "velo_weight_tools", None) is not None

    def execute(self, context):
        _runtime.reset_overlay_pick_runtime(context)
        settings = context.scene.velo_weight_tools
        obj = getattr(context, "active_object", None)
        if obj is None or getattr(obj, "type", None) != 'MESH':
            settings.last_report = "请先选中一个 Mesh 物体"
            self.report({'ERROR'}, iface_(str(settings.last_report)))
            return {'CANCELLED'}
        if getattr(obj, "mode", None) != 'EDIT':
            settings.last_report = "按比例规格化选中顶点需要在 Mesh Edit Mode 下执行"
            self.report({'ERROR'}, iface_(str(settings.last_report)))
            return {'CANCELLED'}
        try:
            selected = _selected_edit_vertex_indices(obj)
            if not selected:
                settings.last_report = "没有选择任何顶点"
                self.report({'ERROR'}, iface_(str(settings.last_report)))
                return {'CANCELLED'}
            bpy.ops.object.mode_set(mode='OBJECT')
            report = _algo.normalize_selected_vertices_proportional(
                obj,
                selected,
                limit_groups_enable=getattr(settings, "limit_groups_enable", False),
                max_groups_per_vertex=getattr(settings, "max_groups_per_vertex", None),
            )
            _set_object_vertex_selection(obj, report.problem_vertices)
            bpy.ops.object.mode_set(mode='EDIT')
            _set_edit_vertex_selection(obj, report.problem_vertices)
            try:
                bpy.ops.mesh.select_mode(type='VERT')
            except Exception:
                pass
            bits = [
                f"按比例规格化选中顶点 {len(selected)} 点",
                f"完成 {report.normalized_vertices} 点",
            ]
            limited_vertices = int(getattr(report, "limited_vertices", 0))
            if limited_vertices:
                bits.append(f"裁剪 {limited_vertices} 点")
            locked_limit_vertices = int(getattr(report, "locked_limit_vertices", 0))
            if locked_limit_vertices:
                bits.append(f"锁定组占满/超限 {locked_limit_vertices} 点")
            if report.problem_vertices:
                bits.append(f"问题顶点 {len(report.problem_vertices)} 点，已保留选中")
            else:
                bits.append("全部完成")
            settings.last_report = "；".join(bits)
            self.report({'INFO'}, iface_(str(settings.last_report)))
            _invalidate_weight_overlay_caches(context)
            return {'FINISHED'}
        except Exception as exc:
            try:
                if getattr(obj, "mode", None) != 'EDIT':
                    bpy.ops.object.mode_set(mode='EDIT')
            except Exception:
                pass
            settings.last_report = str(exc)
            self.report({'ERROR'}, iface_(str(settings.last_report)))
            _invalidate_weight_overlay_caches(context)
            return {'CANCELLED'}


class VELO_OT_weight_transfer(bpy.types.Operator):
    bl_idname = "velo.weight_transfer"
    bl_label = 'Execute weight transfer'
    bl_description = 'Transfer source vertex group weights to the target mesh according to the current WEIGHT settings, and perform optional smoothing, limiting, and normalization.'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "velo_weight_tools", None)
        return settings is not None

    def execute(self, context):
        _runtime.reset_overlay_pick_runtime(context)
        settings = context.scene.velo_weight_tools
        report = _algo.WeightTransferReport(engine=settings.engine)
        source = None
        target = None
        target_group = None
        target_group_name = ""
        mirror_group = None
        mirror_group_name = ""
        snapshots = {}
        created_groups = []
        created_bones = []
        created_group = False
        created_bone = False
        locked_groups = []
        source_lock_snapshot = {}
        original_target_weights = None
        original_mirror_weights = None
        preserve_rows = None
        authority_suppressed_rows = None
        donor_focus_weights = None
        configured_pairs = []
        configured_donors = []
        configured_mirror_donors = []
        robust_smoothing_handled = False
        mirror_flag_stack = contextlib.ExitStack()
        try:
            _props.sync_target_group_name(settings, context)
            _props.sync_mirror_group(settings, context)
            source = settings.source_object
            target = settings.target_object
            if source is None or source.type != 'MESH':
                raise ValueError("请选择来源网格")
            if target is None or target.type != 'MESH':
                raise ValueError("请选择目标网格")
            source_name = (settings.source_group or "").strip()
            source_lock_snapshot = _snapshot_group_locks(source) if source is not target else {}
            source_group = _algo.validate_source_group(source, source_name, require_unlocked=False)
            _source_nonzero, report.source_weight_max = _algo.weight_evidence_stats(
                _algo.read_group_weights(source, source_group)
            )
            resolution = _algo.resolve_target_group(context, settings)
            target_group, created_group, created_bone = _algo.ensure_target_group(
                context,
                settings,
                resolution,
            )
            target_group_name = target_group.name
            if created_group:
                created_groups.append(target_group_name)
            else:
                _snapshot_group(snapshots, target, target_group)
            if source is target and source_name == target_group.name:
                raise ValueError(
                    f"来源和目标是同一个网格 '{source.name}'，且来源组与承接组同为 '{source_name}'；"
                    "继续执行会先清空来源权重。请选择不同承接组、开启手动承接组覆盖，或换一个目标网格。"
                )
            report.target_group = target_group.name
            report.created_group = created_group
            report.created_bone = created_bone
            if created_bone:
                created_bones.append(target_group_name)

            mirror_enabled = False
            mirror_stats = None
            scope_ok, _scope_reason = _algo.is_component_export_object(context.scene, target)
            if scope_ok and (getattr(settings, "mirror_group", "") or "").strip():
                mirror_group_name = _algo.resolve_transfer_mirror_group_name(
                    context,
                    settings,
                    target,
                    target_group.name,
                    (getattr(settings, "mirror_group", "") or "").strip(),
                )
                if mirror_group_name and mirror_group_name != target_group.name:
                    mirror_group, mirror_created = _ensure_editable_group(target, mirror_group_name)
                    if mirror_created:
                        created_groups.append(mirror_group.name)
                    else:
                        _snapshot_group(snapshots, target, mirror_group)
                    mirror_enabled = True
                    mirror_flag_stack.enter_context(_algo.suppress_native_mirror_flags(target))

            if settings.normalize_after:
                configured_pairs = _props.selected_donor_pairs(settings)
                if configured_pairs:
                    configured_names = [donor for donor, _mirror in configured_pairs]
                    exclude_names = [mirror_group.name] if mirror_enabled and mirror_group is not None else []
                    configured_donors = _select_donors_for_group(
                        context,
                        settings,
                        target,
                        target_group,
                        source_name,
                        configured_names=configured_names,
                        exclude_names=exclude_names,
                    )
                    if mirror_enabled and mirror_group is not None and configured_donors:
                        mirror_by_donor = {donor: mirror for donor, mirror in configured_pairs}
                        mirror_names = [mirror_by_donor.get(group.name, "") for group in configured_donors]
                        configured_mirror_donors = _algo.mirrored_donor_groups(
                            context,
                            settings,
                            target,
                            configured_donors,
                            mirror_names=mirror_names,
                            exclude_names=[target_group.name, mirror_group.name],
                        )
                        _validate_mirror_donor_count(configured_donors, configured_mirror_donors)

            original_target_weights = _algo.read_group_weights(target, target_group)
            if mirror_enabled and mirror_group is not None:
                original_mirror_weights = _algo.read_group_weights(target, mirror_group)
            authority_for_domain = [target_group]
            if mirror_enabled and mirror_group is not None:
                authority_for_domain.append(mirror_group)
            locked_boundary_max_groups = (
                getattr(settings, "max_groups_per_vertex", None)
                if getattr(settings, "limit_groups_enable", False)
                else None
            )

            matched = None
            if settings.engine == 'ROBUST':
                weights, matched, matched_count, rescue_info = _algo.transfer_with_robust(context, settings, source_name)
                donor_focus_weights = weights
                robust_smoothing_handled = bool(rescue_info.get("smoothing_handled", False))
                report.raw_weight_nonzero, report.raw_weight_max = _algo.weight_evidence_stats(weights)
                weights, preserve_rows = _algo.apply_locked_boundary_preserve(
                    target,
                    authority_for_domain,
                    target_group,
                    weights,
                    original_weights=original_target_weights,
                    max_groups_per_vertex=locked_boundary_max_groups,
                )
                report.locked_boundary_vertices = int(preserve_rows.sum())
                _algo.write_group_weights(target, target_group, weights)
                report.matched_count = matched_count
                report.rescued_components = int(rescue_info.get("rescued_components", 0))
                report.rescued_vertices = int(rescue_info.get("rescued_vertices", 0))
                report.zero_anchor_components = int(rescue_info.get("zero_anchor_components", 0))
                report.zero_anchor_vertices = int(rescue_info.get("zero_anchor_vertices", 0))
                report.evidence_blocked_components = int(rescue_info.get("evidence_blocked_components", 0))
                report.evidence_blocked_vertices = int(rescue_info.get("evidence_blocked_vertices", 0))
                report.inpaint_fallback = str(rescue_info.get("inpaint_fallback", "") or "")
                report.authority_suppressed_vertices = int(rescue_info.get("authority_suppressed_vertices", 0))
                if robust_smoothing_handled:
                    report.smoothed = bool(rescue_info.get("smoothed", False))
                    report.smoothing_skipped = not report.smoothed
                authority_suppressed_rows = rescue_info.get("authority_suppressed_rows")
            elif settings.engine == 'DATA_TRANSFER_SURFACE':
                weights = _algo.transfer_with_data_transfer(context, settings, source_name, target_group.name)
                report.raw_weight_nonzero, report.raw_weight_max = _algo.weight_evidence_stats(weights)
                weights, preserve_rows = _algo.apply_locked_boundary_preserve(
                    target,
                    authority_for_domain,
                    target_group,
                    weights,
                    original_weights=original_target_weights,
                    max_groups_per_vertex=locked_boundary_max_groups,
                )
                report.locked_boundary_vertices = int(preserve_rows.sum())
                _algo.write_group_weights(target, target_group, weights)
                report.matched_count = _algo.count_group_weights(target, target_group)
            else:
                raise ValueError("未知权重引擎")
            if authority_suppressed_rows is not None:
                preserve_rows = _algo.combine_row_masks(
                    len(target.data.vertices),
                    preserve_rows,
                    authority_suppressed_rows,
                )

            topology_cache = None
            smoothing_needed = (
                settings.smoothing_enable
                and settings.smoothing_repeat > 0
                and settings.smoothing_factor > 0.0
                and not robust_smoothing_handled
            )
            if smoothing_needed or settings.limit_groups_enable:
                try:
                    topology_cache = _algo.build_weight_topology_cache(target)
                except Exception:
                    topology_cache = None
            if smoothing_needed:
                report.smoothed = _algo.apply_seam_safe_smoothing(
                    target,
                    target_group,
                    settings,
                    matched,
                    topology_cache=topology_cache,
                    preserve_rows=preserve_rows,
                )
                report.smoothing_skipped = not report.smoothed
            if mirror_enabled:
                mirror_stats = _algo.mirror_group_weights(
                    target,
                    target_group,
                    mirror_group,
                    preserve_rows=preserve_rows,
                    preserve_weights=original_mirror_weights,
                )
            donors = []
            mirror_donors = []
            if settings.normalize_after:
                automatic_mirror_pairs = mirror_enabled and not configured_pairs
                focus_weights = donor_focus_weights
                if focus_weights is None:
                    focus_weights = _algo.read_group_weights(target, target_group)
                exclude_names = [mirror_group.name] if mirror_enabled and mirror_group is not None else []
                if configured_pairs:
                    donors = list(configured_donors)
                else:
                    donors = _select_donors_for_group(
                        context,
                        settings,
                        target,
                        target_group,
                        source_name,
                        focus_weights=focus_weights,
                        exclude_names=exclude_names,
                        rank_all=automatic_mirror_pairs,
                    )
                report.donors = [vg.name for vg in donors]
                if mirror_enabled and mirror_group is not None and donors:
                    if configured_pairs:
                        mirror_donors = list(configured_mirror_donors)
                    else:
                        eligibility = _algo.auto_donor_pair_eligibility(
                            context,
                            settings,
                            target,
                            donors,
                            exclude_names=[target_group.name, mirror_group.name],
                            max_pairs=_props.donor_count_value(settings),
                        )
                        donors = eligibility.donors
                        mirror_donors = eligibility.mirror_donors
                        skipped_locked_pairs = list(eligibility.skipped_locked_pairs)
                        diagnostic_donors = _select_donors_for_group(
                            context,
                            settings,
                            target,
                            target_group,
                            source_name,
                            focus_weights=focus_weights,
                            exclude_names=exclude_names,
                            include_locked_candidates=True,
                            rank_all=True,
                        )
                        diagnostic = _algo.auto_donor_pair_eligibility(
                            context,
                            settings,
                            target,
                            diagnostic_donors,
                            exclude_names=[target_group.name, mirror_group.name],
                            max_pairs=_props.donor_count_value(settings),
                        )
                        for label in diagnostic.skipped_locked_pairs:
                            if label not in skipped_locked_pairs:
                                skipped_locked_pairs.append(label)
                        report.skipped_locked_donor_pairs = skipped_locked_pairs
                    _validate_mirror_donor_count(donors, mirror_donors)
                    report.donors = [vg.name for vg in donors] + [f"镜像:{vg.name}" for vg in mirror_donors]
            if settings.limit_groups_enable:
                if source is target:
                    report.limit_skipped_same_object = True
                else:
                    authority_groups = [target_group]
                    if mirror_enabled and mirror_group is not None:
                        authority_groups.append(mirror_group)
                    if settings.normalize_after:
                        authority_groups.extend(donors)
                        authority_groups.extend(mirror_donors)
                    for group in authority_groups:
                        _snapshot_group(snapshots, target, group)
                    priority_groups = [target_group]
                    if mirror_enabled and mirror_group is not None:
                        priority_groups.append(mirror_group)
                    limit_report = _algo.apply_limit_groups_scoped(
                        target,
                        settings,
                        authority_groups,
                        priority_groups=priority_groups,
                        preserve_rows=preserve_rows,
                    )
                    report.limited = bool(limit_report.changed)
                    report.protected_over_limit_vertices = int(limit_report.protected_over_limit_vertices)
                    report.authority_limited_vertices = int(limit_report.authority_limited_vertices)
            if settings.normalize_after:
                if source is target:
                    report.normalize_skipped_same_object = True
                elif donors:
                    for group in donors:
                        _snapshot_group(snapshots, target, group)
                    if mirror_enabled and mirror_group is not None:
                        for group in mirror_donors:
                            _snapshot_group(snapshots, target, group)
                        norm_report = _algo.normalize_authority_groups_with_donors(
                            target,
                            [target_group, mirror_group],
                            donors + mirror_donors,
                            preserve_rows=preserve_rows,
                        )
                        report.normalized = bool(norm_report)
                        report.no_yieldable_vertices += int(norm_report.no_yieldable_vertices)
                        report.under_normalized_vertices += int(norm_report.under_normalized_vertices)
                    else:
                        norm_report = _algo.normalize_with_donors(target, target_group, donors, preserve_rows=preserve_rows)
                        report.normalized = bool(norm_report)
                        report.no_yieldable_vertices += int(norm_report.no_yieldable_vertices)
                        report.under_normalized_vertices += int(norm_report.under_normalized_vertices)
                    seed_max_groups = (
                        getattr(settings, "max_groups_per_vertex", None)
                        if getattr(settings, "limit_groups_enable", False)
                        else None
                    )
                    seed_brush = getattr(_algo, "seed_brush_boundary_memberships", None)
                    if callable(seed_brush):
                        seeded = int(seed_brush(
                            target,
                            target_group,
                            donors,
                            topology_cache=topology_cache,
                            max_groups_per_vertex=seed_max_groups,
                        ))
                        if mirror_enabled and mirror_group is not None and mirror_donors:
                            seeded += int(seed_brush(
                                target,
                                mirror_group,
                                mirror_donors,
                                topology_cache=topology_cache,
                                max_groups_per_vertex=seed_max_groups,
                            ))
                        report.brush_seed_vertices = int(getattr(report, "brush_seed_vertices", 0)) + seeded
            _algo.ensure_numeric_export_compatible(target, target_group_name)
            if mirror_enabled and mirror_group is not None:
                _algo.ensure_numeric_export_compatible(target, mirror_group.name)
            current_group = target.vertex_groups.get(target_group_name)
            current_nonzero = 0 if current_group is None else _algo.count_group_weights(target, current_group)
            if current_nonzero <= 0:
                raise RuntimeError(
                    f"权重传递结果为空：'{target.name}/{target_group_name}' 当前没有任何非零权重。"
                    "这通常表示虽然找到了几何匹配，但来源组在这些匹配点上的最终权重仍为 0，"
                    "或后续 limit/normalize 没有可用 donor 可协同分配。请优先检查来源组是否确实覆盖当前区域，"
                    "以及目标区域附近是否存在可参与的 donor。"
                )
            if current_group is not None:
                report.created_bone = _algo.ensure_group_bone(context, settings, target, current_group.name)
                if report.created_bone and current_group.name not in created_bones:
                    created_bones.append(current_group.name)
            if mirror_enabled and mirror_group is not None:
                current_mirror = target.vertex_groups.get(mirror_group.name)
                mirror_nonzero = 0 if current_mirror is None else _algo.count_group_weights(target, current_mirror)
                if mirror_nonzero <= 0:
                    raise RuntimeError(f"镜像权重结果为空：'{target.name}/{mirror_group.name}' 当前没有任何非零权重。")
                if settings.create_bone_if_missing and _algo.ensure_group_bone(context, settings, target, current_mirror.name):
                    created_bones.append(current_mirror.name)
                if _algo.should_persist_transfer_mirror_mapping(
                    context,
                    settings,
                    target,
                    target_group.name,
                    current_mirror.name,
                ):
                    _algo.add_mirror_mapping(settings, target_group.name, current_mirror.name)
            if getattr(settings, "auto_lock_target_groups", True):
                locked_groups = _algo.lock_transfer_target_groups(
                    [group for group in (current_group, mirror_group if mirror_enabled else None) if group is not None]
                )
        except Exception as exc:
            for bone_name in created_bones:
                try:
                    _algo.remove_armature_bone(context, _algo.resolve_armature_object(settings), bone_name)
                except Exception:
                    pass
            _restore_group_snapshots(target, snapshots)
            _remove_created_groups(target, created_groups)
            settings.last_report = str(exc)
            self.report({'ERROR'}, iface_(str(str(exc))))
            _invalidate_weight_overlay_caches(context)
            return {'CANCELLED'}
        finally:
            mirror_flag_stack.close()
            _restore_group_locks(source, source_lock_snapshot)

        _invalidate_weight_overlay_caches(context)

        bits = [f"{report.engine} -> {report.target_group}"]
        if report.created_group:
            bits.append("新组")
        if report.created_bone:
            bits.append("新骨")
        elif settings.create_bone_if_missing and _algo.resolve_armature_object(settings) is None:
            bits.append("未建骨: 未找到目标骨架")
        if report.zero_anchor_components > 0:
            bits.append(f"零锚点 {report.zero_anchor_components} 域/{report.zero_anchor_vertices} 点")
        if report.evidence_blocked_components > 0:
            bits.append(f"无来源正权重 {report.evidence_blocked_components} 域/{report.evidence_blocked_vertices} 点")
        if report.inpaint_fallback:
            bits.append(f"{report.inpaint_fallback.lower()} inpaint 回退")
        if report.raw_weight_nonzero:
            bits.append(
                "evidence "
                f"source {_format_weight_evidence(report.source_weight_max)}/"
                f"raw {_format_weight_evidence(report.raw_weight_max)}/"
                f"{report.raw_weight_nonzero}点"
            )
            if max(float(report.source_weight_max), float(report.raw_weight_max)) < 0.25:
                bits.append("来源权重较弱")
        if report.locked_boundary_vertices:
            bits.append(f"边界保留 {report.locked_boundary_vertices} 点")
        if report.authority_suppressed_vertices:
            bits.append(f"预写入拒绝 {report.authority_suppressed_vertices} 点")
        if report.smoothed:
            bits.append("seam-safe 平滑")
        elif report.smoothing_skipped:
            bits.append("平滑已跳过")
        if report.limited:
            bits.append("limit")
            if report.authority_limited_vertices:
                bits.append(f"本次集合裁剪 {report.authority_limited_vertices} 点")
        if report.protected_over_limit_vertices:
            bits.append(f"锁定组已超限 {report.protected_over_limit_vertices} 点")
        elif report.limit_skipped_same_object:
            bits.append("同对象跳过 limit")
        if report.normalized:
            bits.append("normalize")
            if report.no_yieldable_vertices:
                bits.append(f"无可让路权重 {report.no_yieldable_vertices} 点")
            if report.under_normalized_vertices:
                bits.append(f"规格化不足 {report.under_normalized_vertices} 点")
        elif report.normalize_skipped_same_object:
            bits.append("同对象跳过 normalize")
        if getattr(report, "brush_seed_vertices", 0):
            bits.append(f"模糊边界 membership {report.brush_seed_vertices} 点")
        if report.donors:
            bits.append("供体 " + ", ".join(report.donors))
        skipped_locked_pairs = getattr(report, "skipped_locked_donor_pairs", [])
        if skipped_locked_pairs:
            bits.append("已跳过锁定供体对 " + ", ".join(skipped_locked_pairs))
        if mirror_stats is not None and mirror_group is not None:
            bits.append(f"镜像 {mirror_group.name} {mirror_stats.get('matched_vertices', 0)} 点")
            if mirror_stats.get("coincident_buckets", 0):
                bits.append(f"重合点组 {mirror_stats['coincident_buckets']}")
        if locked_groups:
            if len(locked_groups) == 1:
                bits.append("已锁定承接组")
            else:
                bits.append("已锁定承接组/镜像承接组")
        settings.last_report = "；".join(bits)
        self.report({'INFO'}, iface_(str(settings.last_report)))
        return {'FINISHED'}


_classes = (
    VELO_OT_weight_install_native_dependencies,
    VELO_OT_weight_refresh_groups,
    VELO_OT_weight_show_last_report,
    VELO_OT_weight_copy_last_report,
    VELO_OT_weight_cycle_donor_count,
    VELO_OT_weight_mirror_mapping_add,
    VELO_OT_weight_mirror_mapping_remove,
    VELO_OT_weight_mirror_active_group,
    VELO_OT_weight_merge_groups,
    VELO_OT_weight_merge_mapping_families,
    VELO_OT_weight_normalize_selected_vertices,
    VELO_OT_weight_transfer,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass
