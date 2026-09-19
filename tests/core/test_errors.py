"""错误模型测试。

覆盖需求中的三块内容：
1. 稳定错误码 + HTTP 状态 + 供应商请求 ID 的归一化。
2. 可重试 / 不可重试分类（需求第 88 行的决策表）。
3. 脱敏（需求："针对各个字段进行脱敏"）。
"""

from __future__ import annotations

import httpx
import pytest

from llm_gw.core.errors import (
    ErrorCode,
    GatewayError,
    classify_error_code,
    format_provider_error,
    is_retryable_assistant_error,
    is_retryable_provider_error,
    normalize_provider_error,
    redact_mapping,
    redact_text,
    truncate_error_text,
)
from llm_gw.core.messages import AssistantMessage, Usage

# --------------------------------------------------------------------------
# 归一化
# --------------------------------------------------------------------------


def test_normalize_extracts_status_from_httpx_response():
    """从 httpx.HTTPStatusError 中抠出 HTTP 状态与响应体。"""
    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    response = httpx.Response(429, request=request, json={"error": {"message": "rate limited"}})
    exc = httpx.HTTPStatusError("429 Too Many Requests", request=request, response=response)

    norm = normalize_provider_error(exc)

    assert norm.status == 429
    assert "rate limited" in (norm.body or "")


def test_normalize_extracts_status_from_sdk_style_fields():
    """兼容 SDK 风格字段：statusCode / status / $metadata.httpStatusCode。"""
    for field in ("statusCode", "status"):
        exc = Exception("boom")
        setattr(exc, field, 503)
        assert normalize_provider_error(exc).status == 503

    bedrock = Exception("boom")
    setattr(bedrock, "$metadata", {"httpStatusCode": 400})
    assert normalize_provider_error(bedrock).status == 400


def test_normalize_extracts_provider_request_id_from_headers():
    """供应商请求 ID 用于对账，需从响应头中提取。"""
    request = httpx.Request("POST", "https://api.test/v1/chat/completions")
    response = httpx.Response(
        500,
        request=request,
        headers={"x-request-id": "req_abc123"},
        json={"error": "server error"},
    )
    exc = httpx.HTTPStatusError("500", request=request, response=response)

    assert normalize_provider_error(exc).provider_request_id == "req_abc123"


def test_normalize_handles_non_exception_throw():
    """抛出非异常对象时不得崩溃，退化为字符串消息。"""
    norm = normalize_provider_error({"weird": True})

    assert norm.status is None
    assert "weird" in norm.message


def test_format_provider_error_includes_status_and_prefix():
    """展示串包含供应商前缀与 HTTP 状态，便于日志排查。"""
    request = httpx.Request("POST", "https://api.test/v1")
    response = httpx.Response(401, request=request, json={"error": {"message": "invalid api key"}})
    exc = httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

    text = format_provider_error(normalize_provider_error(exc), prefix="deepseek")

    assert text.startswith("deepseek (401):")
    assert "invalid api key" in text


def test_truncate_error_text_caps_length_and_marks_truncation():
    """错误体需要截断，避免把整页 HTML 塞进日志。"""
    text = "x" * 5000
    out = truncate_error_text(text, 100)

    assert out.startswith("x" * 100)
    assert "truncated" in out
    assert len(out) < 200

    assert truncate_error_text("short", 100) == "short"


# --------------------------------------------------------------------------
# 可重试分类（需求第 88 行决策表）
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504])
def test_retryable_http_statuses(status):
    """连接抖动、短暂 5xx、429 属于有限重试。"""
    request = httpx.Request("POST", "https://api.test/v1")
    exc = httpx.HTTPStatusError(str(status), request=request, response=httpx.Response(status, request=request))

    assert is_retryable_provider_error(exc) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_non_retryable_http_statuses(status):
    """认证失败、请求验证失败不可重试，必须快速失败。"""
    request = httpx.Request("POST", "https://api.test/v1")
    exc = httpx.HTTPStatusError(str(status), request=request, response=httpx.Response(status, request=request))

    assert is_retryable_provider_error(exc) is False


def test_network_errors_are_retryable():
    """网络抖动可有限重试。"""
    assert is_retryable_provider_error(httpx.ConnectError("connection refused")) is True
    assert is_retryable_provider_error(httpx.ReadTimeout("timed out")) is True


def test_x_should_retry_header_overrides_status():
    """供应商显式指令优先：x-should-retry: false 时不重试。"""
    request = httpx.Request("POST", "https://api.test/v1")
    exc = httpx.HTTPStatusError(
        "503",
        request=request,
        response=httpx.Response(503, request=request, headers={"x-should-retry": "false"}),
    )
    assert is_retryable_provider_error(exc) is False


