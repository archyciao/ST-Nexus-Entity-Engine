"""Event 提取 V2 的提示词与确定性分段辅助。

V2 第一次按完整局部故事直接返回短窗口内的 Event 起点和各 Event 的实体名录；
固定脚本由起点位置推导旧 Event 的衔接与三状态操作，并切出无遗漏、无重叠的
本批正文。第二层再并发生成 Event 内容、实体自身信息、关系与引用。
这里不调用模型，也不使用地点、动作等语义关键词猜边界。
"""

from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any, Iterable


NARRATIVE_MAP_BOUNDARY_SOURCE = "ai_narrative_map_v2"
ALLOWED_DISPOSITIONS = {"absent", "keep_distinct", "merge_into_pending"}
BOUNDARY_DECISION_VALUES = {"keep", "remove"}
PENDING_TO_FORMING_BOUNDARY_ID = "previous_pending_to_forming"
PREVIOUS_TO_BATCH_BOUNDARY_ID = "previous_tail_to_current_batch"
BOUNDARY_TEXT_WINDOW_CHARS = 700
QUOTE_FRAGMENT_MIN_CHARS = 10
CONTEXTUAL_CHARACTER_NAMES = {"你", "我", "主角", "玩家", "玩家角色"}
ROSTER_TYPES = {
    "character",
    "location",
    "item",
    "organization",
    "skill",
    "concept",
}
ENTITY_RECORD_ACTIONS = {"create", "update", "unresolved"}
FIELD_RELATIONSHIPS = {"supplement", "revise"}

# 这些名称是开放语义资料的正式落点。模型可以自由组织每个 Component 内的
# 内容，但不能把 ID、位置、关系端点或反向索引伪装成开放字段。
OPEN_SEMANTIC_COMPONENTS: dict[str, tuple[str, ...]] = {
    "character": (
        "character_profile",
        "character_behavior_profile",
        "character_state",
        "character_objective",
    ),
    "location": (
        "location_profile",
        "environment_profile",
        "location_atmosphere",
        "location_state",
    ),
    "item": (
        "item_profile",
        "item_characteristic",
    ),
    "organization": (
        "organization_profile",
        "organization_structure",
        "organization_culture",
        "organization_strategy",
        "organization_objective",
        "organization_state",
    ),
    "skill": (
        "skill_definition",
        "skill_characteristic",
    ),
    "concept": (
        "concept_definition",
        "applicability",
    ),
}

SEMANTIC_COMPONENT_ALIASES = {
    "profile": {
        "character": "character_profile",
        "location": "location_profile",
        "item": "item_profile",
        "organization": "organization_profile",
    },
    "behavior_profile": {"character": "character_behavior_profile"},
    "behaviour_profile": {"character": "character_behavior_profile"},
    "personality": {"character": "character_behavior_profile"},
    "state": {
        "character": "character_state",
        "location": "location_state",
        "organization": "organization_state",
    },
    "objective": {
        "character": "character_objective",
        "organization": "organization_objective",
    },
    "environment": {"location": "environment_profile"},
    "atmosphere": {"location": "location_atmosphere"},
    "characteristic": {
        "item": "item_characteristic",
        "skill": "skill_characteristic",
    },
    "culture": {"organization": "organization_culture"},
    "strategy": {"organization": "organization_strategy"},
    "structure": {"organization": "organization_structure"},
    "definition": {
        "skill": "skill_definition",
        "concept": "concept_definition",
    },
}
TYPE_ALIASES = {
    "character": "character",
    "person": "character",
    "location": "location",
    "place": "location",
    "item": "item",
    "object": "item",
    "organization": "organization",
    "organisation": "organization",
    "faction": "organization",
}


NARRATIVE_MAP_SYSTEM_PROMPT = """
【任务】
把按顺序提供的相邻前文和本批 AIRP 故事整理成 Event，并在每项 Event 内列出本批实体名录。
直接按故事发展顺序逐项输出；此步不写正式摘要或 Description，不补设定，也不输出分析过程。

【Event 是什么】
一个 Event 是故事中发生的一件事。它有眼前的来由，人物围绕这件事展开行动或交流，并产生结果、
变化，或推进到当前尚未结束的位置。一次持续交谈、一场战斗、一段赶路或一次安顿都可以是一项 Event。

【怎样判断首尾】
当前局部故事已经完成、停止或被打断，而且后文已经实际展开新的行动、互动或问题时，从新故事的
触发或开始处另起 Event。仅仅提到以后要做什么，还不表示新故事开始。

时间、地点、主要人物、眼前目标、因果方向或重要物品状态发生变化时，需要复查是否出现新故事。
这些变化是线索，不是单独的切分命令：变化后仍在完成同一件事，就继续当前 Event；新的事情已经
实际展开时，再另起 Event。

【粒度校准】
以下通常放在同一 Event：
- 围绕同一问题的发问、解释、追问、争论和最后决定；
- 同一场战斗中的多次攻防、受伤与反击，直到战斗结束或本批暂止。

以下通常另起 Event：
- 一项交涉已有决定，人物随后实际开始处理另一项请求；
- 一段旅程已经抵达并安顿，人物随后开始调查当地发生的异常。

【上一批如何衔接】
story_context 中的相邻前文按故事顺序放在本批正文之前，只用于判断 Event 是否延续。把前文和本批正文
当成一段连续故事划分，直接返回这个短窗口内各项 Event 的起点。不要判断“生成中”“待定稿”等
系统状态，脚本会根据起点所在位置处理。

【Event 内实体名录】
每项名录记录本批正文在该 Event 中形成清楚事实的 character、location、item、organization、skill、
concept；门派、阵营使用 organization，可学习或施展的命名能力使用 skill，需要跨场景定义或适用判断的
规则、制度、理论和术语使用 concept。这里不判断对象是否实际参与，也不把提及、计划或回忆分成不同
类别。同一对象涉及多项 Event 时分别列出，并各用该 Event 内的本批原文作证。优先沿用既有实体的实际
名称；“你、我、主角”等不是人物姓名。没有实际姓名时使用稳定、可区分的未具名身份，不虚构姓名。

【输出字段】
只输出一个合法 JSON 对象，不输出解释或代码围栏。顶层只有 events；events 是非空数组，按前文与
本批正文组成的故事顺序列出短窗口内的 Event。第一项从 story_context 的最早前文第一句开始；没有
前文时从本批正文第一句开始。前文只用来标出衔接，entity_roster 只列有本批正文证据的对象。

events 每项只有：
- start_quote：从该 Event 在短窗口中的起点逐字复制一小段；
- entity_roster：数组，可为空。

entity_roster 每项只有：
- type：character、location、item、organization、skill、concept 六者之一；
- primary_name：主要名称；
- aliases：本批有原文依据且确认同一对象的别称；没有则用空数组；
- evidence_quote：该对象符合对应 Type 条件的逐字原文短引。
""".strip()


