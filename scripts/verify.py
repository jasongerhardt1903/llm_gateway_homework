#!/usr/bin/env python3
"""独立端到端验证脚本：一次跑通验收清单的六大功能点。

用法::

    .venv/bin/python scripts/verify.py

脚本自己起一个**本地假上游**（同一进程里同时提供 ``openai-completions`` 与
``anthropic-messages`` 两套协议），再起真正的网关进程指向它，然后用真实 HTTP
请求逐项验证：

1. 统一抽象层 —— 两个协议各自的鉴权头与请求体结构由 adapter 翻译，调用方无感；
2. 流式输出   —— ``/v1/tasks:stream`` 的 SSE 逐块返回与单一终态；
3. 结构化输出 —— ``response_format`` 约束出合法 JSON；
4. 提示词版本管理 —— 模板存储 / ``{{变量}}`` 替换 / 版本引用；
5. 可观测性   —— Token 分类统计与首 Token 延迟；
6. 韧性基础   —— 指数退避重试、候选链降级（坏模型一路换到能用的那个）、按模型独立限流 429。

降级这一项还会顺带验证 Trace 的可读性：候选链上的**每次尝试各落一条记录**（同一条
链路共享 ``trace_id``，用 ``attempt_index`` 标位次、``degraded_from`` 标降级来源），
并带上路由决策快照。只在最后落一条的话，失败与重试在 Trace 里会整个消失。

为什么用假上游而不是真实供应商：验收必须**可复现、且不需要任何密钥**。假上游按
真实协议回包（含逐块流式分片与 usage 分两处上报），足以证明网关的翻译、编排与
计量都是真的在跑，而不是被 mock 掉了。要打真实供应商时把控制台里的 ``base_url``
换成官方地址即可，验证脚本与它会走完全相同的代码路径。

限流那一项要求环境变量在进程启动时就固定下来（``policy_from_env`` 每次读进程环境），
因此脚本会起**第二个**网关实例（``RPM=3``）单独验它，第一个实例保持不限额。

版本：0.8.8
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent

#: 假上游收到首个流式分片前的等待，用来让"首 Token 延迟"这个指标是**可观测**的，
#: 而不是恒等于 0。下面断言时会用它做下界。
FIRST_CHUNK_DELAY_S = 0.12
INTER_CHUNK_DELAY_S = 0.02

#: 假上游回包用的用量，断言时逐项对上。
INPUT_TOKENS = 1234
OUTPUT_TOKENS = 56

OPENAI_LABEL = "openai/gpt-4o-mini"
ANTHROPIC_LABEL = "anthropic/claude-3-5-haiku-20241022"
OPENAI_KEY = "sk-verify-openai"
ANTHROPIC_KEY = "sk-verify-anthropic"
OPENAI_PROFILE = "verify-oa"
ANTHROPIC_PROFILE = "verify-an"

#: 降级那一项用的静态路由表：4 个模型、跨两套协议。
#: 前三个走 OpenAI 协议（``/chat/completions``）、最后一个走 Anthropic 协议
#: （``/messages``）——把前者的路打坏，就必须一路降级到最后一个才算罢休。
DEGRADE_PROFILE = "verify-degrade"
DEGRADE_CHAIN = (
    "openai/gpt-4o-mini",
    "openai/gpt-4o",
    "openai/gpt-4.1-mini",
    ANTHROPIC_LABEL,
)


# --------------------------------------------------------------------------
# 假上游
# --------------------------------------------------------------------------


class _UpstreamState:
    """假上游的可变状态：回什么、错几次、收到过什么。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.text = "pong"
        self.fail_next = 0
        #: 让**指定路径**恒失败（按后缀匹配）：某条协议的路一直错、另一条正常，
        #: 才能逼出"换模型"的降级，而不是"同一个模型重试"。
        self.fail_paths: list[str] = []
        #: 恒失败时回什么状态码。401 落到 ``AUTH_INVALID``（处置 = 降级，不重试），
        #: 500 落到 ``UPSTREAM_OVERLOADED``（处置 = 先重试、耗尽后降级）。
        self.fail_status = 500
        self.requests: list[dict] = []


