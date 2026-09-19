"""JSON 修复与增量解析测试。

对应需求：
- 流式 JSON 需要增量解析，不能等全部 chunk 到齐再解析。
- 输出修复要有边界（有限次尝试），失败要返回明确错误而非静默吞掉。
"""

from __future__ import annotations

import json

import pytest

from llm_gw.core.json_utils import (
    extract_json,
    parse_json_with_repair,
    parse_streaming_json,
    repair_json,
)


# --------------------------------------------------------------------------
# repair_json
# --------------------------------------------------------------------------


def test_repair_escapes_raw_control_characters_inside_strings():
    """字符串内的裸换行/制表符必须转义，否则 JSON 无法解析。"""
    broken = '{"text": "line1\nline2\ttabbed"}'

    repaired = repair_json(broken)

    assert json.loads(repaired)["text"] == "line1\nline2\ttabbed"


def test_repair_doubles_invalid_backslash_escapes():
    """非法转义（如 \\d）需补成合法转义，而不是丢弃内容。"""
    # 两个转义都是非法的（\d、\x），必须原样保留字符内容。
    # 注意不能用 \n 之类的合法转义做样例：那本来就会解析成控制字符。
    broken = r'{"path": "C:\data\xfiles"}'

    repaired = repair_json(broken)

    assert json.loads(repaired)["path"] == r"C:\data\xfiles"


def test_repair_preserves_valid_escapes_and_unicode():
    """合法转义与 \\uXXXX 必须原样保留，不得二次转义。"""
    valid = '{"a": "quote\\"tab\\t", "b": "\\u4e2d\\u6587"}'

    assert repair_json(valid) == valid
    assert json.loads(repair_json(valid))["b"] == "中文"


def test_repair_leaves_valid_json_untouched():
    """合法 JSON 不得被修改。"""
    valid = '{"a": 1, "b": [1, 2, {"c": null}], "d": true}'
    assert repair_json(valid) == valid


def test_repair_handles_trailing_backslash():
    """字符串以裸反斜杠结尾时需补成合法转义。"""
    repaired = repair_json('{"a": "x\\')
    # 允许仍不完整，但不得抛异常，且结果必须是字符串。
    assert isinstance(repaired, str)


# --------------------------------------------------------------------------
# parse_json_with_repair
# --------------------------------------------------------------------------


def test_parse_json_with_repair_recovers_broken_payload():
    """坏 JSON 经修复后可解析。"""
    parsed = parse_json_with_repair('{"text": "a\nb"}')
    assert parsed == {"text": "a\nb"}


def test_parse_json_with_repair_raises_on_unrecoverable_input():
    """无法修复时必须抛明确错误，不能静默返回空对象。"""
    with pytest.raises(json.JSONDecodeError):
        parse_json_with_repair("this is not json at all")


# --------------------------------------------------------------------------
# parse_streaming_json
# --------------------------------------------------------------------------


def test_parse_streaming_json_returns_complete_object_unchanged():
    """完整 JSON 直接解析。"""
    assert parse_streaming_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parse_streaming_json_handles_empty_input():
    """空输入返回空对象，供流式增量调用方直接使用。"""
    assert parse_streaming_json("") == {}
    assert parse_streaming_json(None) == {}
    assert parse_streaming_json("   ") == {}


def test_parse_streaming_json_recovers_closed_prefix():
    """增量解析：只到一半的 JSON 也要能产出已到达的字段。"""
    partial = '{"name": "weather", "args": {"city": "Bei'

    parsed = parse_streaming_json(partial)

    assert parsed["name"] == "weather"


def test_parse_streaming_json_recovers_nested_partial_object():
    """嵌套对象未闭合时，已完成的键值应保留。"""
    partial = '{"a": 1, "b": {"c": 2'

    parsed = parse_streaming_json(partial)

    assert parsed["a"] == 1
    assert parsed["b"]["c"] == 2


def test_parse_streaming_json_never_raises():
    """增量解析处于热路径，任何输入都不得抛异常。"""
    for garbage in ['{"a"', "{{{{", '{"a": "unterminated', "]", "null", "[1,2"]:
        assert isinstance(parse_streaming_json(garbage), (dict, list))


# --------------------------------------------------------------------------
# extract_json
# --------------------------------------------------------------------------


def test_extract_json_strips_markdown_fence():
    """模型常把 JSON 包在 ```json 围栏里，需剥离。"""
    text = 'Here you go:\n```json\n{"a": 1}\n```\nDone.'

    assert json.loads(extract_json(text)) == {"a": 1}


def test_extract_json_finds_bare_object_inside_prose():
    """无围栏时，从散文里截出第一个完整 JSON 对象。"""
    text = 'The result is {"a": 1, "b": [2, 3]} as requested.'

    assert json.loads(extract_json(text)) == {"a": 1, "b": [2, 3]}


def test_extract_json_raises_when_no_json_present():
    """没有 JSON 时抛明确错误。"""
    with pytest.raises(ValueError):
        extract_json("no structured output here")
