"""JSON 修复与增量解析。

对应需求：
- 流式 JSON 需要增量解析，不能等全部 chunk 到齐再解析。
- 输出修复要有边界，修复失败应返回明确错误，不是静默吞掉。

移植 pi 的 ``json-parse.ts``：``repairJson`` 负责转义字符串内的裸控制字符、
修正非法转义；``parseStreamingJson`` 在流式热路径上**永不抛异常**，
尽最大努力产出已到达的字段。
"""

from __future__ import annotations

import json
import re

__all__ = [
    "repair_json",
    "parse_json_with_repair",
    "parse_streaming_json",
    "extract_json",
]

_VALID_ESCAPES = set('"\\/bfnrtu')

_CONTROL_ESCAPES = {
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}

_FENCE_PATTERN = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _is_control_character(char: str) -> bool:
    codepoint = ord(char)
    return 0x00 <= codepoint <= 0x1F


def _escape_control_character(char: str) -> str:
    if char in _CONTROL_ESCAPES:
        return _CONTROL_ESCAPES[char]
    return f"\\u{ord(char):04x}"


def repair_json(text: str) -> str:
    """修复字符串字面量内部的非法字符。

    只做两件安全的事：
    - 转义裸控制字符（换行、制表符等）；
    - 把非法转义（如 ``\\d``）补成合法转义。

    字符串之外的内容一律不动，因此合法 JSON 会原样返回。
    """
    repaired: list[str] = []
    in_string = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if not in_string:
            repaired.append(char)
            if char == '"':
                in_string = True
            index += 1
            continue

        if char == '"':
            repaired.append(char)
            in_string = False
            index += 1
            continue

        if char == "\\":
            next_char = text[index + 1] if index + 1 < length else None
            if next_char is None:
                # 字符串以裸反斜杠结尾，补成合法转义。
                repaired.append("\\\\")
                index += 1
                continue

            if next_char == "u":
                digits = text[index + 2 : index + 6]
                if len(digits) == 4 and all(c in "0123456789abcdefABCDEF" for c in digits):
                    repaired.append(f"\\u{digits}")
                    index += 6
                    continue

            if next_char in _VALID_ESCAPES:
                repaired.append(f"\\{next_char}")
                index += 2
                continue

            # 非法转义：双写反斜杠，保留原始字符。
            repaired.append("\\\\")
            index += 1
            continue

        repaired.append(_escape_control_character(char) if _is_control_character(char) else char)
        index += 1

    return "".join(repaired)


def parse_json_with_repair(text: str):
    """先直接解析，失败再修复后解析。

    两者都失败时抛 ``json.JSONDecodeError``——**明确报错，不静默返回空值**。
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = repair_json(text)
        if repaired != text:
            return json.loads(repaired)
        raise


# --------------------------------------------------------------------------
# 增量解析
# --------------------------------------------------------------------------


def _close_open_structures(text: str) -> str:
    """尽最大努力补齐未闭合的字符串与括号。

    只在 ``parse_streaming_json`` 中使用，属于有界的启发式修复：
    不猜测缺失的值，只把已经完整到达的部分闭合起来。
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    last_complete = -1

    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if in_string:
            if char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
                last_complete = index
            continue
        if char == '"':
            in_string = True
            last_complete = index
        elif char in "{[":
            stack.append(char)
            last_complete = index
        elif char in "}]":
            if stack:
                stack.pop()
            last_complete = index
        elif char in "0123456789truefalsn.+-eE":
            last_complete = index

    if not stack and not in_string:
        return text

    if in_string:
        # 截断在字符串内部：已到达的字符是真实内容，保留并补上结束引号。
        # 若退回到键名位置，增量解析就永远看不到正在流出的字符串值。
        candidate = text
    else:
        candidate = text[: last_complete + 1]
        # 截断在键名或冒号处，回退到最后一个完整的键值对。
        candidate = re.sub(r",\s*$", "", candidate.rstrip())
        candidate = re.sub(r":\s*$", "", candidate.rstrip())
        candidate = re.sub(r'"[^"]*"\s*$', "", candidate.rstrip())
        candidate = re.sub(r",\s*$", "", candidate.rstrip())

    # 重新计算需要补齐的括号（截断后栈会变化）。
    stack = []
    in_string = False
    escaped = False
    for char in candidate:
        if escaped:
            escaped = False
            continue
        if in_string:
            if char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]" and stack:
            stack.pop()

    if in_string:
        candidate += '"'
    while stack:
        candidate += "}" if stack.pop() == "{" else "]"

    return candidate


def parse_streaming_json(text: str | None):
    """流式增量解析：永不抛异常，尽最大努力返回已到达的内容。

    返回 ``dict`` 或 ``list``；完全无法解析时返回 ``{}``。
    """
    if text is None:
        return {}
    if not text.strip():
        return {}

    try:
        return _as_container(parse_json_with_repair(text))
    except (json.JSONDecodeError, ValueError):
        pass

    try:
        return _as_container(json.loads(_close_open_structures(text)))
    except (json.JSONDecodeError, ValueError):
        return {}


def _as_container(value):
    """把标量解析结果归一为容器。

    调用方按 ``parsed["field"]`` 取值，因此 ``"null"``/``"123"`` 这类合法
    但非容器的 JSON 也要返回空对象，而不是让调用方拿到 ``None`` 再崩。
    """
    if isinstance(value, (dict, list)):
        return value
    return {}


# --------------------------------------------------------------------------
# 提取
# --------------------------------------------------------------------------


def _find_balanced_json(text: str) -> str | None:
    """扫描出第一个括号配平的 JSON 片段。"""
    start = -1
    opener = closer = ""
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            opener = char
            closer = "}" if char == "{" else "]"
            break
    if start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if in_string:
            if char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_json(text: str) -> str:
    """从模型输出中提取 JSON 文本。

    依次尝试：``` 围栏内容 → 散文中的配平片段 → 整体作为 JSON。
    找不到时抛 ``ValueError``，由调用方决定是否进入有限修复。
    """
    if text is None or not text.strip():
        raise ValueError("empty output, no JSON to extract")

    fenced = _FENCE_PATTERN.search(text)
    if fenced:
        candidate = fenced.group(1).strip()
        if candidate:
            return candidate

    balanced = _find_balanced_json(text)
    if balanced is not None:
        return balanced

    stripped = text.strip()
    try:
        json.loads(stripped)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"no JSON found in model output: {exc}") from exc
    return stripped
