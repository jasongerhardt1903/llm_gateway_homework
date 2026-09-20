"""错误模型：稳定错误码、归一化、可重试分类与脱敏。

三件事集中在这里，因为它们必须保持一致：

1. **归一化**（移植 pi 的 ``error-body.ts``）——不同 SDK 把 HTTP 状态与响应体
   塞在不同字段里（``statusCode`` / ``status`` / ``$metadata.httpStatusCode`` /
   ``$response.statusCode``），只读 ``message`` 会丢掉关键信息。
2. **处置分类**——需求 Harness 层第 91-104 行的决策表要求网关在"重试 / 降级 /
   报错"三者中**选一个**：有些错误可重试，有些不可，有些应换模型。配额/账单
   耗尽这类确定性失败必须快速失败，不能白白重试。
3. **脱敏**——需求要求对记录字段脱敏，密钥绝不能进日志。

版本：0.3.0
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .messages import AssistantMessage

__all__ = [
    "ErrorCode",
    "ERROR_DECISIONS",
    "ErrorDecision",
    "ErrorDisposition",
    "GatewayError",
    "NormalizedError",
    "normalize_provider_error",
    "format_provider_error",
    "truncate_error_text",
    "is_retryable_provider_error",
    "is_retryable_assistant_error",
    "classify_error_code",
    "redact_text",
    "redact_mapping",
    "safe_json_dumps",
    "MAX_PROVIDER_ERROR_BODY_CHARS",
]

MAX_PROVIDER_ERROR_BODY_CHARS = 4000


class ErrorCode(str, Enum):
    """对外稳定错误码。调用方按码程序化处理，不解析文案。"""

    # 认证 / 授权
    AUTH_INVALID = "AUTH_INVALID"
    # 网关入口鉴权失败：调用方未提供或提供了错误的 agent 口令。
    # 与 AUTH_INVALID 区分开——后者是"上游供应商密钥失效"（可换模型降级），
    # 前者是"你没通过网关的门"（换模型毫无意义，只能改正凭证后重发）。
    AUTH_REQUIRED = "AUTH_REQUIRED"
    # 请求验证（参数非法、Schema 缺失）
    REQUEST_INVALID = "REQUEST_INVALID"
    # 请求验证：工具调用轮数超过该模型/profile 配置的上限
    TOOL_ROUNDS_EXCEEDED = "TOOL_ROUNDS_EXCEEDED"
    # Prompt 层（缺变量、超预算）——修正请求后可重试
    PROMPT_INVALID = "PROMPT_INVALID"
    # 路由层（无兼容模型）——配置变化后可重试
    ROUTE_NO_CANDIDATE = "ROUTE_NO_CANDIDATE"
    # 排队（并发已满、截止时间不足）
    QUEUE_REJECTED = "QUEUE_REJECTED"
    # 连接（网络抖动、短暂 5xx）
    CONN_FAILED = "CONN_FAILED"
    # 首 token 前限流
    RATE_LIMITED = "RATE_LIMITED"
    # 上游过载
    UPSTREAM_OVERLOADED = "UPSTREAM_OVERLOADED"
    # 已流式输出后中断——不盲目重新生成
    STREAM_INTERRUPTED_AFTER_DATA = "STREAM_INTERRUPTED_AFTER_DATA"
    # 输出校验失败（JSON/Schema）——有限修复
    OUTPUT_SCHEMA_INVALID = "OUTPUT_SCHEMA_INVALID"
    # 内容拒答——更换模型重试，但每个模型最多尝试一次
    CONTENT_REFUSED = "CONTENT_REFUSED"
    # 客户端取消
    CANCELLED = "CANCELLED"
    # 未知
    UNKNOWN = "UNKNOWN"


class ErrorDisposition(str, Enum):
    """需求 Harness 层第 91 行：错误发生后在"重试 / 降级 / 报错"三者中选一个。"""

    #: 网关自行有限重试**同一个**模型（瞬时错误：连接抖动、限流、过载）。
    RETRY = "retry"
    #: 不重试当前模型，直接换路由表中下一个模型（认证、排队、内容拒答等）。
    DEGRADE = "degrade"
    #: 直接返回错误，不做任何后续动作（请求非法、已流式输出后中断等）。
    FAIL = "fail"


@dataclass(frozen=True)
class ErrorDecision:
    """单个错误码的处置决策。"""

    disposition: ErrorDisposition
    #: 中文说明，供日志与响应；文案与需求文档「处理策略」列保持一致。
    strategy: str = ""

    @property
    def retryable(self) -> bool:
        """是否允许网关自动重试同一模型（不改变请求）。"""
        return self.disposition is ErrorDisposition.RETRY


#: 需求 Harness 层第 91-104 行「阶段 / 典型错误 / 是否适合重试 / 处理策略」决策表。
#:
#: 几个非显然的定级，理由如下：
#:
#: - ``PROMPT_INVALID`` / ``ROUTE_NO_CANDIDATE`` / ``OUTPUT_SCHEMA_INVALID`` 定为
#:   ``DEGRADE`` 而非 ``RETRY``：文档写的是"**修正请求后**重试""**配置变化后**
#:   重试"，前提是请求或配置被改变。网关自身不修改请求，原样重试同一模型只会
#:   白耗一次配额，因此直接进入文档要求的"按照路由更换模型再重试"。
#: - ``CONTENT_REFUSED`` 定为 ``DEGRADE``：不重试当前模型、只换下一个，天然满足
#:   "每个模型最多尝试一次"（路由只提供主备两个候选）。
#: - ``STREAM_INTERRUPTED_AFTER_DATA`` 定为 ``FAIL``：已吐过内容，换模型重来会产出
#:   重复文本，只能如实报错，由客户端决定。
ERROR_DECISIONS: dict[ErrorCode, ErrorDecision] = {
    ErrorCode.AUTH_INVALID: ErrorDecision(
        ErrorDisposition.DEGRADE,
        "更换路由表中下一个模型并告警，不阻塞；没有下一个模型则返回错误。",
    ),
    # 网关入口鉴权失败发生在选模型之前，没有"换一个模型"这回事。
    ErrorCode.AUTH_REQUIRED: ErrorDecision(
        ErrorDisposition.FAIL, "直接返回错误；调用方改正凭证后重新发起。"
    ),
    ErrorCode.REQUEST_INVALID: ErrorDecision(
        ErrorDisposition.FAIL, "直接返回错误，不重试。"
    ),
    # 工具轮数超限是请求本身的问题，重试同一个请求只会再次超限。
    ErrorCode.TOOL_ROUNDS_EXCEEDED: ErrorDecision(
        ErrorDisposition.FAIL, "直接返回错误，不重试。"
    ),
    ErrorCode.PROMPT_INVALID: ErrorDecision(
        ErrorDisposition.DEGRADE,
        "修正请求后重试；网关无法自行修正请求，直接按路由更换模型再试。",
    ),
    ErrorCode.ROUTE_NO_CANDIDATE: ErrorDecision(
        ErrorDisposition.DEGRADE, "配置变化后重试；无兼容模型时直接返回错误。"
    ),
    ErrorCode.QUEUE_REJECTED: ErrorDecision(
        ErrorDisposition.DEGRADE, "拒绝或换池。"
    ),
    ErrorCode.CONN_FAILED: ErrorDecision(
        ErrorDisposition.RETRY, "有限重试；达到最大重试次数后按路由更换模型再试。"
    ),
    ErrorCode.RATE_LIMITED: ErrorDecision(
        ErrorDisposition.RETRY,
        "有限重试或 fallback；达到最大重试次数后按路由更换模型再试。",
    ),
    ErrorCode.UPSTREAM_OVERLOADED: ErrorDecision(
        ErrorDisposition.RETRY,
        "有限重试或 fallback；达到最大重试次数后按路由更换模型再试。",
    ),
    ErrorCode.STREAM_INTERRUPTED_AFTER_DATA: ErrorDecision(
        ErrorDisposition.FAIL, "不盲目重新生成；已输出则如实报错，由客户端决定。"
    ),
    ErrorCode.OUTPUT_SCHEMA_INVALID: ErrorDecision(
        ErrorDisposition.DEGRADE,
        "有限修复；修复在 adapter 的 validate_with_repair 内对同一模型做（最多 2 次），"
        "修复超限后按路由更换模型再试。",
    ),
    ErrorCode.CONTENT_REFUSED: ErrorDecision(
        ErrorDisposition.DEGRADE, "更换模型重试，每个模型最多尝试一次。"
    ),
    ErrorCode.CANCELLED: ErrorDecision(ErrorDisposition.FAIL, "客户端取消，不重试。"),
    ErrorCode.UNKNOWN: ErrorDecision(ErrorDisposition.FAIL, "未知错误，直接返回。"),
}

#: 可以自动重试的错误码：处置为 ``RETRY`` 者。恰好等于
#: ``{CONN_FAILED, RATE_LIMITED, UPSTREAM_OVERLOADED}``，因此
#: :attr:`GatewayError.retryable` 与 :func:`is_retryable_provider_error` 的
#: 外部语义保持不变。
AUTO_RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    code
    for code, decision in ERROR_DECISIONS.items()
    if decision.disposition is ErrorDisposition.RETRY
)


class GatewayError(Exception):
    """网关对外错误。携带稳定错误码、HTTP 状态与供应商请求 ID。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        http_status: int | None = None,
        provider_request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.provider_request_id = provider_request_id
        self.details = details or {}

    @property
    def retryable(self) -> bool:
        return self.code in AUTO_RETRYABLE_CODES

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": redact_text(self.message),
            "http_status": self.http_status,
            "provider_request_id": self.provider_request_id,
            "retryable": self.retryable,
        }

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return f"{self.code.value}: {self.message}"


