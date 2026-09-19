"""gwprofile 层测试：静态顺序解析与高级配置模版的生效规则。

需求第 31 行是这一层的唯一权威描述，本文件逐条对应：

* "静态路由写死各个模型的优先顺序，用逗号分隔" → :func:`parse_order`
* "如果启用模版，那么在 profile 内的模型，如果不勾选'本模型配置优先于模版'，
  则 LLMGW 在使用这个模型时会忽略模型的高级配置项而专用模版中的配置"
  → :func:`resolve_advanced` 的判定矩阵

这一层是纯数据与纯函数，不涉及网络、存储或事件流，因此测试不需要任何 mock。
"""

from __future__ import annotations

import pytest

from llm_gw.core.advanced import THINKING_MODES, AdvancedConfig
from llm_gw.core.messages import Capabilities, CostRates, Model
from llm_gw.router.profile import (
    GwProfile,
    ProfileModelRef,
    parse_order,
    resolve_advanced,
)


def _model(model_id: str = "gpt-4o-mini", *, advanced: AdvancedConfig | None = None) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider="openai",
        base_url="https://mock.local/v1",
        cost=CostRates(input=1.0, output=2.0),
        capabilities=Capabilities(streaming=True, sse=True),
        advanced=advanced or AdvancedConfig(),
    )


# --------------------------------------------------------------------------
# parse_order：逗号分隔的优先顺序
# --------------------------------------------------------------------------


def test_parse_order_splits_on_comma_and_trims_whitespace():
    assert parse_order(" openai/gpt-4o-mini ,  deepseek/deepseek-chat ") == [
        "openai/gpt-4o-mini",
        "deepseek/deepseek-chat",
    ]


def test_parse_order_drops_empty_items():
    """配置里常见的尾随逗号不应产生空标签，否则会污染候选列表。"""
    assert parse_order("a/b,,c/d,") == ["a/b", "c/d"]


def test_parse_order_keeps_first_occurrence_and_preserves_order():
    """重复标签保留首次出现的位置：顺序即主备顺序，不能因去重而被打乱。"""
    assert parse_order("a/b, c/d, a/b, e/f") == ["a/b", "c/d", "e/f"]


def test_parse_order_accepts_chinese_comma():
    """中文输入法下很容易打出全角逗号，静默失效比报错更难排查。"""
    assert parse_order("a/b，c/d") == ["a/b", "c/d"]


def test_parse_order_of_blank_text_is_empty():
    assert parse_order("   ") == []
    assert parse_order("") == []


# --------------------------------------------------------------------------
# AdvancedConfig：默认值与校验
# --------------------------------------------------------------------------


def test_advanced_config_defaults_leave_everything_to_provider():
    """None 表示"不发送该字段"，因此默认值必须全为 None，而不是 0 之类的具体值。"""
    config = AdvancedConfig()

    assert config.temperature is None
    assert config.top_p is None
    assert config.top_k is None
    assert config.max_tokens is None
    assert config.max_tool_rounds is None
    assert config.thinking_mode == "default"


def test_advanced_config_accepts_known_thinking_modes():
    for mode in THINKING_MODES:
        assert AdvancedConfig(thinking_mode=mode).thinking_mode == mode


def test_advanced_config_rejects_unknown_thinking_mode():
    with pytest.raises(ValueError, match="thinking_mode"):
        AdvancedConfig(thinking_mode="maybe")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("temperature", -0.1),
        ("temperature", 2.1),
        ("top_p", -0.1),
        ("top_p", 1.1),
        ("top_k", -1),
        ("max_tool_rounds", -1),
    ],
)
def test_advanced_config_rejects_out_of_range_values(field: str, value: float):
    """采样参数越界会被供应商拒绝，本地先拦住可以省掉一次无效上游调用。"""
    with pytest.raises(ValueError, match=field):
        AdvancedConfig(**{field: value})


def test_advanced_config_allows_boundary_values():
    config = AdvancedConfig(temperature=2.0, top_p=1.0, top_k=0, max_tool_rounds=0)

    assert config.temperature == 2.0
    assert config.top_p == 1.0
    assert config.top_k == 0
    assert config.max_tool_rounds == 0


# --------------------------------------------------------------------------
# GwProfile：成员与顺序
# --------------------------------------------------------------------------


def test_profile_refs_preserves_declaration_order():
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("a/1"), ProfileModelRef("b/2"), ProfileModelRef("c/3")],
    )

    assert profile.refs() == ["a/1", "b/2", "c/3"]


