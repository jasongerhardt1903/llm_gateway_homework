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
    output_validity,
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


# --------------------------------------------------------------------------
# response_format 别名（OpenAI 形态）
# --------------------------------------------------------------------------


def _task_with(**input_overrides):
    return validate_schema(
        {
            "task_id": "t-fmt",
            "input": {"messages": [{"role": "user", "content": "hi"}], **input_overrides},
        }
    )


def test_response_format_json_schema_is_normalized_to_response_schema():
    """OpenAI 形态的 json_schema 抽出 schema，此后全链路只认 response_schema。"""
    task = _task_with(
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "weather",
                "strict": True,
                "schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            },
        }
    )

    assert task.input.response_schema == {
        "type": "object",
        "properties": {"city": {"type": "string"}},
    }
    assert task.input.response_mode() == "json_schema"
    assert task.required_capabilities().json_schema is True


def test_response_format_json_object_does_not_require_schema_capability():
    """json_object 只要求"合法 JSON"，不该把只支持 json_object 的模型挡在路由外。"""
    task = _task_with(response_format={"type": "json_object"})

    assert task.input.response_schema is None
    assert task.input.response_mode() == "json_object"
    # 关键：json_schema 能力仍为 False，DeepSeek 这类模型因此仍可被选中。
    assert task.required_capabilities().json_schema is False


def test_response_format_text_disables_structured_output():
    """显式 text 表示"不要结构化约束"，等价于不传。"""
    task = _task_with(response_format={"type": "text"})

    assert task.input.response_mode() == "none"


def test_response_format_requires_json_schema_body():
    """json_schema 形态缺少 schema 时属于结构错误，必须报错而不是静默放行。"""
    with pytest.raises(SchemaViolation) as excinfo:
        _task_with(response_format={"type": "json_schema", "json_schema": {"name": "x"}})

    assert "response_format.json_schema.schema" in str(excinfo.value)


def test_response_format_rejects_unknown_type():
    with pytest.raises(SchemaViolation):
        _task_with(response_format={"type": "xml"})


def test_response_schema_and_response_format_are_mutually_exclusive():
    """两种写法同时给出属于歧义，直接报错而不是猜一个。"""
    with pytest.raises(SchemaViolation) as excinfo:
        _task_with(
            response_schema={"type": "object"},
            response_format={"type": "json_object"},
        )

    assert "不能同时提供" in str(excinfo.value)


def test_prompt_reference_accepts_optional_version_and_variables():
    """版本可省略（解析时取最新）；变量取值是任意 JSON，供模板做替换。"""
    task = validate_schema(
        {
            "task_id": "t-prompt",
            "input": {"messages": [{"role": "user", "content": "hi"}]},
            "prompt": {"name": "qa", "variables": {"q": "天气", "n": 3}},
        }
    )

    assert task.prompt is not None
    assert task.prompt.name == "qa"
    assert task.prompt.version is None
    assert task.prompt.variables == {"q": "天气", "n": 3}


def test_prompt_reference_requires_a_name():
    """``prompt`` 给了就必须能定位到模板：只有一个 version 是没法引用的。"""
    with pytest.raises(SchemaViolation):
        validate_schema(
            {
                "task_id": "t-prompt",
                "input": {"messages": [{"role": "user", "content": "hi"}]},
                "prompt": {"version": "v1"},
            }
        )


def test_absent_prompt_reference_stays_none():
    """不用模板时 ``input.system`` 是什么就发什么，不能凭空造一个引用出来。"""
    assert validate_schema(_valid_payload()).prompt is None


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


# --------------------------------------------------------------------------
# 路由键：model 字段（需求"根据请求中的 model 字段动态路由到对应适配器"）
# --------------------------------------------------------------------------


def test_task_accepts_model_as_top_level_route_key():
    task = validate_schema(
        {
            "task_id": "t-model",
            "model": "openai/gpt-4o-mini",
            "input": {"messages": [{"role": "user", "content": "hi"}]},
        }
    )

    assert task.model == "openai/gpt-4o-mini"
    assert task.profile is None


def test_model_and_profile_can_be_combined():
    """profile 定池子、model 定点名，两者不冲突。"""
    task = validate_schema(
        {
            "task_id": "t-model",
            "profile": "fast",
            "model": "openai/gpt-4o-mini",
            "input": {"messages": [{"role": "user", "content": "hi"}]},
        }
    )

    assert (task.profile, task.model) == ("fast", "openai/gpt-4o-mini")


def test_absent_model_stays_none():
    assert validate_schema(_valid_payload()).model is None


# --------------------------------------------------------------------------
# 输出兑现校验（落 CallRecord.output_valid）
# --------------------------------------------------------------------------


def test_output_validity_is_none_when_no_constraint_was_requested():
    """没要求结构化输出就没有可判的东西——记 False 会把"没要求"诬成"不合格"。"""
    assert output_validity(_task_with().input, "随便一段文字") is None
    assert output_validity(_task_with(response_format={"type": "text"}).input, "{}") is None


def test_output_validity_json_object_only_requires_parsable_json():
    task_input = _task_with(response_format={"type": "json_object"}).input

    assert output_validity(task_input, '{"a": 1}') is True
    assert output_validity(task_input, "这不是 JSON") is False
    assert output_validity(task_input, "") is False


def test_output_validity_json_schema_checks_structure():
    task_input = _task_with(
        response_schema={
            "type": "object",
            "required": ["city"],
            "properties": {"city": {"type": "string"}, "score": {"type": "number"}},
        }
    ).input

    assert output_validity(task_input, '{"city": "上海", "score": 0.9}') is True
    # 合法 JSON，但缺必填字段 / 类型不对 —— 这就是"结构化输出没兑现"。
    assert output_validity(task_input, '{"score": 0.9}') is False
    assert output_validity(task_input, '{"city": 3}') is False
    assert output_validity(task_input, "不是 JSON") is False


def test_output_validity_checks_arrays_and_enums():
    task_input = _task_with(
        response_schema={
            "type": "array",
            "items": {"type": "object", "required": ["level"], "properties": {"level": {"enum": ["高", "低"]}}},
        }
    ).input

    assert output_validity(task_input, '[{"level": "高"}]') is True
    assert output_validity(task_input, '[{"level": "中"}]') is False


def test_output_validity_does_not_mistake_bool_for_integer():
    """``bool`` 是 ``int`` 的子类，不显式排除就会把 ``true`` 判成合法整数。"""
    task_input = _task_with(response_schema={"type": "object", "properties": {"n": {"type": "integer"}}}).input

    assert output_validity(task_input, '{"n": 3}') is True
    assert output_validity(task_input, '{"n": true}') is False


def test_output_validity_ignores_keywords_outside_its_subset():
    """未支持的关键字不参与判定：宁可少判，也不虚报 False。

    这是"我们只判 ``type``/``enum``/``required``/``properties``/``items``"的
    显式后果，写下来是为了让这个边界是被选择的，而不是被发现的。
    """
    task_input = _task_with(
        response_schema={"type": "object", "minProperties": 5, "properties": {"a": {"type": "string"}}}
    ).input

    assert output_validity(task_input, '{"a": "x"}') is True
