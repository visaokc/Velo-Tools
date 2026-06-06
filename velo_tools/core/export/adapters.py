"""导出适配器（efmi / wwmi）。

每个适配器对外提供：
    available() -> bool : 目标导出插件是否可用
    invoke_export(context) : 调起目标插件的导出流程
"""
from __future__ import annotations

import bpy


# ---------------- 通用 ----------------

def _has_operator(idname: str) -> bool:
    try:
        path = idname.split(".")
        op = bpy.ops
        for p in path:
            op = getattr(op, p)
        # 触发一次 _get_idname 即可
        return op.poll() is not None  # poll 可能 raise
    except Exception:
        return False


# ---------------- EFMI ----------------

def efmi_available() -> bool:
    # 终末地适配在 Velo Tools 内已 vendor，使用 scene.VTEF_settings 与 vtef.export_mod
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
    # 鸣潮 WWMI 已在 Velo Tools 内 vendor + fork，使用 scene.VTWW_settings 与 vtww.export_mod
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
