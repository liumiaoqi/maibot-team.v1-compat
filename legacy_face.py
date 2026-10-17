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

组件桥（`component-bridge` 那一跳）
----------------------------------
`Base*` 四个基类在 `adapter-hop` 那一跳已实现，但**描述组件用的数据载体**（`ComponentType` +
5 个 `*Info` + `ToolParamType`）当时还在 stub 名单里 ⇒ 老插件在 `get_plugin_components()` 里
"**构造描述对象那一步**"就抛 `LocalFaceGapError` ⇒ 适配器 `get_components()` 自然给不出东西。
这一跳把它们做成**真数据**（字段名/默认值/SDK 语义对齐运行期 `maibot_sdk.compat.base.component_types`），
并让 `LocalLegacyPluginAdapter.get_components()` **真翻**出 v2 dict。
⚠️ 只翻得动**已立项**的那几类（见 `_V1_TO_V2_COMPONENT_TYPE`：现为 `command` / `event_handler`）；
其余类别**具名出声**，⛔ 不静默给空。`metadata` 一律按**该类在 v2 侧真读的键**给（不是把 v1 的字段
原样倒过去）—— 逐类的读侧出处写在 `LocalLegacyPluginAdapter._convert_component` 的 docstring 里。
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.abc
import importlib.machinery
import json
import sys
import types
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List

#: ⛔ 这里**故意不做**"自愈式 `sys.modules` 注册"：`exec_module` 期间模块对象**还没进**
#: `sys.modules`，本文件手上只有自己的 globals、拿不到那个对象 ⇒ 只能造一个**替身**塞进去，
#: 而 dataclasses 恰恰要的是"`cls.__module__` 那个模块里能找到本类"，替身里没有本类
#: ⇒ 换汤不换药（实测过）。根在加载器：见宿主 `plugin_loader` 两处 `exec_module` 前的注册。

_SURFACE_PATH = Path(__file__).with_name("legacy_face_surface.json")


class LocalFaceGapError(RuntimeError):
    """本地面尚未实现的名字/模块被**用到**了（import 通、使用出声）。"""


def _load_surface() -> dict[str, Any]:
    return json.loads(_SURFACE_PATH.read_text(encoding="utf-8"))


SURFACE: dict[str, Any] = _load_surface()
ALIASES: Dict[str, str] = dict(SURFACE["aliases"])
FACE_NAMES: List[str] = list(SURFACE["names"])
SIGNATURE_MODULES: List[str] = list(SURFACE["signature_modules"])


# ── 组件描述载体（老插件 `get_plugin_components()` 里**真会构造**的那些）──────────
#
# ⭐ 为什么要**真数据**而不是 stub：老插件拿组件跟宿主对话的第一句就是"构造描述对象"
#    （`CommandInfo(name="ping", component_type=ComponentType.COMMAND, command_pattern="^/ping")`）
#    ——那一刻若是 stub，抛的是 `LocalFaceGapError`，而症状看起来跟"宿主不支持这类组件"
#    一模一样。
# ⇒ 字段名/默认值/枚举取值与运行期 `maibot_sdk.compat.base.component_types` **对齐**：
#    老插件是按那份签名写的，字段名错一个就是 `TypeError`（"能加载 ≠ 能用"的第二层）。
# ⚠️ `ComponentType.<X>.value` 是**小写**（`"command"`）：v2 侧的 `_normalize_component_type`
#    先 `.upper()` 再查表，故大小写这层由宿主吃掉；本地面按 SDK 原样保留小写。
#
# ⚠️⚠️ **`@dataclass` 需要一个能进 `sys.modules` 的模块名**（CPython 3.14 实测）：
#    宿主按"路径 exec"加载本文件（`spec_from_file_location` + `exec_module`，**不注册
#    `sys.modules`**）时，`@dataclass` 会崩在
#        `AttributeError: 'NoneType' object has no attribute '__dict__'`
#        （dataclasses.py `_is_type()` → `sys.modules.get(cls.__module__)` 返回 None）。
#    ⇒ 配套修在宿主 `plugin_loader`（`_local_face_attr` / `_install_local_legacy_face`：
#      exec 前把模块注册进 `sys.modules`）。**光改本文件绕不过去**（换 `attrs`/手写 `__init__`
#      都能绕，但那是拿"不用标准库"换"藏住加载器的缺陷"——加载器迟早还会 exec 别的 dataclass 模块）。


