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

`src/world_simulator_schema/entity_network.py` 将一个提交批次视为封闭网络，检查引用目标、Event 相关对象去重、Character Skill 当前阶段与 MUV Binding，并根据 Event 参与者、实际地点及相关 Item／Organization／Skill／Concept，Memory Owner 与来源 Event、Location／Organization 直接父级和 Item CurrentPlacement 等权威 Reference，重建 History、Relation、Memory、Child、Inventory、Contents 与 Containment 等派生 Index。它同时拒绝父级和容器循环，不生成 Event／Memory 正文，也不让 Index 反向修改权威事实。`examples/networks` 只保存这种跨 Entity 测试样例。

`src/world_simulator_schema/stage_context.py` 只负责 Skill/Concept 阶段结构校验与运行时最小投影。普通回合只返回当前阶段和匹配用途的 Context Block；MUV 缺失、区间重叠或映射失效时返回明确错误，不加载全部阶段兜底。

`src/world_simulator_schema/entity_extraction.py` 负责规划 Entity 两阶段提取：第一次统一发现和消歧，第二次仅按实际变化的 Type 批量细化。它只生成任务，不调用模型、不生成 ID、不写数据库。开发期 `airp_extraction_probe.py` 仍保留合并提示兼容入口，但其候选物化已经使用新的五类 Component、Item 放置权威和反向索引规则。

`tools/event_segmentation_probe.py` 保留 Event 历史分阶段复测入口，不是正式 Event Schema，也不代表当前默认生产调用方案。它将分段开头转成排他性起止限制，并保存完整提示词、模型正式回复、机器结果和检查点。

`tools/opencode_thinking_probe.py` 用固定的极短请求单独诊断 Zen Chat Completions 是否实际遵从“官方 `thinking.type=disabled` + Zen 实测 `reasoning_effort=none`”组合。它只记录正式正文、请求控制字段、时延和隐藏推理字数，不保存隐藏推理原文或 API 密钥，不能代替 Event 语义测试。

`tools/airp_extraction_probe.py` 是当前开发期复测入口：可用 `--task-set event` 只校准 Event，也可让 Event、Memory、其他 Entity 读取同一批次快照错峰并发返回。Event 只处理分段和事件事实；脚本先按回合建立可逆分区并默认保留交界，模型只列应合并项，地点显著变化可标为待观察，但跨批重组仍待完善。Entity 使用稀疏补丁并返回实际参与的原文短引。固定脚本完成分段与来源范围、无歧义重复／空更新清理、三状态、Entity 到 Event 的引用投影、候选 ID、反向 Index 和封闭网络检查。流式进度区分隐藏推理与正式正文；失败报告保存最近已提交轮次，恢复只读取最新检查点及其后同次原回复，不借用旧附录。每场运行只写一份 Markdown 证据记录。人工边界样例的轮数必须与本次处理范围一致；候选网络仍不等于已确认世界事实。

`tests/fixtures/event_boundaries_lantern_rounds_1_8.json`、`event_boundaries_lantern_rounds_1_16.json` 与 `event_boundaries_lantern_rounds_1_20.json` 是不同轮数的开发期人工边界回归样例，只用于对照同范围模型切分结果，不是正式世界存档或运行时裁判数据。

## 中文说明模板

- 统一放在 docs/templates，不得放入 schemas、registry、examples、src 或 tests 的运行时扫描路径。
- Python 说明模板使用 .py；JSON 类说明模板使用支持注释的 .jsonc。
- JSONC 通过 // 注释说明整体功能、结构区块和具体字段，不使用 _说明 伪字段。
- 正式运行文件仍使用 .json，运行时加载器不得扫描 .jsonc。
- 正式 JSON Schema 自身继续使用 title、description 与 $comment 提供基础机器可读说明。
- 所有说明模板文件名必须包含 _说明模板。
