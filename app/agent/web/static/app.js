const button = document.getElementById("ask");
const agentButton = document.getElementById("agent");
const debugButton = document.getElementById("debug");
const pipelineButton = document.getElementById("pipeline");
const question = document.getElementById("question");
const status = document.getElementById("status");
const result = document.getElementById("result");
const agentChatToolbar = document.getElementById("agent-chat-toolbar");
const agentChat = document.getElementById("agent-chat");
const clearChatButton = document.getElementById("clear-chat");
const pipelineSummary = document.getElementById("pipeline-summary");
const answer = document.getElementById("answer");
const citations = document.getElementById("citations");
const pager = document.getElementById("pager");
const previousButton = document.getElementById("previous");
const nextButton = document.getElementById("next");
const pageStatus = document.getElementById("page-status");
let debugOffset = 0;
let debugLimit = 8;
let lastDebugQuestion = "";
let lastDebugMode = "semantic";
const agentHistoryKey = "nextcloud-rag-agent-history-v1";
const maxSavedAgentMessages = 20;
let agentConversation = loadAgentConversation();
let agentIsThinking = false;

function citationPreview(text) {
  if (!text) return "";
  return text.length > 520 ? text.slice(0, 520) + "..." : text;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function citationHtml(items) {
  return (items || []).map((citation) => `
    <article class="citation">
      <div class="citation-title">${escapeHtml(citation.filename || "Unknown document")}</div>
      <div class="citation-meta">
        score=${Number(citation.score || 0).toFixed(3)}
        | fts=${Number(citation.fts_score || 0).toFixed(3)}
        | vector=${Number(citation.vector_score || 0).toFixed(3)}
        | mode=${escapeHtml(citation.retrieval_mode || "semantic")}
        | page=${escapeHtml(citation.page || "n/a")}
        | section=${escapeHtml(citation.section || "n/a")}
        | matched=${citation.matched_fts ? "fts" : ""}${citation.matched_fts && citation.matched_vector ? "+" : ""}${citation.matched_vector ? "vector" : ""}
      </div>
      <div class="citation-meta">${escapeHtml(citation.path || "")}</div>
      <div class="citation-content">${escapeHtml(citationPreview(citation.content || ""))}</div>
    </article>
  `).join("");
}

function renderCitations(items) {
  citations.innerHTML = citationHtml(items);
}

function renderPipelineDocuments(items) {
  return (items || []).map((document) => `
    <article class="citation">
      <div class="citation-title">${escapeHtml(document.filename || "Unknown document")}</div>
      <div class="citation-meta">
        chunks=${Number(document.chunk_count || 0)}
        | indexed=${escapeHtml(document.last_indexed_at || "n/a")}
        | modified=${escapeHtml(document.modified_time || "n/a")}
        | missing=${escapeHtml(document.missing_since || "no")}
      </div>
      <div class="citation-meta">${escapeHtml(document.path || "")}</div>
    </article>
  `).join("");
}

function renderPipelineStatus(data) {
  const strategies = (data.indexing_strategies || []).map((strategy) => `
    <article class="citation">
      <div class="citation-title">${escapeHtml(strategy.indexing_version)}</div>
      <div class="citation-meta">
        docs=${Number(strategy.document_count || 0)}
        | model=${escapeHtml(strategy.embedding_model)}
        | tokenizer=${escapeHtml(strategy.embedding_tokenizer)}
        | chunks=${Number(strategy.chunk_size || 0)}/${Number(strategy.chunk_overlap || 0)}
      </div>
    </article>
  `).join("");

  pipelineSummary.innerHTML = `
    <div class="pipeline-grid">
      <div class="metric">
        <div class="metric-value">${Number(data.document_count || 0)}</div>
        <div class="metric-label">documents</div>
      </div>
      <div class="metric">
        <div class="metric-value">${Number(data.chunk_count || 0)}</div>
        <div class="metric-label">chunks</div>
      </div>
      <div class="metric">
        <div class="metric-value">${Number(data.missing_document_count || 0)}</div>
        <div class="metric-label">missing documents</div>
      </div>
    </div>
  `;

  answer.textContent = "Pipeline status. Recent indexed documents, missing documents, and indexing strategies.";
  citations.innerHTML = `
    <article class="citation">
      <div class="citation-title">recent documents</div>
    </article>
    ${renderPipelineDocuments(data.recent_documents)}
    <article class="citation">
      <div class="citation-title">missing documents</div>
    </article>
    ${renderPipelineDocuments(data.missing_documents)}
    <article class="citation">
      <div class="citation-title">indexing strategies</div>
    </article>
    ${strategies}
  `;
}

function summarizeToolResult(result) {
  const hasError = Boolean(result && !Array.isArray(result) && result.error);
  if (Array.isArray(result)) {
    return `${result.length} result${result.length === 1 ? "" : "s"}`;
  }
  if (hasError) {
    return "error";
  }
  if (result && typeof result.content === "string") {
    return `${result.content.length} chars${result.truncated ? " (truncated)" : ""}`;
  }
  if (result && typeof result === "object" && "remembered" in result) {
    return result.remembered ? "remembered" : "not remembered";
  }
  return "complete";
}

function summarizeArguments(argumentsValue) {
  return Object.entries(argumentsValue || {})
    .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
    .join(" ");
}

function buildAgentTraceEvents(message) {
  const reasoning = Array.isArray(message.reasoning) ? [...message.reasoning] : [];
  const debugEvents = Array.isArray(message.debug) ? message.debug : [];
  const toolResults = Array.isArray(message.toolResults) ? message.toolResults : [];
  const traceEvents = [];
  let toolResultIndex = 0;

  const nextReasoningForPhase = (phase) => {
    const index = reasoning.findIndex((step) => (step.phase || "model") === (phase || "model"));
    return index >= 0 ? reasoning.splice(index, 1)[0] : null;
  };

  debugEvents.forEach((event) => {
    if (event.event === "model_response") {
      const reasoningStep = nextReasoningForPhase(event.phase);
      if (reasoningStep) {
        traceEvents.push({
          type: "thinking",
          title: `${reasoningStep.phase || "model"} thinking`,
          detail: reasoningStep.content || "",
        });
      }
      traceEvents.push({
        type: "model",
        title: `${event.phase || "model"} model output`,
        detail: event.raw_text || "",
      });
      return;
    }

    if (event.event === "parsed_action") {
      traceEvents.push({
        type: "parser",
        title: "parsed action",
        detail: JSON.stringify(event.parsed || {}, null, 2),
      });
      return;
    }

    if (event.event === "controller_decision" && event.decision === "execute_tool") {
      traceEvents.push({
        type: "tool-call",
        title: `tool call · ${event.tool || "unknown"}`,
        meta: summarizeArguments(event.arguments || {}),
        detail: JSON.stringify(event.arguments || {}, null, 2),
      });
      return;
    }

    if (event.event === "tool_result") {
      const completed = toolResults[toolResultIndex] || {};
      toolResultIndex += 1;
      const result = completed.result ?? null;
      traceEvents.push({
        type: result && result.error ? "tool-error" : "tool-result",
        title: `tool result · ${event.tool || completed.tool || "unknown"}`,
        meta: summarizeToolResult(result),
        detail: JSON.stringify(result, null, 2),
      });
      return;
    }

    traceEvents.push({
      type: event.event === "parse_error" ? "warning" : "controller",
      title: [event.event || "event", event.decision, event.tool].filter(Boolean).join(" · "),
      meta: event.reason || "",
      detail: JSON.stringify(event, null, 2),
    });
  });

  reasoning.forEach((step) => {
    traceEvents.push({
      type: "thinking",
      title: `${step.phase || "model"} thinking`,
      detail: step.content || "",
    });
  });

  if (!traceEvents.length && toolResults.length) {
    toolResults.forEach((item) => {
      traceEvents.push({
        type: item.result && item.result.error ? "tool-error" : "tool-result",
        title: `tool result · ${item.tool || "unknown"}`,
        meta: summarizeToolResult(item.result),
        detail: JSON.stringify(item.result ?? null, null, 2),
      });
    });
  }

  return traceEvents;
}

function agentTraceTimelineHtml(message) {
  const events = buildAgentTraceEvents(message);
  const toolCount = (message.toolResults || []).length;
  const rows = events.map((event, index) => `
    <details class="trace-event trace-${escapeHtml(event.type)}">
      <summary>
        <span class="trace-index">${index + 1}</span>
        <span class="trace-kind">${escapeHtml(event.type.replaceAll("-", " "))}</span>
        <span class="trace-title">${escapeHtml(event.title || "event")}</span>
        <span class="trace-meta">${escapeHtml(event.meta || "")}</span>
      </summary>
      <pre>${escapeHtml(event.detail || "")}</pre>
    </details>
  `).join("");

  return `
    <details class="agent-trace">
      <summary>
        <span>agent trace</span>
        <span>${events.length} event${events.length === 1 ? "" : "s"} · ${toolCount} tool call${toolCount === 1 ? "" : "s"}</span>
      </summary>
      ${rows || '<div class="trace-empty">No trace events were recorded.</div>'}
    </details>
  `;
}

function loadAgentConversation() {
  try {
    const saved = JSON.parse(localStorage.getItem(agentHistoryKey) || "[]");
    return Array.isArray(saved) ? saved.slice(-maxSavedAgentMessages) : [];
  } catch (_error) {
    return [];
  }
}

function saveAgentConversation() {
  agentConversation = agentConversation.slice(-maxSavedAgentMessages);
  try {
    localStorage.setItem(agentHistoryKey, JSON.stringify(agentConversation));
  } catch (_error) {
    status.textContent = "Conversation works, but browser storage is full.";
  }
}

function agentHistoryForRequest() {
  return agentConversation.slice(-12).map((message) => ({
    role: message.role,
    content: message.content,
  }));
}

function renderAgentConversation() {
  const messagesHtml = agentConversation.map((message) => {
    if (message.role === "user") {
      return `
        <article class="chat-message user">
          <div class="chat-role">you</div>
          <div class="chat-content">${escapeHtml(message.content)}</div>
        </article>
      `;
    }

    const sources = message.citations || [];
    const sourceFold = sources.length
      ? `
        <details class="source-fold">
          <summary>Sources (${sources.length})</summary>
          <div class="source-list">${citationHtml(sources)}</div>
        </details>
      `
      : "";
    return `
      <article class="chat-message assistant">
        <div class="chat-role">rag agent</div>
        ${agentTraceTimelineHtml(message)}
        <div class="chat-content">${escapeHtml(message.content)}</div>
        ${sourceFold}
      </article>
    `;
  }).join("");

  const thinkingHtml = agentIsThinking
    ? `
      <article class="chat-message assistant thinking-message" role="status" aria-label="RAG agent is thinking">
        <div class="chat-role">rag agent</div>
        <div class="thinking-indicator">
          <span>thinking</span>
          <span class="thinking-dots" aria-hidden="true">
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
            <span class="thinking-dot"></span>
          </span>
        </div>
      </article>
    `
    : "";
  const hasVisibleConversation = agentConversation.length > 0 || agentIsThinking;
  agentChat.innerHTML = messagesHtml + thinkingHtml;
  agentChat.classList.toggle("visible", hasVisibleConversation);
  agentChatToolbar.classList.toggle("visible", hasVisibleConversation);
  if (hasVisibleConversation) {
    result.classList.add("visible");
    agentChat.lastElementChild?.scrollIntoView({ behavior: "smooth", block: "end" });
  }
}

function hideAgentConversation() {
  agentChat.classList.remove("visible");
  agentChatToolbar.classList.remove("visible");
}

async function runQuery(requestedOffset = 0) {
  const value = question.value.trim();
  if (!value) {
    status.textContent = "Type a question first.";
    return;
  }

  button.disabled = true;
  agentButton.disabled = true;
  debugButton.disabled = true;
  pipelineButton.disabled = true;
  const retrievalMode = document.querySelector('input[name="retrieval-mode"]:checked').value;
  const offset = requestedOffset;
  status.textContent = "Retrieving chunks...";
  result.classList.remove("visible");
  answer.classList.remove("error");
  pipelineSummary.innerHTML = "";
  hideAgentConversation();
  pager.classList.remove("visible");
  citations.innerHTML = "";

  try {
    const response = await fetch("/debug/retrieve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: value, mode: retrievalMode, limit: debugLimit, offset }),
    });

    if (!response.ok) {
      throw new Error(`Retrieval failed with HTTP ${response.status}`);
    }

    const data = await response.json();
    const items = data.chunks || [];
    answer.textContent = "Retrieval debug results. No answer was generated.";
    renderCitations(items);
    debugOffset = data.offset || 0;
    debugLimit = data.limit || debugLimit;
    lastDebugQuestion = value;
    lastDebugMode = retrievalMode;
    const totalChunks = data.total_chunks || 0;
    const totalDocuments = data.total_documents || 0;
    const start = totalChunks === 0 ? 0 : debugOffset + 1;
    const end = Math.min(debugOffset + items.length, totalChunks);
    pager.classList.add("visible");
    previousButton.disabled = debugOffset <= 0;
    nextButton.disabled = debugOffset + debugLimit >= totalChunks;
    pageStatus.textContent = `${start}-${end} of ${totalChunks} chunks across ${totalDocuments} documents`;
    status.textContent = `Found ${totalChunks} matching chunk(s) across ${totalDocuments} document(s) with ${retrievalMode} mode.`;
  } catch (error) {
    answer.textContent = error.message;
    answer.classList.add("error");
    status.textContent = "Something went wrong.";
  } finally {
    result.classList.add("visible");
    button.disabled = false;
    agentButton.disabled = false;
    debugButton.disabled = false;
    pipelineButton.disabled = false;
  }
}

