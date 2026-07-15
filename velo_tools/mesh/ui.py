"""Velo mesh tools UI (merged into the Velo Tools main panel, switched via the top tab)."""

import bpy

from .shapekey_model import shapekey_column_units


_SHAPEKEY_CHECKBOX_UNITS = 1.6
_SHAPEKEY_COUNT_UNITS = 3.0
_SHAPEKEY_VALUE_UNITS = 8.0
_SHAPEKEY_MINIMUM_NAME_UNITS = 6.0
_SHAPEKEY_LAYOUT_MARGIN_UNITS = 2.0
_SHAPEKEY_COLUMN_SNAPSHOTS = {}


def _shapekey_available_units(context):
    system = context.preferences.system
    widget_pixels = 20.0 * float(system.ui_scale) * float(getattr(system, "pixel_size", 1.0))
    return max(float(context.region.width) / widget_pixels - _SHAPEKEY_LAYOUT_MARGIN_UNITS, 1.0)


def _shapekey_column_factors(content_units):
    checkbox_units, name_units, count_units, value_units = shapekey_column_units(
        content_units,
        checkbox_units=_SHAPEKEY_CHECKBOX_UNITS,
        count_units=_SHAPEKEY_COUNT_UNITS,
        value_units=_SHAPEKEY_VALUE_UNITS,
        minimum_name_units=_SHAPEKEY_MINIMUM_NAME_UNITS,
    )
    total_units = checkbox_units + name_units + count_units + value_units
    remaining_units = name_units + count_units + value_units
    return (
        checkbox_units / total_units,
        name_units / remaining_units,
        count_units / (count_units + value_units),
    )


def _snapshot_shapekey_columns(context):
    """Capture one column layout for the complete UIList redraw."""
    region_key = context.region.as_pointer()
    _SHAPEKEY_COLUMN_SNAPSHOTS[region_key] = _shapekey_column_factors(
        _shapekey_available_units(context)
    )


def _shapekey_columns(layout, context):
    # UIList rows can redraw in separate batches during a region resize. Every
    # row must consume the panel's single snapshot instead of measuring again.
    factors = _SHAPEKEY_COLUMN_SNAPSHOTS.get(context.region.as_pointer())
    if factors is None:
        factors = _shapekey_column_factors(
            _SHAPEKEY_CHECKBOX_UNITS
            + _SHAPEKEY_MINIMUM_NAME_UNITS
            + _SHAPEKEY_COUNT_UNITS
            + _SHAPEKEY_VALUE_UNITS
        )
    checkbox_factor, name_factor, count_factor = factors
    row = layout.row(align=True)
    checkbox_split = row.split(factor=checkbox_factor, align=True)
    checkbox = checkbox_split.row(align=True)
    remaining = checkbox_split.row(align=True)
    name_split = remaining.split(factor=name_factor, align=True)
    name = name_split.row(align=True)
    fixed_right = name_split.row(align=True)
    right_split = fixed_right.split(factor=count_factor, align=True)
    count = right_split.row(align=True)
    value = right_split.row(align=True)
    return checkbox, name, count, value


def _iter_virtual_items(_ops, route_settings, root, collection):
    entries = []
    for index, item in enumerate(route_settings.route_items):
        if _ops._get_effective_target_collection(root, item, create=False) == collection:
            entries.append((index, item))
    entries.sort(key=lambda pair: _ops.describe_material_route_item(pair[1]).lower())
    return entries