def _pieces(text: str) -> list[str]:
    """把回复切成若干分片，用来证明流式是真逐块而不是一次性回包。"""
    if len(text) <= 4:
        return [text]
    size = max(2, len(text) // 3)
    return [text[i : i + size] for i in range(0, len(text), size)]


def _handler_factory(state: _UpstreamState):
    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0：响应以连接关闭界定，不涉及 chunked 编码，SSE 写法最简单。
        protocol_version = "HTTP/1.0"

        def log_message(self, *_args) -> None:  # 静音访问日志
            pass

        # -- 响应工具 ------------------------------------------------------

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _sse_open(self) -> None:
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("cache-control", "no-cache")
            self.end_headers()

        def _sse_write(self, payload: dict, *, event: str | None = None) -> None:
            frame = ""
            if event:
                frame += f"event: {event}\n"
            frame += f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()

        def _sse_write_raw(self, raw: str) -> None:
            self.wfile.write(raw.encode("utf-8"))
            self.wfile.flush()

        # -- 路由 ----------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            if self.path.endswith("/__requests"):
                with state.lock:
                    self._json(200, {"requests": list(state.requests)})
                return
            self._json(404, {"error": {"message": f"no such route: {self.path}"}})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                body = {}

            if self.path.endswith("/__control"):
                with state.lock:
                    state.text = body.get("text", state.text)
                    state.fail_next = int(body.get("fail_next", state.fail_next))
                    if "fail_paths" in body:
                        state.fail_paths = list(body.get("fail_paths") or [])
                    if "fail_status" in body:
                        state.fail_status = int(body.get("fail_status") or 500)
                self._json(200, {"ok": True})
                return

            with state.lock:
                state.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                    }
                )
                injected = state.fail_next > 0
                if injected:
                    state.fail_next -= 1
                # 按路径恒失败：与"错几次"并列判定，用来构造"这条路一直坏"的场景。
                by_path = any(self.path.endswith(suffix) for suffix in state.fail_paths)
                text, fail, status = state.text, injected or by_path, state.fail_status

            if fail:
                # 默认 5xx 落到 ``UPSTREAM_OVERLOADED``，处置为 RETRY——正是重试那一项
                # 要压的路径。降级那一项会把它改成 401（``AUTH_INVALID``，处置为
                # DEGRADE），好让"换模型"这件事单独成立、不被重试掺进来。
                self._json(status, {"error": {"message": "verify: injected upstream failure"}})
                return

            if self.path.endswith("/chat/completions"):
                self._openai(body, text)
            elif self.path.endswith("/messages"):
                self._anthropic(body, text)
            else:
                self._json(404, {"error": {"message": f"no such route: {self.path}"}})

        # -- 两套协议的响应 ------------------------------------------------

        def _usage(self) -> dict:
            return {
                "prompt_tokens": INPUT_TOKENS,
                "completion_tokens": OUTPUT_TOKENS,
                "total_tokens": INPUT_TOKENS + OUTPUT_TOKENS,
            }

        def _openai(self, body: dict, text: str) -> None:
            model = body.get("model") or "verify-openai"
            if not body.get("stream"):
                self._json(
                    200,
                    {
                        "id": "chatcmpl-verify",
                        "object": "chat.completion",
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": text},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": self._usage(),
                    },
                )
                return

            self._sse_open()
            for index, piece in enumerate(_pieces(text)):
                time.sleep(FIRST_CHUNK_DELAY_S if index == 0 else INTER_CHUNK_DELAY_S)
                self._sse_write(
                    {
                        "id": "chatcmpl-verify",
                        "object": "chat.completion.chunk",
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": piece},
                                "finish_reason": None,
                            }
                        ],
                    }
                )
            self._sse_write(
                {
                    "id": "chatcmpl-verify",
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                }
            )
            # usage 只在最后一个 chunk 里给（``stream_options.include_usage``）。
            self._sse_write(
                {
                    "id": "chatcmpl-verify",
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [],
                    "usage": self._usage(),
                }
            )
            self._sse_write_raw("data: [DONE]\n\n")

        def _anthropic(self, body: dict, text: str) -> None:
            model = body.get("model") or "verify-anthropic"
            if not body.get("stream"):
                self._json(
                    200,
                    {
                        "id": "msg_verify",
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn",
                        "usage": {"input_tokens": INPUT_TOKENS, "output_tokens": OUTPUT_TOKENS},
                    },
                )
                return

            self._sse_open()
            self._sse_write(
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_verify",
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        # Anthropic 把 input 用量放在 message_start。
                        "usage": {"input_tokens": INPUT_TOKENS, "output_tokens": 1},
                    },
                },
                event="message_start",
            )
            self._sse_write(
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                event="content_block_start",
            )
            for index, piece in enumerate(_pieces(text)):
                time.sleep(FIRST_CHUNK_DELAY_S if index == 0 else INTER_CHUNK_DELAY_S)
                self._sse_write(
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": piece},
                    },
                    event="content_block_delta",
                )
            self._sse_write({"type": "content_block_stop", "index": 0}, event="content_block_stop")
            # output 用量放在 message_delta——两处拼起来才是完整用量。
            self._sse_write(
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn"},
                    "usage": {"output_tokens": OUTPUT_TOKENS},
                },
                event="message_delta",
            )
            self._sse_write({"type": "message_stop"}, event="message_stop")

    return Handler


class FakeUpstream:
    """本地假上游：真 HTTP 服务，真协议回包。"""

    def __init__(self) -> None:
        self.state = _UpstreamState()
        self.port = _free_port()
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), _handler_factory(self.state))
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def __enter__(self) -> "FakeUpstream":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    # -- 控制与观测 --------------------------------------------------------

    def reply(self, text: str, *, fail_next: int = 0) -> None:
        httpx.post(
            f"http://127.0.0.1:{self.port}/__control",
            json={"text": text, "fail_next": fail_next},
            timeout=5.0,
        )

    def fail_paths(self, *suffixes: str, status: int = 401) -> None:
        """让指定后缀的上游路径**恒**失败；不传后缀即恢复全部正常。

        与 :meth:`reply` 的 ``fail_next``（错几次就恢复）不同，这里要的是"这条路
        一直坏"，用来验证网关会不会换下一个模型，直到有一个能成功。
        """
        httpx.post(
            f"http://127.0.0.1:{self.port}/__control",
            json={"fail_paths": list(suffixes), "fail_status": status},
            timeout=5.0,
        )

    def requests(self) -> list[dict]:
        got = httpx.get(f"http://127.0.0.1:{self.port}/__requests", timeout=5.0)
        return got.json()["requests"]

    def last(self, suffix: str) -> dict | None:
        for item in reversed(self.requests()):
            if item["path"].endswith(suffix):
                return item
        return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------------------
# 网关进程
# --------------------------------------------------------------------------


class Gateway:
    """把真实网关起成一个子进程；同一个库可以在两个配置下各起一次。"""

    def __init__(self, *, db_path: Path, rate_limit_rpm: int = 0) -> None:
        self.port = _free_port()
        self.db_path = db_path
        self.rate_limit_rpm = rate_limit_rpm
        self._process: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _env(self) -> dict[str, str]:
        # 从干净环境起步：宿主机导出的 LLM_GW_* 会改变行为（agent 口令、库路径、
        # 限流），带上它们就不是"可复现"的验证了。
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLM_GW_")}
        env["LLM_GW_DB"] = str(self.db_path)
        env["LLM_GW_RATE_LIMIT_RPM"] = str(self.rate_limit_rpm)
        env["LLM_GW_RATE_LIMIT_BURST"] = str(max(1, self.rate_limit_rpm))
        return env

    def __enter__(self) -> "Gateway":
        python = ROOT / ".venv" / "bin" / "python"
        interpreter = str(python) if python.exists() else sys.executable
        self._log = tempfile.NamedTemporaryFile(prefix="verify-gw-", suffix=".log", delete=False)
        self._process = subprocess.Popen(
            [
                interpreter,
                "-m",
                "uvicorn",
                "llm_gw.runtime:create_runtime_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
            ],
            cwd=str(ROOT),
            env=self._env(),
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )
        self._wait_ready()
        return self

    def __exit__(self, *_exc) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._log.close()

    def _wait_ready(self) -> None:
        deadline = time.time() + 30
        while time.time() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise RuntimeError(f"网关启动失败，日志：\n{self._read_log()}")
            try:
                if httpx.get(f"{self.base_url}/health", timeout=1.0).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.2)
        raise RuntimeError(f"网关 30 秒内未就绪，日志：\n{self._read_log()}")

    def _read_log(self) -> str:
        return Path(self._log.name).read_text(encoding="utf-8", errors="replace")[-4000:]

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url, timeout=30.0)


