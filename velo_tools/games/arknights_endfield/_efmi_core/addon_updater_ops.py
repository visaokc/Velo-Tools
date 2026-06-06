"""Velo embedded EFMI core: updater disabled intentionally."""


class _DisabledUpdater:
    update_ready = False
    website = ""


updater = _DisabledUpdater()


def register(*args, **kwargs):
    return None


def unregister(*args, **kwargs):
    return None


def get_user_preferences(*args, **kwargs):
    return None


def check_for_update_background(*args, **kwargs):
    return None


def update_notice_box_ui(*args, **kwargs):
    return None


def update_settings_ui(*args, **kwargs):
    return None


def update_settings_ui_condensed(*args, **kwargs):
    return None
