"""WWMI 跨场景多 IB 合并 —— 导出消费侧（consumer）。

producer（``games/wuthering_waves/xscene_merge.py``）产出合并提取文件夹 +
``CrossSceneRouting.json``。用户导入该文件夹、编辑一份网格、导出。本包负责导出期：
读 JSON → 逐 dungeon IB 从其原生 host 派生 buffer → 命名空间合并成一个通吃多场景的 mod。

- ``assembler``：把 N 个独立 per-IB WWMI mod 命名空间合并 + 贴图按 hash 去重 + 自检（port 自
  已验证的独立原型 ``merge_inis.py``，in-process、无 subprocess）。
- ``orchestrator``：派生编排（import host → transfer 编辑增量/破洞 → stock export → 折衣服 →
  调 assembler），= 已验证的 ``xscene_onemesh_build.py`` 流水线落进 velo、由 JSON 驱动。
- ``patch``（后续 M2 收尾）：monkey-patch ``VTWW_Export.execute``，检测到 JSON 就走跨场景分支。

绝不改 ``_wwmi_core``。
"""
