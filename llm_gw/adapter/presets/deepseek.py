"""DeepSeek preset。

**这不是一个独立协议 adapter**。参考项目 pi 里 ``providers/deepseek.ts`` 全文
15 行，只是 ``openAICompletionsApi`` 换了个 baseUrl；DeepSeek 官方 API 就是
OpenAI 兼容协议。因此这里只描述差异：

* baseUrl 不同；
* 只支持 ``response_format={"type": "json_object"}``，**不支持严格 JSON Schema**
  （``capabilities.json_schema=False``）——请求翻译据此自动降级；
* 两个型号都支持思考模式（默认开启），思考内容走 ``reasoning_content``
  （映射为思考块），因此 ``capabilities.reasoning=True``；
* ``deepseek-v4-pro`` **不支持视觉**，``deepseek-flash`` 支持。

清单与价格以官方文档为准（``https://api-docs.deepseek.com`` 的
"Your First API Call" 与 "Models & Pricing"）：上下文 1M、最大输出 384K。
旧的 ``deepseek-chat`` / ``deepseek-reasoner`` 已下线；``deepseek-v4-flash``
与 ``deepseek-v4-flash-vision-exp`` 属于历史名称，官方仍在接受但实际由
DeepSeek-V4.1-Flash 提供服务，因此不再作为独立型号列出。

官方计费分**峰值 / 非峰值**两档（非峰值为峰值的一半，峰值为 UTC 周一至周五
01:00-04:00 与 06:00-10:00），而 :class:`CostRates` 只能存一个单价，这里取
**峰值价**：成本估算宁可偏高，也不要系统性低估。
"""

from __future__ import annotations

from ...core.messages import Capabilities, CostRates, Model
from .registry import Preset

__all__ = ["PRESET", "MODELS"]

_API = "openai-completions"
_BASE_URL = "https://api.deepseek.com/v1"

#: 官方文档给出的两个型号共用同一组上限：上下文 1M、最大输出 384K。
_CONTEXT_WINDOW = 1_000_000
_MAX_TOKENS = 384_000

MODELS: tuple[Model, ...] = (
    Model(
        id="deepseek-flash",
        name="DeepSeek Flash",
        api=_API,
        provider="deepseek",
        base_url=_BASE_URL,
        context_window=_CONTEXT_WINDOW,
        max_tokens=_MAX_TOKENS,
        # 峰值价（美元 / 百万 token）：输入 0.30、输出 1.20、缓存命中 0.006。
        cost=CostRates(input=0.30, output=1.20, cache_read=0.006),
        # 官方型号版本 DeepSeek-V4.1-Flash；支持工具调用与视觉，仅 json_object。
        capabilities=Capabilities(
            sse=True, streaming=True, tools=True, json_schema=False, vision=True, reasoning=True
        ),
        display_provider="DeepSeek",
    ),
    Model(
        id="deepseek-v4-pro",
        name="DeepSeek V4 Pro",
        api=_API,
        provider="deepseek",
        base_url=_BASE_URL,
        context_window=_CONTEXT_WINDOW,
        max_tokens=_MAX_TOKENS,
        # 峰值价：输入 1.32、输出 3.96、缓存命中 0.044。
        cost=CostRates(input=1.32, output=3.96, cache_read=0.044),
        # 官方型号版本 DeepSeek-V4-Pro-0813；**不支持视觉**，其余与 flash 一致。
        capabilities=Capabilities(
            sse=True, streaming=True, tools=True, json_schema=False, vision=False, reasoning=True
        ),
        display_provider="DeepSeek",
    ),
)

PRESET = Preset(
    provider="deepseek",
    display_name="DeepSeek",
    api=_API,
    base_url=_BASE_URL,
    env_key="DEEPSEEK_API_KEY",
    models=MODELS,
    notes="OpenAI 兼容协议；仅支持 json_object，不支持严格 JSON Schema；v4-pro 不支持视觉。",
)
