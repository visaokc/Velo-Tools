"""Velo Tools - 多游戏分区根

每个游戏子模块需要暴露：
    register()     -> 注册自身所有 bpy 类
    unregister()   -> 反注册
    enabled()      -> bool, 是否在当前 UI 暴露

顶层 __init__.py 会按 enabled() 决定是否调用 register()。
"""

from . import arknights_endfield
from . import wuthering_waves

ALL_GAMES = (arknights_endfield, wuthering_waves)


def register():
    for g in ALL_GAMES:
        if g.enabled():
            g.register()


def unregister():
    for g in reversed(ALL_GAMES):
        if g.enabled():
            try:
                g.unregister()
            except Exception:
                pass
