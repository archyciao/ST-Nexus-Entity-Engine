"""Entity 两阶段提取模块说明模板。

一、文件级总览
功能：先统一发现跨类型候选，再只为本批实际变化的 Type 安排批量细化任务。
架构位置：位于不可变原文快照与候选合并脚本之间，不直接写数据库。
输入：发现计划、来源消息和相关既有 Entity 轻量预览。
输出：每个实际变化 Type 至多一个细化任务。
不负责：不调用模型，不生成正式 ID，不修改 Event、Memory 或反向 Index。

二、结构级说明
第一次调用防漏和消歧；第二次调用只看同一 Type 的字段边界。脚本只携带该批候选
引用的原文，避免让模型反复阅读无关叙事。没有变化的 Type 不产生第二次调用。
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class 示例细化任务:
    """三、字段级说明：保存任务类型、专用提示词和最小输入材料。"""

    entity_type: str
    prompt: str
    payload: dict[str, Any]
