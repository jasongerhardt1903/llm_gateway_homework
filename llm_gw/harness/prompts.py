"""提示词模板：存储、变量替换、版本引用。

需求三大能力之一：「提示词版本管理：支持模板存储、变量替换和版本引用」。

三件事的分工：

* **存储**——模板以 ``(name, version)`` 为唯一键落在 SQLite 的 ``prompts`` 表里
  （建表与读写见 ``storage.py``）。同一个 name 可以有任意多个 version。
* **变量替换**——占位符语法 ``{{变量名}}``（``{{ 名字 }}`` 这种带空格的写法也认）。
  变量名在**写入时**就从正文里抽出来存进 ``variables`` 列，因此"这个模板需要哪些
  变量"是可查的，而不是只能靠读正文去猜。
* **版本引用**——task 里用 ``prompt: {name, version, variables}`` 引用；``version``
  省略时取该 name 的最新版本，解析出的**确切版本**会落进调用记录。

严格性是刻意的：缺变量报错、**多给变量也报错**。后者看似苛刻，但模板最常见的故障
恰恰是变量名拼错（把 ``{{question}}`` 写成 ``{{qusetion}}``），静默忽略只会让模型
收到一个带空洞的 prompt，线上排查成本远高于当场报错。

版本：0.8.8
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = [
    "PromptTemplateError",
    "PromptTemplate",
    "extract_variables",
    "render",
    "placeholder_names",
]

#: 占位符：``{{ name }}``。变量名限定标识符形态，避免把 ``{{a.b}}`` 这类
#: 疑似表达式的东西当成合法变量名——本层做的是字符串替换，不是模板语言。
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class PromptTemplateError(ValueError):
    """模板引用失败：找不到版本、缺变量、多给变量。"""


def placeholder_names(body: str) -> list[str]:
    """正文里出现过的占位符名（保持出现顺序，去重）。

    保留出现顺序是为了在报错与界面提示里按"读到的先后"列出变量，而不是字母序。
    """
    seen: dict[str, None] = {}
    for match in _PLACEHOLDER.finditer(body):
        seen.setdefault(match.group(1), None)
    return list(seen)


def extract_variables(body: str) -> list[str]:
    """写入时算出模板需要哪些变量（排序后存库，便于比对与展示）。"""
    return sorted(placeholder_names(body))


def _stringify(value: Any) -> str:
    """变量值转字符串。

    非字符串走 JSON 而不是 ``str()``：``str({"a": 1})`` 产出的是 Python 的字面量
    写法（单引号、True/None），把这种"半 JSON"喂给模型很容易让对方误解。
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def render(body: str, variables: Mapping[str, Any]) -> str:
    """把 ``{{变量}}`` 替换成对应取值。

    缺变量与多给变量都抛 :class:`PromptTemplateError`——调用方据此返回
    ``PROMPT_INVALID``，让 agent 能明确知道该补什么、该删什么。
    """
    required = placeholder_names(body)
    missing = [name for name in required if name not in variables]
    if missing:
        raise PromptTemplateError("缺少变量: " + ", ".join(missing))
    unknown = [name for name in variables if name not in required]
    if unknown:
        raise PromptTemplateError("模板未引用这些变量: " + ", ".join(sorted(unknown)) + "（可能是变量名拼错了）")
    return _PLACEHOLDER.sub(lambda match: _stringify(variables[match.group(1)]), body)


@dataclass(frozen=True)
class PromptTemplate:
    """一个具名版本。``variables`` 是写入时从 ``body`` 抽出来的。"""

    name: str
    version: str
    body: str
    variables: list[str] = field(default_factory=list)
    created_at: float = 0.0

    def render(self, variables: Mapping[str, Any]) -> str:
        try:
            return render(self.body, variables)
        except PromptTemplateError as exc:
            raise PromptTemplateError(f"模板 {self.name}@{self.version} {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "body": self.body,
            "variables": list(self.variables),
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "PromptTemplate":
        raw = row["variables"] or "[]"
        try:
            variables = [str(item) for item in json.loads(raw)]
        except (json.JSONDecodeError, TypeError):
            variables = []
        return cls(
            name=str(row["name"]),
            version=str(row["version"]),
            body=str(row["body"]),
            variables=variables,
            created_at=float(row["created_at"] or 0.0),
        )