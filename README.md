# ST-Nexus-Entity-Engine

本仓库承载 AI 武侠世界模拟系统的可运行框架。当前已实现 Component Schema 基础、Character/Relation 与首批 Location、Item、Organization、Skill、Concept 机器结构、引用网络、阶段最小召回和语义测试工具；尚未搭建完整应用。

## 当前目录

- `schemas/`：正式 JSON Schema。
- `registry/`：Component 运行语义与版本目录。
- `examples/`：合法、可修正和必须拒绝的样例。
- `src/`：Schema 加载、纠错与校验代码。
- `tools/`：人工运行入口。
- `tests/`：回归测试。
- `docs/`：代码项目自身说明与中文代码说明模板。

权威设计文档保存在 Obsidian 的 `AI_World_Simulator` 目录，不复制进本仓库。

## 当前实现范围

- 通用基础：系统生成的 `type_series` Entity ID、Component 局部 ID、EntityManagement、Identity、EntityReference、当前位置引用和历史索引；
- Character Data：Profile、Behavior Profile、State、Objectives；
- Character Reference：Location、Skill、Memory；
- Character Index：Inventory、Relation、History、Memory；Inventory 由 Item 当前放置引用重建；
- Location：稳定档案、环境、氛围、可定位状态、直接父地点，以及 Child/Containment 反向索引；
- Item：稳定档案、特征、可定位状态、容器能力和唯一当前放置引用；Character Inventory、Item Contents 与 Location Containment 均由此反向生成；
- Organization：稳定档案、结构定义、文化、长期策略、中短期目标、当前状态和直接上级；成员、领地和资产暂不复制为正文清单；
- Skill：公共定义、机制、表现、条件与阶段体系；Character SkillReference 0.2.0 可保存显式阶段或受控 MUV 当前值；
- Concept：定义、可定位规则、适用边界与共享阶段体系；完整阶段保存在 Concept，Host 当前阶段和 MUV 不写回 Concept；
- Character Relation：Endpoint、多个 Aspect、双向语义状态、Memory 反向索引与 HistoryIndex；
- Memory Relation 链接：Memory 直接引用 Relation 与相关 Aspect；
- Event 稳定基础：保存叙事已有的起止时间表达；直接引用参与 Character、实际 Location，以及确实涉及的 Item、Organization、Skill、Concept；
- Entity 网络引用层：固定代码从 Event 权威引用反建各目标 Entity 的 HistoryIndex，从 Memory Owner／来源 Event、Location／Organization 直接父级和 Item CurrentPlacement 反建 Memory、Child、Inventory、Contents 与 Containment 等 Index，并拒绝重复引用、父级或容器循环；
- 跨 Entity 校验：检查引用目标存在与 Type、Character Skill 当前阶段／MUV Binding 是否属于目标 Skill/Concept 框架、Character 固定 Memory 的 Owner、亲历 Memory Owner 与来源 Event 参与者一致、单方关系认知依据的 Memory Owner，以及派生 Index 是否过期；
- 完整样例：除 Character/Relation 外，`examples/entities` 还提供 Location、Item、Organization、Skill 和 Concept 五类可校验样例。文件名只方便人工查找，文件内正式 ID 不含姓名；
- 实体网络样例：`examples/networks/character_event_memory_relation.json`，用于验证两名 Character、一个 Location、一个 Event、两条 Memory 和一个 Relation 的双向查询闭环；
- 完整 Entity 校验：在单个 Component Schema 之外，检查 Relation 端点、Aspect、方向状态和 Memory 依据的一致性。
- 阶段召回：`stage_context.py` 校验 MUV Binding 与区间，并提供 `current_stage`、`transition_check`、`framework_overview` 三种投影；普通回合不会加载整套阶段。
- Entity 提取：`entity_extraction.py` 保存“一次跨类型发现 + 按实际变化 Type 批量细化”的提示词和任务规划；每个 Type 最多一个细化任务，只携带相关原文。开发期 AIRP 工具仍保留单次合并提示的兼容入口。
- Event 测试工具：`event_segmentation_probe.py` 保留历史分阶段复测；`airp_extraction_probe.py` 支持 Event 单路短回归，也支持一批一次客观 Event、Memory、其他 Entity 三路错峰并发候选。脚本先把相邻完整回合默认分开，模型只列应合并交界；地点显著变化可标为待观察，但最近未定稿窗口的跨批重组尚未完成验证。固定脚本据此生成分段、来源范围和三状态流转，并负责关键细节增量、Memory 绑定、实体链接、候选 ID、反向 Index、检查点恢复和单文件证据归档。撤销交界后的重复更新会归入最终分区；不属于最终分段且没有本批事实的空更新会删除。每项前台默认只生成一个候选；失败记录显示最近已提交轮次，恢复优先让同次模型原回复重新经过当前脚本，只补取确实缺失的任务且不会借用旧附录候选。它仍是开发测试工具，不是已冻结的生产流程。

