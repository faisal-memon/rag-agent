import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

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
IDENTITY_ONBOARDING_QUESTION = "Before I search your documents, what name should I use to identify your records?"
ProgressCallback = Callable[[dict], None]


class Agent:
    """Long-lived personal document agent and its process-owned state."""

    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.memory = MemoryStore(settings.memory_path)
        self.prompts = PromptRegistry()

    def startup(self) -> str | None:
        """Load prompts and the durable memory file before serving requests."""
        self.prompts.load()
        if not self.settings.memory_enabled:
            self.memory.loaded = True
            return None
        return self.memory.load()

    def answer(
        self,
        question: str,
        history: list[dict] | None = None,
        on_progress: ProgressCallback | None = None,
        profile_name: str | None = None,
    ) -> dict:
        """Answer one user question using bounded model-selected tools."""
        if not self.memory.loaded:
            self.startup()
        return _answer_with_agent(self, question, history, on_progress, profile_name)

    def render_prompt(self, name: str, values: dict[str, object]) -> str:
        return self.prompts.render(name, values)

    def render_system_prompt(self) -> str:
        return self.render_prompt("system.md", {})


def _answer_with_agent(
    agent: Agent,
    question: str,
    history: list[dict] | None = None,
    on_progress: ProgressCallback | None = None,
    profile_name: str | None = None,
) -> dict:
    history = history or []
    max_steps = agent.settings.agent_max_steps
    conversation = _conversation_context(history)
    plan: list[dict] = []
    tool_results: list[dict] = []
    reasoning: list[dict] = []
    debug: list[dict] = []
    seen_calls: set[str] = set()

    question, memory_state, onboarding_response = _prepare_question_and_memory(
        agent, question, history, profile_name, debug
    )
    if onboarding_response:
        return onboarding_response

    memory_approval_response = (
        _handle_memory_approval(agent, question, history, on_progress, reasoning)
        if agent.settings.memory_enabled and agent.settings.memory_require_approval
        else None
    )
    if memory_approval_response:
        return memory_approval_response

    client, model = get_llm_client(agent.settings)

    answer = _run_agent_loop(
        agent,
        question,
        history,
        conversation,
        memory_state,
        profile_name,
        max_steps,
        plan,
        tool_results,
        reasoning,
        debug,
        seen_calls,
        on_progress,
        client,
        model,
    )

    if not answer:
        _emit_progress(on_progress, "thinking", message="Preparing answer")
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


def _run_agent_loop(
    agent: Agent,
    question: str,
    history: list[dict],
    conversation: str,
    memory_state: dict,
    profile_name: str | None,
    max_steps: int,
    plan: list[dict],
    tool_results: list[dict],
    reasoning: list[dict],
    debug: list[dict],
    seen_calls: set[str],
    on_progress: ProgressCallback | None,
    client: OpenAI,
    model: str,
) -> str:
    """Run bounded planner and tool iterations, returning a direct answer if chosen."""
    decision_feedback = ""

    for _ in range(max_steps):
        _emit_progress(
            on_progress,
            "thinking",
            message="Reviewing available information" if tool_results else "Thinking",
        )
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
            profile_name,
        )
        answer, decision_feedback, should_stop = _handle_planner_decision(
            decision,
            plan,
            tool_results,
            debug,
        )
        if should_stop:
            break
        if decision["action"] != "tool":
            continue
        tool_result = _execute_planned_tool(
            decision,
            question,
            history,
            agent,
            plan,
            tool_results,
            debug,
            seen_calls,
            on_progress,
        )
        if decision["tool"] == "call_mcp_tool":
            result = tool_result.get("result") or {}
            if isinstance(result, dict) and result.get("error"):
                server = decision["arguments"].get("server", "unknown")
                tool = decision["arguments"].get("tool", "unknown")
                raise RuntimeError(f"MCP tool {server}/{tool} failed: {result['error']}")

    return answer or ""


