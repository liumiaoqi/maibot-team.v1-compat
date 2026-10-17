"""桥接件两件的**自检**（证据生成器）——在**宿主仓的 venv** 里跑：

    uv run python plugins/maibot-team.v1-compat/selfcheck_legacy_face.py

覆盖：
* 件①(甲) 合成"上游 compat 缺席" ⇒ 本地面装上后老插件的 import 面**仍通**（贴原文）；
* 件①(乙) 反向自检：**搬漏一条** ⇒ 该路径 import **必须出声**（`LocalFaceGapError`）；
* 件①非空自证：清单为空 ⇒ 直接红；并打印**抽到了多少条**（35/60/24）+ ⭐ **组件栏**（判据 (丁)）；
* 件③(甲) **组件桥**：描述载体真能构造 + `LocalLegacyPluginAdapter.get_components()` 真翻出 v2 dict
  （第 1 片 `command`；第 2 片 `event_handler` —— 后者连**类属性 / 描述对象 / 枚举 / 大写串 / 空值**
  五种形态一起对）；
* 件③(丙) 反向自检：**翻不动的类别 / 没有 get_plugin_components / 载体被抹掉 / 映射被抽掉** 四条都必须出声；
* 件②(甲) 合成 runner 侧 `warning` ⇒ 宿主侧**按 level 过滤仍能看到**（贴原文）；
* 件②(乙) 正常 `info` **不被抬级**（否则就是"整体上抬"换可见）。
"""

from __future__ import annotations

import importlib
import importlib.util
import logging  # ruff: ignore[TID251]  自检需要 stdlib Handler 捕获记录，非生产日志入口
import sys
from enum import Enum
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parent
REPO_ROOT = PLUGIN_DIR.parents[1]  # 宿主仓根（`src` 在这里）
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(PLUGIN_DIR.parent))  # 让 `plugins.…` 可导入
sys.path.insert(0, str(PLUGIN_DIR))

legacy_face = importlib.import_module("legacy_face")


def _line(title: str) -> None:
    print(f"\n=== {title} ===")


def item1_positive() -> None:
    """件①(甲)：上游缺席 + 本地面 ⇒ 老插件 import 面仍通。"""

    _line("件①(甲) 上游 compat 缺席 ⇒ 本地面兜住")
    sys.modules["maibot_sdk.compat"] = None
    sys.modules["maibot_sdk.compat._import_hook"] = None
    print(f"  上游可用? {legacy_face.upstream_available()}（合成缺席后应为 False）")

    mode = legacy_face.install_local_face(force=True)
    print(f"  install_local_face() ⇒ {mode}")

    from src.plugin_system import BasePlugin, register_plugin, send_api  # noqa: PLC0415
    from src.plugin_system.apis import send_api as send_api_alt  # noqa: PLC0415

    print("  from src.plugin_system import BasePlugin, BaseAction, register_plugin, send_api ⇒ OK")
    print(f"  BasePlugin 是本地面实现? {BasePlugin is legacy_face._BasePlugin}")
    print(f"  apis.send_api 走同一条? {send_api_alt is send_api}")

    @register_plugin
    class SamplePlugin(BasePlugin):
        plugin_name = "sample"

        def get_plugin_components(self):
            return []

    print(f"  @register_plugin 标记生效? {getattr(SamplePlugin, '_is_legacy_registered', False)}")
    assert BasePlugin is legacy_face._BasePlugin
    assert getattr(SamplePlugin, "_is_legacy_registered", False)


