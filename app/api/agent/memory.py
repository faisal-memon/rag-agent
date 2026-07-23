"""Personal memory storage and user-approval policy for the document agent."""

import re
from pathlib import Path

from app.api.config import get_api_settings

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
PROPOSAL_QUESTION = "should i remember this?"
_memory_store: "MemoryStore | None" = None


class MemoryStore:
    """Cached view of the durable Markdown memory file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.content = ""
        self.exists = False
        self.error: str | None = None
        self.loaded = False

    def load(self) -> str | None:
        """Refresh cached contents and return a read error, if any."""
        try:
            self.content = self.path.read_text(encoding="utf-8", errors="ignore")
            self.exists = True
            self.error = None
        except FileNotFoundError:
            self.content = ""
            self.exists = False
            self.error = None
        except OSError as exc:
            self.content = ""
            self.exists = False
            self.error = str(exc)
        self.loaded = True
        return self.error

    def read(self) -> dict:
        """Return the cached memory in the shape supplied to the agent."""
        if not self.loaded:
            self.load()
        return {
            "path": str(self.path),
            "content": self.content[:MAX_MEMORY_CHARS],
            "exists": self.exists,
            "truncated": len(self.content) > MAX_MEMORY_CHARS,
            "error": self.error,
        }

    def append(self, entry: str, section: str) -> None:
        if not self.loaded:
            self.load()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "" if self.content.endswith("\n") or not self.content else "\n"
        if self.content.strip():
            addition = f"{prefix}\n## {section}\n\n{entry}\n"
        else:
            addition = f"# Personal RAG Memory\n\n## {section}\n\n{entry}\n"
        with self.path.open("a", encoding="utf-8") as file:
            file.write(addition)
        self.load()


def get_memory_store() -> MemoryStore:
    """Return the process-local store for the configured memory file."""
    global _memory_store
    memory_path = get_api_settings().memory_path
    if _memory_store is None or _memory_store.path != memory_path:
        _memory_store = MemoryStore(memory_path)
    return _memory_store


def read_memory() -> dict:
    """Read the bounded personal memory used as agent routing guidance."""
    return get_memory_store().read()


def remember(entry: str, section: str = "Inbox") -> dict:
    """Append a concise Markdown bullet to the personal RAG memory file.

    Args:
        entry: Durable memory bullet, usually starting with "- ". Use for routing hints, vocabulary,
            evidence rules, durable user preferences, or user-approved corrections.
        section: Memory section heading, such as Routing Hints, Vocabulary, or Evidence Rules.
    """
    memory_store = get_memory_store()
    entry = normalize_entry(entry)
    section = normalize_section(section)
    if not entry:
        return {"remembered": False, "path": str(memory_store.path), "error": "Memory entry was empty."}

    memory_store.append(entry, section)

    return {
        "remembered": True,
        "path": str(memory_store.path),
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
    """Extract the immediately preceding explicit memory proposal after approval."""
    if not is_approval_response(question):
        return None

    if not history:
        return None
    previous_message = history[-1]
    if not isinstance(previous_message, dict) or previous_message.get("role") != "assistant":
        return None
    content = str(previous_message.get("content") or "")
    if PROPOSAL_QUESTION not in content.casefold():
        return None
    entry = entry_from_proposal(content)
    return {"entry": entry, "section": "Inbox"} if entry else None


def is_approval_response(question: str) -> bool:
    """Recognize direct or free-form approval of a previously proposed memory."""
    normalized_response = _normalized_response(question)
    if normalized_response in APPROVAL_RESPONSES:
        return True
    if _contains_any(normalized_response, NEGATION_TERMS):
        return False
    return _contains_any(normalized_response, APPROVAL_TERMS) and _contains_any(normalized_response, SAVE_TERMS)


def write_is_allowed(question: str, history: list[dict]) -> bool:
    """Allow only direct save requests or clear approval of an agent proposal."""
    lowered_question = " ".join(question.casefold().split())
    if _contains_any(lowered_question, DIRECT_WRITE_TERMS):
        return True
    return approved_from_history(question, history) is not None


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
