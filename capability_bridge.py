"""CapabilityBridge — V1 能力调用桥接到 V4 PluginContext。

V1 插件通过 capability 字符串（如 send.text）调用宿主能力，
V4 通过 PluginContext 子对象（如 ctx.send.text）提供相同能力。
本桥接层做参数映射和错误转换。
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from src.common.logger import get_logger
from src.plugin_runtime_v2.sdk.context import PluginContext

logger = get_logger("plugin_runtime_v2.compat.capability_bridge")

# ── 无 V4 对应的能力列表 ──
_UNSUPPORTED_CAPABILITIES: frozenset[str] = frozenset({
    "emoji.get_random", "emoji.get_by_description", "emoji.get_count",
    "emoji.get_emotions", "emoji.get_all", "emoji.get_info",
    "emoji.register", "emoji.delete",
    "llm.generate", "llm.generate_with_tools", "llm.embed",
    "llm.transcribe_audio", "llm.get_available_models",
    "message.get_by_time", "message.get_by_id", "message.get_recent",
    "message.count_new", "message.build_readable",
    "statistics.local.models", "statistics.local.model_trend",
    "statistics.local.token_trend", "statistics.local.token_distribution",
    "statistics.local.message_trend", "statistics.local.tool_trend",
    "statistics.local.online_time_trend",
    "knowledge.search", "render.html2png",
    "person.get_id", "person.get_value", "person.get_id_by_name",
    "tool.get_definitions",
    "maisaka.context.append", "maisaka.proactive.trigger",
    "api.call", "api.get", "api.list", "api.replace_dynamic",
    "component.get_all_plugins", "component.get_plugin_info",
    "component.get_plugin_config_schema", "component.update_plugin_config",
    "component.list_loaded_plugins", "component.list_registered_plugins",
    "component.enable", "component.disable",
    "component.load_plugin", "component.unload_plugin", "component.reload_plugin",
    "frequency.get_current_talk_value", "frequency.set_adjust",
    "frequency.get_adjust",
    "agent.emotion.get", "agent.relationship.get",
    "send.command", "send.custom",
    "db.query", "db.count", "db.create",
})


class CapabilityBridge:
    """V1 能力 → V4 PluginContext 桥接。

    V1 插件通过 capability 字符串调用宿主能力，
    本桥接层根据映射表路由到 PluginContext 的子方法。
    """

    def __init__(self, v4_ctx: PluginContext, plugin_id: str) -> None:
        self._ctx = v4_ctx
        self._plugin_id = plugin_id
        self._CAPABILITY_MAP: Dict[str, Callable] = {
            # ── send.* ──
            "send.text": self._cap_send_text,
            "send.image": self._cap_send_image,
            "send.emoji": self._cap_send_emoji,
            "send.forward": self._cap_send_forward,
            "send.hybrid": self._cap_send_hybrid,
            # ── database.* ──
            "database.get": self._cap_database_get,
            "database.save": self._cap_database_save,
            "database.set": self._cap_database_save,
            "database.delete": self._cap_database_delete,
            "db.get": self._cap_database_get,
            "db.save": self._cap_database_save,
            "db.delete": self._cap_database_delete,
            # ── config.* ──
            "config.get": self._cap_config_get,
            "config.get_plugin": self._cap_config_get,
            "config.get_all": self._cap_config_get_all,
            # ── chat.* ──
            "chat.get_all_streams": self._cap_chat_get_streams,
            "chat.get_group_streams": self._cap_chat_get_streams,
            "chat.get_private_streams": self._cap_chat_get_streams,
            "chat.open_session": self._cap_chat_open_session,
            "chat.get_stream_by_group_id": self._cap_chat_get_stream_info,
            "chat.get_stream_by_user_id": self._cap_chat_get_stream_info,
        }

    async def handle_cap_call(
        self, plugin_id: str, capability: str, args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """处理 V1 能力调用，路由到对应的 V4 PluginContext 方法。

        Returns:
            dict: {"success": bool, "result": Any} 或 {"success": False, "error": str}
        """
        handler = self._CAPABILITY_MAP.get(capability)
        if handler is None:
            if capability in _UNSUPPORTED_CAPABILITIES:
                return {
                    "success": False,
                    "error": f"capability '{capability}' not supported in v4 compat",
                }
            return {"success": False, "error": f"unknown capability: {capability}"}
        try:
            result = await handler(args)
            return {"success": True, "result": result}
        except Exception as e:
            logger.debug("CapabilityBridge 调用失败: cap=%s plugin=%s: %s", capability, plugin_id, e)
            return {"success": False, "error": str(e)}

    # ── send.* 处理 ───────────────────────────────────────

    async def _cap_send_text(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args.get("stream_id", args.get("session_id", ""))
        text = args.get("text", args.get("content", ""))
        return await self._ctx.send.text(session_id, text)

    async def _cap_send_image(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args.get("stream_id", args.get("session_id", ""))
        image_base64 = args.get("image_base64", args.get("image", ""))
        return await self._ctx.send.image(session_id, image_base64)

    async def _cap_send_emoji(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args.get("stream_id", args.get("session_id", ""))
        emoji_base64 = args.get("emoji_base64", args.get("emoji", ""))
        return await self._ctx.send.emoji(session_id, emoji_base64)

    async def _cap_send_forward(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args.get("stream_id", args.get("session_id", ""))
        message_id = args.get("message_id", "")
        return await self._ctx.send.forward(session_id, message_id)

    async def _cap_send_hybrid(self, args: Dict[str, Any]) -> Dict[str, Any]:
        session_id = args.get("stream_id", args.get("session_id", ""))
        segments = args.get("segments", [])
        return await self._ctx.send.hybrid(session_id, segments)

    # ── database.* 处理 ───────────────────────────────────

    def _db_key(self, key: str) -> str:
        """V1 键值存储加名前缀，避免与 V4 原生存储冲突。"""
        return f"v1:{self._plugin_id}:{key}"

    async def _cap_database_get(self, args: Dict[str, Any]) -> Any:
        key = self._db_key(str(args.get("key", "")))
        default = args.get("default")
        return await self._ctx.storage.get(key, default)

    async def _cap_database_save(self, args: Dict[str, Any]) -> dict:
        key = self._db_key(str(args.get("key", "")))
        value = args.get("value")
        await self._ctx.storage.set(key, value)
        return {"key": key, "saved": True}

    async def _cap_database_delete(self, args: Dict[str, Any]) -> dict:
        key = self._db_key(str(args.get("key", "")))
        deleted = await self._ctx.storage.delete(key)
        return {"key": key, "deleted": deleted}

    # ── config.* 处理 ─────────────────────────────────────

    async def _cap_config_get(self, args: Dict[str, Any]) -> dict:
        """V1 config.get/config.get_plugin。

        通过 storage 读取 v1_config/{plugin_id} 键，
        CompatConfig 预先写入插件配置到此键。
        """
        key = self._db_key("_plugin_config")
        config = await self._ctx.storage.get(key, {})
        wanted = args.get("key", "")
        if wanted:
            return {wanted: config.get(wanted)}
        return config

    async def _cap_config_get_all(self, args: Dict[str, Any]) -> dict:
        return await self._cap_config_get(args)

    # ── chat.* 处理 ───────────────────────────────────────

    async def _cap_chat_get_streams(self, args: Dict[str, Any]) -> list:
        """V1 chat.get_all_streams / get_group_streams / get_private_streams。

        通过 get_session_info 无法获取流列表，返回空列表。
        """
        return []

    async def _cap_chat_open_session(self, args: Dict[str, Any]) -> dict:
        return {"success": False, "error": "chat.open_session not supported in v4 compat"}

    async def _cap_chat_get_stream_info(self, args: Dict[str, Any]) -> dict:
        session_id = args.get("stream_id", args.get("session_id", ""))
        if not session_id:
            return {"found": False}
        info = await self._ctx.get_session_info(session_id)
        return info
