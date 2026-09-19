"""可观测性测试：Metrics / Logs / Trace 三视图与 TTFT 口径。

需求要求记录每次 LLM 调用的 8 个维度，并区分 Metrics（聚合趋势）、Logs（单次
明细）、Trace（链路）。这里用**注入的 now 函数**驱动时间窗，因此窗口类指标
可以被精确断言，不需要真实等待。
"""

from __future__ import annotations

import aiosqlite
import pytest

from llm_gw.core.errors import ErrorCode
from llm_gw.core.messages import Usage
from llm_gw.core.telemetry import CallRecord, LatencyBreakdown, PromptInfo, ResilienceInfo
from llm_gw.harness.query import Query
from llm_gw.harness.storage import Storage


class _Now:
    """可手动推进的时间源。"""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _record(
    *,
    call_id: str,
    trace_id: str = "trace-1",
    terminal: str = "done",
    total_ms: float = 100.0,
    error_code: ErrorCode | None = None,
    error_message: str | None = None,
    metadata: dict | None = None,
    usage: Usage | None = None,
) -> CallRecord:
    return CallRecord(
        trace_id=trace_id,
        run_id="run-1",
        step_id="step-1",
        call_id=call_id,
        prompt=PromptInfo(name="summarize", version="v3", sha256="deadbeef", schema_version="s1"),
        profile="smart",
        provider="openai",
        model="gpt-4o-mini",
        api="openai-completions",
        usage=usage or Usage(input=100, output=50, total_tokens=150),
        latency=LatencyBreakdown(ttft_ms=20.0, generation_ms=80.0, total_ms=total_ms),
        resilience=ResilienceInfo(attempt=1, retry=0, fallback=False),
        finish_reason="stop" if terminal == "done" else "error",
        terminal=terminal,  # type: ignore[arg-type]
        error_code=error_code,
        error_message=error_message,
        stream_chunk_count=7,
        metadata=metadata or {},
    )


@pytest.fixture
async def storage():
    store = await Storage(":memory:", now=_Now()).init()
    yield store
    await store.close()


# --------------------------------------------------------------------------
# Logs：单次调用明细
# --------------------------------------------------------------------------


async def test_save_call_persists_all_eight_dimensions(storage):
    """一次调用 = 一行明细，8 个维度字段齐全。"""
    flat = await storage.save_call(_record(call_id="c1"))

    # 关联
    assert flat["trace_id"] == "trace-1"
    assert flat["run_id"] == "run-1"
    assert flat["step_id"] == "step-1"
    assert flat["call_id"] == "c1"
    # Prompt
    assert flat["prompt_name"] == "summarize"
    assert flat["prompt_sha256"] == "deadbeef"
    # 路由：生效的 profile 名
    assert flat["profile"] == "smart"
    # 用量
    assert flat["usage"]["input"] == 100
    assert flat["usage"]["total_tokens"] == 150
    # 延迟
    assert flat["ttft_ms"] == 20.0
    assert flat["total_ms"] == 100.0
    # 弹性
    assert flat["attempt"] == 1
    assert flat["fallback"] is False
    assert flat["disposition"] == ""
    # 结果
    assert flat["terminal"] == "done"
    assert flat["finish_reason"] == "stop"
    # 错误
    assert flat["error_code"] is None
    # 成本
    assert "total" in flat["cost"]

    rows = await storage.recent_calls()
    assert len(rows) == 1
    assert rows[0]["call_id"] == "c1"


async def test_error_code_is_persisted(storage):
    await storage.save_call(
        _record(
            call_id="c2",
            terminal="error",
            error_code=ErrorCode.RATE_LIMITED,
            error_message="429 too many requests",
        )
    )

    rows = await storage.recent_calls()
    assert rows[0]["terminal"] == "error"
    assert rows[0]["error_code"] == "RATE_LIMITED"


async def test_secrets_in_metadata_are_redacted(storage):
    """脱敏必须在落库前生效，密钥绝不能进日志。"""
    await storage.save_call(
        _record(
            call_id="c3",
            metadata={"api_key": "sk-super-secret", "authorization": "Bearer abc123", "note": "keep-me"},
        )
    )

    rows = await storage.recent_calls()
    metadata = rows[0]["metadata"]
    assert "sk-super-secret" not in str(metadata)
    assert "abc123" not in str(metadata)
    assert metadata["note"] == "keep-me"


# --------------------------------------------------------------------------
# 旧库迁移：v0.1.0 的 logical_model 列改名为 profile
# --------------------------------------------------------------------------


