# World Simulator Frame

本仓库承载 AI 武侠世界模拟系统的可运行框架。当前阶段只实现 Component Schema 基础，不提前搭建完整应用。

## 当前目录

- `schemas/`：正式 JSON Schema。
- `registry/`：Component 运行语义与版本目录。
- `examples/`：合法、可修正和必须拒绝的样例。
- `src/`：Schema 加载、纠错与校验代码。
- `tools/`：人工运行入口。
- `tests/`：回归测试。
- `docs/`：代码项目自身说明与中文代码说明模板。

权威设计文档保存在 Obsidian 的 `AI_World_Simulator` 目录，不复制进本仓库。

## 中文说明模板

docs/templates 保存中文说明模板。每个模板先提供文件功能、架构位置、输入、运作流程、输出和职责边界，再解释具体函数、变量与字段：

- Python 说明模板使用 .py，通过中文 docstring 和关键代码块注释说明；
- JSON、JSON Schema、Registry 和实例的说明模板使用 .jsonc，通过 // 注释区分说明与真实字段；
- 正式运行文件继续使用 .json，不会读取 .jsonc；
- 不再用 _说明 字段混入 JSON 示例；
- 模板文件名包含 _说明模板，正式实现不得依赖模板文件。

## 本地验证

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\world-schema check-registry
.\.venv\Scripts\world-schema validate --component identity --input examples\repairable\identity.json --repair
```

校验器只自动纠正安全的结构性错误。Entity ID、Type、自由文本、数值和未登记枚举不会通过模糊匹配改写。