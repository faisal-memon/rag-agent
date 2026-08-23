import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from app.agent.evals import EvalCase, EvalResult, evaluate_case, main


class EvalCaseTest(unittest.TestCase):
    def test_main_prints_agent_answer(self) -> None:
        case = EvalCase(question="Example question", expected_answer_substrings=["answer"])
        result = EvalResult(
            case=case,
            answer="This is the answer.",
            failures=[],
            elapsed_seconds=1.5,
        )
        output = io.StringIO()

        with (
            patch("app.agent.evals.load_cases", return_value=[case]),
            patch("app.agent.evals.run_cases", return_value=[result]),
            redirect_stdout(output),
        ):
            exit_code = main(["cases.json"])

        self.assertEqual(0, exit_code)
        self.assertIn("This is the answer.", output.getvalue())
        self.assertIn("PASS (1.50s)", output.getvalue())
        self.assertIn("1/1 passed (100.0%) in 1.50s", output.getvalue())

    def test_accepts_response_with_any_expected_answer_substring(self) -> None:
        case = EvalCase(
            question="Example question",
            expected_answer_substrings=["unmatched", "ANSWER"],
        )
        response = {"answer": "This is the answer."}

        self.assertTrue(evaluate_case(case, response).passed)

    def test_reports_missing_expected_answer_substring(self) -> None:
        case = EvalCase(
            question="Example question",
            expected_answer_substrings=["required", "also missing"],
        )
        response = {"answer": "A different answer"}

        failures = evaluate_case(case, response).failures

        self.assertEqual(
            ["answer is missing an expected substring: ['required', 'also missing']"],
            failures,
        )

    def test_reports_malformed_response_fields(self) -> None:
        result = evaluate_case(
            EvalCase(question="Example question", expected_answer_substrings=["answer"]),
            {},
        )

        self.assertFalse(result.passed)
        self.assertEqual(
            ["response answer is not a string"],
            result.failures,
        )
