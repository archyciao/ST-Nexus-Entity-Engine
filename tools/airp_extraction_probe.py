"""AIRP Event、Memory 与 Entity 同批提取复测工具。

工具既可只运行客观 Event 边界短回归，也可把同一份不可变叙事快照分别交给
Event、主观 Memory 和其他 Entity 三个任务。三项可以并发返回候选；固定代码
随后完成 Event 三状态流转、Memory 来源绑定、稳定 Entity ID 分配、
Reference/Index 投影和封闭网络校验。

本工具只保存模型的正式回复，不请求、读取或归档隐藏思维过程。一次运行始终覆盖
写入同一份 Markdown 记录，避免每批产生一组零散文件。
"""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import html
import json
import os
import re
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
SRC = ROOT / "src"
for import_path in (TOOLS, SRC):
    if str(import_path) not in sys.path:
        sys.path.insert(0, str(import_path))

from event_segmentation_probe import (  # noqa: E402
    apply_boundary_plan,
    apply_content_plan,
    batch_messages,
    bounded_segments_for_content,
    call_chat_completion,
    evaluate_state_against_gold,
    extract_json_object,
    initial_state,
    iter_rounds,
    load_gold_fixture,
    normalize_boundary_plan,
    quote_in_text,
    state_view_for_boundary,
    take_batch,
    validate_boundary_plan,
    validate_content_plan,
)
from world_simulator_schema.entity_ids import new_entity_id, new_local_id  # noqa: E402
from world_simulator_schema.entity_network import (  # noqa: E402
    EntityNetworkValidator,
    rebuild_derived_indexes,
)


ALLOWED_ENTITY_TYPES = {
    "character",
    "location",
    "item",
    "organization",
    "skill",
    "concept",
}
ACQUISITION_MODES = {
    "witnessed_event",
    "heard_from_character",
    "read_from_record",
    "inferred_from_events",
    "generated_summary",
    "implanted_by_event",
    "unknown_from_event",
}
LOCATION_ROLES = {"primary", "start", "transit", "end"}
INVENTORY_ROLES = {"carried", "equipped", "worn"}
VISIBILITIES = {"public", "private", "secret"}
SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


EVENT_ATTENTION_GUIDE = """
输出前静默完成以下交叉检查，不要写出检查过程：
1. 为可能的每段在内部写一张“局部事件卡”：主要发起者—正在推进的直接行动、互动及对象—
   已经得到的结果或当前落点。采用可单独回忆的局部经历层级；长期目标和宏观阶段可以跨越
   多张事件卡。
2. 逐处比较相邻内容。当左侧行动或互动已经形成可陈述的结果、决定或阶段落点，右侧又以
   新的发起动作开启，并明显转向另一直接目的、主要互动对象、处理对象或因果问题时，把右侧
   开头作为候选边界。若右侧只是左侧结果的直接反应、收尾或同一行动的下一步，继续原段。
3. 检查“中断后恢复”：短暂问答、插话或局部动作若嵌在仍持续的主要活动中，随后又回到该
   活动，通常留在同段；若原活动已经落地，后来者、任务或行动另有展开和结果，再考虑新段。
4. 校正两个方向：同一人物、地点、时间、长期目标或前后因果联系，本身不要求合并；换说话
   人、单个动作、连续问答或短暂转场，本身也不形成边界。若一个标题需要用“并、随后、又、
   以及”等串联两个各有进展或结果的局部事件卡，检查是否过粗；若相邻标题只是同一行动的
   步骤、问答往返或直接余波，检查是否过碎。
5. 粒度参照：技术员检查故障、换件、重启并确认设备恢复，是一段维修经历；设备恢复后，
   快递员到场递交一份需当事人另行处理的文件，通常开始下一段。编辑写稿时简短回答同事的
   问题后继续写稿，可以留在原段；交稿完成后开始接受采访，通常转入下一段。
6. 最后按来源清点本批有实际作用的人物、行动、互动、因果、物品、结果和未决事项，确保均
   归入相应事实段且没有同义重复。一批可以没有新边界，也可以有多个边界，不为数量调整结果。
""".strip()


EVENT_SYSTEM_PROMPT = f"""
你负责一次完成连续 AIRP 叙事的客观 Event 分段和内容更新。Event 是可独立命名、
理解、检索和回忆的小故事单元，不等于回合，也不等于长期目标。当前采用“局部经历”
粒度：一个长期目标或宏观阶段可以包含若干 Event；每项 Event 可以包含形成同一局部结果
所需的起因、过程、结果和直接余波。

相关 Entity 和脚本预检清单只用于身份消歧、来源核对和防漏，不能覆盖或补写原文。
不要展示分析过程。

普通边界只能出现在本批新增消息中，不能回到已处理旧消息内部重新切分。同一消息
先收尾旧 Event、再开始新 Event 时，用新 Event 的原文开头定位。结果后的直接余波
可以留在原 Event。old_forming_disposition 只能是：
- absent：首次整理，没有旧 Event；
- keep_distinct：旧生成中仍是独立 Event，本批继续它，必要时再开启新 Event；
- merge_into_pending：旧生成中全部是待定稿余波，固定脚本将整体合并；新边界在本批中。

每个受影响 Event 都返回截至本批结束的完整 title、description，并返回本批新增事实段：
- description 用一至两句客观文字写清主要人物、情境、局部行动和结果或当前落点，便于
  快速定位和向量检索；理想为六十至一百二十个汉字，超过一百六十字只记软性提示，不为长度重试；
- event_beats_add 是本批新增的客观事实段，每段保留来源，覆盖有实际作用的出场人物、
  行动或转折、直接因果、重要物品或命令、结果或明确未决事项；按叙事顺序写，避免同义重复；
- title、description 是完整替换稿，必须保留仍有效的旧事实；其他数组只返回本批增删量；
- 运行中的保真故事稿由固定脚本按来源移动、去重并拼接 event_beats，模型无需重写完整摘要；
- 关键言语必须是原话，关键动作必须是适合回忆闪回的原文特写。只返回本批新候选；
  默认每项 Event 最重要的三条，零至两条也可以，通常不超过五条，不解释、不凑数；
- 若旧特写确实误归或不再值得保留，只把其 id 放入 retire_detail_ids；其余旧特写无需重发。

Event 不再枚举参与者、地点和相关 Entity；这些对象由独立 Entity 任务提取一次，再由固定脚本
按同一来源片段挂接。正式 ID、状态流转、合并、去重、Memory 换绑和索引也由固定脚本处理。

只输出一个 JSON 对象，不输出解释或分析：
{{
  "old_forming_disposition": "absent | keep_distinct | merge_into_pending",
  "decision_reason": "一句边界理由；没有新边界时也说明为什么保持连续",
  "boundary_uncertainties": ["仅列确实含混之处"],
  "segments": [{{
    "slot": "pending_tail | forming_existing | new_1 | new_2 ...",
    "source_refs": ["本批消息引用"],
    "start_anchors": [{{
      "source_ref": "本段开头所在消息",
      "start_quote": "该消息中的开头原文短引"
    }}]
  }}],
  "event_updates": [{{
    "slot": "与 segments 中受影响 slot 相同",
    "title": "简短标题",
    "description": "完整检索说明",
    "event_beats_add": [{{
      "content": "本批新增的客观事实段",
      "source_refs": ["本批消息引用"]
    }}],
    "event_time": {{
      "start_time": {{"expression": "原文时间表达", "precision": "exact | approximate"}},
      "end_time": {{"expression": "原文时间表达", "precision": "exact | approximate"}}
    }},
    "unresolved_add": [],
    "unresolved_resolve": [],
    "retire_detail_ids": [],
    "new_key_details": [{{
      "kind": "statement | action",
      "content": "原话或动作原文特写",
      "actor_key": "character:主要名称；不适用则空字符串",
      "source_refs": ["本批消息引用"]
    }}]
  }}]
}}

event_time 没有叙事依据时填 null；形成中的 Event 没有结束时间时省略 end_time。
每条新增消息至少归属一个 segment；同一消息内切分时相邻 segment 可共同引用该消息，
但后一段必须用 start_anchors 定位。每个 slot 只出现一次。
""".strip()


MEMORY_SYSTEM_PROMPT = """
你只提取本批叙事中具体 Character 实际形成的主观 Memory，不重判 Event 边界，也不写客观世界摘要。

只为亲历、观察、听闻、阅读或明确推断的角色记录其实际认知。玩家角色的内心、未说出口
的想法和只对读者展示的信息不能泄露给其他角色。Memory 可以片面、误解或带情绪，
但必须说明是谁如何获得，并用本批原文书签和短引作依据。没有值得跨回合保留的认知时
返回空数组；不要把每句话都做成 Memory，也不要复制整段 Event 摘要。每批最多返回
三条最值得跨回合保留的 Memory；同一 Owner 在同一经历中形成的相互关联认知应合并
为一条，用多项 evidence 支撑，避免拆成若干近义条目。

稳定候选键使用 type:主要名称；正式 Memory ID、Event 归属和 Relation 引用由固定脚本
根据来源书签处理。只输出 JSON，不解释：
{
  "memories": [{
    "owner_key": "character:主要名称",
    "description": "该角色实际形成的主观认知",
    "acquisition_mode": "witnessed_event | heard_from_character | read_from_record | inferred_from_events | generated_summary | implanted_by_event | unknown_from_event",
    "importance": "recent | core",
    "evidence": [{"source_ref": "本批消息引用", "quote": "可核对的原文短引"}],
    "related_character_keys": ["character:主要名称"],
    "related_relation_pairs": [["character:甲", "character:乙"]]
  }]
}
""".strip()


ENTITY_SYSTEM_PROMPT = """
你只提取本批叙事中新增或发生变化的 Entity 候选，包括 Character、Location、Item、
Organization、Skill、Concept，以及两个 Character 之间的 Character Relation。
不要重判 Event，不生成 Memory，不为凑齐类型而虚构对象。名称不同但明确是同一对象时
使用既有稳定候选键 type:主要名称，并把本批出现的新称呼放入 aliases_add。只返回本批
有证据的新建或更新候选，未变化字段省略，未变化对象不重发。
脚本预检清单只提示明确的说话人、场景头和既有对象精确提及，用它复核防漏，但不要把
“被提到”自动当成“实际参与”。同一名称若在原文中确实同时代表地点与组织，可以分别
返回两个有证据的候选；若只是类型拿不准，不要为保险同时复制成多个 Type。

每个候选都用 evidence_refs 标明本批依据。若对象在某段原文中实际在场、发生、被使用、
被交付、被施展或直接影响事件，再用 event_link_evidence 给出该处原文短引；仅被提及、
计划前往或作为背景知识时不要填写。Character 的该项依据表示实际参与，Location 表示
实际发生或经过，其他类型表示实际涉及。Location 可附 roles；没有明确移动角色时省略，
脚本按 primary 处理。固定脚本据此把对象挂到已经切好的 Event，模型不需要读取正式 Event ID。
每个短引使用能够定位该对象实际参与的最短连续原文，通常八至三十个汉字；roles 只用于
Location，其他 Type 省略。Entity description 使用一句紧凑说明，不复述全部原文。

客观关系事实放入 aspects；某一方当前如何看待或对待另一方放入 directional_states。
师徒、敌对、亲属等不对称角色分别写在 participant_roles 中。所有 kind、roles、tags
使用简短 snake_case 英文标记。Character 的长期资料与当前状态要区分；只有叙事足以
支持时才填写 character_data_patch。它只含本批确认发生变化的字段；内部对象可以只写
改变项，某个数组一旦出现则表示该数组的当前完整值。缺证据的字段省略，不猜测。

只输出 JSON，不解释：
{
  "entities": [{
    "entity_key": "character/location/item/organization/skill/concept:主要名称",
    "type": "character | location | item | organization | skill | concept",
    "primary_name": "新候选必填；更新既有候选时可省略",
    "aliases_add": ["本批确认属于同一对象的新称呼"],
    "description": "新候选必填；当前说明确有变化时返回完整替换稿",
    "evidence_refs": ["本批消息引用"],
    "event_link_evidence": [{
      "source_ref": "对象实际参与或涉及的本批消息引用",
      "quote": "可核对的原文短引",
      "roles": ["仅 Location 可用：primary | start | transit | end"]
    }],
    "character_data_patch": {
      "profile": {"species": "", "gender": "", "age_description": "", "background": "", "appearance": ""},
      "state": {"physical_condition": "", "mental_condition": "", "current_emotions": []},
      "personality": {"summary": "", "traits": [], "behavioral_tendencies": []},
      "motivations": ["长期推动行动的动机"],
      "preferences": [{"attitude": "like | dislike", "subject": "", "description": "", "related_entity_key": ""}],
      "objectives": [{"description": "", "horizon": "short_term | medium_term", "related_entity_keys": []}],
      "inventory": [{"item_key": "item:主要名称", "roles": ["carried | equipped | worn"]}],
      "skills": [{"skill_key": "skill:主要名称", "proficiency_description": ""}],
      "current_location_key": "location:主要名称"
    }
  }],
  "relations": [{
    "participant_keys": ["character:甲", "character:乙"],
    "description": "双方当前关系的完整简述",
    "evidence_refs": ["本批消息引用"],
    "aspects": [{
      "kind": "master_disciple",
      "description": "客观关系事实",
      "status": "active | ended",
      "visibility": "public | private | secret",
      "participant_roles": [
        {"character_key": "character:甲", "roles": ["disciple"]},
        {"character_key": "character:乙", "roles": ["master"]}
      ]
    }],
    "directional_states": [{
      "from_key": "character:甲",
      "toward_key": "character:乙",
      "summary": "甲当前如何看待或对待乙",
      "tags": ["respectful"]
    }]
  }]
}

非 Character 省略 character_data_patch。新出场但暂时没有姓名的对象，可用稳定、可区分的
叙事称呼作主要名称；以后由实体匹配流程合并，不能因此漏掉其行动或物品。可选字段没有内容
时直接省略，不要输出整套空白结构。
""".strip()


