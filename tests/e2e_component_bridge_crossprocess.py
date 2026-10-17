"""跨进程端到端：**老插件的组件注册真的发生**（`component-bridge` 那一跳的 (甲) 判据）。

跑法（在**宿主树**根目录，用宿主树的解释器）：

    MAIBOT_HOST_TREE=<宿主树> python plugins/maibot-team.v1-compat/tests/e2e_component_bridge_crossprocess.py

本件不依赖 pytest（宿主树可能没有测试夹具）；三条用例自带断言、自己起宿主 RPC 与 runner 子进程。

## 前置（本件**不**替宿主补）：`plugin_loader` 两处按路径 `exec_module` 必须先注册 `sys.modules`
`@dataclasses.dataclass` 在"模块没进 `sys.modules`"时会崩（CPython 3.14 实测
`AttributeError: 'NoneType' object has no attribute '__dict__'`），而本件用的描述载体正是 dataclass
⇒ **宿主 `plugin_loader` 那两处（`_local_face_attr` / `_install_local_legacy_face`）要先补
`sys.modules[spec.name] = module`**，否则本件在 (甲) 一定红（且症状是"兜底面装载失败"，
看着像插件的问题）。这一点是 `component-bridge` 的**附带发现**，见回执"覆盖边界"。

## 为什么必须跨进程
`runner` 进程刻意关掉控制台日志（`runner_main._disable_runner_console_logging()`）⇒ 它的日志
到不了宿主控制台 ⇒ "组件注册了没"**只能看 IPC 那一跳的载荷**。本件照
`tests/plugin_runtime_v2/test_legacy_compat_visibility_e2e.py` 的最小宿主 harness 写，
但把两处桩换成**真物**：
* `plugin.register_components` 的桩 ⇒ **真宿主注册路径**（`ComponentRegistry.register_plugin_components`
  + `get_component`），并在 stub 里**如实记录** `component_count` 决定返回值；
* 判据不只看"RPC 到了"，还看**注册产物**（`get_component("e2e.legacy-demo.ping")` 的类型/正则）。

## 现场事实（本树实测，写在这里免得下一个人重踩）
* 上游 `maibot_sdk.compat` 在本机**是装着的**（venv 里 `maibot-plugin-sdk 2.13.0`）
  ⇒ 要测"上游缺席 + 本地面"这条组合，必须用 `sitecustomize.py` 在**子进程**里把它置 None；
  并且**必须注入插件仓根到 `sys.path`**，否则本地面 `legacy_face.py` 的
  `import src.plugin_system` 会撞上宿主仓里那个**真包**（就是本机另一条"假绿"来源）。
* `legacy_face._BasePlugin` 的 `get_plugin_components()` 是**不抛**的（返回 `[]`）；本件用的
  fixture 是子类，所以走的是子类实现。

## 覆盖到哪一步（如实）
✅ 覆盖：宿主 TCP 监听 → runner 子进程握手 → 本地面装上（`bridge-local`）→ 老插件类被认出来
        → `LocalLegacyPluginAdapter` 构造 → `get_components()` 翻 v1→v2（第 1 片 `command` +
        第 2 片 `event_handler`，两条一起走）→ `plugin.register_components`
        RPC → **宿主 handler 收到载荷** → **真注册进 ComponentRegistry**（断言点：注册产物）
❌ 不覆盖：宿主把组件喂给 LLM/工具路由的那一跳；也不覆盖 `host/supervisor.py` 的生产装配
        （本件自己起 `RPCServer`）。宿主 handler 是**替身**：只替"外壳"，注册用真物。
❌ 不覆盖：runner 自己那几行 warning/info 日志（`_disable_runner_console_logging()` 摘了控制台
        handler ⇒ 本件拿不到）⇒ 判据**不**依赖它。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import sys
import textwrap
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT_ENV = "MAIBOT_HOST_TREE"


def _resolve_repo_root() -> Path:
    """定位**宿主树**（含 `src/`，且有本插件在 `plugins/` 下）。

    两条路：① 环境变量 `MAIBOT_HOST_TREE` 显式给；② 从本文件往上找第一个含
    `src/plugin_runtime` 的祖先目录（插件仓在 `<tree>/plugins/<name>/`，故往上两级就够，
    这里写成"找到为止"以免将来挪目录又坏）。
    ⛔ **找不到就当场报错**：静默退回别的树 = 拿着 A 树的宿主测 B 树的插件（假绿的经典形态）。
    """
    override = os.environ.get(REPO_ROOT_ENV, "").strip()
    if override:
        root = Path(override).resolve()
        if not (root / "src" / "plugin_runtime").is_dir():
            raise SystemExit(f"{REPO_ROOT_ENV}={root} 里没有 src/plugin_runtime —— 指错了宿主树")
        return root
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "src" / "plugin_runtime").is_dir():
            return candidate
    raise SystemExit(
        f"找不到宿主树（往上找 src/plugin_runtime 没命中）—— 用 {REPO_ROOT_ENV}=<tree> 显式指定"
    )


REPO_ROOT = _resolve_repo_root()
sys.path.insert(0, str(REPO_ROOT))

from src.plugin_runtime.host.component_registry import ComponentRegistry  # noqa: E402
from src.plugin_runtime.host.rpc_server import RPCServer  # noqa: E402
from src.plugin_runtime.protocol.envelope import Envelope, RegisterPluginPayload  # noqa: E402
from src.plugin_runtime.transport.tcp import TCPTransportServer  # noqa: E402

READY_TIMEOUT_S = 120.0
LEGACY_PLUGIN_ID = "e2e.legacy-demo"
PLUGIN_REPO_DIR = REPO_ROOT / "plugins" / "maibot-team.v1-compat"

#: ⭐ EventHandler 夹具的取值**刻意全取非默认**（见 `_LEGACY_SOURCE`）：三个字段只要有一个没翻过去，
#: (甲) 里"按 event_type 真查得到 + 三个字段逐个对"就会红。全取默认值时，"字段丢了"与"翻对了"
#: **看着一模一样** —— `EventHandlerEntry` 的默认正是 `event_type=""` / `weight=0` / `intercept_message=False`。
_EVENT_HANDLER_NAME = "pre_process_probe"
_EVENT_TYPE = "on_message_pre_process"
_EVENT_WEIGHT = 5

_MANIFEST_V1_FIXTURE: dict[str, Any] = {
    # ⚠️ 本基点宿主只收 manifest_version = 2（manifest_validator 的 SUPPORTED_MANIFEST_VERSIONS）
    "manifest_version": 2,
    "version": "1.0.0",
    "name": "E2E legacy component fixture",
    "description": "E2E 夹具（临时目录内生成，不入库）",
    "author": {"name": "E2E", "url": "https://example.invalid"},
    "license": "GPL-3.0-or-later",
    "urls": {"repository": "https://example.invalid/repo"},
    "host_application": {"min_version": "1.0.0", "max_version": "99.0.0"},
    "sdk": {"min_version": "1.0.0", "max_version": "99.0.0"},
    "capabilities": [],
    "i18n": {"default_locale": "zh_CN", "supported_locales": ["zh_CN"]},
}

# 旧版面插件：真正的 `get_plugin_components()` —— 里面**构造描述对象**那一步正是过去的卡点
_LEGACY_SOURCE = '''"""E2E 夹具：旧版面（V1）插件，带一个 Command 组件 + 一个 EventHandler 组件。"""

from src.plugin_system import BaseEventHandler, BasePlugin, CommandInfo, ComponentType, register_plugin
from src.plugin_system.base.component_types import EventHandlerInfo, ToolParamType


class PingCommand:
    """旧版 Command 组件（类属性声明 + execute）。"""

    command_name = "ping"
    command_pattern = r"^/ping"
    command_description = "示例命令：回声 pong"

    async def execute(self):
        return True, "pong", 0


class PreProcessProbe(BaseEventHandler):
    """旧版 EventHandler 组件（**类属性**声明 —— v1 的 `get_handler_info()` 就是抄这些）。

    ⚠️ 三个取值刻意都取**非默认**（event_type / weight / intercept_message）：只要有一个字段没翻过去，
    (甲) 里那条"按 event_type 真查得到 + 三个字段逐个对"就会红。
    """

    event_type = "on_message_pre_process"
    handler_name = "pre_process_probe"
    handler_description = "示例事件处理器"
    weight = 5
    intercept_message = True

    async def execute(self, message):
        return True, True, None, None, None


@register_plugin
class E2ELegacyDemoPlugin(BasePlugin):
    plugin_name = "e2e-legacy-demo"

    def __init__(self) -> None:
        pass

    def get_plugin_components(self):
        """⭐ 过不去的正是这一步：构造描述对象（CommandInfo / EventHandlerInfo）。"""
        return [
            (
                CommandInfo(
                    name="ping",
                    component_type=ComponentType.COMMAND,
                    description="示例命令：回声 pong",
                    command_pattern=r"^/ping",
                ),
                PingCommand,
            ),
            (
                # ⭐ event_type / weight / intercept_message **不在这里给** ⇒ 只能从实例的类属性上取
                #   （老插件的规范写法；"实例优先、没写才回落到描述对象"这条约定因此被真走过）
                EventHandlerInfo(
                    name="pre_process_probe",
                    component_type=ComponentType.EVENT_HANDLER,
                    description="示例事件处理器",
                ),
                PreProcessProbe,
            ),
        ]


__all__ = ["E2ELegacyDemoPlugin"]
'''

_LEGACY_SOURCE_ACTION_ONLY = '''"""E2E 夹具（反向对照）：只带 Action 组件的老插件 ⇒ 本地面应**具名出声**。"""

from src.plugin_system import ActionInfo, BasePlugin, ComponentType, register_plugin


class PokeAction:
    action_name = "poke"
    action_description = "示例动作"

    async def execute(self):
        return True, "poke", 0


@register_plugin
class E2ELegacyActionOnlyPlugin(BasePlugin):
    plugin_name = "e2e-legacy-action-only"

    def __init__(self) -> None:
        pass

    def get_plugin_components(self):
        return [
            (
                ActionInfo(name="poke", component_type=ComponentType.ACTION, description="示例动作"),
                PokeAction,
            ),
        ]


__all__ = ["E2ELegacyActionOnlyPlugin"]
'''


def _write_plugin(plugin_root: Path, dir_name: str, plugin_id: str, source: str) -> Path:
    plugin_dir = plugin_root / dir_name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    manifest = dict(_MANIFEST_V1_FIXTURE)
    manifest["id"] = plugin_id
    manifest["name"] = f"E2E fixture {plugin_id}"
    (plugin_dir / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=4), encoding="utf-8")
    (plugin_dir / "plugin.py").write_text(source, encoding="utf-8")
    return plugin_dir


def _write_sitecustomize(target_dir: Path, marker_file: Path) -> Path:
    """子进程里：① 把 `maibot_sdk.compat` 置 None（合成上游缺席）；② 把插件仓根顶到 sys.path 最前。

    ③ 另外把宿主换成本次运行树：`sys.path[0]` 被 sitecustomize 顶掉之后，`src` 会指向**插件仓根**
    （那儿没有 `src`）。这里显式把运行树（`REPO_ROOT`）插到 `sys.path[1]`，让 `src.*` 仍来自运行树。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "sitecustomize.py").write_text(
        textwrap.dedent(
            f'''\
            """E2E：合成"上游 compat 缺席" + 让本地面 `import src.plugin_system` 不走宿主里那个真包。"""
            import sys
            from pathlib import Path

            sys.modules["maibot_sdk.compat"] = None
            _plugin_repo = r"{PLUGIN_REPO_DIR}"
            _host_tree = r"{REPO_ROOT}"
            for _entry in (_plugin_repo, _host_tree):
                while _entry in sys.path:
                    sys.path.remove(_entry)
            sys.path.insert(0, _plugin_repo)
            sys.path.insert(1, _host_tree)
            _state = "sys.modules['maibot_sdk.compat'] is None; sys.path[0]=" + sys.path[0] + "; sys.path[1]=" + sys.path[1]
            print(f"[e2e-sitecustomize] applied: {{_state}}", file=sys.stderr, flush=True)
            Path(r"{marker_file}").write_text(_state, encoding="utf-8")
            '''
        ),
        encoding="utf-8",
    )
    return target_dir


