"""Adapters for tools implemented as local Python functions."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from types import UnionType
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints

from app.agent.tools.base import Tool


def _json_type(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        alternatives = [_json_type(item) for item in get_args(annotation) if item is not type(None)]
        alternatives = [item for item in alternatives if item]
        if len(alternatives) == 1:
            return alternatives[0]
        return {"anyOf": alternatives} if alternatives else {}
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is list or origin is list:
        return {"type": "array", "items": {}}
    if annotation is dict or origin is dict:
        return {"type": "object"}
    return {}


def _argument_descriptions(function: Callable[..., Any]) -> dict[str, str]:
    lines = inspect.getdoc(function).splitlines() if inspect.getdoc(function) else []
    descriptions: dict[str, str] = {}
    current: str | None = None
    in_args = False
    for line in lines[1:]:
        value = line.strip()
        if value == "Args:":
            in_args = True
            continue
        if not in_args or not value:
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", value)
        if match:
            current = match.group(1)
            descriptions[current] = match.group(2).strip()
        elif current:
            descriptions[current] = f"{descriptions[current]} {value}".strip()
    return descriptions


@dataclass(frozen=True)
class StaticTool(Tool):
    """A tool whose implementation is a local Python callable."""

    function: Callable[..., Any]

    @property
    def capabilities(self) -> frozenset[str]:
        return getattr(self.function, "__tool_capabilities__", frozenset())

    @property
    def name(self) -> str:
        return self.function.__name__

    @property
    def description(self) -> str:
        doc = inspect.getdoc(self.function) or ""
        return doc.splitlines()[0].strip() if doc else "No description provided."

    @property
    def input_schema(self) -> dict[str, Any]:
        hints = get_type_hints(self.function)
        descriptions = _argument_descriptions(self.function)
        properties: dict[str, Any] = {}
        required: list[str] = []
        for name, parameter in inspect.signature(self.function).parameters.items():
            schema = _json_type(hints.get(name, parameter.annotation))
            if name in descriptions:
                schema["description"] = descriptions[name]
            properties[name] = schema
            if parameter.default is inspect.Parameter.empty:
                required.append(name)
        result: dict[str, Any] = {"type": "object", "properties": properties}
        if required:
            result["required"] = required
        return result

    def execute(self, arguments: dict[str, Any]) -> Any:
        # Resolve by name at call time so instrumentation and test patches on the
        # implementation module remain effective after catalog construction.
        from app.agent.tools import builtin

        return getattr(builtin, self.name)(**arguments)