class ComponentType(Enum):
    """旧版组件类型（取值与 SDK 兼容层一致：小写）。"""

    ACTION = "action"
    COMMAND = "command"
    TOOL = "tool"
    SCHEDULER = "scheduler"
    EVENT_HANDLER = "event_handler"

    def __str__(self) -> str:
        return self.value


class ActionActivationType(Enum):
    """旧版 Action 激活类型（取值与 SDK 兼容层一致：小写）。

    ⭐ 为什么它**必须先于 `ActionInfo`** 落到本地面（前置核对结论，不是顺手搬）：
    `ActionInfo` 的三个 `*_activation_type` 字段默认值写的就是 `ActionActivationType.ALWAYS`
    —— 那是**类体求值**、不是"用到才求"，若本类还是 stub（`_make_stub` 出来的类没有
    `ALWAYS` 这个属性）⇒ `AttributeError` 发生在 `import legacy_face` 那一刻 ⇒ 整个本地面
    **装不上**（症状是"老插件又变成 `No module named 'src.plugin_system'`"，与"兜底面没写"
    长得一模一样）。故它与 `ComponentType` / `ToolParamType` 同级：先真枚举、再谈字段。

    取值/`__str__` 与上游 `maibot_sdk/compat/base/component_types.py:25-32` 逐字对齐。
    """

    NEVER = "never"
    ALWAYS = "always"
    RANDOM = "random"
    KEYWORD = "keyword"

    def __str__(self) -> str:
        return self.value


class ToolParamType(Enum):
    """旧版 Tool 参数类型（v2 侧改由 JSON-schema 表示 ⇒ 见"语义上真不同"第 ② 条）。"""

    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"

    def __str__(self) -> str:
        return self.value


@dataclasses.dataclass
class ComponentInfo:
    """旧版组件描述基类（**身份载体**：v2 那边组件身份是 dict 的 `type` 字段）。

    ⚠️ `component_type` 是**必填参数**（与运行期 `maibot_sdk.compat` 那份**逐字对齐**；
    也与旧版原样一致——老源码 `src/plugin_system/base/component_types.py` 在本仓历史
    `bb497c1a3` 可查，其工厂 `BaseCommand.get_command_info()` / `BaseAction.get_action_info()`
    都是**显式传** `component_type=ComponentType.COMMAND` 的）。
    ⛔ 本地面**故意不给默认值**：加默认值会让"本地面能构造、上游 compat 构造不了"——两套面
    行为不一致比缺失更坏（同一个老插件在两套面下会有两种命运）。
    """

    name: str
    component_type: ComponentType
    description: str = ""
    enabled: bool = True
    plugin_name: str = ""
    is_built_in: bool = False
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        """旧版原样：`metadata=None` 兜成 `{}`（老插件确实会显式传 None）。"""

        if self.metadata is None:
            self.metadata = {}


@dataclasses.dataclass
class ActionInfo(ComponentInfo):
    """旧版 Action 描述（字段/默认值/次序照上游 `component_types.py:96-110` **全带 10 个**）。

    ⚠️ 激活面**这一半现在真落了**：三个 `*_activation_type` 的默认值指向上面的
    `ActionActivationType`（故本类要求它先实现，见那个类的 docstring）。
    ⚠️ 仍**不在这里**的是 `ChatMode`（它不在 `ActionInfo` 的字段里，`ActionInfo` 不需要它）。
    ⚠️ 字段**次序照上游**（不是"先本地已有的四个再补"）：老插件按位置构造时
    （`ActionInfo("n", ComponentType.ACTION, "d", True, "p", False, {}, [], [], …)`）
    次序错一位就是**静默错字段**，比 `TypeError` 更难查。
    """

    action_parameters: Dict[str, str] = dataclasses.field(default_factory=dict)
    action_require: List[str] = dataclasses.field(default_factory=list)
    associated_types: List[str] = dataclasses.field(default_factory=list)
    focus_activation_type: ActionActivationType = ActionActivationType.ALWAYS
    normal_activation_type: ActionActivationType = ActionActivationType.ALWAYS
    activation_type: ActionActivationType = ActionActivationType.ALWAYS
    random_activation_probability: float = 0.0
    activation_keywords: List[str] = dataclasses.field(default_factory=list)
    keyword_case_sensitive: bool = False
    parallel_action: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        self.component_type = ComponentType.ACTION