async def _run_runner(
    plugin_roots: Sequence[Path],
    *,
    tmp_path: Path,
    block_compat: bool,
) -> dict[str, Any]:
    token = secrets.token_hex(32)
    server = TCPTransportServer(host="127.0.0.1", port=0)
    rpc_server = RPCServer(transport=server, session_token=token)

    loop = asyncio.get_running_loop()
    ready: asyncio.Future[dict[str, Any]] = loop.create_future()
    host_calls: list[str] = []
    registry = ComponentRegistry()

    # ⭐ 真宿主注册路径：`supervisor._handle_register_plugin` 干的就是这几步
    #    （model_validate → 取 components → register_plugin_components）。
    registration: dict[str, Any] = {"payload": None, "registered_count": None, "error": None}

    async def _on_register_components(envelope: Envelope) -> Envelope:
        host_calls.append("plugin.register_components")
        registration["payload"] = dict(envelope.payload)
        try:
            payload = RegisterPluginPayload.model_validate(envelope.payload)
            runtime_components = [component.model_dump() for component in payload.components]
            registration["runtime_components"] = runtime_components
            count = registry.register_plugin_components(payload.plugin_id, runtime_components)
            registration["registered_count"] = count
        except Exception as exc:  # noqa: BLE001 - 如实报，不吞
            registration["error"] = f"{type(exc).__name__}: {exc}"
            return envelope.make_response(payload={"accepted": False, "reason": registration["error"]})
        return envelope.make_response(
            payload={"accepted": True, "plugin_id": payload.plugin_id, "registered_components": count}
        )

    async def _on_runner_ready(envelope: Envelope) -> Envelope:
        host_calls.append("runner.ready")
        if not ready.done():
            ready.set_result(dict(envelope.payload))
        return envelope.make_response(payload={"acknowledged": True})

    def _make_stub(method: str):
        async def _stub(envelope: Envelope) -> Envelope:
            host_calls.append(method)
            return envelope.make_response(payload={"accepted": True, "acknowledged": True})

        return _stub

    rpc_server.register_method("plugin.register_components", _on_register_components)
    rpc_server.register_method("runner.ready", _on_runner_ready)
    for method in ("plugin.bootstrap", "plugin.unregister"):
        rpc_server.register_method(method, _make_stub(method))
    await rpc_server.start()

    marker_file = tmp_path / "sitecustomize_applied.txt"
    result: dict[str, Any] = {
        "payload": None,
        "timed_out": False,
        "stdout": "",
        "stderr": "",
        "marker": None,
        "host_calls": host_calls,
        "registration": registration,
        "registry": registry,
    }

    env = os.environ.copy()
    env["MAIBOT_IPC_ADDRESS"] = f"127.0.0.1:{server._actual_port}"
    env["MAIBOT_SESSION_TOKEN"] = token
    env["MAIBOT_PLUGIN_DIRS"] = os.pathsep.join(str(root) for root in plugin_roots)
    env["MAIBOT_HOST_VERSION"] = "1.0.0"
    env["MAIBOT_RUNNER_GROUP"] = "e2e"
    env["MAIBOT_EXTERNAL_PLUGIN_IDS"] = ""
    env["MAIBOT_BLOCKED_PLUGIN_REASONS"] = "{}"
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    if block_compat:
        site_dir = _write_sitecustomize(tmp_path / "sitecustomize_dir", marker_file)
        env["PYTHONPATH"] = str(site_dir) + os.pathsep + env.get("PYTHONPATH", "")

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.plugin_runtime.runner.runner_main",
        cwd=str(REPO_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        result["payload"] = await asyncio.wait_for(ready, timeout=READY_TIMEOUT_S)
    except asyncio.TimeoutError:
        result["timed_out"] = True
    finally:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=20)
        except asyncio.TimeoutError:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
        result["stdout"] = stdout_bytes.decode("utf-8", errors="replace")
        result["stderr"] = stderr_bytes.decode("utf-8", errors="replace")
        result["marker"] = marker_file.read_text(encoding="utf-8") if marker_file.exists() else None
        await rpc_server.stop()

    print(f"\n[e2e] 宿主侧被调用：{host_calls}")
    return result


