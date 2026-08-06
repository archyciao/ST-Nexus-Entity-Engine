"""Event/Entity 提取 V2 实验入口。

每批先用一次模型按完整局部故事生成 Narrative Map（短窗口 Event 起点和各 Event
内实体名录）。固定脚本统一上下文人物姓名、换算运行状态并连续切片后，
再并发运行 Event 内容、Entity 自身信息、关系与引用三项任务。V2 不调用旧版
语义边界正则，不生成 Memory，也不把提示词、模型回复或范围说明写入正式数据库候选。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
SRC = ROOT / "src"
for import_path in (TOOLS, SRC):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

import airp_extraction_probe as legacy  # noqa: E402
from world_simulator_schema.event_extraction_v2 import (  # noqa: E402
    ENTITY_FACT_SYSTEM_PROMPT,
    EVENT_CONTENT_SYSTEM_PROMPT,
    NARRATIVE_MAP_SYSTEM_PROMPT,
    RELATION_REFERENCE_SYSTEM_PROMPT,
    build_boundary_plan,
    build_continuity_context,
    build_entity_content_user_prompt,
    build_event_content_user_prompt,
    build_narrative_map_user_prompt,
    build_relation_reference_user_prompt,
    canonicalize_contextual_character_references,
    finalize_roster_fallbacks,
    merge_event_content_with_boundaries,
    merge_roster_fallbacks,
    normalize_narrative_map,
    validate_narrative_map,
)


def narrative_sources(rounds: list[dict[str, Any]]) -> list[dict[str, str]]:
    """保留真实书签，但只把去掉场景标题的 AI 正文交给第一阶段。"""

    return [
        {
            "ref": str(message["ref"]),
            "content": str(message.get("content", "")),
            "narrative": legacy.event_narrative_body(message.get("content", "")),
        }
        for message in legacy.event_source_messages(rounds)
        if legacy.event_narrative_body(message.get("content", ""))
    ]


def fixed_segments_for_prompt(
    boundary_plan: dict[str, Any], rounds: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """按锁定边界裁原文；模型只看到自己的段落和真实消息书签。"""

    result: list[dict[str, Any]] = []
    for segment in legacy.bounded_segments_for_content(boundary_plan, rounds):
        assigned = []
        for message in segment.get("assigned_messages", []):
            if not isinstance(message, dict):
                continue
            content = legacy.event_narrative_body(message.get("content", ""))
            if not content:
                continue
            assigned.append(
                {
                    "ref": str(message.get("ref", "")),
                    "content": content,
                }
            )
        result.append(
            {
                "partition_key": str(segment.get("partition_key", "")),
                "continues_existing_event": str(segment.get("slot", ""))
                in {"forming_existing", "pending_tail"},
                "entity_roster": deepcopy(segment.get("entity_roster", [])),
                "assigned_messages": assigned,
            }
        )
    return result


def normalize_event_content_plan(
    raw_plan: dict[str, Any],
    *,
    boundary_plan: dict[str, Any],
    state: dict[str, Any],
    rounds: list[dict[str, Any]],
) -> dict[str, Any]:
    canonical = canonicalize_contextual_character_references(raw_plan, state)
    merged = merge_event_content_with_boundaries(canonical, boundary_plan)
    normalized = legacy.normalize_event_plan(merged, state, rounds)
    return normalized


def normalize_entity_fact_plan(
    raw_plan: dict[str, Any],
    *,
    state: dict[str, Any],
    rounds: list[dict[str, Any]],
) -> dict[str, Any]:
    canonical = canonicalize_contextual_character_references(raw_plan, state)
    scope_warnings: list[str] = []
    if canonical.get("relations"):
        scope_warnings.append("实体自身信息任务返回了关系；脚本已忽略，关系由独立任务处理")
    canonical["relations"] = []
    for entity in canonical.get("entities", []):
        if not isinstance(entity, dict):
            continue
        evidence = [
            item
            for item in entity.pop("evidence", [])
            if isinstance(item, dict)
            and str(item.get("source_ref", "")).strip()
            and str(item.get("quote", "")).strip()
        ]
        entity["evidence_refs"] = list(
            dict.fromkeys(str(item["source_ref"]) for item in evidence)
        )
        entity["event_link_evidence"] = [
            {
                "source_ref": str(item["source_ref"]),
                "quote": str(item["quote"]),
            }
            for item in evidence
        ]
        if not isinstance(entity.get("facts_patch"), dict):
            entity["facts_patch"] = {}
        entity.pop("character_data_patch", None)
        entity.pop("domain_data_patch", None)
    normalized = legacy.normalize_entity_plan(canonical, state, rounds)
    normalized["script_normalization_warnings"] = scope_warnings
    return normalized


def _reference_candidates(
    raw_plan: dict[str, Any], state: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []
    type_labels = {
        "character": "人物",
        "location": "地点",
        "item": "物品",
        "organization": "组织",
        "skill": "技能",
        "concept": "概念",
    }
    for index, reference in enumerate(raw_plan.get("reference_updates", [])):
        if not isinstance(reference, dict):
            warnings.append(f"reference_updates[{index}] 不是对象，已忽略")
            continue
        key = str(reference.get("entity_key", "")).strip()
        entity_type = key.partition(":")[0]
        name = key.partition(":")[2].strip()
        source_ref = str(reference.get("source_ref", "")).strip()
        evidence_refs = [source_ref] if source_ref else []
        existing = state.get("entity_candidates", {}).get(key)
        if not key or entity_type not in legacy.ALLOWED_ENTITY_TYPES or not name:
            warnings.append(f"reference_updates[{index}] 无法定位实体，已忽略")
            continue
        candidate = {
            "entity_key": key,
            "type": entity_type,
            "aliases_add": [],
            "evidence_refs": evidence_refs,
            "event_link_evidence": [],
        }
        current_location = str(reference.get("current_location_key", "")).strip()
        if current_location:
            candidate["character_data_patch"] = {
                "current_location_key": current_location
            }
        domain_patch: dict[str, Any] = {}
        parent_key = str(reference.get("parent_key", "")).strip()
        if parent_key:
            domain_patch["parent_key"] = parent_key
        if isinstance(reference.get("placement"), dict):
            domain_patch["placement"] = deepcopy(reference["placement"])
        related = [
            str(value).strip()
            for value in reference.get("related_concept_keys", [])
            if str(value).strip()
        ]
        if related:
            domain_patch["related_concepts"] = related
        if domain_patch:
            candidate["domain_data_patch"] = domain_patch
        if not isinstance(existing, dict):
            candidate["primary_name"] = name
            candidate["description"] = (
                f"本批故事中形成明确事实的{type_labels.get(entity_type, '对象')}“{name}”。"
            )
        candidates.append(candidate)
    return candidates, warnings


def normalize_relation_reference_plan(
    raw_plan: dict[str, Any],
    *,
    event_rosters: list[dict[str, Any]],
    state: dict[str, Any],
    rounds: list[dict[str, Any]],
) -> dict[str, Any]:
    canonical = canonicalize_contextual_character_references(raw_plan, state)
    candidates, warnings = _reference_candidates(canonical, state)
    adjusted_rosters = deepcopy(event_rosters)
    known_keys = {
        *state.get("entity_candidates", {}).keys(),
        *(str(candidate.get("entity_key", "")) for candidate in candidates),
        *(
            str(item.get("entity_key", ""))
            for segment in adjusted_rosters
            if isinstance(segment, dict)
            for item in segment.get("entity_roster", [])
            if isinstance(item, dict)
        ),
    }
    actual_location_keys: set[str] = set()
    locations_by_key: dict[str, dict[str, Any]] = {}
    segment_refs = {
        str(segment.get("partition_key", "")): {
            str(item.get("ref", ""))
            for item in segment.get("assigned_messages", [])
            if isinstance(item, dict)
        }
        for segment in adjusted_rosters
        if isinstance(segment, dict)
    }
    for index, location in enumerate(canonical.get("event_locations", [])):
        if not isinstance(location, dict):
            warnings.append(f"event_locations[{index}] 不是对象，已忽略")
            continue
        partition_key = str(location.get("partition_key", ""))
        key = str(location.get("location_key", "")).strip()
        source_ref = str(location.get("source_ref", "")).strip()
        quote = str(location.get("evidence_quote", "")).strip()
        if (
            not legacy.is_entity_key(key, "location")
            or partition_key not in segment_refs
            or source_ref not in segment_refs[partition_key]
            or not quote
        ):
            warnings.append(f"event_locations[{index}] 无法按固定分段定位，已忽略")
            continue
        actual_location_keys.add(key)
        candidate = locations_by_key.setdefault(
            key,
            {
                "entity_key": key,
                "type": "location",
                "primary_name": key.partition(":")[2],
                "description": f"本批故事实际发生的地点“{key.partition(':')[2]}”。",
                "aliases_add": [],
                "evidence_refs": [],
                "event_link_evidence": [],
                "event_location_evidence": [],
            },
        )
        candidate["evidence_refs"].append(source_ref)
        candidate["event_location_evidence"].append(
            {"source_ref": source_ref, "quote": quote}
        )
        known_keys.add(key)
    candidates.extend(locations_by_key.values())

    for candidate in candidates:
        key = str(candidate.get("entity_key", ""))
        entity_type = str(candidate.get("type", ""))
        character_patch = candidate.get("character_data_patch")
        domain_patch = candidate.get("domain_data_patch")
        if entity_type == "character":
            if isinstance(domain_patch, dict) and domain_patch:
                candidate.pop("domain_data_patch", None)
                warnings.append(f"{key} 是 Character；脚本已忽略不适用的 domain_data_patch")
            if isinstance(character_patch, dict):
                location_key = str(character_patch.get("current_location_key", ""))
                if location_key and (
                    not location_key.startswith("location:")
                    or location_key not in actual_location_keys
                ):
                    character_patch.pop("current_location_key", None)
                    warnings.append(
                        f"{key} 的 current_location_key 指向本批未实际发生的 {location_key}；"
                        "脚本已保留地点实体但不写当前地点引用"
                    )
            continue
        if isinstance(character_patch, dict) and character_patch:
            candidate.pop("character_data_patch", None)
            warnings.append(f"{key} 不是 Character；脚本已忽略 character_data_patch")
        if not isinstance(domain_patch, dict):
            continue
        parent_key = str(domain_patch.get("parent_key", ""))
        expected_parent_type = entity_type if entity_type in {"location", "organization"} else ""
        if parent_key and (
            not expected_parent_type
            or not parent_key.startswith(expected_parent_type + ":")
            or parent_key not in known_keys
        ):
            domain_patch.pop("parent_key", None)
            warnings.append(f"{key} 的 parent_key 无法按同 Type 定位；脚本已忽略该引用")
        placement = domain_patch.get("placement")
        if isinstance(placement, dict):
            target_key = str(placement.get("target_key", ""))
            role = str(placement.get("role", ""))
            role_target_ok = (
                role in {"carried", "equipped", "worn"}
                and target_key.startswith("character:")
            ) or (
                role in {"contained", "stored"} and target_key.startswith("item:")
            ) or (
                role == "placed"
                and target_key.startswith(("item:", "location:"))
            )
            if entity_type != "item" or target_key not in known_keys or not role_target_ok:
                domain_patch.pop("placement", None)
                warnings.append(f"{key} 的 placement 目标或作用无法定位；脚本已忽略该引用")
        related = domain_patch.get("related_concepts")
        if isinstance(related, list):
            valid_related = [
                item
                for item in related
                if (
                    (isinstance(item, str) and item.startswith("concept:") and item in known_keys)
                    or (
                        isinstance(item, dict)
                        and str(item.get("concept_key", "")).startswith("concept:")
                        and str(item.get("concept_key", "")) in known_keys
                    )
                )
            ]
            if valid_related != related:
                domain_patch["related_concepts"] = valid_related
                warnings.append(f"{key} 含无法定位的 related_concepts；脚本已忽略这些引用")
    relations: list[dict[str, Any]] = []
    for relation in canonical.get("relations", []):
        if not isinstance(relation, dict):
            continue
        evidence = [
            item
            for item in relation.pop("evidence", [])
            if isinstance(item, dict) and str(item.get("source_ref", "")).strip()
        ]
        relation["evidence_refs"] = list(
            dict.fromkeys(str(item["source_ref"]) for item in evidence)
        )
        relation["event_link_evidence"] = [
            {
                "source_ref": str(item["source_ref"]),
                "quote": str(item.get("quote", "")).strip(),
            }
            for item in evidence
            if str(item.get("quote", "")).strip()
        ]
        relation["directional_states"] = [
            {
                "from_key": item.get("from_key"),
                "toward_key": item.get("toward_key"),
                "summary": item.get("description"),
            }
            for item in relation.pop("directional_views", [])
            if isinstance(item, dict) and str(item.get("description", "")).strip()
        ]
        relation.setdefault("aspects", [])
        relations.append(relation)

    normalized = legacy.normalize_entity_plan(
        {"entities": candidates, "relations": relations},
        state,
        rounds,
    )
    normalized = merge_roster_fallbacks(normalized, adjusted_rosters)
    normalized = legacy.normalize_entity_plan(normalized, state, rounds)
    normalized = finalize_roster_fallbacks(
        normalized,
        existing_entity_keys=state.get("entity_candidates", {}).keys(),
    )
    normalized["script_normalization_warnings"] = warnings
    return normalized


def load_player_identity(chat_jsonl: Path) -> dict[str, str] | None:
    """从酒馆角色身份资料中读取实际姓名；不把“你／主角”当姓名。"""

    fallback_names: list[str] = []
    ignored = {"", "unused", "user", "用户", "you", "你", "我", "主角"}
    with chat_jsonl.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            if line_number > 50:
                break
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            metadata = item.get("chat_metadata")
            if isinstance(metadata, dict):
                persona = metadata.get("last_user_persona")
                if isinstance(persona, dict):
                    name = str(persona.get("name", "")).strip()
                    if name.casefold() not in ignored:
                        return {
                            "primary_name": name,
                            "entity_key": f"character:{name}",
                            "source": "chat_metadata.last_user_persona.name",
                        }
            for field in ("character_name", "user_name"):
                name = str(item.get(field, "")).strip()
                if name.casefold() not in ignored:
                    fallback_names.append(name)
            if item.get("mes") is not None and item.get("is_user"):
                name = str(item.get("name", "")).strip()
                if name.casefold() not in ignored:
                    fallback_names.append(name)
                    break
    if not fallback_names:
        return None
    name = fallback_names[0]
    return {
        "primary_name": name,
        "entity_key": f"character:{name}",
        "source": "chat user identity",
    }


def seed_player_identity(
    state: dict[str, Any], identity: dict[str, str] | None
) -> None:
    if not identity:
        return
    name = str(identity["primary_name"])
    key = str(identity["entity_key"])
    state["_runtime_player_identity"] = deepcopy(identity)
    state["entity_candidates"][key] = {
        "entity_key": key,
        "type": "character",
        "primary_name": name,
        "aliases": [],
        "description": f"当前 AIRP 中由用户扮演的角色{name}。",
        "evidence_refs": [],
        "event_link_evidence": [],
        "character_data": {},
        "stub": True,
    }


def validate_entity_content_plan(
    plan: dict[str, Any],
    *,
    state: dict[str, Any],
    rounds: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    errors, warnings = legacy.validate_entity_plan(plan, rounds, state)
    additions = plan.get("script_roster_fallback_additions")
    if isinstance(additions, list) and additions:
        warnings.append(
            "第二阶段漏回部分第一阶段名录；脚本已按原文证据补成最小候选："
            + "、".join(str(item) for item in additions)
        )
    warnings.extend(
        str(item) for item in plan.get("script_normalization_warnings", [])
    )
    return errors, warnings


def _task_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(task.get("ok")),
        "elapsed_seconds": task.get("elapsed_seconds"),
        "attempts": len(task.get("attempts", [])),
        "fatal_error": task.get("fatal_error"),
    }


def _pre(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, indent=2
    )
    return "<pre>" + html.escape(text) + "</pre>"


def render_record(run: dict[str, Any]) -> str:
    lines = [
        "# Event / Entity V2 分支提取记录",
        "",
        "> 运行日志与正式数据库候选分离；本文件用于复测核查，不是召回数据。",
        "",
        "## 1. 运行概况",
        "",
        _pre(
            {
                key: run.get(key)
                for key in (
                    "started_at",
                    "completed_at",
                    "status",
                    "chat_jsonl",
                    "player_identity",
                    "endpoint",
                    "model",
                    "batch_size",
                    "batch_count",
                    "checkpoint_round_end",
                )
            }
        ),
        "",
        "## 2. 批次记录",
        "",
    ]
    for batch in run.get("batches", []):
        lines.extend(
            [
                f"### 第 {batch.get('batch_number')} 批：轮次 {batch.get('round_start')}—{batch.get('round_end')}",
                "",
                _pre(
                    {
                        "status": batch.get("status"),
                        "elapsed_seconds": batch.get("elapsed_seconds"),
                        "task_summary": {
                            name: _task_summary(task)
                            for name, task in batch.get("tasks", {}).items()
                            if isinstance(task, dict)
                        },
                        "continuity_context": batch.get("continuity_context"),
                        "boundary_plan": batch.get("boundary_plan"),
                        "fixed_segments": batch.get("fixed_segments"),
                    }
                ),
                "",
            ]
        )
        for name in (
            "narrative_map",
            "event_content",
            "entity_facts",
            "relation_references",
        ):
            task = batch.get("tasks", {}).get(name)
            if not isinstance(task, dict):
                continue
            lines.extend(
                [
                    f"#### {name}",
                    "",
                    "<details><summary>完整系统提示词</summary>",
                    "",
                    _pre(task.get("system_prompt", "")),
                    "</details>",
                    "",
                ]
            )
            for attempt in task.get("attempts", []):
                lines.extend(
                    [
                        f"<details><summary>第 {attempt.get('candidate_attempt')} 次完整用户提示词与回复</summary>",
                        "",
                        _pre(
                            {
                                "user_prompt": attempt.get("user_prompt", ""),
                                "raw": attempt.get("raw", ""),
                                "api": attempt.get("api", {}),
                                "parse_error": attempt.get("parse_error"),
                                "validation_errors": attempt.get(
                                    "validation_errors", []
                                ),
                                "warnings": attempt.get("warnings", []),
                                "parsed_plan": attempt.get("parsed_plan"),
                            }
                        ),
                        "</details>",
                        "",
                    ]
                )
        if batch.get("operations") is not None:
            lines.extend(
                [
                    "#### 固定脚本提交结果",
                    "",
                    _pre(
                        {
                            "operations": batch.get("operations", []),
                            "script_warnings": batch.get("script_warnings", []),
                            "network_validation": batch.get("network_validation"),
                            "database_state_summary": batch.get(
                                "database_state_summary", {}
                            ),
                        }
                    ),
                    "",
                ]
            )
    lines.extend(
        [
            "## 3. 最终结果",
            "",
            "### 数据库候选快照",
            "",
            _pre(run.get("final_database", {})),
            "",
            "### 正式 Entity 网络与校验",
            "",
            _pre(
                {
                    "validation": run.get("final_network_validation", {}),
                    "entities": run.get("final_network", []),
                }
            ),
            "",
            "### 最小恢复检查点（运行记录）",
            "",
            _pre(run.get("checkpoint_state", {})),
            "",
        ]
    )
    return "\n".join(lines)


def persist_record(run: dict[str, Any], output: Path, api_key: str) -> None:
    rendered = render_record(run)
    if api_key and api_key in rendered:
        raise RuntimeError("安全检查失败：记录内容意外包含 API 密钥")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".tmp",
        prefix=output.name + ".",
        dir=output.parent,
        delete=False,
    ) as temporary:
        temporary.write(rendered)
        temporary_path = Path(temporary.name)
    for attempt in range(6):
        try:
            os.replace(temporary_path, output)
            break
        except PermissionError:
            if attempt == 5:
                temporary_path.unlink(missing_ok=True)
                raise
            time.sleep(0.2 * (2**attempt))


def _failed_task(
    name: str,
    system_prompt: str,
    error: Exception,
    started: float,
) -> dict[str, Any]:
    return {
        "task": name,
        "ok": False,
        "fatal_error": f"未处理异常：{error}",
        "system_prompt": system_prompt,
        "attempts": [],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise RuntimeError(f"环境变量 {args.api_key_env} 中没有 API 密钥")
    chat_jsonl = Path(args.chat_jsonl).resolve()
    output = Path(args.output).resolve()
    state = legacy.initial_unified_state()
    player_identity = load_player_identity(chat_jsonl)
    seed_player_identity(state, player_identity)
    run: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "status": "running",
        "chat_jsonl": str(chat_jsonl),
        "player_identity": deepcopy(player_identity),
        "endpoint": args.endpoint,
        "model": args.model,
        "batch_size": args.batch_size,
        "batch_count": args.batches,
        "checkpoint_round_end": 0,
        "batches": [],
        "final_database": legacy.database_report_view(state),
        "checkpoint_state": legacy.runtime_checkpoint(state),
        "final_network": [],
        "final_network_validation": {},
    }
    persist_record(run, output, api_key)
    rounds_iterator = iter(legacy.iter_rounds(chat_jsonl))
    processed_narratives: dict[str, str] = {}

    for batch_number in range(1, args.batches + 1):
        rounds = legacy.take_batch(rounds_iterator, args.batch_size)
        if not rounds:
            break
        batch_started = time.perf_counter()
        snapshot = deepcopy(state)
        sources = narrative_sources(rounds)
        batch: dict[str, Any] = {
            "batch_number": batch_number,
            "round_start": rounds[0]["round"],
            "round_end": rounds[-1]["round"],
            "status": "narrative_map_running",
            "tasks": {},
        }
        run["batches"].append(batch)
        persist_record(run, output, api_key)
        print(
            f"第 {batch_number} 批（轮次 {rounds[0]['round']}—{rounds[-1]['round']}）开始 Narrative Map",
            flush=True,
        )

        existing_tail = legacy.state_view_for_boundary(snapshot)
        entity_previews = legacy.related_entity_context(
            snapshot, rounds, assistant_only=True
        )
        if player_identity:
            entity_previews["current_player_character"] = {
                "entity_key": player_identity["entity_key"],
                "primary_name": player_identity["primary_name"],
                "note": "AI 正文中的第二人称主角称呼使用这个实际姓名，不把你或主角保存成名称。",
            }
        story_text = "".join(source["narrative"] for source in sources)
        continuity_context = build_continuity_context(
            existing_event_tail=existing_tail,
            source_text_by_ref=processed_narratives,
            current_story=story_text,
        )
        batch["continuity_context"] = deepcopy(continuity_context)
        map_prompt = build_narrative_map_user_prompt(
            batch_number=batch_number,
            story_text=story_text,
            continuity_context=continuity_context,
            existing_entity_previews=entity_previews,
        )
        map_result = legacy.run_model_task(
            task="narrative_map",
            endpoint=args.endpoint,
            api_key=api_key,
            model=args.model,
            system_prompt=NARRATIVE_MAP_SYSTEM_PROMPT,
            user_prompt=map_prompt,
            timeout=args.timeout,
            max_tokens=args.map_max_tokens,
            thinking_mode=args.map_thinking,
            response_format=args.response_format,
            stream_idle_timeout=args.stream_idle_timeout,
            content_start_timeout=args.content_start_timeout,
            candidate_attempt_limit=args.candidate_attempt_limit,
            transport_attempt_limit=args.transport_attempt_limit,
            normalizer=lambda plan, s=snapshot, m=sources, c=continuity_context: normalize_narrative_map(
                plan, s, m, c
            ),
            validator=lambda plan, m=sources: validate_narrative_map(plan, m),
        )
        batch["tasks"]["narrative_map"] = map_result
        persist_record(run, output, api_key)
        print(
            f"  Narrative Map 已返回：{'通过' if map_result.get('ok') else '失败'}",
            flush=True,
        )
        if not map_result.get("ok"):
            batch["status"] = "failed_before_slicing"
            batch["elapsed_seconds"] = round(time.perf_counter() - batch_started, 3)
            run["status"] = "failed"
            persist_record(run, output, api_key)
            return run

        narrative_map = map_result["plan"]
        boundary_plan = legacy.normalize_boundary_plan(
            build_boundary_plan(narrative_map, sources), snapshot, rounds
        )
        boundary_errors = legacy.validate_boundary_plan(
            boundary_plan, snapshot, rounds
        )
        if boundary_errors:
            batch["boundary_errors"] = boundary_errors
            batch["status"] = "failed_before_stage2"
            batch["elapsed_seconds"] = round(time.perf_counter() - batch_started, 3)
            run["status"] = "failed"
            persist_record(run, output, api_key)
            return run
        fixed_segments = fixed_segments_for_prompt(boundary_plan, rounds)
        batch["boundary_plan"] = boundary_plan
        batch["fixed_segments"] = fixed_segments
        batch["status"] = "stage2_running"
        persist_record(run, output, api_key)

        event_prompt = build_event_content_user_prompt(
            batch_number=batch_number,
            fixed_segments=fixed_segments,
            existing_event_tail=existing_tail,
        )
        entity_prompt = build_entity_content_user_prompt(
            batch_number=batch_number,
            fixed_segments=fixed_segments,
            existing_entity_previews=entity_previews,
        )
        relation_prompt = build_relation_reference_user_prompt(
            batch_number=batch_number,
            fixed_segments=fixed_segments,
            existing_entity_previews=entity_previews,
        )
        specs: dict[str, dict[str, Any]] = {
            "event_content": {
                "system_prompt": EVENT_CONTENT_SYSTEM_PROMPT,
                "user_prompt": event_prompt,
                "max_tokens": args.event_max_tokens,
                "thinking_mode": args.event_thinking,
                "normalizer": lambda plan, b=boundary_plan, s=snapshot, r=rounds: normalize_event_content_plan(
                    plan, boundary_plan=b, state=s, rounds=r
                ),
                "validator": lambda plan, s=snapshot, r=rounds: legacy.validate_event_plan(
                    plan, s, r
                ),
            },
            "entity_facts": {
                "system_prompt": ENTITY_FACT_SYSTEM_PROMPT,
                "user_prompt": entity_prompt,
                "max_tokens": args.entity_max_tokens,
                "thinking_mode": args.entity_thinking,
                "normalizer": lambda plan, s=snapshot, r=rounds: normalize_entity_fact_plan(
                    plan, state=s, rounds=r
                ),
                "validator": lambda plan, s=snapshot, r=rounds: validate_entity_content_plan(
                    plan, state=s, rounds=r
                ),
            },
            "relation_references": {
                "system_prompt": RELATION_REFERENCE_SYSTEM_PROMPT,
                "user_prompt": relation_prompt,
                "max_tokens": args.relation_max_tokens,
                "thinking_mode": args.relation_thinking,
                "normalizer": lambda plan, event_rosters=fixed_segments, s=snapshot, r=rounds: normalize_relation_reference_plan(
                    plan, event_rosters=event_rosters, state=s, rounds=r
                ),
                "validator": lambda plan, s=snapshot, r=rounds: validate_entity_content_plan(
                    plan, state=s, rounds=r
                ),
            },
        }

        def execute(name: str) -> dict[str, Any]:
            spec = specs[name]
            return legacy.run_model_task(
                task=name,
                endpoint=args.endpoint,
                api_key=api_key,
                model=args.model,
                timeout=args.timeout,
                stream_idle_timeout=args.stream_idle_timeout,
                content_start_timeout=args.content_start_timeout,
                candidate_attempt_limit=args.candidate_attempt_limit,
                transport_attempt_limit=args.transport_attempt_limit,
                response_format=args.response_format,
                **spec,
            )

        print("  固定切片完成；Event、Entity、关系与引用三路并发开始", flush=True)
        with ThreadPoolExecutor(max_workers=max(1, min(3, args.stage2_workers))) as pool:
            futures = {pool.submit(execute, name): name for name in specs}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = _failed_task(
                        name,
                        specs[name]["system_prompt"],
                        exc,
                        batch_started,
                    )
                batch["tasks"][name] = result
                persist_record(run, output, api_key)
                print(
                    f"  {name} 已返回：{'通过' if result.get('ok') else '失败'}",
                    flush=True,
                )

        if not all(batch["tasks"].get(name, {}).get("ok") for name in specs):
            batch["status"] = "failed_before_commit"
            batch["elapsed_seconds"] = round(time.perf_counter() - batch_started, 3)
            run["status"] = "failed"
            persist_record(run, output, api_key)
            return run

        event_plan = batch["tasks"]["event_content"]["plan"]
        entity_plan = batch["tasks"]["entity_facts"]["plan"]
        relation_plan = batch["tasks"]["relation_references"]["plan"]
        candidate_state, operations, _ = legacy.apply_event_candidate(
            snapshot, event_plan, rounds
        )
        candidate_state = legacy.apply_entity_candidates(candidate_state, entity_plan)
        candidate_state = legacy.apply_entity_candidates(candidate_state, relation_plan)
        script_warnings = [
            *legacy.reconcile_entity_event_links(candidate_state, rounds),
            *legacy.reconcile_source_inventory(candidate_state, rounds),
            *legacy.ensure_minimal_entity_nodes(candidate_state),
        ]
        network, network_report = legacy.materialize_network(candidate_state)
        batch["operations"] = operations
        batch["script_warnings"] = list(dict.fromkeys(script_warnings))
        batch["network_validation"] = network_report
        batch["database_state_summary"] = legacy.state_report_summary(
            candidate_state
        )
        batch["elapsed_seconds"] = round(time.perf_counter() - batch_started, 3)
        if not network_report.get("valid"):
            batch["status"] = "network_validation_failed"
            run["status"] = "failed"
            run["final_network"] = network
            run["final_network_validation"] = network_report
            persist_record(run, output, api_key)
            return run

        state = candidate_state
        processed_narratives.update(
            {
                str(source["ref"]): str(source["narrative"])
                for source in sources
            }
        )
        batch["status"] = "committed"
        run["checkpoint_round_end"] = rounds[-1]["round"]
        run["final_database"] = legacy.database_report_view(state)
        run["checkpoint_state"] = legacy.runtime_checkpoint(state)
        run["final_network"] = network
        run["final_network_validation"] = network_report
        persist_record(run, output, api_key)
        print(
            f"  第 {batch_number} 批已原子提交；正式网络 {network_report.get('entity_count')} 项",
            flush=True,
        )

    final_network, final_report = legacy.materialize_network(state)
    run["status"] = "completed" if final_report.get("valid") else "failed"
    run["completed_at"] = datetime.now(timezone.utc).isoformat()
    run["final_database"] = legacy.database_report_view(state)
    run["checkpoint_state"] = legacy.runtime_checkpoint(state)
    run["final_network"] = final_network
    run["final_network_validation"] = final_report
    persist_record(run, output, api_key)
    return run


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="先生成故事窗口 Event 起点与名录，再并发提取 Event、Entity、关系与引用。"
    )
    parser.add_argument("chat_jsonl")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="OPENCODE_API_KEY")
    parser.add_argument("--output", required=True)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--batches", type=int, default=5)
    parser.add_argument("--stage2-workers", type=int, choices=(1, 2, 3), default=3)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--stream-idle-timeout", type=float, default=90.0)
    parser.add_argument("--content-start-timeout", type=float, default=90.0)
    parser.add_argument("--map-max-tokens", type=int, default=12288)
    parser.add_argument("--event-max-tokens", type=int, default=24576)
    parser.add_argument("--entity-max-tokens", type=int, default=32768)
    parser.add_argument("--relation-max-tokens", type=int, default=24576)
    parser.add_argument("--candidate-attempt-limit", type=int, default=1)
    parser.add_argument("--transport-attempt-limit", type=int, default=1)
    thinking_choices = ("default", "off", "low", "high", "max")
    parser.add_argument("--map-thinking", choices=thinking_choices, default="off")
    parser.add_argument("--event-thinking", choices=thinking_choices, default="off")
    parser.add_argument("--entity-thinking", choices=thinking_choices, default="off")
    parser.add_argument("--relation-thinking", choices=thinking_choices, default="off")
    parser.add_argument(
        "--response-format", choices=("text", "json_object"), default="text"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run = run_probe(args)
    return 0 if run.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