def initial_unified_state() -> dict[str, Any]:
    """建立兼容旧 Event 状态机并可累积全部候选的运行状态。"""

    state = initial_state()
    state["next_beat_number"] = 1
    state.update(
        {
            "entity_candidates": {},
            "relation_candidates": {},
            "id_maps": {
                "entity": {},
                "event": {},
                "memory": {},
                "relation": {},
                "local": {},
            },
            "warnings": [],
            "run_timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    return state


def entity_type_from_key(key: str) -> str | None:
    """读取 ``type:name`` 候选键中的 Type。"""

    entity_type, separator, name = str(key).partition(":")
    if not separator or not name.strip() or entity_type not in ALLOWED_ENTITY_TYPES:
        return None
    return entity_type


def is_entity_key(key: Any, expected_type: str | None = None) -> bool:
    """检查批次内候选键，并可限制目标 Type。"""

    if not isinstance(key, str):
        return False
    entity_type = entity_type_from_key(key)
    return entity_type is not None and (
        expected_type is None or entity_type == expected_type
    )


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _ensure_event_beats(state: dict[str, Any]) -> None:
    """兼容旧检查点：把既有故事稿保留成一个带来源的事实段。"""

    state.setdefault("next_beat_number", 1)
    for event in state.get("events", []):
        event.setdefault("event_beats", [])
        if event["event_beats"] or not str(event.get("story_summary", "")).strip():
            continue
        event["event_beats"] = [
            {
                "id": f"beat_legacy_{event.get('id', 'unknown')}",
                "content": str(event["story_summary"]).strip(),
                "source_refs": list(event.get("source_refs", [])),
            }
        ]


def _beat_signature(beat: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    return (
        str(beat.get("content", "")).strip(),
        tuple(str(ref) for ref in _as_list(beat.get("source_refs"))),
    )


def _merge_event_beats(
    existing: Iterable[dict[str, Any]], incoming: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """按“内容 + 来源”去重并保持叙事顺序。"""

    result: list[dict[str, Any]] = []
    signatures: set[tuple[str, tuple[str, ...]]] = set()
    for raw in [*existing, *incoming]:
        if not isinstance(raw, dict):
            continue
        beat = deepcopy(raw)
        signature = _beat_signature(beat)
        if not signature[0] or signature in signatures:
            continue
        signatures.add(signature)
        result.append(beat)
    return result


def _carry_merged_event_beats(
    boundary_state: dict[str, Any],
    source_state: dict[str, Any],
    plan: dict[str, Any],
    slot_event_ids: dict[str, str],
) -> None:
    """旧生成中并入待定稿时，由脚本整体迁移事实段。"""

    if plan.get("old_forming_disposition") != "merge_into_pending":
        return
    pending = next(
        (
            event
            for event in source_state.get("events", [])
            if event.get("status") == "pending_finalization"
        ),
        None,
    )
    forming = next(
        (
            event
            for event in source_state.get("events", [])
            if event.get("status") == "forming"
        ),
        None,
    )
    target_id = slot_event_ids.get("pending_tail")
    target = next(
        (event for event in boundary_state.get("events", []) if event.get("id") == target_id),
        None,
    )
    if pending is None or forming is None or target is None:
        return
    target["event_beats"] = _merge_event_beats(
        pending.get("event_beats", []), forming.get("event_beats", [])
    )


def _append_warning(state: dict[str, Any], warning: str) -> None:
    if warning not in state["warnings"]:
        state["warnings"].append(warning)


def related_entity_context(
    state: dict[str, Any], rounds: list[dict[str, Any]] | None = None, limit: int = 24
) -> dict[str, Any]:
    """只加载本批直接提及和尾部相关候选，避免世界越大、提示词越无限增长。"""

    narrative = "\n".join(
        message["content"]
        for message in batch_messages(rounds or [])
    )
    tail_keys: set[str] = set()
    for event in state.get("events", [])[-2:]:
        tail_keys.update(event.get("participants", []))
        tail_keys.update(event.get("locations", []))
        tail_keys.update(event.get("related_entity_keys", []))

    scored: list[tuple[int, int, str, dict[str, Any]]] = []
    for insertion, (key, candidate) in enumerate(
        state.get("entity_candidates", {}).items()
    ):
        names = [
            str(candidate.get("primary_name", "")).strip(),
            *[str(alias).strip() for alias in candidate.get("aliases", [])],
        ]
        score = 10 * sum(bool(name and name in narrative) for name in names)
        if key in tail_keys:
            score += 6
        if candidate.get("stub"):
            score -= 1
        scored.append((score, insertion, key, candidate))
    selected = sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[
        : max(1, limit)
    ]
    selected_keys = {item[2] for item in selected}
    entities = [
        {
            "entity_key": key,
            "description": candidate.get("description", ""),
            "aliases": candidate.get("aliases", []),
        }
        for _, _, key, candidate in selected
    ]
    relations = []
    for candidate in state.get("relation_candidates", {}).values():
        participants = set(candidate.get("participant_keys", []))
        if participants & selected_keys or any(
            key.split(":", 1)[-1] in narrative for key in participants
        ):
            relations.append(
                {
                    "participant_keys": candidate.get("participant_keys", []),
                    "description": candidate.get("description", ""),
                }
            )
    return {
        "selection_rule": "本批直接提及优先，其次尾部 Event 相关；最多加载少量候选简介",
        "entities": entities,
        "relations": relations,
    }


SCENE_HEADER_RE = re.compile(r"^\[(?:场景时间|时间)[^\n\]]*\]", re.MULTILINE)


def source_inventory(
    state: dict[str, Any], rounds: list[dict[str, Any]]
) -> dict[str, Any]:
    """用固定规则列出明确来源线索，只做防漏提醒，不替模型猜测语义。"""

    messages = batch_messages(rounds)
    speakers = _unique(
        str(message.get("speaker", "")).strip()
        for message in messages
        if message.get("role") == "user"
        and str(message.get("speaker", "")).strip()
    )
    scene_headers: list[dict[str, str]] = []
    for message in messages:
        for match in SCENE_HEADER_RE.finditer(str(message.get("content", ""))):
            scene_headers.append(
                {"source_ref": str(message["ref"]), "header": match.group(0)}
            )

    narrative = "\n".join(str(message.get("content", "")) for message in messages)
    existing_mentions: list[dict[str, Any]] = []
    for key, candidate in state.get("entity_candidates", {}).items():
        names = _unique(
            name
            for name in [
                str(candidate.get("primary_name", "")).strip(),
                *[str(alias).strip() for alias in candidate.get("aliases", [])],
            ]
            if name
        )
        matched = [name for name in names if name in narrative]
        if matched:
            existing_mentions.append(
                {"entity_key": key, "matched_names": matched}
            )
    return {
        "nature": "固定脚本的来源清单；是检查线索，不是自动参与者或自动分段结论",
        "player_speakers": speakers,
        "scene_headers": scene_headers,
        "exact_existing_entity_mentions": existing_mentions,
    }


def build_event_prompt(
    state: dict[str, Any], rounds: list[dict[str, Any]], batch_number: int
) -> str:
    """给 Event 任务完整尾部正文、相关实体简介与本批原文。"""

    tail = state_view_for_boundary(state)
    events_by_id = {event["id"]: event for event in state["events"]}
    for field in ("pending_event", "forming_event"):
        preview = tail.get(field)
        if isinstance(preview, dict) and preview.get("id") in events_by_id:
            event = events_by_id[preview["id"]]
            preview.pop("story_summary", None)
            preview["recent_event_beats"] = deepcopy(
                event.get("event_beats", [])[-8:]
            )
            preview["key_details"] = deepcopy(event.get("key_details", []))
            preview["event_time"] = deepcopy(event.get("event_time"))
            preview["location_occurrences"] = deepcopy(
                event.get("location_occurrences", [])
            )
            preview["related_entity_keys"] = deepcopy(
                event.get("related_entity_keys", [])
            )
    payload = {
        "batch_number": batch_number,
        "source_inventory": source_inventory(state, rounds),
        "existing_event_tail": tail,
        "related_entities": related_entity_context(state, rounds),
        "new_messages": batch_messages(rounds),
    }
    return (
        "处理同一批次的客观 Event。\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n\n"
        + EVENT_ATTENTION_GUIDE
    )


def build_memory_prompt(
    state: dict[str, Any], rounds: list[dict[str, Any]], batch_number: int
) -> str:
    payload = {
        "batch_number": batch_number,
        "source_inventory": source_inventory(state, rounds),
        "event_tail_for_context_only": state_view_for_boundary(state),
        "related_entities": related_entity_context(state, rounds),
        "new_messages": batch_messages(rounds),
    }
    return "只提取本批实际形成的主观 Memory。\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def build_entity_prompt(
    state: dict[str, Any], rounds: list[dict[str, Any]], batch_number: int
) -> str:
    payload = {
        "batch_number": batch_number,
        "source_inventory": source_inventory(state, rounds),
        "existing_entity_candidates": related_entity_context(state, rounds),
        "new_messages": batch_messages(rounds),
    }
    return "只提取本批新增或变化的 Entity 与 Character Relation。\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def task_names_for_set(task_set: str) -> tuple[str, ...]:
    """把面向使用者的测试范围转换为固定任务集合。"""

    if task_set == "event":
        return ("event",)
    if task_set == "full":
        return ("event", "memory", "entity")
    raise ValueError(f"不支持的 task_set：{task_set}")


def build_task_specs(
    args: argparse.Namespace,
    state: dict[str, Any],
    rounds: list[dict[str, Any]],
    batch_number: int,
) -> dict[str, dict[str, Any]]:
    """建立同一快照上的任务；Event 短回归不会误调用其余两路。"""

    all_specs: dict[str, dict[str, Any]] = {
        "event": {
            "system_prompt": EVENT_SYSTEM_PROMPT,
            "user_prompt": build_event_prompt(state, rounds, batch_number),
            "max_tokens": args.event_max_tokens,
            "thinking_mode": args.event_thinking,
            "response_format": args.response_format,
            "normalizer": lambda plan, s=state, r=rounds: normalize_event_plan(
                plan, s, r
            ),
            "validator": lambda plan, s=state, r=rounds: validate_event_plan(
                plan, s, r
            ),
        },
        "memory": {
            "system_prompt": MEMORY_SYSTEM_PROMPT,
            "user_prompt": build_memory_prompt(state, rounds, batch_number),
            "max_tokens": args.memory_max_tokens,
            "thinking_mode": args.memory_thinking,
            "response_format": args.response_format,
            "normalizer": normalize_memory_plan,
            "validator": lambda plan, r=rounds: validate_memory_plan(plan, r),
        },
        "entity": {
            "system_prompt": ENTITY_SYSTEM_PROMPT,
            "user_prompt": build_entity_prompt(state, rounds, batch_number),
            "max_tokens": args.entity_max_tokens,
            "thinking_mode": args.entity_thinking,
            "response_format": args.response_format,
            "normalizer": lambda plan, s=state, r=rounds: normalize_entity_plan(
                plan, s, r
            ),
            "validator": lambda plan, r=rounds, s=state: validate_entity_plan(
                plan, r, s
            ),
        },
    }
    return {
        name: all_specs[name]
        for name in task_names_for_set(getattr(args, "task_set", "full"))
    }


def skipped_network_report() -> dict[str, Any]:
    """Event 单路回归不伪装成一次完整 Entity 网络验证。"""

    return {
        "valid": True,
        "skipped": True,
        "reason": "本次只校准 Event；未调用 Memory 或 Entity，因此不重复网络物化与校验。",
        "entity_count": 0,
        "errors": [],
        "warnings": [],
    }


def normalize_event_plan(
    plan: dict[str, Any], state: dict[str, Any], rounds: list[dict[str, Any]]
) -> dict[str, Any]:
    """由后一 Event 的开头派生前一段结束线，模型无需重复输出边界。"""

    boundary_source = {
        "old_forming_disposition": plan.get("old_forming_disposition"),
        "decision_reason": plan.get("decision_reason"),
        "segments": deepcopy(plan.get("segments")),
    }
    messages = {message["ref"]: message["content"] for message in batch_messages(rounds)}
    script_repairs: list[dict[str, str]] = []
    for segment in _as_list(boundary_source.get("segments")):
        if not isinstance(segment, dict):
            continue
        source_refs = [str(ref) for ref in _as_list(segment.get("source_refs"))]
        anchors = _as_list(segment.get("start_anchors"))
        first_anchor_ref = (
            str(anchors[0].get("source_ref", ""))
            if anchors and isinstance(anchors[0], dict)
            else ""
        )
        original_anchor_quote = (
            str(anchors[0].get("start_quote", ""))
            if anchors and isinstance(anchors[0], dict)
            else ""
        )
        if (
            segment.get("slot") in {"forming_existing", "pending_tail"}
            and source_refs
            and source_refs[0] in messages
            and first_anchor_ref != source_refs[0]
        ):
            opening = messages[source_refs[0]].strip()[:32]
            if opening:
                segment["start_anchors"] = [
                    {"source_ref": source_refs[0], "start_quote": opening}
                ]
                anchors = segment["start_anchors"]
                script_repairs.append(
                    {
                        "source_ref": source_refs[0],
                        "model_quote": original_anchor_quote,
                        "source_quote": opening,
                    }
                )
        for anchor in _as_list(segment.get("start_anchors")):
            if not isinstance(anchor, dict):
                continue
            source_ref = str(anchor.get("source_ref", ""))
            quote = str(anchor.get("start_quote", "")).strip()
            text = messages.get(source_ref, "")
            if not quote or not text or quote_in_text(quote, text):
                continue
            repaired = _nearest_source_quote(text, quote)
            if repaired is None:
                continue
            anchor["start_quote"] = repaired
            script_repairs.append(
                {
                    "source_ref": source_ref,
                    "model_quote": quote,
                    "source_quote": repaired,
                }
            )
    boundary = normalize_boundary_plan(boundary_source, state, rounds)
    normalized = deepcopy(plan)
    normalized.setdefault("decision_reason", "")
    normalized.setdefault("boundary_uncertainties", [])
    for update in _as_list(normalized.get("event_updates")):
        if not isinstance(update, dict):
            continue
        for field in (
            "event_beats_add",
            "unresolved_add",
            "unresolved_resolve",
            "retire_detail_ids",
            "new_key_details",
        ):
            update.setdefault(field, [])
        for deprecated in (
            "participants_add",
            "participants_remove",
            "location_occurrences_add",
            "locations_remove",
            "related_entity_keys_add",
            "related_entity_keys_remove",
        ):
            update.pop(deprecated, None)
    normalized["segments"] = boundary.get("segments", [])
    normalized["boundaries"] = boundary.get("boundaries", [])
    normalized["script_anchor_repairs"] = script_repairs
    return normalized


def _nearest_source_quote(text: str, quote: str) -> str | None:
    """只在长短引存在唯一高相似原文时修正一两字抄写误差。"""

    if len(quote) < 8 or len(text) < len(quote):
        return None
    candidates: list[tuple[float, int, str]] = []
    for width in range(max(8, len(quote) - 2), min(len(text), len(quote) + 2) + 1):
        for start in range(0, len(text) - width + 1):
            candidate = text[start : start + width]
            score = SequenceMatcher(None, quote, candidate, autojunk=False).ratio()
            if score >= 0.88:
                candidates.append((score, start, candidate))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], -abs(len(item[2]) - len(quote))), reverse=True)
    best_score, best_start, best = candidates[0]
    competing_positions = {
        start
        for score, start, candidate in candidates
        if score == best_score and candidate != best and abs(start - best_start) > 2
    }
    if competing_positions:
        return None
    return best


def _evidence_quote_in_text(quote: str, text: str) -> bool:
    """忽略标点换行核对实体依据，并允许一个足够长的原文连续片段定位。"""

    compact_quote = re.sub(r"[\W_]+", "", quote, flags=re.UNICODE)
    compact_text = re.sub(r"[\W_]+", "", text, flags=re.UNICODE)
    if len(compact_quote) >= 8 and compact_quote in compact_text:
        return True
    fragments = sorted(
        (
            part
            for part in re.split(r"[\s，。！？；：、“”‘’（）()【】\[\]—…·,.;:!?\"']+", quote)
            if len(part) >= 8
        ),
        key=len,
        reverse=True,
    )
    return bool(fragments and fragments[0] in text)


def _legacy_content_plan(
    plan: dict[str, Any], boundary_state: dict[str, Any], slot_event_ids: dict[str, str]
) -> dict[str, Any]:
    """把关键特写增量和地点经过转换成已验证过的 Event 内容应用结构。"""

    events_by_id = {event["id"]: event for event in boundary_state["events"]}
    updates: list[dict[str, Any]] = []
    for raw_update in _as_list(plan.get("event_updates")):
        if not isinstance(raw_update, dict):
            updates.append(raw_update)
            continue
        update = deepcopy(raw_update)
        for field in (
            "participants_add",
            "participants_remove",
            "locations_add",
            "locations_remove",
            "related_entity_keys_add",
            "related_entity_keys_remove",
            "unresolved_add",
            "unresolved_resolve",
            "new_key_details",
        ):
            update.setdefault(field, [])
        slot = str(update.get("slot", ""))
        existing_ids: list[str] = []
        event_id = slot_event_ids.get(slot)
        if event_id in events_by_id:
            existing_ids = [
                str(detail.get("id"))
                for detail in events_by_id[event_id].get("key_details", [])
                if isinstance(detail, dict) and detail.get("id")
            ]
        existing_event = events_by_id.get(event_id, {})
        combined_beats = _merge_event_beats(
            existing_event.get("event_beats", []),
            _as_list(update.get("event_beats_add")),
        )
        if combined_beats:
            update["story_summary"] = "\n".join(
                str(beat.get("content", "")).strip() for beat in combined_beats
            )
        retired = {str(value) for value in _as_list(update.get("retire_detail_ids"))}
        update["key_details_keep"] = [
            detail_id for detail_id in existing_ids if detail_id not in retired
        ]
        details: list[Any] = []
        for raw_detail in _as_list(update.get("new_key_details")):
            if not isinstance(raw_detail, dict):
                details.append(raw_detail)
                continue
            detail = deepcopy(raw_detail)
            detail["actor"] = str(detail.pop("actor_key", ""))
            details.append(detail)
        update["new_key_details"] = details
        updates.append(update)
    return {"event_updates": updates}


