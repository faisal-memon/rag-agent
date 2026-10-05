import unittest
from unittest.mock import patch

from app.agent.external_tools.mcp_registry import get_mcp_registry


class MCPRegistryTest(unittest.TestCase):
    @patch("app.agent.external_tools.mcp_registry.urlopen")
    def test_validates_servers_and_returns_json_compatible_entries(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.__enter__.return_value = response
        response.__enter__.return_value.read.return_value = b"{}"
        with patch("app.agent.external_tools.mcp_registry.json.load", return_value={
            "servers": [
                {"name": "news", "url": "http://news-mcp:8080/mcp", "description": "RSS headlines", "enabled": True},
                {"name": "disabled", "url": "http://disabled/mcp", "enabled": False},
            ]
        }):
            result = get_mcp_registry()

        self.assertEqual(
            {"servers": [{"name": "news", "url": "http://news-mcp:8080/mcp", "description": "RSS headlines", "enabled": True}]},
            result,
        )

    @patch("app.agent.external_tools.mcp_registry.urlopen")
    def test_rejects_malformed_server(self, urlopen):
        with patch("app.agent.external_tools.mcp_registry.json.load", return_value={
            "servers": [{"name": "news", "url": "not-a-url"}]
        }):
            self.assertEqual({"error": "MCP registry returned an invalid document."}, get_mcp_registry())