def _diagnose(result: dict[str, Any], headline: str) -> str:
    return (
        f"{headline}\n"
        f"--- runner stdout（尾 2500 字符）---\n{result['stdout'][-2500:]}\n"
        f"--- runner stderr（尾 2500 字符）---\n{result['stderr'][-2500:]}"
    )


def _runner_lines(result: dict[str, Any], needle: str, limit: int = 14) -> list[str]:
    """从 runner 的日志行里挑出含 `needle` 的那些（贴原文用）。"""
    out: list[str] = []
    for raw in (result["stdout"] + "\n" + result["stderr"]).splitlines():
        if needle in raw:
            out.append(raw.strip())
    return out[-limit:]


async def case_positive(tmp_path: Path) -> int:
    """**主判据 (甲)**：老插件的 Command + EventHandler 组件**真的注册进了宿主**。"""

    print("\n" + "=" * 78)
    print("(甲) 老插件 Command + EventHandler 组件 ⇒ 跨进程注册真的发生")
    print("=" * 78)
    plugin_root = tmp_path / "plugins"
    _write_plugin(plugin_root, "e2e_legacy_demo", LEGACY_PLUGIN_ID, _LEGACY_SOURCE)

    result = await _run_runner([plugin_root], tmp_path=tmp_path, block_compat=True)

    assert "[e2e-sitecustomize] applied" in result["stderr"], _diagnose(result, "sitecustomize 没在子进程里生效")
    assert result["marker"] and "sys.modules['maibot_sdk.compat'] is None" in result["marker"], _diagnose(
        result, f"marker 内容不对：{result['marker']!r}"
    )
    assert not result["timed_out"], _diagnose(result, f"{READY_TIMEOUT_S:.0f}s 内没等到 runner.ready")

    payload = result["payload"]
    assert payload is not None
    print(f"[e2e] runner.ready 载荷 loaded_plugins = {payload.get('loaded_plugins')!r}")
    print(f"[e2e] runner.ready 载荷 failed_plugins = {payload.get('failed_plugins')!r}")
    print(f"[e2e] runner.ready 载荷 failed_plugin_reasons = {payload.get('failed_plugin_reasons')!r}")
    assert LEGACY_PLUGIN_ID in (payload.get("loaded_plugins") or []), _diagnose(
        result, f"老插件没加载成功：loaded={payload.get('loaded_plugins')!r}"
    )
    assert not (payload.get("failed_plugin_reasons") or {}), (
        f"不该有失败：{payload.get('failed_plugin_reasons')!r}"
    )

    registration = result["registration"]
    assert registration["error"] is None, f"宿主注册报错：{registration['error']}\n" + _diagnose(result, "")
    print("\n[e2e] ---- 宿主收到的 plugin.register_components 载荷（原文）----")
    print(json.dumps(registration["payload"], ensure_ascii=False, indent=2)[:4000])
    print("[e2e] ---- 载荷原文结束 ----\n")
    print(f"[e2e] 宿主 ComponentRegistry.register_plugin_components 返回 = {registration['registered_count']}")
    declared = len(registration["payload"]["components"])
    assert declared >= 2, f"载荷里只有 {declared} 条组件声明（本片起至少 Command + EventHandler 两条）"
    assert registration["registered_count"] == declared, (
        f"声明 {declared} 条、真注册 {registration['registered_count']} 条"
        " ⇒ 有声明在翻 v2 那一步被静默吞掉了（这正是'加载成功、组件却没注册'的假绿）"
    )

    registry: ComponentRegistry = result["registry"]
    entry = registry.get_component(f"{LEGACY_PLUGIN_ID}.ping")
    assert entry is not None, "宿主注册表里查不到 e2e.legacy-demo.ping"
    print("[e2e] ---- 宿主注册产物（真物，不是回显）----")
    print(f"[e2e] registry.get_component('{LEGACY_PLUGIN_ID}.ping') = {entry!r}")
    print(f"[e2e]   component_type = {entry.component_type!r}")
    print(f"[e2e]   compiled_pattern = {getattr(entry, 'compiled_pattern', None)!r}")
    print(f"[e2e]   aliases = {getattr(entry, 'aliases', None)!r}")
    print(f"[e2e]   metadata = {entry.metadata!r}")
    print("[e2e] ---- 注册产物结束 ----\n")
    assert entry.component_type.value == "COMMAND", entry.component_type
    assert entry.compiled_pattern is not None and entry.compiled_pattern.pattern == r"^/ping", entry.compiled_pattern

    # ⭐ 第 2 片新增的那一类：判据 (甲) 要求"载荷里出现该类型 + 返回数 + get_component 拿得到"，
    #    这里再加一条 v2 的**消费路径**（EventDispatcher 就是这么查的：按 event_type 取、按 weight 排）。
    handler_entry = registry.get_component(f"{LEGACY_PLUGIN_ID}.{_EVENT_HANDLER_NAME}")
    assert handler_entry is not None, f"宿主注册表里查不到 {LEGACY_PLUGIN_ID}.{_EVENT_HANDLER_NAME}"
    print(f"[e2e] registry.get_component('{LEGACY_PLUGIN_ID}.{_EVENT_HANDLER_NAME}') = {handler_entry!r}")
    print(f"[e2e]   component_type = {handler_entry.component_type!r}")
    print(
        f"[e2e]   event_type = {handler_entry.event_type!r} · weight = {handler_entry.weight!r}"
        f" · intercept_message = {handler_entry.intercept_message!r}"
    )
    print(f"[e2e]   metadata = {handler_entry.metadata!r}")
    assert handler_entry.component_type.value == "EVENT_HANDLER", handler_entry.component_type
    assert handler_entry.event_type == _EVENT_TYPE, handler_entry.event_type
    assert handler_entry.weight == _EVENT_WEIGHT, handler_entry.weight
    assert handler_entry.intercept_message is True, handler_entry.intercept_message
    matched = registry.get_event_handlers(_EVENT_TYPE)
    print(f"[e2e] registry.get_event_handlers('{_EVENT_TYPE}') = {[item.full_name for item in matched]}")
    assert [item.full_name for item in matched] == [f"{LEGACY_PLUGIN_ID}.{_EVENT_HANDLER_NAME}"], matched
    assert registry.get_event_handlers("on_message") == [], (
        "换一个 event_type 也命中 ⇒ 上一条是恒真的（查询根本没按 event_type 过滤）"
    )

    stdout_evidence = _runner_lines(result, "注册完成") + _runner_lines(result, "bridge-local")
    print("[e2e] ---- runner 侧 stdout 日志（原文）----")
    for line in stdout_evidence:
        print(f"  {line}")
    if not stdout_evidence:
        print("  （空：`_disable_runner_console_logging()` 把控制台 handler 摘了 ⇒ runner 日志不进 stdout）")
    print("[e2e] ---- 结束 ----\n")
    print(
        "[e2e] ⚠️ 覆盖边界：runner 自己的 warning/info 行**本件拿不到**"
        "（`runner_main._disable_runner_console_logging()` 摘掉了控制台 handler）"
        "⇒ '注册完成'那一行属**未坐实**，本件不据此断言（真据是宿主侧注册产物）。"
    )

    return 0


