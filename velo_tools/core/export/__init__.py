"""导出预处理 + 适配器层（V0.1.6）。

V0.1.6 起：
- 不再注册独立的「MOD 导出」面板，改为：用户在 vendored EFMI 面板点击
  「导出 Mod」时由 hook 自动触发非破坏性预处理后转交原导出器。
- 1.0.8：导出目标统一由「游戏」tab 的 velo_tools.active_game 决定（经 games/registry），
  不再有独立的 export_adapter 下拉。
- WWMI 适配器同样通过 hook 包装 vtww.export_mod 的 execute（Velo 内置 fork）。
"""
from . import preexport  # noqa: F401
from . import adapters   # noqa: F401
from . import operators  # noqa: F401
from . import hook       # noqa: F401


def register():
    operators.register()
    # hook 的安装放在 games.arknights_endfield.register() 末尾，
    # 因为依赖 vendored EFMI 已经完成 _al.register() 后才能找到 VTEF_Export 类。


def unregister():
    try:
        hook.remove_export_hook()
    except Exception:
        pass
    try:
        operators.unregister()
    except Exception:
        pass
