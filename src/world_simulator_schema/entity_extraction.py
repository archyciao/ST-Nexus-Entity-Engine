"""World Entity 开放事实提取的任务规划。

模型负责识别对象和事实；固定脚本负责名称归一、ID、直接引用、关系、反向索引与
Schema 落地。事实字段不套固定资料模板，同一批所有 Type 共用一次细化任务。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable


ENTITY_DISCOVERY_SYSTEM_PROMPT = """
从本批原文发现新增或确有变化的 Character、Location、Item、Organization、Skill 与 Concept。
相同对象只返回一次；使用实际名称，别名另列。这里不判断对象是否实际参与 Event，也不填写位置、
父级、物品放置、关系或正式 ID。每个对象用逐字短引说明依据。

只输出 JSON：
{"entities":[{"entity_key":"type:主要名称","type":"character | location | item | organization | skill | concept","primary_name":"主要名称","aliases_add":[],"record_action":"create | update","evidence":[{"source_ref":"消息书签","quote":"逐字短引"}]}]}
""".strip()


ENTITY_FACT_ENRICHMENT_SYSTEM_PROMPT = """
只补充 payload.candidates 中 Entity 自身新增或变化的事实。新对象填写稳定的一句 Description；
既有对象省略未变化内容。facts_patch 使用清楚的字段名开放填写，不要求补齐固定模板；值可以是文字、
数字、布尔值、数组或嵌套对象。正式名称和别名使用专门字段。当前位置、父级、物品放置、相关概念、
关系端点、正式 ID、反向索引、Event 和 Memory 不放进 facts_patch。

只输出 JSON：
{"entities":[{"entity_key":"type:主要名称","aliases_add":[],"description":"新建或确有变化时填写","evidence":[{"source_ref":"消息书签","quote":"逐字短引"}],"facts_patch":{}}]}
""".strip()


# 保留公开常量名，调用方无需为从“按 Type 固定模板”迁移到“一次开放事实”改接口。
ENTITY_ENRICHMENT_SYSTEM_PROMPTS = {"all": ENTITY_FACT_ENRICHMENT_SYSTEM_PROMPT}


@dataclass(frozen=True)
class EntityEnrichmentJob:
    """同批全部 World Entity 的一次开放事实细化任务。"""

    entity_type: str
    system_prompt: str
    payload: dict[str, Any]


def plan_entity_enrichment_jobs(
    discovery_plan: dict[str, Any],
    *,
    source_messages: Iterable[dict[str, Any]],
    existing_previews: Iterable[dict[str, Any]] = (),
) -> list[EntityEnrichmentJob]:
    """若有候选则生成一个批量任务，只携带候选实际引用的原文。"""

    messages = {
        str(item.get("ref")): deepcopy(item)
        for item in source_messages
        if isinstance(item, dict) and item.get("ref")
    }
    previews = {
        str(item.get("entity_key")): deepcopy(item)
        for item in existing_previews
        if isinstance(item, dict) and item.get("entity_key")
    }
    candidates = [
        deepcopy(item)
        for item in discovery_plan.get("entities", [])
        if isinstance(item, dict)
        and item.get("type")
        in {"character", "location", "item", "organization", "skill", "concept"}
    ]
    if not candidates:
        return []

    refs = {
        str(evidence.get("source_ref"))
        for candidate in candidates
        for evidence in candidate.get("evidence", [])
        if isinstance(evidence, dict)
        and str(evidence.get("source_ref")) in messages
    }
    refs.update(
        str(ref)
        for candidate in candidates
        for ref in candidate.get("evidence_refs", [])
        if str(ref) in messages
    )
    candidate_keys = {str(item.get("entity_key", "")) for item in candidates}
    return [
        EntityEnrichmentJob(
            entity_type="all",
            system_prompt=ENTITY_FACT_ENRICHMENT_SYSTEM_PROMPT,
            payload={
                "candidates": candidates,
                "source_messages": [
                    messages[ref] for ref in messages if ref in refs
                ],
                "existing_previews": [
                    previews[key] for key in previews if key in candidate_keys
                ],
            },
        )
    ]
