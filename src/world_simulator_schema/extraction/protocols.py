"""Explicit provider capabilities; no model-name guessing or mixed wire formats."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit, quote

CAPABILITIES = {
    "version": 1,
    "transports": [
        {"id": "chat_completions", "label": "Chat Completions", "reasoningModes": ["omit", "effort", "thinking", "thinking_and_effort"]},
        {"id": "responses", "label": "Responses", "reasoningModes": ["omit", "effort"]},
        {"id": "messages", "label": "Anthropic Messages", "reasoningModes": ["omit", "budget", "adaptive"]},
        {"id": "gemini", "label": "Gemini GenerateContent", "reasoningModes": ["omit", "budget", "level"]},
    ],
    "reasoningLevels": ["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"],
    "defaults": {"reasoningMode": "omit", "tokenParameter": "max_tokens", "responseFormat": "text", "maxOutputTokens": 8192, "thinkingBudget": 2048, "timeoutSeconds": 300, "stream": False},
}


def options_for(value: dict | None, transport: str = "chat_completions") -> dict:
    options = {**CAPABILITIES["defaults"], **(value or {})}
    protocol = next((p for p in CAPABILITIES["transports"] if p["id"] == transport), None)
    if protocol is None:
        raise ValueError(f"不支持的传输协议：{transport}")
    if options["reasoningMode"] not in protocol["reasoningModes"]:
        raise ValueError(f"{protocol['label']} 不支持所选思考参数格式")
    if options["tokenParameter"] not in {"max_tokens", "max_completion_tokens"}:
        raise ValueError("Chat Completions 输出上限参数无效")
    if options["responseFormat"] not in {"text", "json_object"}:
        raise ValueError("输出格式必须为 text 或 json_object")
    if transport == "messages" and options["responseFormat"] != "text":
        raise ValueError("Messages 当前适配器使用提示词约束 JSON，请选择普通文本传输")
    for key, minimum, maximum in [("maxOutputTokens", 256, 131072), ("thinkingBudget", 0, 131071), ("timeoutSeconds", 10, 1800)]:
        raw = options[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw or not minimum <= raw <= maximum:
            raise ValueError(f"{key} 必须为 {minimum}—{maximum} 之间的整数")
        options[key] = int(raw)
    if not isinstance(options["stream"], bool):
        raise ValueError("stream 必须是布尔值")
    return options


def endpoint_for(base: str, transport: str, model: str) -> str:
    url = urlsplit(base.strip())
    if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
        raise ValueError("请填写有效的 HTTP(S) 地址，认证信息单独保存")
    path = url.path.rstrip("/")
    endings = {"chat_completions": "/chat/completions", "responses": "/responses", "messages": "/messages"}
    if transport == "gemini":
        path = re.sub(r"/models/[^/]+:(?:streamGenerateContent|generateContent)$", "", path)
        path = (path or "/v1beta") + "/models/" + quote(model.removeprefix("models/"), safe="") + ":generateContent"
    elif transport in endings:
        suffix = endings[transport]
        if any(path.endswith(other) for other in endings.values() if other != suffix) or path.endswith((":generateContent", ":streamGenerateContent")):
            raise ValueError("完整接口地址与所选协议不符，请使用该协议地址或共同的基础地址")
        if not path.endswith(suffix):
            path = (path or "/v1") + suffix
    else:
        raise ValueError("不支持的传输协议")
    return urlunsplit((url.scheme, url.netloc, path, url.query, ""))


def build_request(endpoint: str, api_key: str, model: str, system: str, user: str, *, transport="chat_completions", options=None, effort="default", max_tokens=8192) -> tuple[str, dict, dict]:
    opts = options_for(options, transport)
    if effort == "off":
        effort = "none"
    if effort not in CAPABILITIES["reasoningLevels"]:
        raise ValueError("未知思考强度，请使用端点支持的选项")
    tokens = min(int(max_tokens), opts["maxOutputTokens"])
    if not 256 <= tokens <= 131072:
        raise ValueError("输出预算无效")
    mode = opts["reasoningMode"]
    active = mode != "omit" and effort != "default"
    headers = {"Content-Type": "application/json"}
    if transport == "chat_completions":
        headers["Authorization"] = f"Bearer {api_key}"
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], opts["tokenParameter"]: tokens, "stream": opts["stream"]}
        if active and mode in {"effort", "thinking_and_effort"}:
            body["reasoning_effort"] = effort
        if active and mode in {"thinking", "thinking_and_effort"}:
            body["thinking"] = {"type": "disabled" if effort == "none" else "enabled"}
        if opts["responseFormat"] == "json_object":
            body["response_format"] = {"type": "json_object"}
    elif transport == "responses":
        headers["Authorization"] = f"Bearer {api_key}"
        body = {"model": model, "instructions": system, "input": user, "max_output_tokens": tokens, "stream": opts["stream"], "store": False}
        if active:
            body["reasoning"] = {"effort": effort}
        if opts["responseFormat"] == "json_object":
            body["text"] = {"format": {"type": "json_object"}}
    elif transport == "messages":
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
        body = {"model": model, "system": system, "messages": [{"role": "user", "content": user}], "max_tokens": tokens, "stream": opts["stream"]}
        if active:
            if effort == "none":
                body["thinking"] = {"type": "disabled"}
            elif mode == "adaptive":
                if effort not in {"low", "medium", "high", "max"}:
                    raise ValueError("Messages 自适应思考只接受 low、medium、high 或 max")
                body["thinking"] = {"type": "adaptive"}
                body["output_config"] = {"effort": effort}
            else:
                budget = opts["thinkingBudget"]
                if not 1024 <= budget < tokens:
                    raise ValueError("Messages 思考预算需至少 1024 且小于输出上限")
                body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    else:
        headers["x-goog-api-key"] = api_key
        config = {"maxOutputTokens": tokens}
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": config}
        if opts["responseFormat"] == "json_object":
            config["responseMimeType"] = "application/json"
        if active:
            if mode == "level" and effort not in {"minimal", "low", "medium", "high"}:
                raise ValueError("Gemini 思考等级只接受 minimal、low、medium 或 high；关闭请使用预算模式")
            config["thinkingConfig"] = ({"thinkingBudget": 0 if effort == "none" else opts["thinkingBudget"]} if mode == "budget" else {"thinkingLevel": effort})
        if opts["stream"]:
            endpoint = endpoint.replace(":generateContent", ":streamGenerateContent")
            endpoint += ("&" if "?" in endpoint else "?") + "alt=sse"
    if any("\n" in v or "\r" in v for v in headers.values()):
        raise ValueError("认证信息不能包含换行")
    return endpoint, headers, body


def parse_response(data: dict, transport: str) -> tuple[str, dict]:
    """Return only visible answer text, never a reasoning/thinking block."""
    usage = data.get("usage", data.get("usageMetadata", {}))
    reason = None
    if data.get("error"):
        raise ValueError("接口返回错误响应")
    if transport == "chat_completions":
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content", "")
        text = content if isinstance(content, str) else "".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") in {"text", "output_text"})
        reason = choice.get("finish_reason")
        if message.get("refusal") or reason == "content_filter":
            raise ValueError("模型未提供可用正文（拒绝或内容过滤）")
    elif transport == "responses":
        if any(part.get("type") == "refusal" for item in data.get("output", []) for part in item.get("content", [])):
            raise ValueError("模型拒绝了本次请求")
        text = "".join(part.get("text", "") for item in data.get("output", []) if item.get("type") == "message" for part in item.get("content", []) if part.get("type") == "output_text")
        reason = "length" if data.get("status") == "incomplete" else data.get("status")
        if data.get("status") in {"failed", "cancelled"}:
            raise ValueError("Responses 请求未完成")
    elif transport == "messages":
        text = "".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text")
        reason = data.get("stop_reason")
        if reason in {"refusal", "tool_use", "pause_turn"}:
            raise ValueError("Messages 没有返回完整的正式正文")
    else:
        candidate = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
        reason = candidate.get("finishReason")
        if data.get("promptFeedback", {}).get("blockReason") or reason not in {None, "STOP", "MAX_TOKENS"}:
            raise ValueError("Gemini 没有返回可用正文（拒绝或内容过滤）")
    return text, {"usage": usage, "finish_reason": "length" if reason in {"length", "max_tokens", "MAX_TOKENS"} else reason, "model": data.get("model", data.get("modelVersion")), "id": data.get("id"), "content_chars": len(text)}
