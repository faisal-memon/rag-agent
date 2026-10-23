import asyncio
import unittest
from unittest.mock import patch

from app.agent import tools
from app.agent.tools.base import Tool
from app.agent.tools.mcp import MCPTool


class ToolCatalogTest(unittest.TestCase):
    def test_catalog_contains_static_tools_in_planner_order(self) -> None:
        self.assertIn("hybrid_search", tools.STATIC_TOOL_CATALOG.names)
        self.assertIsInstance(tools.STATIC_TOOL_CATALOG.get("hybrid_search"), tools.StaticTool)

    def test_static_tool_exposes_function_description_and_arguments(self) -> None:
        tool = tools.STATIC_TOOL_CATALOG.get("semantic_search")

        self.assertIsNotNone(tool)
        self.assertIsInstance(tool, Tool)
        self.assertIn("vector similarity", tool.description)
        self.assertEqual("string", tool.input_schema["properties"]["query"]["type"])
        self.assertEqual("integer", tool.input_schema["properties"]["limit"]["type"])
        self.assertEqual(["query"], tool.input_schema["required"])

    def test_catalog_dispatches_static_tool(self) -> None:
        expected = [{"chunk_id": 1}]
        with patch("app.agent.tools.builtin.semantic_search", return_value=expected) as implementation:
            result = tools.STATIC_TOOL_CATALOG.execute("semantic_search", {"query": "hello", "limit": 2})

        self.assertEqual(expected, result)
        implementation.assert_called_once_with(query="hello", limit=2)

    def test_tool_resolves_private_location_without_mutating_planner_arguments(self) -> None:
        tool = MCPTool(
            server="muni",
            tool_name="find_nearby_stops",
            url="http://muni/mcp",
            tool_description="Find nearby transit stops.",
            schema={"type": "object"},
        )
        arguments = {"location": {"latitude": "{{user_latitude}}", "longitude": "{{user_longitude}}"}}
        context = tools.ToolExecutionContext(tools.UserLocation(37.7, -122.4))
        with patch.object(MCPTool, "_execute", return_value={"content": []}) as execute:
            result = tool.execute(arguments, context)

        self.assertEqual({"content": []}, result)
        execute.assert_called_once_with({"location": {"latitude": 37.7, "longitude": -122.4}})
        self.assertEqual(
            {"location": {"latitude": "{{user_latitude}}", "longitude": "{{user_longitude}}"}}, arguments
        )

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

    def test_discovered_mcp_tools_are_individual_catalog_entries(self) -> None:
        catalog = tools.build_tool_catalog({"servers": [{
            "name": "muni",
            "url": "http://muni/mcp",
            "tools": [{
                "name": "find_nearby_stops",
                "description": "Find nearby stops.",
                "inputSchema": {"type": "object", "properties": {"latitude": {"type": "number"}}},
            }],
        }]})

        self.assertIn("muni/find_nearby_stops", catalog.names)
        self.assertIsInstance(catalog.get("muni/find_nearby_stops"), MCPTool)
        self.assertNotIn("call_mcp_tool", catalog.names)

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

    def test_mcp_tool_normalizes_mcp_error_response(self) -> None:
        async def call_server(_url, _name, _arguments):
            return {"isError": True, "content": [{"text": "Transit service is unavailable."}]}

        tool = MCPTool(
            server="muni",
            tool_name="find_nearby_stops",
            url="http://muni/mcp",
            tool_description="Find nearby transit stops.",
            schema={"type": "object"},
        )
        with patch("app.agent.tools.integrations.mcp_registry._call_tool_with_timeout", call_server):
            result = tool.execute({})

        self.assertEqual({"error": "Transit service is unavailable."}, result)

    def test_mcp_tool_exposes_nested_transport_error(self) -> None:
        async def call_server(_url, _name, _arguments):
            raise ExceptionGroup("request failed", [TimeoutError("upstream did not respond")])

        tool = MCPTool(
            server="news",
            tool_name="get_news",
            url="http://news/mcp",
            tool_description="Get news.",
            schema={"type": "object"},
        )
        with patch("app.agent.tools.integrations.mcp_registry._call_tool_with_timeout", call_server):
            with self.assertRaisesRegex(
                RuntimeError,
                "MCP transport failed: TimeoutError: upstream did not respond",
            ):
                tool.execute({})


class ToolCatalogManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_current_returns_builtin_snapshot_without_discovery(self) -> None:
        registry_calls = 0

        def load_registry() -> dict:
            nonlocal registry_calls
            registry_calls += 1
            return {"servers": []}

        manager = tools.ToolCatalogManager(refresh_interval=300.0, registry_loader=load_registry)

        self.assertIs(manager.current(), tools.STATIC_TOOL_CATALOG)
        self.assertEqual(0, registry_calls)

    async def test_manager_refreshes_until_stopped(self) -> None:
        registry_calls = 0

        def load_registry() -> dict:
            nonlocal registry_calls
            registry_calls += 1
            return {"servers": []}

        manager = tools.ToolCatalogManager(
            refresh_interval=0.01,
            registry_loader=load_registry,
            catalog_builder=lambda _registry: tools.ToolCatalog(),
        )

        initial_catalog = await manager.start()
        await asyncio.sleep(0.035)
        await manager.stop()
        calls_after_stop = registry_calls
        await asyncio.sleep(0.02)

        self.assertGreaterEqual(calls_after_stop, 2)
        self.assertEqual(calls_after_stop, registry_calls)
        self.assertIs(initial_catalog, manager.current())

    async def test_manager_keeps_builtin_tools_and_retries_after_startup_failure(self) -> None:
        registry_calls = 0

        def load_registry() -> dict:
            nonlocal registry_calls
            registry_calls += 1
            if registry_calls == 1:
                raise TimeoutError("registry timed out")
            return {"servers": []}

        manager = tools.ToolCatalogManager(
            refresh_interval=0.01,
            registry_loader=load_registry,
            catalog_builder=lambda _registry: tools.ToolCatalog(),
        )

        initial_catalog = await manager.start()
        await asyncio.sleep(0.03)
        await manager.stop()

        self.assertIs(initial_catalog, tools.STATIC_TOOL_CATALOG)
        self.assertGreaterEqual(registry_calls, 2)
        self.assertIsNot(manager.current(), tools.STATIC_TOOL_CATALOG)


if __name__ == "__main__":
    unittest.main()
