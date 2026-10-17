"""桥接件两件的**自检**（证据生成器）——在**宿主仓的 venv** 里跑：

    uv run python plugins/maibot-team.v1-compat/selfcheck_legacy_face.py

覆盖：
* 件①(甲) 合成"上游 compat 缺席" ⇒ 本地面装上后老插件的 import 面**仍通**（贴原文）；
* 件①(乙) 反向自检：**搬漏一条** ⇒ 该路径 import **必须出声**（`LocalFaceGapError`）；
* 件①非空自证：清单为空 ⇒ 直接红；并打印**抽到了多少条**（35/60/24）+ ⭐ **组件栏**（判据 (丁)）；
* 件③(甲) **组件桥**：描述载体真能构造 + `LocalLegacyPluginAdapter.get_components()` 真翻出 v2 dict；
* 件③(丙) 反向自检：**翻不动的类别 / 没有 get_plugin_components / 载体被抹掉** 三条都必须出声；
* 件②(甲) 合成 runner 侧 `warning` ⇒ 宿主侧**按 level 过滤仍能看到**（贴原文）；
* 件②(乙) 正常 `info` **不被抬级**（否则就是"整体上抬"换可见）。
"""

from __future__ import annotations

import importlib
import importlib.util
import logging  # ruff: ignore[TID251]  自检需要 stdlib Handler 捕获记录，非生产日志入口
import sys
from pathlib import Path

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
        f"已立项可翻的类别 {counts['mappable_component_types']} 类"
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


def item3_component_bridge() -> None:
    """件③（`component-bridge` 那一跳）：描述载体**真能构造** + `get_components()` **真翻**出 v2 dict。"""

    _line("件③ 组件桥：载体可构造 + 真翻 v2 dict")
    sys.modules["maibot_sdk.compat"] = None
    legacy_face.install_local_face(force=True)
    for key in [k for k in sys.modules if k.startswith("src.plugin_system")]:
        del sys.modules[key]

    from src.plugin_system import BasePlugin, CommandInfo, ComponentType, register_plugin  # noqa: PLC0415
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
            ]

    adapter = legacy_face.LocalLegacyPluginAdapter(BridgePlugin())
    declarations = adapter.get_components()
    print(f"  adapter.get_components() ⇒ {declarations}")
    assert isinstance(declarations, list) and len(declarations) == 1, declarations
    first = declarations[0]
    assert first["name"] == "ping", first
    assert first["type"] == "COMMAND", first
    assert first["metadata"]["command_pattern"] == "^/ping", first
    assert first["metadata"]["legacy"] is True, first
    assert isinstance(first["metadata"], dict), first


def item3_unmappable_is_named() -> None:
    """件③（丙）：**翻不动 ⇒ 具名出声**（⛔ 不许静默给空）——两条都验。"""

    _line("件③(丙) 翻不动 ⇒ 具名出声（LocalFaceGapError；宿主侧兜底另立一件）")
    legacy_face.install_local_face(force=True)

    from src.plugin_system import ActionInfo, BasePlugin, ComponentType  # noqa: PLC0415

    class _NotWired:
        pass

    class _ActionOnly(BasePlugin):
        plugin_name = "action-only"

        def get_plugin_components(self):
            return [
                (
                    ActionInfo(name="poke", component_type=ComponentType.ACTION, description="示例动作"),
                    _NotWired,
                )
            ]

    adapter = legacy_face.LocalLegacyPluginAdapter(_ActionOnly())
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
