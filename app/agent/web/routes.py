from html import escape
from pathlib import Path

from fastapi.responses import HTMLResponse

from app.agent.version import APP_VERSION

WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

INDEX_HTML = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
SETTINGS_HTML = (TEMPLATE_DIR / "settings.html").read_text(encoding="utf-8")
DEVICES_HTML = (TEMPLATE_DIR / "devices.html").read_text(encoding="utf-8")
TOOLS_HTML = (TEMPLATE_DIR / "tools.html").read_text(encoding="utf-8")
SIDEBAR_HTML = (TEMPLATE_DIR / "sidebar.html").read_text(encoding="utf-8")
APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
STYLES_CSS = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")


def index_page() -> HTMLResponse:
    return HTMLResponse(
        _render_page(
            body_class="agent-only",
            heading=None,
            subtitle=None,
            input_label="message",
            input_placeholder="Ask anything...",
        )
    )


def debug_page() -> HTMLResponse:
    return HTMLResponse(
        _render_page(
            body_class="debug-console page-console",
            heading="Retrieval debug",
            subtitle="Semantic search, keyword search, retrieval debug, pipeline status, and agent inspection.",
            input_label="query",
            input_placeholder="How much was the Acme Hardware receipt for the example appliance?",
        )
    )


def settings_page() -> HTMLResponse:
    return HTMLResponse(_render_aux_page(SETTINGS_HTML, "settings"))


def devices_page() -> HTMLResponse:
    return HTMLResponse(_render_aux_page(DEVICES_HTML, "devices"))


def tools_page() -> HTMLResponse:
    return HTMLResponse(_render_aux_page(TOOLS_HTML, "tools"))


def _render_aux_page(template: str, active_page: str) -> str:
    nav_class = f'class="{active_page}-nav"'
    sidebar = SIDEBAR_HTML.replace(nav_class, f'{nav_class} aria-current="page"')
    return template.replace("__SIDEBAR__", sidebar)


def _render_page(
    *,
    body_class: str,
    heading: str | None,
    subtitle: str | None,
    input_label: str,
    input_placeholder: str,
) -> str:
    intro = ""
    if heading is not None:
        intro = f'<h1>{escape(heading, quote=True)}</h1>'
        if subtitle is not None:
            intro += f'<p class="subtitle">{escape(subtitle, quote=True)}</p>'

    replacements = {
        "__BODY_CLASS__": body_class,
        "__INPUT_LABEL__": input_label,
        "__INPUT_PLACEHOLDER__": input_placeholder,
        "__BREADCRUMB_CURRENT__": escape(heading or "", quote=True),
        "__BREADCRUMB_CURRENT_HIDDEN__": "" if body_class != "agent-only" else "hidden",
        "__APP_VERSION__": APP_VERSION,
    }
    page = INDEX_HTML
    for placeholder, value in replacements.items():
        page = page.replace(placeholder, escape(value, quote=True))
    return page.replace("__PAGE_INTRO__", intro)
