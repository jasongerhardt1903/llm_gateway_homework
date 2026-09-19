"""结构化输出：提取、修复、検证。

需求的结构化输出是**双层保证**：

* 第一层——请求时带 JSON schema（OpenAI 用 ``response_format``，Anthropic
  用 prompt 约束，见各 adapter 的 ``build_request``）；
* 第二层——返回后本地校验（真正的 JsonSchema 校验，不信任供应商）。

供应商的 schema 保证不等于应用层安全不报错，所以校验必须在网关里再做一次。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from pydantic import TypeAdapter, ValidationError

from ..core.errors import ErrorCode, GatewayError
from ..core.json_utils import extract_json, parse_json_with_repair, parse_streaming_json
from ..core.messages import AssistantMessage

__all__ = [
    "extract_structured",
    "validate_with_repair",
    "validate_json_schema",
    "SchemaValidationError",
    "parse_structured_output",
    "OutputValidator",
    "StructuredStreamValidator",
    "StructuredRepairError",
]

DEFAULT_MAX_REPAIR_ATTEMPTS = 2


class StructuredRepairError(ValueError):
    """结构化输出修复失败。携带可读的失败原因。"""


class SchemaValidationError(ValueError):
    """值不符合 JSON Schema。``errors`` 为逐条字段路径错误。"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def extract_structured(message: AssistantMessage) -> Any:
    """从助手消息中提取 JSON。

    ``extract_json`` 会剥离 ```json 围栏、从散文里截取配平片段，找不到时抛
    ``ValueError``。
    """
    text = message.text()
    if not text:
        raise StructuredRepairError("模型输出为空，无法提取结构化输出")
    try:
        return json.loads(extract_json(text))
    except ValueError as exc:
        raise StructuredRepairError(str(exc)) from exc


def validate_with_repair(
    raw: str,
    validate: Callable[[Any], Any],
    *,
    max_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
) -> Any:
    """解析 + 修复 + 校验，尝试次数有上限。

    第一次直接解析；之后每次都用 :func:`parse_json_with_repair` 修复后重试。
    **上限由 ``max_attempts`` 硬约束**，不会无限尝试。

    :param raw: 待解析的 JSON 文本（已从模型输出中提取出来）。
    :param validate: 对解析后的值做 schema 校验；不通过时抛异常。
    :param max_attempts: 允许的**修复**次数。总尝试次数为 ``max_attempts + 1``。

    全部失败时抛 :class:`StructuredRepairError`——明确报错，不静默吞掉。
    """
    errors: list[Exception] = []
    for attempt in range(max_attempts + 1):
        try:
            value = json.loads(raw) if attempt == 0 else parse_json_with_repair(raw)
            return validate(value)
        except (json.JSONDecodeError, StructuredRepairError, ValueError, TypeError) as exc:
            errors.append(exc)
    last = errors[-1] if errors else None
    raise StructuredRepairError(
        f"结构化输出在 {max_attempts + 1} 次尝试后仍无法通过校验: {last}"
    ) from last


# --------------------------------------------------------------------------
# JSON Schema 校验（第二层保证）
# --------------------------------------------------------------------------

#: 支持的 JSON Schema 关键字子集。刻意不做完整实现：完整实现需要引入
#: jsonschema 依赖，而 agent 侧实际用到的就是对象/数组/标量/必填/枚举这几类。
_SCALAR_ADAPTERS: dict[str, TypeAdapter] = {
    "string": TypeAdapter(str),
    "integer": TypeAdapter(int),
    "number": TypeAdapter(float),
    "boolean": TypeAdapter(bool),
    "null": TypeAdapter(type(None)),
}


def validate_json_schema(value: Any, schema: dict[str, Any] | None) -> list[str]:
    """用 JSON Schema 校验值，返回错误列表（空列表 = 通过）。

    对象/数组的结构由本函数递归检查，标量类型交给 pydantic 严格校验——
    这样既复用了 pydantic 的类型规则，又能给出带字段路径的错误。
    """
    errors: list[str] = []
    _validate(value, schema or {}, "$", errors)
    return errors


