"""DeepSeek preset。

**这不是一个独立协议 adapter**。参考项目 pi 里 ``providers/deepseek.ts`` 全文
15 行，只是 ``openAICompletionsApi`` 换了个 baseUrl；DeepSeek 官方 API 就是
OpenAI 兼容协议。因此这里只描述差异：

* baseUrl 不同；
* 只支持 ``response_format={"type": "json_object"}``，**不支持严格 JSON Schema**
  （``capabilities.json_schema=False``）——请求翻译据此自动降级；
* ``deepseek-reasoner`` 输出 ``reasoning_content``（映射为思考块），且不支持
  function calling。
"""

from __future__ import annotations

from ...core.messages import Capabilities, CostRates, Model
from .registry import Preset

__all__ = ["PRESET", "MODELS"]

_API = "openai-completions"
_BASE_URL = "https://api.deepseek.com/v1"

MODELS: tuple[Model, ...] = (
    Model(
        id="deepseek-chat",
        name="DeepSeek Chat",
        api=_API,
        provider="deepseek",
        base_url=_BASE_URL,
        context_window=64_000,
        max_tokens=8_192,
        cost=CostRates(input=0.27, output=1.10, cache_read=0.07),
        capabilities=Capabilities(
            sse=True, streaming=True, tools=True, json_schema=False, vision=False, reasoning=False
        ),
        display_provider="DeepSeek",
    ),
    Model(
        id="deepseek-reasoner",
        name="DeepSeek Reasoner",
        api=_API,
        provider="deepseek",
        base_url=_BASE_URL,
        context_window=64_000,
        max_tokens=8_192,
        cost=CostRates(input=0.55, output=2.19, cache_read=0.14),
        # reasoner 不支持 function calling，能力注册表据此把它排除在工具任务之外。
        capabilities=Capabilities(
            sse=True, streaming=True, tools=False, json_schema=False, vision=False, reasoning=True
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
    notes="OpenAI 兼容协议；仅支持 json_object，不支持严格 JSON Schema。",
)
