"""Structured output 测试。

需求："验证 JSON 提取，Schema 校验，修复尝试次数，校验失败时的错误返回。"

关键点：
- 第一层是"请求时带 schema"（已在契约测试断言），这里验证第二层本地校验；
- 修复**有边界**，失败必须返回明确错误码，不能静默吞掉。
"""

from __future__ import annotations

import pytest

from llm_gw.adapter.structured import (
    SchemaValidationError,
    StructuredRepairError,
    StructuredStreamValidator,
    parse_structured_output,
    validate_json_schema,
    validate_with_repair,
)
from llm_gw.core.errors import ErrorCode, GatewayError

_SCHEMA = {
    "type": "object",
    "properties": {
        "city": {"type": "string"},
        "temperature": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["city", "temperature"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# JSON Schema 校验
# --------------------------------------------------------------------------


def test_valid_object_passes():
    assert validate_json_schema({"city": "上海", "temperature": 21}, _SCHEMA) == []


def test_missing_required_field_reports_path():
    errors = validate_json_schema({"city": "上海"}, _SCHEMA)

    assert len(errors) == 1
    assert "$.temperature" in errors[0]


def test_wrong_scalar_type_is_rejected():
    errors = validate_json_schema({"city": "上海", "temperature": "21"}, _SCHEMA)

    assert any("$.temperature" in error for error in errors)


def test_additional_properties_are_rejected():
    errors = validate_json_schema({"city": "上海", "temperature": 1, "extra": True}, _SCHEMA)

    assert any("$.extra" in error for error in errors)


def test_nested_arrays_are_validated_element_wise():
    errors = validate_json_schema({"city": "上海", "temperature": 1, "tags": ["a", 2]}, _SCHEMA)

    assert any("$.tags[1]" in error for error in errors)


def test_enum_is_enforced():
    errors = validate_json_schema("b", {"type": "string", "enum": ["a", "c"]})

    assert errors


def test_empty_schema_accepts_anything():
    assert validate_json_schema({"anything": 1}, None) == []


# --------------------------------------------------------------------------
# 提取 + 有界修复
# --------------------------------------------------------------------------


def test_parse_structured_output_accepts_fenced_json():
    """模型常把 JSON 包在 ```json 围栏里。"""
    raw = '结果如下：\n```json\n{"city": "上海", "temperature": 21}\n```\n希望有帮助。'

    assert parse_structured_output(raw, _SCHEMA) == {"city": "上海", "temperature": 21}


def test_parse_structured_output_accepts_bare_json_in_prose():
    raw = 'The answer is {"city": "上海", "temperature": 21} as requested.'

    assert parse_structured_output(raw, _SCHEMA)["city"] == "上海"


def test_parse_structured_output_repairs_broken_json():
    """裸控制字符属于可修复的语法问题，修复后应通过校验。"""
    raw = '{"city": "上\n海", "temperature": 21}'

    assert parse_structured_output(raw, _SCHEMA) == {"city": "上\n海", "temperature": 21}


def test_schema_violation_returns_stable_error_code():
    """供应商保证 ≠ 应用层安全，第二层校验失败必须是明确错误码。"""
    raw = '{"city": "上海"}'

    with pytest.raises(GatewayError) as excinfo:
        parse_structured_output(raw, _SCHEMA)

    assert excinfo.value.code is ErrorCode.OUTPUT_SCHEMA_INVALID
    assert "temperature" in excinfo.value.message


def test_unparseable_output_returns_stable_error_code():
    with pytest.raises(GatewayError) as excinfo:
        parse_structured_output("完全不是 JSON 的一段话", _SCHEMA)

    assert excinfo.value.code is ErrorCode.OUTPUT_SCHEMA_INVALID


def test_repair_attempts_are_bounded():
    """修复次数必须有上限——不能无限尝试。"""
    calls: list[int] = []

    def never_valid(_value):
        calls.append(1)
        raise SchemaValidationError(["总是不通过"])

    with pytest.raises(StructuredRepairError) as excinfo:
        validate_with_repair('{"a": 1}', never_valid, max_attempts=2)

    # 首次 + 2 次修复 = 最多 3 次，不多不少。
    assert len(calls) == 3
    assert "3 次" in str(excinfo.value)


def test_max_attempts_one_still_fails_fast():
    calls: list[int] = []

    def never_valid(_value):
        calls.append(1)
        raise SchemaValidationError(["总是不通过"])

    with pytest.raises(StructuredRepairError):
        validate_with_repair('{"a": 1}', never_valid, max_attempts=1)

    assert len(calls) == 2


def test_no_schema_means_no_structural_check():
    assert parse_structured_output('{"anything": true}', None) == {"anything": True}


# --------------------------------------------------------------------------
# 流式结构化输出
# --------------------------------------------------------------------------


def test_streaming_validator_parses_incrementally():
    """需求：流式 JSON 要增量解析，不能等全部 chunk 到齐。"""
    validator = StructuredStreamValidator(_SCHEMA)

    assert validator.feed('{"city": "上')["city"] == "上"
    assert validator.feed('海", "temperature": 21')["city"] == "上海"


def test_streaming_validator_defers_schema_check_to_finish():
    """部分 JSON 无法做 schema 校验，delta 阶段不得因此报错。"""
    validator = StructuredStreamValidator(_SCHEMA)

    # 中途只到达一半（缺 temperature 且 JSON 尚未闭合），delta 阶段不能抛。
    validator.feed('{"city": "上海"')
    validator.feed(', "temperature": 21}')

    assert validator.finish()["city"] == "上海"


def test_streaming_validator_finish_rejects_invalid_payload():
    validator = StructuredStreamValidator(_SCHEMA)
    validator.feed('{"city": "上海"}')

    with pytest.raises(GatewayError) as excinfo:
        validator.finish()

    assert excinfo.value.code is ErrorCode.OUTPUT_SCHEMA_INVALID


def test_streaming_validator_never_raises_on_garbage_deltas():
    validator = StructuredStreamValidator(_SCHEMA)

    for chunk in ["", "{", "not json", "]", "}}"]:
        validator.feed(chunk)  # 不得抛异常
