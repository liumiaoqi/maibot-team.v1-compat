"""CompatBridge — V1 IPC 通信桥。

在 V4 Runner 进程内运行 V1 IPC Server（TCP），V1 Runner 子进程连接进来。
复用 V1 的 RPCServer + MsgPackCodec + Envelope，不修改 V1 Runner 代码。
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from typing import Any, Dict, TYPE_CHECKING

from src.common.logger import get_logger
from src.plugin_runtime.protocol.codec import MsgPackCodec
from src.plugin_runtime.protocol.envelope import (
    BootstrapPluginPayload,

    CapabilityRequestPayload,
    CapabilityResponsePayload,
    Envelope,
    HelloPayload,
    HelloResponsePayload,
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

    启动 V1 IPC Server（TCP 127.0.0.1:0），管理 V1 Runner 子进程生命周期。
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
        self._transport: TCPTransportServer | None = None
        self._codec = MsgPackCodec()
        self._runner_process: asyncio.subprocess.Process | None = None
        self._restart_count: int = 0
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._is_ready: bool = False
        self._listen_port: int = 0
        self._loaded_plugins: list[str] = []
        self._conn = None  # V1 Runner 的 IPC 连接
        self._pending_requests: Dict[int, asyncio.Future[Envelope]] = {}
        self._request_id: int = 0
        self._health_task: asyncio.Task | None = None

        # 注册到 ComponentBridge 的回调
        self._component_bridge.set_bridge(self)

    # ── 生命周期 ──────────────────────────────────────────────

    async def start(self) -> None:
        """启动 V1 IPC Server 和 Runner 子进程。"""
        session_token = self._config.session_token or secrets.token_hex(32)
        self._config.session_token = session_token

        # 1. 启动 V1 IPC Server（TCP 随机端口）
        self._transport = TCPTransportServer(host="127.0.0.1", port=0)
        await self._transport.start(self._handle_connection)
        self._listen_port = self._transport._actual_port
        logger.info("[compat] IPC Server 已启动: tcp://127.0.0.1:%d", self._listen_port)

        # 2. 启动 V1 Runner 子进程
        await self._spawn_runner()

        # 3. 启动健康检查循环
        self._health_task = asyncio.create_task(
            self._health_check_loop(), name="compat-health",
        )

    async def stop(self) -> None:
        """关停 V1 Runner 子进程和 IPC Server。"""
        self._shutdown_event.set()
        if self._health_task is not None:
            self._health_task.cancel()
            self._health_task = None

        if self._runner_process is not None:
            try:
                # 发送 prepare_shutdown + shutdown
                await self._send_shutdown()
                # 等待进程退出
                try:
                    await asyncio.wait_for(self._runner_process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._runner_process.kill()
                    await self._runner_process.wait()
            except Exception as exc:
                logger.warning("[compat] 关停 Runner 子进程异常: %s", exc)

        if self._transport is not None:
            await self._transport.stop()
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

    async def _spawn_runner(self) -> None:
        """启动 V1 Runner 子进程。"""
        env = os.environ.copy()
        env["MAIBOT_IPC_ADDRESS"] = f"127.0.0.1:{self._listen_port}"
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

    async def _send_shutdown(self) -> None:
        """向 V1 Runner 发送关停序列。"""
        pass  # 通过 RPCServer 的 request_shutdown / stop 来处理

    # ── V1 IPC 连接处理 ──────────────────────────────────────

    async def _handle_connection(self, conn) -> None:
        """V1 IPC 连接处理 — 一个 Runner 对应一个连接。"""
        # 握手
        try:
            frame = await asyncio.wait_for(conn.recv_frame(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("[compat] 握手超时")
            await conn.close()
            return

        envelope = self._codec.decode(frame)
        hello = HelloPayload(**envelope.payload)
        session_token = hello.session_token

        if session_token != self._config.session_token:
            logger.warning("[compat] session_token 不匹配")
            resp = envelope.make_response(
                payload=HelloResponsePayload(accepted=False, reason="TOKEN_MISMATCH").model_dump(),
            )
            await conn.send_frame(self._codec.encode_envelope(resp))
            await conn.close()
            return

        resp = envelope.make_response(
            payload=HelloResponsePayload(
                accepted=True, host_version=self._config.host_version,
            ).model_dump(),
        )
        await conn.send_frame(self._codec.encode_envelope(resp))
        self._conn = conn
        logger.info("[compat] 握手成功: runner=%s", hello.runner_id)

        # 消息循环
        try:
            while not self._shutdown_event.is_set():
                try:
                    frame = await asyncio.wait_for(conn.recv_frame(), timeout=30.0)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break

                envelope = self._codec.decode(frame)
                await self._dispatch_rpc(conn, envelope)
        finally:
            self._conn = None
            await conn.close()
            self._is_ready = False

    async def _dispatch_rpc(self, conn, envelope: Envelope) -> None:
        """RPC 方法分发。"""

        # 处理 RESPONSE 类型（V4→V1 请求的响应）
        if envelope.message_type == "RESPONSE":
            fut = self._pending_requests.pop(envelope.request_id, None)
            if fut is not None and not fut.done():
                fut.set_result(envelope)
            return

        method = envelope.method
        handler = {
            "runner.log_batch": self._handle_log_batch,
            "plugin.bootstrap": self._handle_bootstrap,
            "plugin.register_components": self._handle_register_components,
            "plugin.register_plugin": self._handle_register_plugin,
            "plugin.unregister": self._handle_unregister,
            "runner.ready": self._handle_runner_ready,
            "cap.call": self._handle_capability_call,
            "host.route_message": self._handle_route_message,
            "host.update_message_gateway_state": self._handle_gateway_state,
        }.get(method)

        if handler is not None:
            resp = await handler(envelope)
            if resp is not None:
                await conn.send_frame(self._codec.encode_envelope(resp))

    # ── RPC 处理器 ───────────────────────────────────────────

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
        # 等价于 register_components
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

    async def _handle_log_batch(self, envelope: Envelope) -> Envelope | None:
        entries = envelope.payload.get("entries", [])
        for entry in entries:
            logger.info("[compat:v1] %s", entry.get("message", ""))
        return None

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
        # V1 MessageGateway 消息路由 → V4 Event 推送
        msg = envelope.payload
        await self._component_bridge.emit_v1_event("host.message", msg)
        return envelope.make_response(payload={"acknowledged": True})

    async def _handle_gateway_state(self, envelope: Envelope) -> Envelope:
        state = envelope.payload
        logger.info("[compat] MessageGateway 状态更新: %s", state.get("plugin_id", ""))
        return envelope.make_response(payload={"acknowledged": True})

    # ── V1 Component 调用 ────────────────────────────────────

    async def invoke_component(
        self,
        plugin_id: str,
        component_name: str,
        args: Dict[str, Any],
        timeout_ms: int = 30000,
    ) -> Dict[str, Any]:
        """通过 V1 IPC 调用 V1 Runner 的组件。"""
        if self._conn is None:
            return {"success": False, "error": "IPC_CONNECTION_NOT_READY"}
        if self._conn.is_closed:
            return {"success": False, "error": "IPC_CONNECTION_CLOSED"}

        self._request_id += 1
        req_id = self._request_id
        envelope = Envelope(
            message_type="REQUEST",
            request_id=req_id,
            method="plugin.invoke_component",
            plugin_id=plugin_id,
            payload={
                "component_name": component_name,
                "args": args,
            },
        )
        fut: asyncio.Future[Envelope] = asyncio.get_event_loop().create_future()
        self._pending_requests[req_id] = fut

        try:
            await self._conn.send_frame(self._codec.encode_envelope(envelope))
            resp = await asyncio.wait_for(fut, timeout=timeout_ms / 1000.0)
            return resp.payload if isinstance(resp.payload, dict) else {}
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            return {"success": False, "error": "TIMEOUT"}
        except Exception as exc:
            self._pending_requests.pop(req_id, None)
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
                    await self._spawn_runner()
        except asyncio.CancelledError:
            pass
