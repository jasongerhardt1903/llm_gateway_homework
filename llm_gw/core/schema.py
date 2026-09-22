"""统一 Task Schema 与两层校验。

需求："定义统一的 task schema。LLM GW 和后端 agent 都应该遵循这个 schema。
对收到的 task 进行 2 层校验。1. 校验语法。2. 校验 task 是否符合 schema。"

两层错误刻意分开：语法错误（``SyntaxViolation``）说明请求根本不是 JSON，
schema 错误（``SchemaViolation``）说明结构不对但可以指出字段路径，
后者对 agent 自我修正更有用。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .messages import Capabilities

__all__ = [
    "SyntaxViolation",
    "SchemaViolation",
    "TextBlock",
    "ImageBlock",
    "ToolCallBlock",
    "ToolResultBlock",
    "TaskMessage",
    "ToolSpec",
    "PromptRef",
    "TaskInput",
    "Task",
    "ResponseMode",
    "validate_syntax",
    "validate_schema",
    "output_validity",
]

#: 结构化输出的生效模式。
#:
#: - ``json_schema``：调用方给了 schema，adapter 用严格 schema 约束上游；
#: - ``json_object``：只要求"返回合法 JSON"，无结构约束；
#: - ``none``：未请求结构化输出。
ResponseMode = Literal["none", "json_object", "json_schema"]


class SyntaxViolation(ValueError):
    """第一层校验失败：不是合法 JSON，或根节点不是对象。"""


class SchemaViolation(ValueError):
    """第二层校验失败：结构不符合统一 task schema。"""


class TextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    text: str


class ImageBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["image"]
    data: str
    mime_type: str = "image/png"


class ToolCallBlock(BaseModel):
    """assistant 轮次里发起的工具调用，多轮工具使用必需。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_call"]
    id: str = ""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    """工具执行结果，回填给模型。"""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tool_result"]
    tool_call_id: str
    content: str


ContentBlock = TextBlock | ImageBlock | ToolCallBlock | ToolResultBlock


class TaskMessage(BaseModel):
    """统一消息。``content`` 可以是纯文本或内容块列表。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[ContentBlock]
    name: str | None = None
    tool_call_id: str | None = None
    #: 失败的轮次（错误 / 取消）不应回灌给模型，由 transform 层丢弃。
    status: Literal["ok", "error", "aborted"] = "ok"

    def has_image(self) -> bool:
        return isinstance(self.content, list) and any(block.type == "image" for block in self.content)

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "".join(block.text for block in self.content if isinstance(block, TextBlock))

    def tool_calls(self) -> list[ToolCallBlock]:
        if not isinstance(self.content, list):
            return []
        return [block for block in self.content if isinstance(block, ToolCallBlock)]

    def tool_results(self) -> list[ToolResultBlock]:
        if not isinstance(self.content, list):
            return []
        return [block for block in self.content if isinstance(block, ToolResultBlock)]


class ToolSpec(BaseModel):
    """工具定义。参数用 JSON Schema 描述。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})


