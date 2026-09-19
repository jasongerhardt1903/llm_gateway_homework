import { describe, expect, it, vi, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import App from "../App.jsx";
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
