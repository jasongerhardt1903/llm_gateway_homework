import { Fragment, useEffect, useRef, useState } from "react";
import { List, Search, ScrollText, Waypoints } from "lucide-react";
import { getTrace, listExchanges, parse_frame, searchTraces } from "../api.js";
import { PageHeader } from "../components/ui/page-header.jsx";
import { Card } from "../components/ui/card.jsx";
import { DataTable } from "../components/ui/data-table.jsx";
import { Table, TBody, Td, Th, THead, Tr } from "../components/ui/table.jsx";
import { Alert } from "../components/ui/alert.jsx";
import { Badge } from "../components/ui/badge.jsx";
import { Button } from "../components/ui/button.jsx";
import { Input } from "../components/ui/field.jsx";

/**
 * Trace 页：结构化展示 + 关键字搜索 + 按任务分组的时间轴瀑布图 + 通讯原始日志。
 *
 * 搜索命中后有三种视图：
 * - 「列表」：逐条调用明细列表（表头可排序）；
 * - 「任务视图」：按 run_id（agent 的一次 task）分组，组内每次调用一根横条，
 *   条宽正比于 total_ms、TTFT 段用浅色标出、颜色按终态区分，组头给出任务级汇总。
 * - 「通讯日志」：按 task_id / 每次通讯两级组合的原始往来报文（需求 Harness 层
 *   第 3 条），可折叠展开，支持 raw / 渲染两种模式与按字段搜索。
 *
 * 前两种视图点开某条都按 trace_id 拉取整条链路，按需求中的 8 个维度
 * （关联 / Prompt / 路由 / 用量 / 延迟 / 弹性 / 结果 / 错误 / 成本）分组渲染，
 * 而不是把 JSON 原样倾倒。
 */
