from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"
_templates: dict[str, str] = {}


def initialize_prompts() -> None:
    """Load shipped prompt templates once when the API process starts."""
    _templates.clear()
    for path in PROMPT_DIR.glob("*.md"):
        _templates[path.name] = path.read_text(encoding="utf-8")


def render_prompt(name: str, values: dict[str, object]) -> str:
    template = _templates.get(name)
    if template is None:
        template = (PROMPT_DIR / name).read_text(encoding="utf-8")
        _templates[name] = template
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", str(value))
    return rendered
