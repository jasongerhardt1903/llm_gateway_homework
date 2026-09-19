"""统一 Task Schema 与两层校验测试。

对应需求：
- 定义统一的 task schema，LLM GW 和后端 agent 都应遵循。
- 对收到的 task 进行 2 层校验：1. 校验语法 2. 校验 task 是否符合 schema。
"""

from __future__ import annotations

import pytest

from llm_gw.core.schema import (
    SchemaViolation,
    SyntaxViolation,
    Task,
    TaskMessage,
    ToolSpec,
    validate_schema,
    validate_syntax,
)


def _valid_payload() -> dict:
    return {
        "task_id": "t-1",
        "profile": "fast-chat",
        "input": {
            "messages": [{"role": "user", "content": "hello"}],
            "stream": True,
        },
    }


# --------------------------------------------------------------------------
# 第一层：语法校验
# --------------------------------------------------------------------------


def test_validate_syntax_accepts_valid_json():
    """合法 JSON 通过第一层，返回解析后的 dict。"""
    raw = '{"task_id": "t-1", "input": {"messages": []}}'
    parsed = validate_syntax(raw)

    assert isinstance(parsed, dict)
    assert parsed["task_id"] == "t-1"


def test_validate_syntax_accepts_bytes():
    """同时支持 bytes 输入（HTTP 请求体）。"""
    assert validate_syntax(b'{"task_id": "t-1", "input": {"messages": []}}')["task_id"] == "t-1"


def test_validate_syntax_rejects_malformed_json():
    """语法错误必须抛 SyntaxViolation，且与 schema 错误区分开。"""
    with pytest.raises(SyntaxViolation):
        validate_syntax('{"task_id": "t-1", ')


def test_validate_syntax_rejects_non_object_root():
    """根节点必须是对象，数组/标量属于语法层不匹配。"""
    with pytest.raises(SyntaxViolation):
        validate_syntax("[1, 2, 3]")


# --------------------------------------------------------------------------
# 第二层：schema 校验
# --------------------------------------------------------------------------


def test_validate_schema_returns_typed_task():
    """合法结构返回强类型 Task。"""
    task = validate_schema(_valid_payload())

    assert isinstance(task, Task)
    assert task.task_id == "t-1"
    assert task.profile == "fast-chat"
    assert task.input.messages[0].role == "user"
    assert task.input.messages[0].content == "hello"
    assert task.input.stream is True


def test_validate_schema_rejects_missing_required_field():
    """缺少必需字段必须抛 SchemaViolation。"""
    payload = _valid_payload()
    del payload["task_id"]

    with pytest.raises(SchemaViolation) as excinfo:
        validate_schema(payload)

    assert "task_id" in str(excinfo.value)


def test_validate_schema_rejects_unknown_role():
    """角色取值必须受约束，拼错要报错而不是静默透传。"""
    payload = _valid_payload()
    payload["input"]["messages"] = [{"role": "robot", "content": "hi"}]

    with pytest.raises(SchemaViolation):
        validate_schema(payload)


def test_validate_schema_rejects_empty_messages():
    """消息列表不得为空，否则请求没有意义。"""
    payload = _valid_payload()
    payload["input"]["messages"] = []

    with pytest.raises(SchemaViolation):
        validate_schema(payload)


def test_schema_violation_exposes_field_paths():
    """校验失败需给出字段路径，便于 agent 修正请求。"""
    payload = _valid_payload()
    payload["input"]["temperature"] = "hot"

    with pytest.raises(SchemaViolation) as excinfo:
        validate_schema(payload)

    assert "temperature" in str(excinfo.value)


# --------------------------------------------------------------------------
# 派生能力需求（供路由层做能力匹配）
# --------------------------------------------------------------------------


def test_task_reports_required_capabilities_for_routing():
    """路由需要知道 task 的特征：是否需要流式、工具、结构化输出、图片。"""
    task = validate_schema(
        {
            "task_id": "t-2",
            "input": {
                "messages": [{"role": "user", "content": "draw"}],
                "stream": False,
                "tools": [{"name": "lookup", "description": "d", "parameters": {"type": "object"}}],
                "response_schema": {"type": "object", "properties": {"a": {"type": "integer"}}},
            },
        }
    )

    caps = task.required_capabilities()

    assert caps.streaming is False
    assert caps.tools is True
    assert caps.json_schema is True
    assert caps.vision is False


def test_task_detects_vision_requirement_from_image_block():
    """消息含图片块时，路由需筛掉不支持图片的模型。"""
    task = validate_schema(
        {
            "task_id": "t-3",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "what is this"},
                            {"type": "image", "data": "AAAA", "mime_type": "image/png"},
                        ],
                    }
                ]
            },
        }
    )

    assert task.required_capabilities().vision is True


def test_task_defaults_stream_to_true():
    """需求规定 adapter 层默认采用 SSE 通讯，因此默认 stream=True。"""
    task = validate_schema({"task_id": "t-4", "input": {"messages": [{"role": "user", "content": "hi"}]}})

    assert task.input.stream is True
    assert task.required_capabilities().streaming is True


def test_prompt_fingerprint_is_stable_and_content_sensitive():
    """Prompt 指纹用于记录 hash 与版本，必须稳定且对内容敏感。"""
    a = validate_schema(_valid_payload())
    b = validate_schema(_valid_payload())

    assert a.prompt_fingerprint() == b.prompt_fingerprint()

    payload = _valid_payload()
    payload["input"]["messages"] = [{"role": "user", "content": "different"}]
    c = validate_schema(payload)

    assert c.prompt_fingerprint() != a.prompt_fingerprint()


def test_tool_spec_requires_name_and_parameters():
    """工具定义必须含名称与参数 schema。"""
    spec = ToolSpec(name="lookup", description="d", parameters={"type": "object"})
    assert spec.name == "lookup"

    with pytest.raises(SchemaViolation):
        validate_schema(
            {
                "task_id": "t-5",
                "input": {
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": [{"description": "missing name", "parameters": {"type": "object"}}],
                },
            }
        )


def test_task_message_accepts_string_content():
    """最简形式：content 为纯字符串。"""
    msg = TaskMessage(role="user", content="hi")
    assert msg.content == "hi"