# --------------------------------------------------------------------------
# 验证结果收集
# --------------------------------------------------------------------------


class Report:
    """逐项打印 PASS/FAIL，最后给总账；有 FAIL 就以非零码退出。"""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.count = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.count += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   — {detail}" if detail else ""))
        if not ok:
            self.failures.append(name)
        return ok

    def section(self, title: str) -> None:
        print(f"\n{title}")

    def finish(self) -> int:
        print("\n" + "-" * 72)
        if self.failures:
            print(f"结果：{self.count - len(self.failures)}/{self.count} 通过，失败项：")
            for name in self.failures:
                print(f"  - {name}")
            return 1
        print(f"结果：全部 {self.count} 项通过。")
        return 0


# --------------------------------------------------------------------------
# 请求工具
# --------------------------------------------------------------------------


def collect_sse(response: httpx.Response) -> tuple[list[tuple[str, dict]], bool]:
    """把 SSE 响应解析成 ``[(事件名, data)]`` 与是否收到 ``[DONE]``。"""
    events: list[tuple[str, dict]] = []
    done_marker = False
    event_name: str | None = None
    for line in response.iter_lines():
        if not line:
            event_name = None
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            if value == "[DONE]":
                done_marker = True
            else:
                events.append((event_name or "", json.loads(value)))
    return events, done_marker


def streamed_text(events: list[tuple[str, dict]]) -> str:
    return "".join(
        str(payload.get("delta", "")) for name, payload in events if name == "text_delta"
    )


def trace_call(client: httpx.Client, trace_id: str) -> dict | None:
    got = client.get(f"/api/traces/{trace_id}")
    if got.status_code != 200:
        return None
    return got.json()["calls"][0]


def task_body(
    *,
    task_id: str,
    content: str = "ping",
    profile: str | None = None,
    model: str | None = None,
    prompt: dict | None = None,
    **input_extra,
) -> dict:
    """按 agent schema 组一个 task。

    ``profile`` / ``model`` / ``prompt`` 都是 **task 顶层字段**，其余进 ``input``——
    ``prompt`` 塞进 ``input`` 会被 ``extra="forbid"`` 挡成 422，这个坑踩过一次。
    """
    body: dict = {
        "task_id": task_id,
        "input": {"messages": [{"role": "user", "content": content}], **input_extra},
    }
    if profile is not None:
        body["profile"] = profile
    if model is not None:
        body["model"] = model
    if prompt is not None:
        body["prompt"] = prompt
    return body


# --------------------------------------------------------------------------
# 配置：把两个模型与两个 profile 登记上去
# --------------------------------------------------------------------------


JSON_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "score": {"type": "number"}},
    "required": ["city", "score"],
    "additionalProperties": False,
}


def configure(client: httpx.Client, upstream: FakeUpstream) -> None:
    """登记两个协议各一个模型，各自代码起一个 profile。

    ``base_url`` 指向假上游；``api_key`` 是任意值，用来断言 adapter 把它翻成了
    各自协议的鉴权头（OpenAI 的 ``Authorization: Bearer``、Anthropic 的 ``x-api-key``）。
    """
    for payload in (
        {
            "id": "gpt-4o-mini",
            "name": "OpenAI gpt-4o-mini",
            "provider": "openai",
            "api": "openai-completions",
            "base_url": upstream.base_url,
            "api_key": OPENAI_KEY,
            "context_window": 128000,
            "max_tokens": 4096,
            "cost": {"input": 0.15, "output": 0.6, "cache_read": 0.0, "cache_write": 0.0},
            "capabilities": {"sse": True, "streaming": True, "json_schema": True},
        },
        {
            "id": "claude-3-5-haiku-20241022",
            "name": "Anthropic Claude Haiku",
            "provider": "anthropic",
            "api": "anthropic-messages",
            "base_url": upstream.base_url,
            "api_key": ANTHROPIC_KEY,
            "context_window": 200000,
            "max_tokens": 4096,
            "cost": {"input": 0.8, "output": 4.0, "cache_read": 0.0, "cache_write": 0.0},
            "capabilities": {"sse": True, "streaming": True, "json_schema": False},
        },
    ):
        got = client.post("/api/models", json=payload)
        got.raise_for_status()

    for name, label in ((OPENAI_PROFILE, OPENAI_LABEL), (ANTHROPIC_PROFILE, ANTHROPIC_LABEL)):
        got = client.post(
            "/api/profiles",
            json={"name": name, "models": [{"label": label}], "retry_enabled": True, "max_retries": 3},
        )
        got.raise_for_status()


# --------------------------------------------------------------------------
# 六个功能点
# --------------------------------------------------------------------------


def check_translation(report: Report, client: httpx.Client, upstream: FakeUpstream) -> None:
    """1. 统一抽象层：同一份 task，两套协议各自的鉴权头与请求体结构。"""
    report.section("1. 统一抽象层（协议翻译）")

    # 同一份 task（只有 profile 不同）分别打两次，再看上游收到的是什么。
    upstream.reply("ok")
    for profile in (OPENAI_PROFILE, ANTHROPIC_PROFILE):
        got = client.post(
            "/v1/tasks",
            json=task_body(profile=profile, task_id=f"verify-xlate-{profile}",
                           system="你是一个简洁的助手。", stream=False),
            headers={"x-trace-id": f"verify-xlate-{profile}"},
        )
        report.check(f"[{profile}] 同一份 task 换 profile 即可调用", got.status_code == 200)

    openai_req = upstream.last("/chat/completions")
    anthropic_req = upstream.last("/messages")

    report.check(
        "OpenAI 协议：密钥翻成 Authorization: Bearer",
        bool(openai_req) and openai_req["headers"].get("authorization") == f"Bearer {OPENAI_KEY}",
        f"authorization={openai_req['headers'].get('authorization') if openai_req else None}",
    )
    report.check(
        "Anthropic 协议：密钥翻成 x-api-key（同一次调用，调用方无感）",
        bool(anthropic_req) and anthropic_req["headers"].get("x-api-key") == ANTHROPIC_KEY,
        f"x-api-key={anthropic_req['headers'].get('x-api-key') if anthropic_req else None}",
    )
    report.check(
        "Anthropic 协议：带上 anthropic-version 头",
        bool(anthropic_req) and "anthropic-version" in anthropic_req["headers"],
    )
    report.check(
        "请求体结构：OpenAI 把 system 放进 messages[0]",
        bool(openai_req)
        and openai_req["body"]["messages"][0]["role"] == "system"
        and "system" not in openai_req["body"],
    )
    report.check(
        "请求体结构：Anthropic 把同一个 system 提到顶层字段",
        bool(anthropic_req)
        and isinstance(anthropic_req["body"].get("system"), str)
        and all(m["role"] != "system" for m in anthropic_req["body"]["messages"]),
    )
    report.check(
        "Anthropic 必填字段 max_tokens 被补齐",
        bool(anthropic_req) and isinstance(anthropic_req["body"].get("max_tokens"), int),
    )


