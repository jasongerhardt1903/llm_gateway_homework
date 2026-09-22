"""路由规则：profile 定作用域 → 静态优先 → 动态筛选 → 主备。

需求第 31 行把 gwprofile 定为路由配置的载体，因此规则的入口是"先解析 profile"：

1. :func:`resolve_profile` —— 取 task 指定的 profile；未指定时取 ``default``。
   显式指定了不存在的 profile 属于配置错误，**不能**悄悄退化成"用全部模型"。
2. :func:`apply_static` —— profile 内按 ``order()`` 取候选（静态模式顺序即主备
   顺序）；一个 profile 都没有时退回全局模型池，保证开箱即用。
3. :func:`dynamic_select` —— 按能力注册表过滤（能力不匹配 / 不可用），
   未固定顺序时再按"消费比 → 单价"排序。
4. :func:`build_primary_backup` —— 取前两名作为主、备（``Decision.candidates``
   则是**整条**候选链，降级时按它依次往下试，不止两个）。

此外 task 还可以带 ``model`` 字段**点名**某个模型（需求第 27 行"根据请求中的 model
字段动态路由"）：点名只把该模型提到首位，其余候选仍作备用，且不允许点名 profile 池子
之外的模型。

每次路由都产出 :class:`Decision`，其中 ``rejected`` 逐条记录被拒模型与原因——
路由"为什么选它"和"为什么不选它"同样重要，否则线上问题无从排查。

版本：0.2.0
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.messages import Model
from ..core.schema import Task
from .profile import DEFAULT_PROFILE, GwProfile
from .registry import CapabilityRegistry

__all__ = [
    "REJECT_CAPABILITY",
    "REJECT_UNAVAILABLE",
    "Decision",
    "StaticSelection",
    "resolve_profile",
    "apply_static",
    "dynamic_select",
    "build_primary_backup",
    "route",
]

#: 拒绝原因前缀，测试按前缀断言。
REJECT_CAPABILITY = "能力不匹配"
REJECT_UNAVAILABLE = "模型当前不可用"


@dataclass
class StaticSelection:
    """静态阶段的产物。``pinned`` 表示顺序来自显式配置，不应被动态打分改写。"""

    candidates: list[Model]
    pinned: bool
    reason: str


@dataclass
class Decision:
    """一次路由的完整决策记录。"""

    #: 生效的 profile；为 ``None`` 表示走全局模型池（一个 profile 都没配）。
    profile: GwProfile | None = None
    candidates: list[Model] = field(default_factory=list)
    rejected: list[tuple[Model, str]] = field(default_factory=list)
    primary: Model | None = None
    backup: Model | None = None
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.primary is not None


def resolve_profile(task: Task, registry: CapabilityRegistry) -> GwProfile | None:
    """解析 task 要使用的 profile。

    未指定时回退到 ``default``：这样"没配 profile"和"配了一个 default profile"
    走同一条代码路径，路由层不必维护两套分支。
    """
    name = task.profile or DEFAULT_PROFILE
    return registry.get_profile(name)


def _profile_candidates(profile: GwProfile, registry: CapabilityRegistry) -> list[Model]:
    """按 profile 的 ``order()`` 取模型，顺序即主备顺序。

    profile 引用了不存在的标签时跳过——配置错误不应让整个请求失败。
    """
    return [model for label in profile.order() if (model := registry.get(label)) is not None]


def apply_static(profile: GwProfile | None, registry: CapabilityRegistry) -> StaticSelection:
    """第一步：确定候选池。

    - 有 profile：候选只来自 profile 内的模型（这是 profile 存在的意义）；
      静态模式保留显式顺序（``pinned=True``），动态模式交由打分排序。
    - 无 profile：用全部模型作为候选池。

    选了静态路由却一个有效标签都对不上时退化为动态选择——比"无候选"更有用。
    """
    if profile is None:
        return StaticSelection(
            candidates=registry.all_models(),
            pinned=False,
            reason="未配置 profile，使用全局模型池",
        )

    ordered = _profile_candidates(profile, registry)
    if not ordered:
        return StaticSelection(
            candidates=registry.profile_models(profile),
            pinned=False,
            reason=f"profile {profile.name} 的静态顺序无有效模型，退化为动态路由",
        )
    if profile.is_pinned():
        return StaticSelection(
            candidates=ordered,
            pinned=True,
            reason=f"命中 profile {profile.name} 的静态路由",
        )
    return StaticSelection(
        candidates=ordered,
        pinned=False,
        reason=f"profile {profile.name} 使用动态路由",
    )


def _score(model: Model, registry: CapabilityRegistry) -> tuple[float, float, str]:
    """动态打分：消费比低者优先，其次单价低者优先，最后按标签稳定排序。"""
    return (registry.usage_ratio(model), model.cost.input, model.label())


def dynamic_select(
    selection: StaticSelection,
    task: Task,
    registry: CapabilityRegistry,
) -> tuple[list[Model], list[tuple[Model, str]]]:
    """第二步：按能力与可用性过滤，必要时按消费比/单价排序。

    返回 ``(保序候选, 拒绝列表)``。
    """
    required = task.required_capabilities()
    kept: list[Model] = []
    rejected: list[tuple[Model, str]] = []

    for model in selection.candidates:
        missing = registry.missing_capabilities(model, required)
        if missing:
            rejected.append((model, f"{REJECT_CAPABILITY}: {', '.join(missing)}"))
            continue
        if not registry.is_available(model):
            rejected.append((model, REJECT_UNAVAILABLE))
            continue
        kept.append(model)

    # 静态路由的顺序是运维显式指定的主备顺序，动态打分不得改写它。
    if not selection.pinned:
        kept.sort(key=lambda model: _score(model, registry))

    return kept, rejected


def build_primary_backup(ordered: list[Model]) -> tuple[Model | None, Model | None]:
    """第三步：取前两名作为主、备。"""
    primary = ordered[0] if ordered else None
    backup = ordered[1] if len(ordered) > 1 else None
    return primary, backup


def route(task: Task, registry: CapabilityRegistry) -> Decision:
    """完整路由：解析 profile → 静态 → 动态 → 主备 → ``model`` 点名。"""
    requested = task.profile
    profile = resolve_profile(task, registry)

    # 显式指定了不存在的 profile：这是配置错误，必须快速失败，而不是扩大候选池。
    if requested and profile is None:
        return Decision(profile=None, reason=f"profile {requested} 不存在")

    selection = apply_static(profile, registry)
    ordered, rejected = dynamic_select(selection, task, registry)

    # 需求第 27 行："根据请求中的 model 字段动态路由到对应适配器"。
    # 点名只**改顺序**，不扩大候选池：点了个池子外的模型，说明调用方对 profile 的理解
    # 与配置不一致，如实报错比悄悄照办更有用。
    pinned = task.model
    if pinned:
        target = registry.find(pinned)
        if target is None:
            return Decision(
                profile=profile,
                rejected=rejected,
                reason=f"model {pinned} 不存在（请用 provider/id 形态或唯一的 id）",
            )
        if target not in ordered:
            # 两种"不在池子里"要分清：被能力/可用性拒了（rejected 里有原因），还是压根
            # 不在 profile 声明的范围内。后者与模型本身的好坏无关，说成"不可用"会误导。
            why = next(
                (reason for model, reason in rejected if model is target),
                f"不在 profile {profile.name} 的候选池里" if profile else "不在候选池里",
            )
            return Decision(profile=profile, rejected=rejected, reason=f"model {pinned} 不可用：{why}")
        ordered = [target, *(model for model in ordered if model is not target)]
        selection.reason = f"{selection.reason}；model {pinned} 已点名"

    primary, backup = build_primary_backup(ordered)

    if primary is None:
        reason = "没有可用候选模型" if not rejected else "全部候选被拒"
    elif backup is None:
        reason = f"{selection.reason}；主 {primary.label()}（无备用）"
    elif len(ordered) > 2:
        # 候选链可以不止两个模型——降级就是一路往下试。只报"主/备"会让 Trace 里
        # 看不全"这次到底会依次试哪几个"，决策快照要把整条链说清楚。
        chain = " → ".join(model.label() for model in ordered)
        reason = f"{selection.reason}；候选链 {chain}"
    else:
        reason = f"{selection.reason}；主 {primary.label()}，备 {backup.label()}"

    return Decision(
        profile=profile,
        candidates=ordered,
        rejected=rejected,
        primary=primary,
        backup=backup,
        reason=reason,
    )