# --------------------------------------------------------------------------
# 归一化
# --------------------------------------------------------------------------


@dataclass
class NormalizedError:
    """归一化后的供应商错误。"""

    message: str
    status: int | None = None
    body: str | None = None
    message_carries_body: bool = False
    provider_request_id: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        target = name.lower()
        for key, value in self.headers.items():
            if key.lower() == target:
                return value
        return None


_REQUEST_ID_HEADERS = (
    "x-request-id",
    "request-id",
    "x-amzn-requestid",
    "x-ms-request-id",
    "anthropic-request-id",
    "x-goog-request-id",
    "cf-ray",
)


def _headers_to_dict(headers: Any) -> dict[str, str]:
    if headers is None:
        return {}
    try:
        return {str(k): str(v) for k, v in dict(headers).items()}
    except (TypeError, ValueError):
        return {}


def _extract_status(exc: Any) -> int | None:
    """按 SDK 字段优先级探测 HTTP 状态，首个数值命中生效。"""
    # httpx.HTTPStatusError
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            return status
    for field_name in ("statusCode", "status"):
        value = getattr(exc, field_name, None)
        if isinstance(value, int):
            return value
    metadata = getattr(exc, "$metadata", None)
    if isinstance(metadata, dict) and isinstance(metadata.get("httpStatusCode"), int):
        return metadata["httpStatusCode"]
    response = getattr(exc, "$response", None)
    if response is not None:
        status = getattr(response, "statusCode", None)
        if isinstance(status, int):
            return status
    return None