#: v0.1.0 的 requests 表结构——与当前唯一差别是路由维度列叫 logical_model。
_LEGACY_REQUESTS_DDL = """
CREATE TABLE requests (
    call_id             TEXT PRIMARY KEY,
    trace_id            TEXT NOT NULL DEFAULT '',
    run_id              TEXT NOT NULL DEFAULT '',
    step_id             TEXT NOT NULL DEFAULT '',
    ts                  REAL NOT NULL,
    logical_model       TEXT NOT NULL DEFAULT '',
    provider            TEXT NOT NULL DEFAULT '',
    model               TEXT NOT NULL DEFAULT '',
    api                 TEXT NOT NULL DEFAULT '',
    prompt_name         TEXT NOT NULL DEFAULT '',
    prompt_version      TEXT NOT NULL DEFAULT '',
    prompt_sha256       TEXT NOT NULL DEFAULT '',
    terminal            TEXT NOT NULL DEFAULT 'done',
    finish_reason       TEXT NOT NULL DEFAULT '',
    error_code          TEXT,
    error_message       TEXT,
    http_status         INTEGER,
    provider_request_id TEXT,
    attempt             INTEGER NOT NULL DEFAULT 1,
    retry               INTEGER NOT NULL DEFAULT 0,
    fallback            INTEGER NOT NULL DEFAULT 0,
    output_valid        INTEGER,
    queue_ms            REAL NOT NULL DEFAULT 0,
    route_ms            REAL NOT NULL DEFAULT 0,
    ttft_ms             REAL NOT NULL DEFAULT 0,
    generation_ms       REAL NOT NULL DEFAULT 0,
    total_ms            REAL NOT NULL DEFAULT 0,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens    INTEGER,
    total_tokens        INTEGER NOT NULL DEFAULT 0,
    cost_total          REAL NOT NULL DEFAULT 0,
    stream_chunk_count  INTEGER NOT NULL DEFAULT 0,
    payload             TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_requests_ts ON requests(ts);
CREATE INDEX idx_requests_trace ON requests(trace_id);
"""


async def test_init_migrates_legacy_logical_model_column(tmp_path):
    """v0.1.0 生成的库直接给 v0.2.0 用：启动时自动改名，历史记录不丢。"""
    path = tmp_path / "legacy.sqlite3"
    db = await aiosqlite.connect(path)
    await db.executescript(_LEGACY_REQUESTS_DDL)
    await db.execute(
        "INSERT INTO requests (call_id, ts, logical_model) VALUES (?, ?, ?)",
        ("c1", 1.0, "fast-chat"),
    )
    await db.commit()
    await db.close()

    store = await Storage(path).init()
    try:
        cursor = await store._conn().execute("PRAGMA table_info(requests)")
        columns = {row["name"] for row in await cursor.fetchall()}
        assert "profile" in columns
        assert "logical_model" not in columns

        cursor = await store._conn().execute("SELECT profile FROM requests WHERE call_id = 'c1'")
        assert (await cursor.fetchone())["profile"] == "fast-chat"
    finally:
        await store.close()


async def test_init_is_idempotent_on_migrated_schema(tmp_path):
    """迁移只做一次：已是 v0.2.0 的库重复启动不报错，数据可查。"""
    path = tmp_path / "v02.sqlite3"
    first = await Storage(path).init()
    await first.save_call(_record(call_id="c1"))
    await first.close()

    again = await Storage(path).init()
    try:
        rows = await again.recent_calls()
        assert [row["call_id"] for row in rows] == ["c1"]
        assert rows[0]["profile"] == "smart"
    finally:
        await again.close()


# --------------------------------------------------------------------------
# Metrics：聚合趋势
# --------------------------------------------------------------------------


async def test_qps_counts_only_within_window(storage):
    now: _Now = storage._now  # type: ignore[assignment]
    await storage.save_call(_record(call_id="old"), ts=now.value - 120)
    await storage.save_call(_record(call_id="new1"), ts=now.value - 5)
    await storage.save_call(_record(call_id="new2"), ts=now.value - 1)

    assert await storage.qps(60) == pytest.approx(2 / 60)
    assert await storage.qps(300) == pytest.approx(3 / 300)


