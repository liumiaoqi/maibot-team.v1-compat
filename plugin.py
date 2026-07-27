"""V1CompatPlugin — V1 兼容层插件主类。

将 V1 插件运行时封装为 V4 插件，通过泛化调度 Tool/Event 桥接 V1 组件。
"""

from __future__ import annotations

from typing import Any, Dict

from src.plugin_runtime_v2.sdk import Event, MaiBotPlugin, Tool

from .compat_bridge import CompatBridge
from .component_bridge import ComponentBridge
from .config import CompatConfig


def create_plugin() -> type[V1CompatPlugin]:
    """插件入口工厂。"""
    
    return V1CompatPlugin


class V1CompatPlugin(MaiBotPlugin):
    """V1 兼容层插件。

    在 V4 Runner 进程中启动 V1 Runner 子进程，通过 V1 IPC 协议桥接旧插件。
    使用泛化调度 Tool（v1.invoke_component）避免每组件独立注册 V4 Tool。
    """

    plugin_id = "maibot-team.v1-compat"
    plugin_version = "1.0.0"
    scopes = [
        "message:send:text",
        "message:send:image",
        "message:send:emoji",
        "message:send:forward",
        "message:send:hybrid",
        "database:read:self",
        "database:write:self",
        "session:read:detail",
        "session:read:list",
        "agent:read:emotion",
        "person:read:id",
        "system:execute:command",
    ]

    def __init__(self) -> None:
        self._config = CompatConfig()
        self._component_bridge = ComponentBridge()
        self._compat_bridge: CompatBridge | None = None

    # ── 生命周期 ──────────────────────────────────────────────

    async def on_load(self) -> None:
        """启动兼容层：创建 IPC Server、启动 V1 Runner 子进程。"""
        self.ctx.logger.info("[v1-compat] on_load 开始")

        # 注入 V4 Event 回调
        self._component_bridge.set_emit_callback(self.ctx.emit_event)

        # 创建 CapabilityBridge（V1 cap.call → V4 PluginContext）
        from .capability_bridge import CapabilityBridge
        capability_bridge = CapabilityBridge(
            plugin_context=self.ctx, plugin_id=self.plugin_id,
        )

        # 启动 CompatBridge（内部启动 IPC Server + V1 Runner 子进程）
        self._compat_bridge = CompatBridge(
            self._config, self._component_bridge, capability_bridge,
        )
        await self._compat_bridge.start()

        # 等待 V1 Runner 就绪
        ready = await self._compat_bridge.wait_ready(timeout_s=30.0)
        if ready:
            self.ctx.logger.info(
                "[v1-compat] V1 Runner 就绪，已加载 %d 个 V1 组件",
                len(self._component_bridge.list_components()),
            )
        else:
            self.ctx.logger.warning("[v1-compat] V1 Runner 就绪超时")

    async def on_unload(self) -> None:
        """停止兼容层：关停 V1 Runner 子进程、停止 IPC Server。"""
        self.ctx.logger.info("[v1-compat] on_unload 开始")
        if self._compat_bridge is not None:
            await self._compat_bridge.stop()
            self._compat_bridge = None

    # ── 泛化调度 Event + Tool ───────────────────────────────

    @Event(name="v1.component_event", description="V1 兼容层事件通道（Hook/Gateway/Event）")
    async def on_component_event(self, event_data: Dict[str, Any]) -> None:
        """V1 兼容层事件通道 — 由 ComponentBridge 通过 ctx.emit_event 推送。"""
        pass

    @Tool(
        name="v1.invoke_component",
        description="调用 V1 插件的组件（Tool/Command/API 等）",
        parameters_schema={
            "type": "object",
            "properties": {
                "component_name": {
                    "type": "string",
                    "description": "V1 组件全名（格式: plugin_id.component_name）",
                },
                "args": {
                    "type": "object",
                    "description": "组件参数",
                },
            },
            "required": ["component_name"],
        },
    )
    async def tool_invoke_component(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """V1 组件泛化调度入口。

        所有 V1 Tool/Command/API 调用通过此单一 Tool 路由到对应 V1 插件。
        """
        component_name = args.get("component_name", "")
        component_args = args.get("args", {})
        return await self._component_bridge.invoke_component(
            component_name, component_args,
        )
