import { describe, expect, it, vi, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import App from "../App.jsx";
import ChatPage, {
  DEFAULT_TOOLS,
  MAX_TOOL_ROUNDS,
  assistant_content,
  parse_tools,
  run_local_tool,
  tool_calls_from_done,
  tool_message,
} from "../pages/ChatPage.jsx";
import SettingsPage from "../pages/SettingsPage.jsx";
import { ProfileForm, reorder } from "../pages/ProfilesPage.jsx";
import {
  TaskWaterfall,
  bar_geometry,
  classify_payload,
  group_by_task,
  group_exchanges,
} from "../pages/TracePage.jsx";
import * as api from "../api.js";
import { streamTask } from "../api.js";

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

describe("控制台冒烟", () => {
  it("渲染功能页签", () => {
    const html = renderToStaticMarkup(<App />);
    for (const label of ["模型定义", "Profile", "Chat", "Dashboard", "Trace", "设置"]) {
      expect(html).toContain(label);
    }
  });

  it("默认展示模型定义页", () => {
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("模型清单");
    expect(html).toContain("高级配置");
  });

  it("侧边栏提供版本号与更新日志入口（需求第 6 条）", () => {
    // 版本号本身来自 /api/meta（静态渲染时还没有），因此这里只验证入口存在。
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain("版本");
    expect(html).toContain("更新日志");
  });

  it("协议下拉的值是 adapter 的协议 ID", () => {
    // 选项值必须与后端 create_adapter 认识的协议名一致，否则手改一次协议
    // 就会让模型（含"测试连接"）以"未知的 API 协议"失败。
    const html = renderToStaticMarkup(<App />);
    expect(html).toContain('value="openai-completions"');
    expect(html).toContain('value="anthropic-messages"');
  });
});

describe("Chat 直连 agent 接口（需求 管理与交互层第 7 条）", () => {
  it("api 暴露 streamTask，且不再暴露旧的 streamChat", () => {
    expect(typeof api.streamTask).toBe("function");
    // 旧的 /api/chat/stream 契约已删除，api.js 不应再导出它。
    expect(api.streamChat).toBeUndefined();
  });

  it("Chat 页提供口令输入并提示口令来源", () => {
    const html = renderToStaticMarkup(<ChatPage />);
    expect(html).toContain("Agent 口令");
    expect(html).toContain("连接配置");
  });

  it("请求落到 /v1/tasks:stream，并按需带上 Bearer 与 x-trace-id", async () => {
    const fetch_mock = vi.fn(async () => respond_with([]));
    vi.stubGlobal("fetch", fetch_mock);
    await streamTask(
      { task_id: "chat-1", input: { messages: [{ role: "user", content: "hi" }] } },
      { password: "secret", traceId: "trace-1", onEvent: () => {} }
    );
    const [url, options] = fetch_mock.mock.calls[0];
    expect(url).toBe("/v1/tasks:stream");
    expect(options.headers.Authorization).toBe("Bearer secret");
    expect(options.headers["x-trace-id"]).toBe("trace-1");
    vi.unstubAllGlobals();
  });
});

describe("Chat 页编排多轮 agent（工具调用循环）", () => {
  it("页面提供工具开关、轮数上限说明与同一会话复用的 task_id", () => {
    const html = renderToStaticMarkup(<ChatPage />);
    expect(html).toContain("工具与多轮循环");
    expect(html).toContain("带上 tools（模型可发起工具调用）");
    expect(html).toContain(`最多 ${MAX_TOOL_ROUNDS} 轮`);
    expect(html).toContain("task_id");
    expect(html).toContain("新会话");
  });

  it("parse_tools 只做结构校验：合法 / 空 / 非 JSON / 非数组 / 缺 name", () => {
    expect(parse_tools(DEFAULT_TOOLS).tools).toHaveLength(2);
    expect(parse_tools(DEFAULT_TOOLS).error).toBe("");
    expect(parse_tools("").tools).toEqual([]);
    expect(parse_tools("  ").error).toBe("");
    expect(parse_tools("{bad").error).toContain("不是合法 JSON");
    expect(parse_tools('{"name":"x"}').error).toContain("必须是数组");
    expect(parse_tools('[{"description":"缺 name"}]').error).toContain("第 1 个工具缺少 name");
  });

  it("run_local_tool 执行本地假工具，未知工具给出可读说明", () => {
    expect(run_local_tool("echo", { text: "你好" })).toBe("你好");
    expect(run_local_tool("echo")).toBe("");
    expect(run_local_tool("get_time")).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    expect(run_local_tool("shell")).toContain("未知工具 shell");
  });

  it("assistant_content 拼出文本 + tool_call 块，空文本时不带空 text 块", () => {
    const calls = [{ id: "call_1", name: "echo", arguments: { text: "hi" } }];
    expect(assistant_content("想一下", calls)).toEqual([
      { type: "text", text: "想一下" },
      { type: "tool_call", id: "call_1", name: "echo", arguments: { text: "hi" } },
    ]);
    expect(assistant_content("", calls)).toHaveLength(1);
    expect(assistant_content("", calls)[0].type).toBe("tool_call");
    // id 缺失也要给空串：schema 允许，且 tool_result 必须能据此对上。
    expect(assistant_content("", [{ name: "echo" }])[0]).toMatchObject({ id: "", arguments: {} });
  });

  it("tool_message 的 tool_call_id 与发起调用一致（否则会被当成孤儿结果丢掉）", () => {
    const call = { id: "call_7", name: "echo", arguments: {} };
    expect(tool_message(call, "结果")).toEqual({
      role: "tool",
      content: [{ type: "tool_result", tool_call_id: "call_7", content: "结果" }],
    });
    expect(tool_message({ name: "echo" }, "结果").content[0].tool_call_id).toBe("");
  });

  it("done 帧里的工具调用能被识读（真实形状：message.content[] 的 toolCall 块）", () => {
    // 实测原文：工具调用在 message.content[] 里、块类型是驼峰 toolCall，
    // done 帧顶层没有 tool_calls 字段（网关用 asdict 展开 AssistantMessage）。
    const frame = {
      type: "done",
      reason: "tool_use",
      message: {
        role: "assistant",
        content: [
          { type: "text", text: "我看看现在几点" },
          { type: "toolCall", id: "call_abc", name: "get_time", arguments: {} },
        ],
        stop_reason: "tool_use",
        raw_stop_reason: "tool_calls",
      },
    };
    expect(tool_calls_from_done(frame)).toEqual([
      { id: "call_abc", name: "get_time", arguments: {} },
    ]);
    // 纯文本回复（stop_reason=stop）不该被误认为有工具调用，否则会空转一轮。
    expect(
      tool_calls_from_done({ type: "done", message: { content: [{ type: "text", text: "你好" }] } })
    ).toEqual([]);
    // 缺 message / content 也不能崩。
    expect(tool_calls_from_done({ type: "done" })).toEqual([]);
  });
});

describe("设置页（需求 Harness 层第 1 条）", () => {
  it("展示口令来源与只写不回显 / 环境变量优先的说明", () => {
    const html = renderToStaticMarkup(<SettingsPage />);
    expect(html).toContain("Agent 接口口令");
    expect(html).toContain("环境变量优先于网页配置");
    expect(html).toContain("只写不回显");
  });
});

describe("路由表拖拉拽编辑（需求 路由模块第 6 条）", () => {
  it("静态模式下展示编辑器、路由表名称与自上而下执行说明", () => {
    const html = renderToStaticMarkup(
      <ProfileForm
        models={[]}
        initial={{ name: "rt", route_mode: "static", static_order: ["openai/gpt-4o"] }}
        submitLabel="保存"
        onSubmit={() => {}}
      />
    );
    expect(html).toContain("路由表：rt");
    expect(html).toContain("自上而下执行");
    expect(html).toContain("全部可选模型");
    expect(html).toContain("openai/gpt-4o");
  });

  it("动态模式下不出现顺序编辑器", () => {
    const html = renderToStaticMarkup(
      <ProfileForm
        models={[]}
        initial={{ name: "rt", route_mode: "dynamic" }}
        submitLabel="保存"
        onSubmit={() => {}}
      />
    );
    expect(html).not.toContain("全部可选模型");
    expect(html).toContain("动态路由由网关");
  });

  it("reorder 把第 from 项移动到第 to 位，同位原样返回", () => {
    expect(reorder(["a", "b", "c"], 0, 2)).toEqual(["b", "c", "a"]);
    expect(reorder(["a", "b", "c"], 2, 0)).toEqual(["c", "a", "b"]);
    expect(reorder(["a", "b"], 1, 1)).toEqual(["a", "b"]);
  });
});

describe("通讯原始日志（需求 Harness 层第 3 条）", () => {
  it("按 task_id 两级分组，组内按 flow_index 升序并记录最后一次时间", () => {
    const list = [
      { task_id: "t1", flow_index: 2, ts: 20, status: "done", exchange_id: "e2" },
      { task_id: "t1", flow_index: 1, ts: 10, status: "error", exchange_id: "e1" },
      { task_id: "t2", flow_index: 1, ts: 5, status: "done", exchange_id: "e3" },
    ];
    const groups = group_exchanges(list);
    expect(groups.map((g) => g.task_id)).toEqual(["t1", "t2"]);
    expect(groups[0].items.map((i) => i.exchange_id)).toEqual(["e1", "e2"]);
    expect(groups[0].last_ts).toBe(20);
  });

  it("classify_payload 区分 JSON / SSE / 纯文本", () => {
    expect(classify_payload('{"a":1}').kind).toBe("json");
    const sse = classify_payload("event: done\ndata: [DONE]");
    expect(sse.kind).toBe("sse");
    expect(sse.frames[0]).toEqual({ event: "done", data: "[DONE]" });
    expect(classify_payload("").kind).toBe("empty");
    expect(classify_payload("just text").kind).toBe("text");
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
    await streamTask(
      { task_id: "t1", input: { messages: [{ role: "user", content: "hi" }] } },
      { onEvent: (e) => events.push(e) }
    );

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
    await streamTask(
      { task_id: "t1", input: { messages: [{ role: "user", content: "hi" }] } },
      { onEvent: (e) => events.push(e) }
    );

    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("error");
    expect(JSON.parse(events[0].data).error_message).toContain("RATE_LIMITED");
  });

  it("HTTP 错误抛出后端返回的 detail", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 401,
        text: async () =>
          JSON.stringify({ detail: { code: "AUTH_REQUIRED", message: "agent 接口口令无效" } }),
      }))
    );

    await expect(
      streamTask(
        { task_id: "t1", input: { messages: [{ role: "user", content: "hi" }] } },
        { onEvent: () => {} }
      )
    ).rejects.toThrow("agent 接口口令无效");
  });
});