def _validate(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if not isinstance(schema, dict) or not schema:
        return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: 取值 {value!r} 不在枚举 {schema['enum']!r} 中")

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: 取值 {value!r} 不等于 const {schema['const']!r}")

    expected = schema.get("type")
    if expected is None:
        return
    if isinstance(expected, list):
        # 联合类型：任一通过即算通过。
        if not any(_type_matches(value, item) for item in expected):
            errors.append(f"{path}: 期望类型 {expected!r}，实际为 {type(value).__name__}")
        return

    if expected == "object":
        _validate_object(value, schema, path, errors)
    elif expected == "array":
        _validate_array(value, schema, path, errors)
    elif not _type_matches(value, expected):
        errors.append(f"{path}: 期望类型 {expected!r}，实际为 {type(value).__name__}")


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    adapter = _SCALAR_ADAPTERS.get(expected)
    if adapter is None:
        return True
    try:
        adapter.validate_python(value, strict=True)
        return True
    except ValidationError:
        return False


def _validate_object(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path}: 期望类型 'object'，实际为 {type(value).__name__}")
        return
    for name in schema.get("required") or []:
        if name not in value:
            errors.append(f"{path}.{name}: 缺少必填字段")
    properties = schema.get("properties") or {}
    for name, sub_schema in properties.items():
        if name in value:
            _validate(value[name], sub_schema, f"{path}.{name}", errors)
    if schema.get("additionalProperties") is False:
        for name in value:
            if name not in properties:
                errors.append(f"{path}.{name}: 不允许的额外字段")


def _validate_array(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{path}: 期望类型 'array'，实际为 {type(value).__name__}")
        return
    items = schema.get("items")
    if isinstance(items, dict):
        for index, item in enumerate(value):
            _validate(item, items, f"{path}[{index}]", errors)


def parse_structured_output(
    raw: str,
    schema: dict[str, Any] | None,
    *,
    max_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
) -> Any:
    """结构化输出总入口：提取 → 有界修复 → schema 校验。

    失败一律抛 :class:`GatewayError`（错误码 ``OUTPUT_SCHEMA_INVALID``），
    **绝不静默返回原值或空值**。
    """
    try:
        text = extract_json(raw)
    except ValueError as exc:
        raise GatewayError(
            ErrorCode.OUTPUT_SCHEMA_INVALID,
            f"无法从模型输出提取 JSON: {exc}",
        ) from exc

    try:
        value = validate_with_repair(text, _schema_validator(schema), max_attempts=max_attempts)
    except StructuredRepairError as exc:
        raise GatewayError(
            ErrorCode.OUTPUT_SCHEMA_INVALID,
            f"结构化输出校验失败（最多尝试 {max_attempts + 1} 次）: {exc}",
        ) from exc
    return value


def _schema_validator(schema: dict[str, Any] | None) -> Callable[[Any], Any]:
    def validate(value: Any) -> Any:
        errors = validate_json_schema(value, schema)
        if errors:
            raise SchemaValidationError(errors)
        return value

    return validate


class OutputValidator:
    """带 schema 的结构化输出校验器（第二层保证）。"""

    def __init__(self, schema: dict[str, Any] | None = None) -> None:
        self._schema = schema
        self._validate = _schema_validator(schema)

    def validate(self, value: Any) -> Any:
        return self._validate(value)

    def validate_text(self, raw: str, *, max_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS) -> Any:
        return parse_structured_output(raw, self._schema, max_attempts=max_attempts)

    def validate_message(self, message: AssistantMessage) -> Any:
        """对完整响应做校验（流式场景在流结束后调用）。"""
        return self.validate_text(message.text())


class StructuredStreamValidator:
    """流式结构化输出：增量解析 + 流结束后最终校验。

    需求："流式 JSON 需要增量解析，不能等全部 chunk 到齐再解析。但部分 JSON
    无法做 Schema 校验，需在流结束后做最终校验。"

    因此 ``feed`` **永不抛异常**，只做尽力而为的增量解析；schema 校验推迟到
    :meth:`finish`。
    """

    def __init__(self, schema: dict[str, Any] | None = None) -> None:
        self._schema = schema
        self._chunks: list[str] = []

    @property
    def text(self) -> str:
        return "".join(self._chunks)

    def feed(self, delta: str) -> Any | None:
        """喂入一个文本增量，返回当前已能解析出的部分结果。"""
        self._chunks.append(delta)
        if not self._chunks:
            return None
        return parse_streaming_json(self.text)

    def finish(self, *, max_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS) -> Any:
        """流结束后做最终校验。失败抛 ``OUTPUT_SCHEMA_INVALID``。"""
        return parse_structured_output(self.text, self._schema, max_attempts=max_attempts)