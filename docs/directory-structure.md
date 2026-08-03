# 目录职责

| 目录 | 权威内容 | 不应放入 |
|---|---|---|
| `schemas/core` | 跨 Component 的基础数据结构 | 具体业务 Component |
| `schemas/meta` | Registry 等规范自身的 Schema | 世界业务规则 |
| `schemas/components` | 按命名空间、名称、版本归档的 Component Schema | 无版本的 latest 文件 |
| `registry` | 类别、宿主、权限、权威性、版本与迁移入口 | 具体字段结构副本 |
| `examples` | 可验证的 Component、完整 Entity 和封闭 Entity 网络样例 | 正式世界存档 |
| `src` | Entity ID 生成、Schema 加载、纠错、单 Entity 与跨 Entity 网络校验、反向索引投影等可复用代码 | 临时脚本 |
| `tools` | 命令行入口与可复现实验 | Schema 与世界语义的权威定义 |
| `tests` | 自动回归验证 | 生产数据 |
| `docs` | 运行、维护说明及不参与运行的中文说明模板 | Obsidian 权威设计全文 |

新增 Component 时，必须同时增加版本化 Schema、Registry 条目和相应测试样例。

新 Entity 的正式 ID 和已登记的 Component 局部 ID 均由 `src` 中的受控生成器产生；实体匹配和数据库唯一约束分别由后续 Resolver/检索层与持久化层负责，不得让 AI 或样例文件自行编造这些 ID。

Character Relation 的逻辑唯一性是“同一世界中的同一无序人物对最多一个 Relation”。当前 Entity 校验器负责端点排序和内部一致性；真正的跨文件唯一约束由后续持久化层实现。

`src/world_simulator_schema/entity_network.py` 将一个提交批次视为封闭网络，检查引用目标并根据 Event 的参与者与实际地点、Memory 的 Owner 与来源 Event 等权威 Reference，重建 Character、Location、Event 与 Relation 的派生 Index。它不生成 Event/Memory 正文，也不让 Index 反向修改权威事实。`examples/networks` 只保存这种跨 Entity 测试样例。

`tools/event_segmentation_probe.py` 保留 Event 历史分阶段复测入口，不是正式 Event Schema，也不代表当前默认生产调用方案。它将分段开头转成排他性起止限制，并保存完整提示词、模型正式回复、机器结果和检查点。

`tools/airp_extraction_probe.py` 是当前开发期复测入口：可用 `--task-set event` 只校准 Event，也可让 Event、Memory、其他 Entity 读取同一批次快照错峰并发返回。Event 只处理分段和事件事实，Entity 使用稀疏补丁并返回实际参与的原文短引。固定脚本完成三状态、来源绑定、Entity 到 Event 的引用投影、候选 ID、反向 Index 和封闭网络检查；语义候选默认一次，失败任务可以从检查点独立恢复，已经通过的候选直接复用。每场运行只写一份 Markdown 证据记录，恢复历史按线性附录保存。该工具的候选网络不等于已确认世界事实，正式语义仍以 Obsidian Event 专项和 Resolver 边界为准。

`tests/fixtures/event_boundaries_lantern_rounds_1_20.json` 是开发期人工边界回归样例，只用于对照模型切分结果，不是正式世界存档或运行时裁判数据。

## 中文说明模板

- 统一放在 docs/templates，不得放入 schemas、registry、examples、src 或 tests 的运行时扫描路径。
- Python 说明模板使用 .py；JSON 类说明模板使用支持注释的 .jsonc。
- JSONC 通过 // 注释说明整体功能、结构区块和具体字段，不使用 _说明 伪字段。
- 正式运行文件仍使用 .json，运行时加载器不得扫描 .jsonc。
- 正式 JSON Schema 自身继续使用 title、description 与 $comment 提供基础机器可读说明。
- 所有说明模板文件名必须包含 _说明模板。
