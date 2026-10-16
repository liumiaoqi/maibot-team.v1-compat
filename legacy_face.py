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
    """**非空自证**：抽到 0 条 ⇒ 直接抛（照"抽到 0 条 ⇒ 直接红"那条规矩）。"""

    counts = {
        "aliases": len(ALIASES),
        "names": len(FACE_NAMES),
        "signature_modules": len(SIGNATURE_MODULES),
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
