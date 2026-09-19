import { useEffect, useState } from "react";
import { getTrace, searchTraces } from "../api.js";

/**
 * Trace 页：结构化展示 + 关键字搜索。
 *
 * 搜索命中后展示调用明细列表；点开某条会按 trace_id 拉取整条链路，
 * 按需求中的 8 个维度（关联 / Prompt / 路由 / 用量 / 延迟 / 弹性 / 结果 / 错误 / 成本）
 * 分组渲染，而不是把 JSON 原样倾倒。
 */
export default function TracePage() {
  const [keyword, setKeyword] = useState("");
  const [rows, setRows] = useState([]);
  const [chain, setChain] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");

  const runSearch = async (value = keyword) => {
    try {
      setRows(await searchTraces(value));
      setChain(null);
      setError("");
    } catch (err) {
      setError(err.message);
    }
  };

  useEffect(() => {
    runSearch("");
  }, []);

  const openTrace = async (traceId) => {
    try {
      const payload = await getTrace(traceId);
      setChain(payload.calls);
      setSelected(traceId);
      setError("");
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <div className="page">
      <h2>Trace</h2>
      {error && <div className="banner banner-error">{error}</div>}

      <div className="row">
        <input
          className="grow"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runSearch()}
          placeholder="搜索 trace_id / call_id / 模型 / prompt 名 / 错误信息"
        />
        <button type="button" onClick={() => runSearch()}>
          搜索
        </button>
      </div>

      <table className="grid">
        <thead>
          <tr>
            <th>时间</th>
            <th>Trace</th>
            <th>模型</th>
            <th>终态</th>
            <th>总延迟</th>
            <th>TTFT</th>
            <th>token</th>
            <th>成本</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.call_id}
              className={row.trace_id === selected ? "row-selected" : ""}
              onClick={() => openTrace(row.trace_id)}
            >
              <td>{format_time(row.ts)}</td>
              <td className="mono">{row.trace_id || "—"}</td>
              <td>{row.model || "—"}</td>
              <td>
                <span className={`pill pill-${terminal_class(row.terminal)}`}>{row.terminal}</span>
              </td>
              <td>{round(row.total_ms)} ms</td>
              <td>{round(row.ttft_ms)} ms</td>
              <td>{row.usage?.total_tokens ?? 0}</td>
              <td>${Number(row.cost?.total ?? 0).toFixed(6)}</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan="8" className="muted">
                没有匹配的调用记录。
              </td>
            </tr>
          )}
        </tbody>
      </table>

      {chain && (
        <section>
          <h3>链路 {selected}</h3>
          {chain.map((call) => (
            <TraceDetail key={call.call_id} call={call} />
          ))}
        </section>
      )}
    </div>
  );
}

/** 一条调用的结构化明细。分组与需求中"每次 LLM 调用都记录的信息"表格一致。 */
function TraceDetail({ call }) {
  const groups = [
    [
      "关联",
      {
        trace_id: call.trace_id,
        run_id: call.run_id,
        step_id: call.step_id,
        call_id: call.call_id,
      },
    ],
    [
      "Prompt",
      {
        名称: call.prompt_name,
        版本: call.prompt_version,
        hash: call.prompt_sha256,
        schema: call.prompt_schema_version,
      },
    ],
    [
      "路由",
      {
        Profile: call.profile,
        供应商: call.provider,
        实际模型: call.model,
        协议: call.api,
      },
    ],
    [
      "用量",
      {
        input: call.usage?.input,
        output: call.usage?.output,
        cache_read: call.usage?.cache_read,
        cache_write: call.usage?.cache_write,
        reasoning: call.usage?.reasoning,
        total: call.usage?.total_tokens,
      },
    ],
    [
      "延迟",
      {
        queue_ms: round(call.queue_ms),
        route_ms: round(call.route_ms),
        ttft_ms: round(call.ttft_ms),
        generation_ms: round(call.generation_ms),
        total_ms: round(call.total_ms),
      },
    ],
    [
      "弹性",
      {
        attempt: call.attempt,
        retry: call.retry,
        fallback: call.fallback,
        timeout_budget_ms: call.timeout_budget_ms,
      },
    ],
    [
      "结果",
      {
        finish_reason: call.finish_reason,
        terminal: call.terminal,
        output_valid: call.output_valid,
        stream_chunk_count: call.stream_chunk_count,
      },
    ],
    [
      "错误",
      {
        error_code: call.error_code,
        error_message: call.error_message,
        http_status: call.http_status,
        provider_request_id: call.provider_request_id,
      },
    ],
    [
      "成本",
      {
        input: call.cost?.input,
        output: call.cost?.output,
        cache_read: call.cost?.cache_read,
        cache_write: call.cost?.cache_write,
        total: call.cost?.total,
      },
    ],
  ];

  return (
    <details className="trace-detail" open>
      <summary>
        <span className="mono">{call.call_id}</span> · {call.model} · {call.terminal}
      </summary>
      <div className="kv-groups">
        {groups.map(([title, entries]) => (
          <div className="kv" key={title}>
            <div className="kv-title">{title}</div>
            {Object.entries(entries).map(([key, value]) => (
              <div className="kv-row" key={key}>
                <span className="kv-key">{key}</span>
                <span className="kv-value mono">{display(value)}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </details>
  );
}

function display(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

const round = (value) => Number(value ?? 0).toFixed(1);

function terminal_class(terminal) {
  if (terminal === "done") return "ok";
  if (terminal === "error") return "bad";
  return "warn";
}

function format_time(value) {
  if (!value) return "—";
  const date = new Date(Number(value) * 1000);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", { hour12: false });
}