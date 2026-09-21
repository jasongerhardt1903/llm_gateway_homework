"""Web API 的请求/响应模型。

与内部类型分开定义：Web 的字段名与校验规则属于**对外契约**，不应随内部
重构而变化。这里负责把 HTTP JSON 与内部 :class:`Model` / :class:`GwProfile` 互转。

两条安全约定：

* ``ModelPayload.api_key`` **只写不回显**——``model_to_payload`` 一律把它置为
  ``None``，只回显 ``api_key_set`` 这个布尔量。
* ``api_key`` 为 ``None`` 表示"不修改已有密钥"；显式传空串才表示清除。

Chat 页不再走控制台自己的请求契约（需求：管理与交互层功能第 7 条"Chat 模仿一个简单的
后端 agent Loop，按照后端 agent 需要遵守的 schema 和 gateway 沟通"）。因此这里
**没有** Chat 专用 payload：Chat 页直接以 :class:`Task` 的 schema 调 ``/v1/tasks:stream``。

版本：0.3.0
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..core.advanced import THINKING_MODES, AdvancedConfig
from ..core.messages import Capabilities, CostRates, Model
from ..router.profile import ROUTE_MODES, GwProfile, ProfileModelRef

__all__ = [
    "CapabilitiesPayload",
    "CostPayload",
    "AdvancedPayload",
    "ModelPayload",
    "ProfileModelRefPayload",
    "ProfilePayload",
    "AgentPasswordPayload",
    "model_to_payload",
    "model_from_payload",
    "profile_to_payload",
    "profile_from_payload",
]


class CapabilitiesPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sse: bool = True
    streaming: bool = True
    tools: bool = False
    json_schema: bool = False
    vision: bool = False
    reasoning: bool = False


class CostPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


class AdvancedPayload(BaseModel):
    """模型高级配置项（需求第 30 行）。

    全部默认 ``None`` = "不发送该字段"。``None`` 与 ``0`` 语义不同，因此不能用
    ``0`` 表示"未配置"。
    """

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=0)
    thinking_mode: str = "default"
    max_tool_rounds: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=1)

    def to_config(self) -> AdvancedConfig:
        """转成内部类型；非法 ``thinking_mode`` 由内部校验统一抛错。"""
        return AdvancedConfig(**self.model_dump())

    @classmethod
    def from_config(cls, config: AdvancedConfig) -> "AdvancedPayload":
        return cls(**config.to_dict())


class ModelPayload(BaseModel):
    """模型定义。``provider`` 来自供应商下拉菜单，``api`` 决定用哪个协议。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = ""
    provider: str = Field(min_length=1)
    api: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    context_window: int = 0
    max_tokens: int = 0
    cost: CostPayload = Field(default_factory=CostPayload)
    capabilities: CapabilitiesPayload = Field(default_factory=CapabilitiesPayload)
    display_provider: str = ""
    #: 需求第 30 行："模型 tag"。
    tag: str = ""
    advanced: AdvancedPayload = Field(default_factory=AdvancedPayload)
    #: 只写字段：``None`` 表示不修改已有密钥，空串表示清除。
    api_key: str | None = None
    #: 只读字段：密钥是否已配置（不回显密钥本身）。
    api_key_set: bool = False