class PromptRef(BaseModel):
    """对网关内某个提示词模板版本的引用（需求"提示词版本管理"）。

    ``version`` 省略时取该 name 的最新版本；``variables`` 用来做 ``{{占位符}}``
    替换。做成 schema 里的具名字段而不是塞进 ``metadata``：模板引用是本次请求的
    一等输入，写错字段名就该 422 报错，而不是被静默忽略。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    version: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[TaskMessage] = Field(min_length=1)
    system: str | None = None
    tools: list[ToolSpec] = Field(default_factory=list)
    #: 结构化输出：请求时带上 JSON schema（第一层保证）。
    response_schema: dict[str, Any] | None = None
    #: 结构化输出的 OpenAI 形态别名。两种写法等价，二选一：
    #:
    #: - ``{"type": "json_object"}`` —— 只要求"返回合法 JSON"；
    #: - ``{"type": "json_schema", "json_schema": {"schema": {…}}}`` —— 归一化到
    #:   :attr:`response_schema`（``json_schema`` 里的 ``name`` / ``strict`` 由
    #:   adapter 按供应商能力重建，调用方不必关心）。
    #:
    #: 之所以要收下这个别名：验收口径按 OpenAI 的字段名发请求，只认
    #: ``response_schema`` 会让请求直接 422。
    response_format: dict[str, Any] | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    #: 需求规定 adapter 层默认采用 SSE 方式与 LLM 通讯。
    stream: bool = True

    @field_validator("temperature")
    @classmethod
    def _temperature_in_range(cls, value: float | None) -> float | None:
        if value is None:
            return value
        if not 0.0 <= value <= 2.0:
            raise ValueError("temperature must be between 0.0 and 2.0")
        return value

    @model_validator(mode="after")
    def _normalize_response_format(self) -> "TaskInput":
        """把 OpenAI 形态的 ``response_format`` 归一化。

        ``json_schema`` 形态抽出 schema 写进 :attr:`response_schema`，此后全链路
        只认后者；``json_object`` / ``text`` 形态不产生 schema，由
        :meth:`response_mode` 在请求翻译时判定。两种写法同时给出属于歧义，直接报错。

        ``json_object`` 刻意**不**落到 ``{"type": "object"}`` 这种空壳 schema：
        那会把 ``required_capabilities().json_schema`` 置真，把一个只支持
        ``json_object`` 的模型（DeepSeek）挡在路由之外——而它恰恰能满足这个请求。
        """
        fmt = self.response_format
        if fmt is None:
            return self
        if self.response_schema is not None:
            raise ValueError("response_schema 与 response_format 不能同时提供，请二选一")
        kind = fmt.get("type")
        if kind == "json_schema":
            spec = fmt.get("json_schema")
            schema = spec.get("schema") if isinstance(spec, dict) else None
            if not isinstance(schema, dict):
                raise ValueError(
                    'response_format.type="json_schema" 需要 '
                    "response_format.json_schema.schema 作为 JSON Schema 对象"
                )
            self.response_schema = schema
        elif kind not in ("json_object", "text"):
            raise ValueError(
                f'不支持的 response_format.type: {kind!r}'
                "（可选 json_object / json_schema / text）"
            )
        return self

    def response_mode(self) -> ResponseMode:
        """本次请求生效的结构化输出模式。"""
        if self.response_schema is not None:
            return "json_schema"
        if isinstance(self.response_format, dict) and self.response_format.get("type") == "json_object":
            return "json_object"
        return "none"


def output_validity(task_input: TaskInput, text: str) -> bool | None:
    """输出是否兑现了请求里声明的结构化约束（落 ``CallRecord.output_valid``）。

    - 未请求结构化输出 → ``None``：没有可判的东西，记 ``False`` 会把"没要求"诬成
      "不合格"；
    - ``json_object`` → 正文是否是合法 JSON；
    - ``json_schema`` → 除合法 JSON 外，结构还要对得上 schema。

    刻意**不引入** ``jsonschema`` 依赖：网关只需回答"兑现了没有"，而不是做通用校验
    器。因此只判 ``type`` / ``enum`` / ``required`` / ``properties`` / ``items``
    五个关键字——它们覆盖了 ``response_format`` 的常见形态；未列出的关键字**不参与
    判定**，``True`` 的准确含义是"在我们能判的范围内没有违例"。
    """
    mode = task_input.response_mode()
    if mode == "none":
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    if mode == "json_object":
        return True
    return _matches_schema(task_input.response_schema or {}, parsed)


def _matches_schema(schema: Any, value: Any) -> bool:
    """递归比对 JSON Schema 的受支持子集。"""
    if not isinstance(schema, dict):
        return True
    expected = schema.get("type")
    if expected is not None:
        names = expected if isinstance(expected, list) else [expected]
        if not any(_is_json_type(value, name) for name in names):
            return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, dict):
        if any(key not in value for key in schema.get("required", [])):
            return False
        for key, sub in (schema.get("properties") or {}).items():
            if key in value and not _matches_schema(sub, value[key]):
                return False
    if isinstance(value, list) and "items" in schema:
        if not all(_matches_schema(schema["items"], item) for item in value):
            return False
    return True


def _is_json_type(value: Any, name: str) -> bool:
    """JSON Schema 的类型判定。

    ``bool`` 在 Python 里是 ``int`` 的子类，因此 ``integer`` / ``number`` 必须显式
    排除它，否则 ``true`` 会被判成合法整数。未知类型名不参与判定（见
    :func:`output_validity` 的说明）。
    """
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "null":
        return value is None
    return True


class Task(BaseModel):
    """统一任务。agent 与网关之间的唯一请求形态。"""

    model_config = ConfigDict(extra="forbid")

    #: 必填：调用方需自带 id 以便幂等与链路追踪，网关不代为生成。
    task_id: str = Field(min_length=1)
    #: profile 名。路由层据此确定候选模型池与路由规则；为空时走 ``default`` profile。
    #: 需求第 31 行的 gwprofile 层就是通过这个字段被 agent 选中的。
    profile: str | None = None
    #: 模型标签（``provider/id``，也接受唯一匹配的裸 ``id``）。
    #:
    #: 需求第 27 行要求"根据请求中的 model 字段动态路由到对应适配器"，这是那条的
    #: 入口。与 ``profile`` 的分工是"**profile 定池子、model 定点名**"：点名后它在
    #: 候选池里排第一，其余模型仍作备用（显式点名不该让"主备两个路由"凭空消失）。
    #: 点名一个不在 profile 池子里的模型属于配置错误，**如实报**
    #: ``ROUTE_NO_CANDIDATE``，而不是悄悄用它——那等于把 profile 的作用域架空。
    model: str | None = None
    input: TaskInput
    metadata: dict[str, Any] = Field(default_factory=dict)
    #: 提示词模板引用（需求"提示词版本管理"）。为空表示本次请求不用模板，
    #: ``input.system`` 是什么就发什么。
    prompt: PromptRef | None = None

    # -- 派生信息 ----------------------------------------------------------

    def tool_rounds(self) -> int:
        """已发生的工具调用轮数：含 ``tool_call`` 块的 assistant 轮次数量。

        供 ``max_tool_rounds`` 护栏使用。网关不编排 agent 的工具循环，因此这个
        上限只能以"拒绝超限请求"的方式表达，而不能靠截断历史。
        """
        return sum(1 for message in self.input.messages if message.tool_calls())

    def required_capabilities(self) -> Capabilities:
        """从 task 特征推导出所需模型能力，供路由层做能力匹配。"""
        messages = self.input.messages
        if self.input.system:
            messages = [*messages]
        has_image = any(message.has_image() for message in messages)

        return Capabilities(
            sse=True,
            streaming=self.input.stream,
            tools=bool(self.input.tools),
            json_schema=self.input.response_schema is not None,
            vision=has_image,
        )

    def prompt_fingerprint(self) -> str:
        """Prompt 内容指纹，用于记录 hash 与版本。

        只取会影响模型行为的部分（system、messages、tools），
        不含 temperature 等采样参数——那些变化不构成新 prompt。
        """
        payload = {
            "system": self.input.system,
            "messages": [message.model_dump(exclude_none=True) for message in self.input.messages],
            "tools": [tool.model_dump() for tool in self.input.tools],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 两层校验
# --------------------------------------------------------------------------


def validate_syntax(raw: str | bytes) -> dict[str, Any]:
    """第一层：校验语法。

    必须是合法 JSON，且根节点为对象。
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SyntaxViolation(f"request body is not valid UTF-8: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SyntaxViolation(f"malformed JSON at line {exc.lineno} column {exc.colno}: {exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise SyntaxViolation(f"task root must be a JSON object, got {type(parsed).__name__}")

    return parsed


def _format_validation_error(exc: ValidationError) -> str:
    """把 pydantic 错误整理成带字段路径的可读文案。"""
    lines: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "root"
        lines.append(f"  - {location}: {error['msg']}")
    return "task does not match schema:\n" + "\n".join(lines)


def validate_schema(data: dict[str, Any]) -> Task:
    """第二层：校验是否符合统一 task schema。"""
    try:
        return Task.model_validate(data)
    except ValidationError as exc:
        raise SchemaViolation(_format_validation_error(exc)) from exc
