from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"


class PromptRegistry:
    """Startup-loaded prompt templates used by one agent process."""

    def __init__(self, prompt_dir: Path = PROMPT_DIR) -> None:
        self.prompt_dir = prompt_dir
        self.templates: dict[str, str] = {}

    def load(self) -> None:
        """Load all shipped Markdown prompt templates."""
        self.templates = {
            path.name: path.read_text(encoding="utf-8")
            for path in self.prompt_dir.glob("*.md")
        }

    def render(self, name: str, values: dict[str, object]) -> str:
        """Render one prompt using simple named placeholder replacement."""
        template = self.templates.get(name)
        if template is None:
            template = (self.prompt_dir / name).read_text(encoding="utf-8")
            self.templates[name] = template
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace(f"{{{{ {key} }}}}", str(value))
        return rendered


_default_prompts = PromptRegistry()


def initialize_prompts() -> None:
    """Load default templates for standalone callers and tests."""
    _default_prompts.load()


def render_prompt(name: str, values: dict[str, object]) -> str:
    """Render a prompt outside the long-lived API runtime."""
    return _default_prompts.render(name, values)