def _extract_body(exc: Any) -> str | None:
    """探测原始响应体，空对象与响应流视为无 body。"""
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            text = response.text
        except Exception:  # noqa: BLE001 - 读取失败按无 body 处理
            text = None
        if text:
            trimmed = text.strip()
            if trimmed:
                return truncate_error_text(trimmed, MAX_PROVIDER_ERROR_BODY_CHARS)

    body_attr = getattr(exc, "body", None)
    if isinstance(body_attr, str) and body_attr.strip():
        return truncate_error_text(body_attr.strip(), MAX_PROVIDER_ERROR_BODY_CHARS)

    error_attr = getattr(exc, "error", None)
    if isinstance(error_attr, dict) and error_attr:
        return truncate_error_text(safe_json_dumps(error_attr), MAX_PROVIDER_ERROR_BODY_CHARS)

    response = getattr(exc, "$response", None)
    if response is not None:
        raw = getattr(response, "body", None)
        if isinstance(raw, str) and raw.strip():
            return truncate_error_text(raw.strip(), MAX_PROVIDER_ERROR_BODY_CHARS)
        if isinstance(raw, dict) and raw:
            return truncate_error_text(safe_json_dumps(raw), MAX_PROVIDER_ERROR_BODY_CHARS)

    return None


def _extract_request_id(exc: Any) -> str | None:
    headers = _headers_to_dict(getattr(getattr(exc, "response", None), "headers", None))
    if not headers:
        headers = _headers_to_dict(getattr(exc, "headers", None))
    lowered = {k.lower(): v for k, v in headers.items()}
    for name in _REQUEST_ID_HEADERS:
        if name in lowered and lowered[name]:
            return lowered[name]
    return None


