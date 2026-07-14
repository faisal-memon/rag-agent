from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"


def render_prompt(name: str, values: dict[str, object]) -> str:
    template = (PROMPT_DIR / name).read_text(encoding="utf-8")
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", str(value))
    return rendered