async def test_error_rate_is_ratio_of_error_terminal(storage):
    now: _Now = storage._now  # type: ignore[assignment]
    await storage.save_call(_record(call_id="ok1"), ts=now.value)
    await storage.save_call(_record(call_id="ok2"), ts=now.value)
    await storage.save_call(_record(call_id="bad", terminal="error"), ts=now.value)

    assert await storage.error_rate(60) == pytest.approx(1 / 3)
    assert await storage.error_rate(1) == pytest.approx(1 / 3)


async def test_error_rate_is_zero_without_samples(storage):
    assert await storage.error_rate(60) == 0.0


async def test_p99_latency_uses_near_rank(storage):
    now: _Now = storage._now  # type: ignore[assignment]
    for index in range(100):
        await storage.save_call(
            _record(call_id=f"c{index}", total_ms=float(index + 1)), ts=now.value
        )

    # 100 个样本的 p99（最近秩）应为第 99 个样本。
    assert await storage.p99_latency(60) == 99.0


async def test_total_cost_sums_cost_ledger(storage):
    now: _Now = storage._now  # type: ignore[assignment]
    usage = Usage(input=1_000_000, output=1_000_000, total_tokens=2_000_000)
    usage.cost.total = 3.5
    await storage.save_call(_record(call_id="c1", usage=usage), ts=now.value)

    assert await storage.total_cost(60) == pytest.approx(3.5)


# --------------------------------------------------------------------------
# Trace：链路
# --------------------------------------------------------------------------


async def test_trace_returns_calls_in_time_order(storage):
    now: _Now = storage._now  # type: ignore[assignment]
    await storage.save_call(_record(call_id="second", trace_id="trace-x"), ts=now.value)
    await storage.save_call(_record(call_id="first", trace_id="trace-x"), ts=now.value - 10)
    await storage.save_call(_record(call_id="other", trace_id="trace-y"), ts=now.value)

    calls = await storage.trace("trace-x")

    assert [call["call_id"] for call in calls] == ["first", "second"]


async def test_search_matches_error_message_and_model(storage):
    await storage.save_call(
        _record(call_id="c1", terminal="error", error_message="upstream overloaded")
    )
    await storage.save_call(_record(call_id="c2"))

    assert [c["call_id"] for c in await storage.search_traces("overloaded")] == ["c1"]
    assert {c["call_id"] for c in await storage.search_traces("gpt-4o-mini")} == {"c1", "c2"}
    assert await storage.search_traces("no-such-thing") == []


async def test_spans_are_stored_per_trace(storage):
    await storage.save_span(span_id="s1", trace_id="trace-1", name="route", end_ms=5.0)
    await storage.save_span(
        span_id="s2", trace_id="trace-1", name="upstream", parent_span_id="s1", status="error"
    )

    cursor = await storage._conn().execute("SELECT COUNT(*) AS n FROM spans WHERE trace_id = ?", ("trace-1",))
    row = await cursor.fetchone()
    assert row["n"] == 2


# --------------------------------------------------------------------------
# model_health
# --------------------------------------------------------------------------


async def test_model_health_accumulates_success_and_error(storage):
    await storage.save_call(_record(call_id="c1"))
    await storage.save_call(_record(call_id="c2", terminal="error", error_message="boom"))

    health = await storage.model_health()

    assert len(health) == 1
    assert health[0]["model"] == "gpt-4o-mini"
    assert health[0]["success_count"] == 1
    assert health[0]["error_count"] == 1
    assert health[0]["status"] == "degraded"
    assert health[0]["last_error"] == "boom"


# --------------------------------------------------------------------------
# Query 视图
# --------------------------------------------------------------------------


async def test_dashboard_aggregates_metrics_and_health(storage):
    now: _Now = storage._now  # type: ignore[assignment]
    await storage.save_call(_record(call_id="c1", total_ms=42.0), ts=now.value)

    dashboard = await Query(storage).dashboard()

    assert dashboard["qps_1m"] == pytest.approx(1 / 60)
    assert dashboard["p99_latency_1m"] == 42.0
    assert dashboard["error_rate_1m"] == 0.0
    assert len(dashboard["health"]) == 1


async def test_trend_returns_one_sample_per_window(storage):
    trend = await Query(storage).trend()

    assert [sample["window_seconds"] for sample in trend["qps"]] == [60, 3600, 86400]
    assert len(trend["error_rate"]) == 3


async def test_query_trace_view(storage):
    await storage.save_call(_record(call_id="c1", trace_id="trace-z"))

    view = await Query(storage).trace("trace-z")

    assert view["trace_id"] == "trace-z"
    assert [call["call_id"] for call in view["calls"]] == ["c1"]