def validate_event_plan(
    plan: dict[str, Any], state: dict[str, Any], rounds: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """区分会破坏机器闭环的结构错误与只需记录的语义软警告。"""

    errors = validate_boundary_plan(plan, state, rounds)
    warnings: list[str] = []
    if not isinstance(plan.get("decision_reason"), str) or not str(
        plan.get("decision_reason", "")
    ).strip():
        warnings.append("模型未提供边界短理由；不影响来源分段和提交")
    if not isinstance(plan.get("boundary_uncertainties"), list):
        errors.append("boundary_uncertainties 必须是数组")
    try:
        source_state = deepcopy(state)
        _ensure_event_beats(source_state)
        boundary_state, _, slot_event_ids = apply_boundary_plan(source_state, plan)
        _carry_merged_event_beats(boundary_state, source_state, plan, slot_event_ids)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(f"Event 状态流转无法应用：{exc}")
        return _unique(errors), warnings

    updates = plan.get("event_updates")
    if not isinstance(updates, list):
        return _unique(errors + ["event_updates 必须是数组"]), warnings
    valid_refs = {message["ref"] for message in batch_messages(rounds)}
    events_by_id = {event["id"]: event for event in boundary_state["events"]}
    for index, update in enumerate(updates):
        if not isinstance(update, dict):
            errors.append(f"event_updates[{index}] 不是对象")
            continue
        slot = str(update.get("slot", ""))
        for field in (
            "unresolved_add",
            "unresolved_resolve",
            "retire_detail_ids",
            "new_key_details",
            "event_beats_add",
        ):
            if not isinstance(update.get(field), list):
                errors.append(f"event_updates[{index}].{field} 必须是数组")
        for beat_index, beat in enumerate(_as_list(update.get("event_beats_add"))):
            if not isinstance(beat, dict):
                errors.append(f"{slot} 的事实段 {beat_index + 1} 不是对象")
                continue
            if not str(beat.get("content", "")).strip():
                errors.append(f"{slot} 的事实段 {beat_index + 1} 内容为空")
            refs = beat.get("source_refs")
            if (
                not isinstance(refs, list)
                or not refs
                or any(ref not in valid_refs for ref in refs)
            ):
                errors.append(f"{slot} 的事实段 {beat_index + 1} 来源不在本批")
        description = str(update.get("description", "")).strip()
        if len(description) > 160:
            warnings.append(
                f"{slot} 的 Description 为 {len(description)} 字，超过一百六十字软性目标；不触发重试"
            )

        event_time = update.get("event_time")
        if event_time is not None:
            if not isinstance(event_time, dict):
                errors.append(f"{slot} 的 event_time 必须是对象或 null")
            else:
                for time_field in ("start_time", "end_time"):
                    point = event_time.get(time_field)
                    if point is None and time_field == "end_time":
                        continue
                    if not isinstance(point, dict):
                        errors.append(f"{slot} 的 {time_field} 非法")
                        continue
                    if not str(point.get("expression", "")).strip() or point.get(
                        "precision"
                    ) not in {"exact", "approximate"}:
                        errors.append(f"{slot} 的 {time_field} 内容非法")

        event_id = slot_event_ids.get(slot)
        existing_ids = {
            str(detail.get("id"))
            for detail in events_by_id.get(event_id, {}).get("key_details", [])
            if isinstance(detail, dict) and detail.get("id")
        }
        unknown_retired = {
            str(value) for value in _as_list(update.get("retire_detail_ids"))
        } - existing_ids
        if unknown_retired:
            errors.append(
                f"{slot} 试图移除不属于该 Event 的特写："
                + ", ".join(sorted(unknown_retired))
            )

    content_plan = _legacy_content_plan(plan, boundary_state, slot_event_ids)
    content_errors, content_warnings = validate_content_plan(
        content_plan,
        plan,
        rounds,
        boundary_state,
        slot_event_ids,
    )
    errors.extend(content_errors)
    warnings.extend(content_warnings)
    if len(_as_list(plan.get("segments"))) > 1:
        warnings.extend(str(item) for item in _as_list(plan.get("boundary_uncertainties")))
    return _unique(errors), _unique(warnings)


def validate_memory_plan(
    plan: dict[str, Any], rounds: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    memories = plan.get("memories")
    if not isinstance(memories, list):
        return ["memories 必须是数组"], warnings
    if len(memories) > 3:
        errors.append("每批 memories 最多三条；请合并同一 Owner 的相关认知并保留最重要内容")
    messages = {message["ref"]: message["content"] for message in batch_messages(rounds)}
    for index, memory in enumerate(memories):
        if not isinstance(memory, dict):
            errors.append(f"memories[{index}] 不是对象")
            continue
        if not is_entity_key(memory.get("owner_key"), "character"):
            errors.append(f"memories[{index}].owner_key 非法")
        if not str(memory.get("description", "")).strip():
            errors.append(f"memories[{index}].description 不能为空")
        if memory.get("acquisition_mode") not in ACQUISITION_MODES:
            errors.append(f"memories[{index}].acquisition_mode 非法")
        if memory.get("importance") not in {"recent", "core"}:
            errors.append(f"memories[{index}].importance 非法")
        evidence = memory.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"memories[{index}].evidence 必须是非空数组")
        else:
            for evidence_index, item in enumerate(evidence):
                if not isinstance(item, dict):
                    errors.append(
                        f"memories[{index}].evidence[{evidence_index}] 不是对象"
                    )
                    continue
                ref = str(item.get("source_ref", ""))
                quote = str(item.get("quote", ""))
                if ref not in messages:
                    errors.append(
                        f"memories[{index}].evidence[{evidence_index}] 来源不在本批"
                    )
                elif not quote_in_text(quote, messages[ref]):
                    warnings.append(
                        f"Memory {index + 1} 的证据短引不能直接连续核对，将按来源保守绑定"
                    )
        related = memory.get("related_character_keys")
        if not isinstance(related, list) or any(
            not is_entity_key(key, "character") for key in _as_list(related)
        ):
            errors.append(f"memories[{index}].related_character_keys 非法")
        pairs = memory.get("related_relation_pairs")
        if not isinstance(pairs, list):
            errors.append(f"memories[{index}].related_relation_pairs 必须是数组")
        else:
            for pair in pairs:
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or pair[0] == pair[1]
                    or any(not is_entity_key(key, "character") for key in pair)
                ):
                    errors.append(f"memories[{index}] 存在非法 Relation 人物对")
    return _unique(errors), _unique(warnings)


def normalize_memory_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """修正确知且不改变语义的兼容端枚举拼写，不浪费一次模型调用。"""

    normalized = deepcopy(plan)
    aliases = {
        "inferred_from_event": "inferred_from_events",
    }
    for memory in _as_list(normalized.get("memories")):
        if isinstance(memory, dict):
            memory.setdefault("related_character_keys", [])
            memory.setdefault("related_relation_pairs", [])
            mode = memory.get("acquisition_mode")
            if mode in aliases:
                memory["acquisition_mode"] = aliases[mode]
    return normalized


def _entity_name_signature(value: Any) -> str:
    """只折叠空白和常见分隔符，不用模糊相似度猜测实体身份。"""

    return re.sub(r"[\s·・,，。．、:：;；]+", "", str(value)).casefold()


def _rewrite_entity_keys(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_rewrite_entity_keys(item, mapping) for item in value]
    if isinstance(value, dict):
        return {
            key: _rewrite_entity_keys(item, mapping) for key, item in value.items()
        }
    return value