def item1_nonempty_and_reverse() -> None:
    """件①非空自证 + (乙) 反向自检（搬漏一条必须出声）。"""

    _line("件① 非空自证 + (乙) 反向自检")
    counts = legacy_face.assert_nonempty()
    print(
        f"  抽到: 别名 {counts['aliases']} 条 · 名字 {counts['names']} 个 · "
        f"签名模块 {counts['signature_modules']} 个 · adapter {counts['adapter']} 个"
    )
    print(
        f"  ⭐ 组件栏（判据 (丁)，0 条即红）: 描述载体 {counts['components']} 个 · "
        f"组件描述对象（*Info，从 `_IMPLEMENTED` 现数）{counts['component_infos']} 个 · "
        f"已立项可翻的类别 {counts['mappable_component_types']} 类 "
        f"（内容层：{legacy_face._V1_TO_V2_COMPONENT_TYPE}）"
    )
    print(f"  覆盖: {legacy_face.coverage()}")

    # ⭐ `adapter-hop` 那一跳的自证（判据 (丁) 的第二栏）：本地面若抽不到 adapter，这条路会在
    # "面在、adapter 不在"的形态下静默退化——症状和没修一模一样（`failed_plugin_reasons` 非空）。
    assert counts["adapter"] > 0, "本地面抽不到 LocalLegacyPluginAdapter ⇒ '上游缺席时接住老插件'这条路已瞎"
    adapter_cls = legacy_face.LocalLegacyPluginAdapter
    probe = adapter_cls(legacy_face._BasePlugin())
    print(
        f"  [adapter 自证] LocalLegacyPluginAdapter 可构造 ✓ · 持有旧实例 = {type(probe._legacy).__name__} · "
        f"契约面 = {sorted(name for name in ('_set_context', 'get_components', 'get_config_reload_subscriptions', 'on_load', 'on_unload', 'on_config_update') if hasattr(probe, name))}"
    )

    keep = dict(legacy_face.ALIASES)
    try:
        legacy_face.ALIASES.pop("src.plugin_system.apis.send_api")
        legacy_face.install_local_face(force=True)
        for key in [k for k in sys.modules if k.startswith("src.plugin_system")]:
            del sys.modules[key]
        try:
            importlib.import_module("src.plugin_system.apis.send_api")
        except legacy_face.LocalFaceGapError as exc:
            print(f"  [反向自检] 搬漏 send_api ⇒ 出声 ✓：{exc}")
        except ModuleNotFoundError as exc:
            print(f"  [反向自检] 搬漏 send_api ⇒ 出声（ModuleNotFoundError）✓：{exc}")
        else:
            raise AssertionError("搬漏一条却**没有**出声 —— 这份面可能'看着在、其实是空的'")
    finally:
        legacy_face.ALIASES.clear()
        legacy_face.ALIASES.update(keep)

    keep_names = list(legacy_face.FACE_NAMES)
    try:
        legacy_face.FACE_NAMES.clear()
        try:
            legacy_face.assert_nonempty()
        except legacy_face.LocalFaceGapError as exc:
            print(f"  [非空自证的反向自检] 清单清空 ⇒ 直接红 ✓：{exc}")
        else:
            raise AssertionError("清空清单却没红 ⇒ 非空断言是恒真的")
    finally:
        legacy_face.FACE_NAMES.extend(keep_names)

    # ⭐ (丁) 的**内容层**反向自检：把本片新立项的那一类抽掉 ⇒ 必须红（"条数 > 0"挡不住这一手）
    keep_mappings = dict(legacy_face._V1_TO_V2_COMPONENT_TYPE)
    try:
        legacy_face._V1_TO_V2_COMPONENT_TYPE.pop("event_handler")
        try:
            legacy_face.assert_nonempty()
        except legacy_face.LocalFaceGapError as exc:
            print(f"  [映射内容层的反向自检] 抽掉 event_handler ⇒ 直接红 ✓：{exc}")
        else:
            raise AssertionError("已立项的 event_handler 被抽掉却没红 ⇒ 那条内容级断言是恒真的")
    finally:
        legacy_face._V1_TO_V2_COMPONENT_TYPE.clear()
        legacy_face._V1_TO_V2_COMPONENT_TYPE.update(keep_mappings)


