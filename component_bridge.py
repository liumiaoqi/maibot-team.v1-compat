"""ComponentBridge — V1 组件声明桥接。

将 V1 的 8 种组件类型映射为 V4 的泛化调度 Tool/Event。
内部维护 component_name→V1ComponentMapping 路由表。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, TYPE_CHECKING

from src.common.logger import get_logger

if TYPE_CHECKING:
    from .compat_bridge import CompatBridge

logger = get_logger("plugin_runtime.v1_compat.component")


# ── 组件类型到调用方法的映射 ──
_COMPONENT_INVOKE_MAP: Dict[str, str] = {
    "TOOL": "invoke_tool",
    "ACTION": "invoke_tool",      # ACTION 内部转 TOOL
    "COMMAND": "invoke_tool",     # COMMAND 内部转 TOOL
    "API": "invoke_api",
    "LLM_PROVIDER": "invoke_llm_provider",
    "MESSAGE_GATEWAY": "invoke_plugin",  # 消息网关 → plugin 调用
    "EVENT_HANDLER": "",          # EVENT 不在 invoke 路径，走 emit_event
    "HOOK_HANDLER": "",           # HOOK 不在 invoke 路径
}


@dataclass
class V1ComponentMapping:
    """V1 组件到 V4 调度的映射条目。"""

    plugin_id: str
    component_name: str
    component_type: str
    description: str = ""
    chat_scope: str = ""
    allowed_session: str = ""


class ComponentBridge:
    """V1 组件声明桥接器 — 泛化调度路由。

    不逐组件注册独立 V4 Tool/Event，而是通过单一 dispatch Tool 路由。
    """

    def __init__(self, emit_event_callback: Callable | None = None) -> None:
        self._emit_event_cb = emit_event_callback
        self._bridge: CompatBridge | None = None
        self._components: Dict[str, V1ComponentMapping] = {}
        self._plugin_components: Dict[str, List[str]] = {}  # plugin_id → [component_name]

    def set_bridge(self, bridge: CompatBridge) -> None:
        self._bridge = bridge

    def set_emit_callback(self, cb: Callable) -> None:
        self._emit_event_cb = cb

    # ── 组件注册 ─────────────────────────────────────────────

    def register_v1_components(
        self,
        plugin_id: str,
        components: List[Dict[str, Any]],
        llm_providers: List[Dict[str, Any]],
        capabilities_required: List[str],
    ) -> None:
        """注册 V1 插件的组件声明。"""
        names: list[str] = []
        for comp in components:
            name = comp.get("name", "")
            if not name:
                continue
            full_name = f"{plugin_id}.{name}"
            mapping = V1ComponentMapping(
                plugin_id=plugin_id,
                component_name=full_name,
                component_type=comp.get("component_type", ""),
                description=comp.get("description", ""),
                chat_scope=comp.get("chat_scope", ""),
                allowed_session=comp.get("allowed_session", ""),
            )
            self._components[full_name] = mapping
            names.append(full_name)

        # LLM Provider 作为特殊 TOOL 注册
        for provider in llm_providers:
            pname = provider.get("provider_name", "")
            if pname:
                full_name = f"{plugin_id}.llm.{pname}"
                self._components[full_name] = V1ComponentMapping(
                    plugin_id=plugin_id,
                    component_name=full_name,
                    component_type="LLM_PROVIDER",
                )
                names.append(full_name)

        self._plugin_components[plugin_id] = names
        logger.info(
            "[compat] 注册 V1 组件: plugin=%s components=%d providers=%d",
            plugin_id, len(components), len(llm_providers),
        )

    def unregister_plugin(self, plugin_id: str) -> None:
        """注销 V1 插件的所有组件。"""
        names = self._plugin_components.pop(plugin_id, [])
        for name in names:
            self._components.pop(name, None)

    # ── 调用路由 ─────────────────────────────────────────────

    async def invoke_component(
        self, component_name: str, args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """V4 Tool handler → V1 组件调用。"""
        mapping = self._components.get(component_name)
        if mapping is None:
            return {"success": False, "error": f"COMPONENT_NOT_FOUND: {component_name}"}

        invoke_method = _COMPONENT_INVOKE_MAP.get(mapping.component_type, "invoke_tool")
        if not invoke_method:
            return {"success": False, "error": f"UNSUPPORTED_TYPE: {mapping.component_type}"}

        if self._bridge is None:
            return {"success": False, "error": "BRIDGE_NOT_READY"}

        return await self._bridge.invoke_component(
            mapping.plugin_id, mapping.component_name, args,
        )

    def get_component_info(self, component_name: str) -> V1ComponentMapping | None:
        return self._components.get(component_name)

    def list_components(self) -> List[V1ComponentMapping]:
        return list(self._components.values())

    # ── 能力调用分发 ─────────────────────────────────────────

    async def dispatch_capability(
        self, capability: str, args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """分发 V1 cap.call 到对应的 V4 PluginContext 方法。

        Phoenix-8 阶段：映射到核心 Protocol 占位调用。
        """
        # 常见能力 → V4 context 方法映射
        if capability.startswith("send."):
            return {"success": False, "error": "SEND_CAPABILITY_VIA_V4_PLUGIN_CONTEXT"}
        if capability.startswith("db.") or capability.startswith("config."):
            return {"success": False, "error": "STORAGE_CAPABILITY_VIA_V4_PLUGIN_CONTEXT"}
        logger.warning("[compat] 未支持的能力: %s", capability)
        return {"success": False, "error": f"UNSUPPORTED_CAPABILITY: {capability}"}

    # ── V1 Event → V4 Event ─────────────────────────────────

    async def emit_v1_event(self, event_name: str, payload: Dict[str, Any]) -> None:
        """V1 事件 → V4 emit_event 推送。"""
        if self._emit_event_cb is not None:
            await self._emit_event_cb(event_name, payload)