def check_model_routing(report: Report, client: httpx.Client, upstream: FakeUpstream) -> None:
    """1b. 按 ``model`` 字段路由（需求第 27 行："根据请求中的 model 字段动态路由到对应适配器"）。"""
    report.section("1b. 按 model 字段路由（点名即换适配器）")
    upstream.reply("ok")

    # 不带 profile、只用 model 点名：两套协议各来一次，看落到哪个 adapter。
    for label, api, suffix in (
        (OPENAI_LABEL, "openai-completions", "/chat/completions"),
        (ANTHROPIC_LABEL, "anthropic-messages", "/messages"),
    ):
        trace_id = f"verify-pin-{api}"
        got = client.post(
            "/v1/tasks",
            json=task_body(task_id=trace_id, model=label, stream=False),
            headers={"x-trace-id": trace_id},
        )
        body = got.json()
        record = trace_call(client, trace_id) or {}
        report.check(
            f"model={label} 被路由到 {api}",
            got.status_code == 200
            and body.get("terminal") == "done"
            and record.get("api") == api
            and record.get("model") == label.split("/", 1)[1],
            f"terminal={body.get('terminal')} api={record.get('api')} model={record.get('model')}",
        )
        report.check(f"model={label} 的上游请求真的打到了 {suffix}",
                     bool(upstream.last(suffix)))

    # 裸 id 也认（唯一匹配时），URL 里写 model=id 很自然。
    got = client.post(
        "/v1/tasks",
        json=task_body(task_id="verify-pin-bare", model="gpt-4o-mini", stream=False),
        headers={"x-trace-id": "verify-pin-bare"},
    )
    report.check("裸 id（唯一匹配）也能点名", got.json().get("terminal") == "done")

    # 点名越界 / 点名不存在的模型：都必须如实报错，而不是悄悄换一个模型顶上。
    for label, why in (
        ("openai/ghost", "不存在的模型"),
        ("anthropic/claude-3-5-haiku-20241022", "profile 池子外的模型（verify-oa 里只有 openai）"),
    ):
        trace_id = f"verify-pin-bad-{label.replace('/', '-')}"
        got = client.post(
            "/v1/tasks",
            json=task_body(task_id=trace_id, profile=OPENAI_PROFILE, model=label, stream=False),
            headers={"x-trace-id": trace_id},
        )
        body = got.json()
        report.check(
            f"点名{why} → 如实 ROUTE_NO_CANDIDATE",
            body.get("terminal") == "error" and "ROUTE_NO_CANDIDATE" in (body.get("error_message") or ""),
            (body.get("error_message") or "")[:80],
        )


def check_streaming(report: Report, client: httpx.Client, upstream: FakeUpstream) -> None:
    """2. 流式输出：SSE 逐块返回 + 单一终态。"""
    report.section("2. 流式输出（stream=true，SSE 逐块）")
    upstream.reply("你好，这是流式的分片输出。")

    for profile in (OPENAI_PROFILE, ANTHROPIC_PROFILE):
        trace_id = f"verify-stream-{profile}"
        with client.stream(
            "POST",
            "/v1/tasks:stream",
            json=task_body(
                profile=profile, task_id=trace_id, system="你是一个简洁的助手。", stream=True
            ),
            headers={"x-trace-id": trace_id},
        ) as response:
            report.check(f"[{profile}] HTTP 200 且 content-type 是 text/event-stream",
                         response.status_code == 200
                         and "text/event-stream" in response.headers.get("content-type", ""))
            events, done_marker = collect_sse(response)

        names = [name for name, _ in events]
        deltas = [name for name in names if name == "text_delta"]
        report.check(f"[{profile}] 收到多个 text_delta（真逐块，不是一次性回包）",
                     len(deltas) >= 2, f"{len(deltas)} 个 delta")
        report.check(f"[{profile}] 拼出的正文与上游一致",
                     streamed_text(events) == "你好，这是流式的分片输出。",
                     repr(streamed_text(events)))
        report.check(f"[{profile}] 终态是 done", names and names[-1] == "done")
        report.check(f"[{profile}] done 之后有 data: [DONE]", done_marker)
        report.check(f"[{profile}] 只出现一个终态事件",
                     sum(name in ("done", "error", "cancelled") for name in names) == 1)


