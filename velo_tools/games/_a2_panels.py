"""Problem A fix + single fold header (mounted/patch style, without touching a single line of _xxx_core / _efmi_core).

History: 1.0.8/1.0.9/1.1.1 three rounds (tag_redraw / moving the dropdown / 0-delay timer re-registering the root panel) all failed,
1.1.2 used A2 (the two game root panels' poll only checks the GAME tab, always instantiate, switching active_game only switches content) and fixed
"switching the dropdown game panel doesn't appear" in the real Blender 4.4 GUI, but A2 would expose both the "终末地 EFMI"
and "鸣潮 WWMI" fold headers under the GAME tab simultaneously (the unselected one empty), which the user does not accept.

1.1.3 = **single container (A1)**: Velo owns a resident container panel `VELO_PT_game` (poll only checks active_tab=='GAME',
so it always instantiates). Its fold header shows the current game name by active_game; its body, via this module's Shim proxy, calls the vendored
root panels' (VTEF_PT_SIDEBAR / VTWW_PT_SIDEBAR) `draw` (along with `draw_menu_*`), thereby reusing the upstream draw
logic as-is, with zero core changes and no duplicated maintenance. Each game's sub-panels (vtef/vtww main panel, bridge, CrossIB, ShapeKey,
ini_toggles, etc.) have their `bl_parent_id` re-parented from the root panel to `VELO_PT_game`, and gate poll by active_game;
the vendored root panels' own poll is changed to always False (only lending their class objects to the Shim, never displayed as panels), so the GAME tab
only shows **one fold header = the current game**, and switching active_game only swaps the container content (the container always instantiates -> sub-panels are added/removed via plain
redraw, replicating the verified-working tool_mode mechanism, not relying on any lazy-instantiation timing).

gate()/ungate() are idempotent (the `_velo_a2` marker prevents disable/enable double-wrapping) and store original values per root_idname for restoration.
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
    """Run the vendored root panel's draw inside the Velo container panel: `.layout` uses the container's,
    other attributes/methods (draw_menu_*, etc.) are delegated to the root class object and invoked with this Shim as self."""

    def __init__(self, layout, root_cls):
        object.__setattr__(self, "layout", layout)
        object.__setattr__(self, "_root_cls", root_cls)

    def __getattr__(self, name):
        attr = getattr(object.__getattribute__(self, "_root_cls"), name)
        if callable(attr) and not isinstance(attr, type):
            return lambda *a, **k: attr(self, *a, **k)
        return attr


def make_draw_body(root_cls):
    """Generate the container body draw function: calls the vendored root panel draw via the Shim (keeping upstream logic, not copying code)."""
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
    """Hide the vendored root panel (poll->False), re-parent its sub-panels to VELO_PT_game and gate them by active_game.

    Must be called at the end of that game's register() (when all its sub-panels are registered), otherwise late-registered sub-panels are missed. Idempotent.
    """
    entry = _STORE.setdefault(root_idname, {"root": None, "subs": []})

    # Compute all sub-panels whose parent chain is rooted at this root panel (before re-parenting, judged via the original parent chain)
    panels = _panel_map()
    descendants = []
    for bid, c in panels.items():
        if bid == root_idname:
            continue
        if _rooted_at(c, root_idname, panels):
            descendants.append((bid, c))

    # Hide the root panel (only lend the class object to the Shim, not displayed as a panel)
    cur_poll = getattr(root_cls, "poll", None)
    cur_fn = getattr(cur_poll, "__func__", None)
    if not (cur_fn is not None and getattr(cur_fn, "_velo_a2", False)):
        entry["root"] = (root_cls, root_cls.__dict__.get("poll", _MISSING))
        root_cls.poll = classmethod(_hidden_poll)

    # Sub-panels: direct sub-panels' bl_parent_id re-parented to the container; all sub-panels' poll gets active_game gating
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
