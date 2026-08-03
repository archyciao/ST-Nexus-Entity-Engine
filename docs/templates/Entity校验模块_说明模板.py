"""Entity 跨组件校验模块说明模板。

一、文件级总览
功能：检查单个 Component 之外的 Entity 整体一致性。
架构位置：位于 JSON Schema 校验之后、数据库写入之前；不负责业务推演。
运作方式：逐个检查 Component，再按 Entity Type 执行跨组件规则，返回错误报告。
输入输出：输入完整 Entity 字典，输出只读校验结果；不修正、不生成、不写回。

二、结构级说明
公开入口应只有校验器和结果对象；某个 Entity Type 的专项规则放在独立内部函数中，
避免把 Character、Relation、Event 的规则混成一个无法维护的大函数。
"""

from dataclasses import dataclass
from typing import Any


@dataclass
class 示例校验报告:
    """保存校验结论；错误项应包含稳定错误码、路径和中文解释。"""

    valid: bool
    errors: list[dict[str, str]]


class 示例Entity校验器:
    """展示跨组件校验入口的职责，不参与正式运行。"""

    def validate(self, entity: dict[str, Any]) -> 示例校验报告:
        """三、字段与步骤级说明

        entity 是已经解析的完整 Entity；正式实现先复用 Component 校验器，
        再检查端点与角色等跨组件关系。这里固定返回通过，仅用于说明调用形状。
        """

        errors: list[dict[str, str]] = []
        # errors 汇总所有可定位问题，让维护者一次看到完整结果。
        return 示例校验报告(valid=not errors, errors=errors)