def check_structured(report: Report, client: httpx.Client, upstream: FakeUpstream) -> None:
    """3. 结构化输出：response_format 约束出合法 JSON。"""
    report.section("3. 结构化输出（response_format）")

    # 3a) 别名：验收口径按 OpenAI 的字段名发请求，两种形态都必须被收下。
    for fmt in ({"type": "json_object"}, {"type": "json_schema", "json_schema": {"name": "s", "schema": JSON_SCHEMA}}):
        got = client.post(
            "/api/tasks:validate",
            json={"task_id": "verify-fmt", "input": {"messages": [{"role": "user", "content": "x"}], "response_format": fmt}},
        )
        report.check(f"response_format 别名 {fmt['type']} 被接受", got.status_code == 200, f"HTTP {got.status_code}")

    bad = client.post(
        "/api/tasks:validate",
        json={"task_id": "verify-fmt", "input": {"messages": [{"role": "user", "content": "x"}], "response_format": {"type": "xml"}}},
    )
    report.check("非法 response_format.type 被 422 挡下并给出可读原因",
                 bad.status_code == 422 and "response_format.type" in json.dumps(bad.json(), ensure_ascii=False))

    # 3b) 真正约束出 JSON：「只要求合法 JSON」这一档，两个协议都必须做到。
    upstream.reply('{"city": "上海", "score": 0.93}')
    expected = {"city": "上海", "score": 0.93}
    for profile in (OPENAI_PROFILE, ANTHROPIC_PROFILE):
        trace_id = f"verify-json-{profile}"
        got = client.post(
            "/v1/tasks",
            json=task_body(
                profile=profile,
                task_id=trace_id,
                content="上海天气如何？",
                response_format={"type": "json_object"},
                stream=False,
            ),
            headers={"x-trace-id": trace_id},
        )
        body = got.json()
        try:
            parsed = json.loads(body.get("text") or "")
        except json.JSONDecodeError:
            parsed = None
        report.check(f"[{profile}] json_object：正文是合法 JSON", parsed is not None, repr(body.get("text"))[:80])
        report.check(f"[{profile}] json_object：JSON 内容正确", parsed == expected, repr(parsed))
        # 网关自己给出的判定（响应体里的那个值与落库的是同一个来源）：
        # 调用方不必再自己解析一遍就能看到"我要的 JSON 到底合不合格"。
        record = trace_call(client, trace_id) or {}
        report.check(f"[{profile}] 兑现与否被判定并落库：output_valid=True",
                     body.get("output_valid") is True and record.get("output_valid") is True,
                     f"响应={body.get('output_valid')} 记录={record.get('output_valid')}")

    # 3c) 同一份意图，两套协议的实现手法不同——这正是抽象层要屏蔽的差异。
    openai_req = upstream.last("/chat/completions")
    report.check(
        "OpenAI 协议：约束走请求体里的 response_format",
        bool(openai_req) and openai_req["body"].get("response_format") == {"type": "json_object"},
    )
    anthropic_req = upstream.last("/messages")
    report.check(
        "Anthropic 协议：没有 response_format 字段，约束改写进 system",
        bool(anthropic_req)
        and "response_format" not in anthropic_req["body"]
        and "JSON" in (anthropic_req["body"].get("system") or ""),
        repr((anthropic_req["body"].get("system") or "")[:60]) if anthropic_req else None,
    )

    # 3d) 带 schema 的严格形态（OpenAI 原生 response_format）。
    upstream.reply('{"city": "北京", "score": 0.71}')
    trace_id = "verify-json-schema"
    got = client.post(
        "/v1/tasks",
        json=task_body(
            profile=OPENAI_PROFILE,
            task_id=trace_id,
            content="北京天气如何？",
            response_format={"type": "json_schema", "json_schema": {"name": "weather", "schema": JSON_SCHEMA}},
            stream=False,
        ),
        headers={"x-trace-id": trace_id},
    )
    body = got.json()
    try:
        parsed = json.loads(body.get("text") or "")
    except json.JSONDecodeError:
        parsed = None
    report.check("json_schema：返回正文是合法 JSON 且与 schema 对齐",
                 parsed == {"city": "北京", "score": 0.71}, repr(parsed))
    sent = upstream.last("/chat/completions")["body"]["response_format"]
    report.check("json_schema：schema 被翻译成上游认的形态",
                 sent.get("type") == "json_schema" and "schema" in (sent.get("json_schema") or {}),
                 json.dumps(sent, ensure_ascii=False)[:100])
    report.check("json_schema：结构对得上 schema 才算兑现（output_valid=True）",
                 body.get("output_valid") is True, str(body.get("output_valid")))

    # 3e) 能力表如实声明：Anthropic 一条没有原生 schema 支持，路由就**不**claim 它支持。
    got = client.post(
        "/v1/tasks",
        json=task_body(profile=ANTHROPIC_PROFILE, task_id="verify-json-gate",
                       response_schema=JSON_SCHEMA, stream=False),
        headers={"x-trace-id": "verify-json-gate"},
    )
    body = got.json()
    report.check("能力表如实的模型不会被硬塞 schema 请求（如实报 ROUTE_NO_CANDIDATE）",
                 body.get("terminal") == "error" and "ROUTE_NO_CANDIDATE" in (body.get("error_message") or ""),
                 (body.get("error_message") or "")[:70])
    upstream.reply("ok")

    # 3f) 上游没照办：明确要求了 JSON，它却回了一段散文。调用本身是成功的（HTTP 200、
    #     terminal=done），但"约束没兑现"必须被单独判定出来——否则这个维度等于没接线。
    trace_id = "verify-json-broken"
    got = client.post(
        "/v1/tasks",
        json=task_body(profile=OPENAI_PROFILE, task_id=trace_id, content="用 json 回答",
                       response_format={"type": "json_object"}, stream=False),
        headers={"x-trace-id": trace_id},
    )
    body = got.json()
    record = trace_call(client, trace_id) or {}
    report.check("上游没回 JSON 时：调用成功但如实标记 output_valid=False",
                 body.get("terminal") == "done"
                 and body.get("output_valid") is False
                 and record.get("output_valid") is False,
                 f"terminal={body.get('terminal')} 响应={body.get('output_valid')} "
                 f"记录={record.get('output_valid')} text={body.get('text')!r}")

    # 3g) 没要求结构化输出 → 记 null。把"没要求"记成 False 会让这一列同时混进两类事实。
    trace_id = "verify-json-none"
    got = client.post(
        "/v1/tasks",
        json=task_body(profile=OPENAI_PROFILE, task_id=trace_id, stream=False),
        headers={"x-trace-id": trace_id},
    )
    body = got.json()
    record = trace_call(client, trace_id) or {}
    report.check("未请求结构化输出时 output_valid 为 null（不把『没要求』记成『不合格』）",
                 body.get("output_valid") is None and record.get("output_valid") is None,
                 f"响应={body.get('output_valid')} 记录={record.get('output_valid')}")