export default function TracePage() {
  const [keyword, setKeyword] = useState("");
  const [rows, setRows] = useState([]);
  const [chain, setChain] = useState(null);
  const [selected, setSelected] = useState(null);
  const [error, setError] = useState("");
  const [view, setView] = useState("list");

  // 通讯原始日志（需求 Harness 层第 3 条）的独立状态。
  const [exKeyword, setExKeyword] = useState("");
  const [exTaskId, setExTaskId] = useState("");
  const [exchanges, setExchanges] = useState([]);
  const [exLoading, setExLoading] = useState(false);
  const [openGroups, setOpenGroups] = useState(() => new Set());
  const [openRows, setOpenRows] = useState(() => new Set());
  const exLoadedRef = useRef(false);

  const runSearch = async (value = keyword) => {
    try {
      setRows(await searchTraces(value));
      setChain(null);
      setError("");
    } catch (err) {
      setError(err.message);
    }
  };

  const runExchangeSearch = async ({ q = exKeyword, taskId = exTaskId } = {}) => {
    setExLoading(true);
    try {
      const list = await listExchanges({ q, taskId });
      setExchanges(list);
      // 默认展开所有 task 分组：日志页的目的是"看见往来内容"，全折叠等于什么都不显示。
      setOpenGroups(new Set(group_exchanges(list).map((group) => group.task_id)));
      setOpenRows(new Set());
      setError("");
    } catch (err) {
      setError(err.message);
    } finally {
      setExLoading(false);
    }
  };

  useEffect(() => {
    runSearch("");
  }, []);

  // 第一次切到通讯日志视图时惰性加载，之后由搜索/筛选按钮驱动。
  useEffect(() => {
    if (view === "exchanges" && !exLoadedRef.current) {
      exLoadedRef.current = true;
      runExchangeSearch();
    }
  }, [view]);

  const filter_by_task = (taskId) => {
    setExTaskId(taskId);
    runExchangeSearch({ taskId });
  };

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

  const columns = [
    {
      id: "ts",
      header: "时间",
      accessorFn: (row) => Number(row.ts ?? 0),
      cell: ({ row }) => (
        <span className="whitespace-nowrap text-muted">{format_time(row.original.ts)}</span>
      ),
    },
    {
      accessorKey: "trace_id",
      header: "Trace",
      cell: ({ getValue }) => (
        <span className="font-mono text-xs">{getValue() || "—"}</span>
      ),
    },
    {
      accessorKey: "model",
      header: "模型",
      cell: ({ getValue }) => getValue() || <span className="text-faint">—</span>,
    },
    {
      accessorKey: "terminal",
      header: "终态",
      cell: ({ getValue }) => (
        <Badge tone={terminal_class(getValue())}>{getValue()}</Badge>
      ),
    },
    {
      accessorKey: "total_ms",
      header: "总延迟",
      cell: ({ getValue }) => <span className="tabular">{round(getValue())} ms</span>,
    },
    {
      accessorKey: "ttft_ms",
      header: "TTFT",
      cell: ({ getValue }) => <span className="tabular">{round(getValue())} ms</span>,
    },
    {
      id: "tokens",
      header: "token",
      accessorFn: (row) => Number(row.usage?.total_tokens ?? 0),
      cell: ({ getValue }) => <span className="tabular">{getValue()}</span>,
    },
    {
      id: "cost",
      header: "成本",
      accessorFn: (row) => Number(row.cost?.total ?? 0),
      cell: ({ getValue }) => <span className="tabular">${getValue().toFixed(6)}</span>,
    },
  ];

  /** 集合开关：折叠/展开分组或某条通讯时复用。 */
  const toggle_in_set = (setter, key) =>
    setter((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div>
      <PageHeader
        title="Trace"
        description="按 trace_id / call_id / 模型 / prompt 名 / 错误信息检索调用记录，并展开完整链路或通讯原始日志。"
      />

      {error && <Alert className="mb-3">{error}</Alert>}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {view === "exchanges" ? (
          <>
            <Input
              className="min-w-[260px] flex-1"
              value={exKeyword}
              onChange={(e) => setExKeyword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runExchangeSearch()}
              placeholder="搜索通讯原文（request / response 内容）"
            />
            <Button onClick={() => runExchangeSearch()}>
              <Search size={14} />
              搜索
            </Button>
            {exTaskId && (
              <>
                <Badge tone="accent">task: {exTaskId}</Badge>
                <Button
                  variant="ghost"
                  onClick={() => {
                    setExTaskId("");
                    runExchangeSearch({ taskId: "" });
                  }}
                >
                  清除筛选
                </Button>
              </>
            )}
          </>
        ) : (
          <>
            <Input
              className="min-w-[260px] flex-1"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runSearch()}
              placeholder="搜索 trace_id / call_id / 模型 / prompt 名 / 错误信息"
            />
            <Button onClick={() => runSearch()}>
              <Search size={14} />
              搜索
            </Button>
          </>
        )}
        <div className="flex items-center gap-1 rounded-md border border-border p-0.5">
          <Button
            size="sm"
            variant={view === "list" ? "primary" : "ghost"}
            onClick={() => setView("list")}
          >
            <List size={12} />
            列表
          </Button>
          <Button
            size="sm"
            variant={view === "tasks" ? "primary" : "ghost"}
            onClick={() => setView("tasks")}
          >
            <Waypoints size={12} />
            任务视图
          </Button>
          <Button
            size="sm"
            variant={view === "exchanges" ? "primary" : "ghost"}
            onClick={() => setView("exchanges")}
          >
            <ScrollText size={12} />
            通讯日志
          </Button>
        </div>
      </div>

      {view === "exchanges" ? (
        <ExchangeLog
          exchanges={exchanges}
          loading={exLoading}
          openGroups={openGroups}
          openRows={openRows}
          onToggleGroup={(taskId) => toggle_in_set(setOpenGroups, taskId)}
          onToggleRow={(id) => toggle_in_set(setOpenRows, id)}
          onFilterTask={filter_by_task}
        />
      ) : view === "list" ? (
        <DataTable
          columns={columns}
          data={rows}
          getRowKey={(row) => row.call_id}
          onRowClick={(row) => openTrace(row.trace_id)}
          isRowActive={(row) => row.trace_id === selected}
          empty="没有匹配的调用记录。"
        />
      ) : (
        <TaskWaterfall rows={rows} selected={selected} onSelect={openTrace} />
      )}

      {chain && (
        <Card className="mt-4">
          <h3 className="mb-3 text-sm font-semibold text-fg">
            链路 <span className="font-mono text-xs text-muted">{selected}</span>
          </h3>
          {chain.map((call) => (
            <TraceDetail key={call.call_id} call={call} />
          ))}
        </Card>
      )}
    </div>
  );
}

