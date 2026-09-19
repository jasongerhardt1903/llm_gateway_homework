"""跨供应商消息归一化。

不同供应商对历史轮次的容忍度不同，但都同样严格：孤儿 tool_result、
缺失的 tool_result、空 assistant 轮次都会直接 400。这些问题应该在网关里
一次性修掉，而不是让每个 agent 自己适配每家供应商。

移植 pi 的 ``transform-messages.ts``，只保留与本网关 schema 相关的部分。
"""

from __future__ import annotations

import hashlib

from ..core.schema import TaskMessage, ToolCallBlock, ToolResultBlock

__all__ = ["normalize_messages", "normalize_tool_call_id", "MISSING_TOOL_RESULT_TEXT"]

MISSING_TOOL_RESULT_TEXT = "工具未执行：该调用没有对应的执行结果。"


def normalize_tool_call_id(raw: str | None, *, fallback: str) -> str:
    """归一化工具调用 id。

    部分供应商会给出带空白或为空的 id，直接回灌会让上下游对不上号。
    """
    if raw and raw.strip():
        return raw.strip()
    digest = hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:12]
    return f"call_{digest}"


def normalize_messages(messages: list[TaskMessage]) -> list[TaskMessage]:
    """把历史消息整理成所有供应商都能接受的最保守形态。

    规则（每条都对应一类真实 400）：

    1. 丢弃 ``status`` 为 error/aborted 的轮次——失败的输出不该回灌给模型；
    2. 丢弃空内容的 assistant 轮次；
    3. 归一化 tool_call id；
    4. 丢弃引用不到任何调用的孤儿 tool_result；
    5. 为没有结果的 tool_call 补一条说明性 tool_result，保持调用成对出现。
    """
    kept: list[TaskMessage] = []
    #: 声明顺序，用于把"没带 id 的结果"按序配对回调用。
    declared: list[str] = []
    resolved: set[str] = set()

    for message in messages:
        if message.status != "ok":
            continue

        if message.role == "assistant":
            if not message.text() and not message.tool_calls():
                continue
            message = _normalize_assistant(message, offset=len(declared))
            for call in message.tool_calls():
                if call.id not in declared:
                    declared.append(call.id)
            kept.append(message)
            continue

        if message.role == "tool":
            normalized = _normalize_tool_message(message, declared, resolved)
            if normalized is None:
                continue
            kept.append(normalized)
            continue

        kept.append(message)

    _fill_missing_results(kept, resolved)
    return kept


def _normalize_assistant(message: TaskMessage, *, offset: int) -> TaskMessage:
    """重建内容块：tool_call 的 id 统一补齐，其余块原样保留。"""
    if not isinstance(message.content, list):
        return message
    calls = message.tool_calls()
    if not calls:
        return message

    replacements = iter(
        ToolCallBlock(
            type="tool_call",
            id=normalize_tool_call_id(call.id, fallback=f"{call.name}:{offset + index}"),
            name=call.name,
            arguments=call.arguments,
        )
        for index, call in enumerate(calls)
    )
    content = [next(replacements) if isinstance(block, ToolCallBlock) else block for block in message.content]
    return message.model_copy(update={"content": content})


def _normalize_tool_message(
    message: TaskMessage,
    declared: list[str],
    resolved: set[str],
) -> TaskMessage | None:
    """过滤孤儿 tool_result；若全部是孤儿则整条丢弃。"""
    results = message.tool_results()
    if results:
        blocks: list[ToolResultBlock] = []
        for result in results:
            call_id = _match_call(result.tool_call_id, declared, resolved)
            if call_id is None:
                continue
            resolved.add(call_id)
            blocks.append(
                ToolResultBlock(type="tool_result", tool_call_id=call_id, content=result.content)
            )
        if not blocks:
            return None
        return message.model_copy(update={"content": blocks})

    call_id = _match_call(message.tool_call_id, declared, resolved)
    if call_id is None:
        return None
    resolved.add(call_id)
    return message


def _match_call(raw_id: str | None, declared: list[str], resolved: set[str]) -> str | None:
    """把结果配回某个已声明的调用。

    结果没带 id 时按声明顺序取第一个未解决的调用——这比直接丢弃更有用，
    因为部分供应商的 tool_result 不带 id。
    """
    if raw_id and raw_id.strip():
        candidate = raw_id.strip()
        return candidate if candidate in declared else None
    for call_id in declared:
        if call_id not in resolved:
            return call_id
    return None


def _fill_missing_results(kept: list[TaskMessage], resolved: set[str]) -> None:
    """把未收到结果的工具调用就地补齐。

    必须插在声明它的 assistant 轮次之后：供应商按顺序配对调用与结果，
    补到历史末尾会让配对错位。
    """
    for index in range(len(kept) - 1, -1, -1):
        message = kept[index]
        if message.role != "assistant":
            continue
        missing = [call.id for call in message.tool_calls() if call.id not in resolved]
        if not missing:
            continue
        resolved.update(missing)
        kept.insert(
            index + 1,
            TaskMessage(
                role="tool",
                content=[
                    ToolResultBlock(
                        type="tool_result",
                        tool_call_id=call_id,
                        content=MISSING_TOOL_RESULT_TEXT,
                    )
                    for call_id in missing
                ],
            ),
        )