def check_prompt_template(report: Report, client: httpx.Client, upstream: FakeUpstream) -> None:
    """4. 提示词版本管理：存储 / 变量替换 / 版本引用。"""
    report.section("4. 提示词版本管理（模板存储 / 变量替换 / 版本引用）")

    v1 = "你是{{persona}}。请用不超过 {{limit}} 字总结：\n{{article}}"
    v2 = "你是{{persona}}。请用不超过 {{limit}} 字总结以下文章，只输出 JSON：\n{{article}}"
    for version, body in (("v1", v1), ("v2", v2)):
        got = client.post("/api/prompts", json={"name": "summarize", "version": version, "body": body})
        report.check(f"模板 {version} 存储成功", got.status_code == 200, f"HTTP {got.status_code}")

    stored = client.get("/api/prompts/summarize").json()
    report.check("两个版本都在库里，新的在前",
                 [item["version"] for item in stored["versions"]] == ["v2", "v1"],
                 str([item["version"] for item in stored["versions"]]))
    report.check("变量清单由正文推导（不采信调用方提交）",
                 sorted(stored["versions"][0]["variables"]) == ["article", "limit", "persona"],
                 str(stored["versions"][0]["variables"]))

    report.check("不存在的模板 → 422 PROMPT_INVALID",
                 client.post("/v1/tasks", json={"task_id": "verify-p1", "prompt": {"name": "nope"},
                                                "input": {"messages": [{"role": "user", "content": "x"}]}}).status_code == 422)
    missing_var = client.post(
        "/v1/tasks",
        json={"task_id": "verify-p2", "prompt": {"name": "summarize", "version": "v1"},
              "input": {"messages": [{"role": "user", "content": "x"}]}},
    )
    report.check("漏给变量 → 422 PROMPT_INVALID",
                 missing_var.status_code == 422
                 and missing_var.json()["detail"]["code"] == "PROMPT_INVALID",
                 missing_var.json()["detail"]["message"])

    # 4d) 版本引用 + 变量替换：缺省 version 应落到最新一版，且记录里记的是**实际用过**的那一版。
    upstream.reply("ok")
    variables = {"persona": "编辑", "limit": "30", "article": "量子计算正在走向工程化。"}
    for profile, trace_id in ((OPENAI_PROFILE, "verify-tpl-oa"), (ANTHROPIC_PROFILE, "verify-tpl-an")):
        got = client.post(
            "/v1/tasks",
            json=task_body(
                profile=profile,
                task_id=trace_id,
                prompt={"name": "summarize", "variables": variables},
                system="补充：保持客观。",
                stream=False,
            ),
            headers={"x-trace-id": trace_id},
        )
        report.check(f"[{profile}] 带模板引用的请求成功", got.status_code == 200)

        sent = upstream.last("/chat/completions" if profile == OPENAI_PROFILE else "/messages")["body"]
        system = sent["messages"][0]["content"] if profile == OPENAI_PROFILE else sent["system"]
        report.check(f"[{profile}] 模板渲染进 system：变量已替换",
                     "{{" not in system and "编辑" in system and "30" in system and "量子计算正在走向工程化。" in system)
        report.check(f"[{profile}] 模板当背景、调用方 system 当即时指令（两者都在）",
                     "只输出 JSON" in system and "保持客观" in system)

        record = trace_call(client, trace_id) or {}
        report.check(f"[{profile}] 调用记录记下实际引用的版本 summarize@v2",
                     record.get("prompt_name") == "summarize" and record.get("prompt_version") == "v2",
                     f"{record.get('prompt_name')}@{record.get('prompt_version')}")
        report.check(f"[{profile}] 记录带 prompt 指纹", bool(record.get("prompt_sha256")))

    # 4e) 显式钉住旧版本。
    upstream.reply("ok")
    got = client.post(
        "/v1/tasks",
        json=task_body(profile=OPENAI_PROFILE, task_id="verify-tpl-v1",
                       prompt={"name": "summarize", "version": "v1", "variables": variables}, stream=False),
        headers={"x-trace-id": "verify-tpl-v1"},
    )
    sent = upstream.last("/chat/completions")["body"]
    report.check("显式指定 version=v1 时渲染的是 v1 正文",
                 got.status_code == 200 and "只输出 JSON" not in sent["messages"][0]["content"])
    report.check("记录里的版本随引用变成 v1",
                 (trace_call(client, "verify-tpl-v1") or {}).get("prompt_version") == "v1")


def check_observability(report: Report, client: httpx.Client) -> None:
    """5. 可观测性：Token 分类统计 + 延迟（含首 Token 延迟）。"""
    report.section("5. 可观测性（Token 分类统计 + 首 Token 延迟）")

    for profile, expected_api in ((OPENAI_PROFILE, "openai-completions"), (ANTHROPIC_PROFILE, "anthropic-messages")):
        record = trace_call(client, f"verify-stream-{profile}") or {}
        usage = record.get("usage") or {}
        cost = record.get("cost") or {}
        report.check(f"[{profile}] 记录里 model / provider / api 三项齐全",
                     record.get("model") and record.get("provider") and record.get("api") == expected_api,
                     f"{record.get('provider')}/{record.get('model')} api={record.get('api')}")
        report.check(f"[{profile}] input_tokens 分类统计正确",
                     usage.get("input") == INPUT_TOKENS, str(usage.get("input")))
        report.check(f"[{profile}] output_tokens 分类统计正确",
                     usage.get("output") == OUTPUT_TOKENS, str(usage.get("output")))
        report.check(f"[{profile}] 缓存与思考类 token 也各有独立字段（无则为 0/None，不混进 input）",
                     usage.get("cache_read") == 0 and usage.get("cache_write") == 0,
                     f"cache_read={usage.get('cache_read')} cache_write={usage.get('cache_write')} reasoning={usage.get('reasoning')}")
        report.check(f"[{profile}] total_tokens = input + output",
                     usage.get("total_tokens") == INPUT_TOKENS + OUTPUT_TOKENS,
                     str(usage.get("total_tokens")))
        report.check(f"[{profile}] 成本按费率算出来了",
                     float(cost.get("total") or 0) > 0, str(cost.get("total")))

        ttft = float(record.get("ttft_ms") or 0)
        total = float(record.get("total_ms") or 0)
        report.check(
            f"[{profile}] 首 Token 延迟被单独测出来（≥ 上游首个分片的等待）",
            ttft >= FIRST_CHUNK_DELAY_S * 1000 * 0.5,
            f"ttft_ms={ttft:.1f}",
        )
        report.check(f"[{profile}] 总延迟 ≥ 首 Token 延迟", total >= ttft > 0, f"total_ms={total:.1f}, ttft_ms={ttft:.1f}")
        report.check(f"[{profile}] 流式分片数被记下来（字段确实来自流式路径）",
                     int(record.get("stream_chunk_count") or 0) > 0, str(record.get("stream_chunk_count")))

    # 聚合视图：取一行原始记录之外的指标口径。
    dashboard = client.get("/api/dashboard")
    report.check("Dashboard 聚合可用", dashboard.status_code == 200)


