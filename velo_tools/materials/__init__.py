"""Semantic material authoring and per-draw slot texture bindings."""


def register():
    from . import ui, hooks, groups, group_ui
    groups.register()
    ui.register()
    group_ui.register()
    hooks.install()


def unregister():
    from . import ui, hooks, groups, group_ui
    hooks.remove()
    group_ui.unregister()
    ui.unregister()
    groups.unregister()
