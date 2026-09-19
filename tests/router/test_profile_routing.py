"""profile 路由行为：作用域、静态顺序、模版生效、per-profile 重试与轮数护栏。

需求第 31 行把 profile 定义为路由的作用域与配置载体，本文件验证它真的在路由
决策里生效，而不只是 Web 界面上的一组字段。

与 ``test_router.py`` 的分工：那边验证既有路由规则（能力匹配、动态打分、降级），
这边验证 profile 这一层引入的新语义。
"""

from __future__ import annotations

import pytest

from llm_gw.adapter.base import Adapter, AdapterOptions
from llm_gw.core.advanced import AdvancedConfig
from llm_gw.core.errors import ErrorCode
from llm_gw.core.messages import AssistantMessage, Capabilities, CostRates, Model
from llm_gw.core.schema import validate_schema
from llm_gw.harness.retry import RetryPolicy
from llm_gw.router.profile import GwProfile, ProfileModelRef
from llm_gw.router.registry import CapabilityRegistry
from llm_gw.router.router import Router
from llm_gw.router.rules import route
from llm_gw.util.clock import FakeClock


def _model(
    model_id: str,
    *,
    cost_in: float = 1.0,
    advanced: AdvancedConfig | None = None,
) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider="openai",
        base_url="https://mock.local/v1",
        cost=CostRates(input=cost_in, output=cost_in * 2),
        capabilities=Capabilities(streaming=True, sse=True),
        advanced=advanced or AdvancedConfig(),
    )


def _task(*, profile: str | None = None, **input_overrides):
    payload: dict = {
        "task_id": "t-1",
        "input": {"messages": [{"role": "user", "content": "hi"}], **input_overrides},
    }
    if profile is not None:
        payload["profile"] = profile
    return validate_schema(payload)


def _registry(*models: Model, profile: GwProfile | None = None) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    registry.register_all(list(models))
    if profile is not None:
        registry.set_profile(profile)
    return registry


def _profile(name: str, labels: list[str], **overrides) -> GwProfile:
    """按标签列表造 profile；``static_order`` 缺省即标签顺序。"""
    return GwProfile(
        name=name,
        models=[ProfileModelRef(label) for label in labels],
        **overrides,
    )


# --------------------------------------------------------------------------
# 作用域：候选只来自 profile 内的模型
# --------------------------------------------------------------------------


def test_profile_scopes_candidates_to_its_models():
    """profile 外的模型不参与路由——这是 profile 存在的意义。"""
    inside, outside = _model("inside"), _model("outside", cost_in=0.01)
    registry = _registry(inside, outside, profile=_profile("prod", ["openai/inside"]))

    decision = route(_task(profile="prod"), registry)

    assert [model.label() for model in decision.candidates] == ["openai/inside"]
    assert decision.primary.label() == "openai/inside"


def test_decision_carries_resolved_profile():
    """决策里带上 profile，供执行阶段解析高级配置与重试策略。"""
    registry = _registry(_model("a"), profile=_profile("prod", ["openai/a"]))

    decision = route(_task(profile="prod"), registry)

    assert decision.profile is not None
    assert decision.profile.name == "prod"


def test_missing_profile_yields_no_candidate():
    """显式指定了不存在的 profile 属于配置错误，不能悄悄退化成"用全部模型"。"""
    registry = _registry(_model("a"), profile=_profile("prod", ["openai/a"]))

    decision = route(_task(profile="typo"), registry)

    assert decision.ok is False
    assert decision.primary is None


def test_no_profiles_configured_falls_back_to_global_pool():
    """一个 profile 都没配时用全部模型，保证开箱即可用，不必先配 profile。"""
    a, b = _model("a"), _model("b")

    decision = route(_task(), _registry(a, b))

    assert {model.label() for model in decision.candidates} == {"openai/a", "openai/b"}


def test_default_profile_is_used_when_task_omits_it():
    """未指定 profile 时走 default，让"没配 profile"与"配了 default"同一条路径。"""
    inside, outside = _model("inside"), _model("outside")
    registry = _registry(inside, outside, profile=_profile("default", ["openai/inside"]))

    decision = route(_task(), registry)

    assert [model.label() for model in decision.candidates] == ["openai/inside"]


# --------------------------------------------------------------------------
# 静态路由：profile 级单一优先顺序
# --------------------------------------------------------------------------


def test_static_profile_order_defines_primary_and_backup():
    a, b, c = _model("a", cost_in=0.1), _model("b", cost_in=5.0), _model("c", cost_in=9.0)
    registry = _registry(
        a,
        b,
        c,
        profile=_profile("prod", ["openai/a", "openai/b", "openai/c"], route_mode="static"),
    )

    decision = route(_task(profile="prod"), registry)

    assert decision.primary.label() == "openai/a"
    assert decision.backup.label() == "openai/b"