def test_profile_dynamic_mode_uses_declaration_order():
    """动态路由下候选池是 profile 内的全部模型，声明顺序即兜底顺序。"""
    profile = GwProfile(name="prod", models=[ProfileModelRef("a/1"), ProfileModelRef("b/2")])

    assert profile.route_mode == "dynamic"
    assert profile.order() == ["a/1", "b/2"]


def test_profile_static_mode_uses_static_order():
    """静态路由"写死各个模型的优先顺序"：即使声明顺序不同，也以 static_order 为准。"""
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("a/1"), ProfileModelRef("b/2"), ProfileModelRef("c/3")],
        route_mode="static",
        static_order=["c/3", "a/1"],
    )

    assert profile.order() == ["c/3", "a/1"]


def test_profile_static_mode_without_order_falls_back_to_declaration():
    """选了静态路由但没填顺序时，退化为声明顺序比"无候选"更有用。"""
    profile = GwProfile(name="prod", models=[ProfileModelRef("a/1")], route_mode="static")

    assert profile.order() == ["a/1"]


def test_profile_ref_for_returns_none_for_outsider():
    profile = GwProfile(name="prod", models=[ProfileModelRef("a/1")])

    assert profile.ref_for("a/1") is not None
    assert profile.ref_for("z/9") is None


def test_profile_has_model_checks_membership():
    profile = GwProfile(name="prod", models=[ProfileModelRef("a/1")])

    assert profile.has_model("a/1") is True
    assert profile.has_model("z/9") is False


def test_profile_rejects_unknown_route_mode():
    with pytest.raises(ValueError, match="route_mode"):
        GwProfile(name="prod", route_mode="random")


# --------------------------------------------------------------------------
# resolve_advanced：需求第 31 行的判定矩阵
# --------------------------------------------------------------------------


def test_resolve_without_profile_uses_model_config():
    model = _model(advanced=AdvancedConfig(temperature=0.3))

    assert resolve_advanced(model, None).temperature == 0.3


def test_resolve_with_template_disabled_uses_model_config():
    """模版未启用时，profile 存在也不影响模型自身配置。"""
    model = _model(advanced=AdvancedConfig(temperature=0.3))
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/gpt-4o-mini")],
        template_enabled=False,
        template=AdvancedConfig(temperature=0.9),
    )

    assert resolve_advanced(model, profile).temperature == 0.3


def test_resolve_with_template_enabled_and_unchecked_uses_template():
    """核心用例：启用模版 + 未勾选"本模型配置优先" → 忽略模型配置，专用模版。"""
    model = _model(advanced=AdvancedConfig(temperature=0.3, top_p=0.1))
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/gpt-4o-mini", prefer_own_config=False)],
        template_enabled=True,
        template=AdvancedConfig(temperature=0.9, top_p=0.95),
    )

    resolved = resolve_advanced(model, profile)

    assert resolved.temperature == 0.9
    assert resolved.top_p == 0.95


def test_resolve_with_template_enabled_and_checked_uses_model_config():
    """勾选"本模型配置优先于模版"后，模型自身配置重新生效。"""
    model = _model(advanced=AdvancedConfig(temperature=0.3))
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/gpt-4o-mini", prefer_own_config=True)],
        template_enabled=True,
        template=AdvancedConfig(temperature=0.9),
    )

    assert resolve_advanced(model, profile).temperature == 0.3


def test_resolve_leaves_outsider_model_untouched():
    """模版只作用于"profile 内的模型"，不在 profile 里的模型不应被套用模版。"""
    model = _model("gpt-4o", advanced=AdvancedConfig(temperature=0.3))
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/gpt-4o-mini")],
        template_enabled=True,
        template=AdvancedConfig(temperature=0.9),
    )

    assert resolve_advanced(model, profile).temperature == 0.3


def test_resolve_returns_model_config_object_when_not_templated():
    """未命中模版时直接返回模型自身对象，避免每次路由都产生一次拷贝。"""
    model = _model(advanced=AdvancedConfig(temperature=0.3))

    assert resolve_advanced(model, None) is model.advanced


def test_resolve_is_independent_of_thinking_mode_and_tool_rounds():
    """模版替换的是整份高级配置，不只是采样参数。"""
    model = _model(advanced=AdvancedConfig(thinking_mode="off", max_tool_rounds=8))
    profile = GwProfile(
        name="prod",
        models=[ProfileModelRef("openai/gpt-4o-mini")],
        template_enabled=True,
        template=AdvancedConfig(thinking_mode="on", max_tool_rounds=2),
    )

    resolved = resolve_advanced(model, profile)

    assert resolved.thinking_mode == "on"
    assert resolved.max_tool_rounds == 2
