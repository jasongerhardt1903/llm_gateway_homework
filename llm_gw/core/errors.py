"""错误模型：稳定错误码、归一化、可重试分类与脱敏。

三件事集中在这里，因为它们必须保持一致：

1. **归一化**（移植 pi 的 ``error-body.ts``）——不同 SDK 把 HTTP 状态与响应体
   塞在不同字段里（``statusCode`` / ``status`` / ``$metadata.httpStatusCode`` /
   ``$response.statusCode``），只读 ``message`` 会丢掉关键信息。
2. **可重试分类**——需求第 88 行的决策表要求"有些错误可重试，有些不可"。
   配额/账单耗尽这类确定性失败必须快速失败，不能白白重试。
3. **脱敏**——需求要求对记录字段脱敏，密钥绝不能进日志。

版本：0.2.0
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
    "RETRY_DECISION",
    "RetryAction",
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
    # 内容拒答——不得绕过模型
    CONTENT_REFUSED = "CONTENT_REFUSED"
    # 客户端取消
    CANCELLED = "CANCELLED"
    # 未知
    UNKNOWN = "UNKNOWN"


class RetryAction(str, Enum):
    """需求第 88 行决策表的动作枚举。"""

    NEVER = "never"
    RETRY_AFTER_FIX = "retry_after_fix"
    RETRY_AFTER_CONFIG = "retry_after_config"
    REJECT_OR_SWITCH_POOL = "reject_or_switch_pool"
    LIMITED_RETRY = "limited_retry"
    LIMITED_RETRY_OR_FALLBACK = "limited_retry_or_fallback"
    NO_BLIND_REGENERATE = "no_blind_regenerate"
    LIMITED_REPAIR = "limited_repair"
    NO_BYPASS = "no_bypass"


#: 需求第 88 行「阶段 / 典型错误 / 是否适合重试」决策表。
RETRY_DECISION: dict[ErrorCode, RetryAction] = {
    ErrorCode.AUTH_INVALID: RetryAction.NEVER,
    ErrorCode.REQUEST_INVALID: RetryAction.NEVER,
    # 工具轮数超限是请求本身的问题，重试同一个请求只会再次超限。
    ErrorCode.TOOL_ROUNDS_EXCEEDED: RetryAction.NEVER,
    ErrorCode.PROMPT_INVALID: RetryAction.RETRY_AFTER_FIX,
    ErrorCode.ROUTE_NO_CANDIDATE: RetryAction.RETRY_AFTER_CONFIG,
    ErrorCode.QUEUE_REJECTED: RetryAction.REJECT_OR_SWITCH_POOL,
    ErrorCode.CONN_FAILED: RetryAction.LIMITED_RETRY,
    ErrorCode.RATE_LIMITED: RetryAction.LIMITED_RETRY_OR_FALLBACK,
    ErrorCode.UPSTREAM_OVERLOADED: RetryAction.LIMITED_RETRY_OR_FALLBACK,
    ErrorCode.STREAM_INTERRUPTED_AFTER_DATA: RetryAction.NO_BLIND_REGENERATE,
    ErrorCode.OUTPUT_SCHEMA_INVALID: RetryAction.LIMITED_REPAIR,
    ErrorCode.CONTENT_REFUSED: RetryAction.NO_BYPASS,
    ErrorCode.CANCELLED: RetryAction.NEVER,
    ErrorCode.UNKNOWN: RetryAction.NEVER,
}

#: 可以自动重试的错误码（不含需要人工修正/配置的）。
AUTO_RETRYABLE_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.CONN_FAILED,
        ErrorCode.RATE_LIMITED,
        ErrorCode.UPSTREAM_OVERLOADED,
    }
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