def normalize_provider_error(exc: Any) -> NormalizedError:
    """把任意供应商异常归一成 :class:`NormalizedError`。"""
    if not isinstance(exc, BaseException):
        message = safe_json_dumps(exc)
        return NormalizedError(message=message, message_carries_body=False)

    message = str(exc) or type(exc).__name__
    status = _extract_status(exc)
    body = _extract_body(exc)
    headers = _headers_to_dict(getattr(getattr(exc, "response", None), "headers", None))
    if not headers:
        headers = _headers_to_dict(getattr(exc, "headers", None))

    return NormalizedError(
        message=message,
        status=status,
        body=body,
        message_carries_body=body is None or body in message,
        provider_request_id=_extract_request_id(exc),
        headers=headers,
    )


def format_provider_error(norm: NormalizedError, prefix: str | None = None) -> str:
    """组合出便于排查的展示串，包含供应商前缀与 HTTP 状态。"""
    if norm.message_carries_body or norm.status is None or norm.body is None:
        if prefix is not None and norm.status is not None:
            return f"{prefix} ({norm.status}): {norm.message}"
        return norm.message
    if prefix is not None:
        return f"{prefix} ({norm.status}): {norm.body}"
    return f"{norm.status}: {norm.body}"


def truncate_error_text(text: str, max_chars: int) -> str:
    """截断超长错误体，避免把整页 HTML 写进日志。"""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}... [truncated {len(text) - max_chars} chars]"


def safe_json_dumps(value: Any) -> str:
    """尽力序列化；失败时退化为 ``str()``，绝不抛异常。"""
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)
    return serialized


# --------------------------------------------------------------------------
# 可重试分类
# --------------------------------------------------------------------------


def _status_of(exc: Any) -> int | None:
    if isinstance(exc, GatewayError):
        return exc.http_status
    return _extract_status(exc)


def _should_retry_header(exc: Any) -> str | None:
    norm_headers = _headers_to_dict(getattr(getattr(exc, "response", None), "headers", None))
    if not norm_headers:
        norm_headers = _headers_to_dict(getattr(exc, "headers", None))
    lowered = {k.lower(): v for k, v in norm_headers.items()}
    return lowered.get("x-should-retry")


def is_retryable_provider_error(exc: Any) -> bool:
    """连接层/传输层错误是否值得重试。

    与 pi 的 ``isRetryableProviderError`` 一致：408 / 409 / 429 / 5xx 可重试；
    无状态码的传输异常（连接失败、超时）视为可重试；``x-should-retry`` 优先。
    """
    if isinstance(exc, GatewayError):
        return exc.retryable

    override = _should_retry_header(exc)
    if override == "true":
        return True
    if override == "false":
        return False

    status = _status_of(exc)
    if status is None:
        # 传输层异常（连接被拒、读超时、DNS 失败）没有状态码，可重试。
        return _is_transport_exception(exc)
    return status in (408, 409, 429) or 500 <= status <= 599


def _is_transport_exception(exc: Any) -> bool:
    if not isinstance(exc, BaseException):
        return False
    try:
        import httpx

        transport_types: tuple[type, ...] = (
            httpx.TransportError,
            httpx.TimeoutException,
        )
        if isinstance(exc, transport_types):
            return True
    except ImportError:  # pragma: no cover - httpx 是必需依赖
        pass
    name = type(exc).__name__.lower()
    return any(
        marker in name
        for marker in ("connect", "timeout", "connection", "network", "socket", "reset", "brokenpipe")
    )


# 确定性失败：配额/账单耗尽，重试没有意义。
_NON_RETRYABLE_PATTERN = re.compile(
    r"insufficient_quota|out of budget|quota exceeded|billing|"
    r"usage limit reached|available balance|free usage limit|"
    r"account (?:is )?(?:disabled|suspended)",
    re.IGNORECASE,
)

