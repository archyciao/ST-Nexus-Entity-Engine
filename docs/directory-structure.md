# 目录职责

| 目录 | 权威内容 | 不应放入 |
|---|---|---|
| `schemas/core` | 跨 Component 的基础数据结构 | 具体业务 Component |
| `schemas/meta` | Registry 等规范自身的 Schema | 世界业务规则 |
| `schemas/components` | 按命名空间、名称、版本归档的 Component Schema | 无版本的 latest 文件 |
| `registry` | 类别、宿主、权限、权威性、版本与迁移入口 | 具体字段结构副本 |
| `examples` | 可验证的输入样例 | 正式世界存档 |
| `src` | 可复用校验代码 | 临时脚本 |
| `tools` | 命令行入口 | 核心规则 |
| `tests` | 自动回归验证 | 生产数据 |
| `docs` | 运行、维护说明及不参与运行的中文说明模板 | Obsidian 权威设计全文 |

新增 Component 时，必须同时增加版本化 Schema、Registry 条目和相应测试样例。

## 中文说明模板

- 统一放在 docs/templates，不得放入 schemas、registry、examples、src 或 tests 的运行时扫描路径。
- Python 说明模板使用 .py；JSON 类说明模板使用支持注释的 .jsonc。
- JSONC 通过 // 注释说明整体功能、结构区块和具体字段，不使用 _说明 伪字段。
- 正式运行文件仍使用 .json，运行时加载器不得扫描 .jsonc。
- 正式 JSON Schema 自身继续使用 title、description 与 $comment 提供基础机器可读说明。
- 所有说明模板文件名必须包含 _说明模板。