async def case_action_only_is_named(tmp_path: Path) -> int:
    """(丙) 回归：**只带 Action 的老插件** ⇒ 翻不动 ⇒ 具名出声。

    ⚠️ 本件**如实断言实测链路**（`component-bridge` 实测改判，与最初设想不同）：
    本地面确实抛了**具名**异常（stderr 里有原文），但**宿主侧拿不到** ——
    `runner_main._register_plugin:1380` 调 `get_components()` 那一跳没有 try
    （`_activate_plugin:1636` / `run():461` 也没有）⇒ runner **子进程整个挂掉**，
    `runner.ready` 载荷里 `failed_plugins` / `failed_plugin_reasons` **两栏都空**、
    `plugin.register_components` 那次 RPC **根本没发生**。
    ⇒ 这条是**第三个缺口**（宿主侧），本件不假装它已经通了：断言的正是"具名 + 不静默 + 响亮"，
    并把它与"静默返回空集"明确区分开。
    """

    print("\n" + "=" * 78)
    print("(丙) 翻不动的类别 ⇒ 具名出声（实测：具名落在跨进程边界**之前**）")
    print("=" * 78)
    plugin_root = tmp_path / "plugins"
    _write_plugin(plugin_root, "e2e_legacy_action_only", "e2e.legacy-action-only", _LEGACY_SOURCE_ACTION_ONLY)

    result = await _run_runner([plugin_root], tmp_path=tmp_path, block_compat=True)
    payload = result["payload"]
    loaded = (payload or {}).get("loaded_plugins") or []
    failed = (payload or {}).get("failed_plugins") or []
    reasons = (payload or {}).get("failed_plugin_reasons") or {}
    print(f"[e2e] runner.ready 到了没 = {payload is not None}（timed_out={result['timed_out']}）")
    print(f"[e2e] loaded_plugins = {loaded!r}")
    print(f"[e2e] failed_plugins = {failed!r}")
    print(f"[e2e] failed_plugin_reasons = {json.dumps(reasons, ensure_ascii=False)!r}")

    stderr_lines = [line.strip() for line in result["stderr"].splitlines() if "尚未立项" in line]
    print("[e2e] ---- runner stderr 里那条具名原因（原文）----")
    for line in stderr_lines:
        print(f"  {line}")
    print("[e2e] ---- 结束 ----\n")
    assert stderr_lines, _diagnose(result, "翻不动却没在 stderr 里给出具名原因（那就是静默）")
    assert any("'action' 尚未立项" in line for line in stderr_lines), stderr_lines
    assert any("不静默跳过" in line for line in stderr_lines), stderr_lines

    # 与"静默"划清界限：③ 没有偷偷注册 ④ 没有偷偷算加载成功
    assert not result["registration"].get("registered_count"), (
        f"翻不动却注册了组件（静默降级）：{result['registration']!r}"
    )
    assert "e2e.legacy-action-only" not in loaded, loaded
    print(
        "[e2e] ⚠️ 未坐实（如实）：这条具名原因**没进宿主** `failed_plugin_reasons`"
        "（那一跳无 try ⇒ runner 崩、两栏皆空）——宿主侧兜底属**另立一件**。"
    )
    return 0