@pytest.mark.parametrize(
    "message",
    [
        "insufficient_quota",
        "You exceeded your current quota",
        "out of budget",
        "billing hard limit reached",
        "quota exceeded",
    ],
)
def test_quota_and_billing_are_not_retryable(message):
    """配额/账单耗尽属于确定性失败，重试无意义。"""
    assert is_retryable_assistant_error(AssistantMessage(content=[], usage=Usage(), stop_reason="error", error_message=message)) is False


@pytest.mark.parametrize(
    "message",
    [
        "overloaded",
        "rate limit exceeded",
        "429 Too Many Requests",
        "503 Service Unavailable",
        "connection reset by peer",
        "socket hang up",
        "request timed out",
        "upstream connect error",
    ],
)
def test_transient_errors_are_retryable(message):
    """过载、限流、连接类错误可有限重试。"""
    assert is_retryable_assistant_error(AssistantMessage(content=[], usage=Usage(), stop_reason="error", error_message=message)) is True


def test_non_error_stop_reason_is_never_retryable():
    """正常结束的响应不进入重试判定。"""
    assert is_retryable_assistant_error(AssistantMessage(content=[], usage=Usage(), stop_reason="stop")) is False


# --------------------------------------------------------------------------
# 错误码分类
# --------------------------------------------------------------------------


def test_classify_auth_error():
    """401/403 → 认证错误，不可重试。"""
    request = httpx.Request("POST", "https://api.test/v1")
    exc = httpx.HTTPStatusError("401", request=request, response=httpx.Response(401, request=request))

    assert classify_error_code(exc) is ErrorCode.AUTH_INVALID


def test_classify_rate_limited_and_overloaded():
    """429 → 限流；5xx → 上游过载。"""
    request = httpx.Request("POST", "https://api.test/v1")
    limited = httpx.HTTPStatusError("429", request=request, response=httpx.Response(429, request=request))
    overloaded = httpx.HTTPStatusError("503", request=request, response=httpx.Response(503, request=request))

    assert classify_error_code(limited) is ErrorCode.RATE_LIMITED
    assert classify_error_code(overloaded) is ErrorCode.UPSTREAM_OVERLOADED


def test_classify_connection_error():
    """连接层失败 → CONN_FAILED。"""
    assert classify_error_code(httpx.ConnectError("refused")) is ErrorCode.CONN_FAILED


def test_classify_content_refusal_is_not_retryable():
    """内容拒答不得绕过模型，错误码独立且不可重试。"""
    assert classify_error_code(Exception("content policy violation: request refused")) is ErrorCode.CONTENT_REFUSED


def test_gateway_error_carries_stable_code_and_retryability():
    """对外错误必须携带稳定错误码，供调用方程序化处理。"""
    err = GatewayError(ErrorCode.RATE_LIMITED, "too many requests", http_status=429, provider_request_id="req_1")

    assert err.code is ErrorCode.RATE_LIMITED
    assert err.code.value == "RATE_LIMITED"
    assert err.http_status == 429
    assert err.retryable is True
    assert err.provider_request_id == "req_1"

    auth = GatewayError(ErrorCode.AUTH_INVALID, "bad key", http_status=401)
    assert auth.retryable is False


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "sk-abcdefghijklmnopqrstuvwxyz012345",
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig",
        "AIzaSyD-1234567890abcdefghijklmnopqrs",
        "ghp_1234567890abcdefghijklmnopqrstuvwx",
    ],
)
def test_redact_text_masks_credentials(secret):
    """脱敏后不得残留任何形式的密钥。"""
    redacted = redact_text(f"failed with key {secret} while calling api")

    assert secret not in redacted
    assert "***" in redacted


def test_redact_mapping_masks_sensitive_keys_recursively():
    """脱敏需递归处理嵌套结构，键名匹配即掩码。"""
    payload = {
        "headers": {"Authorization": "Bearer abcdefghijklmnop", "content-type": "application/json"},
        "api_key": "sk-1234567890abcdefghijklmnop",
        "nested": [{"access_token": "tok_abcdefghijklmnopqrst"}],
        "model": "gpt-4o-mini",
    }

    out = redact_mapping(payload)

    assert out["headers"]["Authorization"] == "***"
    assert out["headers"]["content-type"] == "application/json"
    assert out["api_key"] == "***"
    assert out["nested"][0]["access_token"] == "***"
    # 非敏感字段必须原样保留，否则日志失去排查价值。
    assert out["model"] == "gpt-4o-mini"


def test_redact_text_keeps_ordinary_text_intact():
    """普通文本不得被误伤。"""
    text = "model gpt-4o-mini returned 3 tokens in 120ms"
    assert redact_text(text) == text
