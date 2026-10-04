import unittest
from unittest.mock import patch

from app.agent.external_tools.cat_facts import get_cat_fact


class CatFactsToolTest(unittest.TestCase):
    def test_returns_mcp_content(self) -> None:
        with patch(
            "app.agent.external_tools.cat_facts.anyio.run",
            return_value={"content": ["Cats have whiskers."]},
        ):
            self.assertEqual({"content": ["Cats have whiskers."]}, get_cat_fact())

    def test_converts_mcp_failures_to_tool_result(self) -> None:
        with patch(
            "app.agent.external_tools.cat_facts.anyio.run",
            side_effect=RuntimeError("unavailable"),
        ):
            result = get_cat_fact()

        self.assertIn("Cat Facts MCP request failed", result["error"])