def item3_component_bridge() -> None:
    """件③（`component-bridge` 那一跳）：描述载体**真能构造** + `get_components()` **真翻**出 v2 dict。"""

    _line("件③ 组件桥：载体可构造 + 真翻 v2 dict")
    sys.modules["maibot_sdk.compat"] = None
    legacy_face.install_local_face(force=True)
    for key in [k for k in sys.modules if k.startswith("src.plugin_system")]:
        del sys.modules[key]

    from src.plugin_system import (  # noqa: PLC0415
        BasePlugin,
        CommandInfo,
        ComponentType,
        EventHandlerInfo,
        register_plugin,
    )
    from src.plugin_system.base.component_types import ToolParamType  # noqa: PLC0415

    print(
        "  构造 CommandInfo ⇒ "
        f"{CommandInfo(name='ping', component_type=ComponentType.COMMAND, command_pattern='^/ping')!r}"
    )
    print(f"  ComponentType.COMMAND = {ComponentType.COMMAND!r}（v1 取值小写）· ToolParamType.STRING = {ToolParamType.STRING!r}")

    class _PingCommand:
        command_pattern = "^/ping"
        command_description = "示例命令"

        async def execute(self) -> str:
            return "pong"

    class _PreProcessProbe:
        """旧版 EventHandler 的规范写法：**类属性**声明（v1 的 `get_handler_info()` 就是抄这些）。"""

        event_type = "on_message_pre_process"
        handler_name = "pre_process_probe"
        handler_description = "示例事件处理器"
        weight = 5
        intercept_message = True

        async def execute(self, message: Any) -> tuple:
            return True, True, None, None, None

    @register_plugin
    class BridgePlugin(BasePlugin):
        plugin_name = "bridge-demo"

        def get_plugin_components(self):
            return [
                (
                    CommandInfo(
                        name="ping",
                        component_type=ComponentType.COMMAND,
                        description="示例命令",
                        command_pattern="^/ping",
                    ),
                    _PingCommand,
                ),
                (
                    # ⭐ 第 2 片新增的一类。event_type / weight / intercept_message **不在这里给**
                    #   ⇒ 只能从**实例的类属性**上取（老插件的规范写法；"实例优先、没写才回落到
                    #   描述对象"这条约定因此被真走过）
                    EventHandlerInfo(
                        name="pre_process_probe",
                        component_type=ComponentType.EVENT_HANDLER,
                        description="示例事件处理器",
                    ),
                    _PreProcessProbe,
                ),
            ]

    adapter = legacy_face.LocalLegacyPluginAdapter(BridgePlugin())
    declarations = adapter.get_components()
    print(f"  adapter.get_components() ⇒ {declarations}")
    assert isinstance(declarations, list) and len(declarations) == 2, declarations
    first, second = declarations
    assert first["name"] == "ping", first
    assert first["type"] == "COMMAND", first
    assert first["metadata"]["command_pattern"] == "^/ping", first
    assert first["metadata"]["legacy"] is True, first
    assert isinstance(first["metadata"], dict), first
    # ⭐ 第 2 片：EventHandler 的 metadata 按**v2 读侧真读的键**给（`EventHandlerEntry` 逐字取那三个）
    print(f"  [本片新增] EventHandler 声明 ⇒ {second}")
    assert second["name"] == "pre_process_probe", second
    assert second["type"] == "EVENT_HANDLER", second
    assert second["metadata"]["event_type"] == "on_message_pre_process", second
    assert second["metadata"]["weight"] == 5, second
    assert second["metadata"]["intercept_message"] is True, second
    assert second["metadata"]["handler_name"] == "pre_process_probe", second
    assert second["metadata"]["legacy"] is True, second

    # ⭐ EventHandler 的**其余形态**（四种，都翻得动；口径见 `_v1_event_type_value` / `_from_instance_then_info`）：
    #   ① 值只在**描述对象**上（类属性没写）· ② `event_type` 是**枚举**（上游 compat 在场时那种）
    #   ③ `event_type` 是**大写串**（两面都不归一 ⇒ 行为与上游一致，见回执覆盖边界）
    #   ④ **实例优先**：实例上写着 `False` / `0`，描述对象上写着 `True` / `9` ⇒ 必须听实例的
    class _EnumLike(Enum):
        """本地面里 `EventType` 仍是 stub ⇒ 造一个"类 EventType"来走枚举这一支。"""

        ON_MESSAGE_PRE_PROCESS = "on_message_pre_process"

    class _BareHandler:
        async def execute(self, message: Any) -> tuple:
            return True, True, None, None, None

    class _InstanceWins:
        event_type = "on_start"
        intercept_message = False
        weight = 0

        async def execute(self, message: Any) -> tuple:
            return True, True, None, None, None

    @register_plugin
    class VariantPlugin(BasePlugin):
        plugin_name = "bridge-variant"

        def get_plugin_components(self):
            return [
                (
                    EventHandlerInfo(
                        name="from_info",
                        component_type=ComponentType.EVENT_HANDLER,
                        event_type=_EnumLike.ON_MESSAGE_PRE_PROCESS,
                        weight=0,
                        intercept_message=False,
                    ),
                    _BareHandler,
                ),
                (
                    EventHandlerInfo(
                        name="upper_case",
                        component_type=ComponentType.EVENT_HANDLER,
                        event_type="ON_MESSAGE_PRE_PROCESS",
                    ),
                    _BareHandler,
                ),
                (
                    EventHandlerInfo(name="no_event_type", component_type=ComponentType.EVENT_HANDLER),
                    _BareHandler,
                ),
                (
                    EventHandlerInfo(
                        name="instance_wins",
                        component_type=ComponentType.EVENT_HANDLER,
                        event_type="never_used",
                        weight=9,
                        intercept_message=True,
                    ),
                    _InstanceWins,
                ),
            ]

    variants = {item["name"]: item["metadata"] for item in legacy_face.LocalLegacyPluginAdapter(VariantPlugin()).get_components()}
    print(
        f"  [形态自证] 描述对象上的枚举 ⇒ {variants['from_info']['event_type']!r} · "
        f"大写串 ⇒ {variants['upper_case']['event_type']!r} · "
        f"没写 ⇒ {variants['no_event_type']['event_type']!r} · "
        f"实例优先 ⇒ {variants['instance_wins']['event_type']!r}/"
        f"{variants['instance_wins']['intercept_message']!r}/{variants['instance_wins']['weight']!r}"
    )
    assert variants["from_info"]["event_type"] == "on_message_pre_process", variants
    assert variants["from_info"]["intercept_message"] is False, variants  # False 是"写得明明白白"，不是"没写"
    assert variants["upper_case"]["event_type"] == "ON_MESSAGE_PRE_PROCESS", variants  # 两面都不归一
    assert variants["no_event_type"]["event_type"] == "on_message", variants  # v1 基类默认
    assert variants["instance_wins"]["event_type"] == "on_start", variants
    assert variants["instance_wins"]["intercept_message"] is False, variants
    assert variants["instance_wins"]["weight"] == 0, variants


