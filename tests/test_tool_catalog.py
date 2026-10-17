import unittest
from unittest.mock import patch

from app.agent import tools
from app.agent.tools.base import Tool
from app.agent.tools.mcp import MCPTool


class ToolCatalogTest(unittest.TestCase):
    def test_catalog_contains_static_tools_in_planner_order(self) -> None:
        self.assertEqual(tools.AGENT_TOOL_NAMES, tools.TOOL_CATALOG.names)
        self.assertIsInstance(tools.TOOL_CATALOG.get("hybrid_search"), tools.StaticTool)

    def test_static_tool_exposes_function_description_and_arguments(self) -> None:
        tool = tools.TOOL_CATALOG.get("semantic_search")

        self.assertIsNotNone(tool)
        self.assertIsInstance(tool, Tool)
        self.assertIn("vector similarity", tool.description)
        self.assertEqual("string", tool.input_schema["properties"]["query"]["type"])
        self.assertEqual("integer", tool.input_schema["properties"]["limit"]["type"])
        self.assertEqual(["query"], tool.input_schema["required"])

    def test_catalog_dispatches_static_tool(self) -> None:
        expected = [{"chunk_id": 1}]
        with patch("app.agent.tools.builtin.semantic_search", return_value=expected) as implementation:
            result = tools.TOOL_CATALOG.execute("semantic_search", {"query": "hello", "limit": 2})

        self.assertEqual(expected, result)
        implementation.assert_called_once_with(query="hello", limit=2)

    def test_duplicate_tool_names_are_rejected(self) -> None:
        catalog = tools.ToolCatalog()
        catalog.register(tools.StaticTool(tools.builtin.semantic_search))

        with self.assertRaisesRegex(ValueError, "already registered"):
            catalog.register(tools.StaticTool(tools.builtin.semantic_search))

    def test_mcp_tool_uses_the_same_interface_and_keeps_server_identity(self) -> None:
        tool = MCPTool(
            server="muni",
            tool_name="find_nearby_stops",
            url="http://muni/mcp",
            tool_description="Find nearby transit stops.",
            schema={"type": "object", "properties": {"latitude": {"type": "number"}}},
        )

        self.assertIsInstance(tool, Tool)
        self.assertEqual("muni/find_nearby_stops", tool.name)
        self.assertEqual("number", tool.input_schema["properties"]["latitude"]["type"])

    def test_mcp_tool_dispatches_to_its_server(self) -> None:
        calls = []

        async def call_server(url, name, arguments):
            calls.append((url, name, arguments))
            return {"content": [{"text": "stops"}]}

        tool = MCPTool(
            server="muni",
            tool_name="find_nearby_stops",
            url="http://muni/mcp",
            tool_description="Find nearby transit stops.",
            schema={"type": "object"},
        )
        with patch("app.agent.tools.integrations.mcp_registry._call_tool_with_timeout", call_server):
            result = tool.execute({"limit": 3})

        self.assertEqual({"content": [{"text": "stops"}]}, result)
        self.assertEqual([("http://muni/mcp", "find_nearby_stops", {"limit": 3})], calls)


if __name__ == "__main__":
    unittest.main()