# 瞬时失败：过载、限流、连接类、流中断。
_RETRYABLE_PATTERN = re.compile(
    r"overloaded|currently experiencing high demand|rate.?limit|too many requests|"
    r"\b429\b|\b500\b|\b502\b|\b503\b|\b504\b|\b520\b|\b524\b|"
    r"service.?unavailable|server.?error|internal.?error|"
    r"provider.?returned.?error|"
    r"network.?error|connection.?error|connection.?refused|connection.?lost|connection.?reset|"
    r"other side closed|fetch failed|getaddrinfo|ENOTFOUND|EAI_AGAIN|"
    r"upstream.?connect|reset before headers|reset by peer|socket hang up|socket connection was closed|"
    r"timed? out|timeout|terminated|"
    r"websocket.?closed|websocket.?error|"
    r"ended without|stream ended before|"
    r"you can retry your request|try your request again|please retry your request",
    re.IGNORECASE,
)

# 内容拒答：不得通过换模型绕过。
# 刻意要求 "拒答" 与内容/安全语境同时出现——单独一个 "refused" 常见于连接错误
# （如 ConnectError("refused")），若直接匹配会把网络故障误判成内容拒答。
_CONTENT_REFUSED_PATTERN = re.compile(
    r"content.?polic(?:y|ies)|content.?filter|safety.?polic(?:y|ies)|"
    r"blocked by (?:safety|policy)|prohibited content|responsible ai|"
    r"(?:content|safety|polic(?:y|ies)).{0,24}refus|refus.{0,24}(?:content|safety|polic(?:y|ies))",
    re.IGNORECASE,
)


def is_retryable_assistant_error(message: AssistantMessage) -> bool:
    """判断失败的助手消息是否属于瞬时错误。

    非 error 终态一律不重试；配额/账单类**优先**判定为不可重试，
    避免被可重试正则里的数字误伤。
    """
    if message.stop_reason != "error" or not message.error_message:
        return False
    text = message.error_message
    if _NON_RETRYABLE_PATTERN.search(text):
        return False
    return bool(_RETRYABLE_PATTERN.search(text))


def classify_error_code(exc: Any) -> ErrorCode:
    """把异常映射到稳定错误码。"""
    if isinstance(exc, GatewayError):
        return exc.code

    text = str(exc)

    if _CONTENT_REFUSED_PATTERN.search(text):
        return ErrorCode.CONTENT_REFUSED

    if _NON_RETRYABLE_PATTERN.search(text):
        return ErrorCode.AUTH_INVALID

    status = _status_of(exc)
    if status is not None:
        if status in (401, 403):
            return ErrorCode.AUTH_INVALID
        if status == 404:
            return ErrorCode.REQUEST_INVALID
        if status == 422:
            return ErrorCode.REQUEST_INVALID
        if status == 429:
            return ErrorCode.RATE_LIMITED
        if status == 408:
            return ErrorCode.CONN_FAILED
        if 500 <= status <= 599:
            return ErrorCode.UPSTREAM_OVERLOADED
        if 400 <= status <= 499:
            return ErrorCode.REQUEST_INVALID

    if _is_transport_exception(exc):
        return ErrorCode.CONN_FAILED

    if _RETRYABLE_PATTERN.search(text):
        return ErrorCode.UPSTREAM_OVERLOADED

    return ErrorCode.UNKNOWN


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------

_REDACTION = "***"

#: 明文密钥形态。刻意保守：宁可漏掉罕见形态，也不能误伤普通日志文本。
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{16,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{16,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,}"),
)

#: 键名命中即整体掩码。比较时忽略大小写与 ``-``/``_``。
_SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "apikey",
    "authorization",
    "token",
    "secret",
    "password",
    "credential",
    "privatekey",
)


def redact_text(text: str) -> str:
    """掩码文本中的明文密钥。"""
    if not text:
        return text
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(_REDACTION, redacted)
    return redacted


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "").replace("_", "")
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def redact_mapping(value: Any) -> Any:
    """递归脱敏：键名命中即掩码，字符串值再做一次明文密钥扫描。"""
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                out[key] = _REDACTION
            else:
                out[key] = redact_mapping(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_mapping(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
