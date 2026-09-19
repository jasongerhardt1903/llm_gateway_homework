"""统一类型与成本计算测试。

移植 pi 的 ``calculateCost``（``rates / 1e6 * tokens``，支持阶梯价），
对应需求中"成本：输入、输出、缓存及总估算成本"。
"""

from __future__ import annotations

import pytest

from llm_gw.core.messages import (
    Capabilities,
    CostRates,
    CostTier,
    Model,
    Usage,
    calculate_cost,
)


def _model(rates: CostRates, tiers: list[CostTier] | None = None) -> Model:
    return Model(
        id="m",
        name="M",
        api="openai-compat",
        provider="openai",
        base_url="https://example.test",
        context_window=128000,
        max_tokens=4096,
        cost=rates,
        tiers=tiers or [],
        capabilities=Capabilities(sse=True, streaming=True, tools=True, json_schema=True),
    )


def test_cost_is_rates_per_million_tokens():
    """成本 = 单价 / 1e6 × token 数。"""
    model = _model(CostRates(input=2.5, output=10.0, cache_read=1.25, cache_write=3.0))
    usage = Usage(input=1_000_000, output=1_000_000, cache_read=1_000_000, cache_write=1_000_000)

    cost = calculate_cost(model, usage)

    assert cost.input == pytest.approx(2.5)
    assert cost.output == pytest.approx(10.0)
    assert cost.cache_read == pytest.approx(1.25)
    assert cost.cache_write == pytest.approx(3.0)
    assert cost.total == pytest.approx(2.5 + 10.0 + 1.25 + 3.0)


def test_cost_scales_linearly_below_one_million():
    """不足百万 token 时按比例折算。"""
    model = _model(CostRates(input=3.0, output=15.0))
    usage = Usage(input=1000, output=2000)

    cost = calculate_cost(model, usage)

    assert cost.input == pytest.approx(3.0 * 1000 / 1_000_000)
    assert cost.output == pytest.approx(15.0 * 2000 / 1_000_000)


def test_cost_uses_highest_matching_tier_for_whole_request():
    """阶梯价：输入总量命中最高档时，整单按该档费率计算。"""
    base = CostRates(input=1.0, output=2.0)
    high = CostTier(input=4.0, output=8.0, input_tokens_above=200_000)
    model = _model(base, tiers=[high])

    below = calculate_cost(model, Usage(input=100_000, output=1000))
    assert below.input == pytest.approx(1.0 * 100_000 / 1_000_000)

    above = calculate_cost(model, Usage(input=250_000, output=1000))
    assert above.input == pytest.approx(4.0 * 250_000 / 1_000_000)
    assert above.output == pytest.approx(8.0 * 1000 / 1_000_000)


def test_tier_matching_includes_cache_tokens_in_input_total():
    """阶梯判定用的是 input + cache_read + cache_write 的总输入量。"""
    base = CostRates(input=1.0, output=2.0)
    high = CostTier(input=4.0, output=8.0, input_tokens_above=100_000)
    model = _model(base, tiers=[high])

    # 纯 input 未达阈值，但叠加缓存后超过，应命中高档。
    usage = Usage(input=60_000, cache_read=60_000, cache_write=0, output=1)
    cost = calculate_cost(model, usage)

    assert cost.input == pytest.approx(4.0 * 60_000 / 1_000_000)
    assert cost.cache_read == pytest.approx(base.cache_read * 60_000 / 1_000_000)


def test_cost_ignores_reasoning_tokens_separately():
    """reasoning tokens 是 output 的子集，不得重复计费。"""
    model = _model(CostRates(input=1.0, output=10.0))
    usage = Usage(input=0, output=1000, reasoning=800)

    cost = calculate_cost(model, usage)

    assert cost.output == pytest.approx(10.0 * 1000 / 1_000_000)
    assert cost.total == pytest.approx(cost.output)


def test_usage_total_tokens_defaults_to_input_plus_output():
    """total_tokens 未由供应商给出时，按 input + output 推导。"""
    usage = Usage(input=7, output=5)
    assert usage.effective_total_tokens() == 12

    explicit = Usage(input=7, output=5, total_tokens=99)
    assert explicit.effective_total_tokens() == 99


def test_capabilities_record_what_router_needs():
    """能力注册表所需字段：是否支持 SSE、流式输出、工具、JSON schema。"""
    caps = Capabilities(sse=True, streaming=True, tools=False, json_schema=True)
    assert caps.sse is True
    assert caps.streaming is True
    assert caps.tools is False
    assert caps.json_schema is True
