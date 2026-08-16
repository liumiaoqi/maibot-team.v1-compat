"""CompatBridge — V1 IPC 通信桥。

在 V4 Runner 进程内运行 V1 RPCServer（TCP），V1 Runner 子进程连接进来。
复用 V1 的 RPCServer + MsgPackCodec + Envelope，不修改 V1 Runner 代码。
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from typing import Any, Dict, TYPE_CHECKING

from src.common.logger import get_logger
from src.plugin_runtime.host.rpc_server import RPCServer
from src.plugin_runtime.protocol.envelope import (
    BootstrapPluginPayload,
    CapabilityRequestPayload,
    CapabilityResponsePayload,
    Envelope,
    RegisterPluginPayload,
    RunnerReadyPayload,
)
from src.plugin_runtime.transport.tcp import TCPTransportServer

if TYPE_CHECKING:
    from .component_bridge import ComponentBridge
    from .config import CompatConfig

logger = get_logger("plugin_runtime.v1_compat.bridge")


class CompatBridge:
    """V1 IPC 通信桥 — 在 V4 Runner 进程内充当 V1 Host 角色。

    复用 V1 RPCServer 处理握手、接收循环、发送队列、背压控制。
    仅注册 9 个 V1 RPC 方法处理器，桥接到 ComponentBridge/CapabilityBridge。
    """

    def __init__(
        self,
        config: CompatConfig,
        component_bridge: ComponentBridge,
        capability_bridge=None,
    ) -> None:
        self._config = config
        self._component_bridge = component_bridge
        self._capability_bridge = capability_bridge
        self._rpc_server: RPCServer | None = None
        self._runner_process: asyncio.subprocess.Process | None = None
        self._restart_count: int = 0
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._is_ready: bool = False
        self._loaded_plugins: list[str] = []
        self._health_task: asyncio.Task | None = None

        self._component_bridge.set_bridge(self)

    # ── 生命周期 ──────────────────────────────────────────────

    async def start(self) -> None:
        """启动 V1 RPCServer 和 Runner 子进程。"""
        session_token = self._config.session_token or secrets.token_hex(32)
        self._config.session_token = session_token

        # 1. 创建 V1 RPCServer（复用完整握手+接收循环+发送队列）
        transport = TCPTransportServer(host="127.0.0.1", port=0)
        self._rpc_server = RPCServer(
            transport=transport,
            session_token=session_token,
            host_version=self._config.host_version,
        )

        # 注册 9 个 V1 RPC 方法处理器
        self._rpc_server.register_method("plugin.bootstrap", self._handle_bootstrap)
        self._rpc_server.register_method("plugin.register_components", self._handle_register_components)
        self._rpc_server.register_method("plugin.register_plugin", self._handle_register_plugin)
        self._rpc_server.register_method("plugin.unregister", self._handle_unregister)
        self._rpc_server.register_method("runner.ready", self._handle_runner_ready)
        self._rpc_server.register_method("runner.log_batch", self._handle_log_batch)
        self._rpc_server.register_method("cap.call", self._handle_capability_call)
        self._rpc_server.register_method("host.route_message", self._handle_route_message)
        self._rpc_server.register_method("host.update_message_gateway_state", self._handle_gateway_state)

        await self._rpc_server.start()
        listen_port = transport._actual_port
        logger.info("[compat] RPCServer 已启动: tcp://127.0.0.1:%d", listen_port)

        # 2. 启动 V1 Runner 子进程
        await self._spawn_runner(listen_port)

        # 3. 启动健康检查循环
        self._health_task = asyncio.create_task(
            self._health_check_loop(), name="compat-health",
        )

    async def stop(self) -> None:
        """关停 V1 Runner 子进程和 RPCServer。"""
        self._shutdown_event.set()
        if self._health_task is not None:
            self._health_task.cancel()
            self._health_task = None

        if self._runner_process is not None:
            try:
                try:
                    await asyncio.wait_for(self._runner_process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._runner_process.kill()
                    await self._runner_process.wait()
            except Exception as exc:
                logger.warning("[compat] 关停 Runner 子进程异常: %s", exc)

        if self._rpc_server is not None:
            await self._rpc_server.stop()
        logger.info("[compat] 已停止")

    async def wait_ready(self, timeout_s: float = 30.0) -> bool:
        """等待 V1 Runner 就绪（plugin.register_components 完成）。"""
        deadline = asyncio.get_event_loop().time() + timeout_s
        while not self._is_ready:
            if asyncio.get_event_loop().time() > deadline:
                return False
            await asyncio.sleep(0.1)
        return True

    # ── 子进程管理 ────────────────────────────────────────────

    async def _spawn_runner(self, listen_port: int) -> None:
        """启动 V1 Runner 子进程。"""
        env = os.environ.copy()
        env["MAIBOT_IPC_ADDRESS"] = f"127.0.0.1:{listen_port}"
        env["MAIBOT_SESSION_TOKEN"] = self._config.session_token
        env["MAIBOT_PLUGIN_DIRS"] = ",".join(self._config.plugin_dirs)
        env["MAIBOT_HOST_VERSION"] = self._config.host_version
        env["MAIBOT_RUNNER_GROUP"] = self._config.runner_group
        env["MAIBOT_EXTERNAL_PLUGIN_IDS"] = ""
        env["MAIBOT_BLOCKED_PLUGIN_REASONS"] = "{}"

        self._runner_process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "src.plugin_runtime.runner.runner_main",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info("[compat] Runner 子进程已启动: pid=%d", self._runner_process.pid)

    # ── V1 RPC 方法处理器 ─────────────────────────────────────

    async def _handle_bootstrap(self, envelope: Envelope) -> Envelope:
        payload = BootstrapPluginPayload(**envelope.payload)
        result = {
            "plugin_id": payload.plugin_id,
            "configured": True,
            "session_token": self._config.session_token,
        }
        return envelope.make_response(payload=result)

    async def _handle_register_components(self, envelope: Envelope) -> Envelope:
        payload = RegisterPluginPayload(**envelope.payload)
        self._component_bridge.register_v1_components(
            payload.plugin_id,
            payload.components,
            payload.llm_providers or [],
            payload.capabilities_required or [],
        )
        return envelope.make_response(payload={"accepted": True})

    async def _handle_register_plugin(self, envelope: Envelope) -> Envelope:
        return await self._handle_register_components(envelope)

    async def _handle_unregister(self, envelope: Envelope) -> Envelope:
        plugin_id = envelope.payload.get("plugin_id", "")
        self._component_bridge.unregister_plugin(plugin_id)
        return envelope.make_response(payload={"accepted": True})

    async def _handle_runner_ready(self, envelope: Envelope) -> Envelope:
        payload = RunnerReadyPayload(**envelope.payload)
        self._loaded_plugins = payload.loaded_plugins or []
        self._is_ready = True
        logger.info(
            "[compat] V1 Runner 就绪: loaded=%d failed=%d",
            len(payload.loaded_plugins or []),
            len(payload.failed_plugins or []),
        )
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_log_batch(self, envelope: Envelope) -> Envelope:
        entries = envelope.payload.get("entries", [])
        for entry in entries:
            logger.info("[compat:v1] %s", entry.get("message", ""))
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_capability_call(self, envelope: Envelope) -> Envelope:
        payload = CapabilityRequestPayload(**envelope.payload)
        if self._capability_bridge is not None:
            result = await self._capability_bridge.handle_cap_call(
                envelope.plugin_id, payload.capability, payload.args,
            )
        else:
            result = {"success": False, "error": "CAPABILITY_BRIDGE_NOT_INITIALIZED"}
        return envelope.make_response(
            payload=CapabilityResponsePayload(
                success=result.get("success", False),
                result=result,
            ).model_dump(),
        )

    async def _handle_route_message(self, envelope: Envelope) -> Envelope:
        msg = envelope.payload
        await self._component_bridge.emit_v1_event("host.message", msg)
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_gateway_state(self, envelope: Envelope) -> Envelope:
        state = envelope.payload
        logger.info("[compat] MessageGateway 状态更新: %s", state.get("plugin_id", ""))
        return envelope.make_response(payload={"acknowledged": True})

    # ── V1 Component 调用（V4→V1 方向）────────────────────────

    async def invoke_component(
        self,
        plugin_id: str,
        component_name: str,
        args: Dict[str, Any],
        timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        """通过 V1 RPCServer 向 V1 Runner 调用组件。"""
        if self._rpc_server is None or not self._rpc_server.is_connected:
            return {"success": False, "error": "IPC_CONNECTION_NOT_READY"}

        try:
            resp = await self._rpc_server.send_request(
                method="plugin.invoke_component",
                plugin_id=plugin_id,
                payload={
                    "component_name": component_name,
                    "args": args,
                },
                timeout_ms=timeout_ms,
            )
            return resp.payload if isinstance(resp.payload, dict) else {}
        except Exception as exc:
            return {"success": False, "error": f"IPC_ERROR: {exc}"}

    # ── 健康检查 ──────────────────────────────────────────────

    async def _health_check_loop(self) -> None:
        """定期检查 V1 Runner 子进程状态，崩溃后自动重启。"""
        try:
            while not self._shutdown_event.is_set():
                await asyncio.sleep(self._config.health_check_interval_s)
                if self._shutdown_event.is_set():
                    return
                if self._runner_process is None:
                    continue
                if self._runner_process.returncode is not None:
                    if self._restart_count >= self._config.max_restart_attempts:
                        logger.error(
                            "[compat] Runner 子进程重启次数已达上限 (%d)",
                            self._config.max_restart_attempts,
                        )
                        return
                    delay = min(
                        self._config.restart_initial_delay_s
                        * (2 ** self._restart_count),
                        self._config.restart_max_delay_s,
                    )
                    self._restart_count += 1
                    logger.warning(
                        "[compat] Runner 子进程异常退出，%.1fs 后重启 (%d/%d)",
                        delay, self._restart_count,
                        self._config.max_restart_attempts,
                    )
                    await asyncio.sleep(delay)
                    self._is_ready = False
                    # 需要重建 RPCServer（旧连接已断开）
                    await self._restart_server_and_runner()
        except asyncio.CancelledError:
            pass

    async def _restart_server_and_runner(self) -> None:
        """重启 RPCServer 和 Runner 子进程。"""
        if self._rpc_server is not None:
            await self._rpc_server.stop()

        transport = TCPTransportServer(host="127.0.0.1", port=0)
        self._rpc_server = RPCServer(
            transport=transport,
            session_token=self._config.session_token,
            host_version=self._config.host_version,
        )
        self._rpc_server.register_method("plugin.bootstrap", self._handle_bootstrap)
        self._rpc_server.register_method("plugin.register_components", self._handle_register_components)
        self._rpc_server.register_method("plugin.register_plugin", self._handle_register_plugin)
        self._rpc_server.register_method("plugin.unregister", self._handle_unregister)
        self._rpc_server.register_method("runner.ready", self._handle_runner_ready)
        self._rpc_server.register_method("runner.log_batch", self._handle_log_batch)
        self._rpc_server.register_method("cap.call", self._handle_capability_call)
        self._rpc_server.register_method("host.route_message", self._handle_route_message)
        self._rpc_server.register_method("host.update_message_gateway_state", self._handle_gateway_state)

        await self._rpc_server.start()
        listen_port = transport._actual_port
        logger.info("[compat] RPCServer 已重启: tcp://127.0.0.1:%d", listen_port)
        await self._spawn_runner(listen_port)
