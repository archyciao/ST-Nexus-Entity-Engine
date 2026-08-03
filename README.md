# ST-Nexus-Entity-Engine

本仓库承载 AI 武侠世界模拟系统的可运行框架。当前已实现 Component Schema 基础、Character/Relation 第一批机器结构、Character—Event—Memory—Location 引用网络，以及 Event 语义测试工具；尚未搭建完整应用。

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
- Character Reference：Inventory、Skill、Memory；
- Character Index：Relation、History、Memory；
- Character Relation：Endpoint、多个 Aspect、双向语义状态、Memory 反向索引与 HistoryIndex；
- Memory Relation 链接：Memory 直接引用 Relation 与相关 Aspect；
- Event 稳定基础：保存叙事已有的起止时间表达；实际发生或经过地点直接引用 Location，并保留移动顺序；
- Entity 网络引用层：Event 直接引用参与 Character 与实际 Location，Memory 直接引用 Owner Character 和来源 Event；固定代码重建 Character、Location、Event 与 Relation 的反向 Index；
- 跨 Entity 校验：检查引用目标存在与 Type、Character 固定 Memory 的 Owner、亲历 Memory Owner 与来源 Event 参与者一致、单方关系认知依据的 Memory Owner，以及派生 Index 是否过期；
- 完整样例：`examples/entities/character_linghuchong.json` 与 `examples/entities/character_relation_linghu_yuebuqun.json`。文件名只方便人工查找，文件内正式 ID 不含姓名；
- 实体网络样例：`examples/networks/character_event_memory_relation.json`，用于验证两名 Character、一个 Location、一个 Event、两条 Memory 和一个 Relation 的双向查询闭环；
- 完整 Entity 校验：在单个 Component Schema 之外，检查 Relation 端点、Aspect、方向状态和 Memory 依据的一致性。
- Event 测试工具：`event_segmentation_probe.py` 保留历史分阶段复测；`airp_extraction_probe.py` 支持 Event 单路短回归，也支持一批一次客观 Event、Memory、其他 Entity 三路错峰并发候选。Event 不重复枚举实体引用；Entity 返回带原文短引的实际参与依据，固定脚本再生成参与者、地点和相关对象链接，并负责来源裁切、三状态流转、关键细节增量、Memory 绑定、候选 ID、反向 Index、检查点恢复和单文件证据归档。每项前台默认只生成一个候选；失败时复用已通过候选，只补取失败任务。它仍是开发测试工具，不是已冻结的生产流程。

新增的 Event 时间、地点、参与者和 Memory Component 只冻结当前稳定数据与引用方向，不代表 Event 或 Memory 正文 Schema 已冻结。Event 测试工具用于验证语义准确性；Process 与 Timeline 仍不在当前实现范围。

`tests/fixtures/event_boundaries_lantern_rounds_1_20.json` 保存前二十轮的六项人工边界草案。集成工具使用 `--gold` 记录离线对照结果；该对照不调用第三个裁判模型。当前实测证明结构网络通过不等于 Event 语义通过，因此人工边界结果必须单独查看。

Event 单路短回归示例（API 密钥只放环境变量，不写进命令记录或结果文件）：

```powershell
$env:OPENCODE_API_KEY = '<your key>'
python tools\airp_extraction_probe.py '<chat.jsonl>' `
  --endpoint '<chat completions endpoint>' `
  --model '<model name>' `
  --output '<single record.md>' `
  --task-set event `
  --batch-size 4 `
  --batches 4 `
  --gold 'tests\fixtures\event_boundaries_lantern_rounds_1_20.json'
```

默认请求关闭模型思考，Event 输出安全上限为 32768 Token，总时限 300 秒、流式连续无进展时限 90 秒。输出上限是防截断额度，不是预期长度；提示词仍要求紧凑 JSON。每份测试记录保存实际提示词、正式回复和机器结果，并把恢复历史压成线性附录，避免递归嵌套造成文件膨胀。

## 中文说明模板

docs/templates 保存中文说明模板。每个模板先提供文件功能、架构位置、输入、运作流程、输出和职责边界，再解释具体函数、变量与字段：

- Python 说明模板使用 .py，通过中文 docstring 和关键代码块注释说明；
- JSON、JSON Schema、Registry 和实例的说明模板使用 .jsonc，通过 // 注释区分说明与真实字段；
- 正式运行文件继续使用 .json，不会读取 .jsonc；
- 不再用 _说明 字段混入 JSON 示例；
- 模板文件名包含 _说明模板，正式实现不得依赖模板文件。

## 本地安装与验证

项目依赖声明在标准 `pyproject.toml` 中，`uv` 只是可选开发工具。没有 `uv` 时可直接使用 Python 自带虚拟环境和 `pip`；目录搬迁后删除并重建 `.venv` 即可，正式项目文件不依赖虚拟环境中的绝对路径。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\world-schema check-registry
.\.venv\Scripts\world-schema validate --component identity --input examples\repairable\identity.json --repair
python -m unittest tests.test_event_segmentation_probe -v
python -m unittest tests.test_entity_network -v
```

`uv.lock` 为使用 uv 时提供精确依赖版本；pip 会按 `pyproject.toml` 的允许范围解析版本。若以后需要让 pip 用户也完全复现同一依赖版本，应由发布流程自动导出锁定清单，不手工维护第二份依赖权威。

校验器只自动纠正安全的结构性错误。新 Entity 先由上层流程匹配既有对象，确认需要创建后再由系统生成 ID；Entity ID、Type、自由文本、数值和未登记枚举不会通过模糊匹配改写。
