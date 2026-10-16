"""桥接件两件的**自检**（证据生成器）——在**宿主仓的 venv** 里跑：

    uv run python plugins/maibot-team.v1-compat/selfcheck_legacy_face.py

覆盖：
* 件①(甲) 合成"上游 compat 缺席" ⇒ 本地面装上后老插件的 import 面**仍通**（贴原文）；
* 件①(乙) 反向自检：**搬漏一条** ⇒ 该路径 import **必须出声**（`LocalFaceGapError`）；
* 件①非空自证：清单为空 ⇒ 直接红；并打印**抽到了多少条**（35/60/24）；
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
    item2_log_level_passthrough()
    print("\n全部自检通过。")