def check_retry(report: Report, client: httpx.Client, upstream: FakeUpstream) -> None:
    """6a. 韧性：指数退避重试（最多 3 次）。"""
    report.section("6a. 韧性（指数退避重试）")

    upstream.reply("重试之后成功。", fail_next=2)
    trace_id = "verify-retry"
    started = time.monotonic()
    got = client.post(
        "/v1/tasks",
        json=task_body(profile=OPENAI_PROFILE, task_id=trace_id, stream=False),
        headers={"x-trace-id": trace_id},
    )
    elapsed = time.monotonic() - started
    body = got.json()
    report.check("上游连续两次 500 后最终返回 200", got.status_code == 200, f"HTTP {got.status_code}")
    report.check("正文来自第 3 次尝试", body.get("terminal") == "done" and body.get("text") == "重试之后成功。",
                 str(body.get("text")))

    record = trace_call(client, trace_id) or {}
    report.check("调用记录 attempt=3（首次 + 2 次重试）", record.get("attempt") == 3, str(record.get("attempt")))
    report.check("重试计数与 attempt 一致", record.get("retry") == 2, str(record.get("retry")))
    # 500ms + 1000ms 的指数退避：真等了这么久，而不是立刻重打。
    report.check("退避是真等待（≥ 500ms + 1000ms 的量级）", elapsed >= 1.2, f"{elapsed:.2f}s")

    # 重试次数不是无限的：让上游一直错，最终必须停下来报错。
    upstream.reply("永远不会成功。", fail_next=99)
    trace_id = "verify-retry-exhausted"
    got = client.post(
        "/v1/tasks",
        json=task_body(profile=OPENAI_PROFILE, task_id=trace_id, stream=False),
        headers={"x-trace-id": trace_id},
    )
    record = trace_call(client, trace_id) or {}
    report.check("持续失败时不会无限重试（封顶 3 次重试后如实报错）",
                 got.json().get("terminal") == "error" and record.get("attempt") == 4,
                 f"terminal={got.json().get('terminal')} attempt={record.get('attempt')} "
                 f"code={record.get('error_code')}")
    upstream.reply("ok")


def check_rate_limit(report: Report, client: httpx.Client, upstream: FakeUpstream) -> None:
    """6b. 韧性：按模型独立限流（这一项跑在 RPM=3 的第二个实例上）。"""
    report.section("6b. 韧性（按模型独立限流，RPM=3）")
    upstream.reply("ok")

    codes = []
    retry_after = None
    detail = None
    for index in range(1, 5):
        got = client.post(
            "/v1/tasks",
            json=task_body(profile=OPENAI_PROFILE, task_id=f"verify-rl-{index}", stream=False),
            headers={"x-trace-id": f"verify-rl-{index}"},
        )
        codes.append(got.status_code)
        if got.status_code == 429:
            retry_after = retry_after or got.headers.get("retry-after")
            detail = detail or got.json()["detail"]

    report.check("前 3 个请求放行、第 4 个超限", codes == [200, 200, 200, 429], str(codes))
    report.check("超限返回 429 + Retry-After 响应头",
                 retry_after is not None and int(retry_after) >= 1, f"retry-after={retry_after}")
    report.check("429 正文带稳定错误码 RATE_LIMITED",
                 bool(detail) and detail.get("code") == "RATE_LIMITED", str(detail))

    got = client.post(
        "/v1/tasks",
        json=task_body(profile=ANTHROPIC_PROFILE, task_id="verify-rl-other", stream=False),
        headers={"x-trace-id": "verify-rl-other"},
    )
    report.check("另一个模型有自己的桶（A 被限住不影响 B）", got.status_code == 200, f"HTTP {got.status_code}")

    exchanges = client.get("/api/exchanges", params={"task_id": "verify-rl-4"}).json()
    report.check("被限流的请求也在通讯日志里留痕",
                 [item["error_code"] for item in exchanges] == ["RATE_LIMITED"],
                 str([(item["status"], item["error_code"]) for item in exchanges]))


def check_two_models(report: Report, client: httpx.Client, upstream: FakeUpstream) -> None:
    """验收标准：两个模型（两套协议）都能正常跑通。"""
    report.section("两个模型调用均可正常工作")
    upstream.reply("两个模型都可用。")

    for profile, label in ((OPENAI_PROFILE, OPENAI_LABEL), (ANTHROPIC_PROFILE, ANTHROPIC_LABEL)):
        trace_id = f"verify-both-{profile}"
        got = client.post(
            "/v1/tasks",
            json=task_body(profile=profile, task_id=trace_id, stream=False),
            headers={"x-trace-id": trace_id},
        )
        body = got.json()
        report.check(f"[{profile} → {label}] 非流式调用成功并返回正文",
                     got.status_code == 200 and body.get("terminal") == "done"
                     and body.get("text") == "两个模型都可用。",
                     f"terminal={body.get('terminal')} text={body.get('text')!r}")
        report.check(f"[{profile}] usage 回传给调用方",
                     (body.get("usage") or {}).get("total_tokens") == INPUT_TOKENS + OUTPUT_TOKENS,
                     str(body.get("usage")))


def configure_degradation(client: httpx.Client, upstream: FakeUpstream) -> None:
    """再登记两个 OpenAI 协议模型，并建一个 4 模型的**静态**路由表。

    静态模式才能把顺序钉死，让"下一个模型是谁"是确定的、可断言的。
    """
    for model_id in ("gpt-4o", "gpt-4.1-mini"):
        got = client.post(
            "/api/models",
            json={
                "id": model_id,
                "name": f"OpenAI {model_id}",
                "provider": "openai",
                "api": "openai-completions",
                "base_url": upstream.base_url,
                "api_key": OPENAI_KEY,
                "context_window": 128000,
                "max_tokens": 4096,
                "cost": {"input": 2.5, "output": 10.0, "cache_read": 0.0, "cache_write": 0.0},
                "capabilities": {"sse": True, "streaming": True, "json_schema": True},
            },
        )
        got.raise_for_status()

    got = client.post(
        "/api/profiles",
        json={
            "name": DEGRADE_PROFILE,
            "models": [{"label": label} for label in DEGRADE_CHAIN],
            "route_mode": "static",
            "static_order": list(DEGRADE_CHAIN),
            # 降级是"换模型"，不是"重试同一个模型"。关掉重试才能断言"每个模型恰好
            # 被调用一次"，也省掉指数退避的等待。
            "retry_enabled": False,
            "max_retries": 0,
        },
    )
    got.raise_for_status()


