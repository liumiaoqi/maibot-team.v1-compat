"""V1 兼容层插件 — 将 V1 插件运行时封装为 V4 插件。"""

from plugins.maibot_team.v1_compat.plugin import create_plugin

__all__ = ["create_plugin"]
