import json
import re
from pathlib import Path
from typing import Any

from openai import OpenAI

import app.api.agent.tools as tools
from app.api.agent.prompts import render_prompt
from app.core.config import get_settings
from app.core.embeddings import get_llm_client

MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARS = 24000
MAX_REASONING_STEP_CHARS = 12000
MAX_REASONING_TOTAL_CHARS = 48000
MAX_DEBUG_TEXT_CHARS = 12000
MAX_DEBUG_EVENTS = 80
MEMORY_DIRECT_WRITE_TERMS = (
    "remember",
    "save",
    "store",
    "add it to memory",
    "add that to memory",
)
MEMORY_APPROVAL_RESPONSES = {
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
MEMORY_APPROVAL_TERMS = {"yes", "y", "yep", "yeah", "sure", "ok", "okay"}
MEMORY_SAVE_TERMS = {"remember", "save", "store", "keep"}
MEMORY_NEGATION_TERMS = {"no", "nope", "nah", "not", "dont", "don't", "do not", "never"}


def render_system_prompt() -> str:
    return render_prompt("system.md", {})


def answer_with_agent(question: str, history: list[dict] | None = None) -> dict:
    history = history or []
    max_steps = get_settings().api.agent_max_steps
    conversation = _conversation_context(history)
    plan: list[dict] = []
    tool_results: list[dict] = []
    reasoning: list[dict] = []
    debug: list[dict] = []
    answer = ""
    seen_calls: set[str] = set()
    decision_feedback = ""

    approved_memory = _approved_memory_from_history(question, history)
    if approved_memory:
        step = {"tool": "remember", "arguments": approved_memory}
        tool_result = _execute_tool(step, question, conversation)
        return {
            "answer": _memory_result_answer(tool_result),
            "plan": [step],
            "tool_results": [tool_result],
            "reasoning": reasoning,
            "debug": [
                {
                    "event": "controller_decision",
                    "phase": "memory_approval",
                    "decision": "execute_tool",
                    "tool": "remember",
                    "arguments": approved_memory,
                    "result_summary": _summarize_tool_result(tool_result.get("result")),
                }
            ],
            "citations": [],
        }

    client, model = get_llm_client()
    memory = tools.read_memory()

    for _ in range(max_steps):
        decision = _decide_next_action(
            question,
            conversation,
            memory,
            tool_results,
            max_steps - len(plan),
            decision_feedback,
            reasoning,
            client,
            model,
            debug,
        )
        decision_feedback = ""
        if decision["action"] == "synthesize":
            _append_debug(debug, "controller_decision", phase="planning", decision="synthesize")
            break
        if decision["action"] == "answer":
            if decision["evidence_status"] == "not_found" and not _absence_search_complete(plan):
                decision_feedback = (
                    "The not-found conclusion was rejected. Before claiming absence, use at least two different "
                    "retrieval tools, including keyword_search or grep_documents."
                )
                _append_debug(
                    debug,
                    "controller_decision",
                    phase="planning",
                    decision="reject_answer",
                    reason="not_found_requires_more_retrieval",
                    parsed=decision,
                )
                continue
            answer = decision["answer"]
            _append_debug(
                debug,
                "controller_decision",
                phase="planning",
                decision="return_answer",
                parsed=decision,
            )
            break

        step = {"tool": decision["tool"], "arguments": decision["arguments"]}
        signature = json.dumps(step, sort_keys=True)
        if signature in seen_calls:
            plan.append(step)
            tool_results.append(
                {
                    **step,
                    "result": {
                        "error": "This exact tool call was already executed. Use existing evidence or try a different call."
                    },
                }
            )
            _append_debug(
                debug,
                "controller_decision",
                phase="tool",
                decision="reject_duplicate_tool_call",
                tool=step["tool"],
                arguments=step["arguments"],
            )
            continue

        seen_calls.add(signature)
        plan.append(step)
        _append_debug(
            debug,
            "controller_decision",
            phase="planning",
            decision="execute_tool",
            tool=step["tool"],
            arguments=step["arguments"],
        )
        tool_result = _execute_tool(step, question, conversation)
        tool_results.append(tool_result)
        _append_debug(
            debug,
            "tool_result",
            phase="tool",
            tool=step["tool"],
            arguments=step["arguments"],
            result_summary=_summarize_tool_result(tool_result.get("result")),
        )

    if not answer:
        answer = _synthesize_answer(question, conversation, memory, plan, tool_results, reasoning, client, model, debug)
    citations = _citations_from_tool_results(tool_results)

    return {
        "answer": answer,
        "plan": plan,
        "tool_results": tool_results,
        "reasoning": reasoning,
        "debug": debug,
        "citations": citations,
    }

def _decide_next_action(
    question: str,
    conversation: str,
    memory: dict,
    tool_results: list[dict],
    remaining_steps: int,
    decision_feedback: str,
    reasoning: list[dict],
    client: OpenAI,
    model: str,
    debug: list[dict],
) -> dict:
    prompt = render_prompt(
        "planner.md",
        {
            "remaining_steps": remaining_steps,
            "decision_feedback": decision_feedback or "(none)",
            "memory_path": memory.get("path") or "not configured",
            "memory": _memory_prompt_content(memory),
            "tool_descriptions": tools.render_tool_descriptions(),
            "conversation": conversation or "(none)",
            "question": question,
            "tool_results": json.dumps(_compact_tool_results(tool_results), indent=2),
        },
    )
    text = _complete_text(
        client,
        model,
        system=f"{render_system_prompt()}\n\nChoose one safe bounded action at a time. Return JSON only.",
        prompt=prompt,
        reasoning=reasoning,
        phase="planning",
    )
    _append_debug(debug, "model_response", phase="planning", raw_text=text)
    try:
        raw_json = _extract_json_object(text)
        parsed = json.loads(raw_json)
        _append_debug(debug, "parsed_action", phase="planning", parsed=parsed)
        if parsed.get("action") == "answer":
            answer = str(parsed.get("answer") or "").strip()
            if answer:
                evidence_status = str(parsed.get("evidence_status") or "").strip()
                if evidence_status not in {"supported", "not_found", "casual"}:
                    evidence_status = "not_found" if _looks_like_not_found(answer) else "supported"
                    if not tool_results:
                        evidence_status = "casual"
                return {
                    "action": "answer",
                    "evidence_status": evidence_status,
                    "answer": answer,
                }
        if parsed.get("action") == "tool":
            step = _sanitize_step(parsed)
            if step:
                return {"action": "tool", **step}
            _append_debug(
                debug,
                "controller_decision",
                phase="planning",
                decision="reject_tool",
                reason="tool_call_failed_sanitization",
                parsed=parsed,
            )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        _append_debug(
            debug,
            "parse_error",
            phase="planning",
            reason=str(exc),
            raw_text=text,
        )

    if not tool_results:
        _append_debug(
            debug,
            "controller_decision",
            phase="planning",
            decision="fallback_tool",
            reason="planner_output_unusable_without_tool_results",
            tool="semantic_search",
            arguments={"query": question, "limit": tools.DEFAULT_CHUNK_LIMIT},
        )
        return {
            "action": "tool",
            "tool": "semantic_search",
            "arguments": {"query": question, "limit": tools.DEFAULT_CHUNK_LIMIT},
        }
    _append_debug(
        debug,
        "controller_decision",
        phase="planning",
        decision="fallback_synthesize",
        reason="planner_output_unusable_with_existing_tool_results",
    )
    return {"action": "synthesize"}


def _execute_tool(step: dict, question: str = "", conversation: str = "") -> dict:
    tool = step["tool"]
    arguments = step.get("arguments", {})
    try:
        if tool == "search_documents":
            result = tools.search_documents(
                query=str(arguments.get("query") or ""),
                path_prefix=arguments.get("path_prefix"),
                limit=int(arguments.get("limit") or tools.DEFAULT_DOCUMENT_LIMIT),
            )
        elif tool == "keyword_search":
            result = tools.keyword_search(
                query=str(arguments.get("query") or ""),
                limit=int(arguments.get("limit") or tools.DEFAULT_CHUNK_LIMIT),
            )
        elif tool == "semantic_search":
            result = tools.semantic_search(
                query=str(arguments.get("query") or ""),
                limit=int(arguments.get("limit") or tools.DEFAULT_CHUNK_LIMIT),
            )
        elif tool == "grep_documents":
            result = tools.grep_documents(
                query=str(arguments.get("query") or ""),
                path=arguments.get("path"),
                path_prefix=arguments.get("path_prefix"),
                limit=int(arguments.get("limit") or tools.DEFAULT_GREP_LIMIT),
                context_chars=int(arguments.get("context_chars") or tools.DEFAULT_GREP_CONTEXT_CHARS),
            )
        elif tool == "read_document":
            result = tools.read_document(
                path=str(arguments.get("path") or ""),
                start_line=int(arguments.get("start_line") or 1),
                max_lines=int(arguments.get("max_lines") or tools.DEFAULT_DOCUMENT_LINES),
                max_chars=int(arguments.get("max_chars") or tools.DEFAULT_DOCUMENT_CHARS),
            )
        elif tool == "remember":
            if not _memory_write_is_allowed(question, conversation):
                result = {
                    "remembered": False,
                    "error": (
                        "Memory write refused. The latest user message must explicitly ask to remember/save "
                        "something or clearly approve a previous memory proposal."
                    ),
                }
            else:
                result = tools.remember(
                    entry=str(arguments.get("entry") or ""),
                    section=str(arguments.get("section") or "Inbox"),
                )
        else:
            result = {"error": f"Unsupported tool: {tool}"}
    except Exception as exc:
        result = {"error": str(exc)}

    return {"tool": tool, "arguments": arguments, "result": result}


def _synthesize_answer(
    question: str,
    conversation: str,
    memory: dict,
    plan: list[dict],
    tool_results: list[dict],
    reasoning: list[dict],
    client: OpenAI,
    model: str,
    debug: list[dict],
) -> str:
    prompt = render_prompt(
        "answer.md",
        {
            "memory_path": memory.get("path") or "not configured",
            "memory": _memory_prompt_content(memory),
            "conversation": conversation or "(none)",
            "question": question,
            "plan": json.dumps(plan, indent=2),
            "tool_results": json.dumps(_compact_tool_results(tool_results), indent=2),
        },
    )
    answer = _complete_text(
        client,
        model,
        system=render_system_prompt(),
        prompt=prompt,
        reasoning=reasoning,
        phase="answer",
    )
    _append_debug(debug, "model_response", phase="answer", raw_text=answer)
    return answer


def _complete_text(
    client: OpenAI,
    model: str,
    system: str,
    prompt: str,
    reasoning: list[dict] | None = None,
    phase: str = "",
) -> str:
    api = get_settings().api
    if api.llm_provider == "llamacpp":
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        message = response.choices[0].message
        _record_reasoning(reasoning, phase, _message_reasoning_content(message))
        return message.content or ""

    response = client.responses.create(model=model, input=f"{system}\n\n{prompt}")
    return response.output_text


def _message_reasoning_content(message: Any) -> str:
    content = getattr(message, "reasoning_content", None)
    if not content:
        model_extra = getattr(message, "model_extra", None)
        if isinstance(model_extra, dict):
            content = model_extra.get("reasoning_content")
    if not content:
        return ""
    if isinstance(content, str):
        return content.strip()
    return str(content).strip()


def _record_reasoning(reasoning: list[dict] | None, phase: str, content: str) -> None:
    if reasoning is None or not content:
        return
    used_chars = sum(len(item["content"]) for item in reasoning)
    remaining_chars = MAX_REASONING_TOTAL_CHARS - used_chars
    if remaining_chars <= 0:
        return
    reasoning.append(
        {
            "phase": phase or "model",
            "content": content[: min(MAX_REASONING_STEP_CHARS, remaining_chars)],
        }
    )


def _append_debug(debug: list[dict] | None, event: str, **fields: Any) -> None:
    if debug is None or len(debug) >= MAX_DEBUG_EVENTS:
        return
    item = {"event": event}
    for key, value in fields.items():
        item[key] = _debug_safe_value(value)
    debug.append(item)


def _debug_safe_value(value: Any) -> Any:
    if isinstance(value, str):
        truncated = value[:MAX_DEBUG_TEXT_CHARS]
        if len(value) > MAX_DEBUG_TEXT_CHARS:
            return f"{truncated}\n...[truncated {len(value) - MAX_DEBUG_TEXT_CHARS} chars]"
        return value
    if isinstance(value, list):
        return [_debug_safe_value(item) for item in value[:20]]
    if isinstance(value, dict):
        return {str(key): _debug_safe_value(item) for key, item in value.items()}
    return value


def _summarize_tool_result(result: Any) -> dict:
    if isinstance(result, list):
        return {"type": "list", "count": len(result)}
    if isinstance(result, dict):
        if result.get("error"):
            return {"type": "error", "error": str(result.get("error"))[:1000]}
        if "content" in result:
            content = str(result.get("content") or "")
            return {
                "type": "document",
                "chars": len(content),
                "truncated": bool(result.get("truncated")),
                "path": result.get("path"),
            }
        if "remembered" in result:
            return {
                "type": "memory",
                "remembered": bool(result.get("remembered")),
                "path": result.get("path"),
            }
        return {"type": "object", "keys": sorted(str(key) for key in result.keys())[:20]}
    return {"type": type(result).__name__}


def _conversation_context(history: list[dict]) -> str:
    messages = []
    total_chars = 0
    for item in reversed(history[-MAX_HISTORY_MESSAGES:]):
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        remaining = MAX_HISTORY_CHARS - total_chars
        if remaining <= 0:
            break
        content = content[-remaining:]
        messages.append(f"{role}: {content}")
        total_chars += len(content)

    return "\n".join(reversed(messages))


def _memory_prompt_content(memory: dict) -> str:
    error = memory.get("error")
    if error:
        return f"(memory unavailable: {error})"
    content = str(memory.get("content") or "").strip()
    if not content:
        return "(no saved memory)"
    suffix = "\n\n(memory truncated)" if memory.get("truncated") else ""
    return f"{content}{suffix}"


def _approved_memory_from_history(question: str, history: list[dict]) -> dict | None:
    if not _is_memory_approval_response(question):
        return None

    for item in reversed(history[-MAX_HISTORY_MESSAGES:]):
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        content = str(item.get("content") or "")
        if "remember" not in content.casefold():
            continue
        entry = _memory_entry_from_proposal(content)
        if entry:
            return {"entry": entry, "section": "Inbox"}
    return None


def _is_memory_approval_response(question: str) -> bool:
    normalized_response = _normalized_memory_response(question)
    if normalized_response in MEMORY_APPROVAL_RESPONSES:
        return True
    if _contains_memory_negation(normalized_response):
        return False
    return _contains_approval_term(normalized_response) and _contains_save_term(normalized_response)


def _normalized_memory_response(question: str) -> str:
    return re.sub(r"[^\w\s']", "", question.casefold()).strip()


def _contains_approval_term(text: str) -> bool:
    return any(_phrase_is_present(text, term) for term in MEMORY_APPROVAL_TERMS)


def _contains_save_term(text: str) -> bool:
    return any(_phrase_is_present(text, term) for term in MEMORY_SAVE_TERMS)


def _contains_memory_negation(text: str) -> bool:
    return any(_phrase_is_present(text, term) for term in MEMORY_NEGATION_TERMS)


def _memory_entry_from_proposal(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            return tools._normalize_memory_entry(stripped)
    return ""


def _memory_result_answer(tool_result: dict) -> str:
    result = tool_result.get("result")
    if isinstance(result, dict) and result.get("remembered"):
        return "Saved that memory."
    if isinstance(result, dict) and result.get("error"):
        return f"I could not save that memory: {result['error']}"
    return "I could not save that memory."


def _memory_write_is_allowed(question: str, conversation: str) -> bool:
    lowered_question = " ".join(question.casefold().split())
    if any(_phrase_is_present(lowered_question, term) for term in MEMORY_DIRECT_WRITE_TERMS):
        return True

    lowered_conversation = conversation.casefold()
    return "should i remember" in lowered_conversation and _is_memory_approval_response(question)


def _phrase_is_present(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _sanitize_step(step: Any) -> dict | None:
    if not isinstance(step, dict):
        return None
    tool = step.get("tool")
    if tool not in tools.AGENT_TOOL_FUNCTIONS:
        return None
    arguments = step.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}

    if tool in {"keyword_search", "semantic_search"}:
        return {
            "tool": tool,
            "arguments": {
                "query": str(arguments.get("query") or ""),
                "limit": tools._bounded_limit(arguments.get("limit") or tools.DEFAULT_CHUNK_LIMIT),
            },
        }

    if tool == "search_documents":
        path_prefix = arguments.get("path_prefix")
        return {
            "tool": tool,
            "arguments": {
                "query": str(arguments.get("query") or ""),
                "path_prefix": str(path_prefix) if path_prefix else None,
                "limit": tools._bounded_limit(arguments.get("limit") or tools.DEFAULT_DOCUMENT_LIMIT),
            },
        }

    if tool == "grep_documents":
        path = arguments.get("path")
        path_prefix = arguments.get("path_prefix")
        return {
            "tool": tool,
            "arguments": {
                "query": str(arguments.get("query") or ""),
                "path": str(path) if path else None,
                "path_prefix": str(path_prefix) if path_prefix else None,
                "limit": tools._bounded_limit(arguments.get("limit") or tools.DEFAULT_GREP_LIMIT),
                "context_chars": tools._bounded_context_chars(
                    arguments.get("context_chars") or tools.DEFAULT_GREP_CONTEXT_CHARS
                ),
            },
        }

    if tool == "remember":
        return {
            "tool": tool,
            "arguments": {
                "entry": tools._normalize_memory_entry(str(arguments.get("entry") or "")),
                "section": tools._normalize_memory_section(str(arguments.get("section") or "Inbox")),
            },
        }

    return {
        "tool": tool,
        "arguments": {
            "path": str(arguments.get("path") or ""),
            "start_line": tools._bounded_positive_int(arguments.get("start_line") or 1, maximum=1_000_000),
            "max_lines": tools._bounded_positive_int(
                arguments.get("max_lines") or tools.DEFAULT_DOCUMENT_LINES,
                maximum=1000,
            ),
            "max_chars": tools._bounded_max_chars(arguments.get("max_chars") or tools.DEFAULT_DOCUMENT_CHARS),
        },
    }


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found")
    return text[start : end + 1]


def _absence_search_complete(plan: list[dict]) -> bool:
    used_tools = {step.get("tool") for step in plan}
    retrieval_tools = used_tools & set(tools.RETRIEVAL_TOOL_NAMES)
    used_exact_search = bool(used_tools & {"keyword_search", "grep_documents"})
    return len(retrieval_tools) >= 2 and used_exact_search


def _looks_like_not_found(answer: str) -> bool:
    lowered = answer.casefold()
    return any(
        phrase in lowered
        for phrase in (
            "could not find",
            "couldn't find",
            "do not contain",
            "does not contain",
            "no information",
            "not found",
            "unable to find",
        )
    )


def _compact_tool_results(tool_results: list[dict]) -> list[dict]:
    compacted = []
    for item in tool_results:
        result = item.get("result")
        if isinstance(result, list):
            compact_result = result[:8]
        elif isinstance(result, dict) and "content" in result:
            compact_result = {**result, "content": result.get("content", "")[:8000]}
        else:
            compact_result = result
        compacted.append({**item, "result": compact_result})
    return compacted


def _citations_from_tool_results(tool_results: list[dict]) -> list[dict]:
    citations = []
    seen_paths = set()
    for item in tool_results:
        result = item.get("result")
        if item.get("tool") in {"keyword_search", "semantic_search"} and isinstance(result, list):
            for chunk in result:
                path = chunk.get("path")
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                citations.append(chunk)
        elif item.get("tool") == "search_documents" and isinstance(result, list):
            for document in result:
                path = document.get("path")
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                citations.append(
                    {
                        "chunk_id": None,
                        "filename": document.get("filename") or "",
                        "path": path or "",
                        "section": None,
                        "page": None,
                        "content": "",
                        "score": 0.0,
                    }
                )
        elif item.get("tool") == "grep_documents" and isinstance(result, list):
            for match in result:
                path = match.get("path")
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                citations.append(
                    {
                        "chunk_id": None,
                        "filename": match.get("filename") or Path(path or "").name,
                        "path": path or "",
                        "section": None,
                        "page": None,
                        "content": match.get("content") or "",
                        "score": 0.0,
                    }
                )
        elif item.get("tool") == "read_document" and isinstance(result, dict):
            path = result.get("path")
            if not path or path in seen_paths or result.get("error"):
                continue
            seen_paths.add(path)
            citations.append(
                {
                    "chunk_id": None,
                    "filename": Path(path).name,
                    "path": path,
                    "section": None,
                    "page": None,
                    "content": result.get("content") or "",
                    "score": 0.0,
                }
            )
    return citations
