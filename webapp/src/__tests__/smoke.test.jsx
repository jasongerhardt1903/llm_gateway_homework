import { describe, expect, it, vi, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import App from "../App.jsx";
import { TaskWaterfall, bar_geometry, group_by_task } from "../pages/TracePage.jsx";
import { streamChat } from "../api.js";

describe("控制台冒烟", () => {
  it("渲染五个功能页签", () => {
    const html = renderToStaticMarkup(<App />);
    for (const label of ["模型定义", "Profile", "Chat", "Dashboard", "Trace"]) {
      expect(html).toContain(label);
    }
  });

  it("默认展示模型定义页", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("模型清单");
    expect(html).toContain("高级配置");
  });
});

describe("Trace 任务瀑布图", () => {
  const rows = [
    {
      call_id: "c1",
      trace_id: "t1",
      run_id: "task-a",
      ts: 200,
      model: "gpt-a",
      terminal: "done",
      total_ms: 100,
      ttft_ms: 20,
      cost: { total: 0.001 },
    },
    {
      call_id: "c2",
      trace_id: "t2",
      run_id: "task-a",
      ts: 100,
      model: "gpt-b",
      terminal: "error",
      total_ms: 400,
      ttft_ms: 0,
      cost: { total: 0.002 },
    },
    {
      call_id: "c3",
      trace_id: "t3",
      run_id: "",
      ts: 300,
      model: "gpt-c",
      terminal: "cancelled",
      total_ms: 200,
      ttft_ms: 50,
      cost: { total: 0 },
    },
  ];

  it("按 run_id 分组，空 run_id 归入未标记任务，组内按时间正序", () => {
    const groups = group_by_task(rows);
    expect(groups.map((g) => g.run_id)).toEqual(["task-a", ""]);
    // c2(ts=100) 应排在 c1(ts=200) 之前，且不改变入参顺序
    expect(groups[0].calls.map((c) => c.call_id)).toEqual(["c2", "c1"]);
    expect(rows.map((r) => r.call_id)).toEqual(["c1", "c2", "c3"]);
  });

  it("条宽相对全局最长调用，TTFT 为条内占比", () => {
    // 全局最长 400ms：100ms → 25%，400ms → 100%
    expect(bar_geometry({ total_ms: 100, ttft_ms: 20 }, 400)).toEqual({ width: 25, ttftShare: 20 });
    expect(bar_geometry({ total_ms: 400, ttft_ms: 0 }, 400)).toEqual({ width: 100, ttftShare: 0 });
    // total 为 0 时给最小可见宽度，且不做除零
    expect(bar_geometry({ total_ms: 0, ttft_ms: 0 }, 400)).toEqual({ width: 2, ttftShare: 0 });
  });

  it("渲染任务分组、汇总与终态配色", () => {
    const html = renderToStaticMarkup(
      <TaskWaterfall rows={rows} selected="t1" onSelect={() => {}} />
    );
    expect(html).toContain("task-a");
    expect(html).toContain("未标记任务");
    // 任务级汇总：2 次调用 / 合计 500.0 ms / 1 个错误
    expect(html).toContain("2 次调用");
    expect(html).toContain("500.0 ms");
    expect(html).toContain("错误 1");
    // 终态配色与选中态
    expect(html).toContain("waterfall-bar waterfall-ok");
    expect(html).toContain("waterfall-bar waterfall-bad");
    expect(html).toContain("waterfall-bar waterfall-warn");
    expect(html).toContain("row-selected");
  });
});

describe("SSE 消费", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** 把若干字节片段包装成 fetch 的响应体。 */
  function respond_with(chunks) {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    return { ok: true, body: stream };
  }

  it("按事件解析 delta，遇到 [DONE] 停止且不把哨兵交给调用方", async () => {
    // 故意把一条事件拆成两个网络分片：解析必须按 buffer 累积，
    // 否则真实网络下会偶发丢事件。
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond_with([
          'event: text_delta\ndata: {"type":"text_delta","del',
          'ta":"你好"}\n\nevent: done\ndata: {"type":"done"}\n\ndata: [DONE]\n\n',
        ])
      )
    );

    const events = [];
    await streamChat({ messages: [{ role: "user", content: "hi" }] }, { onEvent: (e) => events.push(e) });

    expect(events).toHaveLength(2);
    expect(JSON.parse(events[0].data).delta).toBe("你好");
    expect(events[1].event).toBe("done");
    // [DONE] 是流结束标记，不应作为业务事件透出。
    expect(events.some((e) => e.data === "[DONE]")).toBe(false);
  });

  it("error 终态不伴随 [DONE]，仍会作为事件交付", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        respond_with(['event: error\ndata: {"type":"error","error_message":"RATE_LIMITED: 429"}\n\n'])
      )
    );

    const events = [];
    await streamChat({ messages: [{ role: "user", content: "hi" }] }, { onEvent: (e) => events.push(e) });

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("error");
    expect(JSON.parse(events[0].data).error_message).toContain("RATE_LIMITED");
  });

  it("HTTP 错误抛出后端返回的 detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 422,
        text: async () => JSON.stringify({ detail: { code: "REQUEST_INVALID", message: "messages 不能为空" } }),
      }))
    );

    await expect(
      streamChat({ messages: [{ role: "user", content: "hi" }] }, { onEvent: () => {} })
    ).rejects.toThrow("messages 不能为空");
  });
});