@dataclasses.dataclass
class CommandInfo(ComponentInfo):
    """旧版 Command 描述（⭐ 本轮**唯一**翻得动的那一类）。"""

    command_pattern: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.component_type = ComponentType.COMMAND


@dataclasses.dataclass
class ToolInfo(ComponentInfo):
    """旧版 Tool 描述（参数是**元组表**；v2 要 JSON-schema ⇒ 见"语义上真不同"第 ② 条）。"""

    tool_parameters: List[Any] = dataclasses.field(default_factory=list)
    tool_description: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.component_type = ComponentType.TOOL


@dataclasses.dataclass
class EventHandlerInfo(ComponentInfo):
    """旧版 EventHandler 描述（⭐ 第 2 片立项：`event_handler` → `EVENT_HANDLER`）。

    ⚠️ `event_type` 的默认值是**字符串** `"on_message"`，上游 SDK 那份是枚举 `EventType.ON_MESSAGE`
    —— 两者取值相同（`str(EventType.ON_MESSAGE) == "on_message"`）；本地面里 `EventType` **仍是 stub**
    （老插件写 `event_type = EventType.ON_MESSAGE` 会在原地看到缺口），故默认值用字符串。
    转换时一律取 `.value`（见 `_v1_event_type_value`），两种形态都翻得动。
    """

    event_type: Any = "on_message"
    intercept_message: bool = False
    weight: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        self.component_type = ComponentType.EVENT_HANDLER


#: ⭐ v1 组件类型（**小写取值**）→ v2 声明 `type`（**大写**，对齐宿主 `ComponentTypes`）。
#: 只列**已立项**的：别的一律由 `_convert_component` 具名出声（⛔ 不静默给空）。
#: 逐类放行（放行之前先读**v2 侧真读哪些键**，不是把 v1 的字段原样倒过去）：
#: * `command`       —— `component-bridge` 第 1 片；读侧 `host/component_registry.py:155-161`；
#: * `event_handler` —— 第 2 片（本片）；读侧 `:257-259`（`EventHandlerEntry.__init__` 逐字取
#:   `event_type` / `weight` / `intercept_message`）+ `:1092`（按 `event_type` 比）+ `:1094`（按 `weight` 排）。
_V1_TO_V2_COMPONENT_TYPE: Dict[str, str] = {
    "command": "COMMAND",
    "event_handler": "EVENT_HANDLER",
}

#: ⭐ 已立项映射的**内容层**自证（判据 (丁) 的第二层；第一层是"条数 > 0"，见 `assert_nonempty`）。
#: 为什么光数条数不够：`len(...)` 从 2 掉回 1，与"这一类还没立项"**不可分辨**；
#: 而 v1 的 `ComponentType` 取值域是**闭集** ⇒ 每一类只有两种合法状态：**在表里且值对得上** v2 的
#: `ComponentTypes`，或者**明说没立项**（由 `_convert_component` 具名出声）。
#: ⚠️ 加一类就往这里加一条：这条断言管的是"**放过的那一类不会悄悄退回**"。
_REQUIRED_MAPPINGS: Dict[str, str] = {
    "command": "COMMAND",
    "event_handler": "EVENT_HANDLER",
}


