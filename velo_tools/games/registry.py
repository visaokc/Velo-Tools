"""Velo Tools 多游戏注册表（单一真相源）。

`scene.velo_tools.active_game`（游戏 tab 顶部下拉）是「当前游戏」的唯一开关。
各 game 驱动在自身 register() 时注册一条 GameDescriptor，共享工具（导出适配器、
导出 hook、按材质分离等）统一查本表，加新游戏只需新增一条描述符，不必逐个改工具。

本模块是纯数据层：不 import bpy，也不 import 任何 game 模块，避免循环导入。
共享层用惰性 import 在函数内取本模块。
"""
from __future__ import annotations


class GameDescriptor:
    """描述一个游戏的导出接线信息。

    settings_attr  : 该游戏在 Scene 上的设置 PointerProperty 名（如 'VTEF_settings'）
    adapter_key    : core/export/adapters.py 的适配器键（'EFMI' / 'WWMI'）
    export_op      : 导出算子 bl_idname（如 'vtef.export_mod'）
    export_op_class: 导出算子类名，给 hook 用 _find_class 定位（如 'VTEF_Export'）
    header_label   : 单容器面板 VELO_PT_game 折叠头显示文本（如 '鸣潮 WWMI'）；由各 game 驱动 register() 时挂上。
    draw_body      : 可选 callable(container_panel, context)，在 VELO_PT_game 容器内绘制本游戏根面板主体
                     （驱动层用 Shim 代理 vendored 根面板 draw）。由各 game 驱动 register() 时挂上。
    """

    def __init__(
        self,
        key: str,
        game_value: str,
        display_name: str,
        settings_attr: str,
        adapter_key: str,
        export_op: str,
        export_op_class: str,
    ):
        self.key = key
        self.game_value = game_value
        self.display_name = display_name
        self.settings_attr = settings_attr
        self.adapter_key = adapter_key
        self.export_op = export_op
        self.export_op_class = export_op_class
        # 单容器面板（VELO_PT_game）用：折叠头文本 + 主体绘制回调，由各 game 驱动 register() 时挂上。
        self.header_label = None
        self.draw_body = None

    def settings(self, scene):
        return getattr(scene, self.settings_attr, None)

    def component_collection(self, scene):
        cfg = self.settings(scene)
        return getattr(cfg, "component_collection", None) if cfg is not None else None

    def __repr__(self):
        return f"<GameDescriptor {self.game_value} settings={self.settings_attr}>"


# game_value -> GameDescriptor（_ORDER 保留注册顺序，给 all_descriptors 用）
_REGISTRY = {}
_ORDER = []

_DEFAULT_GAME = "ENDFIELD"


def register_descriptor(desc: GameDescriptor) -> None:
    _REGISTRY[desc.game_value] = desc
    if desc.game_value not in _ORDER:
        _ORDER.append(desc.game_value)


def unregister_descriptor(game_value: str) -> None:
    _REGISTRY.pop(game_value, None)
    if game_value in _ORDER:
        _ORDER.remove(game_value)


def get_descriptor(game_value: str):
    return _REGISTRY.get(game_value)


def all_descriptors():
    return [_REGISTRY[g] for g in _ORDER if g in _REGISTRY]


def get_active_descriptor(scene):
    """按 scene.velo_tools.active_game 取当前游戏描述符；缺省/未知回退到 ENDFIELD。"""
    s = getattr(scene, "velo_tools", None)
    game_value = getattr(s, "active_game", _DEFAULT_GAME) if s is not None else _DEFAULT_GAME
    return _REGISTRY.get(game_value) or _REGISTRY.get(_DEFAULT_GAME)