/**
 * 任务视图：按 run_id 分组的时间轴瀑布图。
 *
 * 每根横条的宽度以「本次结果里最长的调用」为基准，因此跨任务也能横向比较；
 * 组头给出该任务的调用数 / 合计耗时 / 错误数 / 合计成本。
 */
export function TaskWaterfall({ rows, selected, onSelect }) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted">没有匹配的调用记录。</p>;
  }

  const groups = group_by_task(rows);
  const maxTotalMs = Math.max(...rows.map((row) => Number(row.total_ms ?? 0)), 0);

  return (
    <div className="waterfall">
      {groups.map((group) => {
        const totalMs = group.calls.reduce((sum, call) => sum + Number(call.total_ms ?? 0), 0);
        const cost = group.calls.reduce((sum, call) => sum + Number(call.cost?.total ?? 0), 0);
        const errors = group.calls.filter((call) => call.terminal === "error").length;
        return (
          <section className="task-group" key={group.run_id || "__unlabeled__"}>
            <header className="task-head">
              <span className="font-mono text-xs text-fg">
                {group.run_id || "未标记任务"}
              </span>
              <span className="text-xs text-muted">
                {group.calls.length} 次调用 · 合计 {round(totalMs)} ms · 错误 {errors} · $
                {cost.toFixed(6)}
              </span>
            </header>
            {group.calls.map((call) => {
              const { width, ttftShare } = bar_geometry(call, maxTotalMs);
              return (
                <div
                  key={call.call_id}
                  className={
                    call.trace_id === selected
                      ? "waterfall-row clickable row-selected"
                      : "waterfall-row clickable"
                  }
                  onClick={() => onSelect(call.trace_id)}
                >
                  <span className="waterfall-label">
                    <Badge tone={terminal_class(call.terminal)}>{call.terminal}</Badge>
                    <span className="font-mono text-xs">{call.model || "—"}</span>
                  </span>
                  <span className="waterfall-track">
                    <span
                      className={`waterfall-bar waterfall-${terminal_class(call.terminal)}`}
                      style={{ width: `${width}%` }}
                    >
                      <span className="waterfall-ttft" style={{ width: `${ttftShare}%` }} />
                    </span>
                  </span>
                  <span className="waterfall-value font-mono text-xs">
                    {round(call.total_ms)} ms
                  </span>
                </div>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}

/** 按 run_id 把调用记录分组；run_id 为空的归入「未标记任务」。组内按时间正序。 */
export function group_by_task(rows) {
  const groups = new Map();
  for (const row of rows) {
    const key = row.run_id || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  }
  return [...groups.entries()].map(([run_id, calls]) => ({
    run_id,
    calls: [...calls].sort((a, b) => Number(a.ts ?? 0) - Number(b.ts ?? 0)),
  }));
}

/** 单根瀑布条的几何：条宽（相对全局最长调用）与 TTFT 在条内的占比，均为百分比。 */
export function bar_geometry(call, maxTotalMs) {
  const total = Number(call.total_ms ?? 0);
  const ttft = Number(call.ttft_ms ?? 0);
  const width = maxTotalMs > 0 ? Math.max(2, (total / maxTotalMs) * 100) : 2;
  const ttftShare = total > 0 ? Math.min(100, (ttft / total) * 100) : 0;
  return { width, ttftShare };
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
        disposition: call.disposition,
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
      <summary className="cursor-pointer select-none text-sm hover:text-fg2">
        <span className="font-mono text-xs">{call.call_id}</span> · {call.model} ·{" "}
        {call.terminal}
      </summary>
      <div className="kv-groups">
        {groups.map(([title, entries]) => (
          <div className="kv" key={title}>
            <div className="kv-title">{title}</div>
            {Object.entries(entries).map(([key, value]) => (
              <div className="kv-row" key={key}>
                <span className="kv-key">{key}</span>
                <span className="kv-value font-mono text-xs">{display(value)}</span>
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

/* ==========================================================================
   通讯原始日志（需求 Harness 层第 3 条）
   --------------------------------------------------------------------------
   两级组合：第一级按 task_id 分组（可折叠），第二级是组内按 flow_index 升序的
   每次通讯（可折叠）。每条通讯展开后有 request_raw / response_raw 两栏，可在
   "原始"与"渲染后易读"两种模式间切换。
   ========================================================================== */

/** 第一级分组：按 task_id 聚合，组内按 flow_index 升序；记录组内最后一次时间。 */
export function group_exchanges(list) {
  const groups = new Map();
  for (const item of list) {
    const key = item.task_id || "";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return [...groups.entries()].map(([task_id, items]) => ({
    task_id,
    items: [...items].sort((a, b) => Number(a.flow_index ?? 0) - Number(b.flow_index ?? 0)),
    last_ts: items.reduce((max, item) => Math.max(max, Number(item.ts ?? 0)), 0),
  }));
}

/** 组头状态汇总：按 status 计数。 */
export function status_summary(items) {
  const counts = new Map();
  for (const item of items) {
    const key = item.status || "unknown";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].map(([status, count]) => ({ status, count }));
}

/**
 * 把 raw 文本归类成可读取的结构。
 *
 * JSON 报文直接美化；SSE 文本逐帧解析成"事件类型 + data"；两者都不像时按原文
 * 显示——绝不因为解析失败就把内容吞掉。
 */
export function classify_payload(text) {
  const trimmed = String(text ?? "").trim();
  if (!trimmed) return { kind: "empty" };
  if (trimmed[0] === "{" || trimmed[0] === "[") {
    try {
      return { kind: "json", pretty: JSON.stringify(JSON.parse(trimmed), null, 2) };
    } catch {
      /* 不是合法 JSON，交给下面的 SSE 解析 */
    }
  }
  const frames = parse_sse_frames(trimmed);
  if (frames.length) return { kind: "sse", frames };
  return { kind: "text", raw: trimmed };
}

/** 按空行分帧后复用 api.js 的 :func:`parse_frame`，避免两处各写一套规则。 */
export function parse_sse_frames(text) {
  const frames = [];
  for (const block of String(text ?? "").split(/\n\n+/)) {
    if (!block.trim()) continue;
    const frame = parse_frame(block);
    if (frame) frames.push(frame);
  }
  return frames;
}

/** SSE 的 data 是 JSON 时美化，否则原样返回。 */
function pretty_data(data) {
  try {
    return JSON.stringify(JSON.parse(data), null, 2);
  } catch {
    return String(data ?? "");
  }
}

/** 通讯日志区块：两级折叠 + 每条通讯的字段表格。 */
function ExchangeLog({ exchanges, loading, openGroups, openRows, onToggleGroup, onToggleRow, onFilterTask }) {
  if (loading && exchanges.length === 0) {
    return <p className="text-sm text-muted">加载中…</p>;
  }
  if (exchanges.length === 0) {
    return <p className="text-sm text-muted">没有匹配的通讯记录。</p>;
  }

  const groups = group_exchanges(exchanges);
  return (
    <div className="flex flex-col gap-3">
      {groups.map((group) => {
        const open = openGroups.has(group.task_id);
        return (
          <section key={group.task_id || "__unlabeled__"} className="task-group">
            <header className="task-head">
              <button
                type="button"
                onClick={() => onToggleGroup(group.task_id)}
                className="flex cursor-pointer items-center gap-2 text-left"
              >
                <span className="text-muted">{open ? "▾" : "▸"}</span>
                <span className="font-mono text-xs text-fg">
                  {group.task_id || "未标记 task"}
                </span>
              </button>
              <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted">
                {group.items.length} 次通讯 · 最后 {format_time(group.last_ts)}
                {status_summary(group.items).map(({ status, count }) => (
                  <Badge key={status} tone={terminal_class(status)}>
                    {status} {count}
                  </Badge>
                ))}
                <Button variant="ghost" size="sm" onClick={() => onFilterTask(group.task_id)}>
                  只看此任务
                </Button>
              </span>
            </header>
            {open && (
              <ExchangeTable items={group.items} openRows={openRows} onToggleRow={onToggleRow} />
            )}
          </section>
        );
      })}
    </div>
  );
}

/** 一条通讯一行、每字段一列；展开后在下一行给出 request / response 两栏原文。 */
const EXCHANGE_COLUMNS = [
  "#",
  "endpoint",
  "stream",
  "status",
  "error_code",
  "trace_id",
  "model",
  "duration_ms",
  "时间",
  "操作",
];

function ExchangeTable({ items, openRows, onToggleRow }) {
  return (
    <Table>
      <THead>
        <Tr>
          {EXCHANGE_COLUMNS.map((header) => (
            <Th key={header}>{header}</Th>
          ))}
        </Tr>
      </THead>
      <TBody>
        {items.map((ex) => {
          const open = openRows.has(ex.exchange_id);
          return (
            <Fragment key={ex.exchange_id}>
              <Tr>
                <Td className="tabular">{ex.flow_index}</Td>
                <Td className="font-mono text-xs">{ex.endpoint}</Td>
                <Td>
                  <Badge tone={ex.stream ? "accent" : "neutral"}>
                    {ex.stream ? "流式" : "非流式"}
                  </Badge>
                </Td>
                <Td>
                  <Badge tone={terminal_class(ex.status)}>{ex.status}</Badge>
                </Td>
                <Td className="font-mono text-xs">{ex.error_code || "—"}</Td>
                <Td className="font-mono text-xs">{ex.trace_id || "—"}</Td>
                <Td>{ex.meta?.model || "—"}</Td>
                <Td className="tabular">{round(ex.duration_ms)}</Td>
                <Td className="whitespace-nowrap text-muted">{format_time(ex.ts)}</Td>
                <Td>
                  <Button variant="ghost" size="sm" onClick={() => onToggleRow(ex.exchange_id)}>
                    {open ? "收起" : "展开报文"}
                  </Button>
                </Td>
              </Tr>
              {open && (
                <Tr>
                  <Td colSpan={EXCHANGE_COLUMNS.length}>
                    <div className="grid gap-3 lg:grid-cols-2">
                      <RawPanel title="request_raw" text={ex.request_raw} />
                      <RawPanel title="response_raw" text={ex.response_raw} />
                    </div>
                  </Td>
                </Tr>
              )}
            </Fragment>
          );
        })}
      </TBody>
    </Table>
  );
}

/** 单栏原始报文：raw 模式原样显示，渲染模式美化 JSON / 逐帧展开 SSE。 */
function RawPanel({ title, text }) {
  const [mode, setMode] = useState("raw");
  const parsed = classify_payload(text);
  return (
    <div className="rounded-md border border-border-soft bg-raised p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted">{title}</span>
        <div className="flex items-center gap-1">
          <Button
            size="sm"
            variant={mode === "raw" ? "primary" : "ghost"}
            onClick={() => setMode("raw")}
          >
            raw
          </Button>
          <Button
            size="sm"
            variant={mode === "render" ? "primary" : "ghost"}
            onClick={() => setMode("render")}
          >
            渲染
          </Button>
        </div>
      </div>
      {mode === "raw" ? (
        <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap break-words text-xs text-fg2">
          {text || "—"}
        </pre>
      ) : (
        <RenderedPayload parsed={parsed} />
      )}
    </div>
  );
}

function RenderedPayload({ parsed }) {
  if (parsed.kind === "empty") {
    return <p className="text-xs text-faint">（空）</p>;
  }
  if (parsed.kind === "json") {
    return (
      <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap break-words text-xs text-fg2">
        {parsed.pretty}
      </pre>
    );
  }
  if (parsed.kind === "sse") {
    return (
      <ol className="flex flex-col gap-1.5">
        {parsed.frames.map((frame, index) => (
          <li key={index} className="rounded-md border border-border-soft bg-panel px-2.5 py-1.5">
            <Badge tone="accent">{frame.event || "message"}</Badge>
            <pre className="mt-1 whitespace-pre-wrap break-words text-xs text-fg2">
              {pretty_data(frame.data)}
            </pre>
          </li>
        ))}
      </ol>
    );
  }
  return (
    <div>
      <p className="mb-1 text-xs text-warn">无法解析为 JSON / SSE，按原文显示。</p>
      <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap break-words text-xs text-fg2">
        {parsed.raw || "—"}
      </pre>
    </div>
  );
}