def _draw_collection_tree(layout, root, collection, route_settings, depth=0):
    from . import operators as _ops

    state = _ops.get_collection_state(route_settings, collection, create=False)
    is_active = getattr(route_settings, "active_collection", None) == collection
    expanded = True if state is None else bool(state.expanded)
    active_index = int(getattr(route_settings, "active_route_index", -1))
    active_item = _ops.get_active_material_route_item(route_settings)
    active_target = _ops._get_effective_target_collection(root, active_item, create=False)
    virtual_items = _iter_virtual_items(_ops, route_settings, root, collection)

    row = layout.row(align=True)
    for _ in range(depth):
        row.label(text="", icon='BLANK1')
    toggle = row.operator(
        "velo.route_toggle_collection",
        text="",
        icon='TRIA_DOWN' if expanded else 'TRIA_RIGHT',
        emboss=False,
    )
    toggle.collection_name = collection.name

    select = row.operator(
        "velo.route_set_active_collection",
        text="",
        icon='RADIOBUT_ON' if is_active else 'RADIOBUT_OFF',
        emboss=False,
    )
    select.collection_name = collection.name

    if active_item is not None:
        assign = row.operator(
            "velo.route_assign_item_to_collection",
            text="",
            icon='CHECKMARK' if active_target == collection else 'IMPORT',
            emboss=False,
        )
        assign.collection_name = collection.name
        assign.item_index = active_index
    else:
        row.label(text="", icon='BLANK1')

    label = collection.name
    if virtual_items:
        label = f"{label} ({len(virtual_items)})"
    row.label(text=label, icon='OUTLINER_COLLECTION')

    add = row.operator("velo.route_add_child_collection", text="", icon='ADD', emboss=False)
    add.parent_name = collection.name
    if collection != root:
        remove = row.operator("velo.route_remove_child_collection", text="", icon='X', emboss=False)
        remove.collection_name = collection.name

    if not expanded:
        return

    for child in sorted(collection.children, key=lambda item: item.name.lower()):
        _draw_collection_tree(layout, root, child, route_settings, depth + 1)
    for index, item in virtual_items:
        item_row = layout.row(align=True)
        for _ in range(depth + 1):
            item_row.label(text="", icon='BLANK1')
        op = item_row.operator(
            "velo.route_select_material_item",
            text="",
            icon='RADIOBUT_ON' if active_index == index else 'RADIOBUT_OFF',
            emboss=False,
        )
        op.item_index = index
        item_row.label(text=_ops.describe_material_route_item(item), icon='MESH_DATA')
        current_active_collection = getattr(route_settings, "active_collection", None)
        if current_active_collection is not None and current_active_collection != collection:
            move = item_row.operator("velo.route_assign_item_to_collection", text="", icon='FORWARD', emboss=False)
            move.collection_name = current_active_collection.name
            move.item_index = index


