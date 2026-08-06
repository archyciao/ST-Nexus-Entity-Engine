"""用最小请求检查 OpenCode Zen 是否实际遵从关闭思考参数。"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from event_segmentation_probe import ChatCompletionTransportError, call_chat_completion


SYSTEM_PROMPT = "你正在执行接口参数诊断。不要解释，只输出字符串 OK。"
USER_PROMPT = "输出 OK"


def diagnostic_result(
    *,
    metadata: dict[str, Any],
    content: str,
    error: str | None = None,
) -> dict[str, Any]:
    """只保存可公开诊断信息，不保存密钥或隐藏推理原文。"""

    reasoning_chars = int(metadata.get("reasoning_chars", 0))
    content_chars = int(metadata.get("content_chars", len(content)))
    if reasoning_chars == 0 and content.strip():
        verdict = "thinking_disabled_observed"
    elif reasoning_chars > 0:
        verdict = "thinking_disable_not_honored"
    else:
        verdict = "inconclusive"
    return {
        "verdict": verdict,
        "error": error,
        "request_options": metadata.get("request_options", {}),
        "reasoning_chars": reasoning_chars,
        "content_chars": content_chars,
        "timing_seconds": metadata.get("timing_seconds", {}),
        "finish_reason": metadata.get("finish_reason"),
        "content": content,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--endpoint",
        default="https://opencode.ai/zen/v1/chat/completions",
    )
    parser.add_argument("--model", default="deepseek-v4-flash-free")
    parser.add_argument("--api-key-env", default="OPENCODE_API_KEY")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--content-start-timeout", type=float, default=45.0)
    parser.add_argument("--stream-idle-timeout", type=float, default=30.0)
    parser.add_argument("--max-tokens", type=int, default=32)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise RuntimeError(f"环境变量 {args.api_key_env} 中没有 API 密钥")

    content = ""
    try:
        content, metadata = call_chat_completion(
            args.endpoint,
            api_key,
            args.model,
            SYSTEM_PROMPT,
            USER_PROMPT,
            args.timeout,
            args.max_tokens,
            thinking_mode="off",
            response_format="text",
            stream_idle_timeout=args.stream_idle_timeout,
            content_start_timeout=args.content_start_timeout,
        )
        result = diagnostic_result(metadata=metadata, content=content)
    except ChatCompletionTransportError as exc:
        result = diagnostic_result(
            metadata=exc.metadata,
            content=exc.partial_content,
            error=str(exc),
        )

    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["verdict"] == "thinking_disabled_observed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
