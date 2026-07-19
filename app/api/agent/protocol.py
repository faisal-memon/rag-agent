"""Translate untrusted model output into bounded agent actions.

The planner is allowed to choose a tool, but never to invoke Python functions
or write SQL directly. This module is the boundary between model text and the
small action dictionary that the controller is willing to execute.
"""

import json
import re
from typing import Any

import app.api.agent.tools as tools
from app.api.agent import memory


def extract_json_object(text: str) -> str:
    """Return the outer JSON object from a model response."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found")
    return text[start : end + 1]


def extract_native_tool_call(text: str) -> dict | None:
    """Parse llama.cpp's native tool-call text when a model emits it."""
    match = re.search(
        r"<\|tool_call\>\s*call:(?P<tool>\w+)\s*(?P<args>\{.*?\})\s*<tool_call\|>",
        text,
        flags=re.DOTALL,
    )
    if not match:
        return None

    return sanitize_step(
        {
            "tool": match.group("tool"),
            "arguments": parse_native_tool_arguments(match.group("args")),
        }
    )


def parse_native_tool_arguments(text: str) -> dict[str, Any]:
    """Parse the limited argument syntax emitted by llama.cpp tool calls."""
    body = text.strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1]
    arguments: dict[str, Any] = {}
    for match in re.finditer(r"(?P<key>\w+)\s*:\s*(?P<value>\"[^\"]*\"|'[^']*'|[^,}]+)", body):
        key = match.group("key")
        value = match.group("value").strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            arguments[key] = value[1:-1]
            continue
        try:
            arguments[key] = int(value)
        except ValueError:
            arguments[key] = value
    return arguments


def sanitize_step(step: Any) -> dict | None:
    """Allowlist a model-selected tool and bound each of its arguments."""
    if not isinstance(step, dict):
        return None
    tool = step.get("tool")
    if tool not in tools.AGENT_TOOL_FUNCTIONS:
        return None
    arguments = step.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}

    if tool in {"keyword_search", "semantic_search"}:
        return {
            "tool": tool,
            "arguments": {
                "query": str(arguments.get("query") or ""),
                "limit": tools._bounded_limit(arguments.get("limit") or tools.DEFAULT_CHUNK_LIMIT),
            },
        }

    if tool == "search_documents":
        path_prefix = arguments.get("path_prefix")
        return {
            "tool": tool,
            "arguments": {
                "query": str(arguments.get("query") or ""),
                "path_prefix": str(path_prefix) if path_prefix else None,
                "limit": tools._bounded_limit(arguments.get("limit") or tools.DEFAULT_DOCUMENT_LIMIT),
            },
        }

    if tool == "grep_documents":
        path = arguments.get("path")
        path_prefix = arguments.get("path_prefix")
        return {
            "tool": tool,
            "arguments": {
                "query": str(arguments.get("query") or ""),
                "path": str(path) if path else None,
                "path_prefix": str(path_prefix) if path_prefix else None,
                "limit": tools._bounded_limit(arguments.get("limit") or tools.DEFAULT_GREP_LIMIT),
                "context_chars": tools._bounded_context_chars(
                    arguments.get("context_chars") or tools.DEFAULT_GREP_CONTEXT_CHARS
                ),
            },
        }

    if tool == "remember":
        return {
            "tool": tool,
            "arguments": {
                "entry": memory.normalize_entry(str(arguments.get("entry") or "")),
                "section": memory.normalize_section(str(arguments.get("section") or "Inbox")),
            },
        }

    return {
        "tool": tool,
        "arguments": {
            "path": str(arguments.get("path") or ""),
            "start_line": tools._bounded_positive_int(arguments.get("start_line") or 1, maximum=1_000_000),
            "max_lines": tools._bounded_positive_int(
                arguments.get("max_lines") or tools.DEFAULT_DOCUMENT_LINES,
                maximum=1000,
            ),
            "max_chars": tools._bounded_max_chars(arguments.get("max_chars") or tools.DEFAULT_DOCUMENT_CHARS),
        },
    }