def _handle_planner_decision(
    decision: dict,
    plan: list[dict],
    tool_results: list[dict],
    debug: list[dict],
) -> tuple[str | None, str, bool]:
    """Apply a planner decision that does not execute a tool."""
    if decision["action"] == "synthesize":
        _append_debug(debug, "controller_decision", phase="planning", decision="synthesize")
        return None, "", True

    if decision["action"] != "answer":
        return None, "", False

    if decision["evidence_status"] == "not_found" and not _absence_search_complete(plan):
        feedback = (
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
        return None, feedback, False

    _append_debug(
        debug,
        "controller_decision",
        phase="planning",
        decision="return_answer",
        parsed=decision,
    )
    return decision["answer"], "", True


def _execute_planned_tool(
    decision: dict,
    question: str,
    history: list[dict],
    agent: Agent,
    plan: list[dict],
    tool_results: list[dict],
    debug: list[dict],
    seen_calls: set[str],
    on_progress: ProgressCallback | None,
) -> dict:
    """Execute one planner-selected tool and record its trace events."""
    step = {"tool": decision["tool"], "arguments": decision["arguments"]}
    signature = json.dumps(step, sort_keys=True)
    if signature in seen_calls:
        for prior_step, prior_result in zip(plan, tool_results):
            if prior_step == step:
                _append_debug(
                    debug,
                    "controller_decision",
                    phase="tool",
                    decision="reuse_existing_tool_result",
                    tool=step["tool"],
                    arguments=step["arguments"],
                )
                _emit_progress(
                    on_progress,
                    "tool_result",
                    tool=step["tool"],
                    result=_summarize_tool_result(prior_result.get("result")),
                )
                return prior_result

        # Keep the guard safe if a caller supplies an inconsistent plan/result pair.
        duplicate_result = {
            **step,
            "result": {"error": "A previous result for this tool call is unavailable."},
        }
        plan.append(step)
        tool_results.append(duplicate_result)
        _append_debug(
            debug,
            "controller_decision",
            phase="tool",
            decision="reject_duplicate_tool_call",
            tool=step["tool"],
            arguments=step["arguments"],
        )
        _emit_progress(
            on_progress,
            "tool_result",
            tool=step["tool"],
            result=_summarize_tool_result(duplicate_result["result"]),
        )
        return duplicate_result

    seen_calls.add(signature)
    plan.append(step)
    _emit_progress(on_progress, "tool_call", tool=step["tool"], arguments=step["arguments"])
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
    _emit_progress(
        on_progress,
        "tool_result",
        tool=step["tool"],
        result=_summarize_tool_result(tool_result.get("result")),
    )
    _append_debug(
        debug,
        "tool_result",
        phase="tool",
        tool=step["tool"],
        arguments=step["arguments"],
        result_summary=_summarize_tool_result(tool_result.get("result")),
    )
    return tool_result


def _prepare_question_and_memory(
    agent: Agent,
    question: str,
    history: list[dict],
    profile_name: str | None,
    debug: list[dict],
) -> tuple[str, dict, dict | None]:
    """Load memory and complete the profile-name onboarding flow when needed."""
    if not agent.settings.memory_enabled:
        return question, {"path": "(disabled)", "content": "", "exists": False, "truncated": False, "error": None}, None
    memory_state = agent.memory.read()
    original_question = _identity_question_from_history(history)
    if profile_name or memory.profile_name(memory_state):
        return question, memory_state, None

    if not original_question:
        if _needs_profile_name(question):
            return question, memory_state, _identity_onboarding_response()
        return question, memory_state, None

    name = memory.profile_name_from_reply(question)
    if not name:
        return (
            question,
            memory_state,
            _identity_onboarding_response("Please reply with the name you would like me to use for your records."),
        )

    remembered = memory.remember(agent.memory, f"{memory.PROFILE_NAME_PREFIX} {name}", "Profile")
    if remembered.get("error"):
        return question, memory_state, _identity_onboarding_response(f"I could not save your name: {remembered['error']}")

    _append_debug(
        debug,
        "controller_decision",
        phase="identity",
        decision="save_profile_name",
        profile_name=name,
    )
    return original_question, agent.memory.read(), None


def _handle_memory_approval(
    agent: Agent,
    question: str,
    history: list[dict],
    on_progress: ProgressCallback | None,
    reasoning: list[dict],
) -> dict | None:
    """Persist an explicitly approved memory proposal without invoking the model."""
    approved_memory = memory.approved_from_history(question, history)
    if not approved_memory:
        return None

    step = {"tool": "remember", "arguments": approved_memory}
    _emit_progress(on_progress, "tool_call", tool=step["tool"], arguments=step["arguments"])
    tool_result = _execute_tool(step, question, history, agent)
    result_summary = _summarize_tool_result(tool_result.get("result"))
    _emit_progress(on_progress, "tool_result", tool=step["tool"], result=result_summary)
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
                "result_summary": result_summary,
            }
        ],
        "citations": [],
    }


