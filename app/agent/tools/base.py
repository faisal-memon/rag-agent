"""Common interface for tools available to the agent."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UserLocation:
    """Private location available during tool execution, never planner input."""

    latitude: float
    longitude: float


@dataclass(frozen=True)
class ToolExecutionContext:
    """Request-scoped facts that tools may use while executing an action."""

    user_location: UserLocation | None = None

    def resolve_placeholders(self, value: Any) -> Any:
        """Resolve execution-only placeholders without changing recorded planner arguments."""
        if self.user_location is not None:
            if value == "{{user_latitude}}":
                return self.user_location.latitude
            if value == "{{user_longitude}}":
                return self.user_location.longitude
        if isinstance(value, dict):
            return {key: self.resolve_placeholders(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve_placeholders(item) for item in value]
        return value


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

    def execute(self, arguments: dict[str, Any], context: ToolExecutionContext | None = None) -> Any:
        """Execute the operation with execution-only placeholders resolved."""
        resolved_arguments = (context or ToolExecutionContext()).resolve_placeholders(arguments)
        return self._execute(resolved_arguments)

    @abstractmethod
    def _execute(self, arguments: dict[str, Any]) -> Any:
        """Execute resolved, validated arguments for this concrete tool."""


def with_capabilities(*capabilities: str):
    """Mark a built-in function with controller-relevant capabilities."""
    def decorate(function):
        function.__tool_capabilities__ = frozenset(capabilities)
        return function

    return decorate
