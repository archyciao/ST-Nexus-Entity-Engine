"""Small, string-aware syntax recovery. Never invent missing brackets or facts."""
import json


def extract_object(text):
    if text.lstrip().startswith('['):
        raise ValueError('提取结果必须是单个 JSON 对象，不能从数组中截取一项')
    start = text.find('{')
    if start < 0:
        raise ValueError('模型输出中没有 JSON 对象')
    depth, quoted, escaped, end = 0, False, False, None
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': quoted = False
        elif char == '"': quoted = True
        elif char in '{[': depth += 1
        elif char in '}]':
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        raise ValueError('JSON 被截断或括号不完整；不会补齐成假成功')
    if '{' in text[end:]:
        raise ValueError('回复包含多个 JSON 对象，无法确定唯一结果')
    candidate = text[start:end]
    try:
        result = json.loads(candidate)
    except json.JSONDecodeError:
        # Remove only commas immediately before closing delimiters outside strings.
        chars, quoted, escaped = [], False, False
        for index, char in enumerate(candidate):
            if quoted:
                chars.append(char)
                if escaped: escaped = False
                elif char == '\\': escaped = True
                elif char == '"': quoted = False
                continue
            if char == '"': quoted = True
            if char == ',' and candidate[index + 1:].lstrip().startswith(('}', ']')):
                continue
            chars.append(char)
        result = json.loads(''.join(chars))
    if not isinstance(result, dict):
        raise ValueError('提取结果必须为 JSON 对象')
    return result