新增的 Event 时间、地点、参与者、相关对象和 Memory Component 只冻结当前稳定数据与引用方向，不代表 Event 或 Memory 正文 Schema 已冻结。共同出场不会自动生成 Relation；Event 测试工具用于验证语义准确性，Process 与 Timeline 仍不在当前实现范围。

`tests/fixtures/event_boundaries_lantern_rounds_1_8.json`、`event_boundaries_lantern_rounds_1_16.json` 与 `event_boundaries_lantern_rounds_1_20.json` 分别保存前八轮两项、前十六轮五项、前二十轮六项人工边界草案。集成工具使用 `--gold` 记录离线对照结果；测试轮数必须与样例范围一致。该对照不调用第三个裁判模型。当前实测证明结构网络通过不等于 Event 语义通过，因此人工边界结果必须单独查看。

Event 单路短回归示例（API 密钥只放环境变量，不写进命令记录或结果文件）：

```powershell
$env:OPENCODE_API_KEY = '<your key>'
.\nexus.cmd python tools\airp_extraction_probe.py '<chat.jsonl>' `
  --endpoint '<chat completions endpoint>' `
  --model '<model name>' `
  --output '<single record.md>' `
  --task-set event `
  --batch-size 4 `
  --batches 2 `
  --gold 'tests\fixtures\event_boundaries_lantern_rounds_1_8.json'
```

关闭思考时同时发送 DeepSeek 官方 `thinking.type=disabled` 与 Zen 实测兼容的 `reasoning_effort=none`；后者不是 DeepSeek 官方承诺，仍须按实际 `reasoning_content` 判断。Event 输出安全上限为 32768 Token，总时限 300 秒、流式连续无字节进展时限 90 秒；持续收到推理但 90 秒仍没有正式正文时也会停止。进度分别显示隐藏推理与正式正文字数；输出上限是防截断额度，不是预期长度。每份测试记录保存实际提示词、正式回复、最近提交检查点和机器结果，并把历史压成线性附录。

## 中文说明模板

docs/templates 保存中文说明模板。每个模板先提供文件功能、架构位置、输入、运作流程、输出和职责边界，再解释具体函数、变量与字段：

- Python 说明模板使用 .py，通过中文 docstring 和关键代码块注释说明；
- JSON、JSON Schema、Registry 和实例的说明模板使用 .jsonc，通过 // 注释区分说明与真实字段；
- 正式运行文件继续使用 .json，不会读取 .jsonc；
- 不再用 _说明 字段混入 JSON 示例；
- 模板文件名包含 _说明模板，正式实现不得依赖模板文件。

## 下载后运行与验证

`.venv` 是每台电脑、每个下载目录各自生成的临时环境，不随仓库分发，也不能设计成可跨电脑搬运的相对环境。统一入口会定位自身所在目录：首次运行自动创建环境，仓库搬迁后自动识别并重建；有 `uv` 时按 `uv.lock` 精确同步，没有 `uv` 时自动使用本机 Python 3.11 以上版本和 `pip`。使用者不需要手工创建、激活或修复虚拟环境。

```powershell
.\nexus.cmd setup
.\nexus.cmd test
.\nexus.cmd check-registry
.\nexus.cmd python -m world_simulator_schema.cli validate --component identity --input examples\repairable\identity.json --repair
```

macOS/Linux 使用 `sh ./nexus.sh`，命令名称相同。`uv.lock` 为 uv 路径提供精确依赖版本；pip 后备路径按 `pyproject.toml` 的允许范围解析版本，不手工维护第二份依赖权威。

这些入口服务于当前 Python 源码、Schema 和测试工具。未来交付给普通玩家的 SillyTavern 插件应提供已经构建好的 JavaScript 文件，不附带 `.venv`，也不要求玩家安装 Python、uv 或开发依赖。

OpenCode Zen 关闭思考的最小接口诊断：

```powershell
$env:OPENCODE_API_KEY = '<your key>'
.\nexus.cmd python tools\opencode_thinking_probe.py
```

诊断发送 `thinking.type=disabled`、Zen 实测兼容的顶层 `reasoning_effort=none`、`temperature=0`、流式开关和 32 Token 输出安全上限；不会发送 Responses API 的嵌套 `reasoning.effort` 或 OpenCode 配置层字段，也不会保存密钥或隐藏推理正文。

校验器只自动纠正安全的结构性错误。新 Entity 先由上层流程匹配既有对象，确认需要创建后再由系统生成 ID；Entity ID、Type、自由文本、数值和未登记枚举不会通过模糊匹配改写。
