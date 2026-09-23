"""Bounded HTTP execution and visible-text parsing for explicitly selected protocols."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .protocols import build_request, options_for, parse_response
from .segmentation import ChatCompletionTransportError


def parse_wire(raw: str, transport: str) -> tuple[str, dict]:
    if not raw.lstrip().startswith(("data:", "event:", ":")):
        return parse_response(json.loads(raw), transport)
    texts, metadata, complete = [], {}, False
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            complete = True
            continue
        data = json.loads(payload)
        if data.get("error") or data.get("type") in {"error", "response.failed"}:
            raise ValueError("流式接口返回错误")
        if transport == "chat_completions":
            choice = (data.get("choices") or [{}])[0]
            if choice.get("delta", {}).get("refusal") or choice.get("finish_reason") == "content_filter":
                raise ValueError("模型未提供可用正文（拒绝或内容过滤）")
            content = choice.get("delta", {}).get("content")
            if isinstance(content, str):
                texts.append(content)
            if choice.get("finish_reason"):
                complete = True
                metadata["finish_reason"] = choice["finish_reason"]
            if data.get("usage"):
                metadata["usage"] = data["usage"]
        elif transport == "responses":
            if data.get("type") == "response.refusal.delta":
                raise ValueError("模型拒绝了本次请求")
            if data.get("type") == "response.output_text.delta":
                texts.append(data.get("delta", ""))
            if data.get("type") in {"response.completed", "response.incomplete"}:
                complete = True
                final_text, metadata = parse_response(data.get("response", {}), transport)
                if final_text:
                    texts = [final_text]
        elif transport == "messages":
            kind, delta = data.get("type"), data.get("delta", {})
            if kind == "content_block_delta" and delta.get("type") == "text_delta":
                texts.append(delta.get("text", ""))
            if kind == "message_start":
                metadata["usage"] = data.get("message", {}).get("usage", {})
            if kind == "message_delta":
                metadata["finish_reason"] = delta.get("stop_reason")
                metadata["usage"] = {**metadata.get("usage", {}), **data.get("usage", {})}
            complete |= kind == "message_stop"
        else:
            text, detail = parse_response(data, transport)
            texts.append(text)
            if detail.get("finish_reason"):
                complete = True
                metadata = detail
    if not complete:
        raise ValueError("流式回复中断，缺少结束标志；不会把不完整 JSON 当成成功")
    if metadata.get("finish_reason") in {"max_tokens", "MAX_TOKENS"}:
        metadata["finish_reason"] = "length"
    if metadata.get("finish_reason") in {"refusal", "tool_use", "pause_turn", "content_filter"}:
        raise ValueError("流式请求没有返回完整的正式正文")
    return "".join(texts), {**metadata, "stream": True}


def call_model(endpoint, api_key, model, system_prompt, user_prompt, timeout, max_tokens, *, client_options, thinking_mode="default", **_):
    if os.environ.get("NEXUS_EXTRACTION_OFFLINE") == "1":
        raise RuntimeError("离线验收模式禁止模型网络调用")
    protocol = client_options.get("transport", "chat_completions")
    options = options_for(client_options.get("options"), protocol)
    endpoint, headers, body = build_request(endpoint, api_key, model, system_prompt, user_prompt, transport=protocol, options=options, effort=thinking_mode, max_tokens=max_tokens)
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("未找到 curl，请检查运行环境")
    started = time.monotonic()
    timeout = min(timeout, options["timeoutSeconds"])
    with tempfile.TemporaryDirectory(prefix="nexus-request-") as directory:
        payload = Path(directory) / "body.json"
        header_file = Path(directory) / "headers.txt"
        payload.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        # Credentials are passed over stdin, not in process arguments or persisted artifacts.
        escape = lambda value: value.replace("\\", "\\\\").replace('"', '\\"')
        config = "\n".join(f'header = "{escape(key + ": " + value)}"' for key, value in headers.items())
        args = [curl, "--config", "-", "--silent", "--show-error", "--request", "POST", "--max-time", str(timeout), "--dump-header", str(header_file), "--data-binary", "@" + str(payload), endpoint]
        try:
            result = subprocess.run(args, input=config, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout + 5, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        except subprocess.TimeoutExpired as exc:
            raise ChatCompletionTransportError("模型请求超时", metadata={"error_code": "timeout", "retryable": True}) from exc
        header_lines = header_file.read_text(encoding="utf-8", errors="replace").splitlines() if header_file.exists() else []
        statuses = [int(line.split()[1]) for line in header_lines if line.startswith("HTTP/") and len(line.split()) > 1 and line.split()[1].isdigit()]
        status = statuses[-1] if statuses else 0
        retry_after = next((line.split(":", 1)[1].strip() for line in reversed(header_lines) if line.lower().startswith("retry-after:")), "")
        if result.returncode or not 200 <= status < 300:
            code = "authentication" if status in {401, 403} else "rate_limit" if status == 429 else "configuration" if 400 <= status < 500 else "timeout" if result.returncode == 28 else "transport"
            detail = result.stderr.strip() if not status else f"HTTP {status}"
            if status in {400, 404, 422}:
                try:
                    error = json.loads(result.stdout).get("error", {})
                    detail += ": " + str(error.get("message", "请求参数或模型不受端点支持"))[:500]
                except (ValueError, AttributeError):
                    pass
            detail = detail.replace(api_key, "[已隐藏]") if api_key else detail
            raise ChatCompletionTransportError(detail or "网络连接失败", metadata={"error_code": code, "http_status": status, "retryable": status in {408, 429} or status >= 500 or (not status and result.returncode in {5, 6, 7, 18, 28, 52, 55, 56}), "retry_after": min(60, int(retry_after)) if retry_after.isdigit() else 0})
        try:
            content, metadata = parse_wire(result.stdout, protocol)
        except (ValueError, TypeError, KeyError) as exc:
            raise ChatCompletionTransportError(str(exc).replace(api_key, "[已隐藏]"), metadata={"error_code": "response_protocol", "retryable": False}) from exc
        if not content.strip():
            raise ChatCompletionTransportError("接口已结束但没有正式正文，请检查模型、思考预算与协议配置", metadata={**metadata, "error_code": "empty_content", "retryable": False})
        metadata.update({"transport": protocol, "model": metadata.get("model") or model, "content_chars": len(content), "timing_seconds": {"total": round(time.monotonic() - started, 3)}, "request_options": options})
        return content, metadata
