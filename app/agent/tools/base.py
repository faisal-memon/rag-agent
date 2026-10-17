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

    @abstractmethod
    def execute(self, arguments: dict[str, Any]) -> Any:
        """Execute the operation with validated arguments."""
