"""Blender-native localization for Velo Tools.

English UI text is the canonical msgid and fallback. Simplified and
Traditional Chinese intentionally share the same Simplified Chinese catalog.
"""

from __future__ import annotations

import bpy

from .zh import ZH_TRANSLATIONS


DOMAIN = __name__
CHINESE_LOCALES = ("zh_CN", "zh_HANS", "zh_TW", "zh_HANT")
CONTEXTS = ("*", "Operator", "Tooltip")
TRANSLATIONS = {
    locale: {
        (context, msgid): translated
        for msgid, translated in ZH_TRANSLATIONS.items()
        for context in CONTEXTS
    }
    for locale in CHINESE_LOCALES
}


def iface_(msgid: str) -> str:
    """Translate dynamic interface and report text in Blender's active locale."""

    translations = getattr(bpy.app, "translations", None)
    if translations is None:
        return msgid
    return translations.pgettext_iface(msgid)


def tip_(msgid: str) -> str:
    """Translate dynamic tooltip text in Blender's active locale."""

    translations = getattr(bpy.app, "translations", None)
    if translations is None:
        return msgid
    return translations.pgettext_tip(msgid)


def register() -> None:
    try:
        bpy.app.translations.unregister(DOMAIN)
    except RuntimeError:
        pass
    bpy.app.translations.register(DOMAIN, TRANSLATIONS)


def unregister() -> None:
    try:
        bpy.app.translations.unregister(DOMAIN)
    except RuntimeError:
        pass
