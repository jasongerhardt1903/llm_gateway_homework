"""路由测试：能力匹配、静态优先、主备顺序、拒绝原因。

路由是纯决策逻辑，因此这些测试不需要任何网络与 mock transport：给注册表喂
模型，给任务喂特征，断言 :class:`Decision`。
"""

from __future__ import annotations

from llm_gw.adapter.base import Adapter
from llm_gw.core.errors import ErrorCode
from llm_gw.core.messages import AssistantMessage, Capabilities, CostRates, Model
from llm_gw.core.schema import validate_schema
from llm_gw.harness.decisions import decision_for, should_auto_retry, should_fallback
from llm_gw.harness.retry import RetryPolicy
from llm_gw.router.profile import GwProfile, ProfileModelRef
from llm_gw.router.registry import CapabilityRegistry
from llm_gw.router.router import Router
from llm_gw.router.rules import REJECT_CAPABILITY, REJECT_UNAVAILABLE, route
from llm_gw.util.clock import FakeClock


def _model(
    model_id: str,
    *,
    provider: str = "openai",
    cost_in: float = 1.0,
    capabilities: Capabilities | None = None,
) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url="https://mock.local/v1",
        cost=CostRates(input=cost_in, output=cost_in * 2),
        capabilities=capabilities or Capabilities(streaming=True, sse=True),
    )


def _task(*, profile: str | None = None, **input_overrides):
    payload = {
        "task_id": "t-1",
        "input": {"messages": [{"role": "user", "content": "hi"}], **input_overrides},
    }
    if profile is not None:
        payload["profile"] = profile
    return validate_schema(payload)


def _registry(*models: Model) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register_all(list(models))
    return registry


def _static_profile(name: str, order: list[str]) -> GwProfile:
    """按显式优先顺序造一个静态 profile。"""
    return GwProfile(
        name=name,
        models=[ProfileModelRef(label) for label in order],
        route_mode="static",
        static_order=list(order),
    )


# --------------------------------------------------------------------------
# 静态优先
# --------------------------------------------------------------------------


def test_static_route_is_used_in_configured_order():
    a, b = _model("a"), _model("b")
    registry = _registry(a, b)
    registry.set_profile(_static_profile("smart", ["openai/b", "openai/a"]))

    decision = route(_task(profile="smart"), registry)

    assert decision.primary.label() == "openai/b"
    assert decision.backup.label() == "openai/a"
    assert "静态路由" in decision.reason


def test_static_order_is_not_reordered_by_cost():
    """静态路由是运维显式指定的主备顺序，动态打分不得改写它。"""
    cheap, expensive = _model("cheap", cost_in=0.1), _model("expensive", cost_in=99.0)
    registry = _registry(cheap, expensive)
    registry.set_profile(_static_profile("smart", ["openai/expensive", "openai/cheap"]))

    decision = route(_task(profile="smart"), registry)

    assert decision.primary.label() == "openai/expensive"
    assert decision.backup.label() == "openai/cheap"


def test_static_order_skips_unknown_labels():
    """静态顺序引用了不存在的标签时跳过——配置错误不应让整个请求失败。"""
    a, b = _model("a"), _model("b")
    registry = _registry(a, b)
    registry.set_profile(_static_profile("smart", ["openai/ghost", "openai/a", "openai/b"]))

    decision = route(_task(profile="smart"), registry)

    assert decision.primary.label() == "openai/a"
    assert decision.backup.label() == "openai/b"


# --------------------------------------------------------------------------
# 动态选择
# --------------------------------------------------------------------------


def test_dynamic_select_prefers_cheaper_model():
    expensive, cheap = _model("expensive", cost_in=5.0), _model("cheap", cost_in=0.5)
    registry = _registry(expensive, cheap)

    decision = route(_task(), registry)

    assert decision.primary.label() == "openai/cheap"
    assert decision.backup.label() == "openai/expensive"