async def case_upstream_present(tmp_path: Path) -> int:
    """(乙) 上游在场（block_compat=False）⇒ 同一夹具仍走**上游** compat，行为不变。"""

    print("\n" + "=" * 78)
    print("(乙) 上游 compat 在场 ⇒ 行为不变（反向对照）")
    print("=" * 78)
    plugin_root = tmp_path / "plugins"
    _write_plugin(plugin_root, "e2e_legacy_demo", LEGACY_PLUGIN_ID, _LEGACY_SOURCE)

    result = await _run_runner([plugin_root], tmp_path=tmp_path, block_compat=False)
    assert not result["timed_out"], _diagnose(result, f"{READY_TIMEOUT_S:.0f}s 内没等到 runner.ready")
    payload = result["payload"]
    assert payload is not None
    loaded = payload.get("loaded_plugins") or []
    reasons = payload.get("failed_plugin_reasons") or {}
    print(f"[e2e] loaded_plugins = {loaded!r}")
    print(f"[e2e] failed_plugin_reasons = {json.dumps(reasons, ensure_ascii=False)!r}")
    local_lines = _runner_lines(result, "bridge-local")
    print(f"[e2e] runner 日志里 'bridge-local' 行数 = {len(local_lines)}（上游在场 ⇒ 应为 0）")
    for line in local_lines:
        print(f"  {line}")
    assert LEGACY_PLUGIN_ID in loaded, _diagnose(result, "上游在场时老插件没加载成功")
    assert not reasons, f"上游在场时不该有失败：{reasons!r}"
    assert not local_lines, "上游在场却启用了本地面 ⇒ 行为变了（本地面只该在上游缺席时兜）"
    return 0


async def main() -> int:
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="e2e-compat-component-"))
    print(f"[e2e] 临时根：{root}")
    failures: list[str] = []
    for name, case in (
        ("(甲) 组件注册真的发生", case_positive),
        ("(丙) 翻不动 ⇒ 具名", case_action_only_is_named),
        ("(乙) 上游在场 ⇒ 行为不变", case_upstream_present),
    ):
        case_dir = root / name.split()[0].strip("()")
        case_dir.mkdir(parents=True, exist_ok=True)
        try:
            await case(case_dir)
            print(f"\n[PASS] {name}")
        except AssertionError as exc:
            print(f"\n[FAIL] {name}\n{exc}")
            failures.append(name)
    print("\n" + "=" * 78)
    print(f"结果：{3 - len(failures)}/3 通过" + (f"；失败：{failures}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
