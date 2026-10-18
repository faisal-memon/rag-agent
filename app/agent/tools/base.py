"""Common interface for tools available to the agent."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """A named operation the agent can discover and invoke."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable name used in planner actions."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Short explanation shown to the planner."""

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """JSON Schema describing accepted arguments."""

    @property
    def capabilities(self) -> frozenset[str]:
        """Behavioral capabilities used by controller policy, not tool names."""
        return frozenset()

    @abstractmethod
    def execute(self, arguments: dict[str, Any]) -> Any:
        """Execute the operation with validated arguments."""


def with_capabilities(*capabilities: str):
    """Mark a built-in function with controller-relevant capabilities."""
    def decorate(function):
        function.__tool_capabilities__ = frozenset(capabilities)
        return function

    return decorate
