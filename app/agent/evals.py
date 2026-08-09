"""Run deterministic evaluation cases against the agent HTTP endpoint."""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class EvalCase:
    question: str
    expected_answer_substring: str


@dataclass(frozen=True)
class EvalResult:
    case: EvalCase
    failures: list[str]

    @property
    def passed(self) -> bool:
        return not self.failures


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
        print(f"{'PASS' if result.passed else 'FAIL'} {result.case.question}")
        for failure in result.failures:
            print(f"  - {failure}")
    passed = sum(result.passed for result in results)
    print(f"{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


def load_cases(path: Path) -> list[EvalCase]:
    """Load evaluation cases from a JSON list."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("evaluation cases must be a JSON list")
    return [EvalCase(**case) for case in data]


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
        return EvalResult(case=case, failures=failures)

    if case.expected_answer_substring not in answer:
        failures.append(
            "answer is missing expected substring: "
            f"{case.expected_answer_substring!r}"
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


if __name__ == "__main__":
    raise SystemExit(main())
