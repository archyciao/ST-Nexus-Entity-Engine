"""Lossless source snapshots with explicit roles and budgeted message batches."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def message_rounds(path: Path):
    pending = []
    previous = None
    for index, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines()):
        item = json.loads(line)
        if "mes" not in item:
            continue
        extra = item.get("extra") or {}
        floor = int(extra.get("nexus_source_floor", index))
        raw_content = str(item.get("mes") or "")
        content = raw_content.replace("\x00", "")
        version = str(extra.get("nexus_source_version") or hashlib.sha256(raw_content.encode()).hexdigest())
        role = "system" if item.get("is_system") else "user" if item.get("is_user") else "assistant"
        identity = str(extra.get("nexus_source_id") or f"floor-{floor}")
        message = {"ref": f"m{floor}.{version[:12]}.{role}", "role": role, "name": str(item.get("name") or role), "source_line": floor + 1, "source_floor": floor, "source_id": identity, "version": version, "content": content, "raw_content": raw_content}
        if role != "assistant" or not content.strip():
            pending.append(message)
            continue
        # Compatibility shape for event lifecycle code; all real messages are also retained.
        user = next((m for m in reversed(pending) if m["role"] == "user"), None)
        if user is None:
            user = {"ref": f"m{floor}.context.user", "role": "user", "name": "user", "source_line": floor + 1, "content": ""}
        if previous:
            yield previous
        previous = {"round": floor + 1, "user": user, "assistant": message, "messages": [*pending, message]}
        pending = []
    if previous:
        previous["messages"].extend(pending)
        yield previous


def budget_batches(rounds, max_messages=8, max_chars=24000):
    batch, chars = [], 0
    for row in rounds:
        size = sum(len(m["content"]) for m in row.get("messages", [row["assistant"]]))
        if size > max_chars:
            raise ValueError(f"第 {row['round'] - 1} 楼超过单批字符预算，请调高预算或缩短该消息；原文没有被截断")
        if batch and (len(batch) >= max_messages or chars + size > max_chars):
            yield batch
            batch, chars = [], 0
        batch.append(row)
        chars += size
    if batch:
        yield batch
