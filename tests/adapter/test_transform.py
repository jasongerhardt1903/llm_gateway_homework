"""跨供应商消息归一化测试。

每条规则都对应一类真实的上游 400，因此断言到具体结构而不是"没抛异常"。
"""

from __future__ import annotations

from llm_gw.adapter.transform import MISSING_TOOL_RESULT_TEXT, normalize_messages
from llm_gw.core.schema import TaskMessage


def _assistant_with_call(call_id: str = "c1", name: str = "f"):
    return TaskMessage(
        role="assistant",
        content=[{"type": "tool_call", "id": call_id, "name": name, "arguments": {"a": 1}}],
    )


def _tool_result(call_id: str, content: str = "ok"):
    return TaskMessage(role="tool", content=[{"type": "tool_result", "tool_call_id": call_id, "content": content}])


def test_drops_failed_turns():
    """错误/取消的轮次不该回灌给模型。"""
    messages = [
        TaskMessage(role="user", content="hi"),
        TaskMessage(role="assistant", content="broken", status="error"),
        TaskMessage(role="assistant", content="cut off", status="aborted"),
        TaskMessage(role="assistant", content="good"),
    ]

    kept = normalize_messages(messages)

    assert [message.text() for message in kept] == ["hi", "good"]


def test_drops_empty_assistant_turn():
    messages = [
        TaskMessage(role="user", content="hi"),
        TaskMessage(role="assistant", content=[]),
    ]

    assert len(normalize_messages(messages)) == 1


def test_synthesizes_missing_tool_result_right_after_the_call():
    """调用与结果必须成对，且补齐位置要紧跟声明它的轮次。"""
    messages = [
        TaskMessage(role="user", content="q"),
        _assistant_with_call("c1"),
        TaskMessage(role="user", content="follow up"),
    ]

    kept = normalize_messages(messages)

    assert [message.role for message in kept] == ["user", "assistant", "tool", "user"]
    assert kept[2].tool_results()[0].tool_call_id == "c1"
    assert kept[2].tool_results()[0].content == MISSING_TOOL_RESULT_TEXT


def test_drops_orphan_tool_result():
    """引用不到调用的结果会让上游 400，必须丢弃。"""
    messages = [
        TaskMessage(role="user", content="q"),
        _tool_result("ghost"),
    ]

    assert [message.role for message in normalize_messages(messages)] == ["user"]


def test_normalizes_blank_tool_call_id_and_pairs_result_by_position():
    """空 id 的调用要补出稳定 id，且位置配对的空 id 结果仍能对上。"""
    messages = [
        TaskMessage(role="user", content="q"),
        _assistant_with_call(""),
        _tool_result("", "sunny"),
    ]

    kept = normalize_messages(messages)

    call_id = kept[1].tool_calls()[0].id
    assert call_id.startswith("call_")
    assert kept[2].tool_results()[0].tool_call_id == call_id
    assert len(kept) == 3  # 没有多余的补齐


def test_strips_whitespace_from_tool_call_id():
    messages = [
        TaskMessage(role="user", content="q"),
        _assistant_with_call("  c1  "),
        _tool_result("c1"),
    ]

    kept = normalize_messages(messages)

    assert kept[1].tool_calls()[0].id == "c1"
    assert len(kept) == 3


def test_keeps_plain_text_messages_untouched():
    messages = [
        TaskMessage(role="system", content="be brief"),
        TaskMessage(role="user", content="hi"),
    ]

    assert normalize_messages(messages) == messages