def item3_unmappable_is_named() -> None:
    """件③（丙）：**翻不动 ⇒ 具名出声**（⛔ 不许静默给空）——两条都验。"""

    _line("件③(丙) 翻不动 ⇒ 具名出声（LocalFaceGapError；宿主侧兜底另立一件）")
    legacy_face.install_local_face(force=True)

    from src.plugin_system import BasePlugin, ComponentType, ToolInfo  # noqa: PLC0415

    class _NotWired:
        pass

    class _ToolOnly(BasePlugin):
        plugin_name = "tool-only"

        def get_plugin_components(self):
            return [
                (
                    # ⭐ 本片补上 `action` 之后，**未立项**的反向对照换成 `tool`（tool 仍未立项）
                    ToolInfo(name="lookup", component_type=ComponentType.TOOL, tool_description="示例工具"),
                    _NotWired,
                )
            ]

    adapter = legacy_face.LocalLegacyPluginAdapter(_ToolOnly())
    try:
        adapter.get_components()
    except legacy_face.LocalFaceGapError as exc:
        print(f"  [未立项的类别] 出声 ✓：{exc}")
    else:
        raise AssertionError("未立项的组件类别却**没有**出声 —— 那就是'加载成功、组件却没注册'的假绿")
    # ⚠️ 这里**不能**用 `BasePlugin` 子类：本地面基类自带 `get_plugin_components()`（返回 `[]`）
    #    ⇒ 永远走不到"没有 provider"这一支。用一个**裸对象**才测得到。
    class _NoProvider:
        pass

    adapter = legacy_face.LocalLegacyPluginAdapter(_NoProvider())
    try:
        adapter.get_components()
    except legacy_face.LocalFaceGapError as exc:
        print(f"  [没有 get_plugin_components] 出声 ✓：{exc}")
    else:
        raise AssertionError("旧实例连 get_plugin_components 都没有，却静默给了空")

    # 载体被抹掉 ⇒ 组件栏那一栏必须红（否则"0 条"与"全在"不可分辨）
    keep = {name: legacy_face._IMPLEMENTED.pop(name) for name in legacy_face._COMPONENT_CARRIERS}
    try:
        try:
            legacy_face.assert_nonempty()
        except legacy_face.LocalFaceGapError as exc:
            print(f"  [组件栏的反向自检] 载体清空 ⇒ 直接红 ✓：{exc}")
        else:
            raise AssertionError("组件描述载体全没了却没红 ⇒ 组件栏是恒真的")
    finally:
        legacy_face._IMPLEMENTED.update(keep)

    # [对照] 没有组件的插件仍返回空列表（**空清单合法**；空是"插件真没组件"，不是"翻不动被吞掉"）
    class _NoComponents(BasePlugin):
        plugin_name = "no-components"

        def get_plugin_components(self):
            return []

    print(f"  [对照] 无组件的插件仍返回空列表：{legacy_face.LocalLegacyPluginAdapter(_NoComponents()).get_components()}")
    print(f"  [对照] 'command' 在已立项名单里：{'command' in legacy_face._V1_TO_V2_COMPONENT_TYPE}")
    print(f"  [对照] CommandInfo 在 _IMPLEMENTED 里（即已移出 stub 名单）：{'CommandInfo' in legacy_face._IMPLEMENTED}")