def test_static_order_beats_dynamic_scoring():
    """静态顺序是运维显式指定的主备顺序，动态打分（消费比/单价）不得改写它。"""
    cheap, expensive = _model("cheap", cost_in=0.1), _model("expensive", cost_in=99.0)
    registry = _registry(
        cheap,
        expensive,
        profile=_profile("prod", ["openai/cheap", "openai/expensive"], route_mode="static"),
    )

    decision = route(_task(profile="prod"), registry)

    assert decision.primary.label() == "openai/cheap"


def test_static_order_can_reverse_declaration_order():
    """static_order 独立于声明顺序：先声明的不一定先路由。"""
    a, b = _model("a"), _model("b")
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/a"), ProfileModelRef("openai/b")],
        route_mode="static",
        static_order=["openai/b", "openai/a"],
    )

    decision = route(_task(profile="prod"), _registry(a, b, profile=profile))

    assert decision.primary.label() == "openai/b"
    assert decision.backup.label() == "openai/a"


def test_static_order_skips_labels_not_in_profile():
    """顺序里写了 profile 外的标签时跳过，配置错误不应让整个请求失败。"""
    a = _model("a")
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/a")],
        route_mode="static",
        static_order=["openai/ghost", "openai/a"],
    )

    decision = route(_task(profile="prod"), _registry(a, profile=profile))

    assert decision.primary.label() == "openai/a"


def test_static_order_with_no_valid_label_falls_back_to_dynamic():
    """顺序里一个有效标签都没有时退化为动态选择，比"无候选"更有用。"""
    a, b = _model("a", cost_in=9.0), _model("b", cost_in=0.1)
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/a"), ProfileModelRef("openai/b")],
        route_mode="static",
        static_order=["openai/ghost"],
    )

    decision = route(_task(profile="prod"), _registry(a, b, profile=profile))

    assert decision.primary.label() == "openai/b"


def test_dynamic_profile_sorts_by_cost_within_scope():
    expensive, cheap = _model("expensive", cost_in=5.0), _model("cheap", cost_in=0.5)
    registry = _registry(
        expensive,
        cheap,
        profile=_profile("prod", ["openai/expensive", "openai/cheap"]),
    )

    decision = route(_task(profile="prod"), registry)

    assert decision.primary.label() == "openai/cheap"


def test_capability_filtering_still_applies_inside_profile():
    """profile 只缩小候选池，能力匹配照旧生效。"""
    plain, with_tools = _model("plain"), _model("tools")
    with_tools.capabilities = Capabilities(streaming=True, sse=True, tools=True)
    registry = _registry(plain, with_tools, profile=_profile("prod", ["openai/plain", "openai/tools"]))

    decision = route(_task(profile="prod", tools=[{"name": "get_weather"}]), registry)

    assert decision.primary.label() == "openai/tools"


# --------------------------------------------------------------------------
# 高级配置：路由层负责解析，adapter 只收到结果
# --------------------------------------------------------------------------


class _CapturingAdapter(Adapter):
    """记录每次调用收到的 AdapterOptions，用于断言高级配置的解析结果。"""

    api = "capturing"

    def __init__(self) -> None:
        super().__init__(client=None)  # type: ignore[arg-type]
        self.options: list[AdapterOptions] = []

    def build_request(self, model, task, options=None, *, stream=True):  # pragma: no cover
        raise NotImplementedError

    def parse_response(self, body, model):  # pragma: no cover
        raise NotImplementedError

    def feed(self, event, assembler):  # pragma: no cover
        raise NotImplementedError

    async def complete(self, model, task, options=None):
        self.options.append(options or AdapterOptions())
        return AssistantMessage()


def _router(registry: CapabilityRegistry, adapter: Adapter, **overrides) -> Router:
    return Router(
        registry,
        adapter_for=lambda model: adapter,
        options_for=lambda model, advanced: AdapterOptions(advanced=advanced),
        retry_policy=RetryPolicy(enabled=False),
        clock=FakeClock(),
        **overrides,
    )


async def test_router_passes_model_advanced_config_when_template_disabled():
    model = _model("a", advanced=AdvancedConfig(temperature=0.3))
    registry = _registry(model, profile=_profile("prod", ["openai/a"]))
    adapter = _CapturingAdapter()

    await _router(registry, adapter).execute(_task(profile="prod"))

    assert adapter.options[0].advanced.temperature == 0.3


async def test_router_passes_template_when_enabled_and_unchecked():
    """启用模版且未勾选"本模型配置优先"时，模型自身配置被忽略。"""
    model = _model("a", advanced=AdvancedConfig(temperature=0.3))
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/a", prefer_own_config=False)],
        template_enabled=True,
        template=AdvancedConfig(temperature=0.9),
    )
    adapter = _CapturingAdapter()

    await _router(_registry(model, profile=profile), adapter).execute(_task(profile="prod"))

    assert adapter.options[0].advanced.temperature == 0.9


