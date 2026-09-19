"""gwprofile 层：模型编组、高级配置模版与 profile 级路由配置。

需求第 31 行："提供一个抽象的 gwprofile 层……这个 gwprofile 包括包含哪些定义的
模型。可以配置一个在 profile 内生效的统一模型高级配置项模版。如果启用模版，
那么在 profile 内的模型，如果不勾选'本模型配置优先于模版'，则 LLMGW 在使用这个
模型时会忽略模型的高级配置项而专用模版中的配置。gwprofile 层还提供路由配置。
路由有两种选项。1，动态路由，2，静态路由。静态路由写死各个模型的优先顺序，
用逗号分隔。"

profile 是**路由的作用域**：agent 在 task 里指定 profile，路由只在 profile 声明
的模型里选。这比"全局模型池 + 逐逻辑模型映射"更贴合需求，也让多租户/多环境
（dev、prod、按团队隔离）可以用同一份模型定义表达不同可见集。

本模块刻意保持为纯数据与纯函数：不碰 I/O、不碰事件流，因此可以被路由规则、
Web 层、存储层共同依赖而不引入循环。

版本：0.2.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core.advanced import AdvancedConfig
from ..core.messages import Model

__all__ = [
    "ROUTE_MODES",
    "DEFAULT_PROFILE",
    "AdvancedConfig",
    "ProfileModelRef",
    "GwProfile",
    "parse_order",
    "resolve_advanced",
]

#: 路由模式。需求第 31 行："路由有两种选项。1，动态路由，2，静态路由"。
ROUTE_MODES: tuple[str, ...] = ("dynamic", "static")

#: 未指定 profile 时使用的名字。让"没有配置 profile"和"配置了一个默认 profile"
#: 走同一条代码路径，避免路由层出现两套分支。
DEFAULT_PROFILE = "default"

#: 全角逗号在中文输入法下极易被误打，静默失效比直接报错更难排查，因此显式兼容。
_COMMA_VARIANTS = ("，", "、")


def parse_order(text: str) -> list[str]:
    """解析"逗号分隔的模型优先顺序"。

    规则：全角/半角逗号与顿号都作分隔符；去首尾空白；丢弃空项（尾随逗号很常见）；
    **保序去重**——顺序即主备顺序，重复项只保留首次出现的位置。
    """
    normalized = text
    for variant in _COMMA_VARIANTS:
        normalized = normalized.replace(variant, ",")

    ordered: list[str] = []
    for item in normalized.split(","):
        label = item.strip()
        if label and label not in ordered:
            ordered.append(label)
    return ordered


@dataclass
class ProfileModelRef:
    """profile 内的一个模型引用。

    ``prefer_own_config`` 即需求中的"本模型配置优先于模版"勾选：勾上后，即使
    profile 启用了模版，该模型也使用自己的高级配置项。
    """

    label: str
    prefer_own_config: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "prefer_own_config": self.prefer_own_config}


@dataclass
class GwProfile:
    """一组模型 + 一份统一高级配置模版 + 一份路由配置。"""

    name: str
    display_name: str = ""
    models: list[ProfileModelRef] = field(default_factory=list)
    template_enabled: bool = False
    template: AdvancedConfig = field(default_factory=AdvancedConfig)
    route_mode: str = "dynamic"
    #: ``route_mode="static"`` 时的优先顺序（主 → 备）。
    static_order: list[str] = field(default_factory=list)
    #: 需求第 128 行："最大重试次数可以在 web 界面配置。默认为 3。"
    retry_enabled: bool = True
    max_retries: int = 3

    def __post_init__(self) -> None:
        if self.route_mode not in ROUTE_MODES:
            raise ValueError(
                f"route_mode must be one of {', '.join(ROUTE_MODES)}, got {self.route_mode!r}"
            )
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {self.max_retries}")

    # -- 成员 --------------------------------------------------------------

    def refs(self) -> list[str]:
        """profile 内全部模型的标签，按声明顺序。"""
        return [ref.label for ref in self.models]

    def ref_for(self, label: str) -> ProfileModelRef | None:
        for ref in self.models:
            if ref.label == label:
                return ref
        return None

    def has_model(self, label: str) -> bool:
        return self.ref_for(label) is not None

    # -- 顺序 --------------------------------------------------------------

    def order(self) -> list[str]:
        """路由时使用的候选顺序。

        ``static`` 模式以 ``static_order`` 为准（需求："静态路由写死各个模型的优先
        顺序"）；选了 static 却没填顺序时退化为声明顺序——比"没有候选"更有用。
        """
        if self.route_mode == "static" and self.static_order:
            return list(self.static_order)
        return self.refs()

    def is_pinned(self) -> bool:
        """顺序是否来自显式配置（不应被动态打分改写）。"""
        return self.route_mode == "static" and bool(self.static_order)

    # -- 序列化 ------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "models": [ref.to_dict() for ref in self.models],
            "template_enabled": self.template_enabled,
            "template": self.template.to_dict(),
            "route_mode": self.route_mode,
            "static_order": list(self.static_order),
            "retry_enabled": self.retry_enabled,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GwProfile:
        return cls(
            name=data["name"],
            display_name=data.get("display_name", ""),
            models=[
                ProfileModelRef(label=item["label"], prefer_own_config=item.get("prefer_own_config", False))
                for item in data.get("models", [])
            ],
            template_enabled=data.get("template_enabled", False),
            template=AdvancedConfig.from_dict(data.get("template")),
            route_mode=data.get("route_mode", "dynamic"),
            static_order=list(data.get("static_order", [])),
            retry_enabled=data.get("retry_enabled", True),
            max_retries=data.get("max_retries", 3),
        )


def resolve_advanced(model: Model, profile: GwProfile | None) -> AdvancedConfig:
    """决定某模型在某 profile 下实际生效的高级配置。

    需求第 31 行的判定规则，逐字实现：

    | 模版 | 该模型勾选"本模型配置优先" | 生效配置        |
    | ---- | -------------------------- | --------------- |
    | 未启用 | —                        | 模型自身配置    |
    | 启用 | 否                         | **profile 模版**（忽略模型配置） |
    | 启用 | 是                         | 模型自身配置    |

    额外一条：模版只作用于"profile 内的模型"。不在 profile 里的模型不套用它的模版，
    否则一个 profile 的模版会悄悄影响别的 profile 的模型。

    未命中模版时直接返回模型自身的配置对象（不做拷贝），因为路由是热路径。
    """
    if profile is None or not profile.template_enabled:
        return model.advanced

    ref = profile.ref_for(model.label())
    if ref is None or ref.prefer_own_config:
        return model.advanced

    return profile.template