EVENT_CONTENT_SYSTEM_PROMPT = """
你为已经切好的 Event 原文写内容。每个 fixed_segment 已由上一阶段和脚本确定为一项 Event；
不要移动边界，不要合并、拆分或增加分段。

Summary 是一项 Event 的客观故事梗概。它用概括性的事件记录语言，让以后单独读到这项 Event 时，
能够迅速理解发生了什么；它不像原文那样描写现场，也不是关键细节的集合。Summary 包含主要人物、
事情的来由、主要行动与回应、关键因果、重要物品或命令，以及结果或当前未决事项。

Summary 通常写约 300 个汉字，原则上不超过 500 个汉字。内容很少时可以更短；事实较多时优先写全，
不要为了字数删除事实、拆分 Event 或要求重试。

例：原文描写人物反复检查门锁、倾听脚步并低声催促同伴时，Summary 可写成“甲察觉有人接近，确认
退路后催促乙立即离开”；动作姿态、声音和现场气氛可留给 key details，不逐项写进 Summary。

每段返回：
- story_summary_add：只写该段本批新增的客观故事梗概；
- description：供快速定位和向量检索的一两句短说明，写人物、情境、主要行动和结果或当前进展；
- new_key_details：零至五条最有回忆画面感的关键言语或动作。对白可逐字引用；也可忠实转述，
  转述不加引号。能够确认说话者或动作主体时填写 actor_key，不能确认则留空。不说明选择过程，
  不为数量凑条目。

每个 fixed_segment 内的 entity_roster 是上一阶段为该 Event 列出的候选，只帮助理解和写全内容，
不能用来改边界。时间、地点引用、来源书签、正式编号和状态由脚本或 Entity 任务处理。若
fixed_segment 延续既有 Event，可参考 existing_event_tail 更新 description；
story_summary_add 仍只写本段新增内容。

只输出 JSON，不解释。每个 partition_key 恰好返回一次：
{
  "event_updates": [{
    "partition_key": "原样复制 fixed_segment.partition_key",
    "story_summary_add": "该固定分段的完整新增故事摘要",
    "description": "短检索说明",
    "new_key_details": [{
      "kind": "statement | action",
      "content": "关键原话或忠实转述",
      "actor_key": "character:主要名称；不适用则空字符串"
    }]
  }]
}
""".strip()


ENTITY_CREATE_SYSTEM_PROMPT = """
Event 边界和新增 Entity 目录已经锁定。本任务只为目录中 record_action=create 的对象建立第一版资料；
不要重判 Event，不把对象改成 update，也不填写位置、父级、物品放置、Relation、正式 ID、Index、
Event 或 Memory。不要补建目录以外的对象，也不能凭背景设定虚构对象。

每个新对象填写实际主要名称、别名、稳定的一句 Description、原文依据和 semantic_fields。
semantic_fields 的键使用该 Type 的开放语义 Component 名称；每个值是开放 JSON 对象，可以按事实自然
填写文字、数字、布尔值、数组或嵌套对象，不要求补齐模板：
- character：character_profile、character_behavior_profile、character_state、character_objective
- location：location_profile、environment_profile、location_atmosphere、location_state
- item：item_profile、item_characteristic
- organization：organization_profile、organization_structure、organization_culture、organization_strategy、organization_objective、organization_state
- skill：skill_definition、skill_characteristic
- concept：concept_definition、applicability

只输出 JSON，不解释：
{"entities":[{"entity_key":"type:主要名称","type":"character | location | item | organization | skill | concept","primary_name":"实际名称","aliases_add":[],"description":"稳定短说明","evidence":[{"source_ref":"本批来源书签","quote":"逐字原文短引"}],"semantic_fields":{"正式 Component 名称":{}}}]}
""".strip()


ENTITY_UPDATE_SYSTEM_PROMPT = """
Event 边界和既有 Entity 目录已经锁定。本任务只更新 record_action=update 的对象。输入中的 existing_entity
是当前资料；没有新事实时不返回该对象。不要重写整个 Entity，不填写位置、父级、物品放置、Relation、
正式 ID、Index、Event 或 Memory。

每次只返回发生变化的开放语义字段。模型只判断新证据与该字段旧内容的关系：
- supplement：旧内容仍然成立，新内容只是补充；value 只写新增或扩展部分。
- revise：新证据使旧字段不再完整或不再准确；value 写该字段修订后的完整当前内容。

判断依据是事实含义，不是字数多少。字段原本不存在时直接返回 supplement，脚本会按新增字段处理。
遗漏字段不表示删除；拿不准是否改变时不覆盖旧值。semantic_fields 的正式名称与开放内容范围和新增任务
相同。Description 只有核心辨识信息确实改变时才返回 description_update 完整新稿。

只输出 JSON，不解释：
{"entities":[{"entity_key":"沿用输入键","aliases_add":[],"description_update":"可省略","field_updates":[{"field_name":"正式 Component 名称","relationship_to_old":"supplement | revise","value":{},"evidence":[{"source_ref":"本批来源书签","quote":"逐字原文短引"}]}]}]}
""".strip()


