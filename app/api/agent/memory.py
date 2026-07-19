"""Personal memory storage and user-approval policy for the document agent."""

import re
from pathlib import Path

from app.core.config import get_settings

MAX_MEMORY_CHARS = 12000
MAX_MEMORY_ENTRY_CHARS = 1000
DIRECT_WRITE_TERMS = (
    "remember",
    "save",
    "store",
    "add it to memory",
    "add that to memory",
)
APPROVAL_RESPONSES = {
    "yes",
    "y",
    "yep",
    "yeah",
    "sure",
    "ok",
    "okay",
    "do it",
    "make it so",
}
APPROVAL_TERMS = {"yes", "y", "yep", "yeah", "sure", "ok", "okay"}
SAVE_TERMS = {"remember", "save", "store", "keep"}
NEGATION_TERMS = {"no", "nope", "nah", "not", "dont", "don't", "do not", "never"}


def read_memory() -> dict:
    """Read the bounded personal memory file used as agent routing guidance."""
    memory_path = get_settings().api.memory_path
    try:
        content = memory_path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return _empty_memory(memory_path)
    except OSError as exc:
        return _empty_memory(memory_path, error=str(exc))

    return {
        "path": str(memory_path),
        "content": content[:MAX_MEMORY_CHARS],
        "exists": True,
        "truncated": len(content) > MAX_MEMORY_CHARS,
        "error": None,
    }


def remember(entry: str, section: str = "Inbox") -> dict:
    """Append a concise Markdown bullet to the personal RAG memory file.

    Args:
        entry: Durable memory bullet, usually starting with "- ". Use for routing hints, vocabulary,
            evidence rules, durable user preferences, or user-approved corrections.
        section: Memory section heading, such as Routing Hints, Vocabulary, or Evidence Rules.
    """
    memory_path = get_settings().api.memory_path
    entry = normalize_entry(entry)
    section = normalize_section(section)
    if not entry:
        return {"remembered": False, "path": str(memory_path), "error": "Memory entry was empty."}

    memory_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = memory_path.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        existing = ""

    prefix = "" if existing.endswith("\n") or not existing else "\n"
    if existing.strip():
        addition = f"{prefix}\n## {section}\n\n{entry}\n"
    else:
        addition = f"# Personal RAG Memory\n\n## {section}\n\n{entry}\n"
    with memory_path.open("a", encoding="utf-8") as file:
        file.write(addition)

    return {
        "remembered": True,
        "path": str(memory_path),
        "section": section,
        "entry": entry,
        "error": None,
    }


def prompt_content(memory: dict) -> str:
    """Render memory for a prompt while preserving unavailable/truncated state."""
    error = memory.get("error")
    if error:
        return f"(memory unavailable: {error})"
    content = str(memory.get("content") or "").strip()
    if not content:
        return "(no saved memory)"
    suffix = "\n\n(memory truncated)" if memory.get("truncated") else ""
    return f"{content}{suffix}"


def approved_from_history(question: str, history: list[dict]) -> dict | None:
    """Extract a prior memory proposal when the latest user reply approves it."""
    if not is_approval_response(question):
        return None

    for item in reversed(history[-12:]):
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        content = str(item.get("content") or "")
        if "remember" not in content.casefold():
            continue
        entry = entry_from_proposal(content)
        if entry:
            return {"entry": entry, "section": "Inbox"}
    return None


def is_approval_response(question: str) -> bool:
    """Recognize direct or free-form approval of a previously proposed memory."""
    normalized_response = _normalized_response(question)
    if normalized_response in APPROVAL_RESPONSES:
        return True
    if _contains_any(normalized_response, NEGATION_TERMS):
        return False
    return _contains_any(normalized_response, APPROVAL_TERMS) and _contains_any(normalized_response, SAVE_TERMS)


def write_is_allowed(question: str, conversation: str) -> bool:
    """Allow only direct save requests or clear approval of an agent proposal."""
    lowered_question = " ".join(question.casefold().split())
    if _contains_any(lowered_question, DIRECT_WRITE_TERMS):
        return True
    return "should i remember" in conversation.casefold() and is_approval_response(question)


def result_answer(tool_result: dict) -> str:
    """Turn a memory write result into the user-facing confirmation."""
    result = tool_result.get("result")
    if isinstance(result, dict) and result.get("remembered"):
        return "Saved that memory."
    if isinstance(result, dict) and result.get("error"):
        return f"I could not save that memory: {result['error']}"
    return "I could not save that memory."


def normalize_entry(entry: str) -> str:
    """Convert model text into one bounded Markdown memory bullet."""
    cleaned = " ".join(str(entry or "").split())[:MAX_MEMORY_ENTRY_CHARS].strip()
    if not cleaned:
        return ""
    return cleaned if cleaned.startswith("- ") else f"- {cleaned.lstrip('-').strip()}"


def normalize_section(section: str) -> str:
    """Convert model text into a safe, bounded Markdown heading."""
    cleaned = " ".join(str(section or "").replace("#", "").split())
    return (cleaned or "Inbox")[:80]


def _empty_memory(memory_path: Path, error: str | None = None) -> dict:
    return {
        "path": str(memory_path),
        "content": "",
        "exists": False,
        "truncated": False,
        "error": error,
    }


def _normalized_response(question: str) -> str:
    return re.sub(r"[^\w\s']", "", question.casefold()).strip()


def _contains_any(text: str, phrases: set[str] | tuple[str, ...]) -> bool:
    return any(_phrase_is_present(text, phrase) for phrase in phrases)


def _phrase_is_present(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def entry_from_proposal(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            return normalize_entry(stripped)
    return ""
