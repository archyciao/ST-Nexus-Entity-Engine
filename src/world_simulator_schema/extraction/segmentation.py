"""Event 分阶段语义测试工具。

边界模型只决定来源切分；固定脚本创建、合并和迁移引用；内容模型在固定边界内完善
Event。每批原子提交，并完整归档提示词、模型正式回复和机器结果。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from copy import deepcopy
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator


BOUNDARY_SYSTEM_PROMPT = """
你只负责判断连续 AIRP 叙事的 Event 边界，不写标题、摘要、关键细节或 Memory。

Event 是可独立命名、理解、检索和回忆的小故事，不等于回合，也不等于跨场景的长期目标。结果后的直接余波可以留在原 Event；主导目标、因果中心、互动目的、话题或整体情境明显改变时开始新 Event。“拜师并回山”“长期追查”“适应宗门”等宽泛目的不能吞并多个本可独立回忆的小故事。

普通边界只能出现在本批新增消息中。若同一条消息先收尾旧 Event、随后开始新行动，必须把边界定位到新行动开头，不能把整条消息提前划给新 Event。不要回到已经处理的旧消息内部重新切分。

old_forming_disposition 只能是：
- absent：首次整理，此前没有 Event；
- keep_distinct：旧生成中是独立 Event，本批可以继续它并在之后开启新 Event；
- merge_into_pending：旧生成中全部属于待定稿余波，固定脚本会整体合并；真正的新边界必须在本批内容中。

只输出 JSON：
{
  "old_forming_disposition": "absent | keep_distinct | merge_into_pending",
  "decision_reason": "一句边界理由",
  "segments": [{
    "slot": "pending_tail | forming_existing | new_1 | new_2 ...",
    "source_refs": ["本批消息引用"],
    "start_anchors": [{
      "source_ref": "这个原文开头所在的消息引用",
      "start_quote": "本段在该消息中的开头原文短引"
    }],
    "segment_summary": "一句话说明本段发生了什么"
  }]
}

slot 规则：
- merge_into_pending：旧生成中由脚本并入待定稿；本批前缀用 pending_tail，边界后从 new_1 开始，不得使用 forming_existing。
- keep_distinct：旧生成中延续部分用 forming_existing，后续从 new_1 开始，不得使用 pending_tail。
- absent：从 new_1 开始，不得使用旧 slot。
- 每个 slot 只输出一个 segment，start_anchors 至少有一项；第一项必须定位 source_refs 的第一条消息。
- 同一消息内部有边界时，相邻 segment 可以重复该消息引用，新 segment 用 start_anchors 定位。
- 若同一轮的 user 和 assistant 消息都各自包含旧 Event 收尾与新 Event 开头，必须在新 segment 中为两条消息分别给出 start_anchor。
- 固定脚本从每个 new_N 的 start_anchors 自动推导边界；不要另写 boundaries。
- 每条新增消息至少归属一个 segment。
- 不判断数据库操作、状态编号、Memory 迁移或 Event 内容质量。
""".strip()


CONTENT_SYSTEM_PROMPT = """
你只负责完善已经切好边界的 Event 内容。边界、slot 和来源归属已经由上一阶段确定，不得重新切分、合并或质疑。

对每个 affected_event 输出截至本批结束的完整 title、description 和 story_summary：
- description 用一至三句客观文字概括参与者、情境、主要行动和当前落点，便于快速理解和向量检索；
- story_summary 约三百个汉字且不超过五百字，覆盖有实际作用的出场人物、主要行动与转折、直接因果、重要物品或命令、结果或明确未决事项，不能只是改写 description；
- 旧 Event 必须保留 existing_content 中仍有效的信息，不能只写本批增量；
- locations 只记录实际发生或经过的地点，不记录仅提及、受邀或计划前往的地点；
- unresolved 只记录叙事明确留下的问题，不自由猜测；
- key_details 是该 Event 截至本批结束的总选择，不是每批叠加。默认保留最值得闪回的三条关键言语原话或关键动作原文特写，零至两条也可以，通常不超过五条；数量是软约束，不解释，也不为凑数添加。
- fixed_segments 中的 end_before_anchors 是固定脚本派生的排他性结束线；assigned_messages 已由脚本按每条消息的起止定位裁好，只包含当前 Event 可使用的本批原文。不得从相邻 Event 或其他上下文吸收事实。

不虚构输入中没有发生的事实、动机、结果或参与者。相关 Entity 只用于消除名称和设定歧义，不能覆盖叙事事实。

