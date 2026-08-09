# ST-Nexus-Entity-Engine

SillyTavern 长篇 AIRP 的 Entity、Event、Memory 与 Relation 数据底座。当前仓库同时是 NexusEntityEngine 的正式核心与可安装 Tavern 插件：根目录提供前端入口，`server` 提供宿主适配，二者直接调用同仓库 Schema、Registry、校验器和 Event V2 提取器。

权威设计文档位于 `D:\Obsidian Vault\AI_World_Simulator`，本仓库只保存机器实现和维护说明。

## 当前原则

- Identity、ID、Type、引用、Relation、实际地点、当前位置、父级、物品放置和反向 Index 使用严格结构；
- 其他客观资料写入各 Type 已命名的开放语义 Component，例如 `character_behavior_profile`、`location_atmosphere`、`item_characteristic`；字段内容按事实开放填写，不要求模型补齐固定模板；
- `entity_facts` 只保留旧数据兼容，不是现行提取入口；无法安全归类的候选进入仅供维护的 `entity_field_maintenance`，不参加普通 AI 召回；
- Event 的 `event_content` 保存三阶段状态、故事摘要和少量关键言语／动作；Entity Core 保存短 Description；
- Event 以普通引用连接有清楚事实的 Character、Location、Item、Organization、Skill、Concept 和 Character Relation，不分类“在场、提及、计划或回忆”；
- 实际发生或经过的 Location 单独保存；
- Memory 直接引用 Owner 和来源 Event。Owner 可能听闻、阅读或推断，不能反推为 Event 参与者；
- Index 由脚本从权威引用重建，AI 不维护反向目录；
- 原始消息／来源证据、运行提示词／原回复分别属于维护层与运行记录，不进入常规 Event 召回。

## Event V2

1. 模型读取相邻前文与本批连续 AI 正文，返回较粗 Event 起点和每项 Event 内的 Character、Location、Item、Organization、Skill、Concept 名录；
2. 脚本校验起点并锁定无遗漏、无重叠的原文分段；
3. 脚本先把段内目录解析为新增、更新或待确认；待确认对象不擅自新建，下游任务也不能绕过目录补建身份；
4. 固定分段后，并发生成 Event 内容、新增 Entity 资料、既有 Entity 字段更新、Relation 与严格引用；没有对应候选时跳过该 Entity 任务；
5. 更新只作用于发生变化的开放语义字段：补充内容合并，改变旧事实时用完整字段修订稿替换并保留修订历史；
6. 脚本完成姓名归一、旧 Event 衔接、字段名纠错、Memory 绑定、实际地点、普通关联和反向 Index；
7. Schema 与封闭网络校验通过后再提交。

现行提示词在 `src/world_simulator_schema/event_extraction_v2.py`，开发复测入口是 `tools/airp_extraction_v2_probe.py`。历史探针仍供回归和复用稳定工具函数，不代表当前提示词。

## 目录

- `index.js`、`style.css`、`manifest.json`、`assets/`、`ui/`：Tavern 前端扩展与工作台；
- `server/`：Tavern 服务端适配、每聊天 SQLite 分库和批量任务进程；
- `schemas/`：版本化 JSON Schema；
- `registry/`：Component 宿主、权限、权威性和版本；
- `src/`：ID、校验、网络投影、提取规划和召回；
- `tools/`：开发诊断与复测入口；
- `examples/`：合法、可修复及封闭网络样例；
- `tests/`：回归测试；
- `docs/`：代码维护说明和不参与运行的中文模板。

当前开发机的 `data/default-user/extensions/NexusEntityEngine` 目录联接直接指向仓库根目录，`plugins/NexusEntityEngine` 指向根目录的 `server`。不存在第二份需要同步的酒馆插件源码。

工作台已支持 API 地址自动识别、正式五阶段编排、阶段 API、Schema 驱动实体编辑、每聊天物理分库和真实手动批量提取。跨独立任务幂等、检查点续跑、自动楼层触发和召回仍在后续里程碑内。

## 运行

Windows：

```powershell
.\nexus.cmd setup
.\nexus.cmd test
.\nexus.cmd check-registry
```

macOS／Linux 使用 `sh ./nexus.sh`，命令相同。统一入口会在当前下载目录自动创建或重建 `.venv`：有 `uv` 时按锁文件同步，否则使用 Python 3.11+ 与 pip。`.venv` 不随仓库分发；未来 Tavern 构建产物也不会要求普通用户安装 Python。

Event V2 开发复测：

```powershell
$env:OPENCODE_API_KEY = '<your key>'
.\nexus.cmd python tools\airp_extraction_v2_probe.py '<chat.jsonl>' `
  --endpoint '<chat completions endpoint>' `
  --model '<model name>' `
  --output '<single record.md>' `
  --batch-size 4 `
  --batches 2
```

API 密钥只放环境变量。测试记录可以保存实际提示词、正式回复和耗时，但不得保存密钥或隐藏推理正文。