def _forced_location_mcp_action(question: str, agent: Agent, registry: dict) -> dict | None:
    """Route explicit transit/location requests without document-search fallback."""
    text = question.lower()
    transit_terms = ("muni", "transit", "bus", "train", "tram", "streetcar")
    location_terms = ("near me", "near my", "nearby", "my location", "my coordinates", "near coordinates")
    if not any(term in text for term in transit_terms) or not any(term in text for term in location_terms):
        return None
    latitude = agent.settings.home_latitude
    longitude = agent.settings.home_longitude
    if latitude is None or longitude is None:
        raise RuntimeError("Location is not configured for this transit request.")
    for server in registry.get("servers", []) if isinstance(registry, dict) else []:
        if server.get("name") != "muni" or not server.get("enabled", True):
            continue
        tools_list = server.get("tools")
        if isinstance(tools_list, list) and any(item.get("name") == "muni_stops_nearby" for item in tools_list if isinstance(item, dict)):
            return {
                "action": "tool",
                "tool": "call_mcp_tool",
                "arguments": {
                    "server": "muni",
                    "tool": "muni_stops_nearby",
                    "arguments": {
                        "latitude": "{{user_latitude}}",
                        "longitude": "{{user_longitude}}",
                        "radius_meters": 1000,
                        "operator_id": "SF",
                    },
                },
            }
        raise RuntimeError("The Muni MCP server is enabled but its nearby-stops tool is unavailable.")
    raise RuntimeError("The Muni MCP server is not enabled for this transit request.")

def _current_local_time() -> str:
    """Return the current time in the host's local time zone."""
    return datetime.now().astimezone().isoformat(timespec="minutes")


