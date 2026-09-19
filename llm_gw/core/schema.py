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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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
    "TaskInput",
    "Task",
    "validate_syntax",
    "validate_schema",
]


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


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[TaskMessage] = Field(min_length=1)
    system: str | None = None
    tools: list[ToolSpec] = Field(default_factory=list)
    #: 结构化输出：请求时带上 JSON schema（第一层保证）。
    response_schema: dict[str, Any] | None = None
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


class Task(BaseModel):
    """统一任务。agent 与网关之间的唯一请求形态。"""

    model_config = ConfigDict(extra="forbid")

    #: 必填：调用方需自带 id 以便幂等与链路追踪，网关不代为生成。
    task_id: str = Field(min_length=1)
    #: profile 名。路由层据此确定候选模型池与路由规则；为空时走 ``default`` profile。
    #: 需求第 31 行的 gwprofile 层就是通过这个字段被 agent 选中的。
    profile: str | None = None
    input: TaskInput
    metadata: dict[str, Any] = Field(default_factory=dict)

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
