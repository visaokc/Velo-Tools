"""Semantic material authoring and per-draw slot texture bindings."""


def register():
    from . import ui, hooks
    ui.register()
    hooks.install()


def unregister():
    from . import ui, hooks
    hooks.remove()
    ui.unregister()
