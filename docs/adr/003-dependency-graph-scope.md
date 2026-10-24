# ADR-003 循环依赖的判定口径：只算运行期模块级导入

状态: accepted
日期: 2026-10-24

## 背景
`proj graph` 报告 3 组循环依赖（`plugin_manager↔kernel`、`sdk/context↔sdk/plugin_base`、
`proofread/dashboard_page↔plugin`），`quality-gate` 因此常驻一条 WARN。逐个查看导入点后发现：

| 循环 | 实际写法 | 运行期影响 |
|---|---|---|
| `plugin_manager → kernel` | 在 `if TYPE_CHECKING:` 里 `from core.kernel import Kernel`，仅用于类型注解 | 无（不执行） |
| `sdk/context → plugin_base` | 同上，仅 `PluginManifest` 类型注解 | 无 |
| `dashboard_page → plugin` | 同上，仅 `ProofreadPlugin` 类型注解；反向是 `plugin` 在方法内延迟导入 `dashboard_page` | 无（调用时才导入） |

也就是说：这些"循环"是**类型注解与延迟导入**造成的静态误报，不是导入期风险。
真正的循环依赖会让模块导入直接失败，而本项目 `src/main.py` 一直能正常启动。

## 备选
### 备选 A：改产品代码，删掉 TYPE_CHECKING 导入与延迟导入
- 优点：工具口径不变，图更"干净"。
- 被否理由：`TYPE_CHECKING` + 延迟导入是 Python 里**打破循环**的标准写法；为了迎合统计口径而删掉它们，
  要么让类型注解退化成字符串注释、失去 IDE 检查，要么让插件在模块层导入自己的子模块（真把风险变成真的）。

### 备选 B：工具按运行期语义统计（选中）
`proj graph` 只把**模块级、非 `TYPE_CHECKING` 分支、非函数体内**的导入算作依赖边。
- 优点：口径与"导入会不会真的炸"一致；`if TYPE_CHECKING: ... else: ...` 的 else 分支仍计入；
  类体导入、`try: import x` 仍计入（它们确实在导入期执行）。
- 被否理由：需要维护 ast 遍历逻辑（本次已实现并自检 6 种写法）。

### 备选 C：忽略这条 WARN
- 优点：零改动。
- 被否理由：掩盖真实循环；一旦将来出现真循环，我们不会被告警。

## 决策
采用备选 B：`tools/proj/proj_core/graph.py::_py_specs` 只统计运行期模块级导入。
- 跳过：`if TYPE_CHECKING:`（含 `typing.TYPE_CHECKING`、布尔组合里出现的）分支；
  函数/`async` 函数体内的导入。
- 保留：模块级导入、类体导入、`try/except` 里的导入、`if TYPE_CHECKING` 的 `else` 分支。

## 后果
- 优点：`proj graph` 现在报 **0 组循环**（123 节点 / 190 内部依赖）；`quality-gate` 的循环 WARN 消失；
  产品代码无需为了统计口径做任何妥协。
- 代价：图不再显示仅用于类型注解的依赖边，`depends_on`/`imported_by` 里看不到它们（属预期）。
- 风险：若有人把真正的运行期导入塞进 `if TYPE_CHECKING:` 里（错误写法）会被漏报 ⇒ 单测/启动冒烟仍能发现
  （`NameError`），并已在 `proj graph` 的文档注释里写明口径。
