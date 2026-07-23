from html import escape
from pathlib import Path

from fastapi.responses import HTMLResponse

WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

INDEX_HTML = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
STYLES_CSS = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")


def index_page() -> HTMLResponse:
    return HTMLResponse(
        _render_page(
            body_class="agent-only",
            heading="your document agent",
            subtitle="Ask questions, continue the conversation, and inspect evidence when you need it.",
            input_label="message",
            input_placeholder="Ask about your documents...",
        )
    )


def debug_page() -> HTMLResponse:
    return HTMLResponse(
        _render_page(
            body_class="debug-console",
            heading="document query console",
            subtitle="Semantic search, keyword search, retrieval debug, pipeline status, and agent inspection.",
            input_label="query",
            input_placeholder="How much was the Acme Hardware receipt for the example appliance?",
        )
    )


def _render_page(
    *,
    body_class: str,
    heading: str,
    subtitle: str,
    input_label: str,
    input_placeholder: str,
) -> str:
    replacements = {
        "__BODY_CLASS__": body_class,
        "__PAGE_HEADING__": heading,
        "__PAGE_SUBTITLE__": subtitle,
        "__INPUT_LABEL__": input_label,
        "__INPUT_PLACEHOLDER__": input_placeholder,
    }
    page = INDEX_HTML
    for placeholder, value in replacements.items():
        page = page.replace(placeholder, escape(value, quote=True))
    return page
