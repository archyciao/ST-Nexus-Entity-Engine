# 目录职责

| 目录 | 权威内容 | 不应放入 |
|---|---|---|
| `schemas/core` | 跨 Component 的基础数据结构 | 具体业务 Component |
| `schemas/meta` | Registry 等规范自身的 Schema | 世界业务规则 |
| `schemas/components` | 按命名空间、名称、版本归档的 Component Schema | 无版本的 latest 文件 |
| `registry` | 类别、宿主、权限、权威性、版本与迁移入口 | 具体字段结构副本 |
| `examples` | 可验证的 Component、完整 Entity 和封闭 Entity 网络样例 | 正式世界存档 |
| `src` | Entity ID 生成、Schema 加载、纠错、单 Entity 与跨 Entity 网络校验、反向索引投影、阶段上下文和提取任务规划等可复用代码 | 临时脚本 |
| `tools` | 命令行入口与可复现实验 | Schema 与世界语义的权威定义 |
| `tests` | 自动回归验证 | 生产数据 |
| `docs` | 运行、维护说明及不参与运行的中文说明模板 | Obsidian 权威设计全文 |

根目录 `nexus.cmd`、`nexus.ps1` 与 `nexus.sh` 是源码仓库的统一入口：自动在当前目录创建或重建 `.venv`，优先按 `uv.lock` 同步，缺少 uv 时使用本机 Python 与 pip。`.venv` 不随仓库分发。未来玩家使用的 Tavern 构建产物不得依赖这些 Python 开发入口。

新增 Component 时，必须同时增加版本化 Schema、Registry 条目和相应测试样例。

新 Entity 的正式 ID 和已登记的 Component 局部 ID 均由 `src` 中的受控生成器产生；实体匹配和数据库唯一约束分别由后续 Resolver/检索层与持久化层负责，不得让 AI 或样例文件自行编造这些 ID。

Character Relation 的逻辑唯一性是“同一世界中的同一无序人物对最多一个 Relation”。当前 Entity 校验器负责端点排序和内部一致性；真正的跨文件唯一约束由后续持久化层实现。

`src/world_simulator_schema/entity_network.py` 将一个提交批次视为封闭网络，检查引用目标、Event 普通相关对象去重、Character Skill 当前阶段与 MUV Binding，并根据 Event 的普通相关对象与实际地点、Memory Owner 与来源 Event、Location／Organization 直接父级和 Item CurrentPlacement 等权威 Reference，重建 History、Relation、Memory、Child、Inventory、Contents 与 Containment 等派生 Index。它同时拒绝父级和容器循环，不生成 Event／Memory 正文，也不让 Index 反向修改权威事实。`examples/networks` 只保存这种跨 Entity 测试样例。

`src/world_simulator_schema/stage_context.py` 只负责 Skill/Concept 阶段结构校验与运行时最小投影。普通回合只返回当前阶段和匹配用途的 Context Block；MUV 缺失、区间重叠或映射失效时返回明确错误，不加载全部阶段兜底。

`src/world_simulator_schema/entity_extraction.py` 负责规划一次跨 Type 发现和至多一次开放事实补充。它只生成任务，不调用模型、不生成 ID、不写数据库。名称、Type、引用与 Relation 保持严格；其他资料进入 `entity_facts`，不要求每个 Type 填固定模板。

`tools/event_segmentation_probe.py` 保留 Event 历史分阶段复测入口，不是正式 Event Schema，也不代表当前默认生产调用方案。它将分段开头转成排他性起止限制，并保存完整提示词、模型正式回复、机器结果和检查点。

`tools/opencode_thinking_probe.py` 用固定的极短请求单独诊断 Zen Chat Completions 是否实际遵从“官方 `thinking.type=disabled` + Zen 实测 `reasoning_effort=none`”组合。它只记录正式正文、请求控制字段、时延和隐藏推理字数，不保存隐藏推理原文或 API 密钥，不能代替 Event 语义测试。

`tools/airp_extraction_v2_probe.py` 是当前开发期复测入口。第一次模型调用返回较粗 Event 起点和各段实体名录；脚本校验后形成固定分段；第二层并发生成 Event 内容、开放 Entity 事实、Relation 与严格引用。脚本负责旧 Event 衔接、名称归一、普通 Event 关联、实际地点、Memory 链路和反向 Index。`tools/airp_extraction_probe.py` 保留历史回归与 V2 共用的稳定工具函数，不作为当前提示词权威。运行证据与世界数据库分开保存。

`src/world_simulator_schema/event_context.py` 构建有硬预算的 Event 常规召回包。它采用字段白名单，不输出来源书签、边界位置或后台原文；预算不足时先省略特写，再省略摘要和低相关 Event，不截断句子。

`tests/fixtures/event_boundaries_lantern_rounds_1_8.json`、`event_boundaries_lantern_rounds_1_16.json` 与 `event_boundaries_lantern_rounds_1_20.json` 是不同轮数的开发期人工边界回归样例，只用于对照同范围模型切分结果，不是正式世界存档或运行时裁判数据。

## 中文说明模板

- 统一放在 docs/templates，不得放入 schemas、registry、examples、src 或 tests 的运行时扫描路径。
- Python 说明模板使用 .py；JSON 类说明模板使用支持注释的 .jsonc。
- JSONC 通过 // 注释说明整体功能、结构区块和具体字段，不使用 _说明 伪字段。
- 正式运行文件仍使用 .json，运行时加载器不得扫描 .jsonc。
- 正式 JSON Schema 自身继续使用 title、description 与 $comment 提供基础机器可读说明。
- 所有说明模板文件名必须包含 _说明模板。