RELATION_REFERENCE_SYSTEM_PROMPT = """
Event 边界和实体名录已经锁定。本任务只提取必须使用稳定引用表达的结构事实和 Character Relation；
不写 Entity 自身资料，不重判 Event，也不判断人物、物品或组织是否实际参与。

Event 的实际发生地点单独填写 event_locations。被提到、计划前往或只在回忆中出现的地点不填；无法
确认实际地点时可以为空。其他 Entity 与 Event 的普通关联由脚本根据实体证据建立，不在这里重复判断。

reference_updates 只填写正文明确支持的当前地点、直接父级、物品当前放置和长期相关 Concept。目标必须
能在既有对象或本批名录中定位；不确定时省略。Character Relation 连接两个 Character：双方共有的关系
事实集中写入 description，单方向的当前态度或做法写入 directional_views；不要求再分大量类别。既有关系
只在事实变化时更新。不要生成正式 ID、反向索引、Event 内容或 Memory。

只输出 JSON，不解释：
{
  "reference_updates": [{
    "entity_key": "type:主要名称",
    "source_ref": "本批来源书签",
    "evidence_quote": "支持本次引用的逐字原文短引",
    "current_location_key": "仅 Character：location:当前实际地点",
    "parent_key": "仅 Location/Organization：同 Type 的直接父级",
    "placement": {"target_key": "仅 Item：character/item/location:目标", "role": "carried | equipped | worn | contained | placed | stored", "detail": "可选"},
    "related_concept_keys": ["concept:名称"]
  }],
  "event_locations": [{
    "partition_key": "原样复制 fixed_event_segment.partition_key",
    "location_key": "location:实际发生地点",
    "source_ref": "本批来源书签",
    "evidence_quote": "证明故事实际发生在这里的逐字短引"
  }],
  "relations": [{
    "participant_keys": ["character:甲", "character:乙"],
    "description": "新关系的完整简述或既有关系的更新稿",
    "evidence": [{"source_ref": "本批来源书签", "quote": "关系依据的逐字原文短引"}],
    "directional_views": [{
      "from_key": "character:甲",
      "toward_key": "character:乙",
      "description": "一方当前如何看待或对待另一方"
    }]
  }]
}

每项只返回有依据的字段，不输出整套空结构。人物 Inventory、地点包含目录、容器内容和 Event—Entity
普通关联均由脚本重建。current_location_key 只能填写人物在本批结尾实际身处的 Location；仅被提到、
计划前往或尚未抵达的地点不能作为当前地点。
""".strip()


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique(values: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _anchor_characters(text: str) -> list[tuple[str, int]]:
    return [
        (character, index)
        for index, character in enumerate(str(text))
        if not character.isspace()
        and character not in "*_`~"
        and not unicodedata.category(character).startswith("P")
    ]


def normalize_anchor(text: Any) -> str:
    return "".join(character for character, _ in _anchor_characters(str(text)))


def _anchor_spans(text: str, quote: str) -> list[tuple[int, int]]:
    kept = _anchor_characters(text)
    normalized_text = "".join(character for character, _ in kept)
    normalized_quote = normalize_anchor(quote)
    if not normalized_quote:
        return []
    spans: list[tuple[int, int]] = []
    cursor = 0
    while True:
        start = normalized_text.find(normalized_quote, cursor)
        if start < 0:
            break
        end = start + len(normalized_quote) - 1
        spans.append((kept[start][1], kept[end][1] + 1))
        cursor = start + 1
    return spans


def _narrative_sources(source_messages: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for message in source_messages:
        if not isinstance(message, dict):
            continue
        ref = str(message.get("ref", "")).strip()
        narrative = str(
            message.get("narrative", message.get("content", ""))
        ).strip()
        if ref and narrative:
            result.append({"ref": ref, "narrative": narrative})
    return result


def _locate_quote(
    sources: list[dict[str, str]],
    quote: str,
    *,
    after: tuple[int, int] | None = None,
) -> dict[str, Any] | None:
    after_source, after_offset = after if after is not None else (0, -1)
    for source_index, source in enumerate(sources):
        if source_index < after_source:
            continue
        for start, end in _anchor_spans(source["narrative"], quote):
            if source_index == after_source and start <= after_offset:
                continue
            return {
                "source_index": source_index,
                "source_ref": source["ref"],
                "offset": start,
                "start_quote": source["narrative"][start:end].strip(),
            }
    return None


def _first_anchor_quote(text: str, limit: int = 48) -> str:
    compact = str(text).lstrip()
    if len(compact) <= limit:
        return compact
    sentence = re.search(r"^.{8,%d}?[。！？!?；;\n]" % limit, compact, re.S)
    if sentence:
        return sentence.group(0).strip()
    return compact[:limit].strip()


def _state_flags(state: dict[str, Any]) -> tuple[bool, bool, bool]:
    events = [item for item in _as_list(state.get("events")) if isinstance(item, dict)]
    return (
        bool(events),
        any(item.get("status") == "forming" for item in events),
        any(item.get("status") == "pending_finalization" for item in events),
    )


def canonicalize_contextual_character_references(
    value: Any, state: dict[str, Any]
) -> Any:
    """把“你／我／主角”等上下文称呼确定性地改写到当前角色。"""

    identity = state.get("_runtime_player_identity")
    if not isinstance(identity, dict):
        return deepcopy(value)
    name = str(identity.get("primary_name", "")).strip()
    key = str(identity.get("entity_key", "")).strip()
    if not name or not key.startswith("character:"):
        return deepcopy(value)
    key_mapping = {
        f"character:{mention}": key for mention in CONTEXTUAL_CHARACTER_NAMES
    }

    def rewrite(item: Any) -> Any:
        if isinstance(item, str):
            return key_mapping.get(item, item)
        if isinstance(item, list):
            return [rewrite(child) for child in item]
        if not isinstance(item, dict):
            return deepcopy(item)
        result = {field: rewrite(child) for field, child in item.items()}
        if str(result.get("type", "")) == "character":
            primary = str(result.get("primary_name", "")).strip()
            entity_key = str(result.get("entity_key", "")).strip()
            if primary in CONTEXTUAL_CHARACTER_NAMES or entity_key == key:
                result["primary_name"] = name
            for aliases_field in ("aliases", "aliases_add"):
                aliases = result.get(aliases_field)
                if isinstance(aliases, list):
                    result[aliases_field] = [
                        alias
                        for alias in aliases
                        if str(alias).strip() not in CONTEXTUAL_CHARACTER_NAMES
                        and str(alias).strip() != name
                    ]
        return result

    return rewrite(value)


def _boundary_preview(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict):
        return {}
    preview: dict[str, Any] = {}
    for key in (
        "description",
        "story_summary",
        "related_entity_keys",
        "locations",
        "unresolved",
    ):
        value = deepcopy(event.get(key))
        if value not in (None, "", []):
            preview[key] = value
    return preview


def _source_text_for_event(
    event: Any, source_text_by_ref: dict[str, str]
) -> str:
    if not isinstance(event, dict):
        return ""
    return "\n".join(
        str(source_text_by_ref.get(str(ref), "")).strip()
        for ref in _as_list(event.get("source_refs"))
        if str(source_text_by_ref.get(str(ref), "")).strip()
    )


def _text_head(text: Any, limit: int = BOUNDARY_TEXT_WINDOW_CHARS) -> str:
    return str(text).strip()[:limit]


def _text_tail(text: Any, limit: int = BOUNDARY_TEXT_WINDOW_CHARS) -> str:
    return str(text).strip()[-limit:]


def _locate_unique_quote_fragment(
    sources: list[dict[str, str]],
    quote: str,
    *,
    after: tuple[int, int] | None,
) -> dict[str, Any] | None:
    """完整短引失败时，只用其中唯一、足够长的逐字片段恢复位置。"""

    fragments = sorted(
        {
            fragment.strip()
            for fragment in re.split(r"[。！？!?；;\n]+", str(quote))
            if len(normalize_anchor(fragment)) >= QUOTE_FRAGMENT_MIN_CHARS
        },
        key=lambda fragment: len(normalize_anchor(fragment)),
        reverse=True,
    )
    after_source, after_offset = after if after is not None else (0, -1)
    for fragment in fragments:
        matches: list[dict[str, Any]] = []
        for source_index, source in enumerate(sources):
            if source_index < after_source:
                continue
            for start, end in _anchor_spans(source["narrative"], fragment):
                if source_index == after_source and start <= after_offset:
                    continue
                matches.append(
                    {
                        "source_index": source_index,
                        "source_ref": source["ref"],
                        "offset": start,
                        "start_quote": source["narrative"][start:end].strip(),
                        "recovered_fragment": fragment,
                    }
                )
        if len(matches) == 1:
            return matches[0]
    return None


def build_continuity_context(
    *,
    existing_event_tail: dict[str, Any],
    source_text_by_ref: dict[str, str],
    current_story: str,
) -> dict[str, Any]:
    """按故事顺序提供上一待定稿尾部和完整生成中窗口。

    模型不再选择抽象衔接状态，只在这段前文与本批正文组成的短窗口中标出
    实际 Event 起点。旧 Event 内部不能重切，脚本只接受旧区块起点。
    """

    pending = existing_event_tail.get("pending_event")
    forming = existing_event_tail.get("forming_event")
    if not isinstance(forming, dict):
        return {}

    parts: list[dict[str, Any]] = []
    if isinstance(pending, dict):
        pending_text = _source_text_for_event(pending, source_text_by_ref)
        parts.append(
            {
                "part_key": "previous_pending_tail",
                "event_preview": _boundary_preview(pending),
                "text": _text_tail(pending_text),
            }
        )
    forming_text = _source_text_for_event(forming, source_text_by_ref)
    parts.append(
        {
            "part_key": "previous_forming",
            "event_preview": _boundary_preview(forming),
            "text": (
                forming_text
                if len(forming_text) <= BOUNDARY_TEXT_WINDOW_CHARS * 2
                else _text_head(forming_text)
                + "\n[中段省略；旧 Event 内部不在本批重切]\n"
                + _text_tail(forming_text)
            ),
        }
    )
    return {"ordered_parts": parts}


def _continuity_sources(context: dict[str, Any] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in _as_list((context or {}).get("ordered_parts")):
        if not isinstance(item, dict):
            continue
        part_key = str(item.get("part_key", "")).strip()
        text = str(item.get("text", "")).strip()
        if part_key and text:
            result.append({"ref": f"__context__:{part_key}", "narrative": text})
    return result


def _name_signature(value: Any) -> str:
    return re.sub(r"[\s·・,，。．、:：;；]+", "", str(value)).casefold()


def _field_signature(value: Any) -> str:
    """折叠字段名的大小写和常见分隔符，供高置信纠错使用。"""

    return re.sub(r"[\s_\-./\\:：]+", "", str(value)).casefold()


def canonical_semantic_component_name(
    entity_type: str, proposed_name: Any
) -> tuple[str | None, str]:
    """把模型字段名映射到该 Type 的开放 Component。

    返回 ``(正式名称, 原因)``。只有显式别名、折叠后精确命中或明显唯一的
    拼写误差才自动修正；其余内容交给维护收件箱，避免猜错字段后污染召回。
    """

    proposed = str(proposed_name).strip()
    allowed = OPEN_SEMANTIC_COMPONENTS.get(entity_type, ())
    if not proposed or not allowed:
        return None, "empty_or_unsupported_type"
    if proposed in allowed:
        return proposed, "exact"

    signature = _field_signature(proposed)
    exact_matches = [name for name in allowed if _field_signature(name) == signature]
    if len(exact_matches) == 1:
        return exact_matches[0], "normalized_exact"

    alias_targets = SEMANTIC_COMPONENT_ALIASES.get(proposed.casefold()) or {}
    alias = alias_targets.get(entity_type)
    if alias in allowed:
        return alias, "registered_alias"

    scores = sorted(
        (
            SequenceMatcher(None, signature, _field_signature(name)).ratio(),
            name,
        )
        for name in allowed
    )
    best_score, best_name = scores[-1]
    second_score = scores[-2][0] if len(scores) > 1 else 0.0
    if best_score >= 0.92 and best_score - second_score >= 0.05:
        return best_name, "unique_typo_repair"
    return None, "unclassified"


def open_semantic_component_catalog() -> dict[str, list[str]]:
    """返回可注入提示词或前端的 Type—开放 Component 目录。"""

    return {
        entity_type: list(component_names)
        for entity_type, component_names in OPEN_SEMANTIC_COMPONENTS.items()
    }


def _canonical_roster_identity(
    *,
    state: dict[str, Any],
    entity_type: str,
    name: str,
    aliases: list[str],
) -> tuple[str, str, list[str], dict[str, Any] | None, str]:
    candidates = state.get("entity_candidates", {})
    direct_key = f"{entity_type}:{name}"
    signatures = {_name_signature(name), *(_name_signature(alias) for alias in aliases)}
    matches: list[tuple[str, dict[str, Any]]] = []
    cross_type_matches: list[str] = []
    for key, candidate in candidates.items():
        if not isinstance(candidate, dict):
            continue
        known = {
            _name_signature(candidate.get("primary_name", "")),
            *(
                _name_signature(alias)
                for alias in _as_list(candidate.get("aliases"))
            ),
        }
        if any(signature and signature in known for signature in signatures):
            if candidate.get("type") == entity_type:
                matches.append((str(key), candidate))
            else:
                cross_type_matches.append(str(key))
    if cross_type_matches:
        return direct_key, name, aliases, None, "unresolved"
    if len(matches) == 0:
        return direct_key, name, aliases, None, "create"
    if len(matches) > 1:
        return direct_key, name, aliases, None, "unresolved"
    key, existing = matches[0]
    canonical_name = str(existing.get("primary_name", name))
    merged_aliases = _unique(
        [*aliases, *(value for value in (name,) if value != canonical_name)]
    )
    return key, canonical_name, merged_aliases, existing, "update"


def normalize_narrative_map(
    plan: dict[str, Any],
    state: dict[str, Any],
    source_messages: Iterable[dict[str, Any]],
    continuity_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """保全正文优先地规范第一阶段回复。

    无法定位的额外边界会被丢弃并留下警告，正文因此只会少切、不会丢失；
    无法核对的名录项也不会自动进入数据库。JSON 本身无法解析仍由调用层报错。
    """

    normalized = canonicalize_contextual_character_references(plan, state)
    sources = _narrative_sources(source_messages)
    context_sources = _continuity_sources(continuity_context)
    window_sources = [*context_sources, *sources]
    current_source_start = len(context_sources)
    warnings: list[str] = []
    has_events, has_forming, has_pending = _state_flags(state)

    if any(
        field in normalized
        for field in (
            "continuity_choice",
            "boundary_decisions",
            "old_forming_disposition",
        )
    ) or any(
        isinstance(item, dict) and "continues_previous" in item
        for item in _as_list(normalized.get("events"))
    ):
        warnings.append("模型返回了旧衔接或状态字段；脚本已忽略，只按 Event 起点位置换算")
    for field in (
        "continuity_choice",
        "boundary_decisions",
        "old_forming_disposition",
    ):
        normalized.pop(field, None)

    raw_events = normalized.get("events")
    if not isinstance(raw_events, list):
        raw_events = []
        warnings.append("events 缺失；脚本将整批正文保守保存在一个 Event 中")

    located_window_events: list[dict[str, Any]] = []
    roster_candidates: list[dict[str, Any]] = []
    cursor: tuple[int, int] | None = None
    for event_index, raw_event in enumerate(raw_events):
        if not isinstance(raw_event, dict):
            warnings.append(f"events[{event_index}] 不是对象，已忽略")
            continue
        quote = str(raw_event.get("start_quote", "")).strip()
        located = _locate_quote(window_sources, quote, after=cursor) if quote else None
        if located is None and quote and event_index > 0:
            located = _locate_unique_quote_fragment(
                window_sources, quote, after=cursor
            )
            if located is not None:
                fragment = str(located.pop("recovered_fragment", ""))
                warnings.append(
                    f"第 {event_index + 1} 个 Event 的完整开头无法定位；"
                    f"脚本已用其中唯一逐字片段“{fragment}”恢复切点"
                )
        if located is None:
            warnings.append(
                f"第 {event_index + 1} 个 Event 开头无法在原文定位；"
                "脚本保守并入相邻 Event，名录仍按证据位置归属"
            )
        else:
            position = (int(located["source_index"]), int(located["offset"]))
            cursor = position
            if int(located["source_index"]) < current_source_start and int(
                located["offset"]
            ) != 0:
                warnings.append(
                    f"第 {event_index + 1} 个 Event 起点落在已处理旧 Event 内部；"
                    "脚本不回切旧内容，已忽略该旧起点"
                )
            elif not any(
                (item["source_index"], item["offset"]) == position
                for item in located_window_events
            ):
                located_window_events.append(located)

        for roster_index, raw_entity in enumerate(
            _as_list(raw_event.get("entity_roster"))
        ):
            if not isinstance(raw_entity, dict):
                warnings.append(
                    f"events[{event_index}].entity_roster[{roster_index}] 不是对象，已忽略"
                )
                continue
            raw_type = str(raw_entity.get("type", "")).strip().casefold()
            entity_type = TYPE_ALIASES.get(raw_type, raw_type)
            name = str(
                raw_entity.get("primary_name", raw_entity.get("name", ""))
            ).strip()
            evidence = str(raw_entity.get("evidence_quote", "")).strip()
            if entity_type not in ROSTER_TYPES or not name:
                warnings.append(
                    f"events[{event_index}].entity_roster[{roster_index}] 的 Type 或主要名称无效，已忽略"
                )
                continue
            evidence_location = _locate_quote(sources, evidence) if evidence else None
            if evidence_location is None:
                warnings.append(
                    f"{entity_type}:{name} 的名录证据无法核对，已交给第二阶段重新发现"
                )
                continue
            aliases = _unique(
                str(alias).strip()
                for alias in _as_list(raw_entity.get("aliases"))
                if str(alias).strip() and str(alias).strip() != name
            )
            key, canonical_name, aliases, existing, record_action = _canonical_roster_identity(
                state=state,
                entity_type=entity_type,
                name=name,
                aliases=aliases,
            )
            if record_action == "unresolved":
                warnings.append(
                    f"{entity_type}:{name} 与多个既有身份或其他 Type 冲突；"
                    "脚本已保留为待确认目录项，不自动新增或更新"
                )
            roster_candidates.append(
                {
                    "entity_key": key,
                    "type": entity_type,
                    "primary_name": canonical_name,
                    "aliases": aliases,
                    "record_action": record_action,
                    **(
                        {
                            "existing_entity": {
                                "entity_key": key,
                                "primary_name": canonical_name,
                                "aliases": deepcopy(_as_list(existing.get("aliases"))),
                                "description": str(existing.get("description", "")),
                                "semantic_fields": deepcopy(
                                    existing.get("semantic_fields", {})
                                    if isinstance(existing.get("semantic_fields"), dict)
                                    else {}
                                ),
                            }
                        }
                        if existing is not None
                        else {}
                    ),
                    "evidence": [
                        {
                            "source_ref": evidence_location["source_ref"],
                            "quote": evidence_location["start_quote"],
                        }
                    ],
                    "evidence_position": (
                        int(evidence_location["source_index"]),
                        int(evidence_location["offset"]),
                    ),
                }
            )

    if not located_window_events or (
        int(located_window_events[0]["source_index"]),
        int(located_window_events[0]["offset"]),
    ) != (0, 0):
        located_window_events.insert(
            0,
            {
                "source_index": 0,
                "source_ref": window_sources[0]["ref"] if window_sources else "",
                "offset": 0,
                "start_quote": (
                    _first_anchor_quote(window_sources[0]["narrative"])
                    if window_sources
                    else ""
                ),
            },
        )
        warnings.append("模型首项没有从故事窗口第一句开始；脚本已补齐窗口起点")
    located_window_events.sort(
        key=lambda item: (item["source_index"], item["offset"])
    )

    part_indexes = {
        source["ref"].partition(":")[2]: index
        for index, source in enumerate(context_sources)
    }
    forming_index = part_indexes.get("previous_forming")
    forming_starts_new = bool(
        forming_index is not None
        and any(
            (int(item["source_index"]), int(item["offset"]))
            == (forming_index, 0)
            for item in located_window_events
        )
    )
    current_starts_new = any(
        (int(item["source_index"]), int(item["offset"]))
        == (current_source_start, 0)
        for item in located_window_events
    )
    if not has_events:
        disposition = "absent"
    elif has_pending and has_forming and not forming_starts_new:
        disposition = "merge_into_pending"
    else:
        disposition = "keep_distinct"
    opening_continues = bool(has_forming and not current_starts_new)
    boundary_decisions: list[dict[str, str]] = []
    if has_pending and has_forming:
        boundary_decisions.append(
            {
                "boundary_id": PENDING_TO_FORMING_BOUNDARY_ID,
                "decision": "keep" if forming_starts_new else "remove",
            }
        )
    if has_forming:
        boundary_decisions.append(
            {
                "boundary_id": PREVIOUS_TO_BATCH_BOUNDARY_ID,
                "decision": "keep" if current_starts_new else "remove",
            }
        )

    current_positions = [
        item
        for item in located_window_events
        if int(item["source_index"]) >= current_source_start
    ]
    located_events: list[dict[str, Any]] = []
    for item in current_positions:
        shifted_index = int(item["source_index"]) - current_source_start
        located_events.append(
            {
                **item,
                "source_index": shifted_index,
                "source_ref": sources[shifted_index]["ref"],
                "continues_previous": False,
            }
        )
    if not located_events or (
        int(located_events[0]["source_index"]), int(located_events[0]["offset"])
    ) != (0, 0):
        located_events.insert(
            0,
            {
                "source_index": 0,
                "source_ref": sources[0]["ref"] if sources else "",
                "offset": 0,
                "start_quote": (
                    _first_anchor_quote(sources[0]["narrative"]) if sources else ""
                ),
                "continues_previous": opening_continues,
            },
        )
        if not opening_continues:
            warnings.append("模型没有给出本批第一句的 Event 起点；脚本已补齐批首分段")
    located_events[0]["continues_previous"] = opening_continues
    for item in located_events[1:]:
        item["continues_previous"] = False
    located_events.sort(key=lambda item: (item["source_index"], item["offset"]))

    roster_by_event: list[dict[str, dict[str, Any]]] = [
        {} for _ in located_events
    ]
    event_positions = [
        (int(item["source_index"]), int(item["offset"]))
        for item in located_events
    ]
    for candidate in roster_candidates:
        evidence_position = candidate.pop("evidence_position")
        target_index = max(
            index
            for index, position in enumerate(event_positions)
            if position <= evidence_position
        )
        by_key = roster_by_event[target_index]
        key = str(candidate["entity_key"])
        target = by_key.setdefault(
            key,
            {
                "entity_key": key,
                "type": candidate["type"],
                "primary_name": candidate["primary_name"],
                "aliases": [],
                "record_action": candidate["record_action"],
                "evidence": [],
                **(
                    {"existing_entity": deepcopy(candidate["existing_entity"])}
                    if isinstance(candidate.get("existing_entity"), dict)
                    else {}
                ),
            },
        )
        target["aliases"] = _unique(
            [*target["aliases"], *_as_list(candidate.get("aliases"))]
        )
        for evidence_item in _as_list(candidate.get("evidence")):
            if evidence_item not in target["evidence"]:
                target["evidence"].append(evidence_item)

    normalized["events"] = [
        {
            **event,
            "entity_roster": list(roster_by_event[index].values()),
        }
        for index, event in enumerate(located_events)
    ]
    normalized["old_forming_disposition"] = disposition
    normalized["boundary_decisions"] = boundary_decisions
    normalized["boundary_source"] = NARRATIVE_MAP_BOUNDARY_SOURCE
    normalized["source_roles"] = ["assistant"]
    normalized["script_warnings"] = warnings
    return normalized


def validate_narrative_map(
    plan: dict[str, Any], source_messages: Iterable[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    sources = _narrative_sources(source_messages)
    if not sources:
        errors.append("本批没有可供 Event 提取的 AI 正文")
    if plan.get("old_forming_disposition") not in ALLOWED_DISPOSITIONS:
        errors.append("old_forming_disposition 非法")
    decisions = plan.get("boundary_decisions")
    if not isinstance(decisions, list):
        errors.append("boundary_decisions 必须是数组")
    else:
        seen_decisions: set[str] = set()
        for index, decision in enumerate(decisions):
            if not isinstance(decision, dict):
                errors.append(f"boundary_decisions[{index}] 不是对象")
                continue
            boundary_id = str(decision.get("boundary_id", ""))
            if not boundary_id or boundary_id in seen_decisions:
                errors.append(f"boundary_decisions[{index}] 的 boundary_id 无效或重复")
            seen_decisions.add(boundary_id)
            if decision.get("decision") not in BOUNDARY_DECISION_VALUES:
                errors.append(f"boundary_decisions[{index}].decision 非法")
    events = plan.get("events")
    if not isinstance(events, list) or not events:
        errors.append("events 必须是非空数组")
    else:
        positions = [
            (int(item.get("source_index", -1)), int(item.get("offset", -1)))
            for item in events
            if isinstance(item, dict)
        ]
        if positions != sorted(set(positions)):
            errors.append("events 没有按原文顺序排列")
        for index, event in enumerate(events):
            if not isinstance(event, dict):
                errors.append(f"events[{index}] 不是对象")
                continue
            if not isinstance(event.get("continues_previous"), bool):
                errors.append(f"events[{index}].continues_previous 必须是布尔值")
            if not isinstance(event.get("entity_roster"), list):
                errors.append(f"events[{index}].entity_roster 必须是数组")
    return errors, [str(item) for item in _as_list(plan.get("script_warnings"))]


def build_boundary_plan(
    narrative_map: dict[str, Any], source_messages: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """把 AI 的嵌套 Event + 名录转换为完整、连续的来源分段。"""

    sources = _narrative_sources(source_messages)
    if not sources:
        raise ValueError("本批没有可分段的 AI 正文")
    starts = [
        deepcopy(item)
        for item in _as_list(narrative_map.get("events"))
        if isinstance(item, dict)
    ]
    if not starts:
        raise ValueError("Narrative Map 没有可用 Event")
    disposition = str(narrative_map.get("old_forming_disposition", ""))
    opening_continues = bool(starts[0].get("continues_previous"))
    next_new_number = 1
    segments: list[dict[str, Any]] = []
    for index, start in enumerate(starts):
        if index == 0 and opening_continues and disposition == "keep_distinct":
            slot = "forming_existing"
        elif index == 0 and opening_continues and disposition == "merge_into_pending":
            slot = "pending_tail"
        else:
            slot = f"new_{next_new_number}"
            next_new_number += 1
        start_source_index = int(start["source_index"])
        end_source_index = (
            int(starts[index + 1]["source_index"])
            if index + 1 < len(starts)
            else len(sources) - 1
        )
        segments.append(
            {
                "partition_key": f"v2_segment_{index + 1:03d}",
                "slot": slot,
                "source_refs": [
                    source["ref"]
                    for source in sources[start_source_index : end_source_index + 1]
                ],
                "start_anchors": [
                    {
                        "source_ref": str(start["source_ref"]),
                        "start_quote": str(start["start_quote"]),
                    }
                ],
                "entity_roster": deepcopy(_as_list(start.get("entity_roster"))),
            }
        )
    return {
        "old_forming_disposition": disposition,
        "decision_reason": "AI 顺序返回故事窗口内的 Event 起点；脚本按起点位置换算状态、定位短引并连续切片",
        "boundary_source": NARRATIVE_MAP_BOUNDARY_SOURCE,
        "source_roles": ["assistant"],
        "boundary_decisions": deepcopy(
            _as_list(narrative_map.get("boundary_decisions"))
        ),
        "segments": segments,
    }


def build_narrative_map_user_prompt(
    *,
    batch_number: int,
    story_text: str,
    continuity_context: dict[str, Any],
    existing_entity_previews: dict[str, Any],
) -> str:
    import json

    return (
        f"第 {batch_number} 批。按顺序排列的相邻前文（可为空）：\n"
        + json.dumps(continuity_context, ensure_ascii=False, indent=2)
        + "\n\n相关既有实体简表（只用于确认名称和别称）：\n"
        + json.dumps(existing_entity_previews, ensure_ascii=False, indent=2)
        + "\n\n本批连续故事：\n"
        + story_text.strip()
        + "\n\n直接返回最终 JSON。"
    )


def build_event_content_user_prompt(
    *,
    batch_number: int,
    fixed_segments: list[dict[str, Any]],
    existing_event_tail: dict[str, Any],
) -> str:
    payload = {
        "batch_number": batch_number,
        "fixed_segments": fixed_segments,
        "existing_event_tail": existing_event_tail,
    }
    import json

    return "请为每个固定 Event 分段写内容。\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def split_entity_workloads(
    fixed_segments: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """按脚本解析结果拆出新增、更新和待确认目录。

    每个模型任务仍按批次处理，不为每个 Entity 单独调用。分段正文保持原样，
    只过滤目录项；这样新增与更新任务都能看到支持自身候选的完整上下文。
    """

    workloads: dict[str, list[dict[str, Any]]] = {
        "create": [],
        "update": [],
        "unresolved": [],
    }
    for segment in fixed_segments:
        if not isinstance(segment, dict):
            continue
        rosters: dict[str, list[dict[str, Any]]] = {
            action: [] for action in workloads
        }
        for roster in _as_list(segment.get("entity_roster")):
            if not isinstance(roster, dict):
                continue
            action = str(roster.get("record_action", "")).strip()
            if action not in ENTITY_RECORD_ACTIONS:
                action = "unresolved"
            rosters[action].append(deepcopy(roster))
        for action, items in rosters.items():
            if not items:
                continue
            workloads[action].append(
                {
                    **{
                        key: deepcopy(value)
                        for key, value in segment.items()
                        if key != "entity_roster"
                    },
                    "entity_roster": items,
                }
            )
    return workloads


def build_entity_create_user_prompt(
    *,
    batch_number: int,
    fixed_segments: list[dict[str, Any]],
) -> str:
    payload = {
        "batch_number": batch_number,
        "fixed_event_segments": fixed_segments,
        "open_semantic_component_catalog": open_semantic_component_catalog(),
    }
    import json

    return "请只建立脚本已判定为新增的 Entity。\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def build_entity_update_user_prompt(
    *,
    batch_number: int,
    fixed_segments: list[dict[str, Any]],
) -> str:
    payload = {
        "batch_number": batch_number,
        "fixed_event_segments": fixed_segments,
        "open_semantic_component_catalog": open_semantic_component_catalog(),
    }
    import json

    return "请只为脚本已匹配的既有 Entity 返回字段级更新。\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def build_relation_reference_user_prompt(
    *,
    batch_number: int,
    fixed_segments: list[dict[str, Any]],
    existing_entity_previews: dict[str, Any],
) -> str:
    payload = {
        "batch_number": batch_number,
        "fixed_event_segments": fixed_segments,
        "existing_entity_candidates": existing_entity_previews,
    }
    import json

    return "请核对本批 Entity—Event 引用并提取 Character Relation。\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


def merge_roster_fallbacks(
    entity_plan: dict[str, Any], event_rosters: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """把第一阶段已确认且有原文证据的名录项补成最小 Entity 候选。

    第二阶段仍负责丰富内容和发现遗漏；脚本只复用名称、Type、别称与证据，不
    猜外貌、背景、所有权等资料。
    """

    normalized = deepcopy(entity_plan)
    entities = [
        item for item in _as_list(normalized.get("entities")) if isinstance(item, dict)
    ]
    normalized["entities"] = entities
    normalized.setdefault("relations", [])
    by_key = {str(item.get("entity_key", "")): item for item in entities}
    additions: list[str] = []
    type_labels = {
        "character": "人物",
        "location": "地点",
        "item": "物品",
        "organization": "组织",
        "skill": "技能",
        "concept": "概念",
    }
    roster_items = [
        roster_item
        for event_section in event_rosters
        if isinstance(event_section, dict)
        for roster_item in _as_list(event_section.get("entity_roster"))
        if isinstance(roster_item, dict)
    ]
    for roster_item in roster_items:
        if not isinstance(roster_item, dict):
            continue
        if roster_item.get("record_action") == "unresolved":
            continue
        key = str(roster_item.get("entity_key", "")).strip()
        entity_type = str(roster_item.get("type", "")).strip()
        name = str(roster_item.get("primary_name", "")).strip()
        evidence = [
            item
            for item in _as_list(roster_item.get("evidence"))
            if isinstance(item, dict)
            and str(item.get("source_ref", "")).strip()
            and str(item.get("quote", "")).strip()
        ]
        if not key or entity_type not in ROSTER_TYPES or not name or not evidence:
            continue
        candidate = by_key.get(key)
        if candidate is None:
            label = type_labels[entity_type]
            candidate = {
                "entity_key": key,
                "type": entity_type,
                "primary_name": name,
                "aliases_add": deepcopy(_as_list(roster_item.get("aliases"))),
                "description": f"本批故事中形成清楚事实的{label}“{name}”。",
                "evidence_refs": _unique(item["source_ref"] for item in evidence),
                "event_link_evidence": [
                    {
                        "source_ref": item["source_ref"],
                        "quote": item["quote"],
                    }
                    for item in evidence
                ],
                "_roster_fallback": True,
            }
            entities.append(candidate)
            by_key[key] = candidate
            additions.append(key)
            continue
        candidate["aliases_add"] = _unique(
            [
                *_as_list(candidate.get("aliases_add")),
                *_as_list(roster_item.get("aliases")),
            ]
        )
        candidate["evidence_refs"] = _unique(
            [
                *_as_list(candidate.get("evidence_refs")),
                *(item["source_ref"] for item in evidence),
            ]
        )
        existing_links = [
            item
            for item in _as_list(candidate.get("event_link_evidence"))
            if isinstance(item, dict)
        ]
        for item in evidence:
            link = {
                "source_ref": item["source_ref"],
                "quote": item["quote"],
            }
            if not any(
                old.get("source_ref") == link["source_ref"]
                and old.get("quote") == link["quote"]
                for old in existing_links
            ):
                existing_links.append(link)
        candidate["event_link_evidence"] = existing_links
    normalized["script_roster_fallback_additions"] = additions
    return normalized


def finalize_roster_fallbacks(
    entity_plan: dict[str, Any], *, existing_entity_keys: Iterable[str] = ()
) -> dict[str, Any]:
    """归一名称后合并名录兜底，避免通用说明覆盖既有或模型丰富内容。"""

    normalized = deepcopy(entity_plan)
    existing = {str(key) for key in existing_entity_keys}
    output: list[dict[str, Any]] = []
    first_by_key: dict[str, dict[str, Any]] = {}
    for candidate in _as_list(normalized.get("entities")):
        if not isinstance(candidate, dict):
            continue
        item = deepcopy(candidate)
        key = str(item.get("entity_key", ""))
        is_fallback = bool(item.pop("_roster_fallback", False))
        target = first_by_key.get(key)
        if is_fallback and target is not None:
            target["aliases_add"] = _unique(
                [
                    *_as_list(target.get("aliases_add")),
                    *_as_list(item.get("aliases_add")),
                ]
            )
            target["evidence_refs"] = _unique(
                [
                    *_as_list(target.get("evidence_refs")),
                    *_as_list(item.get("evidence_refs")),
                ]
            )
            links = [
                link
                for link in _as_list(target.get("event_link_evidence"))
                if isinstance(link, dict)
            ]
            for link in _as_list(item.get("event_link_evidence")):
                if isinstance(link, dict) and link not in links:
                    links.append(deepcopy(link))
            target["event_link_evidence"] = links
            continue
        if is_fallback and key in existing:
            item.pop("primary_name", None)
            item.pop("description", None)
        output.append(item)
        first_by_key.setdefault(key, item)
    normalized["entities"] = output
    normalized["script_roster_fallback_additions"] = _unique(
        str(key)
        for key in _as_list(normalized.get("script_roster_fallback_additions"))
        if str(key)
    )
    return normalized


def merge_event_content_with_boundaries(
    content_plan: dict[str, Any], boundary_plan: dict[str, Any]
) -> dict[str, Any]:
    """第二阶段只能填内容；边界字段全部取自脚本锁定稿。"""

    event_updates = []
    for raw_update in _as_list(content_plan.get("event_updates")):
        if not isinstance(raw_update, dict):
            continue
        event_updates.append(
            {
                key: deepcopy(raw_update[key])
                for key in (
                    "partition_key",
                    "story_summary_add",
                    "description",
                    "new_key_details",
                )
                if key in raw_update
            }
        )

    return {
        "old_forming_disposition": boundary_plan["old_forming_disposition"],
        "decision_reason": boundary_plan.get("decision_reason", ""),
        "boundary_source": NARRATIVE_MAP_BOUNDARY_SOURCE,
        "source_roles": ["assistant"],
        "segments": deepcopy(_as_list(boundary_plan.get("segments"))),
        "event_updates": event_updates,
    }
