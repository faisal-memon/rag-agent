"""Tool adapter for a manually configured remote MCP endpoint."""

from dataclasses import dataclass

from app.agent.tools.mcp import MCPTool


@dataclass(frozen=True)
class RemoteMCPTool(MCPTool):
    """An MCP tool called directly at its configured remote endpoint."""

    @property
    def name(self) -> str:
        return f"remote/{self.server}/{self.tool_name}"