只输出 JSON：
{
  "event_updates": [{
    "slot": "与 affected_events 相同",
    "title": "简短标题",
    "description": "完整检索说明",
    "story_summary": "完整故事摘要",
    "participants_add": ["本批新增的客观参与者"],
    "participants_remove": ["确认误归、需要移除的参与者"],
    "locations_add": ["本批新增的实际发生地点"],
    "locations_remove": ["确认误归、需要移除的地点"],
    "unresolved_add": ["本批新出现的明确未决事项"],
    "unresolved_resolve": ["本批已经解决或确认不成立的事项"],
    "key_details_keep": ["从 existing_content.key_details 中保留的 id"],
    "new_key_details": [{
      "kind": "statement | action",
      "content": "叙事原话或动作原文短引",
      "actor": "相关人物；不适用则空字符串",
      "source_refs": ["本批消息引用"]
    }]
  }]
}
""".strip()


ALLOWED_DISPOSITIONS = {"absent", "keep_distinct", "merge_into_pending"}
OLD_SLOTS = {"pending_tail", "forming_existing"}
TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
CONTENT_RE = re.compile(r"<content\b[^>]*>(.*?)</content>", re.IGNORECASE | re.DOTALL)
SCENE_RE = re.compile(r"^\s*```([^`\r\n]+)```", re.MULTILINE)


def clean_message(text: str, role: str) -> str:
    """提取酒馆消息中会被角色实际看到的叙事正文。"""

    text = text.replace("\x00", "")
    if role == "assistant":
        scene = SCENE_RE.search(text)
        content = CONTENT_RE.search(text)
        if content:
            text = content.group(1)
        text = COMMENT_RE.sub("", text)
        text = TAG_RE.sub("", text)
        text = html.unescape(text)
        if scene:
            text = f"[场景时间：{scene.group(1).strip()}]\n{text}"
    else:
        text = COMMENT_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def iter_messages(path: Path) -> Iterator[dict[str, Any]]:
    """逐行读取 JSONL；不会把完整聊天载入内存。"""

    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_number} 行不是有效 JSON：{exc}") from exc
            if "mes" not in item:
                continue
            role = "user" if item.get("is_user") else "assistant"
            yield {
                "source_line": line_number,
                "role": role,
                "name": str(item.get("name") or role),
                "content": clean_message(str(item.get("mes") or ""), role),
            }


def iter_rounds(path: Path) -> Iterator[dict[str, Any]]:
    """把一条 user 消息和其后的 assistant 消息组成一轮。"""

    pending_user: dict[str, Any] | None = None
    round_number = 0
    for message in iter_messages(path):
        if message["role"] == "user":
            pending_user = message
            continue
        if pending_user is None:
            continue
        round_number += 1
        user = {**pending_user, "ref": f"r{round_number:04d}.user"}
        assistant = {**message, "ref": f"r{round_number:04d}.assistant"}
        yield {"round": round_number, "user": user, "assistant": assistant}
        pending_user = None


def take_batch(iterator: Iterator[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    return list(islice(iterator, size))


def initial_state() -> dict[str, Any]:
    return {
        "events": [],
        "memories": [],
        "next_event_number": 1,
        "next_memory_number": 1,
        "next_detail_number": 1,
    }


def event_by_status(state: dict[str, Any], status: str) -> dict[str, Any] | None:
    return next((event for event in state["events"] if event["status"] == status), None)


def state_view_for_boundary(state: dict[str, Any]) -> dict[str, Any]:
    finalized = [event for event in state["events"] if event["status"] == "finalized"]
    pending = event_by_status(state, "pending_finalization")
    forming = event_by_status(state, "forming")

    def preview(event: dict[str, Any] | None) -> dict[str, Any] | None:
        if event is None:
            return None
        return {
            "id": event["id"],
            "title": event.get("title", ""),
            "description": event.get("description", ""),
            "story_summary": event.get("story_summary", ""),
            "participants": event.get("participants", []),
            "locations": event.get("locations", []),
            "unresolved": event.get("unresolved", []),
            "source_refs": event.get("source_refs", []),
        }

    return {
        "previous_finalized_description": (
            finalized[-1].get("description", "") if finalized else None
        ),
        "pending_event": preview(pending),
        "forming_event": preview(forming),
    }


def batch_messages(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for item in rounds:
        if "messages" in item:
            messages.extend({**m, "speaker": m["name"]} for m in item["messages"])
            continue
        for role in ("user", "assistant"):
            message = item[role]
            messages.append(
                {
                    "ref": message["ref"],
                    "role": role,
                    "speaker": message["name"],
                    "source_line": message["source_line"],
                    "content": message["content"],
                }
            )
    return messages


def build_boundary_prompt(
    state: dict[str, Any],
    rounds: list[dict[str, Any]],
    batch_number: int,
    entity_context: Any = None,
) -> str:
    payload = {
        "batch_number": batch_number,
        "existing_event_tail": state_view_for_boundary(state),
        "related_entities": entity_context,
        "new_messages": batch_messages(rounds),
    }
    return (
        "只判定本批 Event 边界。existing_event_tail 是已处理内容的压缩上下文；"
        "所有 boundary 和 segment 来源必须定位在 new_messages。\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def build_content_prompt(
    state: dict[str, Any],
    boundary_plan: dict[str, Any],
    slot_event_ids: dict[str, str],
    rounds: list[dict[str, Any]],
    batch_number: int,
    entity_context: Any = None,
) -> str:
    events_by_id = {event["id"]: event for event in state["events"]}
    affected_events = []
    for slot in dict.fromkeys(segment["slot"] for segment in boundary_plan["segments"]):
        event = events_by_id[slot_event_ids[slot]]
        affected_events.append(
            {
                "slot": slot,
                "event_id": event["id"],
                "status": event["status"],
                "existing_content": {
                    "title": event.get("title", ""),
                    "description": event.get("description", ""),
                    "story_summary": event.get("story_summary", ""),
                    "participants": event.get("participants", []),
                    "locations": event.get("locations", []),
                    "unresolved": event.get("unresolved", []),
                    "key_details": event.get("key_details", []),
                    "merged_forming_content": event.get("_merged_forming_content"),
                },
            }
        )
    payload = {
        "batch_number": batch_number,
        "fixed_segments": bounded_segments_for_content(boundary_plan, rounds),
        "affected_events": affected_events,
        "related_entities": entity_context,
    }
    return (
        "边界已经固定。只完善 affected_events 的内容，不得改变 fixed_segments。\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


class ChatCompletionTransportError(RuntimeError):
    """携带流式失败前已收到的正式正文与非敏感统计。"""

    def __init__(
        self,
        message: str,
        *,
        partial_content: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.partial_content = partial_content
        self.metadata = metadata or {}


def chat_completion_request_body(
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    *,
    thinking_mode: str,
    response_format: str,
) -> dict[str, Any]:
    """建立请求体；便于单测确认思考开关没有串用其他 API。"""

    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "stream": True,
    }
    if thinking_mode == "off":
        # DeepSeek 官方开关仍是权威字段；Zen 当前实测还需要顶层 none
        # 才能稳定关闭。后者是网关兼容行为，保留实际 reasoning 监测兜底。
        body["thinking"] = {"type": "disabled"}
        body["reasoning_effort"] = "none"
        body["temperature"] = 0
    elif thinking_mode != "default":
        body["thinking"] = {"type": "enabled"}
        body["reasoning_effort"] = thinking_mode
    if response_format == "json_object":
        body["response_format"] = {"type": "json_object"}
    return body


def call_chat_completion(
    endpoint: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float,
    max_tokens: int,
    *,
    thinking_mode: str = "default",
    response_format: str = "text",
    stream_idle_timeout: float = 90.0,
    content_start_timeout: float = 90.0,
) -> tuple[str, dict[str, Any]]:
    if thinking_mode not in {"default", "off", "low", "medium", "high", "max"}:
        raise ValueError(f"不支持的 thinking_mode：{thinking_mode}")
    if response_format not in {"text", "json_object"}:
        raise ValueError(f"不支持的 response_format：{response_format}")
    body = chat_completion_request_body(
        model,
        system_prompt,
        user_prompt,
        max_tokens,
        thinking_mode=thinking_mode,
        response_format=response_format,
    )
    wire_controls = {
        key: deepcopy(body[key])
        for key in ("thinking", "reasoning_effort", "temperature", "response_format")
        if key in body
    }

    request_started = time.perf_counter()
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl is None:
        raise RuntimeError("系统中没有 curl，无法调用受 Cloudflare 保护的 API")
    request_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as request_file:
            json.dump(body, request_file, ensure_ascii=False)
            request_path = Path(request_file.name)
        # 认证头只从 curl 的标准输入读取，不进入命令行参数、请求文件或日志。
        curl_config = "\n".join(
            [
                "silent",
                "show-error",
                "fail-with-body",
                'request = "POST"',
                f'header = "Authorization: Bearer {api_key}"',
                'header = "Content-Type: application/json"',
                'header = "Accept: application/json"',
                'user-agent = "curl/8.21.0"',
            ]
        )
        curl_args = [
                curl,
                "--config",
                "-",
                "--no-buffer",
                "--max-time",
                str(timeout),
                "--data-binary",
                f"@{request_path}",
                endpoint,
            ]
        if stream_idle_timeout > 0:
            curl_args[8:8] = [
                "--speed-limit",
                "1",
                "--speed-time",
                str(max(1, int(stream_idle_timeout))),
            ]
        process = subprocess.Popen(
            curl_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        process.stdin.write(curl_config)
        process.stdin.close()
        process.stdin = None

        content_parts: list[str] = []
        plain_lines: list[str] = []
        metadata: dict[str, Any] = {
            "id": None,
            "model": None,
            "usage": {},
            "finish_reason": None,
            "stream": True,
            "request_options": {
                "thinking_mode": thinking_mode,
                "response_format": response_format,
                "max_tokens": max_tokens,
                "stream_idle_timeout": stream_idle_timeout,
                "content_start_timeout": content_start_timeout,
                "wire_controls": wire_controls,
            },
            "reasoning_chars": 0,
            "content_chars": 0,
            "timing_seconds": {
                "first_stream_chunk": None,
                "first_reasoning": None,
                "first_content": None,
                "total": None,
            },
        }
        received_chars = 0
        next_progress = 1000
        stream_started = False
        abort_reason: str | None = None
        for line in process.stdout:
            stripped = line.strip()
            if not stripped:
                continue
            elapsed = time.perf_counter() - request_started
            if (
                content_start_timeout > 0
                and metadata["timing_seconds"]["first_content"] is None
                and elapsed >= content_start_timeout
            ):
                abort_reason = (
                    f"流式响应持续 {round(elapsed, 3)} 秒仍未开始正式 content；"
                    "已停止本次异常长推理"
                )
                process.terminate()
                break
            if not stripped.startswith("data:"):
                plain_lines.append(line)
                continue
            payload = stripped[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                chunk = json.loads(payload)
            except json.JSONDecodeError:
                plain_lines.append(line)
                continue
            if not stream_started:
                print("  已收到首个流式响应片段", flush=True)
                stream_started = True
                metadata["timing_seconds"]["first_stream_chunk"] = round(
                    time.perf_counter() - request_started, 3
                )
            metadata["id"] = chunk.get("id") or metadata["id"]
            metadata["model"] = chunk.get("model") or metadata["model"]
            if chunk.get("usage"):
                metadata["usage"] = chunk["usage"]
            metadata["timing_seconds"]["last_stream_activity"] = round(
                elapsed, 3
            )
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            if choice.get("finish_reason"):
                metadata["finish_reason"] = choice["finish_reason"]
            delta = choice.get("delta") or {}
            content_delta = delta.get("content")
            reasoning_delta = delta.get("reasoning_content")
            if isinstance(content_delta, str):
                if content_delta and metadata["timing_seconds"]["first_content"] is None:
                    metadata["timing_seconds"]["first_content"] = round(
                        time.perf_counter() - request_started, 3
                    )
                content_parts.append(content_delta)
                received_chars += len(content_delta)
                metadata["content_chars"] += len(content_delta)
            if isinstance(reasoning_delta, str):
                if (
                    reasoning_delta
                    and metadata["timing_seconds"]["first_reasoning"] is None
                ):
                    metadata["timing_seconds"]["first_reasoning"] = round(
                        time.perf_counter() - request_started, 3
                    )
                received_chars += len(reasoning_delta)
                metadata["reasoning_chars"] += len(reasoning_delta)
            if received_chars >= next_progress:
                print(
                    "  流式保活：隐藏推理约 "
                    f"{metadata['reasoning_chars']} 字，正式正文约 "
                    f"{metadata['content_chars']} 字",
                    flush=True,
                )
                next_progress += 1000

        if abort_reason is not None and process.poll() is None:
            process.terminate()
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return_code = process.wait(timeout=5)
        stderr_text = process.stderr.read()
        metadata["timing_seconds"]["total"] = round(
            time.perf_counter() - request_started, 3
        )
    finally:
        if request_path is not None:
            request_path.unlink(missing_ok=True)
    content = "".join(content_parts)
    if abort_reason is not None:
        raise ChatCompletionTransportError(
            abort_reason
            + f"（reasoning_chars={metadata['reasoning_chars']}，"
            f"content_chars={metadata['content_chars']}）",
            partial_content=content,
            metadata=metadata,
        )
    if return_code != 0:
        detail = ("".join(plain_lines) or stderr_text).strip()
        raise ChatCompletionTransportError(
            f"API 请求失败（curl {return_code}）：{detail[:500]}",
            partial_content=content,
            metadata=metadata,
        )

    if content.strip():
        return content, metadata

    # 某些兼容端点会忽略 stream=true 并返回普通 JSON，保留兼容读取。
    response_body = "".join(plain_lines)
    if not response_body.strip():
        reasoning_chars = metadata["reasoning_chars"]
        content_chars = metadata["content_chars"]
        if metadata["finish_reason"] == "length":
            raise RuntimeError(
                "生成达到 max_tokens 后仍没有完整正式 content"
                f"（reasoning_chars={reasoning_chars}，content_chars={content_chars}，"
                "finish_reason=length）"
            )
        if received_chars == 0:
            raise RuntimeError(
                "接口结束但没有返回推理或正式 content"
                f"（finish_reason={metadata['finish_reason']}）"
            )
        raise RuntimeError(
            "流式响应结束但没有可用的正式 content"
            f"（reasoning_chars={reasoning_chars}，content_chars={content_chars}，"
            f"finish_reason={metadata['finish_reason']}）"
        )
    data = json.loads(response_body)
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("API 响应没有 choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part)
            for part in content
        )
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("API 响应没有可用的 message.content")
    metadata = {
        "id": data.get("id"),
        "model": data.get("model"),
        "usage": data.get("usage") or {},
        "finish_reason": choices[0].get("finish_reason"),
        "stream": False,
        "request_options": {
            "thinking_mode": thinking_mode,
            "response_format": response_format,
            "max_tokens": max_tokens,
            "stream_idle_timeout": stream_idle_timeout,
            "content_start_timeout": content_start_timeout,
            "wire_controls": wire_controls,
        },
        "reasoning_chars": len(str(message.get("reasoning_content") or "")),
        "content_chars": len(content),
        "timing_seconds": {
            "first_stream_chunk": None,
            "first_reasoning": None,
            "first_content": None,
            "total": round(time.perf_counter() - request_started, 3),
        },
    }
    return content, metadata


def extract_json_object(text: str) -> dict[str, Any]:
    from .json_output import extract_object
    return extract_object(text)


def current_message_map(rounds: list[dict[str, Any]]) -> dict[str, str]:
    return {
        message["ref"]: message["content"]
        for item in rounds
        for message in (item["user"], item["assistant"])
    }


def normalize_anchor(text: str) -> str:
    """忽略空白、Markdown 符号和纯标点差异，保留文字与数字。"""

    return "".join(
        character
        for character in text
        if not character.isspace()
        and character not in "*_`~"
        and not unicodedata.category(character).startswith("P")
    )


def quote_in_text(quote: str, text: str) -> bool:
    normalized_quote = normalize_anchor(quote)
    return bool(normalized_quote) and normalized_quote in normalize_anchor(text)


def anchor_span(text: str, quote: str) -> tuple[int, int] | None:
    """返回宽松匹配的原文位置，用于由脚本裁切消息内边界。"""

    normalized_quote = normalize_anchor(quote)
    if not normalized_quote:
        return None
    kept: list[tuple[str, int]] = []
    for index, character in enumerate(text):
        if (
            not character.isspace()
            and character not in "*_`~"
            and not unicodedata.category(character).startswith("P")
        ):
            kept.append((character, index))
    normalized_text = "".join(character for character, _ in kept)
    start = normalized_text.find(normalized_quote)
    if start < 0:
        return None
    end = start + len(normalized_quote) - 1
    return kept[start][1], kept[end][1] + 1


def bounded_segments_for_content(
    boundary_plan: dict[str, Any], rounds: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """把每个分段的原文真正裁到起止线内，避免只靠 AI 守边界。"""

    messages = {message["ref"]: message for message in batch_messages(rounds)}
    bounded: list[dict[str, Any]] = []
    for raw_segment in boundary_plan.get("segments") or []:
        segment = deepcopy(raw_segment)
        refs = list(segment.get("source_refs") or [])
        start_anchors = {
            str(anchor.get("source_ref", "")): str(anchor.get("start_quote", ""))
            for anchor in segment.get("start_anchors") or []
            if isinstance(anchor, dict)
        }
        end_anchors = {
            str(anchor.get("source_ref", "")): str(anchor.get("before_quote", ""))
            for anchor in segment.get("end_before_anchors") or []
            if isinstance(anchor, dict)
        }
        assigned: list[dict[str, Any]] = []
        for ref in refs:
            if ref not in messages:
                continue
            message = messages[ref]
            text = str(message.get("content", ""))
            start_index = 0
            if ref in start_anchors:
                span = anchor_span(text, start_anchors[ref])
                if span is not None:
                    start_index = span[0]
            end_index = len(text)
            if ref in end_anchors:
                span = anchor_span(text, end_anchors[ref])
                if span is not None:
                    end_index = span[0]
            content = text[start_index:end_index].strip()
            if content:
                assigned.append(
                    {
                        "ref": ref,
                        "role": message["role"],
                        "speaker": message["speaker"],
                        "content": content,
                    }
                )
        segment["assigned_messages"] = assigned
        bounded.append(segment)
    return bounded


def anchor_signatures(anchors: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (
            str(anchor.get("source_ref", "")),
            normalize_anchor(str(anchor.get("start_quote", ""))),
        )
        for anchor in anchors
        if isinstance(anchor, dict)
    ]


def equivalent_anchor_signatures(
    expected: list[tuple[str, str]], actual: list[tuple[str, str]]
) -> bool:
    """同一消息、同一起点的长短原文引句等价，不把引用长度误当边界偏移。"""

    if len(expected) != len(actual):
        return False
    for (expected_ref, expected_quote), (actual_ref, actual_quote) in zip(
        expected, actual
    ):
        if expected_ref != actual_ref:
            return False
        if not expected_quote or not actual_quote:
            return False
        if not (
            expected_quote.startswith(actual_quote)
            or actual_quote.startswith(expected_quote)
        ):
            return False
    return True


def evaluate_state_against_gold(
    state: dict[str, Any], gold: dict[str, Any], processed_round_end: int
) -> dict[str, Any]:
    """用人工边界样例做离线对照，不在运行时再调用裁判模型。"""

    required_round_end = int(gold.get("source", {}).get("round_end", 0))
    if processed_round_end < required_round_end:
        return {
            "status": "not_due",
            "processed_round_end": processed_round_end,
            "required_round_end": required_round_end,
            "passed": None,
            "mismatches": [],
        }

    expected_events = list(gold.get("expected_events") or [])
    actual_events = list(state.get("events") or [])
    mismatches: list[str] = []
    if len(actual_events) != len(expected_events):
        mismatches.append(
            f"Event 数量应为 {len(expected_events)}，实际为 {len(actual_events)}"
        )

    for index, (expected, actual) in enumerate(
        zip(expected_events, actual_events), start=1
    ):
        expected_status = str(expected.get("status", ""))
        actual_status = str(actual.get("status", ""))
        if actual_status != expected_status:
            mismatches.append(
                f"第 {index} 项状态应为 {expected_status}，实际为 {actual_status}"
            )
        source_slices = list(actual.get("source_slices") or [])
        actual_anchors: list[dict[str, Any]] = []
        for source_slice in source_slices:
            if isinstance(source_slice, dict) and source_slice.get("start_anchors"):
                actual_anchors = list(source_slice["start_anchors"])
                break
        expected_signatures = anchor_signatures(expected.get("start_anchors") or [])
        actual_signatures = anchor_signatures(actual_anchors)
        if not equivalent_anchor_signatures(expected_signatures, actual_signatures):
            mismatches.append(
                f"第 {index} 项起点不符：应为 {expected_signatures}，实际为 {actual_signatures}"
            )

    return {
        "status": "passed" if not mismatches else "failed",
        "processed_round_end": processed_round_end,
        "required_round_end": required_round_end,
        "passed": not mismatches,
        "expected_event_count": len(expected_events),
        "actual_event_count": len(actual_events),
        "mismatches": mismatches,
    }


def load_gold_fixture(path: Path, chat_jsonl: Path) -> dict[str, Any]:
    gold = json.loads(path.read_text(encoding="utf-8"))
    expected_hash = str(gold.get("source", {}).get("sha256", "")).lower()
    if expected_hash:
        digest = hashlib.sha256()
        with chat_jsonl.open("rb") as source_file:
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)
        actual_hash = digest.hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError("语义回归样例与当前 JSONL 的 SHA-256 不一致")
    return gold


def slot_sort_key(slot: str) -> tuple[int, int]:
    if slot == "pending_tail":
        return (0, 0)
    if slot == "forming_existing":
        return (1, 0)
    match = re.fullmatch(r"new_(\d+)", slot)
    if match:
        return (2, int(match.group(1)))
    return (99, 0)


def normalize_boundary_plan(
    plan: dict[str, Any],
    state: dict[str, Any],
    rounds: list[dict[str, Any]],
) -> dict[str, Any]:
    """从 Event 分段开头派生边界和上一段结束线。"""

    normalized = deepcopy(plan)
    segments = normalized.get("segments") or []
    used_slots = [
        str(segment.get("slot", ""))
        for segment in segments
        if isinstance(segment, dict)
    ]
    new_slots = sorted(
        {slot for slot in used_slots if re.fullmatch(r"new_\d+", slot)},
        key=slot_sort_key,
    )
    boundary_slots = (
        new_slots[1:]
        if normalized.get("old_forming_disposition") == "absent"
        else new_slots
    )
    for segment in segments:
        if isinstance(segment, dict):
            segment.pop("end_before_anchors", None)

    # 模型只标“下一个 Event 从哪里开始”；脚本把同一位置同步写成
    # 上一段的排他性结束线，防止内容阶段读过同一条消息内的切点。
    for current, following in zip(segments, segments[1:]):
        if not isinstance(current, dict) or not isinstance(following, dict):
            continue
        if current.get("slot") == following.get("slot"):
            continue
        current_refs = set(current.get("source_refs") or [])
        shared_anchors = [
            {
                "source_ref": str(anchor.get("source_ref", "")),
                "before_quote": str(anchor.get("start_quote", "")).strip(),
            }
            for anchor in following.get("start_anchors") or []
            if isinstance(anchor, dict)
            and str(anchor.get("source_ref", "")) in current_refs
        ]
        if shared_anchors:
            current["end_before_anchors"] = shared_anchors

    boundaries: list[dict[str, Any]] = []
    for slot in boundary_slots:
        first_segment = next(
            (segment for segment in segments if segment.get("slot") == slot), None
        )
        if not isinstance(first_segment, dict):
            continue
        boundaries.append(
            {
                "slot": slot,
                "before_anchors": deepcopy(first_segment.get("start_anchors") or []),
                "signal": f"由 {slot} 的分段开头派生",
            }
        )
    normalized["boundaries"] = boundaries
    return normalized


def validate_boundary_plan(
    plan: dict[str, Any], state: dict[str, Any], rounds: list[dict[str, Any]]
) -> list[str]:
    errors: list[str] = []
    disposition = plan.get("old_forming_disposition")
    if disposition not in ALLOWED_DISPOSITIONS:
        errors.append("old_forming_disposition 非法")

    pending = event_by_status(state, "pending_finalization")
    forming = event_by_status(state, "forming")
    if not state["events"] and disposition != "absent":
        errors.append("首次整理必须使用 absent")
    if state["events"] and disposition == "absent":
        errors.append("已有 Event 时不能使用 absent")
    if disposition == "merge_into_pending" and not (pending and forming):
        errors.append("merge_into_pending 需要同时存在待定稿和生成中")
    if disposition == "keep_distinct" and forming is None:
        errors.append("keep_distinct 需要已有生成中 Event")

    source_roles = plan.get("source_roles")
    if isinstance(source_roles, list) and source_roles:
        allowed_roles = {str(role) for role in source_roles}
        message_map = {
            str(message["ref"]): str(message["content"])
            for message in batch_messages(rounds)
            if str(message.get("role", "")) in allowed_roles
        }
    else:
        # 旧记录没有 source_roles，仍按完整 user + assistant 来源验证。
        message_map = current_message_map(rounds)
    valid_refs = set(message_map)
    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append("segments 必须是非空数组")
        segments = []

    used_refs: set[str] = set()
    used_slots: list[str] = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"segments[{index}] 不是对象")
            continue
        slot = str(segment.get("slot", ""))
        if slot in OLD_SLOTS or re.fullmatch(r"new_\d+", slot):
            used_slots.append(slot)
        else:
            errors.append(f"segments[{index}].slot 非法：{slot}")
        refs = segment.get("source_refs") or []
        if not refs:
            errors.append(f"segments[{index}] 没有 source_refs")
        for ref in refs:
            if ref not in valid_refs:
                errors.append(f"segments[{index}] 引用了非本批消息：{ref}")
            else:
                used_refs.add(ref)
        start_anchors = segment.get("start_anchors")
        if not isinstance(start_anchors, list) or not start_anchors:
            errors.append(f"segments[{index}].start_anchors 必须是非空数组")
            start_anchors = []
        anchor_refs: list[str] = []
        for anchor_index, anchor in enumerate(start_anchors):
            if not isinstance(anchor, dict):
                errors.append(
                    f"segments[{index}].start_anchors[{anchor_index}] 不是对象"
                )
                continue
            ref = str(anchor.get("source_ref", ""))
            quote = str(anchor.get("start_quote", "")).strip()
            anchor_refs.append(ref)
            if ref not in refs:
                errors.append(
                    f"segments[{index}].start_anchors[{anchor_index}] 没有指向本段来源"
                )
            elif not quote or not quote_in_text(quote, message_map.get(ref, "")):
                errors.append(
                    f"segments[{index}].start_anchors[{anchor_index}] 不在对应原文中"
                )
        if len(anchor_refs) != len(set(anchor_refs)):
            errors.append(f"segments[{index}].start_anchors 存在重复消息")
        if refs and anchor_refs and anchor_refs[0] != refs[0]:
            errors.append(f"segments[{index}] 第一个 start_anchor 必须对齐第一条来源")

        for anchor_index, anchor in enumerate(segment.get("end_before_anchors") or []):
            if not isinstance(anchor, dict):
                errors.append(
                    f"segments[{index}].end_before_anchors[{anchor_index}] 不是对象"
                )
                continue
            ref = str(anchor.get("source_ref", ""))
            quote = str(anchor.get("before_quote", "")).strip()
            if ref not in refs or not quote_in_text(quote, message_map.get(ref, "")):
                errors.append(
                    f"segments[{index}].end_before_anchors[{anchor_index}] 不在对应原文中"
                )

    missing_refs = valid_refs - used_refs
    if missing_refs:
        errors.append("存在未归属消息：" + ", ".join(sorted(missing_refs)))
    if used_slots != sorted(used_slots, key=slot_sort_key):
        errors.append("segment slot 顺序不符合时间顺序")
    if len(used_slots) != len(set(used_slots)):
        errors.append("每个 slot 只能有一个 segment")
    for index, (current, following) in enumerate(zip(segments, segments[1:])):
        if not isinstance(current, dict) or not isinstance(following, dict):
            continue
        shared_refs = set(current.get("source_refs") or []) & set(
            following.get("source_refs") or []
        )
        following_anchor_refs = {
            str(anchor.get("source_ref", ""))
            for anchor in following.get("start_anchors") or []
            if isinstance(anchor, dict)
        }
        missing_shared_anchors = shared_refs - following_anchor_refs
        if missing_shared_anchors:
            errors.append(
                f"segments[{index + 1}] 缺少共用消息的独立起点："
                + ", ".join(sorted(missing_shared_anchors))
            )
    if disposition == "merge_into_pending":
        if "forming_existing" in used_slots:
            errors.append("merge_into_pending 时不能使用 forming_existing")
        if not any(slot.startswith("new_") for slot in used_slots):
            errors.append("merge_into_pending 后必须在新增内容中形成新 Event")
    elif disposition == "keep_distinct" and "pending_tail" in used_slots:
        errors.append("keep_distinct 时不能使用 pending_tail")
    elif disposition == "absent" and any(slot in OLD_SLOTS for slot in used_slots):
        errors.append("absent 时不能使用旧 Event slot")

    new_numbers = sorted(
        {int(slot.split("_", 1)[1]) for slot in used_slots if slot.startswith("new_")}
    )
    if new_numbers and new_numbers != list(range(1, max(new_numbers) + 1)):
        errors.append("new_N slot 必须从 new_1 连续编号")

    boundaries = plan.get("boundaries") or []
    for index, boundary in enumerate(boundaries):
        if not isinstance(boundary, dict):
            errors.append(f"boundaries[{index}] 不是对象")
            continue
        before_anchors = boundary.get("before_anchors") or []
        for anchor_index, anchor in enumerate(before_anchors):
            ref = anchor.get("source_ref") if isinstance(anchor, dict) else None
            quote = (
                str(anchor.get("start_quote", ""))
                if isinstance(anchor, dict)
                else ""
            )
            if ref not in valid_refs:
                errors.append(
                    f"boundaries[{index}].before_anchors[{anchor_index}] 不在本批新增消息中"
                )
            elif not quote_in_text(quote, message_map[ref]):
                errors.append(
                    f"boundaries[{index}].before_anchors[{anchor_index}] 不在原文中"
                )

    new_slots_in_order = sorted(
        {slot for slot in used_slots if slot.startswith("new_")}, key=slot_sort_key
    )
    boundary_slots = (
        new_slots_in_order[1:] if disposition == "absent" else new_slots_in_order
    )
    if len(boundaries) != len(boundary_slots):
        errors.append(
            f"边界数量 {len(boundaries)} 与新 Event 开头数量 {len(boundary_slots)} 不一致"
        )
    else:
        for index, (boundary, slot) in enumerate(zip(boundaries, boundary_slots)):
            first_segment = next(
                (segment for segment in segments if segment.get("slot") == slot), None
            )
            if not isinstance(boundary, dict) or not isinstance(first_segment, dict):
                continue
            boundary_anchors = [
                (
                    str(anchor.get("source_ref", "")),
                    normalize_anchor(str(anchor.get("start_quote", ""))),
                )
                for anchor in boundary.get("before_anchors") or []
                if isinstance(anchor, dict)
            ]
            segment_anchors = [
                (
                    str(anchor.get("source_ref", "")),
                    normalize_anchor(str(anchor.get("start_quote", ""))),
                )
                for anchor in first_segment.get("start_anchors") or []
                if isinstance(anchor, dict)
            ]
            if boundary.get("slot") != slot or boundary_anchors != segment_anchors:
                errors.append(f"boundaries[{index}] 没有对齐 {slot} 的 start_anchors")

    return errors


def validate_content_plan(
    content_plan: dict[str, Any],
    boundary_plan: dict[str, Any],
    rounds: list[dict[str, Any]],
    state: dict[str, Any] | None = None,
    slot_event_ids: dict[str, str] | None = None,
) -> tuple[list[str], list[str]]:
    """检查内容阶段的机器结构；语义软问题只记录警告，不触发重写。"""

    errors: list[str] = []
    warnings: list[str] = []
    updates = content_plan.get("event_updates")
    if not isinstance(updates, list):
        return ["event_updates 必须是数组"], warnings

    expected_slots = list(
        dict.fromkeys(segment["slot"] for segment in boundary_plan.get("segments") or [])
    )
    update_slots = [
        str(update.get("slot", "")) for update in updates if isinstance(update, dict)
    ]
    if len(update_slots) != len(updates):
        errors.append("event_updates 中存在非对象条目")
    if len(update_slots) != len(set(update_slots)):
        errors.append("event_updates 中存在重复 slot")
    missing = [slot for slot in expected_slots if slot not in update_slots]
    extra = [slot for slot in update_slots if slot not in expected_slots]
    if missing:
        errors.append("缺少 Event 内容：" + ", ".join(missing))
    if extra:
        errors.append("出现未分配的 Event 内容：" + ", ".join(extra))

    assigned_text: dict[str, dict[str, str]] = {}
    for segment in bounded_segments_for_content(boundary_plan, rounds):
        slot_text = assigned_text.setdefault(str(segment.get("slot", "")), {})
        for message in segment.get("assigned_messages") or []:
            ref = str(message.get("ref", ""))
            content = str(message.get("content", ""))
            slot_text[ref] = "\n".join(
                part for part in (slot_text.get(ref, ""), content) if part
            )
    segment_refs: dict[str, set[str]] = {}
    for segment in boundary_plan.get("segments") or []:
        segment_refs.setdefault(segment["slot"], set()).update(segment.get("source_refs") or [])

    list_fields = (
        "participants_add",
        "participants_remove",
        "locations_add",
        "locations_remove",
        "unresolved_add",
        "unresolved_resolve",
        "key_details_keep",
        "new_key_details",
    )
    for index, update in enumerate(updates):
        if not isinstance(update, dict):
            continue
        slot = str(update.get("slot", ""))
        for field in ("description", "story_summary"):
            if not isinstance(update.get(field), str) or not update[field].strip():
                errors.append(f"event_updates[{index}].{field} 不能为空")
        summary = update.get("story_summary")
        if isinstance(summary, str) and len(summary) > 500:
            warnings.append(f"{slot} 的 story_summary 超过 500 字")
        for field in list_fields:
            if not isinstance(update.get(field), list):
                errors.append(f"event_updates[{index}].{field} 必须是数组")

        kept_ids = update.get("key_details_keep") or []
        existing_detail_ids: set[str] = set()
        if state is not None and slot_event_ids is not None and slot in slot_event_ids:
            event_id = slot_event_ids[slot]
            event = next(
                (item for item in state.get("events", []) if item.get("id") == event_id),
                None,
            )
            if event is not None:
                existing_detail_ids = {
                    str(detail.get("id"))
                    for detail in event.get("key_details", [])
                    if detail.get("id")
                }
        invalid_kept_ids = [
            str(detail_id)
            for detail_id in kept_ids
            if str(detail_id) not in existing_detail_ids
        ]
        if invalid_kept_ids:
            errors.append(
                f"{slot} 保留了不属于该 Event 的关键细节 id："
                + ", ".join(invalid_kept_ids)
            )

        details = update.get("new_key_details") or []
        if len(kept_ids) + len(details) > 5:
            warnings.append(f"{slot} 的关键细节总数超过 5 条")
        for detail_index, detail in enumerate(details):
            if not isinstance(detail, dict):
                errors.append(
                    f"event_updates[{index}].new_key_details[{detail_index}] 不是对象"
                )
                continue
            if detail.get("kind") not in {"statement", "action"}:
                errors.append(
                    f"event_updates[{index}].new_key_details[{detail_index}].kind 非法"
                )
            content = str(detail.get("content", "")).strip()
            refs = detail.get("source_refs")
            if not content:
                errors.append(
                    f"event_updates[{index}].new_key_details[{detail_index}].content 不能为空"
                )
            if not isinstance(refs, list) or not refs:
                errors.append(
                    f"event_updates[{index}].new_key_details[{detail_index}] 缺少来源"
                )
                continue
            invalid_refs = [ref for ref in refs if ref not in segment_refs.get(slot, set())]
            if invalid_refs:
                errors.append(
                    f"event_updates[{index}].new_key_details[{detail_index}] 来源不属于 {slot}"
                )
                continue
            if (
                content
                and detail.get("fidelity") != "paraphrase"
                and not any(
                quote_in_text(content, assigned_text.get(slot, {}).get(ref, ""))
                for ref in refs
                )
            ):
                warnings.append(
                    f"{slot} 的关键细节 {detail_index + 1} 不是可直接核对的连续原文"
                )
    return errors, warnings


def new_event(state: dict[str, Any]) -> dict[str, Any]:
    number = state["next_event_number"]
    state["next_event_number"] += 1
    event = {
        "id": f"event_probe_{number:03d}",
        "status": "forming",
        "title": "",
        "description": "",
        "story_summary": "",
        "participants": [],
        "locations": [],
        "unresolved": [],
        "source_refs": [],
        "source_slices": [],
        "key_details": [],
    }
    state["events"].append(event)
    return event


def unique_extend(target: list[Any], values: Iterable[Any]) -> None:
    for value in values:
        if value not in target:
            target.append(value)


def merge_forming_into_pending(
    state: dict[str, Any], operations: list[str]
) -> dict[str, Any]:
    pending = event_by_status(state, "pending_finalization")
    forming = event_by_status(state, "forming")
    if pending is None or forming is None:
        raise ValueError("缺少可合并的待定稿或生成中 Event")
    pending["_merged_forming_content"] = {
        "title": forming.get("title", ""),
        "description": forming.get("description", ""),
        "story_summary": forming.get("story_summary", ""),
    }

    def merge_text(left: Any, right: Any, separator: str) -> str:
        left_text = str(left or "").strip()
        right_text = str(right or "").strip()
        if not left_text:
            return right_text
        if not right_text:
            return left_text
        left_compact = re.sub(r"[\W_]+", "", left_text, flags=re.UNICODE)
        right_compact = re.sub(r"[\W_]+", "", right_text, flags=re.UNICODE)
        if right_compact and right_compact in left_compact:
            return left_text
        if left_compact and left_compact in right_compact:
            return right_text
        if separator == "\n":
            return left_text.rstrip() + separator + right_text
        return left_text.rstrip("；。\n ") + separator + right_text

    # 合并是固定状态操作，不能把已经生成的事实留在过程字段里等模型重写。
    # Summary 完整串接；Description 只串接两段模型已写好的短说明，不重新概括。
    pending["story_summary"] = merge_text(
        pending.get("story_summary"), forming.get("story_summary"), "\n"
    )
    pending["description"] = merge_text(
        pending.get("description"), forming.get("description"), "；"
    )
    unique_extend(pending["source_refs"], forming.get("source_refs", []))
    unique_extend(pending["participants"], forming.get("participants", []))
    unique_extend(pending["locations"], forming.get("locations", []))
    unique_extend(pending["unresolved"], forming.get("unresolved", []))
    unique_extend(pending.setdefault("source_slices", []), forming.get("source_slices", []))
    unique_extend(pending["key_details"], forming.get("key_details", []))
    moved_memories = 0
    for memory in state["memories"]:
        if memory["event_id"] == forming["id"]:
            memory["event_id"] = pending["id"]
            moved_memories += 1
    state["events"].remove(forming)
    operations.append(
        f"{forming['id']} 整体并入 {pending['id']}；固定脚本换绑 {moved_memories} 条 Memory"
    )
    return pending


def apply_event_update(
    state: dict[str, Any], event: dict[str, Any], update: dict[str, Any]
) -> None:
    for field in ("description", "story_summary"):
        value = update.get(field)
        if isinstance(value, str) and value.strip():
            event[field] = value.strip()
    for field, add_key, remove_key in (
        ("participants", "participants_add", "participants_remove"),
        ("locations", "locations_add", "locations_remove"),
        ("unresolved", "unresolved_add", "unresolved_resolve"),
    ):
        additions = update.get(add_key) or []
        removals = {str(value) for value in (update.get(remove_key) or [])}
        if isinstance(additions, list):
            unique_extend(event[field], (str(value) for value in additions))
        if removals:
            event[field] = [value for value in event[field] if value not in removals]
    existing_details = {
        str(detail.get("id")): detail
        for detail in event.get("key_details", [])
        if detail.get("id")
    }
    selected_details = [
        deepcopy(existing_details[str(detail_id)])
        for detail_id in update.get("key_details_keep") or []
        if str(detail_id) in existing_details
    ]
    for detail in update.get("new_key_details") or []:
        if not isinstance(detail, dict):
            continue
        signature = (detail.get("content"), tuple(detail.get("source_refs") or []))
        matching_old = next(
            (
                old
                for old in event.get("key_details", [])
                if (old.get("content"), tuple(old.get("source_refs") or []))
                == signature
            ),
            None,
        )
        if matching_old is not None:
            if not any(
                old.get("id") == matching_old.get("id") for old in selected_details
            ):
                selected_details.append(deepcopy(matching_old))
            continue
        if any(
            (old.get("content"), tuple(old.get("source_refs") or [])) == signature
            for old in selected_details
        ):
            continue
        number = state["next_detail_number"]
        state["next_detail_number"] += 1
        detail_record = {
            "id": f"detail_probe_{number:03d}",
            "kind": detail.get("kind", "scene"),
            "content": str(detail.get("content", "")).strip(),
            "fidelity": str(detail.get("fidelity", "paraphrase")),
            "actor": str(detail.get("actor", "")).strip(),
            "source_refs": list(detail.get("source_refs") or []),
        }
        if detail.get("source_unit_refs"):
            detail_record["source_unit_refs"] = list(detail["source_unit_refs"])
        selected_details.append(detail_record)
    event["key_details"] = selected_details


def recalculate_statuses(state: dict[str, Any]) -> None:
    events = state["events"]
    if not events:
        return
    for event in events:
        event["status"] = "finalized"
    if len(events) == 1:
        events[-1]["status"] = "forming"
    else:
        events[-2]["status"] = "pending_finalization"
        events[-1]["status"] = "forming"


def apply_boundary_plan(
    state: dict[str, Any], plan: dict[str, Any]
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    next_state = deepcopy(state)
    operations: list[str] = []
    disposition = plan["old_forming_disposition"]
    slot_map: dict[str, dict[str, Any]] = {}

    if disposition == "merge_into_pending":
        slot_map["pending_tail"] = merge_forming_into_pending(next_state, operations)
    elif disposition == "keep_distinct":
        forming = event_by_status(next_state, "forming")
        if forming is None:
            raise ValueError("keep_distinct 缺少生成中 Event")
        slot_map["forming_existing"] = forming

    used_slots = list(
        dict.fromkeys(segment["slot"] for segment in plan.get("segments") or [])
    )
    for slot in sorted(
        (slot for slot in used_slots if slot.startswith("new_")), key=slot_sort_key
    ):
        slot_map[slot] = new_event(next_state)
        operations.append(f"创建 {slot} → {slot_map[slot]['id']}")

    for segment in plan.get("segments") or []:
        event = slot_map[segment["slot"]]
        unique_extend(event["source_refs"], segment.get("source_refs") or [])
        event.setdefault("source_slices", []).append(
            {
                "source_refs": list(segment.get("source_refs") or []),
                "start_anchors": deepcopy(segment.get("start_anchors") or []),
                "end_before_anchors": deepcopy(
                    segment.get("end_before_anchors") or []
                ),
            }
        )

    recalculate_statuses(next_state)
    operations.append(
        "固定脚本重算状态："
        + "，".join(f"{event['id']}={event['status']}" for event in next_state["events"])
    )
    slot_event_ids = {slot: event["id"] for slot, event in slot_map.items()}
    return next_state, operations, slot_event_ids


def apply_content_plan(
    state: dict[str, Any],
    content_plan: dict[str, Any],
    slot_event_ids: dict[str, str],
) -> dict[str, Any]:
    next_state = deepcopy(state)
    events_by_id = {event["id"]: event for event in next_state["events"]}
    for update in content_plan.get("event_updates") or []:
        event_id = slot_event_ids[update["slot"]]
        apply_event_update(next_state, events_by_id[event_id], update)
        events_by_id[event_id].pop("_merged_forming_content", None)
    return next_state


def run_model_stage(
    *,
    stage: str,
    batch_number: int,
    output_dir: Path,
    endpoint: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: float,
    max_tokens: int,
    validator: Callable[[dict[str, Any]], tuple[list[str], list[str]]],
    normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """调用一个语义阶段；只对机器结构错误要求同一模型重写一次。"""

    candidate_prompt = user_prompt
    candidate_attempts: list[dict[str, Any]] = []
    transport_failures: list[dict[str, Any]] = []
    last_plan: dict[str, Any] | None = None
    last_raw = ""
    last_api: dict[str, Any] = {}
    last_errors: list[str] = []
    last_warnings: list[str] = []

    (output_dir / f"batch_{batch_number:02d}_{stage}_system_prompt.txt").write_text(
        system_prompt, encoding="utf-8"
    )

    for candidate_attempt in range(1, 3):
        (output_dir / f"batch_{batch_number:02d}_{stage}_candidate_{candidate_attempt}_user_prompt.txt").write_text(
            candidate_prompt, encoding="utf-8"
        )
        stage_raw = ""
        stage_api: dict[str, Any] = {}
        stage_plan: dict[str, Any] | None = None
        for transport_attempt in range(1, 4):
            try:
                stage_raw, stage_api = call_chat_completion(
                    endpoint,
                    api_key,
                    model,
                    system_prompt,
                    candidate_prompt,
                    timeout,
                    max_tokens,
                )
                (output_dir / f"batch_{batch_number:02d}_{stage}_candidate_{candidate_attempt}_transport_{transport_attempt}_raw.txt").write_text(
                    stage_raw, encoding="utf-8"
                )
                stage_plan = extract_json_object(stage_raw)
                if normalizer is not None:
                    stage_plan = normalizer(stage_plan)
                break
            except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                transport_failures.append(
                    {
                        "candidate_attempt": candidate_attempt,
                        "transport_attempt": transport_attempt,
                        "error": str(exc),
                    }
                )
                if transport_attempt == 3:
                    return {
                        "stage": stage,
                        "ok": False,
                        "fatal_error": str(exc),
                        "api": stage_api,
                        "candidate_attempts": candidate_attempts,
                        "transport_failures": transport_failures,
                        "plan": stage_plan,
                        "validation_errors": [str(exc)],
                        "warnings": [],
                        "raw": stage_raw,
                    }
                print(
                    f"  {stage} 第 {transport_attempt} 次传输失败，准备重试：{exc}",
                    file=sys.stderr,
                    flush=True,
                )
                time.sleep(2**transport_attempt)

        assert stage_plan is not None
        errors, warnings = validator(stage_plan)
        candidate_attempts.append(
            {
                "attempt": candidate_attempt,
                "api": stage_api,
                "validation_errors": errors,
                "warnings": warnings,
            }
        )
        (output_dir / f"batch_{batch_number:02d}_{stage}_attempt_{candidate_attempt}_raw.txt").write_text(
            stage_raw, encoding="utf-8"
        )
        last_plan = stage_plan
        last_raw = stage_raw
        last_api = stage_api
        last_errors = errors
        last_warnings = warnings
        if not errors:
            return {
                "stage": stage,
                "ok": True,
                "fatal_error": None,
                "api": stage_api,
                "candidate_attempts": candidate_attempts,
                "transport_failures": transport_failures,
                "plan": stage_plan,
                "validation_errors": [],
                "warnings": warnings,
                "raw": stage_raw,
            }
        if candidate_attempt == 1:
            print(
                f"  {stage} 候选未通过机器结构检查，要求同一模型修正一次",
                flush=True,
            )
            candidate_prompt = (
                user_prompt
                + "\n\n上一次候选未通过机器结构检查。请只修正下列结构错误，重新输出完整 JSON，"
                "不要解释，也不要改变叙事事实。\n- "
                + "\n- ".join(errors)
                + "\n上一次候选：\n"
                + json.dumps(stage_plan, ensure_ascii=False)
            )

    return {
        "stage": stage,
        "ok": False,
        "fatal_error": "两次候选均未通过机器结构检查",
        "api": last_api,
        "candidate_attempts": candidate_attempts,
        "transport_failures": transport_failures,
        "plan": last_plan,
        "validation_errors": last_errors,
        "warnings": last_warnings,
        "raw": last_raw,
    }


def result_directory(base: Path | None) -> Path:
    if base is not None:
        return base
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path(__file__).resolve().parents[3] / ".event_probe_runs" / stamp


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def render_report(run: dict[str, Any]) -> str:
    lines = [
        "# Event 分阶段语义测试报告",
        "",
        f"- 模型：`{run['model']}`",
        f"- 输入：`{run['input_file']}`",
        f"- 批次：{len(run['batches'])}；每批 {run['rounds_per_batch']} 轮",
        "- 流程：边界判定 → 固定脚本切分 → Event 内容生成",
        "- 密钥：仅从运行时环境读取，未写入报告或结果文件",
        "",
    ]
    for batch in run["batches"]:
        boundary = batch["boundary"]
        content = batch.get("content")
        plan = boundary.get("plan") or {}
        lines.extend(
            [
                f"## 批次 {batch['batch_number']}（第 {batch['round_start']}—{batch['round_end']} 轮）",
                "",
                f"- 清理后输入字符：{batch['input_chars']}",
                f"- 边界阶段：{'通过' if boundary.get('ok') else '失败'}；决策 `{plan.get('old_forming_disposition')}`",
                f"- 边界理由：{plan.get('decision_reason', '')}",
                f"- 边界候选次数：{len(boundary.get('candidate_attempts', []))}",
                f"- 边界传输重试：{len(boundary.get('transport_failures', []))}",
            ]
        )
        boundaries = plan.get("boundaries") or []
        if boundaries:
            lines.append("- 边界：")
            for boundary in boundaries:
                anchor_text = "；".join(
                    f"`{anchor.get('source_ref')}` 的“{anchor.get('start_quote', '')}”"
                    for anchor in boundary.get("before_anchors") or []
                    if isinstance(anchor, dict)
                )
                lines.append(
                    f"  - {boundary.get('slot', '')}：{anchor_text}；{boundary.get('signal', '')}"
                )
        else:
            lines.append("- 边界：本批未提出新边界")
        if content is None:
            lines.append("- 内容阶段：未执行")
        else:
            lines.append(
                f"- 内容阶段：{'通过' if content.get('ok') else '失败'}；候选次数 {len(content.get('candidate_attempts', []))}；传输重试 {len(content.get('transport_failures', []))}"
            )
            for warning in content.get("warnings") or []:
                lines.append(f"  - 软性警告：{warning}")
        lines.append("- 固定脚本操作：")
        for operation in batch.get("operations") or []:
            lines.append(f"  - {operation}")
        if batch.get("failure"):
            lines.append(f"- 本批未提交：{batch['failure']}")
        lines.extend(["", "| 状态 | Event | Description |", "|---|---|---|"])
        for event in batch["state_after"]["events"]:
            lines.append(
                f"| {event['status']} | {event['id']} | {event.get('description', '').replace('|', '｜')} |"
            )
        lines.append("")

    final_state = run["final_state"]
    lines.extend(
        [
            "## 最终状态",
            "",
            "| 状态 | Event | Description |",
            "|---|---|---|",
        ]
    )
    for event in final_state["events"]:
        lines.append(
            f"| {event['status']} | {event['id']} | {event.get('description', '').replace('|', '｜')} |"
        )
    semantic = run.get("semantic_evaluation")
    if semantic:
        lines.extend(
            [
                "",
                "## 人工边界回归对照",
                "",
                f"- 状态：`{semantic.get('status')}`",
                f"- 预期 Event：{semantic.get('expected_event_count', '尚未到期')}；实际 Event：{semantic.get('actual_event_count', '尚未到期')}",
            ]
        )
        for mismatch in semantic.get("mismatches") or []:
            lines.append(f"  - {mismatch}")
    return "\n".join(lines) + "\n"


def load_entity_context(path: Path | None) -> Any:
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def stage_record(stage: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in stage.items() if key != "raw"}


def render_full_record(run: dict[str, Any], output_dir: Path) -> str:
    """汇总可公开核对的完整提示词、正式回复和机器结果。"""

    lines = [render_report(run).rstrip(), "", "---", "", "# 完整原始记录", ""]
    lines.append(
        "以下按实际调用顺序保存完整提示词与模型正式 content。传输元数据和机器结果保留在各批 JSON；模型内部 reasoning_content 不属于正式回复，不写入记录。"
    )
    lines.append("")
    for batch in run["batches"]:
        number = batch["batch_number"]
        lines.extend(
            [
                f"## 批次 {number}（第 {batch['round_start']}—{batch['round_end']} 轮）",
                "",
            ]
        )
        for stage, stage_title in (("boundary", "边界阶段"), ("content", "内容阶段")):
            system_path = output_dir / f"batch_{number:02d}_{stage}_system_prompt.txt"
            if not system_path.exists():
                continue
            lines.extend([f"### {stage_title}：系统提示词", "", "`````text"])
            lines.append(system_path.read_text(encoding="utf-8"))
            lines.extend(["`````", ""])
            user_paths = sorted(
                output_dir.glob(f"batch_{number:02d}_{stage}_candidate_*_user_prompt.txt")
            )
            for index, user_path in enumerate(user_paths, 1):
                lines.extend(
                    [f"### {stage_title}：第 {index} 次候选输入提示词", "", "`````text"]
                )
                lines.append(user_path.read_text(encoding="utf-8"))
                lines.extend(["`````", ""])
            raw_paths = sorted(
                output_dir.glob(
                    f"batch_{number:02d}_{stage}_candidate_*_transport_*_raw.txt"
                )
            )
            for index, raw_path in enumerate(raw_paths, 1):
                lines.extend(
                    [f"### {stage_title}：第 {index} 份模型原回复", "", "`````json"]
                )
                lines.append(raw_path.read_text(encoding="utf-8"))
                lines.extend(["`````", ""])
        lines.extend(["### 本批机器结果", "", "`````json"])
        lines.append(json.dumps(batch, ensure_ascii=False, indent=2))
        lines.extend(["`````", ""])
    return "\n".join(lines).rstrip() + "\n"


def persist_run(
    output_dir: Path,
    run: dict[str, Any],
    api_key: str,
    full_record_output: Path | None = None,
) -> None:
    serialized = json.dumps(run, ensure_ascii=False)
    if api_key in serialized:
        raise RuntimeError("安全检查失败：结果中意外出现 API 密钥")
    write_json(output_dir / "checkpoint.json", run)
    write_json(output_dir / "run.json", run)
    (output_dir / "report.md").write_text(render_report(run), encoding="utf-8")
    full_record = render_full_record(run, output_dir)
    if api_key in full_record:
        raise RuntimeError("安全检查失败：完整记录中意外出现 API 密钥")
    (output_dir / "full_record.md").write_text(full_record, encoding="utf-8")
    if full_record_output is not None:
        full_record_output.parent.mkdir(parents=True, exist_ok=True)
        full_record_output.write_text(full_record, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="分阶段调用外部模型，验证 Event 边界与内容准确性。"
    )
    parser.add_argument("--chat-jsonl", type=Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="OPENCODE_API_KEY")
    parser.add_argument("--entity-context", type=Path)
    parser.add_argument("--gold-fixture", type=Path)
    parser.add_argument("--require-gold-match", action="store_true")
    parser.add_argument("--rounds-per-batch", type=int, default=4)
    parser.add_argument("--batches", type=int, default=1)
    parser.add_argument("--skip-rounds", type=int, default=0)
    parser.add_argument("--batch-number-start", type=int, default=1)
    parser.add_argument("--initial-state", type=Path)
    parser.add_argument("--boundary-max-tokens", type=int, default=3500)
    parser.add_argument("--content-max-tokens", type=int, default=8000)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--full-record-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        print(f"缺少环境变量 {args.api_key_env}", file=sys.stderr)
        return 2
    if args.rounds_per_batch < 1 or args.batches < 1:
        print("批次和每批轮数必须大于零", file=sys.stderr)
        return 2
    if args.require_gold_match and not args.gold_fixture:
        print("--require-gold-match 必须与 --gold-fixture 一起使用", file=sys.stderr)
        return 2

    output_dir = result_directory(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state = (
        json.loads(args.initial_state.read_text(encoding="utf-8"))
        if args.initial_state
        else initial_state()
    )
    entity_context = load_entity_context(args.entity_context)
    gold_fixture = (
        load_gold_fixture(args.gold_fixture, args.chat_jsonl)
        if args.gold_fixture
        else None
    )
    rounds = iter_rounds(args.chat_jsonl)
    for _ in range(args.skip_rounds):
        try:
            next(rounds)
        except StopIteration as exc:
            raise RuntimeError("聊天轮数不足，无法跳过指定数量") from exc

    run: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": "boundary_then_content_v1",
        "input_file": args.chat_jsonl.name,
        "endpoint": args.endpoint,
        "model": args.model,
        "rounds_per_batch": args.rounds_per_batch,
        "skip_rounds": args.skip_rounds,
        "initial_state_file": args.initial_state.name if args.initial_state else None,
        "entity_context_file": args.entity_context.name if args.entity_context else None,
        "gold_fixture_file": args.gold_fixture.name if args.gold_fixture else None,
        "batches": [],
        "final_state": deepcopy(state),
    }

    for local_batch_index in range(args.batches):
        batch_number = args.batch_number_start + local_batch_index
        batch = take_batch(rounds, args.rounds_per_batch)
        if len(batch) != args.rounds_per_batch:
            raise RuntimeError(
                f"聊天只剩 {len(batch)} 轮，不足批次 {batch_number} 所需的 {args.rounds_per_batch} 轮"
            )
        input_chars = sum(len(message["content"]) for message in batch_messages(batch))
        print(
            f"批次 {batch_number}：第 {batch[0]['round']}—{batch[-1]['round']} 轮，输入 {input_chars} 字符",
            flush=True,
        )
        state_before = deepcopy(state)

        boundary_prompt = build_boundary_prompt(
            state, batch, batch_number, entity_context
        )
        boundary_stage = run_model_stage(
            stage="boundary",
            batch_number=batch_number,
            output_dir=output_dir,
            endpoint=args.endpoint,
            api_key=api_key,
            model=args.model,
            system_prompt=BOUNDARY_SYSTEM_PROMPT,
            user_prompt=boundary_prompt,
            timeout=args.timeout,
            max_tokens=args.boundary_max_tokens,
            validator=lambda plan: (validate_boundary_plan(plan, state, batch), []),
            normalizer=lambda plan: normalize_boundary_plan(plan, state, batch),
        )
        if boundary_stage["raw"]:
            (output_dir / f"batch_{batch_number:02d}_boundary_raw.txt").write_text(
                boundary_stage["raw"], encoding="utf-8"
            )
        if not boundary_stage["ok"]:
            batch_result = {
                "batch_number": batch_number,
                "round_start": batch[0]["round"],
                "round_end": batch[-1]["round"],
                "input_chars": input_chars,
                "boundary": stage_record(boundary_stage),
                "content": None,
                "operations": [],
                "failure": boundary_stage["fatal_error"],
                "state_before": state_before,
                "state_after": deepcopy(state),
            }
            run["batches"].append(batch_result)
            run["final_state"] = deepcopy(state)
            write_json(output_dir / f"batch_{batch_number:02d}_result.json", batch_result)
            persist_run(output_dir, run, api_key, args.full_record_output)
            raise RuntimeError(f"批次 {batch_number} 边界阶段失败")

        segmented_state, operations, slot_event_ids = apply_boundary_plan(
            state, boundary_stage["plan"]
        )
        content_prompt = build_content_prompt(
            segmented_state,
            boundary_stage["plan"],
            slot_event_ids,
            batch,
            batch_number,
            entity_context,
        )
        content_stage = run_model_stage(
            stage="content",
            batch_number=batch_number,
            output_dir=output_dir,
            endpoint=args.endpoint,
            api_key=api_key,
            model=args.model,
            system_prompt=CONTENT_SYSTEM_PROMPT,
            user_prompt=content_prompt,
            timeout=args.timeout,
            max_tokens=args.content_max_tokens,
            validator=lambda plan: validate_content_plan(
                plan,
                boundary_stage["plan"],
                batch,
                segmented_state,
                slot_event_ids,
            ),
        )
        if content_stage["raw"]:
            (output_dir / f"batch_{batch_number:02d}_content_raw.txt").write_text(
                content_stage["raw"], encoding="utf-8"
            )
        if not content_stage["ok"]:
            batch_result = {
                "batch_number": batch_number,
                "round_start": batch[0]["round"],
                "round_end": batch[-1]["round"],
                "input_chars": input_chars,
                "boundary": stage_record(boundary_stage),
                "content": stage_record(content_stage),
                "operations": [],
                "tentative_operations": operations,
                "slot_event_ids": slot_event_ids,
                "failure": content_stage["fatal_error"],
                "state_before": state_before,
                "state_after": deepcopy(state),
            }
            run["batches"].append(batch_result)
            run["final_state"] = deepcopy(state)
            write_json(output_dir / f"batch_{batch_number:02d}_result.json", batch_result)
            persist_run(output_dir, run, api_key, args.full_record_output)
            raise RuntimeError(f"批次 {batch_number} 内容阶段失败")

        state = apply_content_plan(segmented_state, content_stage["plan"], slot_event_ids)
        batch_result = {
            "batch_number": batch_number,
            "round_start": batch[0]["round"],
            "round_end": batch[-1]["round"],
            "input_chars": input_chars,
            "boundary": stage_record(boundary_stage),
            "content": stage_record(content_stage),
            "operations": operations,
            "slot_event_ids": slot_event_ids,
            "failure": None,
            "state_before": state_before,
            "state_after": deepcopy(state),
        }
        run["batches"].append(batch_result)
        run["final_state"] = deepcopy(state)
        write_json(output_dir / f"batch_{batch_number:02d}_state.json", state)
        write_json(output_dir / f"batch_{batch_number:02d}_result.json", batch_result)
        persist_run(output_dir, run, api_key, args.full_record_output)
        print(f"批次 {batch_number} 已提交并写入检查点", flush=True)

    run["finished_at"] = datetime.now(timezone.utc).isoformat()
    run["final_state"] = deepcopy(state)
    if gold_fixture is not None:
        processed_round_end = (
            int(run["batches"][-1]["round_end"])
            if run["batches"]
            else args.skip_rounds
        )
        run["semantic_evaluation"] = evaluate_state_against_gold(
            state, gold_fixture, processed_round_end
        )
    persist_run(output_dir, run, api_key, args.full_record_output)
    print(f"完成。结果目录：{output_dir}", flush=True)
    if (
        args.require_gold_match
        and run.get("semantic_evaluation", {}).get("passed") is False
    ):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