def check_degradation(report: Report, client: httpx.Client, upstream: FakeUpstream) -> None:
    """6c. 韧性：候选链降级（一个模型坏了，自动换下一个，直到有一个能成功）。"""
    report.section("6c. 韧性（候选链降级：4 个模型依次尝试）")
    configure_degradation(client, upstream)

    # 把 OpenAI 那条路（``/chat/completions``）打成**恒**失败；用 401 让它落到
    # ``AUTH_INVALID``——处置是"直接换模型"，不含"重试同一个模型"的干扰。
    # Anthropic 那条路（``/messages``）保持正常，降级的终点就是它。
    upstream.reply("降级之后才成功。")
    upstream.fail_paths("/chat/completions", status=401)

    trace_id = "verify-degrade-chain"
    try:
        got = client.post(
            "/v1/tasks",
            json=task_body(profile=DEGRADE_PROFILE, task_id=trace_id, stream=False),
            headers={"x-trace-id": trace_id},
        )
        body = got.json()
        chain = sorted(
            (client.get(f"/api/traces/{trace_id}").json().get("calls") or []),
            key=lambda row: row.get("attempt_index") or 0,
        )
        allotted = [row.get("model") for row in chain]

        report.check("前三个模型都不可用时，请求最终仍然成功（降级到第 4 个）",
                     got.status_code == 200 and body.get("terminal") == "done"
                     and body.get("text") == "降级之后才成功。",
                     f"terminal={body.get('terminal')} text={body.get('text')!r}")
        report.check(
            "候选链上 4 个模型**依次都试过**",
            allotted == ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "claude-3-5-haiku-20241022"],
            str(allotted),
        )
        report.check("每次尝试各落一条调用记录（4 次尝试 = 4 条记录）",
                     len(chain) == 4, f"{len(chain)} 条：{allotted}")
        report.check("4 条记录共享同一个 trace_id（一次请求就是一条链路）",
                     {row.get("trace_id") for row in chain} == {trace_id},
                     str({row.get("trace_id") for row in chain}))
        report.check("位次 attempt_index 从 1 连续到 4",
                     [row.get("attempt_index") for row in chain] == [1, 2, 3, 4],
                     str([row.get("attempt_index") for row in chain]))

        first = chain[0] if chain else {}
        report.check("失败的那几条如实记下错误码与处置（不再只剩一条『成功』记录）",
                     first.get("error_code") == "AUTH_INVALID"
                     and first.get("disposition") == "degrade",
                     f"error_code={first.get('error_code')} disposition={first.get('disposition')}")
        report.check("首跳没有『从谁降级而来』", not first.get("degraded_from"),
                     repr(first.get("degraded_from")))
        report.check(
            "后续每条都标出『从哪个模型降级而来』，降级方向可读",
            [row.get("degraded_from") for row in chain[1:]]
            == ["openai/gpt-4o-mini", "openai/gpt-4o", "openai/gpt-4.1-mini"],
            str([row.get("degraded_from") for row in chain[1:]]),
        )
        report.check("整条链路都被标为 fallback（降级不是隐形的）",
                     [row.get("fallback") for row in chain] == [1, 1, 1, 1],
                     str([row.get("fallback") for row in chain]))
        report.check("最终成功的记录写着真正出力的那个模型（不再写成主路由）",
                     chain[-1].get("model") == "claude-3-5-haiku-20241022"
                     and chain[-1].get("api") == "anthropic-messages"
                     and not chain[-1].get("error_code"),
                     f"model={chain[-1].get('model')} api={chain[-1].get('api')} "
                     f"error_code={chain[-1].get('error_code')}")

        route = first.get("route") or {}
        report.check("每条记录都带路由决策快照：候选链 4 个标签",
                     route.get("candidates") == list(DEGRADE_CHAIN),
                     str(route.get("candidates")))
        report.check("路由决策快照给出依据（为什么是这几个模型、这个顺序）",
                     bool(route.get("reason")), repr(route.get("reason")))

        per_model = [(row.get("model"), row.get("attempt")) for row in chain]
        report.check("关掉重试后每个模型恰好被调用一次（降级 = 换模型，不是原地重打）",
                     per_model == [
                         ("gpt-4o-mini", 1), ("gpt-4o", 1),
                         ("gpt-4.1-mini", 1), ("claude-3-5-haiku-20241022", 1),
                     ],
                     str(per_model))
    finally:
        # 无论如何都要把这条坏路修好，否则后面的验收项会莫名其妙地失败。
        upstream.fail_paths()


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def main() -> int:
    report = Report()
    with tempfile.TemporaryDirectory(prefix="llm-gw-verify-") as tmp:
        db_path = Path(tmp) / "verify.sqlite3"
        with FakeUpstream() as upstream:
            print(f"假上游：{upstream.base_url}")
            # 第一个实例不限额，跑功能项；限流那一项需要环境变量在启动时就固定，
            # 因此单独起第二个实例。
            with Gateway(db_path=db_path) as gateway:
                print(f"网关（不限额）：{gateway.base_url}")
                with gateway.client() as client:
                    configure(client, upstream)
                    check_translation(report, client, upstream)
                    check_model_routing(report, client, upstream)
                    check_streaming(report, client, upstream)
                    check_structured(report, client, upstream)
                    check_prompt_template(report, client, upstream)
                    check_observability(report, client)
                    check_retry(report, client, upstream)
                    check_two_models(report, client, upstream)
                    check_degradation(report, client, upstream)

            with Gateway(db_path=db_path, rate_limit_rpm=3) as gateway:
                print(f"网关（RPM=3）：{gateway.base_url}")
                with gateway.client() as client:
                    check_rate_limit(report, client, upstream)

    return report.finish()


if __name__ == "__main__":
    sys.exit(main())