class VELO_UL_shapekey_agg(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        checkbox, name, count, value = _shapekey_columns(layout, context)
        checkbox.enabled = not item.is_deform_numbered
        checkbox.prop(item, "selected", text="")
        name.prop(item, "name", text="", emboss=True, icon='SHAPEKEY_DATA')
        count.alignment = 'CENTER'
        tooltip = count.operator(
            "velo.shapekey_contributors_tooltip",
            text=f"x{item.count}",
            emboss=False,
        )
        tooltip.count = item.count
        tooltip.object_names = item.contributor_names
        value.prop(item, "value", text="", slider=True)


def _is_mesh_tab(context):
    return context.scene.velo_tools.active_tab == 'MESH'


class VELO_PT_mesh_sculpt(bpy.types.Panel):
    """Multi-object sculpt — merge/restore sculpt workflow from the VTEF toolbox."""
    bl_label = "多物体雕刻"
    bl_idname = "VELO_PT_mesh_sculpt"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 0

    @classmethod
    def poll(cls, context):
        return _is_mesh_tab(context)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 1.1
        col.operator("vtef.create_merged_object", icon='MOD_BOOLEAN')
        col.operator("vtef.apply_merged_object_sculpt", icon='SCULPTMODE_HLT')
        col.operator("vtef.apply_merged_object_sculpt_with_shapekeys", icon='SHAPEKEY_DATA')


class VELO_PT_mesh_actions(bpy.types.Panel):
    bl_label = "材质工具"
    bl_idname = "VELO_PT_mesh_actions"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return _is_mesh_tab(context)

    def draw(self, context):
        layout = self.layout
        sel_count = sum(1 for o in context.selected_objects if o.type == 'MESH')

        info = layout.row()
        info.enabled = False
        info.label(text=f"当前选中网格: {sel_count}", icon='RESTRICT_SELECT_OFF')

        col = layout.column(align=True)
        col.scale_y = 1.2
        col.enabled = sel_count > 0

        prefix_row = col.row(align=True)
        prefix_row.operator(
            "velo.add_component_prefix",
            icon='OUTLINER_COLLECTION',
            text="为选中物体添加Component前缀",
        )
        prefix_row.prop(context.scene.velo_tools, "mesh_component_prefix_id", text="")

        material_row = col.row(align=True)
        material_row.operator("velo.gen_material_for_selected",
                              icon='MATERIAL', text="选中物体生成材质球")
        material_row.prop(
            context.scene.velo_tools,
            "mesh_auto_material_on_rename",
            text="",
            icon='FILE_REFRESH',
            toggle=True,
        )
        sub = col.row(align=True)
        sub.enabled = sel_count > 0
        sub.operator("velo.split_by_material",
                     icon='MOD_EXPLODE', text="按材质拆分")
        thr_row = col.row(align=True)
        thr_row.prop(context.scene.velo_tools, "shapekey_cleanup_threshold",
                     text="形态键清理阈值")
        sub2 = col.row(align=True)
        sub2.enabled = sel_count >= 2
        sub2.operator("velo.merge_by_texture",
                      icon='AUTOMERGE_ON', text="按贴图合并")

        # —— Utilities (from the VTEF toolbox) ——
        layout.separator()
        util = layout.column(align=True)
        util.scale_y = 1.05
        util.label(text="实用工具", icon='TOOL_SETTINGS')
        util.operator("vtef.fill_missing_mesh_data", icon='MESH_DATA')
        util.operator("vtef.apply_modifier_for_object_with_shape_keys", icon='MODIFIER')
        util.operator("vtef.convert_vertex_colors", icon='COLOR')

        if sel_count == 0:
            box = layout.box()
            box.scale_y = 0.8
            box.label(text="请先在视图中选择网格物体", icon='INFO')


class VELO_PT_shapekey_panel(bpy.types.Panel):
    bl_label = "形态键聚合 (按集合)"
    bl_idname = "VELO_PT_shapekey_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 3

    @classmethod
    def poll(cls, context):
        return _is_mesh_tab(context)

    def draw(self, context):
        layout = self.layout
        s = context.scene.velo_tools

        layout.prop(s, "target_collection", text="集合")
        row = layout.row(align=True)
        row.scale_y = 1.1
        row.enabled = s.target_collection is not None
        row.operator("velo.refresh_shapekey_list",
                     icon='FILE_REFRESH', text="强制刷新")
        eligible = [item for item in s.shapekey_items if not item.is_deform_numbered]
        all_selected = bool(eligible) and all(item.selected for item in eligible)
        row.operator(
            "velo.toggle_shapekey_rename_selection",
            text="全不选" if all_selected else "全选",
            icon='CHECKBOX_HLT' if all_selected else 'CHECKBOX_DEHLT',
            depress=all_selected,
        )
        row.operator("velo.rename_shapekeys_deform",
                     icon='SORTALPHA', text="自动重命名")

        n = len(s.shapekey_items)
        if n == 0:
            box = layout.box()
            box.scale_y = 0.8
            box.label(text="选择集合后会自动扫描 (仅扫网格顶点形态键)", icon='INFO')
            return

        layout.label(text=f"共 {n} 种形态键 (自动同步; 改名会刷新到所有网格)")
        _snapshot_shapekey_columns(context)
        layout.template_list(
            "VELO_UL_shapekey_agg", "",
            s, "shapekey_items",
            s, "active_shapekey_index",
            rows=10,
        )
        box = layout.box()
        box.scale_y = 0.8
        box.label(text="勾选未编号项后自动重命名; x N 悬浮可查看所在对象", icon='INFO')


class VELO_PT_mesh_material_routing(bpy.types.Panel):
    bl_label = "按材质分离所属集合"
    bl_idname = "VELO_PT_mesh_material_routing"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Velo Tools'
    bl_parent_id = 'VELO_PT_main'
    bl_order = 2
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _is_mesh_tab(context)

    def draw(self, context):
        from . import operators as _ops

        layout = self.layout
        route_settings = getattr(context.scene, "velo_material_route_tools", None)
        root = _ops.get_export_component_root(context.scene)

        if root is None:
            box = layout.box()
            display = _ops.get_active_game_display(context.scene)
            box.label(text=f"请先在{display} Export Mod 中指定部件集合", icon='INFO')
            return

        layout.label(text=f"导出根集合: {root.name}", icon='OUTLINER_COLLECTION')
        if getattr(_ops.get_export_component_settings(context.scene), "ignore_nested_collections", False):
            warn = layout.box()
            warn.alert = True
            warn.label(text="当前仍在忽略嵌套集合；执行本面板操作时会自动关闭该选项", icon='ERROR')

        toolbar = layout.column(align=True)
        toolbar.scale_y = 1.05
        row = toolbar.row(align=True)
        row.operator("velo.route_refresh_materials", icon='FILE_REFRESH')
        split_row = toolbar.row(align=True)
        split_row.operator("velo.split_by_material_to_collections", icon='MOD_EXPLODE')
        split_row.operator("velo.split_by_texture_to_collections", icon='MATERIAL')
        tree_tools = toolbar.row(align=True)
        tree_tools.operator("velo.route_add_child_collection", icon='ADD', text="新增子集合")
        tree_tools.operator("velo.route_remove_child_collection", icon='X', text="移除当前集合")

        if route_settings is not None and route_settings.active_collection is not None:
            layout.label(text=f"当前集合: {route_settings.active_collection.name}", icon='RESTRICT_SELECT_OFF')
        if route_settings is not None:
            active_item = _ops.get_active_material_route_item(route_settings)
            if active_item is None:
                layout.label(text="当前材质分组: 未选择", icon='MESH_DATA')
            else:
                layout.label(text=f"当前材质分组: {_ops.describe_material_route_item(active_item)}", icon='MESH_DATA')

        tree_box = layout.box()
        tree_box.label(text="集合树（虚拟材质分组）", icon='OUTLINER')
        if route_settings is None or len(route_settings.route_items) == 0:
            tree_box.label(text="点击“刷新材质分组”后，这里会按材质模拟最终分组", icon='INFO')
        else:
            _draw_collection_tree(tree_box.column(align=True), root, root, route_settings)

        note = layout.box()
        note.scale_y = 0.8
        note.label(text="树中的集合是真实 Blender 集合；叶子是按材质模拟出的最终分组，不代表当前真实网格。", icon='INFO')
        note.label(text="已拆分的单材质对象，或按贴图合并后同贴图的多材质对象，在大纲里拖拽后会自动回拉虚拟叶子归属。", icon='FILE_REFRESH')
        note.label(text="点“按贴图分离并归纳到集合”时，会尽量把同贴图材质重组成较少对象后再归纳。", icon='MATERIAL')
        note.label(text="先点一个材质分组，再点任意集合行左侧导入图标即可把它改归属。", icon='MOUSE_LMB')


_classes = (
    VELO_UL_shapekey_agg,
    VELO_PT_mesh_sculpt,
    VELO_PT_mesh_actions,
    VELO_PT_mesh_material_routing,
    VELO_PT_shapekey_panel,
)


def register():
    for c in _classes:
        bpy.utils.register_class(c)


def unregister():
    _SHAPEKEY_COLUMN_SNAPSHOTS.clear()
    for c in reversed(_classes):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass
