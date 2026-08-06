"""构建不携带原始叙事的 Event 常规召回包。

原文记录属于内部证据与维护层。常规角色扮演只得到 Event 的检索说明、故事摘要、
结构化引用和少量已经选定的关键特写；本模块没有“自动读取原文”的入口。
上游检索器负责按相关性排列 Event，并可明确指定本次需要的 ``detail_id``。
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _detail_view(detail: dict[str, Any]) -> dict[str, Any]:
    """只投影回忆需要的特写；来源书签和原文位置不得进入常规上下文。"""

    result = {
        key: detail[key]
        for key in ("kind", "content", "fidelity")
        if detail.get(key) not in (None, "", [])
    }
    detail_id = detail.get("id") or detail.get("detail_id")
    if detail_id:
        result["id"] = detail_id
    actor = detail.get("actor") or detail.get("actor_ref")
    if actor:
        result["actor"] = actor
    return result


def _component_data(event: dict[str, Any], name: str) -> dict[str, Any]:
    component = event.get("components", {}).get(name)
    if not isinstance(component, dict) or not isinstance(component.get("data"), dict):
        return {}
    return component["data"]


def _recall_event_view(event: dict[str, Any]) -> dict[str, Any]:
    """把正式 Entity 或开发期扁平 Event 统一成召回视图。"""

    if not isinstance(event.get("components"), dict):
        return event
    content = _component_data(event, "event_content")
    time_data = _component_data(event, "event_time")
    location_data = _component_data(event, "event_location_reference")
    related_data = _component_data(event, "event_related_entity_reference")
    return {
        "id": event.get("id"),
        "description": event.get("description"),
        "status": content.get("status"),
        "story_summary": content.get("story_summary"),
        "key_details": content.get("key_details", []),
        "event_time": time_data or None,
        "locations": [
            item.get("location_ref")
            for item in location_data.get("location_refs", [])
            if isinstance(item, dict) and isinstance(item.get("location_ref"), dict)
        ],
        "related_entity_refs": related_data.get("related_entity_refs", []),
    }


def _ordered_details(
    event: dict[str, Any], requested_ids: Iterable[str] | None
) -> list[dict[str, Any]]:
    details = [
        detail
        for detail in event.get("key_details", [])
        if isinstance(detail, dict) and str(detail.get("content", "")).strip()
    ]
    if requested_ids is None:
        return details
    by_id = {
        str(detail.get("id") or detail.get("detail_id") or ""): detail
        for detail in details
    }
    return [by_id[detail_id] for detail_id in requested_ids if detail_id in by_id]


def build_event_recall_pack(
    events: Iterable[dict[str, Any]],
    *,
    event_ids: Iterable[str] | None = None,
    detail_ids_by_event: Mapping[str, Iterable[str]] | None = None,
    max_events: int = 5,
    max_key_details: int = 5,
    max_chars: int = 4000,
) -> dict[str, Any]:
    """生成有硬预算的常规 Event 召回包。

    ``events`` 应由上游检索器按相关性从高到低排列。预算不足时先省略关键特写，
    再省略故事摘要，最后停止加入更多 Event；不会以截断句子的方式改写事实。
    """

    if max_events < 1 or max_key_details < 0 or max_chars < 200:
        raise ValueError("Event 召回预算非法")

    wanted = set(event_ids) if event_ids is not None else None
    selected = [
        _recall_event_view(event)
        for event in events
        if isinstance(event, dict)
        and (wanted is None or str(event.get("id", "")) in wanted)
    ][:max_events]
    requested = detail_ids_by_event or {}
    cards: list[dict[str, Any]] = []
    omitted_event_ids: list[str] = []
    omitted_summary_ids: list[str] = []
    omitted_detail_ids: list[str] = []
    details_used = 0

    for event in selected:
        event_id = str(event.get("id", ""))
        card = {
            key: event[key]
            for key in (
                "id",
                "status",
                "description",
                "event_time",
                "locations",
                "related_entity_keys",
                "related_entity_refs",
            )
            if event.get(key) not in (None, "", [])
        }
        if _json_chars({"events": [*cards, card]}) > max_chars:
            omitted_event_ids.append(event_id)
            continue

        summary = str(event.get("story_summary", "")).strip()
        if summary:
            with_summary = {**card, "story_summary": summary}
            if _json_chars({"events": [*cards, with_summary]}) <= max_chars:
                card = with_summary
            else:
                omitted_summary_ids.append(event_id)

        requested_ids = requested.get(event_id)
        detail_candidates = _ordered_details(event, requested_ids)
        kept_details: list[dict[str, Any]] = []
        for detail in detail_candidates:
            detail_id = str(detail.get("id", ""))
            if details_used >= max_key_details:
                if detail_id:
                    omitted_detail_ids.append(detail_id)
                continue
            projected = _detail_view(detail)
            with_detail = {**card, "key_details": [*kept_details, projected]}
            if _json_chars({"events": [*cards, with_detail]}) > max_chars:
                if detail_id:
                    omitted_detail_ids.append(detail_id)
                continue
            kept_details.append(projected)
            details_used += 1
        if kept_details:
            card["key_details"] = kept_details
        cards.append(card)

    included_ids = {str(card.get("id", "")) for card in cards}
    omitted_event_ids.extend(
        str(event.get("id", ""))
        for event in selected
        if str(event.get("id", "")) not in included_ids
        and str(event.get("id", "")) not in omitted_event_ids
    )
    result = {
        "mode": "normal_event_recall",
        "events": cards,
        "omitted": {
            "event_ids": omitted_event_ids,
            "summary_event_ids": omitted_summary_ids,
            "key_detail_ids": omitted_detail_ids,
        },
        "budget": {
            "max_events": max_events,
            "max_key_details": max_key_details,
            "max_chars": max_chars,
            "used_chars": 0,
        },
    }

    def refresh_used_chars() -> int:
        # used_chars 自身位数也占上下文；迭代几次即可稳定。
        for _ in range(4):
            size = _json_chars(result)
            if result["budget"]["used_chars"] == size:
                return size
            result["budget"]["used_chars"] = size
        return _json_chars(result)

    # ``max_chars`` 约束整个可注入对象，不只约束 Event 正文。极小预算下仍按
    # “特写 -> 摘要 -> 后排 Event”的顺序退让，不截断句子或偷偷改写原文。
    while refresh_used_chars() > max_chars:
        detail_removed = False
        for card in reversed(cards):
            details = card.get("key_details")
            if not isinstance(details, list) or not details:
                continue
            removed = details.pop()
            detail_id = str(removed.get("id", "")) if isinstance(removed, dict) else ""
            if detail_id and detail_id not in omitted_detail_ids:
                omitted_detail_ids.append(detail_id)
            if not details:
                card.pop("key_details", None)
            detail_removed = True
            break
        if detail_removed:
            continue

        summary_card = next(
            (card for card in reversed(cards) if "story_summary" in card), None
        )
        if summary_card is not None:
            summary_card.pop("story_summary", None)
            event_id = str(summary_card.get("id", ""))
            if event_id and event_id not in omitted_summary_ids:
                omitted_summary_ids.append(event_id)
            continue

        if cards:
            removed = cards.pop()
            event_id = str(removed.get("id", ""))
            if event_id and event_id not in omitted_event_ids:
                omitted_event_ids.append(event_id)
            continue

        # 只有诊断编号本身挤满极小预算时才压成计数；正常召回仍保留具体编号。
        omission_lists = result.get("omitted", {})
        if any(isinstance(value, list) and value for value in omission_lists.values()):
            result["omitted"] = {
                "event_count": len(omitted_event_ids),
                "summary_count": len(omitted_summary_ids),
                "key_detail_count": len(omitted_detail_ids),
            }
            continue

        # max_chars 最低允许 200，正常不会走到这里；保留一个明确的空召回标记。
        result.clear()
        result.update(
            {
                "mode": "normal_event_recall",
                "events": [],
                "truncated_by_budget": True,
            }
        )
        break

    if "budget" in result:
        refresh_used_chars()
    return result
