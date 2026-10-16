"""老插件 import 面的**本地提供者**：把 `src.plugin_system` 面搬进本插件。

为什么
------
上游 `maibot_sdk.compat` 由其自身声明"**将在未来版本中移除**"
（`maibot_sdk/compat/__init__.py:7`）⇒ 老插件的 `from src.plugin_system import …` **不能继续吊在它上面**。
本模块让**本插件自己**提供那一层：上游在时优先用上游（行为不变），上游不在时用本地面。

搬运清单（生成物，勿手改）
--------------------------
`legacy_face_surface.json` —— 由守卫快照 `gen_plugin_compat_surface.py` 产出：
**别名 35 条 · 名字 60 个 · 签名模块 24 个**（子模块 apis/base）。
⭐ 三条口径（与守卫同源）：
* **非空自证**：`assert_nonempty()` 抽到 0 条 ⇒ 直接抛（"空"不等于"一致"）；
* **按形状/清单找，不按变量名找**：别名表来自清单，不靠猜上游内部常量名；
* **缺口出声**：未实现的名字**可 import**（`from src.plugin_system import X` 通），但**用到就抛**
  `LocalFaceGapError` ⇒ 不存在"看着在、其实是空的"。
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

_SURFACE_PATH = Path(__file__).with_name("legacy_face_surface.json")


class LocalFaceGapError(RuntimeError):
    """本地面尚未实现的名字/模块被**用到**了（import 通、使用出声）。"""


def _load_surface() -> dict[str, Any]:
    return json.loads(_SURFACE_PATH.read_text(encoding="utf-8"))


SURFACE: dict[str, Any] = _load_surface()
ALIASES: Dict[str, str] = dict(SURFACE["aliases"])
FACE_NAMES: List[str] = list(SURFACE["names"])
SIGNATURE_MODULES: List[str] = list(SURFACE["signature_modules"])


# ── 已实现的名字（老插件在本仓真会用到的那些）─────────────────────────────────


class _BasePlugin:
    """旧版插件基类（本地实现：只承载旧插件真正依赖的契约）。"""

    # ⭐ 本地面身份标记：上游 compat 缺席时，宿主侧**拿不到** `LegacyBasePlugin` 这个类对象
    # 来做 `issubclass` 比对（此刻 `maibot_sdk.compat` 连子模块都 import 不了）⇒ 宿主改用
    # "基类上带这个标记"来认旧插件子类（`plugin_loader._find_local_face_plugin_class`）。
    # 仍然**不挂命名空间、不冒充上游模块**（路 B 的理由见 `adapter-hop` 回执）。
    _local_legacy_face_base = True

    plugin_name: str = ""
    enable_plugin: bool = True
    dependencies: List[str] = []
    python_dependencies: List[str] = []
    config_file_name: str = "config.toml"
    config_schema: Dict[str, Any] = {}
    config_section_descriptions: Dict[str, Any] = {}

    def get_plugin_components(self) -> list:  # 旧版契约：子类实现
        return []

    async def on_load(self) -> None: ...

    async def on_unload(self) -> None: ...


class _ComponentBase:
    """旧版组件基类（Action/Tool/Command/EventHandler 共用最小面）。"""


class LocalLegacyPluginAdapter:
    """旧版实例 → 新版 Runner 契约的**本地面适配器**（上游 `LegacyPluginAdapter` 缺席时的落点）。

    为什么在这里
    ------------
    老插件被 Runner 接管时，Runner 只认"新 SDK 插件面"（见契约清单）。上游
    `maibot_sdk.compat.legacy_adapter.LegacyPluginAdapter` 提供这一面，但上游 compat
    自述"将在未来版本中移除"、且本仓要能在**上游整个缺席**时仍认得出并加载老插件
    ⇒ 这一层必须有一个本地面实现（这是 `adapter-hop` 那一跳）。

    最小面（按实物核对，不是照抄上游）
    --------------------------------
    宿主侧**真会调用**的只有这些（逐一核对过调用点）：
    * `_set_context(ctx)`      —— `runner_main.py:701`（`hasattr` 探测）/ `:774`
    * `get_components()`       —— `runner_main.py:1378-1380`（R-2 形状：`list[dict]`，由 Runner 读
      `name`/`type`/`metadata` 建 `ComponentDeclaration`）
    * `get_config_reload_subscriptions()` —— `:1408-1409`
    * `on_load()` / `on_unload()` —— `:1567-1571` / `:1589-1593`（**转发给旧实例**）
    * `on_config_update(scope, config_data, version)` —— `:2372-2380`（三参，**存在性被检查**）
    ⛔ 本类**故意不做**的：不 `_sync_config_cache`（那是上游 compat 的 config_api 缓存，
    本地面没有那个 API 层）、不注册全局 context holder（`legacy_face` 只提供 import 面）。
    ⇒ 面小但**每一条都有调用点或存在性检查**，不做"看着在、其实没人调"的空壳。
    """

    def __init__(self, legacy_instance: Any) -> None:
        self._legacy = legacy_instance
        self._ctx: Any = None

    # ── 生命周期：转发给旧实例（旧实例没实现就什么都不做）──────────────

    async def on_load(self) -> None:
        on_load = getattr(self._legacy, "on_load", None)
        if on_load is not None:
            await on_load()

    async def on_unload(self) -> None:
        on_unload = getattr(self._legacy, "on_unload", None)
        if on_unload is not None:
            await on_unload()

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        """把配置热更新转给旧实例（旧实例的同名方法约定收 `(config_data, version)`）。

        上游 `LegacyPluginAdapter.on_config_update` 按签名自适应地决定传几个参数——
        那是为兼容"各种历史签名"的旧插件。本地面按**本仓插件**的实际写法收窄为
        `(config_data, version)`；旧实例若签名不符，异常向上抛（**不静默吞**：宿主
        `:2381` 会把失败回成 error response）。
        """

        handler = getattr(self._legacy, "on_config_update", None)
        if handler is None:
            return
        await handler(config_data, version)

    # ── Runner 契约面 ─────────────────────────────────────────────────

    def _set_context(self, ctx: Any) -> None:
        """由 Runner 注入 `PluginContext`（`runner_main.py:774`）。"""

        self._ctx = ctx

    def get_components(self) -> list[dict[str, Any]]:
        """返回新版组件声明的**空集**——旧版组件的桥接另有一条路。

        为什么是空集而不是转换 `get_plugin_components()`：旧版返回的是
        `list[tuple[ComponentInfo, type]]`，而 Runner 要的是**新 schema 的 dict**
        （`type`/`name`/`description`/`metadata`，见 `runner_main.py:1392-1407`）；
        转换逻辑在上游 `LegacyPluginAdapter._convert_component` 里，**本件没搬**
        （超范围，且搬了就是复刻上游实现）。
        ⇒ 老插件在"上游缺席 + 本地面"这条路上**能被加载、能跑生命周期**，但其旧版组件
        暂不被注册（宿主看得见插件、看不见它的组件）——**这是如实的最小面**，不是静默：
        本模块的 `coverage()`/`assert_nonempty()` 报的是 import 面覆盖，不含组件桥接。
        """

        return []

    def get_config_reload_subscriptions(self) -> list[str]:
        """旧实例未声明订阅 ⇒ 空（不抬级、不猜测）。"""

        raw = getattr(self._legacy, "config_reload_subscriptions", ())
        if isinstance(raw, str) or not isinstance(raw, (list, tuple, set, frozenset)):
            return []
        return sorted({str(item).strip().lower() for item in raw if str(item).strip().lower() in {"bot", "model"}})


def register_plugin(cls: Any = None, **kwargs: Any) -> Any:
    """旧版 `@register_plugin`（无参或带参）——标记类 + 在调用方模块留痕。"""

    def _decorator(klass: type) -> type:
        klass._is_legacy_registered = True  # type: ignore[attr-defined]
        klass._plugin_meta = kwargs  # type: ignore[attr-defined]
        return klass

    return _decorator(cls) if cls is not None else _decorator


def _log_event(_: str) -> None:
    return None


async def _send_unsupported(*_args: Any, **_kwargs: Any) -> bool:
    raise LocalFaceGapError("send_api 的本地实现尚未接上（缺口出声，不静默返回 False）")


_IMPLEMENTED: Dict[str, Any] = {
    "BasePlugin": _BasePlugin,
    "BaseAction": _ComponentBase,
    "BaseTool": _ComponentBase,
    "BaseCommand": _ComponentBase,
    "BaseEventHandler": _ComponentBase,
    "register_plugin": register_plugin,
    "get_logger": _log_event,
    "PROJECT_ROOT": str(Path(__file__).resolve().parent),
    "CONFIG_DIR": "config",
    "BOT_CONFIG_PATH": "config/bot_config.toml",
    "MODEL_CONFIG_PATH": "config/model_config.toml",
    "PLUGINS_DIR": "plugins",
    "INTERNAL_PLUGINS_DIR": "plugins",
}


def _make_stub(name: str) -> Any:
    """未实现的名字：**可 import**，用到就抛（缺口必须出声）。"""

    class _Stub:
        _local_face_stub = name

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise LocalFaceGapError(f"本地面尚未实现 {name}（import 通、使用出声）")

    _Stub.__name__ = name
    return _Stub


def _build_top_level_module() -> types.ModuleType:
    module = types.ModuleType("src.plugin_system")
    stubbed: List[str] = []
    for name in FACE_NAMES:
        if name in _IMPLEMENTED:
            setattr(module, name, _IMPLEMENTED[name])
        elif name.endswith("_api") or name == "get_logger":
            setattr(module, name, _build_api_module(name))
        else:
            setattr(module, name, _make_stub(name))
            stubbed.append(name)
    module.LOCAL_FACE_STUBBED = tuple(stubbed)
    module.LOCAL_FACE_IMPLEMENTED = tuple(name for name in FACE_NAMES if name not in stubbed)
    return module


_API_MODULE_CACHE: Dict[str, types.ModuleType] = {}


def _build_api_module(name: str) -> types.ModuleType:
    """API 模块（**同一名字恒返回同一对象**：老插件两条路径都 import 过同一个东西）。"""

    cached = _API_MODULE_CACHE.get(name)
    if cached is not None:
        return cached
    module = types.ModuleType(f"src.plugin_system.apis.{name}")
    provided = _IMPLEMENTED.get(name)
    if provided is not None:
        setattr(module, name, provided)
    # 未实现的 API 模块：属性访问即出声
    module.__getattr__ = lambda attr, _n=name: _raise_gap(f"{_n}.{attr}")  # type: ignore[attr-defined]
    _API_MODULE_CACHE[name] = module
    return module


def _raise_gap(where: str) -> Any:
    raise LocalFaceGapError(f"本地面尚未实现 {where}（缺口出声，不静默）")


def _build_alias_module(target: str) -> types.ModuleType:
    """把某个别名目标（`maibot_sdk.compat.…`）映射成本地模块对象。"""

    tail = target.split("maibot_sdk.compat", 1)[-1].strip(".")
    if not tail:
        return _build_top_level_module()
    module = types.ModuleType(f"src.plugin_system.{tail.replace('.', '_')}")
    if tail.startswith("apis"):
        parts = tail.split(".")
        api_name = parts[1] if len(parts) > 1 else ""
        if api_name:
            return _build_api_module(api_name)
        for name in FACE_NAMES:
            if name.endswith("_api"):
                setattr(module, name, _build_api_module(name))
        return module
    if tail.startswith("base"):
        for name in FACE_NAMES:
            if name in _IMPLEMENTED:
                setattr(module, name, _IMPLEMENTED[name])
        return module
    module.__getattr__ = lambda attr, _t=tail: _raise_gap(f"{_t}.{attr}")  # type: ignore[attr-defined]
    return module


class _LocalFaceFinder(importlib.abc.MetaPathFinder):
    """把 `src.plugin_system*` 的 import 接到本地面（只接清单里的那些路径）。"""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname != "src.plugin_system" and not fullname.startswith("src.plugin_system."):
            return None
        is_package = fullname == "src.plugin_system"
        spec = importlib.machinery.ModuleSpec(fullname, _LocalFaceLoader(fullname), is_package=is_package)
        return spec


class _LocalFaceLoader(importlib.abc.Loader):
    def __init__(self, fullname: str) -> None:
        self._fullname = fullname

    def create_module(self, spec: Any) -> types.ModuleType:
        if self._fullname in ALIASES:
            return _build_alias_module(ALIASES[self._fullname])
        tail = self._fullname.replace("src.plugin_system", "").strip(".")
        return _build_alias_module(f"maibot_sdk.compat.{tail.replace('.', '.')}" if tail else "maibot_sdk.compat")

    def exec_module(self, module: types.ModuleType) -> None:
        return None


def assert_nonempty() -> Dict[str, int]:
    """**非空自证**：抽到 0 条 ⇒ 直接抛（照"抽到 0 条 ⇒ 直接红"那条规矩）。

    ⭐ `adapter` 一栏是 `adapter-hop` 这一跳的自证：`LocalLegacyPluginAdapter` 抽到 0 个
    ⇒ 直接红 —— 否则"上游缺席时由本地面接住"这条路会在**没人察觉**的情况下退化成
    "面在、adapter 不在"，症状与现在（`failed_plugin_reasons` 非空）长得一模一样。
    """

    counts = {
        "aliases": len(ALIASES),
        "names": len(FACE_NAMES),
        "signature_modules": len(SIGNATURE_MODULES),
        "adapter": 1 if LocalLegacyPluginAdapter else 0,
    }
    empty = [key for key, value in counts.items() if value <= 0]
    if empty:
        raise LocalFaceGapError(f"搬运清单为空（{', '.join(empty)}）—— '空'不等于'一致'，本地面可能已瞎")
    return counts


def upstream_available() -> bool:
    try:
        importlib.import_module("maibot_sdk.compat")
    except Exception:  # noqa: BLE001 - 任何导入失败都算不可用
        return False
    return True


def install_local_face(force: bool = False) -> str:
    """幂等安装本地面。返回 `"upstream"` / `"local"` / `"local(forced)"`。

    默认**上游在时优先上游**（行为不变）；上游不在时才装本地面 —— 除非 `force=True`（自检用）。
    """

    assert_nonempty()
    if not force and upstream_available():
        return "upstream"
    if any(isinstance(finder, _LocalFaceFinder) for finder in sys.meta_path):
        return "local"
    sys.meta_path.insert(0, _LocalFaceFinder())
    for name in [key for key in sys.modules if key == "src.plugin_system" or key.startswith("src.plugin_system.")]:
        del sys.modules[name]
    return "local(forced)" if force else "local"


def coverage() -> Dict[str, int]:
    """本地面覆盖：已实现名字数 / 缺口名字数（缺口**必须被点名**，不许静默）。"""

    module = _build_top_level_module()
    stubbed = getattr(module, "LOCAL_FACE_STUBBED", ())
    return {
        "implemented": len(FACE_NAMES) - len(stubbed),
        "stubbed": len(stubbed),
        "names": len(FACE_NAMES),
    }
