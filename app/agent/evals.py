"""Run deterministic evaluation cases against the agent HTTP endpoint."""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


RETRIEVAL_TOOLS = frozenset({"semantic_search", "keyword_search"})


@dataclass(frozen=True)
class EvalCase:
    name: str
    question: str
    required_answer_substrings: list[str] = field(default_factory=list)
    forbidden_answer_substrings: list[str] = field(default_factory=list)
    required_citation_path_substrings: list[str] = field(default_factory=list)
    forbidden_citation_path_substrings: list[str] = field(default_factory=list)
    minimum_tool_calls: int | None = None
    maximum_tool_calls: int | None = None
    required_tools: list[str] = field(default_factory=list)
    minimum_distinct_retrieval_tools: int | None = None


@dataclass(frozen=True)
class EvalResult:
    case: EvalCase
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures


def load_cases(path: Path) -> list[EvalCase]:
    """Load evaluation cases from a JSON list."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("evaluation cases must be a JSON list")

    cases = []
    for index, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"case {index} must be an object")
        try:
            cases.append(EvalCase(**item))
        except TypeError as exc:
            raise ValueError(f"case {index} is invalid: {exc}") from exc
    return cases


def query_agent(base_url: str, question: str) -> dict[str, Any]:
    """Send one question to the running agent endpoint."""
    url = f"{base_url.rstrip('/')}/agent/query"
    request = Request(
        url,
        data=json.dumps({"question": question}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=60) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("agent response must be a JSON object")
    return payload


def evaluate_case(case: EvalCase, response: dict[str, Any]) -> EvalResult:
    """Evaluate one response without making a network request."""
    failures = []
    answer = response.get("answer")
    if not isinstance(answer, str):
        failures.append("response answer is not a string")
        answer = ""

    citations = response.get("citations")
    citation_paths = [item.get("path", "") for item in citations if isinstance(item, dict)] if isinstance(citations, list) else []
    if not isinstance(citations, list):
        failures.append("response citations is not a list")

    tool_results = response.get("tool_results")
    tool_names = [item.get("tool", "") for item in tool_results if isinstance(item, dict)] if isinstance(tool_results, list) else []
    if not isinstance(tool_results, list):
        failures.append("response tool_results is not a list")

    for text in case.required_answer_substrings:
        if text not in answer:
            failures.append(f"answer is missing required substring: {text!r}")
    for text in case.forbidden_answer_substrings:
        if text in answer:
            failures.append(f"answer contains forbidden substring: {text!r}")
    for text in case.required_citation_path_substrings:
        if not any(text in path for path in citation_paths):
            failures.append(f"citations are missing required path substring: {text!r}")
    for text in case.forbidden_citation_path_substrings:
        if any(text in path for path in citation_paths):
            failures.append(f"citations contain forbidden path substring: {text!r}")

    tool_count = len(tool_names)
    if case.minimum_tool_calls is not None and tool_count < case.minimum_tool_calls:
        failures.append(f"tool calls {tool_count} is below minimum {case.minimum_tool_calls}")
    if case.maximum_tool_calls is not None and tool_count > case.maximum_tool_calls:
        failures.append(f"tool calls {tool_count} exceeds maximum {case.maximum_tool_calls}")
    for tool in case.required_tools:
        if tool not in tool_names:
            failures.append(f"required tool was not called: {tool!r}")

    retrieval_count = len(set(tool_names) & RETRIEVAL_TOOLS)
    if (
        case.minimum_distinct_retrieval_tools is not None
        and retrieval_count < case.minimum_distinct_retrieval_tools
    ):
        failures.append(
            "distinct retrieval tools "
            f"{retrieval_count} is below minimum {case.minimum_distinct_retrieval_tools}"
        )
    return EvalResult(case=case, failures=failures)


def run_cases(cases: list[EvalCase], base_url: str) -> list[EvalResult]:
    """Query and evaluate every case, retaining request failures as results."""
    results = []
    for case in cases:
        try:
            results.append(evaluate_case(case, query_agent(base_url, case.question)))
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            results.append(EvalResult(case=case, failures=[f"request failed: {exc}"]))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="path to a JSON list of evaluation cases")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("RAG_EVAL_BASE_URL", "http://localhost:8000"),
        help="agent base URL (default: RAG_EVAL_BASE_URL or http://localhost:8000)",
    )
    args = parser.parse_args(argv)
    try:
        results = run_cases(load_cases(args.cases), args.base_url)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for result in results:
        print(f"{'PASS' if result.passed else 'FAIL'} {result.case.name}")
        for failure in result.failures:
            print(f"  - {failure}")
    passed = sum(result.passed for result in results)
    print(f"{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
