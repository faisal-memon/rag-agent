"""Long-lived resources owned by the document-agent process."""

from app.agent.config import ApiSettings
from app.agent.memory import MemoryStore
from app.agent.prompts import PromptRegistry


class AgentRuntime:
    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.memory = MemoryStore(settings.memory_path)
        self.prompts = PromptRegistry()

    def startup(self) -> str | None:
        self.prompts.load()
        return self.memory.load()