def test_dynamic_select_prefers_lower_usage_ratio():
    """消费比低者优先——避免把流量全压在一个模型上。"""
    a, b = _model("a", cost_in=1.0), _model("b", cost_in=1.0)
    registry = _registry(a, b)
    registry.quota = {"openai/a": 100, "openai/b": 100}
    registry.record_usage(a, 90)
    registry.record_usage(b, 5)

    decision = route(_task(), registry)

    assert decision.primary.label() == "openai/b"


# --------------------------------------------------------------------------
# 能力匹配与拒绝原因
# --------------------------------------------------------------------------


def test_missing_tools_capability_is_rejected_with_reason():
    with_tools = _model("tools", capabilities=Capabilities(streaming=True, sse=True, tools=True))
    without = _model("plain")
    registry = _registry(with_tools, without)

    decision = route(_task(tools=[{"name": "get_weather"}]), registry)

    assert decision.primary.label() == "openai/tools"
    reasons = {model.label(): reason for model, reason in decision.rejected}
    assert reasons["openai/plain"].startswith(REJECT_CAPABILITY)
    assert "tools" in reasons["openai/plain"]


def test_missing_json_schema_capability_is_rejected():
    strict = _model("strict", capabilities=Capabilities(streaming=True, sse=True, json_schema=True))
    loose = _model("loose")
    registry = _registry(strict, loose)

    decision = route(_task(response_schema={"type": "object"}), registry)

    assert decision.primary.label() == "openai/strict"
    assert all(model.label() != "openai/loose" for model in decision.candidates)


def test_vision_requirement_filters_models():
    seeing = _model("seeing", capabilities=Capabilities(streaming=True, sse=True, vision=True))
    blind = _model("blind")
    registry = _registry(seeing, blind)

    task = validate_schema(
        {
            "task_id": "t-1",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "image", "data": "AAAA", "mime_type": "image/png"}],
                    }
                ]
            },
        }
    )

    decision = route(task, registry)

    assert decision.primary.label() == "openai/seeing"
    assert [model.label() for model in decision.candidates] == ["openai/seeing"]


def test_unavailable_model_is_rejected_with_reason():
    a, b = _model("a"), _model("b")
    registry = _registry(a, b)
    registry.mark_unavailable(a)

    decision = route(_task(), registry)

    assert decision.primary.label() == "openai/b"
    reasons = {model.label(): reason for model, reason in decision.rejected}
    assert reasons["openai/a"] == REJECT_UNAVAILABLE


def test_no_candidate_marks_decision_not_ok():
    registry = _registry(_model("plain"))

    decision = route(_task(tools=[{"name": "get_weather"}]), registry)

    assert decision.ok is False
    assert decision.primary is None
    assert decision.reason == "全部候选被拒"


# --------------------------------------------------------------------------
# 决策表
# --------------------------------------------------------------------------


def test_decision_table_matches_requirement():
    assert decision_for(ErrorCode.AUTH_INVALID).value == "never"
    assert decision_for(ErrorCode.REQUEST_INVALID).value == "never"
    assert decision_for(ErrorCode.CONTENT_REFUSED).value == "no_bypass"
    assert decision_for(ErrorCode.STREAM_INTERRUPTED_AFTER_DATA).value == "no_blind_regenerate"
    assert decision_for(ErrorCode.OUTPUT_SCHEMA_INVALID).value == "limited_repair"


def test_auto_retry_and_fallback_flags():
    assert should_auto_retry(ErrorCode.CONN_FAILED)
    assert should_auto_retry(ErrorCode.RATE_LIMITED)
    assert not should_auto_retry(ErrorCode.AUTH_INVALID)
    assert not should_auto_retry(ErrorCode.CONTENT_REFUSED)

    assert should_fallback(ErrorCode.UPSTREAM_OVERLOADED)
    assert should_fallback(ErrorCode.QUEUE_REJECTED)
    assert not should_fallback(ErrorCode.AUTH_INVALID)


# --------------------------------------------------------------------------
# 执行与降级
# --------------------------------------------------------------------------


