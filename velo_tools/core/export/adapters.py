"""Export adapters (efmi / wwmi).

Each adapter exposes:
    available() -> bool : whether the target export add-on is available
    invoke_export(context) : trigger the target add-on's export flow
"""
from __future__ import annotations

import bpy


# ---------------- common ----------------

def _has_operator(idname: str) -> bool:
    try:
        path = idname.split(".")
        op = bpy.ops
        for p in path:
            op = getattr(op, p)
        # triggering _get_idname once is enough
        return op.poll() is not None  # poll may raise
    except Exception:
        return False


# ---------------- EFMI ----------------

def efmi_available() -> bool:
    # The Endfield adapter is vendored inside Velo Tools; use scene.VTEF_settings and vtef.export_mod
    return hasattr(bpy.types.Scene, "VTEF_settings")


def efmi_invoke_export(context) -> dict:
    if not efmi_available():
        return {"ok": False, "msg": "未检测到 EFMI 适配（缺少 scene.VTEF_settings）"}
    try:
        bpy.ops.vtef.export_mod('INVOKE_DEFAULT')
        return {"ok": True, "msg": "已转交 EFMI 导出（vtef.export_mod）"}
    except Exception as e:
        return {"ok": False, "msg": f"调用 vtef.export_mod 失败: {e}"}


# ---------------- WWMI ----------------

def wwmi_available() -> bool:
    # Wuthering Waves WWMI is vendored + forked inside Velo Tools; use scene.VTWW_settings and vtww.export_mod
    return hasattr(bpy.types.Scene, "VTWW_settings")


def wwmi_invoke_export(context) -> dict:
    if not wwmi_available():
        return {"ok": False, "msg": "未检测到 WWMI 适配（缺少 scene.VTWW_settings）"}
    try:
        bpy.ops.vtww.export_mod('INVOKE_DEFAULT')
        return {"ok": True, "msg": "已转交 WWMI 导出（vtww.export_mod）"}
    except Exception as e:
        return {"ok": False, "msg": f"调用 vtww.export_mod 失败: {e}"}


ADAPTERS = {
    "EFMI": (efmi_available, efmi_invoke_export),
    "WWMI": (wwmi_available, wwmi_invoke_export),
}


def get_adapter(name: str):
    return ADAPTERS.get(name, ADAPTERS["EFMI"])
