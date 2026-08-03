"""Entity ID 模块中文说明模板。

一、文件级总览
文件功能：说明系统如何生成和校验 ``type_series`` Entity ID，以及组件局部 ID。
架构位置：属于 Entity Core 基础能力，由受控创建流程调用，不由 AI 直接调用。
输入：已经确认的 Entity Type，以及系统时间和安全随机源。
运作方式：先匹配既有 Entity；确认确需新建后，系统生成 ULID，并与 Type 组合。
输出：例如 ``character_01K2ABCDEFGHJKMNPQRSTV0001`` 的 Entity ID，或
     ``objective_01K2ABCDEFGHJKMNPQRSTV0105`` 的组件局部 ID。
职责边界：不保存姓名、不匹配既有 Entity、不替代数据库唯一约束。

二、结构级说明
正式模块分别提供 Entity ID 与局部 ID 的生成、校验函数，并共用内部 ULID 编码。
创建 Entity 的调用方必须先完成实体匹配；持久化层必须检查唯一性并处理冲突。

三、字段级说明
``type`` 是 Type Registry 中的稳定类型；``series`` 是 26 位 ULID。姓名、化名、
公开身份和隐藏身份都属于 Entity 数据，不能进入 ID。``motivation``、
``preference``、``objective``、``relation_aspect`` 前缀只表示 Component 内的
局部对象种类；对应 ID 不能作为 Entity Reference。

本文件只供人和 AI 阅读，不参与运行。
"""

from __future__ import annotations


def example_new_entity_id(entity_type: str) -> str:
    """展示调用形态；正式实现位于 ``src/world_simulator_schema/entity_ids.py``。"""

    example_series = "01K2ABCDEFGHJKMNPQRSTV0001"
    # example_series 只是格式示例；正式序列必须由系统随机生成。
    return f"{entity_type}_{example_series}"


def example_new_local_id(kind: str) -> str:
    """展示局部 ID 调用形态；正式实现会限制允许的 kind。"""

    example_series = "01K2ABCDEFGHJKMNPQRSTV0105"
    return f"{kind}_{example_series}"
