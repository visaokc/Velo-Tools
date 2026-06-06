"""问题 A 修复 + 单折叠头（挂载式，不改 _xxx_core / _efmi_core 任何一行）。

历程：1.0.8/1.0.9/1.1.1 三轮（tag_redraw / 移下拉 / 0 延迟 timer 重注册根面板）都没修好，
1.1.2 用 A2（两个游戏根面板 poll 只判 GAME tab、恒实例化、切 active_game 只切内容）在真实
Blender 4.4 GUI 修好了「切下拉游戏面板不出现」，但 A2 会在 GAME tab 同时露出「终末地 EFMI」
「鸣潮 WWMI」两个折叠头（未选中的为空），用户不接受。

1.1.3 = **单容器（A1）**：Velo 自有一个常驻容器面板 `VELO_PT_game`（poll 只判 active_tab=='GAME'，
故恒实例化）。它的折叠头按 active_game 显示当前游戏名；它的主体经本模块的 Shim 代理调用 vendored
根面板（VTEF_PT_SIDEBAR / VTWW_PT_SIDEBAR）的 `draw`（连同 `draw_menu_*`），从而原样复用上游绘制
逻辑、零核心改动、不重复维护。各游戏的子面板（vtef/vtww 主面板、bridge、CrossIB、ShapeKey、
ini_toggles 等）的 `bl_parent_id` 由根面板重挂到 `VELO_PT_game`，并按 active_game 门控 poll；
vendored 根面板自身 poll 改为恒 False（只借其类对象给 Shim 用，绝不作为面板显示），于是 GAME tab
内**只出现一个折叠头 = 当前游戏**，切 active_game 只换容器内容（容器恒实例化 → 子面板随 plain
redraw 增减，复刻已验证可用的 tool_mode 机制，不依赖任何惰性实例化时机）。

gate()/ungate() 幂等（`_velo_a2` 标记防 disable/enable 重复包裹）且按 root_idname 存原值可复原。
"""
import bpy

_TAB_VALUE = "GAME"
_CONTAINER = "VELO_PT_game"
_MISSING = object()

# root_idname -> {"root": (cls, own_poll|_MISSING) | None, "subs": [(cls, {attr: old})]}
_STORE = {}


def _scene(context):
    return getattr(context.scene, "velo_tools", None)


class Shim:
    """在 Velo 容器面板里执行 vendored 根面板的 draw：`.layout` 用容器的，
    其余属性/方法（draw_menu_* 等）委托给根类对象，并以本 Shim 作为 self 调用。"""

    def __init__(self, layout, root_cls):
        object.__setattr__(self, "layout", layout)
        object.__setattr__(self, "_root_cls", root_cls)

    def __getattr__(self, name):
        attr = getattr(object.__getattribute__(self, "_root_cls"), name)
        if callable(attr) and not isinstance(attr, type):
            return lambda *a, **k: attr(self, *a, **k)
        return attr


def make_draw_body(root_cls):
    """生成容器主体绘制函数：经 Shim 调 vendored 根面板 draw（保持上游逻辑，不复制代码）。"""
    def _draw_body(container, context):
        root_cls.draw(Shim(container.layout, root_cls), context)
    return _draw_body


def _hidden_poll(cls, context):
    return False
_hidden_poll._velo_a2 = True


def _make_sub_poll(orig_func, game_value):
    def _poll(cls, context):
        s = _scene(context)
        if s is None or getattr(s, "active_game", "ENDFIELD") != game_value:
            return False
        if orig_func is None:
            return True
        return orig_func(cls, context)
    _poll._velo_a2 = True
    return _poll


def _panel_map():
    m = {}
    for nm in dir(bpy.types):
        c = getattr(bpy.types, nm, None)
        if isinstance(c, type) and issubclass(c, bpy.types.Panel) and c is not bpy.types.Panel:
            m[getattr(c, "bl_idname", "") or nm] = c
    return m


def _rooted_at(c, root_idname, panels):
    seen = set()
    cur = c
    while cur is not None:
        bid = getattr(cur, "bl_idname", "") or getattr(cur, "__name__", "")
        if bid == root_idname:
            return True
        pid = getattr(cur, "bl_parent_id", "")
        if not pid or pid in seen:
            return False
        seen.add(pid)
        cur = panels.get(pid)
    return False


def gate(root_idname, game_value, root_cls):
    """隐藏 vendored 根面板（poll→False）、把其子面板重挂到 VELO_PT_game 并按 active_game 门控。

    必须在该游戏 register() 末尾调用（此时它的所有子面板都已注册），否则晚注册的子面板会漏掉。幂等。
    """
    entry = _STORE.setdefault(root_idname, {"root": None, "subs": []})

    # 计算「parent 链根于本根面板」的全部子面板（在重挂之前，用原始 parent 链判定）
    panels = _panel_map()
    descendants = []
    for bid, c in panels.items():
        if bid == root_idname:
            continue
        if _rooted_at(c, root_idname, panels):
            descendants.append((bid, c))

    # 隐藏根面板（只借类对象给 Shim 用，不作为面板显示）
    cur_poll = getattr(root_cls, "poll", None)
    cur_fn = getattr(cur_poll, "__func__", None)
    if not (cur_fn is not None and getattr(cur_fn, "_velo_a2", False)):
        entry["root"] = (root_cls, root_cls.__dict__.get("poll", _MISSING))
        root_cls.poll = classmethod(_hidden_poll)

    # 子面板：直接子面板 bl_parent_id 重挂到容器；所有子面板 poll 加 active_game 门控
    for bid, c in descendants:
        rec = {}
        if getattr(c, "bl_parent_id", "") == root_idname:
            rec["bl_parent_id"] = c.__dict__.get("bl_parent_id", _MISSING)
            c.bl_parent_id = _CONTAINER
        cp = getattr(c, "poll", None)
        cf = getattr(cp, "__func__", None)
        if not (cf is not None and getattr(cf, "_velo_a2", False)):
            rec["poll"] = c.__dict__.get("poll", _MISSING)
            orig_func = cp.__func__ if (cp is not None and hasattr(cp, "__func__")) else None
            c.poll = classmethod(_make_sub_poll(orig_func, game_value))
        if rec:
            entry["subs"].append((c, rec))

    # Bake the changed poll / bl_parent_id into the registered PanelTypes. Blender reads poll &
    # bl_parent_id at register_class time; mutating them on an ALREADY-registered class is ignored by
    # the N-panel region renderer (only Python `cls.poll()` reflects it). So re-register the patched
    # classes: subs first (so they no longer count as the now-hidden root's children), then the root.
    for c, _rec in entry["subs"]:
        try:
            bpy.utils.unregister_class(c)
            bpy.utils.register_class(c)
        except Exception:
            import traceback
            traceback.print_exc()
    if entry["root"] is not None:
        _rc = entry["root"][0]
        try:
            bpy.utils.unregister_class(_rc)
            bpy.utils.register_class(_rc)
        except Exception:
            import traceback
            traceback.print_exc()


def ungate(root_idname):
    entry = _STORE.pop(root_idname, None)
    if not entry:
        return
    for c, rec in reversed(entry["subs"]):
        for attr, old in rec.items():
            try:
                if old is _MISSING:
                    if attr in c.__dict__:
                        delattr(c, attr)
                else:
                    setattr(c, attr, old)
            except Exception:
                pass
    r = entry["root"]
    if r is not None:
        root_cls, own = r
        try:
            if own is _MISSING:
                if "poll" in root_cls.__dict__:
                    delattr(root_cls, "poll")
            else:
                root_cls.poll = own
        except Exception:
            pass