def normalize_entity_plan(
    plan: dict[str, Any], state: dict[str, Any] | None = None,
    rounds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """补齐稀疏容器，并把与既有正式名称或别名精确重合的键自动归一。"""

    normalized = deepcopy(plan)
    normalized.setdefault("entities", [])
    normalized.setdefault("relations", [])
    for entity in _as_list(normalized.get("entities")):
        if not isinstance(entity, dict):
            continue
        if "aliases_add" not in entity and isinstance(entity.get("aliases"), list):
            entity["aliases_add"] = entity.pop("aliases")
        if "character_data_patch" not in entity and isinstance(
            entity.get("character_data"), dict
        ):
            entity["character_data_patch"] = entity.pop("character_data")
        entity.setdefault("aliases_add", [])
        entity.setdefault("event_link_evidence", [])
        if rounds is not None:
            messages = {
                message["ref"]: message["content"] for message in batch_messages(rounds)
            }
            for link in _as_list(entity.get("event_link_evidence")):
                if not isinstance(link, dict):
                    continue
                ref = str(link.get("source_ref", ""))
                quote = str(link.get("quote", "")).strip()
                if quote and ref in messages and _evidence_quote_in_text(
                    quote, messages[ref]
                ):
                    continue
                matching_refs = [
                    candidate_ref
                    for candidate_ref, text in messages.items()
                    if quote and _evidence_quote_in_text(quote, text)
                ]
                if len(matching_refs) == 1:
                    link["source_ref"] = matching_refs[0]
        key = str(entity.get("entity_key", ""))
        if (
            state is not None
            and key in state.get("entity_candidates", {})
            and not _as_list(entity.get("evidence_refs"))
            and rounds is not None
        ):
            existing = state["entity_candidates"][key]
            names = _unique(
                name
                for name in [
                    existing.get("primary_name"),
                    *_as_list(existing.get("aliases")),
                    key.partition(":")[2],
                ]
                if str(name).strip()
            )
            inferred_refs = []
            for message in batch_messages(rounds):
                content = str(message.get("content", ""))
                speaker = str(message.get("speaker", "")).strip()
                if any(str(name) in content for name in names) or (
                    entity.get("type") == "character"
                    and message.get("role") == "user"
                    and speaker in names
                ):
                    inferred_refs.append(message["ref"])
            if inferred_refs:
                entity["evidence_refs"] = _unique(inferred_refs)
    for relation in _as_list(normalized.get("relations")):
        if not isinstance(relation, dict):
            continue
        relation.setdefault("aspects", [])
        relation.setdefault("directional_states", [])

    if state is None:
        return normalized
    existing_by_name: dict[tuple[str, str], set[str]] = {}
    for key, candidate in state.get("entity_candidates", {}).items():
        entity_type = str(candidate.get("type", ""))
        names = [candidate.get("primary_name"), *_as_list(candidate.get("aliases"))]
        for name in names:
            signature = _entity_name_signature(name)
            if signature:
                existing_by_name.setdefault((entity_type, signature), set()).add(key)

    key_mapping: dict[str, str] = {}
    for entity in _as_list(normalized.get("entities")):
        if not isinstance(entity, dict):
            continue
        key = str(entity.get("entity_key", ""))
        entity_type = str(entity.get("type", ""))
        key_name = key.partition(":")[2]
        names = [entity.get("primary_name") or key_name, *_as_list(entity.get("aliases_add"))]
        matches: set[str] = set()
        for name in names:
            matches.update(
                existing_by_name.get((entity_type, _entity_name_signature(name)), set())
            )
        if len(matches) != 1:
            continue
        canonical = next(iter(matches))
        if canonical == key:
            continue
        key_mapping[key] = canonical
        canonical_name = str(
            state["entity_candidates"][canonical].get("primary_name", "")
        )
        if key_name and key_name != canonical_name:
            entity["aliases_add"] = _unique(
                [*_as_list(entity.get("aliases_add")), key_name]
            )
        entity["entity_key"] = canonical
        entity.pop("primary_name", None)
    return _rewrite_entity_keys(normalized, key_mapping)


def validate_entity_plan(
    plan: dict[str, Any], rounds: list[dict[str, Any]], state: dict[str, Any]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    valid_refs = {message["ref"] for message in batch_messages(rounds)}
    entities = plan.get("entities")
    relations = plan.get("relations")
    if not isinstance(entities, list):
        errors.append("entities 必须是数组")
        entities = []
    if not isinstance(relations, list):
        errors.append("relations 必须是数组")
        relations = []
    seen_keys: set[str] = set()
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            errors.append(f"entities[{index}] 不是对象")
            continue
        key = entity.get("entity_key")
        entity_type = entity.get("type")
        name = str(entity.get("primary_name", "")).strip()
        existing = state.get("entity_candidates", {}).get(str(key))
        if not is_entity_key(key) or entity_type_from_key(str(key)) != entity_type:
            errors.append(f"entities[{index}] 的候选键与 type 不一致")
        if name and not str(key).endswith(":" + name):
            errors.append(f"entities[{index}] 的候选键与主要名称不一致")
        if existing is None and not name:
            errors.append(f"entities[{index}] 是新候选，primary_name 不能为空")
        if key in seen_keys:
            warnings.append(f"entities 中重复候选键 {key}；脚本将按出现顺序合并补丁")
        seen_keys.add(str(key))
        if not isinstance(entity.get("aliases_add"), list):
            errors.append(f"entities[{index}].aliases_add 必须是数组")
        if existing is None and not str(entity.get("description", "")).strip():
            errors.append(f"entities[{index}] 是新候选，description 不能为空")
        evidence_refs = entity.get("evidence_refs")
        if not isinstance(evidence_refs, list) or any(
            ref not in valid_refs for ref in _as_list(evidence_refs)
        ):
            errors.append(f"entities[{index}].evidence_refs 非法")
        elif not evidence_refs:
            if existing is None:
                errors.append(f"entities[{index}] 是新候选，evidence_refs 不能为空")
            else:
                warnings.append(f"{key} 的本批补丁没有新来源书签；保留旧来源并记录提示")
        links = entity.get("event_link_evidence", [])
        if not isinstance(links, list):
            errors.append(f"entities[{index}].event_link_evidence 必须是数组")
            links = []
        messages = {
            message["ref"]: message["content"] for message in batch_messages(rounds)
        }
        for link_index, link in enumerate(links):
            if not isinstance(link, dict):
                errors.append(
                    f"entities[{index}].event_link_evidence[{link_index}] 不是对象"
                )
                continue
            ref = str(link.get("source_ref", ""))
            quote = str(link.get("quote", "")).strip()
            if ref not in valid_refs or not quote:
                errors.append(f"{key} 的 Event 挂接依据非法")
                continue
            if not _evidence_quote_in_text(quote, messages[ref]):
                warnings.append(
                    f"{key} 在 {ref} 的 Event 挂接短引不能直接核对；脚本不会凭该短引强行挂接"
                )
            roles = link.get("roles", [])
            if roles and (
                entity_type != "location"
                or not isinstance(roles, list)
                or any(role not in LOCATION_ROLES for role in _as_list(roles))
            ):
                warnings.append(f"{key} 的 Location roles 将被忽略")
        character_patch = entity.get("character_data_patch")
        if entity_type == "character" and character_patch is not None and not isinstance(
            character_patch, dict
        ):
            errors.append(f"entities[{index}].character_data_patch 非法")
        if entity_type != "character" and character_patch not in (None, {}):
            warnings.append(f"{key} 不是 Character，character_data_patch 将被忽略")

    for index, relation in enumerate(relations):
        if not isinstance(relation, dict):
            errors.append(f"relations[{index}] 不是对象")
            continue
        participants = relation.get("participant_keys")
        if (
            not isinstance(participants, list)
            or len(participants) != 2
            or participants[0] == participants[1]
            or any(not is_entity_key(key, "character") for key in _as_list(participants))
        ):
            errors.append(f"relations[{index}].participant_keys 非法")
            participants = []
        existing_relation = state.get("relation_candidates", {}).get(
            relation_key(participants)
        )
        if existing_relation is None and not str(relation.get("description", "")).strip():
            errors.append(f"relations[{index}] 是新候选，description 不能为空")
        evidence_refs = relation.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs or any(
            ref not in valid_refs for ref in _as_list(evidence_refs)
        ):
            errors.append(f"relations[{index}].evidence_refs 非法")
        aspects = relation.get("aspects")
        states = relation.get("directional_states")
        if not isinstance(aspects, list):
            errors.append(f"relations[{index}].aspects 必须是数组")
            aspects = []
        if not isinstance(states, list):
            errors.append(f"relations[{index}].directional_states 必须是数组")
            states = []
        for aspect_index, aspect in enumerate(aspects):
            if not isinstance(aspect, dict):
                errors.append(f"relations[{index}].aspects[{aspect_index}] 不是对象")
                continue
            if not SNAKE_CASE_RE.fullmatch(str(aspect.get("kind", ""))):
                errors.append(f"relations[{index}] 的 aspect.kind 必须是 snake_case")
            if aspect.get("status") not in {"active", "ended"}:
                errors.append(f"relations[{index}] 的 aspect.status 非法")
            if aspect.get("visibility") not in VISIBILITIES:
                errors.append(f"relations[{index}] 的 aspect.visibility 非法")
            roles = aspect.get("participant_roles")
            role_keys = {
                item.get("character_key")
                for item in _as_list(roles)
                if isinstance(item, dict)
            }
            if not isinstance(roles, list) or set(participants) != role_keys:
                errors.append(f"relations[{index}] 的 aspect 角色未覆盖两个端点")
            for role in _as_list(roles):
                role_values = role.get("roles") if isinstance(role, dict) else None
                if (
                    not isinstance(role_values, list)
                    or not role_values
                    or any(not SNAKE_CASE_RE.fullmatch(str(value)) for value in role_values)
                ):
                    errors.append(f"relations[{index}] 的 participant roles 非法")
        for state_index, direction in enumerate(states):
            if not isinstance(direction, dict):
                errors.append(
                    f"relations[{index}].directional_states[{state_index}] 不是对象"
                )
                continue
            if {
                direction.get("from_key"),
                direction.get("toward_key"),
            } != set(participants):
                errors.append(f"relations[{index}] 的方向状态没有对齐两个端点")
            tags = direction.get("tags")
            if not isinstance(tags, list) or any(
                not SNAKE_CASE_RE.fullmatch(str(tag)) for tag in _as_list(tags)
            ):
                errors.append(f"relations[{index}] 的方向 tags 非法")
    return _unique(errors), _unique(warnings)


def run_model_task(
    *,
    task: str,
    endpoint: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float,
    max_tokens: int,
    thinking_mode: str = "default",
    response_format: str = "text",
    stream_idle_timeout: float = 90.0,
    retry_offset_seconds: float = 0.0,
    candidate_attempt_limit: int = 1,
    transport_attempt_limit: int = 1,
    normalizer: Callable[[dict[str, Any]], dict[str, Any]],
    validator: Callable[[dict[str, Any]], tuple[list[str], list[str]]],
) -> dict[str, Any]:
    """运行一项语义任务；无正文的传输可重试，语义候选默认只生成一次。"""

    started = time.perf_counter()
    attempts: list[dict[str, Any]] = []
    candidate_prompt = user_prompt
    candidate_attempt_limit = max(1, candidate_attempt_limit)
    transport_attempt_limit = max(1, transport_attempt_limit)
    for candidate_attempt in range(1, candidate_attempt_limit + 1):
        raw = ""
        metadata: dict[str, Any] = {}
        transport_failures: list[str] = []
        for transport_attempt in range(1, transport_attempt_limit + 1):
            try:
                raw, metadata = call_chat_completion(
                    endpoint,
                    api_key,
                    model,
                    system_prompt,
                    candidate_prompt,
                    timeout,
                    max_tokens,
                    thinking_mode=thinking_mode,
                    response_format=response_format,
                    stream_idle_timeout=stream_idle_timeout,
                )
                break
            except RuntimeError as exc:
                transport_failures.append(str(exc))
                if transport_attempt == transport_attempt_limit:
                    attempts.append(
                        {
                            "candidate_attempt": candidate_attempt,
                            "user_prompt": candidate_prompt,
                            "raw": raw,
                            "api": metadata,
                            "transport_failures": transport_failures,
                            "validation_errors": [str(exc)],
                            "warnings": [],
                        }
                    )
                    return {
                        "task": task,
                        "ok": False,
                        "fatal_error": str(exc),
                        "system_prompt": system_prompt,
                        "request_settings": {
                            "thinking_mode": thinking_mode,
                            "response_format": response_format,
                            "max_tokens": max_tokens,
                            "stream_idle_timeout": stream_idle_timeout,
                        },
                        "attempts": attempts,
                        "elapsed_seconds": round(time.perf_counter() - started, 3),
                    }
                time.sleep(2**transport_attempt + max(0.0, retry_offset_seconds))

        plan: dict[str, Any] | None = None
        parse_error: str | None = None
        try:
            if metadata.get("finish_reason") == "length":
                raise ValueError("正式回复达到输出长度上限并被截断；请压缩、合并后完整重写")
            plan = normalizer(extract_json_object(raw))
            errors, warnings = validator(plan)
            if thinking_mode == "off" and int(metadata.get("reasoning_chars", 0)) > 500:
                warnings = [
                    *warnings,
                    "接口虽收到关闭思考参数，仍返回大量 reasoning_content；按兼容端点未完全遵从记录",
                ]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            parse_error = str(exc)
            errors, warnings = [f"正式回复无法解析：{exc}"], []
        attempts.append(
            {
                "candidate_attempt": candidate_attempt,
                "user_prompt": candidate_prompt,
                "raw": raw,
                "api": metadata,
                "transport_failures": transport_failures,
                "parsed_plan": plan,
                "parse_error": parse_error,
                "validation_errors": errors,
                "warnings": warnings,
            }
        )
        if not errors:
            return {
                "task": task,
                "ok": True,
                "fatal_error": None,
                "system_prompt": system_prompt,
                "request_settings": {
                    "thinking_mode": thinking_mode,
                    "response_format": response_format,
                    "max_tokens": max_tokens,
                    "stream_idle_timeout": stream_idle_timeout,
                },
                "attempts": attempts,
                "plan": plan,
                "warnings": warnings,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            }
        if candidate_attempt < candidate_attempt_limit:
            prior = json.dumps(plan, ensure_ascii=False) if plan is not None else raw
            candidate_prompt = (
                user_prompt
                + "\n\n上一次正式候选只有下列机器结构错误。请保留叙事事实，只修正结构，"
                "结构修正不是移动或合并语义边界的理由；边界书签保持原意，通过调整来源归属、"
                "补齐旧段或修正字段来解决。重新输出完整 JSON，不解释：\n- "
                + "\n- ".join(errors)
                + "\n上一次正式候选：\n"
                + prior
            )
    return {
        "task": task,
        "ok": False,
        "fatal_error": f"{candidate_attempt_limit} 次候选均未通过机器结构检查",
        "system_prompt": system_prompt,
        "request_settings": {
            "thinking_mode": thinking_mode,
            "response_format": response_format,
            "max_tokens": max_tokens,
            "stream_idle_timeout": stream_idle_timeout,
        },
        "attempts": attempts,
        "plan": attempts[-1].get("parsed_plan") if attempts else None,
        "warnings": attempts[-1].get("warnings", []) if attempts else [],
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }


def _merge_keyed_list(
    existing: list[dict[str, Any]],
    incoming: Iterable[dict[str, Any]],
    key: Callable[[dict[str, Any]], Any],
) -> list[dict[str, Any]]:
    """按稳定语义键替换同项，保留未被本批更新的旧项。"""

    result = deepcopy(existing)
    positions = {key(item): index for index, item in enumerate(result)}
    for item in incoming:
        item_copy = deepcopy(item)
        signature = key(item_copy)
        if signature in positions:
            result[positions[signature]] = item_copy
        else:
            positions[signature] = len(result)
            result.append(item_copy)
    return result


def _deep_patch(existing: Any, patch: dict[str, Any]) -> dict[str, Any]:
    """递归应用对象补丁；省略即不变，显式标量或数组表示当前完整值。"""

    result = deepcopy(existing) if isinstance(existing, dict) else {}
    for field, value in patch.items():
        if isinstance(value, dict):
            result[field] = _deep_patch(result.get(field), value)
        else:
            result[field] = deepcopy(value)
    return result


def apply_event_candidate(
    state: dict[str, Any], plan: dict[str, Any]
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """执行 Event 分段与内容更新，并同步修正旧 Memory 的合并引用。"""

    old_state = deepcopy(state)
    _ensure_event_beats(old_state)
    pending_before = next(
        (
            event
            for event in old_state["events"]
            if event.get("status") == "pending_finalization"
        ),
        None,
    )
    forming_before = next(
        (event for event in old_state["events"] if event.get("status") == "forming"),
        None,
    )
    boundary_state, operations, slot_event_ids = apply_boundary_plan(old_state, plan)
    _carry_merged_event_beats(boundary_state, old_state, plan, slot_event_ids)

    if (
        plan.get("old_forming_disposition") == "merge_into_pending"
        and pending_before is not None
        and forming_before is not None
    ):
        old_id = forming_before["id"]
        new_id = pending_before["id"]
        for memory in boundary_state.get("memories", []):
            event_ids = [
                new_id if event_id == old_id else event_id
                for event_id in _as_list(memory.get("event_ids"))
            ]
            memory["event_ids"] = _unique(event_ids)
            if memory.get("event_id") == old_id:
                memory["event_id"] = new_id

    content_plan = _legacy_content_plan(plan, boundary_state, slot_event_ids)
    next_state = apply_content_plan(boundary_state, content_plan, slot_event_ids)
    events_by_id = {event["id"]: event for event in next_state["events"]}
    for update in _as_list(plan.get("event_updates")):
        if not isinstance(update, dict):
            continue
        event_id = slot_event_ids.get(str(update.get("slot", "")))
        event = events_by_id.get(event_id)
        if event is None:
            continue
        if update.get("event_time") is not None:
            event["event_time"] = deepcopy(update["event_time"])

        incoming_beats = _as_list(update.get("event_beats_add"))
        existing_signatures = {
            _beat_signature(beat)
            for beat in event.setdefault("event_beats", [])
            if isinstance(beat, dict)
        }
        for raw_beat in incoming_beats:
            if not isinstance(raw_beat, dict):
                continue
            beat = deepcopy(raw_beat)
            signature = _beat_signature(beat)
            if not signature[0] or signature in existing_signatures:
                continue
            number = next_state.setdefault("next_beat_number", 1)
            next_state["next_beat_number"] = number + 1
            beat["id"] = f"beat_probe_{number:03d}"
            event["event_beats"].append(beat)
            existing_signatures.add(signature)
        if event.get("event_beats"):
            event["story_summary"] = "\n".join(
                str(beat.get("content", "")).strip()
                for beat in event["event_beats"]
                if str(beat.get("content", "")).strip()
            )

        removed_locations = {
            str(key) for key in _as_list(update.get("locations_remove"))
        }
        occurrences = [
            item
            for item in event.get("location_occurrences", [])
            if item.get("entity_key") not in removed_locations
        ]
        for occurrence in _as_list(update.get("location_occurrences_add")):
            if not isinstance(occurrence, dict):
                continue
            signature = (
                occurrence.get("entity_key"),
                tuple(occurrence.get("source_refs") or []),
            )
            if not any(
                (
                    old.get("entity_key"),
                    tuple(old.get("source_refs") or []),
                )
                == signature
                for old in occurrences
            ):
                occurrences.append(deepcopy(occurrence))
        event["location_occurrences"] = occurrences

        related = [
            key
            for key in event.get("related_entity_keys", [])
            if key not in set(_as_list(update.get("related_entity_keys_remove")))
        ]
        event["related_entity_keys"] = _unique(
            related + [str(key) for key in _as_list(update.get("related_entity_keys_add"))]
        )
    return next_state, operations, slot_event_ids


def _event_slice_text(
    event: dict[str, Any], source_ref: str, rounds: list[dict[str, Any]]
) -> list[str]:
    """取得某 Event 在本批某条消息中真正拥有的原文片段。"""

    slices: list[str] = []
    for source_slice in event.get("source_slices", []):
        if source_ref not in _as_list(source_slice.get("source_refs")):
            continue
        segment = {
            "slot": "target",
            "source_refs": deepcopy(source_slice.get("source_refs", [])),
            "start_anchors": deepcopy(source_slice.get("start_anchors", [])),
            "end_before_anchors": deepcopy(
                source_slice.get("end_before_anchors", [])
            ),
        }
        bounded = bounded_segments_for_content({"segments": [segment]}, rounds)
        if not bounded:
            continue
        for message in bounded[0].get("assigned_messages", []):
            if message.get("ref") == source_ref:
                slices.append(str(message.get("content", "")))
    return slices


def bind_memory_to_events(
    memory: dict[str, Any], state: dict[str, Any], rounds: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """优先用证据短引精确绑定；含混时保留全部可能来源，绝不丢弃。"""

    exact: list[str] = []
    fallback: list[str] = []
    warnings: list[str] = []
    for evidence in _as_list(memory.get("evidence")):
        if not isinstance(evidence, dict):
            continue
        ref = str(evidence.get("source_ref", ""))
        quote = str(evidence.get("quote", ""))
        matching_ref_events: list[str] = []
        matching_quote_events: list[str] = []
        for event in state["events"]:
            if ref not in event.get("source_refs", []):
                continue
            matching_ref_events.append(event["id"])
            texts = _event_slice_text(event, ref, rounds)
            if quote and any(quote_in_text(quote, text) for text in texts):
                matching_quote_events.append(event["id"])
        exact.extend(matching_quote_events)
        fallback.extend(matching_ref_events)

    exact = _unique(exact)
    fallback = _unique(fallback)
    if exact:
        if len(exact) > 1:
            warnings.append("同一 Memory 证据跨越多个 Event，已保留全部精确来源绑定")
        return exact, warnings
    if fallback:
        warnings.append("Memory 证据短引未能唯一落入 Event 片段，已按来源书签保守绑定")
        return fallback, warnings
    warnings.append("Memory 没有找到可绑定 Event；该候选不能进入正式网络")
    return [], warnings


def apply_memory_candidates(
    state: dict[str, Any], plan: dict[str, Any], rounds: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    """新增去重后的 Memory 候选，并用脚本完成 Event 来源绑定。"""

    next_state = deepcopy(state)
    warnings: list[str] = []
    existing_signatures = {
        (
            memory.get("owner_key"),
            memory.get("description"),
            tuple(
                (item.get("source_ref"), item.get("quote"))
                for item in _as_list(memory.get("evidence"))
                if isinstance(item, dict)
            ),
        )
        for memory in next_state.get("memories", [])
    }
    for candidate in _as_list(plan.get("memories")):
        if not isinstance(candidate, dict):
            continue
        signature = (
            candidate.get("owner_key"),
            candidate.get("description"),
            tuple(
                (item.get("source_ref"), item.get("quote"))
                for item in _as_list(candidate.get("evidence"))
                if isinstance(item, dict)
            ),
        )
        if signature in existing_signatures:
            continue
        event_ids, binding_warnings = bind_memory_to_events(
            candidate, next_state, rounds
        )
        warnings.extend(binding_warnings)
        if not event_ids:
            continue
        number = next_state["next_memory_number"]
        next_state["next_memory_number"] += 1
        memory = deepcopy(candidate)
        memory["id"] = f"memory_probe_{number:03d}"
        memory["event_ids"] = event_ids
        memory["event_id"] = event_ids[0]
        next_state["memories"].append(memory)
        existing_signatures.add(signature)
    return next_state, _unique(warnings)


def relation_key(participant_keys: Iterable[str]) -> str:
    """同一对 Character 只形成一个 Relation 候选。"""

    return "|".join(sorted(str(key) for key in participant_keys))


def apply_entity_candidates(
    state: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    """按候选键应用稀疏补丁；省略字段不覆盖旧资料。"""

    next_state = deepcopy(state)
    entities = next_state["entity_candidates"]
    for candidate in _as_list(plan.get("entities")):
        if not isinstance(candidate, dict):
            continue
        key = str(candidate.get("entity_key", ""))
        old = entities.get(key, {})
        merged = deepcopy(old)
        for field in ("entity_key", "type", "primary_name", "description"):
            if candidate.get(field) not in (None, ""):
                merged[field] = deepcopy(candidate[field])
        merged["aliases"] = _unique(
            _as_list(old.get("aliases")) + _as_list(candidate.get("aliases_add"))
        )
        merged["evidence_refs"] = _unique(
            _as_list(old.get("evidence_refs"))
            + _as_list(candidate.get("evidence_refs"))
        )
        merged["event_link_evidence"] = _merge_keyed_list(
            _as_list(old.get("event_link_evidence")),
            [
                item
                for item in _as_list(candidate.get("event_link_evidence"))
                if isinstance(item, dict)
            ],
            lambda item: (
                item.get("source_ref"),
                item.get("quote"),
                tuple(item.get("roles") or []),
            ),
        )
        if isinstance(candidate.get("character_data_patch"), dict):
            merged["character_data"] = _deep_patch(
                old.get("character_data"), candidate["character_data_patch"]
            )
        merged["stub"] = False
        entities[key] = merged

    relations = next_state["relation_candidates"]
    for candidate in _as_list(plan.get("relations")):
        if not isinstance(candidate, dict):
            continue
        key = relation_key(candidate.get("participant_keys", []))
        old = relations.get(key, {})
        merged = deepcopy(old)
        merged["participant_keys"] = sorted(candidate.get("participant_keys", []))
        if candidate.get("description"):
            merged["description"] = str(candidate["description"])
        merged["evidence_refs"] = _unique(
            _as_list(old.get("evidence_refs"))
            + _as_list(candidate.get("evidence_refs"))
        )
        merged["aspects"] = _merge_keyed_list(
            _as_list(old.get("aspects")),
            [item for item in _as_list(candidate.get("aspects")) if isinstance(item, dict)],
            lambda item: item.get("kind"),
        )
        merged["directional_states"] = _merge_keyed_list(
            _as_list(old.get("directional_states")),
            [
                item
                for item in _as_list(candidate.get("directional_states"))
                if isinstance(item, dict)
            ],
            lambda item: (item.get("from_key"), item.get("toward_key")),
        )
        relations[key] = merged
    return next_state


def referenced_candidate_keys(state: dict[str, Any]) -> set[str]:
    """收集三项任务共同提到的候选键，供脚本补齐最小节点。"""

    keys: set[str] = set()
    for event in state.get("events", []):
        keys.update(str(key) for key in event.get("participants", []))
        keys.update(str(key) for key in event.get("locations", []))
        keys.update(str(key) for key in event.get("related_entity_keys", []))
        for detail in event.get("key_details", []):
            actor = detail.get("actor") if isinstance(detail, dict) else None
            if is_entity_key(actor, "character"):
                keys.add(str(actor))
    for memory in state.get("memories", []):
        keys.add(str(memory.get("owner_key", "")))
        keys.update(str(key) for key in memory.get("related_character_keys", []))
        for pair in memory.get("related_relation_pairs", []):
            keys.update(str(key) for key in pair)
    for relation in state.get("relation_candidates", {}).values():
        keys.update(str(key) for key in relation.get("participant_keys", []))
    for candidate in state.get("entity_candidates", {}).values():
        data = candidate.get("character_data") or {}
        location = data.get("current_location_key")
        if is_entity_key(location, "location"):
            keys.add(location)
        for item in _as_list(data.get("inventory")):
            if isinstance(item, dict) and is_entity_key(item.get("item_key"), "item"):
                keys.add(item["item_key"])
        for skill in _as_list(data.get("skills")):
            if isinstance(skill, dict) and is_entity_key(skill.get("skill_key"), "skill"):
                keys.add(skill["skill_key"])
        for objective in _as_list(data.get("objectives")):
            if isinstance(objective, dict):
                keys.update(
                    str(key)
                    for key in _as_list(objective.get("related_entity_keys"))
                    if is_entity_key(key)
                )
        for preference in _as_list(data.get("preferences")):
            if isinstance(preference, dict) and is_entity_key(
                preference.get("related_entity_key")
            ):
                keys.add(preference["related_entity_key"])
    return {key for key in keys if is_entity_key(key)}


def ensure_minimal_entity_nodes(state: dict[str, Any]) -> list[str]:
    """跨任务漏标时创建可追踪最小节点；信息保留优先于丢弃引用。"""

    warnings: list[str] = []
    entities = state["entity_candidates"]
    type_labels = {
        "character": "角色",
        "location": "地点",
        "item": "物品",
        "organization": "组织",
        "skill": "技能",
        "concept": "概念",
    }
    for key in sorted(referenced_candidate_keys(state)):
        if key in entities:
            continue
        entity_type = entity_type_from_key(key)
        assert entity_type is not None
        name = key.split(":", 1)[1]
        entities[key] = {
            "entity_key": key,
            "type": entity_type,
            "primary_name": name,
            "aliases": [],
            "description": f"{name}；本批其他任务已引用，但 Entity 任务尚未单独展开的{type_labels[entity_type]}候选。",
            "evidence_refs": [],
            "character_data": {} if entity_type == "character" else None,
            "stub": True,
        }
        warning = f"其他任务引用了 {key}，Entity 任务未返回；脚本已保留最小候选节点"
        warnings.append(warning)
        _append_warning(state, warning)
    return warnings


def reconcile_witnessed_memory_participants(state: dict[str, Any]) -> list[str]:
    """Memory 已声明亲历时，固定补齐 Owner 的 Event 参与引用，避免历史索引漏链。"""

    warnings: list[str] = []
    events = {event["id"]: event for event in state.get("events", [])}
    for memory in state.get("memories", []):
        if memory.get("acquisition_mode") != "witnessed_event":
            continue
        owner_key = memory.get("owner_key")
        if not is_entity_key(owner_key, "character"):
            continue
        for event_id in memory.get("event_ids", []):
            event = events.get(event_id)
            if event is None or owner_key in event.get("participants", []):
                continue
            event.setdefault("participants", []).append(owner_key)
            warning = (
                f"{memory.get('id')} 声明 {owner_key} 亲历 {event_id}；"
                "脚本已补齐 Event 参与者引用"
            )
            warnings.append(warning)
            _append_warning(state, warning)
    return warnings


def reconcile_key_detail_actors(state: dict[str, Any]) -> list[str]:
    """能说出关键原话或完成动作特写的 Character 必然属于该 Event。"""

    warnings: list[str] = []
    for event in state.get("events", []):
        participants = event.setdefault("participants", [])
        for detail in event.get("key_details", []):
            actor = detail.get("actor") if isinstance(detail, dict) else None
            if not is_entity_key(actor, "character") or actor in participants:
                continue
            participants.append(actor)
            warning = (
                f"{event.get('id')} 的关键特写由 {actor} 说出或完成；"
                "脚本已补齐 Event 参与者引用"
            )
            warnings.append(warning)
            _append_warning(state, warning)
    return warnings


def reconcile_source_inventory(
    state: dict[str, Any], rounds: list[dict[str, Any]]
) -> list[str]:
    """补齐明确说话人的参与引用；场景头只做地点遗漏警告。"""

    warnings: list[str] = []
    messages = {message["ref"]: message for message in batch_messages(rounds)}
    headers_by_ref = {
        item["source_ref"]
        for item in source_inventory(state, rounds).get("scene_headers", [])
    }
    for event in state.get("events", []):
        refs = set(str(ref) for ref in event.get("source_refs", []))
        participants = event.setdefault("participants", [])
        for ref in sorted(refs):
            message = messages.get(ref)
            if not message or message.get("role") != "user":
                continue
            speaker = str(message.get("speaker", "")).strip()
            if not speaker or speaker.lower() in {"user", "用户"}:
                continue
            key = f"character:{speaker}"
            if key in participants:
                continue
            participants.append(key)
            warning = f"{event.get('id')} 包含 {ref} 的玩家行动；脚本已补齐参与者 {key}"
            warnings.append(warning)
            _append_warning(state, warning)
        if refs & headers_by_ref and not event.get("locations"):
            warning = (
                f"{event.get('id')} 的来源含场景头但没有实际地点引用；"
                "脚本保留为检查警告，不凭格式文本自动猜地点"
            )
            warnings.append(warning)
            _append_warning(state, warning)
    return warnings


def reconcile_entity_event_links(
    state: dict[str, Any], rounds: list[dict[str, Any]]
) -> list[str]:
    """按 Entity 的原文依据投影 Event 引用；共享消息用短引辨认真正归属。"""

    warnings: list[str] = []
    events = state.get("events", [])
    messages = {
        message["ref"]: message["content"] for message in batch_messages(rounds)
    }
    ref_owners: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        for ref in event.get("source_refs", []):
            ref_owners.setdefault(str(ref), []).append(event)

    for key, candidate in state.get("entity_candidates", {}).items():
        entity_type = candidate.get("type")
        for evidence in _as_list(candidate.get("event_link_evidence")):
            if not isinstance(evidence, dict):
                continue
            ref = str(evidence.get("source_ref", ""))
            quote = str(evidence.get("quote", "")).strip()
            if ref not in messages:
                continue
            if not quote or not _evidence_quote_in_text(quote, messages[ref]):
                warning = f"{key} 在 {ref} 的 Event 挂接短引无法核对；脚本未自动挂接"
                warnings.append(warning)
                _append_warning(state, warning)
                continue

            owners = ref_owners.get(ref, [])
            matching = owners
            if len(owners) > 1:
                matching = [
                    event
                    for event in owners
                    if any(
                        _evidence_quote_in_text(quote, event_slice)
                        for event_slice in _event_slice_text(event, ref, rounds)
                    )
                ]
            if len(matching) != 1:
                warning = (
                    f"{key} 的依据 {ref} 无法唯一落到一个 Event；"
                    "脚本保留疑点，不强行分配"
                )
                warnings.append(warning)
                _append_warning(state, warning)
                continue

            event = matching[0]
            related = event.setdefault("related_entity_keys", [])
            if key not in related:
                related.append(key)
            if entity_type == "character":
                participants = event.setdefault("participants", [])
                if key not in participants:
                    participants.append(key)
                    warning = f"{key} 由 {ref} 自动挂入 {event.get('id')} 参与者"
                    warnings.append(warning)
                    _append_warning(state, warning)
            elif entity_type == "location":
                locations = event.setdefault("locations", [])
                if key not in locations:
                    locations.append(key)
                roles = [
                    role
                    for role in _as_list(evidence.get("roles"))
                    if role in LOCATION_ROLES
                ] or ["primary"]
                occurrences = event.setdefault("location_occurrences", [])
                if not any(
                    item.get("entity_key") == key and ref in item.get("source_refs", [])
                    for item in occurrences
                    if isinstance(item, dict)
                ):
                    occurrences.append(
                        {
                            "entity_key": key,
                            "roles": roles,
                            "source_refs": [ref],
                        }
                    )
    return _unique(warnings)


def _mapped_id(state: dict[str, Any], namespace: str, key: str, entity_type: str) -> str:
    mapping = state["id_maps"][namespace]
    if key not in mapping:
        mapping[key] = new_entity_id(entity_type)
    return mapping[key]


def _mapped_local_id(state: dict[str, Any], key: str, kind: str) -> str:
    mapping = state["id_maps"]["local"]
    compound = f"{kind}:{key}"
    if compound not in mapping:
        mapping[compound] = new_local_id(kind)
    return mapping[compound]


def _management(timestamp: str) -> dict[str, Any]:
    return {
        "schema_version": "0.2.0",
        "data": {
            "created_at": timestamp,
            "updated_at": timestamp,
            "data_source": "system_generated",
            "revision": 1,
            "lifecycle_status": "active",
        },
    }


def _ref(state: dict[str, Any], candidate_key: str) -> dict[str, str]:
    entity_type = entity_type_from_key(candidate_key)
    if entity_type is None:
        raise ValueError(f"候选键非法：{candidate_key}")
    return {
        "id": _mapped_id(state, "entity", candidate_key, entity_type),
        "type": entity_type,
    }


def _nonempty_strings(values: Any, maximum: int | None = None) -> list[str]:
    result = _unique(
        str(value).strip()
        for value in _as_list(values)
        if str(value).strip()
    )
    return result[:maximum] if maximum is not None else result


def _character_components(
    state: dict[str, Any], candidate_key: str, candidate: dict[str, Any]
) -> dict[str, Any]:
    """只物化满足现有 Schema 最小条件的 Character 专项 Component。"""

    data = candidate.get("character_data") or {}
    components: dict[str, Any] = {}
    profile = data.get("profile") or {}
    if str(profile.get("species", "")).strip() and str(
        profile.get("background", "")
    ).strip():
        profile_data = {
            field: str(profile[field]).strip()
            for field in (
                "species",
                "gender",
                "age_description",
                "background",
                "appearance",
            )
            if str(profile.get(field, "")).strip()
        }
        components["character_profile"] = {
            "schema_version": "0.1.0",
            "data": profile_data,
        }
    character_state = data.get("state") or {}
    if all(
        field in character_state
        for field in ("physical_condition", "mental_condition", "current_emotions")
    ) and str(character_state.get("physical_condition", "")).strip() and str(
        character_state.get("mental_condition", "")
    ).strip():
        components["character_state"] = {
            "schema_version": "0.1.0",
            "data": {
                "physical_condition": str(
                    character_state["physical_condition"]
                ).strip(),
                "mental_condition": str(
                    character_state["mental_condition"]
                ).strip(),
                "current_emotions": _nonempty_strings(
                    character_state.get("current_emotions"), 16
                ),
            },
        }
    personality = data.get("personality") or {}
    if str(personality.get("summary", "")).strip():
        personality_data: dict[str, Any] = {
            "summary": str(personality["summary"]).strip()
        }
        traits = _nonempty_strings(personality.get("traits"), 24)
        tendencies = _nonempty_strings(
            personality.get("behavioral_tendencies"), 24
        )
        if traits:
            personality_data["traits"] = traits
        if tendencies:
            personality_data["behavioral_tendencies"] = tendencies
        motivations = [
            {
                "motivation_id": _mapped_local_id(
                    state, f"{candidate_key}:{description}", "motivation"
                ),
                "description": description,
            }
            for description in _nonempty_strings(data.get("motivations"), 32)
        ]
        preferences: list[dict[str, Any]] = []
        for item in _as_list(data.get("preferences"))[:48]:
            if not isinstance(item, dict):
                continue
            attitude = item.get("attitude")
            subject = str(item.get("subject", "")).strip()
            if attitude not in {"like", "dislike"} or not subject:
                continue
            preference: dict[str, Any] = {
                "preference_id": _mapped_local_id(
                    state, f"{candidate_key}:{attitude}:{subject}", "preference"
                ),
                "attitude": attitude,
                "subject": subject,
            }
            if str(item.get("description", "")).strip():
                preference["description"] = str(item["description"]).strip()
            if is_entity_key(item.get("related_entity_key")):
                preference["related_entity_ref"] = _ref(
                    state, item["related_entity_key"]
                )
            preferences.append(preference)
        components["character_behavior_profile"] = {
            "schema_version": "0.1.0",
            "data": {
                "personality": personality_data,
                "motivations": motivations,
                "preferences": preferences,
            },
        }

    objectives: list[dict[str, Any]] = []
    for item in _as_list(data.get("objectives"))[:24]:
        if not isinstance(item, dict):
            continue
        description = str(item.get("description", "")).strip()
        horizon = item.get("horizon")
        if not description or horizon not in {"short_term", "medium_term"}:
            continue
        objective: dict[str, Any] = {
            "objective_id": _mapped_local_id(
                state, f"{candidate_key}:{description}", "objective"
            ),
            "description": description,
            "horizon": horizon,
        }
        refs = [
            _ref(state, key)
            for key in _as_list(item.get("related_entity_keys"))
            if is_entity_key(key)
        ]
        if refs:
            objective["related_entity_refs"] = refs
        objectives.append(objective)
    if objectives:
        components["character_objective"] = {
            "schema_version": "0.1.0",
            "data": {"objectives": objectives},
        }

    location_key = data.get("current_location_key")
    if is_entity_key(location_key, "location"):
        components["current_location_reference"] = {
            "schema_version": "0.1.0",
            "data": {"location_ref": _ref(state, location_key)},
        }
    inventory = []
    for item in _as_list(data.get("inventory")):
        if not isinstance(item, dict) or not is_entity_key(item.get("item_key"), "item"):
            continue
        roles = [
            role for role in _unique(_as_list(item.get("roles"))) if role in INVENTORY_ROLES
        ]
        if roles:
            inventory.append(
                {
                    "item_ref": _ref(state, item["item_key"]),
                    "inventory_roles": roles,
                }
            )
    if inventory:
        components["inventory_reference"] = {
            "schema_version": "0.1.0",
            "data": {"item_refs": inventory},
        }
    skills = []
    for item in _as_list(data.get("skills")):
        if not isinstance(item, dict) or not is_entity_key(item.get("skill_key"), "skill"):
            continue
        proficiency = str(item.get("proficiency_description", "")).strip()
        if proficiency:
            skills.append(
                {
                    "skill_ref": _ref(state, item["skill_key"]),
                    "proficiency_description": proficiency,
                }
            )
    if skills:
        components["skill_reference"] = {
            "schema_version": "0.1.0",
            "data": {"skill_refs": skills},
        }
    return components


def materialize_network(state: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """把批次候选转成现有 Schema 能校验的封闭 Entity 网络。"""

    reconcile_key_detail_actors(state)
    reconcile_witnessed_memory_participants(state)
    ensure_minimal_entity_nodes(state)
    timestamp = state["run_timestamp"]
    entities: list[dict[str, Any]] = []

    for candidate_key, candidate in sorted(state["entity_candidates"].items()):
        entity_type = entity_type_from_key(candidate_key)
        if entity_type is None:
            continue
        components: dict[str, Any] = {
            "entity_management": _management(timestamp),
            "identity": {
                "schema_version": "0.1.0",
                "data": {
                    "primary_name": str(candidate.get("primary_name", "")).strip()
                    or candidate_key.split(":", 1)[1],
                    "aliases": _nonempty_strings(candidate.get("aliases")),
                },
            },
        }
        if entity_type == "character":
            components.update(_character_components(state, candidate_key, candidate))
        entities.append(
            {
                "id": _mapped_id(state, "entity", candidate_key, entity_type),
                "type": entity_type,
                "description": str(candidate.get("description", "")).strip()
                or candidate_key.split(":", 1)[1],
                "components": components,
            }
        )

    event_formal_ids: dict[str, str] = {}
    for event in state["events"]:
        event_id = _mapped_id(state, "event", event["id"], "event")
        event_formal_ids[event["id"]] = event_id
        components: dict[str, Any] = {
            "entity_management": _management(timestamp),
        }
        event_time = event.get("event_time")
        if isinstance(event_time, dict) and isinstance(event_time.get("start_time"), dict):
            time_data = {"start_time": deepcopy(event["event_time"]["start_time"])}
            if isinstance(event_time.get("end_time"), dict):
                time_data["end_time"] = deepcopy(event_time["end_time"])
            components["event_time"] = {
                "schema_version": "0.1.0",
                "data": time_data,
            }
        occurrences = []
        for sequence, item in enumerate(event.get("location_occurrences", [])):
            key = item.get("entity_key") if isinstance(item, dict) else None
            if not is_entity_key(key, "location"):
                continue
            roles = [
                role
                for role in _unique(_as_list(item.get("roles")))
                if role in LOCATION_ROLES
            ]
            if not roles:
                roles = ["primary"]
            occurrences.append(
                {
                    "location_ref": _ref(state, key),
                    "location_roles": roles,
                    "sequence": sequence,
                }
            )
        if occurrences:
            components["event_location_reference"] = {
                "schema_version": "0.1.0",
                "data": {"location_refs": occurrences},
            }
        participant_keys = _unique(
            key
            for key in event.get("participants", [])
            if is_entity_key(key, "character")
        )
        if participant_keys:
            components["event_participant_reference"] = {
                "schema_version": "0.1.0",
                "data": {
                    "participant_refs": [
                        {
                            "participant_ref": _ref(state, key),
                            "participation_roles": ["participant"],
                        }
                        for key in participant_keys
                    ]
                },
            }
        entities.append(
            {
                "id": event_id,
                "type": "event",
                "description": str(event.get("description", "")).strip()
                or str(event.get("title", "")).strip()
                or "形成中的叙事事件。",
                "components": components,
            }
        )

    relation_formal_ids: dict[str, str] = {}
    relation_aspect_ids: dict[str, list[str]] = {}
    for key, candidate in sorted(state["relation_candidates"].items()):
        relation_id = _mapped_id(state, "relation", key, "character_relation")
        relation_formal_ids[key] = relation_id
        participant_keys = sorted(candidate.get("participant_keys", []))
        if len(participant_keys) != 2:
            continue
        participant_refs = sorted(
            (_ref(state, candidate_key) for candidate_key in participant_keys),
            key=lambda ref: ref["id"],
        )
        endpoint_key_by_id = {
            _ref(state, candidate_key)["id"]: candidate_key
            for candidate_key in participant_keys
        }
        canonical_keys = [endpoint_key_by_id[ref["id"]] for ref in participant_refs]
        aspects: list[dict[str, Any]] = []
        aspect_ids: list[str] = []
        for raw_aspect in candidate.get("aspects", []):
            if not isinstance(raw_aspect, dict):
                continue
            kind = str(raw_aspect.get("kind", "")).strip()
            description = str(raw_aspect.get("description", "")).strip()
            if not SNAKE_CASE_RE.fullmatch(kind) or not description:
                continue
            aspect_id = _mapped_local_id(state, f"{key}:{kind}", "relation_aspect")
            aspect_ids.append(aspect_id)
            roles_by_key = {
                item.get("character_key"): _nonempty_strings(item.get("roles"), 8)
                for item in _as_list(raw_aspect.get("participant_roles"))
                if isinstance(item, dict)
            }
            aspects.append(
                {
                    "aspect_id": aspect_id,
                    "kind": kind,
                    "description": description,
                    "status": raw_aspect.get("status", "active"),
                    "perspective": {"mode": "shared_fact"},
                    "participant_roles": [
                        {
                            "character_ref": _ref(state, candidate_key),
                            "roles": roles_by_key.get(candidate_key)
                            or ["related_person"],
                        }
                        for candidate_key in canonical_keys
                    ],
                    "disclosure": {
                        "visibility": raw_aspect.get("visibility", "private"),
                        "concealed_from_refs": [],
                    },
                }
            )
        relation_aspect_ids[key] = aspect_ids
        directional_states: list[dict[str, Any]] = []
        for direction in candidate.get("directional_states", []):
            if not isinstance(direction, dict):
                continue
            from_key = direction.get("from_key")
            toward_key = direction.get("toward_key")
            summary = str(direction.get("summary", "")).strip()
            if (
                {from_key, toward_key} != set(participant_keys)
                or from_key == toward_key
                or not summary
            ):
                continue
            item: dict[str, Any] = {
                "from_character_ref": _ref(state, from_key),
                "toward_character_ref": _ref(state, toward_key),
                "summary": summary,
            }
            tags = [
                str(tag)
                for tag in _as_list(direction.get("tags"))
                if SNAKE_CASE_RE.fullmatch(str(tag))
            ]
            if tags:
                item["tags"] = _unique(tags)
            directional_states.append(item)
        entities.append(
            {
                "id": relation_id,
                "type": "character_relation",
                "description": str(candidate.get("description", "")).strip()
                or "两名角色之间已在叙事中形成关联。",
                "components": {
                    "entity_management": _management(timestamp),
                    "relation_endpoint_reference": {
                        "schema_version": "0.1.0",
                        "data": {"participant_refs": participant_refs},
                    },
                    "character_relation_aspects": {
                        "schema_version": "0.1.0",
                        "data": {"aspects": aspects},
                    },
                    "character_relation_state": {
                        "schema_version": "0.1.0",
                        "data": {"directional_states": directional_states},
                    },
                    "history_index": {
                        "schema_version": "0.1.0",
                        "data": {"event_refs": []},
                    },
                },
            }
        )

    for memory in state["memories"]:
        owner_key = memory.get("owner_key")
        event_ids = [
            event_formal_ids[event_id]
            for event_id in memory.get("event_ids", [])
            if event_id in event_formal_ids
        ]
        if not is_entity_key(owner_key, "character") or not event_ids:
            continue
        memory_id = _mapped_id(state, "memory", memory["id"], "memory")
        components: dict[str, Any] = {
            "entity_management": _management(timestamp),
            "memory_owner_reference": {
                "schema_version": "0.1.0",
                "data": {"owner_ref": _ref(state, owner_key)},
            },
            "source_event_reference": {
                "schema_version": "0.1.0",
                "data": {
                    "event_refs": [
                        {"id": event_id, "type": "event"}
                        for event_id in _unique(event_ids)
                    ],
                    "acquisition_mode": memory.get(
                        "acquisition_mode", "unknown_from_event"
                    ),
                },
            },
        }
        relation_links = []
        for pair in memory.get("related_relation_pairs", []):
            key = relation_key(pair)
            if key not in relation_formal_ids:
                continue
            relation_links.append(
                {
                    "relation_ref": {
                        "id": relation_formal_ids[key],
                        "type": "character_relation",
                    },
                    "related_aspect_ids": relation_aspect_ids.get(key, []),
                    "context_roles": [
                        "important" if memory.get("importance") == "core" else "current_basis"
                    ],
                }
            )
        if relation_links:
            components["relation_context_reference"] = {
                "schema_version": "0.1.0",
                "data": {"relation_links": relation_links},
            }
        entities.append(
            {
                "id": memory_id,
                "type": "memory",
                "description": str(memory.get("description", "")).strip(),
                "components": components,
            }
        )

    projected = rebuild_derived_indexes(entities)
    report = EntityNetworkValidator(ROOT).validate(
        projected, require_derived_indexes=True
    )
    return projected, report.to_dict()


def _task_summary(task_result: dict[str, Any]) -> dict[str, Any]:
    """为记录首页压缩任务状态，完整正文仍在批次详情中。"""

    attempts = task_result.get("attempts", [])
    final = attempts[-1] if attempts else {}
    return {
        "ok": task_result.get("ok"),
        "elapsed_seconds": task_result.get("elapsed_seconds"),
        "candidate_attempts": len(attempts),
        "transport_failures": sum(
            len(attempt.get("transport_failures", [])) for attempt in attempts
        ),
        "warnings": final.get("warnings", []),
        "validation_errors": final.get("validation_errors", []),
        "api": final.get("api", {}),
    }


def _pre(value: Any) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    return "<pre>" + html.escape(value) + "</pre>"


def render_record(run: dict[str, Any]) -> str:
    """把全部提示词、正式回复和机器结果集中渲染进唯一记录文件。"""

    event_only = run.get("task_set") == "event"
    title = (
        "# 240 Event 边界与内容单路短回归完整记录"
        if event_only
        else "# 240 Event、Memory 与全 Entity 单次提取复测完整记录"
    )
    lines = [
        title,
        "",
        "> 本文件由测试工具持续覆盖写入；一场运行只产生这一份公开记录。",
        "> 不保存 API 密钥，也不请求或记录模型隐藏思维过程。",
        "",
        "## 1. 运行范围",
        "",
        f"- 开始时间：{run.get('started_at', '')}",
        f"- 当前状态：{run.get('status', '')}",
        f"- 原始记录：`{run.get('chat_jsonl', '')}`",
        f"- 模型：`{run.get('model', '')}`",
        f"- 接口：`{run.get('endpoint', '')}`",
        f"- 任务范围：`{run.get('task_set', 'full')}`",
        f"- 批次：每批 {run.get('batch_size')} 轮，目标 {run.get('batch_count')} 批",
        f"- 最大并发：{run.get('concurrency')}",
        f"- 调度：`{run.get('schedule', '')}`；错峰间隔 {run.get('stagger_seconds', 0)} 秒",
        f"- 思考设置：`{run.get('thinking_modes', run.get('calibration_modes', {}))}`",
        f"- 返回格式：`{run.get('response_format', '')}`",
        f"- 输出安全上限：`{run.get('max_tokens', {})}`；这是截断上限，不是期望输出长度。",
        f"- 总超时／流式无进展超时：{run.get('timeout_seconds', '')}／{run.get('stream_idle_timeout_seconds', '')} 秒。",
        f"- 每项语义候选上限：{run.get('candidate_attempt_limit', 1)} 次；无正文的传输失败另行恢复。",
        f"- 每项传输尝试上限：{run.get('transport_attempt_limit', 1)} 次。",
        "- Event 判断同时核对事实覆盖、局部中心和边界感知；不预设边界数量。",
        "",
    ]
    if run.get("calibration"):
        lines.extend(
            [
                "## 1.1 同一输入的思考强度校准",
                "",
                run.get("calibration_note", ""),
                "",
            ]
        )
        for item in run.get("calibration", []):
            task = item.get("task", {})
            summary = _task_summary(task)
            attempts = task.get("attempts", [])
            api = attempts[-1].get("api", {}) if attempts else {}
            lines.extend(
                [
                    f"### `{item.get('mode')}`",
                    "",
                    f"- 结构检查：{'通过' if summary['ok'] else '未通过'}",
                    f"- 总耗时：{summary['elapsed_seconds']} 秒",
                    f"- 推理字符／正式正文字符：{api.get('reasoning_chars', 0)}／{api.get('content_chars', 0)}",
                    f"- 人工边界对照：`{item.get('gold_evaluation', {}).get('status', '未形成可比较结果')}`",
                    "",
                    "<details><summary>完整系统提示词</summary>",
                    "",
                    _pre(task.get("system_prompt", "")),
                    "</details>",
                    "",
                ]
            )
            for attempt in attempts:
                number = attempt.get("candidate_attempt")
                lines.extend(
                    [
                        f"<details><summary>第 {number} 次候选：完整用户提示词</summary>",
                        "",
                        _pre(attempt.get("user_prompt", "")),
                        "</details>",
                        "",
                        f"<details><summary>第 {number} 次候选：模型完整正式回复</summary>",
                        "",
                        _pre(attempt.get("raw", "")),
                        "</details>",
                        "",
                        f"<details><summary>第 {number} 次候选：解析、接口与检查结果</summary>",
                        "",
                        _pre(
                            {
                                "api": attempt.get("api", {}),
                                "transport_failures": attempt.get(
                                    "transport_failures", []
                                ),
                                "parse_error": attempt.get("parse_error"),
                                "validation_errors": attempt.get(
                                    "validation_errors", []
                                ),
                                "warnings": attempt.get("warnings", []),
                                "parsed_plan": attempt.get("parsed_plan"),
                                "applied_state": item.get("state", {}),
                                "gold_evaluation": item.get(
                                    "gold_evaluation", {}
                                ),
                            }
                        ),
                        "</details>",
                        "",
                    ]
                )
    lines.extend(["## 2. 任务总览", ""])
    for batch in run.get("batches", []):
        lines.append(f"### 第 {batch.get('batch_number')} 批（轮次 {batch.get('round_start')}—{batch.get('round_end')}）")
        lines.append("")
        lines.append(f"- 批次状态：{batch.get('status')}")
        lines.append(f"- 墙钟耗时：{batch.get('elapsed_seconds', '')} 秒")
        for task_name in ("event", "memory", "entity"):
            if task_name in batch.get("tasks", {}):
                summary = _task_summary(batch["tasks"][task_name])
                lines.append(
                    f"- {task_name}：{'通过' if summary['ok'] else '失败'}；"
                    f"{summary['elapsed_seconds']} 秒；候选 {summary['candidate_attempts']} 次；"
                    f"传输失败 {summary['transport_failures']} 次"
                )
        network = batch.get("network_validation")
        if isinstance(network, dict) and not network.get("skipped"):
            lines.append(
                f"- 实体网络：{'通过' if network.get('valid') else '失败'}；"
                f"{network.get('entity_count')} 个正式 Entity；{len(network.get('errors', []))} 项错误"
            )
        lines.append("")

    lines.extend(["## 3. 完整批次记录", ""])
    for batch in run.get("batches", []):
        lines.extend(
            [
                f"### 第 {batch.get('batch_number')} 批：轮次 {batch.get('round_start')}—{batch.get('round_end')}",
                "",
                f"批次状态：`{batch.get('status')}`",
                "",
            ]
        )
        for task_name in ("event", "memory", "entity"):
            task = batch.get("tasks", {}).get(task_name)
            if not isinstance(task, dict):
                continue
            lines.extend(
                [
                    f"#### {task_name} 任务",
                    "",
                    "<details><summary>完整系统提示词</summary>",
                    "",
                    _pre(task.get("system_prompt", "")),
                    "</details>",
                    "",
                ]
            )
            for attempt in task.get("attempts", []):
                attempt_number = attempt.get("candidate_attempt")
                lines.extend(
                    [
                        f"<details><summary>第 {attempt_number} 次候选：完整用户提示词</summary>",
                        "",
                        _pre(attempt.get("user_prompt", "")),
                        "</details>",
                        "",
                        f"<details><summary>第 {attempt_number} 次候选：模型完整正式回复</summary>",
                        "",
                        _pre(attempt.get("raw", "")),
                        "</details>",
                        "",
                        f"<details><summary>第 {attempt_number} 次候选：解析、接口与检查结果</summary>",
                        "",
                        _pre(
                            {
                                "api": attempt.get("api", {}),
                                "transport_failures": attempt.get(
                                    "transport_failures", []
                                ),
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
                    "#### 固定脚本应用结果",
                    "",
                    _pre(
                        {
                            "operations": batch.get("operations", []),
                            "binding_warnings": batch.get("binding_warnings", []),
                            "stub_warnings": batch.get("stub_warnings", []),
                            "network_validation": batch.get("network_validation"),
                            "state_after_batch": batch.get("state_after_batch"),
                        }
                    ),
                    "",
                ]
            )

    lines.extend(
        [
            "## 4. 最终机器结果",
            "",
            "### 4.1 人工边界样例对照",
            "",
            _pre(run.get("gold_evaluation", {})),
            "",
            "### 4.2 最终候选状态",
            "",
            _pre(run.get("final_state", {})),
            "",
        ]
    )
    if not event_only:
        lines.extend(
            [
                "### 4.3 类型覆盖",
                "",
                _pre(run.get("type_counts", {})),
                "",
                "### 4.4 最终正式 Entity 网络",
                "",
                _pre(
                    {
                        "validation": run.get("final_network_validation", {}),
                        "entities": run.get("final_network", []),
                    }
                ),
                "",
            ]
        )
    lines.extend(
        [
            "## 5. 场外复核",
            "",
            run.get("human_review")
            or "运行完成后由 Codex 对照原文、人工样例和机器检查结果补写。",
            "",
        ]
    )
    if run.get("preflight_record"):
        lines.extend(
            [
                "## 附录：本次复测启动前的校准或未提交记录",
                "",
                "> 为避免散落多个文件，前置校准、异常或未提交记录保留在同一文件中供核查。",
                "",
                "<details><summary>展开前置完整原记录</summary>",
                "",
                _pre(run["preflight_record"]),
                "</details>",
                "",
            ]
        )
    return "\n".join(lines)


def persist_single_record(run: dict[str, Any], output: Path, api_key: str) -> None:
    """原子覆盖唯一公开记录；写入前主动检查密钥没有混入。"""

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


def public_state(state: dict[str, Any]) -> dict[str, Any]:
    """记录候选语义和映射，不写入任何认证或调用内部对象。"""

    return deepcopy(state)


def flatten_record_history(record: str) -> str:
    """把递归附录压成线性历史，避免每次恢复都成倍复制旧记录。"""

    if not record.strip():
        return ""
    pending = [record]
    flattened: list[str] = []
    seen: set[str] = set()
    while pending:
        current = pending.pop(0)
        current_run, separator, appendix = current.partition("## 附录：")
        current_run = current_run.rstrip()
        if current_run and current_run not in seen:
            flattened.append(current_run)
            seen.add(current_run)
        if not separator:
            continue
        for match in re.finditer(r"<pre>(.*?)</pre>", appendix, re.DOTALL):
            decoded = html.unescape(match.group(1)).strip()
            if decoded.startswith("# 240 "):
                pending.append(decoded)
                break
    return "\n\n---\n\n".join(flattened)


def _json_objects_from_record(
    record: str, *, _depth: int = 0
) -> list[dict[str, Any]]:
    """读取本工具写入的 ``<pre>`` JSON，用于从同一记录恢复检查点。"""

    current_run, separator, appendix = record.partition("## 附录：")
    objects: list[dict[str, Any]] = []
    if separator and _depth < 12:
        for match in re.finditer(r"<pre>(.*?)</pre>", appendix, re.DOTALL):
            decoded = html.unescape(match.group(1)).strip()
            if decoded.startswith("# 240 Event、Memory 与全 Entity"):
                objects.extend(_json_objects_from_record(decoded, _depth=_depth + 1))
                break
    for match in re.finditer(r"<pre>(.*?)</pre>", current_run, re.DOTALL):
        decoded = html.unescape(match.group(1)).strip()
        if not decoded.startswith("{"):
            continue
        try:
            value = json.loads(decoded)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def _source_round_numbers(value: Any) -> set[int]:
    """递归收集候选中真正的回合来源书签，用于识别失败批次的任务结果。"""

    numbers: set[int] = set()
    if isinstance(value, str):
        match = re.fullmatch(r"r(\d{4})\.(?:user|assistant)", value)
        if match:
            numbers.add(int(match.group(1)))
    elif isinstance(value, list):
        for item in value:
            numbers.update(_source_round_numbers(item))
    elif isinstance(value, dict):
        for item in value.values():
            numbers.update(_source_round_numbers(item))
    return numbers


def recovery_state_and_current_plans(
    record: str,
    target_round_end: int | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """取得最后已提交状态及其后失败批次中仍可复用的各任务候选。"""

    states: list[dict[str, Any]] = []
    plans_by_task: dict[str, list[dict[str, Any]]] = {
        "event": [],
        "memory": [],
        "entity": [],
    }
    for value in _json_objects_from_record(record):
        state = value.get("state_after_batch")
        if isinstance(state, dict) and isinstance(state.get("events"), list):
            states.append(state)
        plan = value.get("parsed_plan")
        if not isinstance(plan, dict):
            continue
        if "old_forming_disposition" in plan and "event_updates" in plan:
            plans_by_task["event"].append(plan)
        elif isinstance(plan.get("memories"), list):
            plans_by_task["memory"].append(plan)
        elif isinstance(plan.get("entities"), list) and isinstance(
            plan.get("relations"), list
        ):
            plans_by_task["entity"].append(plan)

    if target_round_end is None:
        state = deepcopy(states[-1]) if states else initial_unified_state()
    else:
        eligible = [
            (index, candidate)
            for index, candidate in enumerate(states)
            if _processed_round_end(candidate) < target_round_end
        ]
        state = (
            deepcopy(
                max(
                    eligible,
                    key=lambda item: (_processed_round_end(item[1]), item[0]),
                )[1]
            )
            if eligible
            else initial_unified_state()
        )
    processed_round = _processed_round_end(state)
    current: dict[str, dict[str, Any]] = {}
    for task, plans in plans_by_task.items():
        for plan in reversed(plans):
            source_rounds = _source_round_numbers(plan)
            if (
                source_rounds
                and max(source_rounds) > processed_round
                and (
                    target_round_end is None
                    or max(source_rounds) <= target_round_end
                )
            ):
                current[task] = deepcopy(plan)
                break
    return state, current


def recovery_inputs_from_record(
    record: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """兼容旧测试入口：恢复当前批次的 Event 与 Memory 成功候选。"""

    state, plans = recovery_state_and_current_plans(record)
    if "event" not in plans or "memory" not in plans:
        raise ValueError("唯一记录中缺少可恢复的已提交状态或成功 Event/Memory 候选")
    return state, plans["event"], plans["memory"]


def _processed_round_end(state: dict[str, Any]) -> int:
    values = []
    for event in state.get("events", []):
        for source_ref in event.get("source_refs", []):
            match = re.fullmatch(r"r(\d{4})\.(?:user|assistant)", str(source_ref))
            if match:
                values.append(int(match.group(1)))
    return max(values, default=0)


def _reused_task_result(
    task: str,
    system_prompt: str,
    plan: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "task": task,
        "ok": True,
        "fatal_error": None,
        "system_prompt": system_prompt,
        "attempts": [
            {
                "candidate_attempt": 1,
                "user_prompt": "复用前一运行中同一批次、同一快照的成功候选；完整原提示词见本文件附录。",
                "raw": "复用前一运行的完整正式回复；原文见本文件附录，未再次调用模型。",
                "api": {"reused_from_prior_record": True},
                "transport_failures": [],
                "parsed_plan": deepcopy(plan),
                "parse_error": None,
                "validation_errors": [],
                "warnings": warnings,
            }
        ],
        "plan": deepcopy(plan),
        "warnings": warnings,
        "elapsed_seconds": 0.0,
        "reused_from_prior_record": True,
    }


def recover_final_batch(args: argparse.Namespace) -> dict[str, Any]:
    """逐项复验失败批次，只重新调用没有可复用候选的任务。"""

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise RuntimeError(f"环境变量 {args.api_key_env} 中没有 API 密钥")
    output = Path(args.output).resolve()
    if not output.exists():
        raise RuntimeError("恢复模式需要输出路径中已有失败记录")
    prior_record = flatten_record_history(output.read_text(encoding="utf-8"))
    state, recovered_plans = recovery_state_and_current_plans(prior_record)
    recovered_plans = {
        name: plan
        for name, plan in recovered_plans.items()
        if name in task_names_for_set(args.task_set)
    }
    processed_round_end = _processed_round_end(state)
    all_rounds = list(iter_rounds(Path(args.chat_jsonl).resolve()))
    rounds = [item for item in all_rounds if item["round"] > processed_round_end][
        : args.batch_size
    ]
    if not rounds:
        raise RuntimeError("恢复检查点后没有可处理的新回合")
    if not recovered_plans and processed_round_end > 0:
        print(
            f"第 {processed_round_end} 轮检查点完整；直接并发继续下一批",
            flush=True,
        )
        return run_probe(
            args,
            initial_state_override=state,
            start_round=processed_round_end,
            preflight_record_override=prior_record,
        )

    batch_number = processed_round_end // args.batch_size + 1
    run: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "recovering_failed_tasks",
        "chat_jsonl": str(Path(args.chat_jsonl).resolve()),
        "endpoint": args.endpoint,
        "model": args.model,
        "task_set": args.task_set,
        "batch_size": args.batch_size,
        "batch_count": 1,
        "concurrency": 1,
        "schedule": "serial_failed_tasks_only",
        "stagger_seconds": 0,
        "thinking_modes": {
            name: {
                "event": args.event_thinking,
                "memory": args.memory_thinking,
                "entity": args.entity_thinking,
            }[name]
            for name in task_names_for_set(args.task_set)
        },
        "response_format": args.response_format,
        "max_tokens": {
            name: {
                "event": args.event_max_tokens,
                "memory": args.memory_max_tokens,
                "entity": args.entity_max_tokens,
            }[name]
            for name in task_names_for_set(args.task_set)
        },
        "timeout_seconds": args.timeout,
        "stream_idle_timeout_seconds": args.stream_idle_timeout,
        "candidate_attempt_limit": args.candidate_attempt_limit,
        "transport_attempt_limit": args.transport_attempt_limit,
        "batches": [],
        "gold_evaluation": {},
        "type_counts": {},
        "final_state": {},
        "final_network": [],
        "final_network_validation": {},
        "preflight_record": prior_record,
    }
    batch: dict[str, Any] = {
        "batch_number": batch_number,
        "round_start": rounds[0]["round"],
        "round_end": rounds[-1]["round"],
        "status": "recovering_failed_tasks",
        "tasks": {},
    }
    run["batches"].append(batch)
    task_specs = build_task_specs(args, state, rounds, batch_number)
    recovery_started = time.perf_counter()
    reused_names: list[str] = []
    called_names: list[str] = []
    for name, spec in task_specs.items():
        recovered = recovered_plans.get(name)
        if recovered is not None:
            normalized = spec["normalizer"](recovered)
            errors, warnings = spec["validator"](normalized)
            if not errors:
                batch["tasks"][name] = _reused_task_result(
                    name, spec["system_prompt"], normalized, warnings
                )
                reused_names.append(name)
                continue
        called_names.append(name)
        print(f"恢复批次只补取 {name}", flush=True)
        batch["tasks"][name] = run_model_task(
            task=name,
            endpoint=args.endpoint,
            api_key=api_key,
            model=args.model,
            system_prompt=spec["system_prompt"],
            user_prompt=spec["user_prompt"],
            timeout=args.timeout,
            max_tokens=spec["max_tokens"],
            thinking_mode=spec["thinking_mode"],
            response_format=spec["response_format"],
            stream_idle_timeout=args.stream_idle_timeout,
            candidate_attempt_limit=args.candidate_attempt_limit,
            transport_attempt_limit=args.transport_attempt_limit,
            normalizer=spec["normalizer"],
            validator=spec["validator"],
        )
        persist_single_record(run, output, api_key)

    batch["elapsed_seconds"] = round(time.perf_counter() - recovery_started, 3)
    if not all(batch["tasks"].get(name, {}).get("ok") for name in task_specs):
        batch["status"] = "task_recovery_failed"
        run["status"] = "failed"
        persist_single_record(run, output, api_key)
        return run

    event_plan = batch["tasks"]["event"]["plan"]
    candidate_state, operations, _ = apply_event_candidate(state, event_plan)
    binding_warnings: list[str] = []
    stub_warnings: list[str] = []
    if args.task_set == "full":
        memory_plan = batch["tasks"]["memory"]["plan"]
        entity_plan = batch["tasks"]["entity"]["plan"]
        candidate_state, binding_warnings = apply_memory_candidates(
            candidate_state, memory_plan, rounds
        )
        candidate_state = apply_entity_candidates(candidate_state, entity_plan)
        stub_warnings = [
            *reconcile_entity_event_links(candidate_state, rounds),
            *reconcile_source_inventory(candidate_state, rounds),
            *ensure_minimal_entity_nodes(candidate_state),
        ]
        network, report = materialize_network(candidate_state)
    else:
        network, report = [], skipped_network_report()
    batch["operations"] = [
        "复用任务：" + ("、".join(reused_names) if reused_names else "无"),
        "重新调用任务：" + ("、".join(called_names) if called_names else "无"),
        *operations,
    ]
    batch["binding_warnings"] = binding_warnings
    batch["stub_warnings"] = stub_warnings
    batch["network_validation"] = report
    batch["state_after_batch"] = public_state(candidate_state)
    batch["status"] = "committed_after_task_recovery" if report.get("valid") else "network_validation_failed"
    counts: dict[str, int] = {}
    for entity in network:
        counts[entity["type"]] = counts.get(entity["type"], 0) + 1
    run["status"] = "completed" if report.get("valid") else "failed"
    run["completed_at"] = datetime.now(timezone.utc).isoformat()
    run["final_state"] = public_state(candidate_state)
    run["final_network"] = network
    run["final_network_validation"] = report
    run["type_counts"] = counts
    if args.gold:
        gold = load_gold_fixture(
            Path(args.gold).resolve(), Path(args.chat_jsonl).resolve()
        )
        run["gold_evaluation"] = evaluate_state_against_gold(
            candidate_state, gold, rounds[-1]["round"]
        )
    persist_single_record(run, output, api_key)
    result_label = (
        "Event 单路状态已提交"
        if args.task_set == "event"
        else f"正式网络 {report.get('entity_count')} 项"
    )
    print(
        f"失败任务恢复完成；复用 {reused_names or ['无']}，重新调用 {called_names or ['无']}；"
        f"{result_label}，校验={'通过' if report.get('valid') else '失败'}",
        flush=True,
    )
    if report.get("valid") and rounds[-1]["round"] < args.batches * args.batch_size:
        recovered_record = flatten_record_history(output.read_text(encoding="utf-8"))
        print(
            f"恢复批次已提交；从第 {rounds[-1]['round'] + 1} 轮继续剩余短回归，"
            "不重做已成功任务",
            flush=True,
        )
        return run_probe(
            args,
            initial_state_override=candidate_state,
            start_round=rounds[-1]["round"],
            preflight_record_override=recovered_record,
        )
    return run


def finalize_recovered_offline(args: argparse.Namespace) -> dict[str, Any]:
    """只使用记录中已有的三项候选完成提交；缺一项或复验失败都拒绝继续。"""

    if args.task_set != "full":
        raise RuntimeError("离线三路封存只适用于 task_set=full；Event 单路请使用失败任务恢复")
    output = Path(args.output).resolve()
    if not output.exists():
        raise RuntimeError("离线封存需要输出路径中已有复测记录")
    prior_record = flatten_record_history(output.read_text(encoding="utf-8"))
    state, plans = recovery_state_and_current_plans(
        prior_record, args.offline_rebuild_round_end
    )
    if set(plans) != {"event", "memory", "entity"}:
        raise RuntimeError(
            "离线封存拒绝继续：记录中的 Event、Memory、Entity 候选不完整"
        )
    processed_round_end = _processed_round_end(state)
    rounds = [
        item
        for item in iter_rounds(Path(args.chat_jsonl).resolve())
        if item["round"] > processed_round_end
        and (
            args.offline_rebuild_round_end is None
            or item["round"] <= args.offline_rebuild_round_end
        )
    ][: args.batch_size]
    if not rounds:
        raise RuntimeError("离线封存没有找到检查点后的叙事回合")

    event_plan = normalize_event_plan(plans["event"], state, rounds)
    memory_plan = normalize_memory_plan(plans["memory"])
    entity_plan = normalize_entity_plan(plans["entity"], state, rounds)
    checks = {
        "event": validate_event_plan(event_plan, state, rounds),
        "memory": validate_memory_plan(memory_plan, rounds),
        "entity": validate_entity_plan(entity_plan, rounds, state),
    }
    errors = [
        f"{name}: {error}"
        for name, (task_errors, _) in checks.items()
        for error in task_errors
    ]
    if errors:
        raise RuntimeError("离线封存复验失败：" + "; ".join(errors))

    candidate_state, operations, _ = apply_event_candidate(state, event_plan)
    candidate_state, binding_warnings = apply_memory_candidates(
        candidate_state, memory_plan, rounds
    )
    candidate_state = apply_entity_candidates(candidate_state, entity_plan)
    stub_warnings = [
        *reconcile_entity_event_links(candidate_state, rounds),
        *reconcile_source_inventory(candidate_state, rounds),
        *ensure_minimal_entity_nodes(candidate_state),
    ]
    network, report = materialize_network(candidate_state)
    batch_number = processed_round_end // args.batch_size + 1
    batch = {
        "batch_number": batch_number,
        "round_start": rounds[0]["round"],
        "round_end": rounds[-1]["round"],
        "status": "committed_offline_recovery" if report.get("valid") else "network_validation_failed",
        "elapsed_seconds": 0.0,
        "tasks": {
            name: _reused_task_result(
                name,
                {
                    "event": EVENT_SYSTEM_PROMPT,
                    "memory": MEMORY_SYSTEM_PROMPT,
                    "entity": ENTITY_SYSTEM_PROMPT,
                }[name],
                plan,
                checks[name][1],
            )
            for name, plan in {
                "event": event_plan,
                "memory": memory_plan,
                "entity": entity_plan,
            }.items()
        },
        "operations": ["纯离线复验并提交；未调用任何模型。", *operations],
        "binding_warnings": binding_warnings,
        "stub_warnings": stub_warnings,
        "network_validation": report,
        "state_after_batch": public_state(candidate_state),
    }
    counts: dict[str, int] = {}
    for entity in network:
        counts[entity["type"]] = counts.get(entity["type"], 0) + 1
    run: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "status": "completed" if report.get("valid") else "failed",
        "chat_jsonl": str(Path(args.chat_jsonl).resolve()),
        "endpoint": args.endpoint,
        "model": args.model,
        "task_set": "full",
        "batch_size": args.batch_size,
        "batch_count": args.batches,
        "concurrency": 0,
        "schedule": "offline_existing_candidates_only",
        "stagger_seconds": 0,
        "thinking_modes": {},
        "response_format": "reused",
        "max_tokens": {
            "event": args.event_max_tokens,
            "memory": args.memory_max_tokens,
            "entity": args.entity_max_tokens,
        },
        "timeout_seconds": args.timeout,
        "stream_idle_timeout_seconds": args.stream_idle_timeout,
        "candidate_attempt_limit": 0,
        "transport_attempt_limit": 0,
        "batches": [batch],
        "gold_evaluation": {},
        "type_counts": counts,
        "final_state": public_state(candidate_state),
        "final_network": network,
        "final_network_validation": report,
        "preflight_record": prior_record,
    }
    if args.gold:
        gold = load_gold_fixture(
            Path(args.gold).resolve(), Path(args.chat_jsonl).resolve()
        )
        run["gold_evaluation"] = evaluate_state_against_gold(
            candidate_state, gold, rounds[-1]["round"]
        )
    persist_single_record(run, output, os.environ.get(args.api_key_env, ""))
    return run


def run_thinking_calibration(args: argparse.Namespace) -> dict[str, Any]:
    """同一首批、同一提示词顺序比较思考强度，不混入并发变量。"""

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise RuntimeError(f"环境变量 {args.api_key_env} 中没有 API 密钥")
    chat_jsonl = Path(args.chat_jsonl).resolve()
    output = Path(args.output).resolve()
    preflight_record = (
        flatten_record_history(output.read_text(encoding="utf-8"))
        if args.preserve_existing and output.exists()
        else ""
    )
    rounds = take_batch(iter(iter_rounds(chat_jsonl)), args.batch_size)
    if not rounds:
        raise RuntimeError("校准没有读到可处理的叙事回合")
    gold = (
        load_gold_fixture(Path(args.gold).resolve(), chat_jsonl) if args.gold else None
    )
    base_state = initial_unified_state()
    prompt = build_event_prompt(base_state, rounds, 1)
    run: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "calibrating_thinking",
        "chat_jsonl": str(chat_jsonl),
        "endpoint": args.endpoint,
        "model": args.model,
        "task_set": "event",
        "batch_size": args.batch_size,
        "batch_count": 1,
        "concurrency": 1,
        "schedule": "serial_controlled_calibration",
        "response_format": args.response_format,
        "max_tokens": {"event": args.event_max_tokens},
        "timeout_seconds": args.timeout,
        "stream_idle_timeout_seconds": args.stream_idle_timeout,
        "candidate_attempt_limit": args.candidate_attempt_limit,
        "transport_attempt_limit": args.transport_attempt_limit,
        "calibration_modes": list(args.calibration_modes),
        "calibration_note": (
            f"{len(args.calibration_modes)} 次使用完全相同的首批正文和提示词，按顺序单独发送；"
            "只改变 thinking 参数。DeepSeek 当前文档中 V4 Flash 的 low、high、max 分别按"
            " low、high、max 执行；兼容端点是否完整转发仍以实际 reasoning_content 为准。"
        ),
        "calibration": [],
        "batches": [],
        "gold_evaluation": {},
        "type_counts": {},
        "final_state": {},
        "final_network": [],
        "final_network_validation": {},
        "preflight_record": preflight_record,
    }
    persist_single_record(run, output, api_key)
    for mode in args.calibration_modes:
        print(f"思考强度校准：{mode} 开始", flush=True)
        result = run_model_task(
            task=f"event_{mode}",
            endpoint=args.endpoint,
            api_key=api_key,
            model=args.model,
            system_prompt=EVENT_SYSTEM_PROMPT,
            user_prompt=prompt,
            timeout=args.timeout,
            max_tokens=args.event_max_tokens,
            thinking_mode=mode,
            response_format=args.response_format,
            stream_idle_timeout=args.stream_idle_timeout,
            candidate_attempt_limit=1,
            transport_attempt_limit=args.transport_attempt_limit,
            normalizer=lambda plan, s=base_state, r=rounds: normalize_event_plan(
                plan, s, r
            ),
            validator=lambda plan, s=base_state, r=rounds: validate_event_plan(
                plan, s, r
            ),
        )
        item: dict[str, Any] = {"mode": mode, "task": result}
        if result.get("ok"):
            candidate_state, operations, _ = apply_event_candidate(
                base_state, result["plan"]
            )
            item["operations"] = operations
            item["state"] = public_state(candidate_state)
            if gold is not None:
                item["gold_evaluation"] = evaluate_state_against_gold(
                    candidate_state, gold, rounds[-1]["round"]
                )
        run["calibration"].append(item)
        persist_single_record(run, output, api_key)
        print(
            f"思考强度校准：{mode} {'通过结构检查' if result.get('ok') else '未通过'}",
            flush=True,
        )
    run["status"] = "calibration_completed"
    run["completed_at"] = datetime.now(timezone.utc).isoformat()
    persist_single_record(run, output, api_key)
    return run


def run_probe(
    args: argparse.Namespace,
    *,
    initial_state_override: dict[str, Any] | None = None,
    start_round: int = 0,
    preflight_record_override: str | None = None,
) -> dict[str, Any]:
    """执行指定范围复测；多项任务读取同一快照，返回后原子提交。"""

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise RuntimeError(f"环境变量 {args.api_key_env} 中没有 API 密钥")
    chat_jsonl = Path(args.chat_jsonl).resolve()
    output = Path(args.output).resolve()
    preflight_record = (
        flatten_record_history(preflight_record_override)
        if preflight_record_override is not None
        else (
            flatten_record_history(output.read_text(encoding="utf-8"))
            if args.preserve_existing and output.exists()
            else ""
        )
    )
    gold = (
        load_gold_fixture(Path(args.gold).resolve(), chat_jsonl) if args.gold else None
    )
    state = (
        deepcopy(initial_state_override)
        if initial_state_override is not None
        else initial_unified_state()
    )
    run: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "chat_jsonl": str(chat_jsonl),
        "endpoint": args.endpoint,
        "model": args.model,
        "task_set": args.task_set,
        "batch_size": args.batch_size,
        "batch_count": args.batches,
        "concurrency": args.concurrency,
        "schedule": args.schedule,
        "stagger_seconds": args.stagger_seconds,
        "thinking_modes": {
            name: {
                "event": args.event_thinking,
                "memory": args.memory_thinking,
                "entity": args.entity_thinking,
            }[name]
            for name in task_names_for_set(args.task_set)
        },
        "response_format": args.response_format,
        "max_tokens": {
            name: {
                "event": args.event_max_tokens,
                "memory": args.memory_max_tokens,
                "entity": args.entity_max_tokens,
            }[name]
            for name in task_names_for_set(args.task_set)
        },
        "timeout_seconds": args.timeout,
        "stream_idle_timeout_seconds": args.stream_idle_timeout,
        "candidate_attempt_limit": args.candidate_attempt_limit,
        "transport_attempt_limit": args.transport_attempt_limit,
        "batches": [],
        "gold_evaluation": {},
        "type_counts": {},
        "final_state": {},
        "final_network": [],
        "final_network_validation": {},
        "preflight_record": preflight_record,
    }
    persist_single_record(run, output, api_key)
    rounds_iterator = iter(
        round_item
        for round_item in iter_rounds(chat_jsonl)
        if round_item["round"] > start_round
    )
    processed_round_end = start_round

    first_batch_number = start_round // args.batch_size + 1
    for batch_number in range(first_batch_number, args.batches + 1):
        rounds = take_batch(rounds_iterator, args.batch_size)
        if not rounds:
            break
        batch_started = time.perf_counter()
        snapshot = deepcopy(state)
        batch: dict[str, Any] = {
            "batch_number": batch_number,
            "round_start": rounds[0]["round"],
            "round_end": rounds[-1]["round"],
            "status": "tasks_running",
            "tasks": {},
        }
        run["batches"].append(batch)
        persist_single_record(run, output, api_key)
        task_specs = build_task_specs(args, snapshot, rounds, batch_number)
        scope_label = "Event 单路" if args.task_set == "event" else "三项"
        print(
            f"第 {batch_number} 批（轮次 {rounds[0]['round']}—{rounds[-1]['round']}）开始{scope_label}提取",
            flush=True,
        )

        def execute_task(name: str, delay: float = 0.0) -> dict[str, Any]:
            if delay > 0:
                time.sleep(delay)
            return run_model_task(
                task=name,
                endpoint=args.endpoint,
                api_key=api_key,
                model=args.model,
                timeout=args.timeout,
                stream_idle_timeout=args.stream_idle_timeout,
                retry_offset_seconds=delay / 2,
                candidate_attempt_limit=args.candidate_attempt_limit,
                transport_attempt_limit=args.transport_attempt_limit,
                **task_specs[name],
            )

        def save_task_result(name: str, result: dict[str, Any]) -> None:
            batch["tasks"][name] = result
            print(
                f"  {name} 已返回：{'通过' if result.get('ok') else '失败'}",
                flush=True,
            )
            persist_single_record(run, output, api_key)

        task_names = list(task_specs)
        if args.schedule == "serial":
            for name in task_names:
                try:
                    save_task_result(name, execute_task(name))
                except Exception as exc:
                    save_task_result(
                        name,
                        {
                            "task": name,
                            "ok": False,
                            "fatal_error": f"未处理异常：{exc}",
                            "system_prompt": task_specs[name]["system_prompt"],
                            "attempts": [],
                            "elapsed_seconds": round(
                                time.perf_counter() - batch_started, 3
                            ),
                        },
                    )
        else:
            with ThreadPoolExecutor(
                max_workers=max(1, min(len(task_specs), args.concurrency))
            ) as pool:
                future_names = {
                    pool.submit(
                        execute_task,
                        name,
                        index * args.stagger_seconds
                        if args.schedule == "staggered"
                        else 0.0,
                    ): name
                    for index, name in enumerate(task_names)
                }
                for future in as_completed(future_names):
                    name = future_names[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # 保留同批其他已成功结果和完整错误。
                        result = {
                            "task": name,
                            "ok": False,
                            "fatal_error": f"未处理异常：{exc}",
                            "system_prompt": task_specs[name]["system_prompt"],
                            "attempts": [],
                            "elapsed_seconds": round(
                                time.perf_counter() - batch_started, 3
                            ),
                        }
                    save_task_result(name, result)

        if not all(batch["tasks"].get(name, {}).get("ok") for name in task_specs):
            batch["status"] = "failed_before_commit"
            batch["elapsed_seconds"] = round(time.perf_counter() - batch_started, 3)
            run["status"] = "failed"
            persist_single_record(run, output, api_key)
            return run

        event_plan = batch["tasks"]["event"]["plan"]
        candidate_state, operations, _ = apply_event_candidate(snapshot, event_plan)
        binding_warnings: list[str] = []
        stub_warnings: list[str] = []
        if args.task_set == "full":
            memory_plan = batch["tasks"]["memory"]["plan"]
            entity_plan = batch["tasks"]["entity"]["plan"]
            candidate_state, binding_warnings = apply_memory_candidates(
                candidate_state, memory_plan, rounds
            )
            candidate_state = apply_entity_candidates(candidate_state, entity_plan)
            stub_warnings = [
                *reconcile_entity_event_links(candidate_state, rounds),
                *reconcile_source_inventory(candidate_state, rounds),
                *ensure_minimal_entity_nodes(candidate_state),
            ]
            network, network_report = materialize_network(candidate_state)
        else:
            network, network_report = [], skipped_network_report()
        batch["operations"] = operations
        batch["binding_warnings"] = binding_warnings
        batch["stub_warnings"] = stub_warnings
        batch["network_validation"] = network_report
        batch["state_after_batch"] = public_state(candidate_state)
        batch["elapsed_seconds"] = round(time.perf_counter() - batch_started, 3)
        if not network_report.get("valid"):
            batch["status"] = "network_validation_failed"
            run["status"] = "failed"
            run["final_state"] = public_state(candidate_state)
            run["final_network"] = network
            run["final_network_validation"] = network_report
            persist_single_record(run, output, api_key)
            return run

        state = candidate_state
        processed_round_end = rounds[-1]["round"]
        batch["status"] = "committed"
        commit_label = (
            "Event 状态"
            if args.task_set == "event"
            else f"正式网络 {network_report['entity_count']} 项"
        )
        print(
            f"  第 {batch_number} 批已提交并写回唯一记录；{commit_label}",
            flush=True,
        )
        persist_single_record(run, output, api_key)

    if args.task_set == "full":
        final_network, final_report = materialize_network(state)
    else:
        final_network, final_report = [], skipped_network_report()
    counts: dict[str, int] = {}
    for entity in final_network:
        counts[entity["type"]] = counts.get(entity["type"], 0) + 1
    run["status"] = "completed" if final_report.get("valid") else "failed"
    run["completed_at"] = datetime.now(timezone.utc).isoformat()
    run["final_state"] = public_state(state)
    run["final_network"] = final_network
    run["final_network_validation"] = final_report
    run["type_counts"] = counts
    if gold is not None:
        run["gold_evaluation"] = evaluate_state_against_gold(
            state, gold, processed_round_end
        )
    persist_single_record(run, output, api_key)
    return run


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="提取 Event，或同批提取 Event、Memory 与全部已实现 Entity，并只保存一份记录。"
    )
    parser.add_argument("chat_jsonl")
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="OPENCODE_API_KEY")
    parser.add_argument("--output", required=True)
    parser.add_argument("--gold")
    parser.add_argument(
        "--task-set",
        choices=("full", "event"),
        default="full",
        help="full 运行三路；event 只做 Event 边界与内容短回归。",
    )
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--batches", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument(
        "--schedule",
        choices=("concurrent", "staggered", "serial"),
        default="staggered",
        help="三项任务立即并发、短间隔错峰并发，或完全串行。",
    )
    parser.add_argument("--stagger-seconds", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--stream-idle-timeout",
        type=float,
        default=90.0,
        help="流式连接连续无有效传输时的停止秒数；总时长仍由 --timeout 限制。",
    )
    parser.add_argument("--event-max-tokens", type=int, default=32768)
    parser.add_argument("--memory-max-tokens", type=int, default=16384)
    parser.add_argument("--entity-max-tokens", type=int, default=32768)
    parser.add_argument(
        "--candidate-attempt-limit",
        type=int,
        default=1,
        help="每项语义候选最多生成次数；正式前台默认一次，失败任务由检查点恢复。",
    )
    parser.add_argument(
        "--transport-attempt-limit",
        type=int,
        default=1,
        help="每项前台传输尝试次数；默认一次，失败候选由后续恢复处理。",
    )
    thinking_choices = ("default", "off", "low", "high", "max")
    parser.add_argument(
        "--event-thinking", choices=thinking_choices, default="off"
    )
    parser.add_argument(
        "--memory-thinking", choices=thinking_choices, default="off"
    )
    parser.add_argument(
        "--entity-thinking", choices=thinking_choices, default="off"
    )
    parser.add_argument(
        "--response-format",
        choices=("text", "json_object"),
        default="text",
        help="Zen 当前实测应使用 text；仍由提示词和解析器严格要求 JSON。",
    )
    parser.add_argument(
        "--thinking-calibration",
        action="store_true",
        help="只对同一首批 Event 顺序比较关闭、低与最大思考，不运行完整三路流程。",
    )
    parser.add_argument(
        "--calibration-modes",
        nargs="+",
        choices=thinking_choices,
        default=["off", "low", "max"],
    )
    parser.add_argument(
        "--preserve-existing",
        action="store_true",
        help="把输出路径中已有校准或未提交记录附入同一最终文件。",
    )
    parser.add_argument(
        "--recover-final-batch",
        action="store_true",
        help="从同一记录的最后提交检查点恢复，逐项复验并只补取真正失败的任务。",
    )
    parser.add_argument(
        "--finalize-recovered-offline",
        action="store_true",
        help="禁止外部调用，只用记录中齐全且复验通过的三项候选完成最后提交。",
    )
    parser.add_argument(
        "--offline-rebuild-round-end",
        type=int,
        help="离线封存时从目标轮次之前的最新检查点重建该批；不会调用模型。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if min(args.event_max_tokens, args.memory_max_tokens, args.entity_max_tokens) <= 0:
        print("输出安全上限必须大于零。", file=sys.stderr, flush=True)
        return 1
    if args.timeout <= 0 or args.stream_idle_timeout < 0:
        print("总超时必须大于零，流式无进展超时不能小于零。", file=sys.stderr, flush=True)
        return 1
    try:
        if args.finalize_recovered_offline:
            run = finalize_recovered_offline(args)
        elif args.recover_final_batch:
            run = recover_final_batch(args)
        elif args.thinking_calibration:
            run = run_thinking_calibration(args)
        else:
            run = run_probe(args)
    except Exception as exc:
        print(f"复测启动或记录失败：{exc}", file=sys.stderr, flush=True)
        return 1
    if run.get("status") not in {"completed", "calibration_completed"}:
        print("复测未完成，请查看唯一记录中的失败批次。", file=sys.stderr, flush=True)
        return 1
    if run.get("status") == "calibration_completed":
        print("思考强度受控校准完成。", flush=True)
        return 0
    gold = run.get("gold_evaluation", {})
    if args.task_set == "event":
        print(
            "Event 单路短回归完成：人工边界对照="
            + str(gold.get("status", "未提供")),
            flush=True,
        )
        return 0
    print(
        "复测完成：实体网络通过；人工边界对照="
        + str(gold.get("status", "未提供")),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
