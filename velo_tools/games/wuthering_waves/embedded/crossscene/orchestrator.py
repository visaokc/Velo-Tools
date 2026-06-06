"""跨场景导出编排：从合并文件夹 + CrossSceneRouting.json 派生各 dungeon IB，命名空间合并成一个 mod。

= 已游戏验证的独立原型 ``xscene_onemesh_build.py`` 流水线落进 velo、由 JSON 驱动：
  1. body：导出（编辑过的）合并基底 → showcase 共享 buffer（原样拷成 work/body）。
  2. 可折 IB（衣服/面部等，.fmt 全布局兼容）：各自 stock 导出 host → ``fold.apply_fold`` 把几何重定向
     （+ 面部的 morph 重投影 / blend remap；衣服等无形态键的只折几何）**就地折进 work/body**，不单独成 mod。
  3. 不可折 IB（小熊，骨数 4≠8）：各自 host 导出独立 buffer（host-transfer）。
  4. editable IB（form2 面部，几何不属基底）：作为新 component 单独导出。
  5. assembler 命名空间合并 [body, own…, editable…] + 贴图 hash 去重 + 自检。
``hole=True`` 走破洞测试版（位置确定性）；``hole=False`` 走真实编辑派生。
绝不改 ``_wwmi_core``：每条 IB 都用 stock ``bpy.ops.vtww.export_mod`` 针对其自己的 source 导出
（face 的原生 morph 因此自动保留），仅在外面编排 + 字符串合并。

折叠件不再区分 clothing/face：producer 自动判可折性（``foldable``），可折就统一走 fold.py
（有形态键则连 morph 一起折、没有则只折几何），不可折走 own-buffer。每条 IB 用其 vb0 hash 作 tag。
"""
import json
import shutil
from pathlib import Path

import bpy
import bmesh


def _dhash(x, y, z):
    h = 2166136261
    for v in (x, y, z):
        iv = (int(round(v * 100)) + 1000000) & 0xffffffff
        h = ((h ^ iv) * 16777619) & 0xffffffff
    return h


