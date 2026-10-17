"""组件描述载体「**不是 stub**」的用例：`ActionInfo` / `ToolInfo` **真构造**一次。

为什么"import 成功"证明不了任何事
--------------------------------
本地面的 stub 恰恰是"**能 import、用才抛**"（`_make_stub`：类在清单里、`__init__` 里抛
`LocalFaceGapError`）⇒ `from src.plugin_system import ActionInfo` 这条断言在**修好与没修好**
两种世界里**都通过**。故每条用例都必须：
① **真构造** `cls(name=…, component_type=…)`（老插件在 `get_plugin_components()` 里的第一句）；
② 断言类上**没有** `_local_face_stub` 标记（stub 的身份印记）；
③ 断言 `__post_init__` 真把 `component_type` 立成该类应有的枚举值（"构造得出来、字段是错的"是第二层）。

跑法（宿主仓根目录，用宿主树的 venv）：
    uv run python plugins/maibot-team.v1-compat/tests/verify_component_info_real_construct.py

⚠️ 为什么是**独立脚本**而不是 `test_*.py`（本树实测，写在这里免得下一个人再试一遍）：
本插件目录名带点（`maibot-team.v1-compat`）而它自己又是包（`__init__.py` 里
`from plugins.maibot_team.v1_compat.plugin import …`）⇒ 在仓根跑 pytest 时，pytest 把测试模块
命名成 `plugins.maibot-team.v1-compat.tests.test_…`，**导入父包**那一步就会执行插件的
`__init__.py` 并炸在 `No module named 'plugins.maibot_team'`（`--import-mode=importlib` /
`prepend` 都试过）⇒ 任何 `test_*.py` 落在 `plugins/maibot-team.v1-compat/tests/` 都会让
**全仓 `pytest` 在收集阶段直接红**。与 `tests/e2e_component_bridge_crossprocess.py` 同一条路：
自带断言、自己起环境、不依赖 pytest。
"""

from __future__ import annotations

import dataclasses
import importlib
import sys
from pathlib import Path
from typing import Any

PLUGIN_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_DIR.parents[1]  # 宿主仓根（`src` 在这里）
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(PLUGIN_DIR))

legacy_face = importlib.import_module("legacy_face")

#: `ActionInfo` **自有**字段（不含继承自 `ComponentInfo` 的 7 个）——次序照上游
#: `maibot_sdk/compat/base/component_types.py:96-110`，⛔ 别按"先本地已有的再补"排：
#: 老插件按位置构造时错一位就是**静默错字段**。
ACTION_INFO_OWN_FIELDS = [
    "action_parameters",
    "action_require",
    "associated_types",
    "focus_activation_type",
    "normal_activation_type",
    "activation_type",
    "random_activation_probability",
    "activation_keywords",
    "keyword_case_sensitive",
    "parallel_action",
]


def _install_local_face_only() -> str:
    """合成"上游 compat 缺席" ⇒ 装本地面（本机 venv 装着 SDK，不合成就是假绿）。"""

    sys.modules["maibot_sdk.compat"] = None
    sys.modules["maibot_sdk.compat._import_hook"] = None
    mode = legacy_face.install_local_face(force=True)
    for key in [key for key in sys.modules if key == "src.plugin_system" or key.startswith("src.plugin_system.")]:
        del sys.modules[key]
    return mode


def _case(cls: Any, expected: str) -> None:
    """一条用例：真构造 + 无 stub 标记 + `component_type` 生效（三件事一起才算"不是 stub"）。"""

    module = importlib.import_module("src.plugin_system")
    component_type = module.ComponentType
    name = cls.__name__

    # ① 它是**本地面那份**（否则可能是上游 compat 顶进来的，考不到本件）
    assert cls is legacy_face._IMPLEMENTED[name], f"{name} 不是本地面那份 ⇒ 这条用例考的不是本题"
    # ② stub 的身份印记（stub 类上有 `_local_face_stub = <名字>`）
    assert getattr(cls, "_local_face_stub", None) is None, f"{name} 还是 stub（import 通、用才抛）"

    # ③ **真构造**（老插件在 `get_plugin_components()` 里就这么写）
    info = cls(name="n", description="d", component_type=component_type[expected])
    assert info.name == "n"
    assert info.description == "d"
    assert info.component_type is component_type[expected], (
        f"{name}.component_type 没生效：得到 {info.component_type!r}，期望 {component_type[expected]!r}"
    )
    # ③' `__post_init__` 是权威（与上游一致）：传一个错的也照样立成该类应有的取值
    mismatched = cls(name="n2", component_type=component_type.SCHEDULER)
    assert mismatched.component_type is component_type[expected], f"{name} 的 __post_init__ 没覆盖 component_type"

    print(
        f"  [{expected}] {name}(name='n', description='d') ⇒ 真构造成功 ✓ · "
        f"`_local_face_stub` = {getattr(cls, '_local_face_stub', None)!r} ✓ · "
        f"component_type = {info.component_type!r} ✓（传 SCHEDULER 也被 __post_init__ 覆盖 ✓）"
    )