def _user_location_capability(agent: Agent) -> str:
    """Describe location capability without exposing coordinates to the model."""
    if agent.settings.home_latitude is not None and agent.settings.home_longitude is not None:
        return (
            "User location is configured. For location-aware tools, the server will inject the saved "
            "coordinates into the tool call. Use {{user_latitude}} and {{user_longitude}} as the coordinate "
            "placeholders when a tool schema requires them. Do not ask the user for coordinates, claim they "
            "are missing, search documents for coordinates, or include coordinate values in your response."
        )
    return "User location is not configured."


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
    profile_name: str | None = None,
) -> dict:
    mcp_registry = tools.get_mcp_registry()
    # Force the location-aware MCP call only for the first action in a turn.
    # After it returns, let the planner synthesize from that result instead of
    # issuing the same controller action again.
    if not tool_results:
        forced_location_action = _forced_location_mcp_action(question, agent, mcp_registry)
        if forced_location_action is not None:
            _append_debug(debug, "controller_decision", phase="planning", decision="forced_location_mcp", parsed=forced_location_action)
            return forced_location_action
    prompt = agent.render_prompt(
        "planner.md",
        {
            "remaining_steps": remaining_steps,
            "decision_feedback": decision_feedback or "(none)",
            "memory_path": memory_state.get("path") or "not configured",
            "memory": memory.prompt_content(memory_state),
            "tool_descriptions": tools.render_tool_descriptions(),
            "planner_action_schema": protocol.planner_action_schema(),
            "conversation": conversation or "(none)",
            "question": question,
            "profile_name": profile_name or memory.profile_name(memory_state) or "(not provided)",
            "current_local_time": _current_local_time(),
            "user_location_capability": _user_location_capability(agent),
            "tool_results": json.dumps(_compact_tool_results(tool_results), indent=2),
            "mcp_registry": json.dumps(mcp_registry, indent=2),
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
        if parsed.get("action") == "tool" or parsed.get("action") in tools.AGENT_TOOL_FUNCTIONS:
            step = protocol.sanitize_planner_action(parsed)
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
            raise RuntimeError("Planner returned an invalid action; please try again.") from exc

    if not tool_results:
        _append_debug(
            debug,
            "controller_decision",
            phase="planning",
            decision="error",
            reason="planner_output_unusable_without_tool_results",
        )
        raise RuntimeError(
            "Planner returned no usable action; no tool was executed. "
            "The request could not be safely routed."
        )
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


def _needs_profile_name(question: str) -> bool:
    """Recognize questions where first-person identity affects document retrieval."""
    normalized = " ".join(question.casefold().split())
    return bool(
        re.search(r"\bmy\b|\bmine\b", normalized)
        or any(phrase in normalized for phrase in ("do i own", "do i have", "i own", "i have", "am i"))
    )


def _identity_question_from_history(history: list[dict]) -> str | None:
    """Return the retrieval question immediately before the identity onboarding prompt."""
    if not history:
        return None
    previous = history[-1]
    if previous.get("role") != "assistant" or previous.get("content") != IDENTITY_ONBOARDING_QUESTION:
        return None
    for message in reversed(history[:-1]):
        if message.get("role") == "user":
            return str(message.get("content") or "").strip() or None
    return None


def _identity_onboarding_response(detail: str | None = None) -> dict:
    """Return a deterministic identity question before an ownership-related search."""
    return {
        "answer": detail or IDENTITY_ONBOARDING_QUESTION,
        "plan": [],
        "tool_results": [],
        "reasoning": [],
        "debug": [
            {
                "event": "controller_decision",
                "phase": "identity",
                "decision": "request_profile_name",
            }
        ],
        "citations": [],
    }


def _inject_user_location_placeholders(arguments: dict, agent: Agent) -> dict:
    """Replace private location placeholders immediately before an MCP call."""
    latitude = agent.settings.home_latitude
    longitude = agent.settings.home_longitude

    def replace(value):
        if value == "{{user_latitude}}":
            return latitude
        if value == "{{user_longitude}}":
            return longitude
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return value

    return replace(arguments)


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
        elif tool == "hybrid_search":
            result = tools.hybrid_search(
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
        elif tool == "get_ocean_schedule":
            result = tools.get_ocean_schedule(day=arguments.get("day"))
        elif tool == "get_noe_schedule":
            result = tools.get_noe_schedule(day=arguments.get("day"))
        elif tool == "get_upcoming_yoga_classes":
            result = tools.get_upcoming_yoga_classes()
        elif tool == "get_school_lunch":
            result = tools.get_school_lunch(day=arguments.get("day"))
        elif tool == "get_city_sports_schedule":
            result = tools.get_city_sports_schedule(day=arguments.get("day"))
        elif tool == "get_weather":
            result = tools.get_weather(day=arguments.get("day"))
        elif tool == "get_mcp_registry":
            result = tools.get_mcp_registry()
        elif tool == "call_mcp_tool":
            mcp_arguments = _inject_user_location_placeholders(
                arguments.get("arguments") if isinstance(arguments.get("arguments"), dict) else {}, agent
            )
            result = tools.call_mcp_tool(
                server=str(arguments.get("server") or ""),
                tool=str(arguments.get("tool") or ""),
                arguments=mcp_arguments,
            )
        elif tool == "remember":
            if agent is None:
                result = {"remembered": False, "error": "Agent is required to write memory."}
            elif not agent.settings.memory_enabled:
                result = {"remembered": False, "error": "Memory is disabled in Settings."}
            elif agent.settings.memory_require_approval and not memory.write_is_allowed(question, history or []):
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


def _emit_progress(callback: ProgressCallback | None, event: str, **fields: Any) -> None:
    """Report a concise, user-facing lifecycle event to an optional listener."""
    if callback is None:
        return
    callback({"type": event, **fields})


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
