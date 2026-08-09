"""当前已经进入机器闭环、可由宿主管理的 Entity Type。

宿主和界面必须读取这里，不各自维护类型白名单。Process 与 Timeline 在进入正式
Schema 和运行闭环后，再由核心在此发布。
"""

from __future__ import annotations


CURRENT_ENTITY_TYPES = (
    {"id": "event", "label": "Event", "description": "已经成立或正在定稿的客观事件"},
    {"id": "character", "label": "Character", "description": "人物身份、资料与当前状态"},
    {"id": "location", "label": "Location", "description": "地点资料、环境与空间关系"},
    {"id": "item", "label": "Item", "description": "物品资料、状态与放置关系"},
    {"id": "organization", "label": "Organization", "description": "组织资料、结构、目标与状态"},
    {"id": "skill", "label": "Skill", "description": "技能定义、机制与阶段"},
    {"id": "concept", "label": "Concept", "description": "概念、规则与适用范围"},
    {"id": "memory", "label": "Memory", "description": "具体角色的主观认知及其 Event 来源"},
    {"id": "character_relation", "label": "Relation", "description": "人物关系端点、面向与方向状态"},
)

CURRENT_ENTITY_TYPE_IDS = frozenset(item["id"] for item in CURRENT_ENTITY_TYPES)