def _pos_hole(obj, frac=35):
    """位置确定性破洞：按面质心位置哈希删 ~frac% 面（showcase 与各 host 删同一批物理面 → 跨场景同款）。"""
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bm = bmesh.from_edit_mesh(obj.data)
    faces = list(bm.faces)
    if len(faces) >= 10:
        fd = [f for f in faces if _dhash(*f.calc_center_median()) % 100 < frac]
        if len(fd) >= len(faces):
            fd = fd[:len(faces) // 2]
        bmesh.ops.delete(bm, geom=fd, context='FACES')
        bmesh.update_edit_mesh(obj.data, destructive=True)
    bpy.ops.object.mode_set(mode='OBJECT')


def _import_one(cfg, src, want_hash):
    before = set(c.name for c in bpy.data.collections)
    cfg.object_source_folder = src
    bpy.ops.vtww.import_object()
    new = set(c.name for c in bpy.data.collections) - before
    return bpy.data.collections.get(want_hash) or (bpy.data.collections[sorted(new)[0]] if new else None)


def _export_col(cfg, col, modout, name, src):
    Path(modout).mkdir(parents=True, exist_ok=True)
    cfg.object_source_folder = src
    cfg.component_collection = col
    cfg.mod_output_folder = modout
    cfg.mod_name = name
    bpy.ops.vtww.export_mod()


def _copy_body(work: Path):
    """body = showcase 共享 buffer mod：把基底导出（work/sc）原样拷成 work/body。
    各可折 IB 之后 ``fold.apply_fold`` 就地往 work/body/mod.ini 追加 FoldHost（+ morph）段。"""
    body = work / "body"
    if body.exists():
        shutil.rmtree(body)
    body.mkdir()
    shutil.copytree(work / "sc" / "Meshes", body / "Meshes")
    shutil.copytree(work / "sc" / "Textures", body / "Textures")
    shutil.copy(work / "sc" / "mod.ini", body / "mod.ini")
    return body


def build_cross_scene_mod(context, cfg, base_collection, merged_folder, out_folder, hole=True, workdir=None):
    """主入口：派生 + 合并成一个跨场景 mod。返回 assembler 的自检 report。
    base_collection: 已导入（并可能已编辑）的合并基底 collection（Component 0-7 + 5.001）。
    merged_folder: producer 产出的合并文件夹（含 CrossSceneRouting.json + scene_ibs/）。"""
    merged_folder = Path(merged_folder)
    routing = json.loads((merged_folder / "CrossSceneRouting.json").read_text(encoding="utf-8"))
    work = Path(workdir) if workdir else (Path(out_folder).parent / "_xscene_work")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    # 可折 IB（衣服/面部，.fmt 全布局兼容）折进 base buffer（fold.py）；不可折（小熊，骨数 4≠8）出独立 buffer。
    foldable_ibs = [s for s in routing["scene_ibs"] if s.get("foldable")]
    own_ibs = [s for s in routing["scene_ibs"] if not s.get("foldable")]
    editable_ibs = routing.get("editable_ibs", [])
    # 可编辑额外 IB（如 form2 面部）的 merged component 名 → body 导出须排除
    # （否则 ObjectMerger 把 C8-11 当 showcase comp 8-11，base Metadata 无 8-11 → 报 missing Component 8）。
    eib_comp_names = {f"Component {mi}" for rec in editable_ibs for mi in rec["merged_components"]}

    saved = (cfg.object_source_folder, getattr(cfg, "component_collection", None),
             cfg.mod_output_folder, cfg.mod_name)
    temp_cols = []
    try:
        # 1) body: (破洞) 基底所有 mesh（含额外 IB component，各破各的）；body 导出只取 showcase 组
        if hole:
            for o in [o for o in base_collection.objects if o.type == 'MESH']:
                _pos_hole(o)
        if eib_comp_names:
            body_col = bpy.data.collections.new("xs_body")
            bpy.context.scene.collection.children.link(body_col)
            temp_cols.append(body_col)
            for o in base_collection.objects:
                if o.type == 'MESH' and o.name not in eib_comp_names:
                    body_col.objects.link(o)
            body_export_col = body_col
        else:
            body_export_col = base_collection
        _export_col(cfg, body_export_col, str(work / "sc"), "om_sc", str(merged_folder))

        # body = showcase 共享 buffer mod（基底导出原样拷成 body）；所有可折 IB 折进它。
        mods = [str(_copy_body(work))]

        # 2) 不可折 IB（小熊 waist）：各自 host 导出独立 buffer（host-transfer）
        for s in own_ibs:
            src = str(merged_folder / s["source_folder"])
            col = _import_one(cfg, src, s["ib_hash"])
            if hole:
                for o in [o for o in col.objects if o.type == 'MESH']:
                    _pos_hole(o)
            tag = s["ib_hash"]
            _export_col(cfg, col, str(work / tag), "om_" + tag, src)
            mods.append(str(work / tag))

        # 3) 可折 IB（衣服/面部）：导出取其 host（face 带 morph），再 fold.apply_fold 把 geometry 重定向
        #    +（面部）morph 重投影 + blend remap 全折进 body buffer mod（就地改 work/body）；不单独成一个 mod。
        from . import fold
        # morph 重投影对**编辑后的 body 自身**做位置匹配（绿 mod 当初的做法）：存活顶点在原位、自然匹配
        # dungeon face，morph vid = 编辑后 body 的真实行号、与 body 天然对齐 → 删几何等改拓扑的编辑也正确。
        # （曾有 morph_ref 未编辑参照的 hack，假设同拓扑、删顶点即行号错位粘连，已移除；reproject_morph 的
        #   ref 缺省即 body_meshes，故不传 morph_ref。大幅移动顶点远离原位时被移动顶点按最近邻近似——已知局限。）
        for s in foldable_ibs:
            src = str(merged_folder / s["source_folder"])
            col = _import_one(cfg, src, s["ib_hash"])
            if hole:
                for o in [o for o in col.objects if o.type == 'MESH']:
                    _pos_hole(o)
            tag = s["ib_hash"]
            _export_col(cfg, col, str(work / tag), "om_" + tag, src)
            fold.apply_fold(work, s, tag)

        # 4) editable_ibs（form2 面部等）：复制 C8-11 → 临时 Component 0-3 → against 自己 source 导出
        #    （形态键 per-object，必须单独导出；mesh.copy() 带 form2 形态键 → export 自动重发）。
        eib_roles = []
        for rec in editable_ibs:
            tag = rec["ib_hash"]
            eib_col = bpy.data.collections.new("xs_eib_" + rec["ib_hash"])
            bpy.context.scene.collection.children.link(eib_col)
            temp_cols.append(eib_col)
            temp_objs = []
            for li, mi in zip(rec["local_components"], rec["merged_components"]):
                src_obj = base_collection.objects.get(f"Component {mi}")
                if src_obj is None:
                    continue
                cp = src_obj.copy()
                cp.data = src_obj.data.copy()
                cp.name = f"Component {li}"
                eib_col.objects.link(cp)
                temp_objs.append(cp)
            eib_src = str(merged_folder / rec["source_folder"])
            _export_col(cfg, eib_col, str(work / tag), "om_" + tag, eib_src)
            mods.append(str(work / tag))
            eib_roles.append(tag)
            for cp in temp_objs:
                mesh = cp.data
                bpy.data.objects.remove(cp, do_unlink=True)
                try:
                    bpy.data.meshes.remove(mesh)
                except Exception:
                    pass

        # 5) 命名空间合并 + 贴图去重 + 自检
        from . import assembler
        report = assembler.assemble(str(out_folder), mods)
        report["ib_count"] = len(mods)
        report["roles"] = ["body"] + [s["ib_hash"] for s in own_ibs] + eib_roles
        return report
    finally:
        cfg.object_source_folder, cfg.mod_output_folder, cfg.mod_name = saved[0], saved[2], saved[3]
        if saved[1] is not None:
            cfg.component_collection = saved[1]
        for col in temp_cols:
            for o in list(col.objects):
                col.objects.unlink(o)
            try:
                bpy.context.scene.collection.children.unlink(col)
            except Exception:
                pass
            try:
                bpy.data.collections.remove(col)
            except Exception:
                pass
        # 清理临时工作目录（中间导出 sc/body/各 host），让 mod 输出文件夹只剩纯净的 cross_scene_velo。
        shutil.rmtree(work, ignore_errors=True)
