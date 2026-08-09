import unittest

from app.agent.evals import EvalCase, evaluate_case


class EvalCaseTest(unittest.TestCase):
    def test_accepts_response_that_meets_all_checks(self) -> None:
        case = EvalCase(
            name="complete",
            question="Example question",
            required_answer_substrings=["answer"],
            forbidden_answer_substrings=["invented"],
            required_citation_path_substrings=["manual"],
            forbidden_citation_path_substrings=["private"],
            minimum_tool_calls=2,
            maximum_tool_calls=3,
            required_tools=["semantic_search", "keyword_search"],
            minimum_distinct_retrieval_tools=2,
        )
        response = {
            "answer": "This is the answer.",
            "citations": [{"path": "/documents/manual.pdf"}],
            "tool_results": [{"tool": "semantic_search"}, {"tool": "keyword_search"}],
        }

        self.assertTrue(evaluate_case(case, response).passed)

    def test_reports_deterministic_failures(self) -> None:
        case = EvalCase(
            name="failures",
            question="Example question",
            required_answer_substrings=["required"],
            forbidden_answer_substrings=["forbidden"],
            required_citation_path_substrings=["wanted"],
            forbidden_citation_path_substrings=["blocked"],
            minimum_tool_calls=2,
            maximum_tool_calls=0,
            required_tools=["keyword_search"],
            minimum_distinct_retrieval_tools=2,
        )
        response = {
            "answer": "A forbidden answer",
            "citations": [{"path": "/documents/blocked.pdf"}],
            "tool_results": [{"tool": "semantic_search"}],
        }

        failures = evaluate_case(case, response).failures

        self.assertEqual(8, len(failures))
        self.assertIn("answer is missing required substring: 'required'", failures)
        self.assertIn("required tool was not called: 'keyword_search'", failures)

    def test_reports_malformed_response_fields(self) -> None:
        result = evaluate_case(EvalCase(name="bad", question="Example question"), {})

        self.assertFalse(result.passed)
        self.assertEqual(
            [
                "response answer is not a string",
                "response citations is not a list",
                "response tool_results is not a list",
            ],
            result.failures,
        )