def _from_instance_then_info(instance: Any, comp_info: Any, attr: str, default: Any) -> Any:
    """取字段值：**实例（类属性）优先，没写才回落到描述对象**（两边都没有 ⇒ `default`）。

    ⚠️ "没写"按 `None` 判，**不按真值判**：`intercept_message = False` / `weight = 0` 是**写得明明白白
    的取值**；按真值判会把它们当成"没写"、被描述对象里的值盖掉 —— 那就是"插件说了不算"。

    与上一片 `command_pattern` 同一条口径（老插件两种写法都有：类属性声明 / 直接构造 `*Info`）。
    ⚠️ 上游 `maibot_sdk.compat.legacy_adapter._convert_component` **只认实例**（`:264-276`）——
    本地面多这一层回落是**有意的**：v1 的工厂 `BaseEventHandler.get_handler_info()` 就是把类属性抄进
    `*Info`，两种写法在老插件里都出现；两条路都给值时结果相同（故与上游行为一致）。
    """

    value = getattr(instance, attr, None)
    if value is None:
        value = getattr(comp_info, attr, None)
    return default if value is None else value


def _v1_event_type_value(raw: Any) -> str:
    """v1 的 `event_type` → **v2 的取值域**（小写字符串，`EventType` 那套）。

    * 两种形态都有：枚举（上游 compat 在场时是 `EventType.ON_MESSAGE`）/ 裸串 `"on_message"`
      ⇒ 一律取 `.value`。⚠️ 比上游那行 `str(getattr(instance, "event_type", "on_message"))` 稳：
      上游靠 v1 枚举自带 `__str__ = value`，**件外的普通 `Enum`** 会被 `str()` 成
      `'EventType.ON_MESSAGE'` 这种垃圾串（注册得上、永不触发）。
    * 空串按**没写**处理（v1 基类默认就是 `EventType.ON_MESSAGE`）⇒ 回落 `"on_message"`：
      宁可落到 v1 的默认值上，也不落一个"注册成功、永不触发"的空值。
      （⚠️ 唯一一处与上游的**有意**差异：上游会把空串原样放进 metadata。写进回执的覆盖边界。）
    * 本函数**不做**大小写归一：宿主 `component_registry.py:1092` 是 `comp.event_type == event_type`
      **逐字**比（不像 `component_type` 那层有 `_normalize_component_type` 的 `.upper()`），故口径
      与上游 compat 保持一致（都不归一）——`"ON_MESSAGE"` 这种写法两面都一样"注册得上、永不触发"。
    """

    if raw is None:
        return "on_message"
    text = str(getattr(raw, "value", raw)).strip()
    return text or "on_message"


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
      `name`/`type`/`metadata` 建 `ComponentDeclaration`）—— `component-bridge` 那一跳起**真翻**
      已立项的组件类别（见本方法 docstring），⛔ 翻不动就具名出声
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
        #: v1 组件名 → (描述对象, 实例)——`invoke_component` 将来要按名字找实例。
        self._components: Dict[str, Any] = {}

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
        """把旧实例的 `get_plugin_components()` 翻成 **Runner 要的 v2 dict**（真翻，不是空集）。

        形状（与 `runner_main.py:1377-1404` 的读法逐条对齐）：
        `{"name": str, "type": str, "description": str, "metadata": dict}`
        —— `type` 是**大写**（宿主 `ComponentTypes` 的取值域），`metadata` 进
        `ComponentDeclaration.metadata`。

        ⭐ 与 `adapter-hop` 那一版的关键差别：那一版返回**空集**（"宿主看得见插件、看不见组件"），
        理由是"转换逻辑在上游 `_convert_component`、本件没搬"；本跳把**已立项的那几类**
        （`CommandInfo` → `COMMAND`；`EventHandlerInfo` → `EVENT_HANDLER`，见 `_V1_TO_V2_COMPONENT_TYPE`）
        真搬过来 ⇒ 老插件的**命令类 / 事件处理器组件**从此真能被注册。
        ⚠️ 注册得上 ≠ 调得动：调用路由（`metadata["handler_name"]` → 实例上那个可调用）仍是**下一跳**，
        本件只做**声明面**（与上一片同边界）。

        ⛔ **翻不动的类别不许静默跳过**（具名出声）：
        上游那一版对未知类型只 `logger.warning` + `return None`（组件凭空消失）；本地面照
        "缺口出声"的规矩**直接抛**，⛔ 不返回空、不 `continue`（那才是"加载成功、组件却没注册"）。

        ## ⚠️ 实测：这条"具名"落在**跨进程边界之前**（第三个缺口，需宿主侧修）
        本方法是在**宿主自己的激活链**里被调的（`runner_main._register_plugin:1380`），而那一跳
        **没有 try**（上游 `_activate_plugin:1636` 没有，`run():461` 也没有）⇒ 抛出去会一路逃到
        `runner_main.main()`、**把 runner 子进程打挂**：
        * 症状：宿主侧 `plugin.register_components` 那次 RPC **根本没发生**；
          `runner.ready` 载荷里 `failed_plugins` / `failed_plugin_reasons` **两栏都空**
          ——正是要避免的"插件凭空消失"，只是这次它**写在 stderr 里**（具名，没被吞）。
        * 实测过"换成普通 `RuntimeError`"**没有用**（同一条链上没有 `except`），所以本件不为此
          多铺异常类型；**真要落地"宿主 failed_plugin_reasons 里看得见"，必须宿主侧在那一跳
          加兜（把 `get_components()` 的异常收成具名失败）** —— 与上游 `LegacyPluginAdapter`
          同一处（它现在是静默 `return None`，症状更坏）。
        * 本地面这一侧**保持抛**：宁可 runner 响、也不静默退化成空集。

        Raises:
            LocalFaceGapError: 旧实例没给出 `get_plugin_components()`、旧插件自己抛错、某个组件的
                类型尚未立项、或组件缺 `name` / 实例化失败。**原因一律具名**（点名组件名 + 类型）。
        """

        return self._collect_declarations()

    def _collect_declarations(self) -> list[dict[str, Any]]:
        """`get_components()` 的正体。"""

        provider = getattr(self._legacy, "get_plugin_components", None)
        if provider is None:
            raise LocalFaceGapError(
                f"旧实例 {type(self._legacy).__name__} 没有 get_plugin_components()"
                "——本地面无法知道它有哪些组件（缺口出声，不静默给空）"
            )

        try:
            entries = list(provider())
        except Exception as exc:  # noqa: BLE001 - 旧插件自己抛的（例如 stub 描述载体）⇒ 具名转给宿主
            raise LocalFaceGapError(
                f"旧实例 {type(self._legacy).__name__}.get_plugin_components() 抛错"
                f"（{type(exc).__name__}: {exc}）"
            ) from exc

        declarations: list[dict[str, Any]] = []
        for entry in entries:
            comp_info, comp_cls = self._unpack_component_entry(entry)
            if not bool(getattr(comp_info, "enabled", True)):
                continue
            name = str(getattr(comp_info, "name", "") or "").strip()
            if not name:
                raise LocalFaceGapError(f"组件缺 name（描述对象 {type(comp_info).__name__}）⇒ 无法注册（不静默跳过）")
            try:
                instance = comp_cls()
            except Exception as exc:  # noqa: BLE001 - 实例化失败也要**具名**（不许静默丢组件）
                raise LocalFaceGapError(f"组件 {name} 实例化失败（{type(comp_cls).__name__}: {exc}）") from exc
            self._components[name] = (comp_info, instance)
            declarations.append(self._convert_component(comp_info, instance))
        return declarations

    @staticmethod
    def _unpack_component_entry(entry: Any) -> tuple[Any, Any]:
        """把 `get_plugin_components()` 的一项拆成 `(描述对象, 组件类)`。

        旧版契约是 `list[tuple[ComponentInfo, type]]`；形状不对就具名出声
        （⛔ 不 `continue` 跳过——"跳过"就是组件凭空消失）。
        """

        if isinstance(entry, (tuple, list)) and len(entry) == 2:
            return entry[0], entry[1]
        raise LocalFaceGapError(
            f"get_plugin_components() 的返回项形状不对：期望 (ComponentInfo, type)，实得 {type(entry).__name__}"
        )

    @staticmethod
    def _convert_component(comp_info: Any, instance: Any) -> dict[str, Any]:
        """单个 v1 描述对象 → v2 声明 dict（**只认已立项的类型**）。

        `metadata` **不是**把 v1 的字段原样倒过去，而是按**该类在 v2 侧真读的键**给
        （读侧出处逐类列在下面；改这里之前先去读那一处）：

        * `COMMAND` ⇒ `command_pattern` —— 读侧 `host/component_registry.py:155-161`
          （`CommandEntry`：`metadata.get("aliases", [])` + `re.compile(metadata["command_pattern"])`）。
        * `EVENT_HANDLER` ⇒ `event_type` / `weight` / `intercept_message` —— 读侧 `:257-259`
          （`EventHandlerEntry.__init__` 逐字取这三个键）+ `:1092`（`get_event_handlers` 按
          `event_type` 比）+ `:1094`（按 `weight` 降序排）。
        * 两类共有 `handler_name` —— 读侧 `runner/runner_main.py:1389`：拿它建
          `meta.component_handlers` 映射；缺了会**静默回落**成组件名（故这里显式给组件名，
          与上游 compat 不给该键**等效**）。`legacy=True` 是本地面身份标记。

        取值口径：**实例（类属性）优先，没写才回落到描述对象** —— 见 `_from_instance_then_info`。

        Raises:
            LocalFaceGapError: 类型未立项、已立项但字段翻不成 v2 声明（**一律具名**，不静默跳过）。
        """

        raw_type = str(getattr(getattr(comp_info, "component_type", ""), "value", "") or "").strip().lower()
        component_type = _V1_TO_V2_COMPONENT_TYPE.get(raw_type)
        if component_type is None:
            raise LocalFaceGapError(
                f"组件 {getattr(comp_info, 'name', '?')} 的类型 {raw_type or '?'!r} 尚未立项"
                f"（本地面已立项：{sorted(_V1_TO_V2_COMPONENT_TYPE)}）"
                " ⇒ 不静默跳过（否则会得到'加载成功、组件却没注册'的假绿）"
            )

        name = str(getattr(comp_info, "name", "") or "").strip()
        description = str(getattr(comp_info, "description", "") or "").strip()
        try:
            if raw_type == "command":
                metadata: Dict[str, Any] = {
                    "command_pattern": str(
                        _from_instance_then_info(instance, comp_info, "command_pattern", "") or ""
                    ),
                }
                description = description or str(getattr(instance, "command_description", "") or "")
            elif raw_type == "event_handler":
                metadata = {
                    "event_type": _v1_event_type_value(
                        _from_instance_then_info(instance, comp_info, "event_type", None)
                    ),
                    "intercept_message": bool(
                        _from_instance_then_info(instance, comp_info, "intercept_message", False)
                    ),
                    # 收成 int：v2 那侧要拿它 `sort(key=..., reverse=True)`（`component_registry.py:1094`）
                    # ⇒ 非数会**在派发时才炸**（离现场很远）；这里当场具名。
                    "weight": int(_from_instance_then_info(instance, comp_info, "weight", 0)),
                }
                description = description or str(getattr(instance, "handler_description", "") or "")
            else:  # 表里已立项但这里没有分支 ⇒ 具名（加类时漏改这里，不该静默走成别的类）
                raise LocalFaceGapError(
                    f"组件 {name or '?'} 的类型 {raw_type!r} 已在 `_V1_TO_V2_COMPONENT_TYPE` 里立项，"
                    "但 `_convert_component` 没有它的分支（加类时漏了这一步）⇒ 不静默跳过"
                )
        except (TypeError, ValueError) as exc:
            raise LocalFaceGapError(
                f"组件 {name or '?'}（{raw_type}）的字段翻不成 v2 声明（{type(exc).__name__}: {exc}）"
                " ⇒ 不静默跳过（否则会得到'注册成功、字段却是空的'假绿）"
            ) from exc

        metadata["handler_name"] = name
        metadata["legacy"] = True
        return {
            "name": name,
            "type": component_type,
            "description": description,
            "metadata": metadata,
        }

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
    # ── 组件描述载体（`component-bridge` 那一跳：由 stub 升为真数据）──────────────
    "ComponentType": ComponentType,
    "ActionActivationType": ActionActivationType,
    "ComponentInfo": ComponentInfo,
    "ActionInfo": ActionInfo,
    "CommandInfo": CommandInfo,
    "ToolInfo": ToolInfo,
    "EventHandlerInfo": EventHandlerInfo,
    "ToolParamType": ToolParamType,
    "register_plugin": register_plugin,
    "get_logger": _log_event,
    "PROJECT_ROOT": str(Path(__file__).resolve().parent),
    "CONFIG_DIR": "config",
    "BOT_CONFIG_PATH": "config/bot_config.toml",
    "MODEL_CONFIG_PATH": "config/model_config.toml",
    "PLUGINS_DIR": "plugins",
    "INTERNAL_PLUGINS_DIR": "plugins",
}

