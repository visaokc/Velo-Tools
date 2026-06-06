"""跨场景导出 hook —— monkey-patch ``VTWW_Export.execute``。

用户对"从合并文件夹导入并编辑过的基底"点常规「导出 Mod」时：若导出源文件夹含
``CrossSceneRouting.json``，自动走跨场景 orchestrator（折叠合并出通吃多场景的单 mod）；
否则原样 stock 导出（非跨场景项目零影响）。

- **前缀门控**：只 patch velo vendored 的 ``VTWW_Export``（从游戏注册表取类名），不碰用户单装的
  独立 WWMI-Tools 的同名算子。
- **递归 guard**：orchestrator 内部对各子 IB 重导出、以及对 morph 参照重新导出（参照也指向含
  JSON 的合并文件夹）时会再触发 ``vtww.export_mod``；``_IN_XSCENE`` 置位后本 patch 直通 orig
  (stock)，不再递归进跨场景分支。
- 幂等：已 patch 的类跳过（``_PATCHED``）。绝不改 ``_wwmi_core``。
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

import bpy

_PATCHED = {}
_IN_XSCENE = [False]


def _find_vtww_export():
    """从游戏注册表取 WWMI 导出算子类名，再在 sys.modules 里定位该 Operator 子类。"""
    name = "VTWW_Export"
    try:
        from ...games import registry as _registry
        for d in _registry.all_descriptors():
            if getattr(d, "adapter_key", None) == "WWMI":
                name = d.export_op_class
                break
    except Exception:
        pass
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        cls = getattr(mod, name, None)
        if isinstance(cls, type) and hasattr(cls, "execute"):
            return cls
    return None


def _make_patched(orig_execute):
    def patched(self, context):
        if _IN_XSCENE[0]:
            return orig_execute(self, context)  # 递归 guard：子导出/参照导出走 stock
        cfg = getattr(context.scene, "VTWW_settings", None)
        src = getattr(cfg, "object_source_folder", "") if cfg is not None else ""
        routing = (Path(bpy.path.abspath(src)) / "CrossSceneRouting.json") if src else None
        base_col = getattr(cfg, "component_collection", None) if cfg is not None else None
        if not (routing and routing.is_file()) or base_col is None:
            return orig_execute(self, context)  # 非跨场景 → 原样 stock 导出
        from . import orchestrator
        out = str(Path(bpy.path.abspath(cfg.mod_output_folder)) / "cross_scene_velo")
        _IN_XSCENE[0] = True
        try:
            rep = orchestrator.build_cross_scene_mod(
                context, cfg, base_col, str(Path(bpy.path.abspath(src))), out, hole=False)
        except Exception:
            traceback.print_exc()
            try:
                self.report({'ERROR'}, "Cross-scene export failed (see system console).")
            except Exception:
                pass
            return {'CANCELLED'}
        finally:
            _IN_XSCENE[0] = False
        try:
            self.report({'INFO'}, "Cross-scene mod exported to %s | roles=%s" % (out, rep.get("roles")))
        except Exception:
            pass
        return {'FINISHED'}

    return patched


def install():
    cls = _find_vtww_export()
    if cls is None:
        print("[velo.xscene-hook] VTWW_Export not found, skip install")
        return
    if id(cls) in _PATCHED:
        return
    _PATCHED[id(cls)] = (cls, cls.execute)
    cls.execute = _make_patched(cls.execute)
    print("[velo.xscene-hook] patched VTWW_Export.execute (cross-scene fold on CrossSceneRouting.json)")


def remove():
    for _cid, (cls, orig) in list(_PATCHED.items()):
        try:
            cls.execute = orig
        except Exception:
            traceback.print_exc()
    _PATCHED.clear()