async def test_router_passes_model_config_when_checked_against_template():
    model = _model("a", advanced=AdvancedConfig(temperature=0.3))
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/a", prefer_own_config=True)],
        template_enabled=True,
        template=AdvancedConfig(temperature=0.9),
    )
    adapter = _CapturingAdapter()

    await _router(_registry(model, profile=profile), adapter).execute(_task(profile="prod"))

    assert adapter.options[0].advanced.temperature == 0.3


# --------------------------------------------------------------------------
# per-profile 重试策略
# --------------------------------------------------------------------------


async def test_profile_retry_policy_overrides_router_default():
    """需求第 128 行：最大重试次数可在界面配置。它挂在 profile 上，因此要能覆盖默认。"""
    model = _model("a")
    profile = _profile("prod", ["openai/a"], retry_enabled=True, max_retries=7)
    registry = _registry(model, profile=profile)
    router = _router(registry, _CapturingAdapter())
    router.retry_policy = RetryPolicy(enabled=False, max_retries=1)

    policy = router.policy_for(route(_task(profile="prod"), registry).profile)

    assert policy.max_retries == 7
    assert policy.enabled is True


def test_policy_for_without_profile_falls_back_to_router_default():
    router = Router(CapabilityRegistry(), retry_policy=RetryPolicy(enabled=True, max_retries=2))

    assert router.policy_for(None).max_retries == 2


# --------------------------------------------------------------------------
# 工具调用轮数护栏
# --------------------------------------------------------------------------


def _task_with_tool_rounds(rounds: int, *, profile: str | None = None, **input_overrides):
    """构造一段包含 ``rounds`` 轮工具调用的历史。"""
    messages: list[dict] = [{"role": "user", "content": "hi"}]
    for index in range(rounds):
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "tool_call", "id": f"c{index}", "name": "lookup", "arguments": {}}],
            }
        )
        messages.append({"role": "tool", "content": "ok", "tool_call_id": f"c{index}"})
    payload: dict = {"task_id": "t-1", "input": {"messages": messages, **input_overrides}}
    if profile is not None:
        payload["profile"] = profile
    return validate_schema(payload)


def test_task_counts_tool_rounds():
    assert _task_with_tool_rounds(0).tool_rounds() == 0
    assert _task_with_tool_rounds(3).tool_rounds() == 3


async def test_tool_rounds_within_limit_passes():
    model = _model("a", advanced=AdvancedConfig(max_tool_rounds=5))
    registry = _registry(model, profile=_profile("prod", ["openai/a"]))
    adapter = _CapturingAdapter()

    message = await _router(registry, adapter).execute(_task_with_tool_rounds(2, ))

    assert message.stop_reason != "error"


async def test_tool_rounds_over_limit_is_rejected_before_calling_upstream():
    """护栏必须在调用上游之前生效，否则白白消耗一次配额。"""
    model = _model("a", advanced=AdvancedConfig(max_tool_rounds=1))
    registry = _registry(model, profile=_profile("prod", ["openai/a"]))
    adapter = _CapturingAdapter()

    message = await _router(registry, adapter).execute(_task_with_tool_rounds(3))

    assert message.stop_reason == "error"
    assert ErrorCode.TOOL_ROUNDS_EXCEEDED.value in (message.error_message or "")
    assert adapter.options == []


async def test_tool_rounds_unset_means_no_limit():
    model = _model("a")
    registry = _registry(model, profile=_profile("prod", ["openai/a"]))
    adapter = _CapturingAdapter()

    message = await _router(registry, adapter).execute(_task_with_tool_rounds(50))

    assert message.stop_reason != "error"


async def test_tool_rounds_guard_reads_template_value():
    """护栏阈值同样受模版影响：启用模版时以模版为准。"""
    model = _model("a", advanced=AdvancedConfig(max_tool_rounds=99))
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/a")],
        template_enabled=True,
        template=AdvancedConfig(max_tool_rounds=1),
    )
    adapter = _CapturingAdapter()

    message = await _router(_registry(model, profile=profile), adapter).execute(
        _task_with_tool_rounds(3, profile="prod")
    )

    assert message.stop_reason == "error"
    assert ErrorCode.TOOL_ROUNDS_EXCEEDED.value in (message.error_message or "")


@pytest.mark.parametrize("boundary", [1, 2])
async def test_tool_rounds_boundary_is_inclusive(boundary: int):
    """恰好等于上限时放行，只有超过才拒绝。"""
    model = _model("a", advanced=AdvancedConfig(max_tool_rounds=boundary))
    registry = _registry(model, profile=_profile("prod", ["openai/a"]))

    message = await _router(registry, _CapturingAdapter()).execute(_task_with_tool_rounds(boundary))

    assert message.stop_reason != "error"
