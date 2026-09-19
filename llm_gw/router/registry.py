"""能力注册表。

路由层需要知道"有哪些模型可用、各自支持什么能力、当前是否健康、消费了多少"。
这些信息集中在这里，使路由规则保持纯粹（只读注册表，不做 I/O）。

能力字段与 :class:`llm_gw.core.messages.Capabilities` 一一对应，因此
``task.required_capabilities()`` 的结果可以直接用来做匹配。

版本：0.2.0
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.messages import Capabilities, Model
from .profile import GwProfile

__all__ = ["CapabilityRegistry", "REQUIRED_FLAGS"]

#: 参与能力匹配的布尔标志。顺序固定，便于生成稳定的拒绝原因。
REQUIRED_FLAGS: tuple[str, ...] = ("sse", "streaming", "tools", "json_schema", "vision", "reasoning")


@dataclass
class CapabilityRegistry:
    """模型目录 + profile 编组 + 健康状态 + 消费计数。"""

    models: list[Model] = field(default_factory=list)
    #: profile 名 → profile 定义。需求第 31 行的 gwprofile 层即存放于此。
    profiles: dict[str, GwProfile] = field(default_factory=dict)
    #: 可选配额，用于计算"消费比"。缺省表示不限额。
    quota: dict[str, int] = field(default_factory=dict)
    #: 运行时状态，不随模型定义漂移。
    _unavailable: set[str] = field(default_factory=set, repr=False)
    _usage: dict[str, int] = field(default_factory=dict, repr=False)

    # -- 注册 --------------------------------------------------------------

    def register(self, model: Model) -> Model:
        """加入模型目录；重复标签以新定义覆盖。"""
        self.models = [existing for existing in self.models if existing.label() != model.label()]
        self.models.append(model)
        return model

    def register_all(self, models: list[Model]) -> None:
        for model in models:
            self.register(model)

    def set_profile(self, profile: GwProfile) -> GwProfile:
        """新增或整体替换一个 profile。"""
        self.profiles[profile.name] = profile
        return profile

    def remove_profile(self, name: str) -> None:
        self.profiles.pop(name, None)

    # -- 查询 --------------------------------------------------------------

    def get(self, label: str) -> Model | None:
        for model in self.models:
            if model.label() == label:
                return model
        return None

    def all_models(self) -> list[Model]:
        return list(self.models)

    def get_profile(self, name: str | None) -> GwProfile | None:
        """按名字取 profile；``None`` 或不存在都返回 ``None``。

        是否存在与"名字是否合法"由路由规则判定，注册表只负责查表。
        """
        if not name:
            return None
        return self.profiles.get(name)

    def profile_models(self, profile: GwProfile) -> list[Model]:
        """profile 内的模型，按 profile 声明的顺序。

        profile 引用了不存在的标签时跳过——配置错误不应让整个请求失败。
        """
        found = [model for label in profile.refs() if (model := self.get(label)) is not None]
        return found

    # -- 能力匹配 ----------------------------------------------------------

    @staticmethod
    def matches(model: Model, required: Capabilities) -> bool:
        """模型是否满足全部必需能力。"""
        return not CapabilityRegistry.missing_capabilities(model, required)

    @staticmethod
    def missing_capabilities(model: Model, required: Capabilities) -> list[str]:
        """列出缺失的能力名，供拒绝原因使用。"""
        return [
            flag
            for flag in REQUIRED_FLAGS
            if getattr(required, flag, False) and not getattr(model.capabilities, flag, False)
        ]

    # -- 健康与消费 --------------------------------------------------------

    def is_available(self, model: Model) -> bool:
        return model.label() not in self._unavailable

    def mark_unavailable(self, model: Model) -> None:
        self._unavailable.add(model.label())

    def mark_available(self, model: Model) -> None:
        self._unavailable.discard(model.label())

    def usage(self, model: Model) -> int:
        return self._usage.get(model.label(), 0)

    def record_usage(self, model: Model, amount: int = 1) -> None:
        self._usage[model.label()] = self.usage(model) + amount

    def usage_ratio(self, model: Model) -> float:
        """消费比：已消费 / 配额。未配置配额时为 0（不参与打分差异）。"""
        limit = self.quota.get(model.label())
        if not limit:
            return 0.0
        return self.usage(model) / limit
