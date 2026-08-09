import unittest

from app.agent.evals import EvalCase, evaluate_case


class EvalCaseTest(unittest.TestCase):
    def test_accepts_response_with_expected_answer_substring(self) -> None:
        case = EvalCase(
            question="Example question",
            expected_answer_substring="answer",
        )
        response = {"answer": "This is the answer."}

        self.assertTrue(evaluate_case(case, response).passed)

    def test_reports_missing_expected_answer_substring(self) -> None:
        case = EvalCase(
            question="Example question",
            expected_answer_substring="required",
        )
        response = {"answer": "A different answer"}

        failures = evaluate_case(case, response).failures

        self.assertEqual(["answer is missing expected substring: 'required'"], failures)

    def test_reports_malformed_response_fields(self) -> None:
        result = evaluate_case(
            EvalCase(question="Example question", expected_answer_substring="answer"),
            {},
        )

        self.assertFalse(result.passed)
        self.assertEqual(
            ["response answer is not a string"],
            result.failures,
        )