async function runAgent() {
  const value = question.value.trim();
  if (!value) {
    status.textContent = "Type a question first.";
    return;
  }

  const requestHistory = agentHistoryForRequest();
  agentConversation.push({
    role: "user",
    content: value,
    createdAt: new Date().toISOString(),
  });
  agentIsThinking = true;
  saveAgentConversation();
  renderAgentConversation();
  question.value = "";

  button.disabled = true;
  agentButton.disabled = true;
  debugButton.disabled = true;
  pipelineButton.disabled = true;
  status.textContent = "Agent is planning and calling tools...";
  result.classList.add("visible");
  answer.classList.remove("error");
  answer.textContent = "";
  pipelineSummary.innerHTML = "";
  pager.classList.remove("visible");
  citations.innerHTML = "";

  try {
    const response = await fetch("/agent/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: value, history: requestHistory }),
    });

    if (!response.ok) {
      throw new Error(`Agent query failed with HTTP ${response.status}`);
    }

    const data = await response.json();
    agentIsThinking = false;
    agentConversation.push({
      role: "assistant",
      content: data.answer || "No answer returned.",
      plan: data.plan || [],
      toolResults: data.tool_results || [],
      reasoning: data.reasoning || [],
      debug: data.debug || [],
      citations: data.citations || [],
      createdAt: new Date().toISOString(),
    });
    saveAgentConversation();
    renderAgentConversation();
    status.textContent = `Agent completed ${Number((data.tool_results || []).length)} tool call(s).`;
  } catch (error) {
    agentIsThinking = false;
    agentConversation.push({
      role: "assistant",
      content: `Agent request failed: ${error.message}`,
      plan: [],
      toolResults: [],
      reasoning: [],
      debug: [],
      citations: [],
      createdAt: new Date().toISOString(),
    });
    saveAgentConversation();
    renderAgentConversation();
    status.textContent = "Agent query failed.";
  } finally {
    agentIsThinking = false;
    result.classList.add("visible");
    button.disabled = false;
    agentButton.disabled = false;
    debugButton.disabled = false;
    pipelineButton.disabled = false;
  }
}

