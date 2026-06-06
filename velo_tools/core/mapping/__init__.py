"""三段映射核心（R3 阶段实现）。

文件分布：
- algorithms.py : MMD/unified/native 三段映射算法 + 重命名+权重合并
- text_io.py    : 映射文本格式 parse / serialize
- operators.py  : 5 个面向用户的算子（PLAN §2.3）
- ui.py         : 极简 UI（终末地 Tab 下子面板）
"""

from . import operators as _ops
from . import ui as _ui


def register():
    _ops.register()
    _ui.register()


def unregister():
    _ui.unregister()
    _ops.unregister()
