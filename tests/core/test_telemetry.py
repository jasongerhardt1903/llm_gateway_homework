"""遥测记录测试。

对应需求"每次 LLM 调用都记录好以下信息"的 8 个维度：
关联 / Prompt / 用量 / 延迟 / 弹性 / 结果 / 错误 / 成本，
并要求"针对各个字段进行脱敏"。
"""

from __future__ import annotations

import pytest

from llm_gw.core.telemetry import CallRecord, LatencyBreakdown, PromptInfo, ResilienceInfo


def _record() -> CallRecord:
    return CallRecord(
        trace_id="trace-1",
        run_id="run-1",
        step_id="step-1",
        call_id="call-1",
        prompt=PromptInfo(name="chat", version="3", sha256="abc123", schema_version="1"),
        provider="deepseek",
        model="deepseek-chat",
        api="openai-compat",
        profile="fast-chat",
        latency=LatencyBreakdown(queue_ms=2.0, route_ms=1.0, ttft_ms=120.0, generation_ms=800.0, total_ms=923.0),
        resilience=ResilienceInfo(attempt=2, retry=1, fallback=False, timeout_budget_ms=30000),
        finish_reason="stop",
        terminal="done",
        output_valid=True,
        error_code=None,
        http_status=200,
        provider_request_id="req_1",
    )


def test_record_covers_all_required_dimensions():
    """8 个维度必须齐全，缺一不可。"""
    record = _record()

    # 关联
    assert (record.trace_id, record.run_id, record.step_id, record.call_id) == ("trace-1", "run-1", "step-1", "call-1")
    # Prompt
    assert record.prompt.name == "chat"
    assert record.prompt.version == "3"
    assert record.prompt.sha256 == "abc123"
    assert record.prompt.schema_version == "1"
    # 延迟
    assert record.latency.ttft_ms == 120.0
    assert record.latency.total_ms == 923.0
    # 弹性
    assert record.resilience.attempt == 2
    assert record.resilience.retry == 1
    assert record.resilience.fallback is False
    assert record.resilience.disposition == ""
    # 结果
    assert record.finish_reason == "stop"
    assert record.terminal == "done"
    # 错误
    assert record.http_status == 200
    assert record.provider_request_id == "req_1"


def test_usage_and_cost_are_recorded():
    """用量与成本维度：input/output/cached/reasoning 与各项成本。"""
    record = _record()
    record.usage.input = 1000
    record.usage.output = 500
    record.usage.cache_read = 200
    record.usage.reasoning = 300

    record.cost.input = 0.001
    record.cost.output = 0.002
    record.cost.total = 0.003

    payload = record.to_dict()

    assert payload["usage"]["input"] == 1000
    assert payload["usage"]["output"] == 500
    assert payload["usage"]["cache_read"] == 200
    assert payload["usage"]["reasoning"] == 300
    assert payload["cost"]["total"] == pytest.approx(0.003)


def test_to_dict_is_json_serializable_and_flat_enough_for_sqlite():
    """记录需能落库与返回给 Web，必须是可 JSON 序列化的纯结构。"""
    import json

    payload = _record().to_dict()

    assert isinstance(payload, dict)
    # 关联字段与延迟字段需在顶层，便于建索引与 SQL 聚合。
    assert payload["trace_id"] == "trace-1"
    assert payload["ttft_ms"] == 120.0
    assert payload["total_ms"] == 923.0
    json.dumps(payload)  # 不得抛异常


def test_record_redacts_credentials_everywhere():
    """脱敏必须作用于整个记录，含 prompt 与错误体。"""
    record = _record()
    record.error_message = "auth failed for Bearer sk-1234567890abcdefghijklmnop"
    record.metadata = {"api_key": "sk-abcdefghijklmnopqrstuvwxyz", "region": "cn"}

    payload = record.to_dict()

    assert "sk-1234567890abcdefghijklmnop" not in payload["error_message"]
    assert payload["metadata"]["api_key"] == "***"
    assert payload["metadata"]["region"] == "cn"


def test_terminal_state_is_restricted_to_single_terminals():
    """终态字段只允许 done / error / cancelled 三种。"""
    record = _record()
    record.terminal = "error"
    assert record.to_dict()["terminal"] == "error"

    record.terminal = "weird"
    with pytest.raises(ValueError):
        record.to_dict()
