"""SQLite 存储：Metrics / Logs / Trace 三种消费方共用一份数据。

需求要求可观测性覆盖 Metrics（聚合趋势）、Logs（单次调用明细）、Trace（链路）。
三者不是三套系统，而是同一份调用记录的不同切法：

* 一次调用写一行 ``requests``（= Logs 明细）；
* 该行的 ``trace_id / run_id / step_id / call_id`` 串联即 Trace；
* 对 ``requests`` 按时间窗聚合即 Metrics（QPS / p99 / 错误率）。

另有 ``cost_ledger``（成本账本，独立成表以便按账期结算）、``model_health``
（模型健康，供路由动态选择）与 ``exchanges``（与后端 agent 的原始往来报文）。

``exchanges`` 与 ``requests`` 刻意分开：前者是**通讯层**事实（agent 发来什么字节、
网关回了什么字节），后者是**调用层**事实（翻译后的请求落到哪个模型、花了多少钱）。
一次通讯可能对应 0 次或多次模型调用（校验失败时一次都没有），因此不能合成一张表。
需求 Harness 层功能第 3 条要求日志"按 task id / 每次通讯的流程两个层级组合"，
``(task_id, flow_index)`` 就是那两级。

时间来自注入的 ``now`` 函数，因此窗口类指标可以被测试精确驱动，
不需要真实等待。

版本：0.8.5
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import aiosqlite

from ..core.errors import redact_mapping, redact_text
from ..core.telemetry import CallRecord

__all__ = ["Storage"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    call_id             TEXT PRIMARY KEY,
    trace_id            TEXT NOT NULL DEFAULT '',
    run_id              TEXT NOT NULL DEFAULT '',
    step_id             TEXT NOT NULL DEFAULT '',
    ts                  REAL NOT NULL,
    profile             TEXT NOT NULL DEFAULT '',
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
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);
CREATE INDEX IF NOT EXISTS idx_requests_trace ON requests(trace_id);

CREATE TABLE IF NOT EXISTS spans (
    span_id         TEXT PRIMARY KEY,
    trace_id        TEXT NOT NULL,
    parent_span_id  TEXT,
    name            TEXT NOT NULL DEFAULT '',
    start_ms        REAL NOT NULL DEFAULT 0,
    end_ms          REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'ok',
    attributes      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);

CREATE TABLE IF NOT EXISTS cost_ledger (
    entry_id            TEXT PRIMARY KEY,
    ts                  REAL NOT NULL,
    trace_id            TEXT NOT NULL DEFAULT '',
    call_id             TEXT NOT NULL DEFAULT '',
    provider            TEXT NOT NULL DEFAULT '',
    model               TEXT NOT NULL DEFAULT '',
    input_cost          REAL NOT NULL DEFAULT 0,
    output_cost         REAL NOT NULL DEFAULT 0,
    cache_read_cost     REAL NOT NULL DEFAULT 0,
    cache_write_cost    REAL NOT NULL DEFAULT 0,
    total_cost          REAL NOT NULL DEFAULT 0,
    currency            TEXT NOT NULL DEFAULT 'USD'
);
CREATE INDEX IF NOT EXISTS idx_cost_ts ON cost_ledger(ts);

CREATE TABLE IF NOT EXISTS model_health (
    model            TEXT PRIMARY KEY,
    provider         TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'unknown',
    success_count    INTEGER NOT NULL DEFAULT 0,
    error_count      INTEGER NOT NULL DEFAULT 0,
    last_error       TEXT,
    last_success_ts  REAL,
    updated_at       REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exchanges (
    exchange_id   TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL DEFAULT '',
    trace_id      TEXT NOT NULL DEFAULT '',
    ts            REAL NOT NULL,
    flow_index    INTEGER NOT NULL DEFAULT 1,
    endpoint      TEXT NOT NULL DEFAULT '',
    stream        INTEGER NOT NULL DEFAULT 0,
    request_raw   TEXT NOT NULL DEFAULT '',
    response_raw  TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'done',
    error_code    TEXT,
    error_message TEXT,
    duration_ms   REAL NOT NULL DEFAULT 0,
    meta          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_exchanges_task ON exchanges(task_id);
CREATE INDEX IF NOT EXISTS idx_exchanges_ts ON exchanges(ts);
"""


