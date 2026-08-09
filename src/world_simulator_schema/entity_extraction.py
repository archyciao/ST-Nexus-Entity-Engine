"""World Entity 目录解析与字段级提取任务规划。

模型先发现对象；固定脚本再把目录解析为新增、更新或待确认。新增与更新使用不同
提示词，仍各自按整批调用，不为每个 Entity 逐一请求模型。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Iterable
import unicodedata

from .event_extraction_v2 import (
    ENTITY_CREATE_SYSTEM_PROMPT,
    ENTITY_UPDATE_SYSTEM_PROMPT,
)


ENTITY_TYPES = {
    "character", "location", "item", "organization", "skill", "concept"
}

ENTITY_DISCOVERY_SYSTEM_PROMPT = """
从本批原文发现 Character、Location、Item、Organization、Skill 与 Concept。相同对象只返回一次；
使用实际名称，别名另列。这里不判断新增还是更新，也不填写位置、父级、物品放置、关系、正式 ID、
Event 或 Memory。每个对象用逐字短引说明依据。

只输出 JSON：
{"entities":[{"entity_key":"type:主要名称","type":"character | location | item | organization | skill | concept","primary_name":"实际主要名称","aliases_add":[],"evidence":[{"source_ref":"消息书签","quote":"逐字短引"}]}]}
""".strip()

ENTITY_CREATE_ENRICHMENT_SYSTEM_PROMPT = ENTITY_CREATE_SYSTEM_PROMPT
ENTITY_UPDATE_ENRICHMENT_SYSTEM_PROMPT = ENTITY_UPDATE_SYSTEM_PROMPT
ENTITY_ENRICHMENT_SYSTEM_PROMPTS = {
    "create": ENTITY_CREATE_ENRICHMENT_SYSTEM_PROMPT,
    "update": ENTITY_UPDATE_ENRICHMENT_SYSTEM_PROMPT,
}


@dataclass(frozen=True)
class EntityEnrichmentJob:
    """同批新增或更新 Entity 的一次字段提取任务。"""

    entity_type: str
    record_action: str
    system_prompt: str
    payload: dict[str, Any]


def _name_signature(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[\s\-_·•・,，。.!！?？:：;；'\"“”‘’（）()\[\]【】]+", "", text)


def _candidate_names(candidate: dict[str, Any]) -> set[str]:
    key_name = str(candidate.get("entity_key", "")).partition(":")[2]
    return {
        signature
        for value in [
            candidate.get("primary_name"),
            key_name,
            *candidate.get("aliases_add", []),
        ]
        if (signature := _name_signature(value))
    }


def _preview_names(preview: dict[str, Any]) -> set[str]:
    key_name = str(preview.get("entity_key", "")).partition(":")[2]
    return {
        signature
        for value in [
            preview.get("primary_name"),
            key_name,
            *preview.get("aliases", []),
        ]
        if (signature := _name_signature(value))
    }


def resolve_entity_directory(
    discovery_plan: dict[str, Any],
    *,
    existing_previews: Iterable[dict[str, Any]] = (),
) -> dict[str, list[dict[str, Any]]]:
    """以稳定名称和别名把模型目录解析成新增、更新或待确认。

    只有同 Type 唯一匹配才能更新；多候选或跨 Type 同名不自动创建，避免把同一对象
    拆成两个节点。待确认目录由运行记录/维护界面处理，不进入模型细化任务。
    """

    previews = [
        deepcopy(item)
        for item in existing_previews
        if isinstance(item, dict)
        and str(item.get("entity_key", "")).partition(":")[0] in ENTITY_TYPES
    ]
    for preview in previews:
        preview.setdefault("type", str(preview["entity_key"]).partition(":")[0])
    preview_names = {
        str(item["entity_key"]): _preview_names(item) for item in previews
    }
    by_key = {str(item["entity_key"]): item for item in previews}
    result: dict[str, list[dict[str, Any]]] = {
        "create": [], "update": [], "unresolved": []
    }
    for raw in discovery_plan.get("entities", []):
        if not isinstance(raw, dict):
            continue
        candidate = deepcopy(raw)
        entity_type = str(candidate.get("type", "")).strip()
        key = str(candidate.get("entity_key", "")).strip()
        if entity_type not in ENTITY_TYPES or key.partition(":")[0] != entity_type:
            candidate["resolution_reason"] = "invalid_type_or_key"
            candidate["record_action"] = "unresolved"
            result["unresolved"].append(candidate)
            continue
        names = _candidate_names(candidate)
        direct = by_key.get(key)
        if isinstance(direct, dict) and direct.get("type") == entity_type:
            matches = [direct]
        else:
            matches = [
                preview
                for preview in previews
                if preview.get("type") == entity_type
                and names.intersection(preview_names[str(preview["entity_key"])])
            ]
        cross_type_matches = [
            preview
            for preview in previews
            if preview.get("type") != entity_type
            and names.intersection(preview_names[str(preview["entity_key"])])
        ]
        if cross_type_matches:
            candidate["record_action"] = "unresolved"
            candidate["resolution_reason"] = "cross_type_name_conflict"
            candidate["candidate_entity_keys"] = [
                str(item["entity_key"]) for item in [*matches, *cross_type_matches]
            ]
            result["unresolved"].append(candidate)
        elif len(matches) == 1:
            existing = matches[0]
            original_name = key.partition(":")[2]
            canonical_name = str(existing.get("primary_name", "")).strip()
            candidate["entity_key"] = str(existing["entity_key"])
            candidate.pop("primary_name", None)
            aliases = list(candidate.get("aliases_add", []))
            if original_name and original_name != canonical_name:
                aliases.append(original_name)
            candidate["aliases_add"] = list(dict.fromkeys(aliases))
            candidate["record_action"] = "update"
            candidate["existing_entity"] = existing
            result["update"].append(candidate)
        elif matches:
            candidate["record_action"] = "unresolved"
            candidate["resolution_reason"] = "multiple_same_type_matches"
            candidate["candidate_entity_keys"] = [
                str(item["entity_key"]) for item in matches
            ]
            result["unresolved"].append(candidate)
        else:
            candidate["record_action"] = "create"
            result["create"].append(candidate)
    return result


def plan_entity_enrichment_jobs(
    discovery_plan: dict[str, Any],
    *,
    source_messages: Iterable[dict[str, Any]],
    existing_previews: Iterable[dict[str, Any]] = (),
) -> list[EntityEnrichmentJob]:
    """为新增与更新各生成至多一个批量任务，并只携带候选引用的原文。"""

    messages = {
        str(item.get("ref")): deepcopy(item)
        for item in source_messages
        if isinstance(item, dict) and item.get("ref")
    }
    preview_list = [deepcopy(item) for item in existing_previews if isinstance(item, dict)]
    resolved = resolve_entity_directory(
        discovery_plan, existing_previews=preview_list
    )
    jobs: list[EntityEnrichmentJob] = []
    for action in ("create", "update"):
        candidates = resolved[action]
        if not candidates:
            continue
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
        jobs.append(
            EntityEnrichmentJob(
                entity_type="all",
                record_action=action,
                system_prompt=ENTITY_ENRICHMENT_SYSTEM_PROMPTS[action],
                payload={
                    "candidates": candidates,
                    "source_messages": [
                        messages[ref] for ref in messages if ref in refs
                    ],
                },
            )
        )
    return jobs