#: ⭐ `assert_nonempty` 的**组件栏**：这一跳**已实现**的描述载体（判据 (丁)：0 条 ⇒ 直接红）。
#: 为什么单列一栏：`names` 那一栏是"面上有多少个名字"，**不区分真实现与 stub** ⇒ 老插件
#: "构造描述对象那一步"全体抛错时，`names`/`aliases` 两栏照样是满的（看着全绿、其实全瞎）。
#: ⚠️ 只列**真落地的 8 个**：`ChatMode` 仍是 stub，**不进这一栏**
#: —— 本栏的判据是"实现没实现"，把它算进来就是**恒假**的红（第一版就这么错过一次：
#: `assert_nonempty()` 每次都抛 ⇒ 兜底面整块装不上 ⇒ 现场症状是"老插件又变成
#: `No module named 'src.plugin_system'`"，与"兜底面没写"长得一模一样）。
#: ⚠️ `ActionActivationType` 是**第 3 片（ActionInfo 全带 10 个字段）**才进来的：`ActionInfo`
#: 的字段默认值指它 ⇒ 它不真，`ActionInfo` 连类体都过不去（见那个类的 docstring）。
_COMPONENT_CARRIERS: tuple[str, ...] = (
    "ComponentType",
    "ActionActivationType",
    "ComponentInfo",
    "ActionInfo",
    "CommandInfo",
    "ToolInfo",
    "EventHandlerInfo",
    "ToolParamType",
)