class Storage:
    """基于 SQLite 的调用记录存储。

    ``path`` 为 ``":memory:"`` 时使用内存库（测试用）。
    """

    def __init__(self, path: str | Path = ":memory:", *, now: Callable[[], float] | None = None) -> None:
        self.path = str(path)
        self._now = now or time.time
        self._db: aiosqlite.Connection | None = None

    async def init(self) -> "Storage":
        """建表并打开连接。可重复调用。"""
        if self._db is not None:
            return self
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._migrate()
        await self._db.commit()
        return self

    async def _migrate(self) -> None:
        """把旧库文件升级到当前 schema。

        0.2.0 把 ``requests.logical_model`` 改名为 ``profile``（逻辑模型概念被
        gwprofile 取代）。``CREATE TABLE IF NOT EXISTS`` 不会改动已存在的表，
        因此这里显式重命名，避免旧库文件在启动时报"no such column"。
        """
        db = self._conn()
        cursor = await db.execute("PRAGMA table_info(requests)")
        columns = {row["name"] for row in await cursor.fetchall()}
        if "logical_model" in columns and "profile" not in columns:
            await db.execute("ALTER TABLE requests RENAME COLUMN logical_model TO profile")

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> "Storage":
        return await self.init()

    async def __aexit__(self, *exc_info) -> None:
        await self.close()

    def _conn(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage 尚未初始化，请先 await storage.init()")
        return self._db

    # -- 写入 --------------------------------------------------------------

    async def save_call(self, record: CallRecord, *, ts: float | None = None) -> dict[str, Any]:
        """写入一次调用：requests + cost_ledger + model_health 三处同步更新。

        返回落库用的扁平字典，便于测试断言与 Web 直接复用。
        """
        db = self._conn()
        flat = record.to_dict()
        timestamp = self._now() if ts is None else ts
        # 时间戳只在库里作为独立列存在，但 Trace 页需要展示"请求时间"，
        # 因此冗余进 payload，避免前端再回查一次。
        flat["ts"] = timestamp

        await db.execute(
            """
            INSERT OR REPLACE INTO requests (
                call_id, trace_id, run_id, step_id, ts,
                profile, provider, model, api,
                prompt_name, prompt_version, prompt_sha256,
                terminal, finish_reason, error_code, error_message,
                http_status, provider_request_id,
                attempt, retry, fallback, output_valid,
                queue_ms, route_ms, ttft_ms, generation_ms, total_ms,
                input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                reasoning_tokens, total_tokens, cost_total, stream_chunk_count, payload
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                record.call_id,
                record.trace_id,
                record.run_id,
                record.step_id,
                timestamp,
                record.profile,
                record.provider,
                record.model,
                record.api,
                record.prompt.name,
                record.prompt.version,
                record.prompt.sha256,
                record.terminal,
                record.finish_reason,
                flat["error_code"],
                flat["error_message"],
                record.http_status,
                record.provider_request_id,
                record.resilience.attempt,
                record.resilience.retry,
                1 if record.resilience.fallback else 0,
                None if record.output_valid is None else (1 if record.output_valid else 0),
                record.latency.queue_ms,
                record.latency.route_ms,
                record.latency.ttft_ms,
                record.latency.generation_ms,
                record.latency.total_ms,
                record.usage.input,
                record.usage.output,
                record.usage.cache_read,
                record.usage.cache_write,
                record.usage.reasoning,
                record.usage.effective_total_tokens(),
                record.cost.total,
                record.stream_chunk_count,
                json.dumps(flat, ensure_ascii=False, default=str),
            ),
        )

        await db.execute(
            """
            INSERT OR REPLACE INTO cost_ledger (
                entry_id, ts, trace_id, call_id, provider, model,
                input_cost, output_cost, cache_read_cost, cache_write_cost, total_cost, currency
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                f"cost-{record.call_id}",
                timestamp,
                record.trace_id,
                record.call_id,
                record.provider,
                record.model,
                record.cost.input,
                record.cost.output,
                record.cost.cache_read,
                record.cost.cache_write,
                record.cost.total,
                "USD",
            ),
        )

        await self._update_health(record, timestamp)
        await db.commit()
        return flat

    async def _update_health(self, record: CallRecord, ts: float) -> None:
        db = self._conn()
        if not record.model:
            return
        success = 1 if record.terminal == "done" else 0
        error = 1 if record.terminal == "error" else 0
        await db.execute(
            """
            INSERT INTO model_health (model, provider, status, success_count, error_count,
                                      last_error, last_success_ts, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model) DO UPDATE SET
                provider        = excluded.provider,
                status          = excluded.status,
                success_count   = model_health.success_count + excluded.success_count,
                error_count     = model_health.error_count + excluded.error_count,
                last_error      = COALESCE(excluded.last_error, model_health.last_error),
                last_success_ts = COALESCE(excluded.last_success_ts, model_health.last_success_ts),
                updated_at      = excluded.updated_at
            """,
            (
                record.model,
                record.provider,
                "healthy" if success else "degraded",
                success,
                error,
                record.error_message if error else None,
                ts if success else None,
                ts,
            ),
        )

    async def save_span(
        self,
        *,
        span_id: str,
        trace_id: str,
        name: str,
        start_ms: float = 0.0,
        end_ms: float = 0.0,
        parent_span_id: str | None = None,
        status: str = "ok",
        attributes: dict[str, Any] | None = None,
    ) -> None:
        db = self._conn()
        await db.execute(
            """
            INSERT OR REPLACE INTO spans (span_id, trace_id, parent_span_id, name, start_ms, end_ms, status, attributes)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                span_id,
                trace_id,
                parent_span_id,
                name,
                start_ms,
                end_ms,
                status,
                json.dumps(attributes or {}, ensure_ascii=False, default=str),
            ),
        )
        await db.commit()

    # -- Metrics -----------------------------------------------------------

    async def qps(self, window_seconds: float) -> float:
        """窗口内的平均每秒请求数。"""
        if window_seconds <= 0:
            return 0.0
        count = await self._count_since(window_seconds)
        return count / window_seconds

    async def error_rate(self, window_seconds: float) -> float:
        """窗口内的错误率（error 终态 / 全部调用）。"""
        total = await self._count_since(window_seconds)
        if total == 0:
            return 0.0
        errors = await self._count_since(window_seconds, terminal="error")
        return errors / total

    async def p99_latency(self, window_seconds: float) -> float:
        """窗口内总延迟的 p99（毫秒）。

        样本在 Python 侧计算：SQLite 没有内置百分位函数，且窗口样本量可控。
        """
        values = await self._latencies_since(window_seconds)
        if not values:
            return 0.0
        values.sort()
        # 最近秩法：p99 取第 ceil(0.99 * n) 个样本（1-indexed）。
        import math

        index = max(0, min(len(values) - 1, math.ceil(0.99 * len(values)) - 1))
        return values[index]

    async def total_cost(self, window_seconds: float) -> float:
        db = self._conn()
        cursor = await db.execute(
            "SELECT COALESCE(SUM(total_cost), 0) AS total FROM cost_ledger WHERE ts >= ?",
            (self._now() - window_seconds,),
        )
        row = await cursor.fetchone()
        return float(row["total"] or 0.0)

    async def _count_since(self, window_seconds: float, *, terminal: str | None = None) -> int:
        db = self._conn()
        since = self._now() - window_seconds
        if terminal is None:
            cursor = await db.execute("SELECT COUNT(*) AS n FROM requests WHERE ts >= ?", (since,))
        else:
            cursor = await db.execute(
                "SELECT COUNT(*) AS n FROM requests WHERE ts >= ? AND terminal = ?", (since, terminal)
            )
        row = await cursor.fetchone()
        return int(row["n"] or 0)

    async def _latencies_since(self, window_seconds: float) -> list[float]:
        db = self._conn()
        cursor = await db.execute(
            "SELECT total_ms FROM requests WHERE ts >= ?", (self._now() - window_seconds,)
        )
        rows = await cursor.fetchall()
        return [float(row["total_ms"]) for row in rows]

    # -- Logs / Trace ------------------------------------------------------

    async def recent_calls(self, limit: int = 50, *, window_seconds: float | None = None) -> list[dict[str, Any]]:
        """最近的调用明细（Logs）。"""
        db = self._conn()
        if window_seconds is None:
            cursor = await db.execute("SELECT payload FROM requests ORDER BY ts DESC LIMIT ?", (limit,))
        else:
            cursor = await db.execute(
                "SELECT payload FROM requests WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                (self._now() - window_seconds, limit),
            )
        rows = await cursor.fetchall()
        return [json.loads(row["payload"]) for row in rows]

    async def trace(self, trace_id: str) -> list[dict[str, Any]]:
        """按 trace_id 取整条链路，按时间升序。"""
        db = self._conn()
        cursor = await db.execute(
            "SELECT payload FROM requests WHERE trace_id = ? ORDER BY ts ASC", (trace_id,)
        )
        rows = await cursor.fetchall()
        return [json.loads(row["payload"]) for row in rows]

    async def search_traces(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        """关键字搜索：匹配 trace_id / call_id / model / prompt 名 / 错误信息。

        用参数化 LIKE 而不是拼接 SQL，避免注入。
        """
        db = self._conn()
        pattern = f"%{query}%"
        cursor = await db.execute(
            """
            SELECT payload FROM requests
            WHERE trace_id LIKE ?
               OR call_id LIKE ?
               OR model LIKE ?
               OR prompt_name LIKE ?
               OR COALESCE(error_message, '') LIKE ?
            ORDER BY ts DESC LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, pattern, limit),
        )
        rows = await cursor.fetchall()
        return [json.loads(row["payload"]) for row in rows]

    # -- 通讯原始往来数据（需求 Harness 层功能第 3 条） ---------------------

    async def save_exchange(
        self,
        *,
        task_id: str,
        request_raw: str | bytes = "",
        response_raw: str = "",
        trace_id: str = "",
        endpoint: str = "",
        stream: bool = False,
        status: str = "done",
        error_code: str | None = None,
        error_message: str | None = None,
        duration_ms: float = 0.0,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """落一条"与后端 agent 的通讯"。

        ``flow_index`` 按 ``task_id`` 自增：同一个 task 可能来回多次（校验失败重发、
        流式重连、多轮补充），需求要求按"task id / 每次通讯流程"两级组织，这个序号
        就是第二级。

        报文一律先脱敏：agent 可能在 metadata 里夹带自己的凭据，落进库里就等于
        把凭据写到了磁盘上。
        """
        db = self._conn()
        raw_request = request_raw.decode("utf-8", "replace") if isinstance(request_raw, bytes) else request_raw

        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM exchanges WHERE task_id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        flow_index = int(row["n"] or 0) + 1

        exchange = {
            "exchange_id": f"ex-{uuid.uuid4().hex[:12]}",
            "task_id": task_id,
            "trace_id": trace_id or "",
            "ts": self._now(),
            "flow_index": flow_index,
            "endpoint": endpoint,
            "stream": stream,
            "request_raw": redact_text(raw_request),
            "response_raw": redact_text(response_raw),
            "status": status,
            "error_code": error_code,
            "error_message": redact_text(error_message) if error_message else None,
            "duration_ms": duration_ms,
            "meta": redact_mapping(meta or {}),
        }
        await db.execute(
            """
            INSERT OR REPLACE INTO exchanges (
                exchange_id, task_id, trace_id, ts, flow_index, endpoint, stream,
                request_raw, response_raw, status, error_code, error_message, duration_ms, meta
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                exchange["exchange_id"],
                exchange["task_id"],
                exchange["trace_id"],
                exchange["ts"],
                exchange["flow_index"],
                exchange["endpoint"],
                1 if stream else 0,
                exchange["request_raw"],
                exchange["response_raw"],
                exchange["status"],
                exchange["error_code"],
                exchange["error_message"],
                exchange["duration_ms"],
                json.dumps(exchange["meta"], ensure_ascii=False, default=str),
            ),
        )
        await db.commit()
        return exchange

    async def recent_exchanges(self, limit: int = 100) -> list[dict[str, Any]]:
        """最近的通讯记录（新的在前）。"""
        db = self._conn()
        cursor = await db.execute(
            "SELECT * FROM exchanges ORDER BY ts DESC, flow_index DESC LIMIT ?", (limit,)
        )
        return [_exchange_row(row) for row in await cursor.fetchall()]

    async def exchanges_for_task(self, task_id: str) -> list[dict[str, Any]]:
        """某个 task 下的全部通讯，按流程顺序升序。"""
        db = self._conn()
        cursor = await db.execute(
            "SELECT * FROM exchanges WHERE task_id = ? ORDER BY flow_index ASC", (task_id,)
        )
        return [_exchange_row(row) for row in await cursor.fetchall()]

    async def search_exchanges(self, query: str, limit: int = 200) -> list[dict[str, Any]]:
        """按字段搜通讯：task_id / trace_id / endpoint / 错误信息 / 两侧报文原文。

        报文是原文检索（需求："可以按照各个字段进行搜索和展示"），因此模型名、错误码
        这些出现在报文里的内容也能被搜到。
        """
        db = self._conn()
        pattern = f"%{query}%"
        cursor = await db.execute(
            """
            SELECT * FROM exchanges
            WHERE task_id LIKE ?
               OR trace_id LIKE ?
               OR endpoint LIKE ?
               OR COALESCE(error_code, '') LIKE ?
               OR COALESCE(error_message, '') LIKE ?
               OR request_raw LIKE ?
               OR response_raw LIKE ?
            ORDER BY ts DESC, flow_index DESC LIMIT ?
            """,
            (pattern, pattern, pattern, pattern, pattern, pattern, pattern, limit),
        )
        return [_exchange_row(row) for row in await cursor.fetchall()]

    async def exchange(self, exchange_id: str) -> dict[str, Any] | None:
        db = self._conn()
        cursor = await db.execute("SELECT * FROM exchanges WHERE exchange_id = ?", (exchange_id,))
        row = await cursor.fetchone()
        return _exchange_row(row) if row is not None else None

    # -- 模型健康 ----------------------------------------------------------

    async def model_health(self) -> list[dict[str, Any]]:
        db = self._conn()
        cursor = await db.execute(
            """
            SELECT model, provider, status, success_count, error_count, last_error, last_success_ts, updated_at
            FROM model_health ORDER BY model ASC
            """
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # -- 配置持久化 --------------------------------------------------------

    async def save_config(self, key: str, value: Any) -> None:
        """保存一份 JSON 配置（模型清单、路由规则等）。

        与调用记录分开存：配置是"怎么连"，记录是"连过什么"，生命周期不同。
        """
        db = self._conn()
        await db.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (key, json.dumps(value, ensure_ascii=False, default=str)),
        )
        await db.commit()

    async def load_config(self, key: str, default: Any = None) -> Any:
        db = self._conn()
        cursor = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if row is None:
            return default
        return json.loads(row["value"])


def _exchange_row(row: aiosqlite.Row) -> dict[str, Any]:
    """把 exchanges 行转成接口直接可用的字典。

    SQLite 没有布尔类型，入库时把 ``stream`` 存成了 0/1，出库还原成 ``bool``——
    否则前端拿到的 ``stream`` 是数字，``v-if="ex.stream"`` 这类判断虽然能跑，
    "原始 vs 流式"的语义却丢了。``meta`` 同理：库里是 JSON 字符串，出库还原成对象。
    """
    data = dict(row)
    data["stream"] = bool(data.get("stream"))
    raw_meta = data.get("meta") or "{}"
    try:
        data["meta"] = json.loads(raw_meta)
    except (json.JSONDecodeError, TypeError):
        data["meta"] = {}
    return data