def action_info_all_ten_fields() -> None:
    """`ActionInfo` 必须**全带**上游那 10 个字段（含 3 个 `*_activation_type`），默认值逐条对齐。"""

    module = importlib.import_module("src.plugin_system")
    action_info = module.ActionInfo
    activation = module.ActionActivationType

    inherited = {f.name for f in dataclasses.fields(module.ComponentInfo)}
    own = [f.name for f in dataclasses.fields(action_info) if f.name not in inherited]
    assert own == ACTION_INFO_OWN_FIELDS, f"ActionInfo 自有字段与上游 component_types.py:96-110 不一致：{own}"
    print(f"  [字段] ActionInfo 自有字段 {len(own)} 个（次序照上游）: {own}")

    info = action_info(name="n", component_type=module.ComponentType.ACTION)
    for field_name in ("focus_activation_type", "normal_activation_type", "activation_type"):
        assert getattr(info, field_name) is activation.ALWAYS, f"{field_name} 默认值不是 ActionActivationType.ALWAYS"
    assert info.random_activation_probability == 0.0
    assert info.activation_keywords == []
    assert info.keyword_case_sensitive is False
    assert info.parallel_action is False
    assert info.action_parameters == {}
    assert info.action_require == []
    assert info.associated_types == []
    print(
        "  [默认值] 三个 *_activation_type = ActionActivationType.ALWAYS ✓ · "
        "random_activation_probability = 0.0 ✓ · activation_keywords = [] ✓ · "
        "keyword_case_sensitive = False ✓ · parallel_action = False ✓"
    )

    # `ActionActivationType` 本身也得是真枚举（它是 `ActionInfo` 三个字段默认值的**前置**：
    # 它还是 stub 时 `ActionInfo.ALWAYS` 在类体求值就崩 ⇒ `import legacy_face` 当场失败）
    assert getattr(activation, "_local_face_stub", None) is None
    assert [member.value for member in activation] == ["never", "always", "random", "keyword"]
    assert str(activation.KEYWORD) == "keyword"
    print(f"  [前置] ActionActivationType 是真枚举 ✓ · 取值 = {[member.value for member in activation]}")


def main() -> None:
    mode = _install_local_face_only()
    print(f"=== 合成上游缺席 ⇒ install_local_face(force=True) ⇒ {mode} ===")
    assert mode in {"local", "local(forced)"}, f"本地面没装上（{mode}）⇒ 下面的用例没考察到本题"

    module = importlib.import_module("src.plugin_system")
    # ⭐ 参数化：老插件的两种描述对象 —— (类, 该类应有的 component_type 成员名)
    for cls, expected in ((module.ActionInfo, "ACTION"), (module.ToolInfo, "TOOL")):
        _case(cls, expected)

    # 另一条 import 路：老插件也写 `from src.plugin_system.base.component_types import …`
    # ⇒ 别名模块必须给**同一批对象**（不是再造一份，更不是 stub）
    alias_module = importlib.import_module("src.plugin_system.base.component_types")
    assert alias_module.ActionInfo is module.ActionInfo
    assert alias_module.ActionActivationType is module.ActionActivationType
    print("  [别名模块] src.plugin_system.base.component_types 给出同一批对象 ✓")

    action_info_all_ten_fields()
    print("\n全部用例通过。")


if __name__ == "__main__":
    main()