class ProfileModelRefPayload(BaseModel):
    """profile 内的一个模型引用。

    ``prefer_own_config`` 即需求第 31 行的"本模型配置优先于模版"勾选。
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    prefer_own_config: bool = False


class ProfilePayload(BaseModel):
    """gwprofile 定义（需求第 31 行）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    display_name: str = ""
    models: list[ProfileModelRefPayload] = Field(default_factory=list)
    template_enabled: bool = False
    template: AdvancedPayload = Field(default_factory=AdvancedPayload)
    route_mode: str = "dynamic"
    static_order: list[str] = Field(default_factory=list)
    retry_enabled: bool = True
    max_retries: int = Field(default=3, ge=0, le=10)

    def to_profile(self) -> GwProfile:
        if self.route_mode not in ROUTE_MODES:
            raise ValueError(f"route_mode 必须是 {' 或 '.join(ROUTE_MODES)}")
        if self.template.thinking_mode not in THINKING_MODES:
            raise ValueError(f"thinking_mode 必须是 {' 或 '.join(THINKING_MODES)}")
        return GwProfile(
            name=self.name,
            display_name=self.display_name,
            models=[
                ProfileModelRef(label=ref.label, prefer_own_config=ref.prefer_own_config)
                for ref in self.models
            ],
            template_enabled=self.template_enabled,
            template=self.template.to_config(),
            route_mode=self.route_mode,
            static_order=list(self.static_order),
            retry_enabled=self.retry_enabled,
            max_retries=self.max_retries,
        )

    @classmethod
    def from_profile(cls, profile: GwProfile) -> "ProfilePayload":
        return cls(
            name=profile.name,
            display_name=profile.display_name,
            models=[
                ProfileModelRefPayload(label=ref.label, prefer_own_config=ref.prefer_own_config)
                for ref in profile.models
            ],
            template_enabled=profile.template_enabled,
            template=AdvancedPayload.from_config(profile.template),
            route_mode=profile.route_mode,
            static_order=list(profile.static_order),
            retry_enabled=profile.retry_enabled,
            max_retries=profile.max_retries,
        )


class AgentPasswordPayload(BaseModel):
    """agent 接口口令的网页配置（需求 Harness 层功能第 1 条）。

    ``password`` 允许为空串：空串与 ``None`` 都表示"清除口令、接口不再要求凭证"，
    否则一旦设过口令就再也关不掉。口令**只写不回显**——状态接口只回
    ``source``（env / console / none），不回口令本身。
    """

    model_config = ConfigDict(extra="forbid")

    password: str | None = None


def model_to_payload(model: Model) -> ModelPayload:
    """模型 → 对外 payload。

    ``api_key`` 强制为 ``None``：密钥只写不回显，避免它随一次 GET 泄漏到浏览器
    历史、代理日志或前端状态里。前端只需知道"有没有配"。
    """
    return ModelPayload(
        id=model.id,
        name=model.name,
        provider=model.provider,
        api=model.api,
        base_url=model.base_url,
        context_window=model.context_window,
        max_tokens=model.max_tokens,
        cost=CostPayload(
            input=model.cost.input,
            output=model.cost.output,
            cache_read=model.cost.cache_read,
            cache_write=model.cost.cache_write,
        ),
        capabilities=CapabilitiesPayload(**vars(model.capabilities)),
        display_provider=model.display_provider,
        tag=model.tag,
        advanced=AdvancedPayload.from_config(model.advanced),
        api_key=None,
        api_key_set=bool(model.api_key),
    )


def model_from_payload(payload: ModelPayload, *, api_key: str | None = None) -> Model:
    """payload → 模型。

    ``api_key`` 由调用方显式给出：新增时用 payload 的值，更新时要保留旧密钥
    （``payload.api_key is None``），这个判断需要旧模型，因此不放在这里。
    """
    return Model(
        id=payload.id,
        name=payload.name or payload.id,
        provider=payload.provider,
        api=payload.api,
        base_url=payload.base_url,
        context_window=payload.context_window,
        max_tokens=payload.max_tokens,
        cost=CostRates(
            input=payload.cost.input,
            output=payload.cost.output,
            cache_read=payload.cost.cache_read,
            cache_write=payload.cost.cache_write,
        ),
        capabilities=Capabilities(**payload.capabilities.model_dump()),
        display_provider=payload.display_provider or payload.provider,
        tag=payload.tag,
        advanced=payload.advanced.to_config(),
        api_key=api_key if api_key is not None else (payload.api_key or ""),
    )


def profile_to_payload(profile: GwProfile) -> ProfilePayload:
    return ProfilePayload.from_profile(profile)


def profile_from_payload(payload: ProfilePayload) -> GwProfile:
    return payload.to_profile()
