# 中文说明模板使用规则

本目录保存给项目负责人、开发者和 AI 阅读的代码说明模板，不属于运行时数据，也不会被 SchemaStore、Component Registry 或 Python 包自动加载。

## 文件

- Component模块_说明模板.py：展示 Python 文件的整体说明、模块结构、函数、变量和业务步骤注释方式。
- ComponentSchema_说明模板.jsonc：用带 // 注释的 JSONC 展示正式 JSON Schema 的结构与字段。
- ComponentRegistry条目_说明模板.jsonc：说明 Registry 各区块、字段、权威性和权限边界。
- Component实例_说明模板.jsonc：说明正式 Component 实例的外壳和业务字段。
- Character实体_说明模板.jsonc：按整体、结构、字段三级说明完整 Character 的数据边界和读取顺序。
- CharacterRelation实体_说明模板.jsonc：说明同一人物对的 Endpoint、多个 Aspect、方向状态和 Memory 索引如何配合。
- EntityID模块_说明模板.py：说明 `type_series` ID 的系统生成方式、校验边界和上层调用顺序。
- Entity校验模块_说明模板.py：说明单个 Component 校验之后如何执行完整 Entity 的跨组件一致性检查。
- Entity网络模块_说明模板.py：说明多个 Entity 的目标校验、反向索引重建和权威方向。
- Entity网络样例_说明模板.jsonc：说明 Character、Location、Event、Memory 与 Relation 封闭测试样例的引用结构。
- AIRP三路提取测试工具_说明模板.py：说明 Event、Memory、其他 Entity 同批并发、固定汇合、失败恢复和单文件证据归档。

## 说明层级

### 1. 文件级总览

每个说明模板开头先用非技术语言说明：

- 文件解决什么问题；
- 位于整体架构的哪个位置，与哪些模块连接；
- 接收什么输入；
- 按什么顺序运作；
- 产生什么输出；
- 明确不负责什么；
- 推荐从哪里开始阅读。

### 2. 结构级说明

对类、函数、Schema 对象和 Registry 区块说明职责、调用关系、参数、返回值、权限、权威来源和失败方式。

### 3. 代码级说明

对关键常量、属性、局部变量、字段和有业务含义的处理步骤添加中文备注。注释解释“为什么”和“会影响什么”，不重复抄写代码表面动作。

“逐行说明”不是给 import、括号、赋值等每一行机械加注释，而是保证每段有业务意义的代码都能被不懂编程的人顺着说明理解。

## 格式规则

1. Python 说明模板继续使用 .py，文件名包含 _说明模板。
2. 正式 JSON 文件继续使用 .json；其说明模板使用 .jsonc，通过 // 或 /* */ 写中文注释。
3. JSONC 说明模板只供阅读，不要求标准 JSON 解析，也禁止进入运行时扫描路径。
4. 正式 JSON Schema 仍应使用标准 title、description 和 $comment 提供可被工具读取的基础说明。
5. 不再使用 _说明 或 字段名_说明 伪装成业务字段，避免与真实结构混淆。
6. 模板统一放在 docs/templates，不得放入 schemas、registry、examples、src 或 tests。
7. 接口、变量、字段含义或数据结构变化时，同步更新对应说明模板。
8. 通用模板不能准确解释关键模块时，应增加专用说明模板。