class _FakeAdapter(Adapter):
    """不联网的 adapter：按脚本返回消息或抛异常。"""

    api = "fake"

    def __init__(self, script: list) -> None:
        super().__init__(client=None)  # type: ignore[arg-type]
        self.script = list(script)
        self.calls: list[str] = []

    def build_request(self, model, task, options=None, *, stream=True):  # pragma: no cover
        raise NotImplementedError

    def parse_response(self, body, model):  # pragma: no cover
        raise NotImplementedError

    def feed(self, event, assembler):  # pragma: no cover
        raise NotImplementedError

    async def complete(self, model, task, options=None):
        self.calls.append(model.label())
        item = self.script.pop(0) if self.script else AssistantMessage()
        if isinstance(item, Exception):
            raise item
        return item


def _router_with(registry: CapabilityRegistry, adapters: dict[str, _FakeAdapter]) -> Router:
    return Router(
        registry,
        adapter_for=lambda model: adapters[model.label()],
        retry_policy=RetryPolicy(enabled=False),
        clock=FakeClock(),
    )


async def test_execute_uses_primary_when_successful():
    a, b = _model("a"), _model("b")
    registry = _registry(a, b)
    registry.set_profile(_static_profile("smart", ["openai/a", "openai/b"]))
    adapters = {"openai/a": _FakeAdapter([AssistantMessage()]), "openai/b": _FakeAdapter([])}

    message = await _router_with(registry, adapters).execute(_task(profile="smart"))

    assert message.stop_reason == "pending"
    assert adapters["openai/a"].calls == ["openai/a"]
    assert adapters["openai/b"].calls == []


async def test_execute_falls_back_to_backup_on_transient_error():
    a, b = _model("a"), _model("b")
    registry = _registry(a, b)
    registry.set_profile(_static_profile("smart", ["openai/a", "openai/b"]))
    transient = AssistantMessage(stop_reason="error", error_message="503 overloaded")
    adapters = {
        # profile 默认允许重试 3 次，因此主模型要一直失败才会走到降级。
        "openai/a": _FakeAdapter([transient] * 4),
        "openai/b": _FakeAdapter([AssistantMessage()]),
    }
    fallbacks: list[tuple[str, str, str]] = []

    message = await _router_with(registry, adapters).execute(
        _task(profile="smart"),
        on_fallback=lambda src, dst, err: fallbacks.append((src.label(), dst.label(), err)),
    )

    assert message.stop_reason == "pending"
    assert adapters["openai/a"].calls == ["openai/a"] * 4
    assert adapters["openai/b"].calls == ["openai/b"]
    assert fallbacks == [("openai/a", "openai/b", "503 overloaded")]


async def test_execute_does_not_fallback_on_non_retryable_error():
    """认证失败换模型也不会变好，降级只是浪费。"""
    a, b = _model("a"), _model("b")
    registry = _registry(a, b)
    registry.set_profile(_static_profile("smart", ["openai/a", "openai/b"]))
    adapters = {
        "openai/a": _FakeAdapter([AssistantMessage(stop_reason="error", error_message="insufficient_quota")]),
        "openai/b": _FakeAdapter([AssistantMessage()]),
    }

    message = await _router_with(registry, adapters).execute(_task(profile="smart"))

    assert message.stop_reason == "error"
    assert adapters["openai/b"].calls == []


async def test_execute_returns_route_error_when_no_candidate():
    registry = _registry(_model("plain"))
    router = _router_with(registry, {})

    message = await router.execute(_task(tools=[{"name": "get_weather"}]))

    assert message.stop_reason == "error"
    assert "ROUTE_NO_CANDIDATE" in (message.error_message or "")


async def test_execute_records_usage_on_success():
    a = _model("a")
    registry = _registry(a)
    adapters = {"openai/a": _FakeAdapter([AssistantMessage()])}

    await _router_with(registry, adapters).execute(_task())

    assert registry.usage(a) == 1