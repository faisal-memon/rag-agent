import json
from pathlib import Path
from typing import Any

from openai import OpenAI

import app.agent.tools as tools
from app.agent import memory
from app.agent import protocol
from app.agent.config import ApiSettings
from app.agent.llm import get_llm_client
from app.agent.memory import MemoryStore
from app.agent.prompts import PromptRegistry

MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARS = 24000
MAX_REASONING_STEP_CHARS = 12000
MAX_REASONING_TOTAL_CHARS = 48000
MAX_DEBUG_TEXT_CHARS = 12000
MAX_DEBUG_EVENTS = 80


class Agent:
    """Long-lived personal document agent and its process-owned state."""

    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.memory = MemoryStore(settings.memory_path)
        self.prompts = PromptRegistry()

    def startup(self) -> str | None:
        """Load prompts and the durable memory file before serving requests."""
        self.prompts.load()
        return self.memory.load()

    def answer(self, question: str, history: list[dict] | None = None) -> dict:
        """Answer one user question using bounded model-selected tools."""
        if not self.memory.loaded:
            self.startup()
        return _answer_with_agent(self, question, history)

    def render_prompt(self, name: str, values: dict[str, object]) -> str:
        return self.prompts.render(name, values)

    def render_system_prompt(self) -> str:
        return self.render_prompt("system.md", {})


def _answer_with_agent(
    agent: Agent,
    question: str,
    history: list[dict] | None = None,
) -> dict:
    history = history or []
    max_steps = agent.settings.agent_max_steps
    conversation = _conversation_context(history)
    plan: list[dict] = []
    tool_results: list[dict] = []
    reasoning: list[dict] = []
    debug: list[dict] = []
    answer = ""
    seen_calls: set[str] = set()
    decision_feedback = ""

    approved_memory = memory.approved_from_history(question, history)
    if approved_memory:
        step = {"tool": "remember", "arguments": approved_memory}
        tool_result = _execute_tool(step, question, history, agent)
        return {
            "answer": memory.result_answer(tool_result),
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

    client, model = get_llm_client(agent.settings)
    memory_state = agent.memory.read()

    for _ in range(max_steps):
        decision = _decide_next_action(
            question,
            conversation,
            memory_state,
            tool_results,
            max_steps - len(plan),
            decision_feedback,
            reasoning,
            client,
            model,
            debug,
            agent,
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
        tool_result = _execute_tool(step, question, history, agent)
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
        answer = _synthesize_answer(
            question,
            conversation,
            memory_state,
            plan,
            tool_results,
            reasoning,
            client,
            model,
            debug,
            agent,
        )
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
    memory_state: dict,
    tool_results: list[dict],
    remaining_steps: int,
    decision_feedback: str,
    reasoning: list[dict],
    client: OpenAI,
    model: str,
    debug: list[dict],
    agent: Agent,
) -> dict:
    prompt = agent.render_prompt(
        "planner.md",
        {
            "remaining_steps": remaining_steps,
            "decision_feedback": decision_feedback or "(none)",
            "memory_path": memory_state.get("path") or "not configured",
            "memory": memory.prompt_content(memory_state),
            "tool_descriptions": tools.render_tool_descriptions(),
            "conversation": conversation or "(none)",
            "question": question,
            "tool_results": json.dumps(_compact_tool_results(tool_results), indent=2),
        },
    )
    text = _complete_text(
        client,
        model,
        system=f"{agent.render_system_prompt()}\n\nChoose one safe bounded action at a time. Return JSON only.",
        prompt=prompt,
        settings=agent.settings,
        reasoning=reasoning,
        phase="planning",
    )
    _append_debug(debug, "model_response", phase="planning", raw_text=text)
    native_step = protocol.extract_native_tool_call(text)
    if native_step:
        _append_debug(
            debug,
            "parsed_action",
            phase="planning",
            parsed={"action": "tool", **native_step, "format": "native_tool_call"},
        )
        return {"action": "tool", **native_step}

    try:
        raw_json = protocol.extract_json_object(text)
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
            step = protocol.sanitize_step(parsed)
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
        if not tool_results and _is_agent_configuration_question(question) and text.strip():
            _append_debug(
                debug,
                "controller_decision",
                phase="planning",
                decision="fallback_prose_answer",
                reason="planner_prose_for_agent_configuration_question",
            )
            return {
                "action": "answer",
                "evidence_status": "casual",
                "answer": text.strip(),
            }
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


def _is_agent_configuration_question(question: str) -> bool:
    question_lower = question.lower()
    return any(
        phrase in question_lower
        for phrase in (
            "system prompt",
            "prompt",
            "your instructions",
            "agent behavior",
            "your behavior",
            "your tools",
            "your configuration",
        )
    )


def _execute_tool(
    step: dict,
    question: str = "",
    history: list[dict] | None = None,
    agent: Agent | None = None,
) -> dict:
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
            if not memory.write_is_allowed(question, history or []):
                result = {
                    "remembered": False,
                    "error": (
                        "Memory write refused. The latest user message must explicitly ask to remember/save "
                        "something or clearly approve a previous memory proposal."
                    ),
                }
            else:
                if agent is None:
                    raise RuntimeError("Agent is required to write memory.")
                result = memory.remember(
                    agent.memory,
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
    memory_state: dict,
    plan: list[dict],
    tool_results: list[dict],
    reasoning: list[dict],
    client: OpenAI,
    model: str,
    debug: list[dict],
    agent: Agent,
) -> str:
    prompt = agent.render_prompt(
        "answer.md",
        {
            "memory_path": memory_state.get("path") or "not configured",
            "memory": memory.prompt_content(memory_state),
            "conversation": conversation or "(none)",
            "question": question,
            "plan": json.dumps(plan, indent=2),
            "tool_results": json.dumps(_compact_tool_results(tool_results), indent=2),
        },
    )
    answer = _complete_text(
        client,
        model,
        system=agent.render_system_prompt(),
        prompt=prompt,
        settings=agent.settings,
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
    settings: ApiSettings,
    reasoning: list[dict] | None = None,
    phase: str = "",
) -> str:
    if settings.llm_provider == "llamacpp":
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