async function loadPipelineStatus() {
  button.disabled = true;
  agentButton.disabled = true;
  debugButton.disabled = true;
  pipelineButton.disabled = true;
  status.textContent = "Reading pipeline status...";
  result.classList.remove("visible");
  answer.classList.remove("error");
  pager.classList.remove("visible");
  pipelineSummary.innerHTML = "";
  hideAgentConversation();
  citations.innerHTML = "";

  try {
    const response = await fetch("/debug/pipeline?limit=10");
    if (!response.ok) {
      throw new Error(`Pipeline status failed with HTTP ${response.status}`);
    }

    const data = await response.json();
    renderPipelineStatus(data);
    status.textContent = `Indexed ${Number(data.document_count || 0)} document(s), ${Number(data.chunk_count || 0)} chunk(s), ${Number(data.missing_document_count || 0)} missing.`;
  } catch (error) {
    answer.textContent = error.message;
    answer.classList.add("error");
    status.textContent = "Something went wrong.";
  } finally {
    result.classList.add("visible");
    button.disabled = false;
    agentButton.disabled = false;
    debugButton.disabled = false;
    pipelineButton.disabled = false;
  }
}

button.addEventListener("click", () => runQuery());
agentButton.addEventListener("click", runAgent);
debugButton.addEventListener("click", () => runQuery(0));
pipelineButton.addEventListener("click", loadPipelineStatus);
clearChatButton.addEventListener("click", () => {
  agentConversation = [];
  localStorage.removeItem(agentHistoryKey);
  renderAgentConversation();
  result.classList.remove("visible");
  status.textContent = "Agent conversation cleared.";
});
previousButton.addEventListener("click", () => runQuery(Math.max(debugOffset - debugLimit, 0)));
nextButton.addEventListener("click", () => runQuery(debugOffset + debugLimit));
question.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
    return;
  }

  event.preventDefault();
  if (document.body.classList.contains("agent-only")) {
    runAgent();
  } else {
    runQuery();
  }
});
renderAgentConversation();
