"""供应商 preset 注册表。

一个 preset = 一个供应商的静态描述：协议、baseUrl、密钥环境变量、模型清单
（含价格与能力）。模型清单被三处共用：Web 的供应商下拉、路由的能力注册表、
成本计算。放在一处定义可避免三份清单漂移。

preset 只描述"怎么连"，不含密钥本身——密钥来自环境变量或请求级覆盖，
以免随配置四处传播。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from ...core.messages import Model

__all__ = ["Preset", "PRESETS", "get_preset", "all_presets", "all_models", "find_model", "provider_choices"]


@dataclass(frozen=True)
class Preset:
    """一个供应商的静态描述。"""

    provider: str
    display_name: str
    api: str
    base_url: str
    env_key: str
    models: tuple[Model, ...] = ()
    notes: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def _load() -> dict[str, Preset]:
    # 延迟导入，避免 presets 包内循环引用。
    from . import anthropic, deepseek, openai

    presets = (openai.PRESET, deepseek.PRESET, anthropic.PRESET)
    return {preset.provider: preset for preset in presets}


PRESETS: dict[str, Preset] = _load()


def get_preset(provider: str) -> Preset | None:
    return PRESETS.get(provider)


def all_presets() -> list[Preset]:
    return list(PRESETS.values())


def all_models() -> list[Model]:
    """所有 preset 的模型清单（深拷贝，调用方可安全修改）。"""
    models: list[Model] = []
    for preset in PRESETS.values():
        models.extend(copy.deepcopy(list(preset.models)))
    return models


def find_model(provider: str, model_id: str) -> Model | None:
    preset = PRESETS.get(provider)
    if preset is None:
        return None
    for model in preset.models:
        if model.id == model_id:
            return copy.deepcopy(model)
    return None


def provider_choices() -> list[dict[str, str]]:
    """供 Web 供应商下拉菜单使用。"""
    return [
        {
            "provider": preset.provider,
            "display_name": preset.display_name,
            "api": preset.api,
            "base_url": preset.base_url,
            "env_key": preset.env_key,
            "notes": preset.notes,
        }
        for preset in PRESETS.values()
    ]