#: ⭐ `assert_nonempty` 的**组件描述对象栏**（第 3 片新增）：`*Info` 那四个类（不含基类 `ComponentInfo`）。
#: 为什么另立一栏：`components` 那一栏把**枚举**（`ComponentType` / `ActionActivationType` /
#: `ToolParamType`）与**数据载体**混在一起数 ⇒ 少搬**一个 `*Info`** 时那个数字只从 8 掉到 7，
#: 与"某一类还没立项"**不可分辨**。老插件构造组件的第一句就是 `XxxInfo(...)` ⇒ "少一个"必须是
#: **直接红**，不能只靠人盯打印出来的数字（故下面有 `missing_infos` 那条具名断言）。
#: ⚠️ 计数一律**从 `_IMPLEMENTED` 里数**（`counts["component_infos"]`），⛔ 不写死 4：
#: 写死的数字在"少搬一个"时照样是 4，自证就变成了一句谎。
_COMPONENT_INFOS: tuple[str, ...] = (
    "ActionInfo",
    "ToolInfo",
    "CommandInfo",
    "EventHandlerInfo",
)


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
    # ⭐ 必须挂 `__path__`：否则 `import src.plugin_system.base.component_types` 会在
    #    "`base` 不是包"这一步就 `ModuleNotFoundError`（**还没轮到**走到名字查表）。
    module.__path__ = []  # type: ignore[attr-defined]
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
        # ⚠️ "是不是包"不能只看顶层：`src.plugin_system.base` / `…apis` 是**中间层**
        #    而 `…base.component_types` 这种子模块导入要求它先被认成包（只认顶层会
        #    得到 `No module named 'src.plugin_system.base.component_types';
        #    'src.plugin_system.base' is not a package`）。别名表里的每一个路径都算包。
        is_package = fullname == "src.plugin_system" or fullname in ALIASES
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

    ⭐ `components` 一栏是 `component-bridge` 这一跳的自证（判据 (丁)）：**组件描述载体**
    （`ComponentType` + 5 个 `*Info` + `ToolParamType`）在 `_IMPLEMENTED` 里抽到 0 个 ⇒ 直接红。
    为什么这条不能靠 `names` 那一栏兜：老插件在 `get_plugin_components()` 里"构造描述对象"
    那一步若全体抛错，`names`/`aliases` 两栏**照样是满的**（stub 也占名字位）⇒ 只数名字
    等于"看着全绿、其实构造不出任何组件"。

    ⭐ 另有一条**内容层**的自证（同在 (丁) 名下）：`_REQUIRED_MAPPINGS` 里每一条都要真的在
    `_V1_TO_V2_COMPONENT_TYPE` 里对上值 —— 条数 > 0 只挡"全空"，挡不住"加过的那一类悄悄退回"。

    ⭐ `component_infos` 一栏（第 3 片）：`*Info` 那四个类的**已实现条数**，值从 `_IMPLEMENTED`
    现数（⛔ 不写死 4）⇒ "少搬一个"时它从 4 掉到 3，"自证跟着实现变，不是写死"。
    ⚠️ 光有那一栏**还不算红**（3 > 0 过得了"非空"这一关）⇒ 配套 `missing_infos` 具名断言：
    四个 `*Info` 缺任何一个都直接抛。
    """

    counts = {
        "aliases": len(ALIASES),
        "names": len(FACE_NAMES),
        "signature_modules": len(SIGNATURE_MODULES),
        "adapter": 1 if LocalLegacyPluginAdapter else 0,
        "components": len([name for name in _COMPONENT_CARRIERS if name in _IMPLEMENTED]),
        #: ⭐ `*Info` 那四个类：**从 `_IMPLEMENTED` 里数**（写死一个 4 就等于"少搬了也照样绿"）。
        "component_infos": sum(1 for name in _COMPONENT_INFOS if name in _IMPLEMENTED),
        "mappable_component_types": len(_V1_TO_V2_COMPONENT_TYPE),
    }
    empty = [key for key, value in counts.items() if value <= 0]
    if empty:
        raise LocalFaceGapError(f"搬运清单为空（{', '.join(empty)}）—— '空'不等于'一致'，本地面可能已瞎")
    missing_carriers = [name for name in _COMPONENT_CARRIERS if name not in _IMPLEMENTED]
    if missing_carriers:
        raise LocalFaceGapError(
            f"组件描述载体缺 {len(missing_carriers)} 个：{missing_carriers}"
            " —— 老插件构造描述对象那一步会当场抛，本地面等于'看着在、其实构造不了组件'"
        )
    missing_infos = [name for name in _COMPONENT_INFOS if name not in _IMPLEMENTED]
    if missing_infos:
        raise LocalFaceGapError(
            f"组件描述对象缺 {len(missing_infos)} 个：{missing_infos}"
            " —— 老插件构造组件的第一句就是 `XxxInfo(...)`，缺一个就等于那一类组件全废"
            "（计数栏此时已从 4 掉下来：自证跟着实现变，不是写死）"
        )
    missing_mappings = [
        f"{v1}→{v2}"
        for v1, v2 in _REQUIRED_MAPPINGS.items()
        if _V1_TO_V2_COMPONENT_TYPE.get(v1) != v2
    ]
    if missing_mappings:
        raise LocalFaceGapError(
            f"已立项的组件类别缺 {len(missing_mappings)} 条映射：{missing_mappings}"
            "（内容层自证：条数 > 0 挡不住'加过的那一类悄悄退回'）—— 具名出声，不静默降级"
        )
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