def item2_log_level_passthrough() -> None:
    """件②：runner 的 warning 经宿主转发**仍可在 warning 视图里看到**；info 不被抬级。"""

    _line("件② 转发不降级")
    spec = importlib.util.spec_from_file_location("v1_compat_bridge", PLUGIN_DIR / "compat_bridge.py")
    assert spec and spec.loader
    bridge = importlib.util.module_from_spec(spec)
    sys.modules["v1_compat_bridge"] = bridge
    spec.loader.exec_module(bridge)
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    target = logging.getLogger("plugin_runtime.v1_compat.bridge")
    target.addHandler(handler)
    target.setLevel(logging.DEBUG)
    try:
        forwarded = bridge._forward_runner_log  # 新增的纯函数（见 compat_bridge）
        forwarded({"level": "warning", "message": "合成警告：兼容层缺席"})
        forwarded({"level": "info", "message": "合成通知：一切正常"})
    finally:
        target.removeHandler(handler)

    by_level = [(record.levelname, record.getMessage()) for record in records]
    print(f"  转发后宿主侧记录: {by_level}")
    assert any(level == "WARNING" and "合成警告" in msg for level, msg in by_level), "warning 被降级了"
    assert any(level == "INFO" and "合成通知" in msg for level, msg in by_level), "info 不见了"
    assert not any(level == "WARNING" and "合成通知" in msg for level, msg in by_level), "info 被抬级了"
    print("  ⇒ '只看 warning 及以上'的视图里能看到那条合成警告 ✓；info 仍是 info ✓")


if __name__ == "__main__":
    item1_positive()
    item1_nonempty_and_reverse()
    item3_component_bridge()
    item3_unmappable_is_named()
    item2_log_level_passthrough()
    print("\n全部自检通过。")
