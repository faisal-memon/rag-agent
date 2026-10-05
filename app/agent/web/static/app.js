const button = document.getElementById("ask");
const agentButton = document.getElementById("agent");
const recordButton = document.getElementById("record");
const debugButton = document.getElementById("debug");
const pipelineButton = document.getElementById("pipeline");
const question = document.getElementById("question");
const status = document.getElementById("status");
const result = document.getElementById("result");
const agentChatToolbar = document.getElementById("agent-chat-toolbar");
const agentChat = document.getElementById("agent-chat");
const clearChatButton = document.getElementById("clear-chat");
const conversationSidebar = document.getElementById("conversation-sidebar");
const conversationList = document.getElementById("conversation-list");
const newConversationButton = document.getElementById("new-conversation");
const toggleConversations = document.getElementById("toggle-conversations");
const showConversations = document.getElementById("show-conversations");
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
const agentConversationIdKey = "nextcloud-rag-agent-conversation-id-v1";
const maxSavedAgentMessages = 20;
let agentConversation = loadAgentConversation();
let agentConversationId = localStorage.getItem(agentConversationIdKey) || null;
let mediaRecorder = null;
let recordedChunks = [];

async function loadConversations() {
  if (!conversationList) return;
  try {
    const response = await fetch("/agent/conversations");
    if (!response.ok) {
      conversationList.innerHTML = response.status === 401
        ? '<div class="conversation-empty">Sign in to load conversations.</div>'
        : '<div class="conversation-empty">Unable to load conversations.</div>';
      return;
    }
    const items = await response.json();
    conversationList.innerHTML = items.map(item => `<button class="conversation-item ${item.id === agentConversationId ? "active" : ""}" data-conversation-id="${escapeHtml(item.id)}"><span>${escapeHtml(item.title)}</span><small>${escapeHtml(new Date(item.updated_at).toLocaleString())}</small></button>`).join("");
    conversationList.querySelectorAll("[data-conversation-id]").forEach(button => button.addEventListener("click", () => loadConversation(button.dataset.conversationId)));
  } catch (error) { console.warn("Unable to load conversations", error); }
}

async function loadConversation(conversationId) {
  try {
    const response = await fetch(`/agent/conversations/${encodeURIComponent(conversationId)}`);
    if (!response.ok) throw new Error(`conversation load failed (${response.status})`);
    agentConversationId = conversationId;
    agentConversation = await response.json();
    localStorage.setItem(agentConversationIdKey, agentConversationId);
    localStorage.setItem(agentHistoryKey, JSON.stringify(agentConversation));
    renderAgentConversation();
    await loadConversations();
  } catch (error) {
    console.warn("Unable to load conversation", error);
    status.textContent = "Unable to load that conversation.";
    status.classList.add("error");
  }
}

function setConversationSidebar(hidden) {
  conversationSidebar?.classList.toggle("hidden", hidden);
  localStorage.setItem("conversation-sidebar-hidden", hidden ? "1" : "0");
}

newConversationButton?.addEventListener("click", () => {
  agentConversationId = null;
  agentConversation = [];
  localStorage.removeItem(agentConversationIdKey);
  localStorage.setItem(agentHistoryKey, "[]");
  renderAgentConversation();
  loadConversations();
});
toggleConversations?.addEventListener("click", () => setConversationSidebar(true));
showConversations?.addEventListener("click", () => setConversationSidebar(false));
setConversationSidebar(localStorage.getItem("conversation-sidebar-hidden") === "1");
loadConversations();

function showRecordingError(message) {
  recordButton.title = message;
  status.classList.add("error");
  status.textContent = message;
}

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

function progressToolTitle(tool, argumentsValue) {
  const day = argumentsValue?.day;
  const labels = {
    get_school_lunch: "Checking school lunch",
    get_ocean_schedule: "Checking Yoga Flow Ocean classes",
    get_noe_schedule: "Checking Yoga Flow Noe classes",
    get_upcoming_yoga_classes: "Checking upcoming Yoga Flow classes",
    search_documents: "Searching your documents",
    keyword_search: "Searching your documents",
    semantic_search: "Searching your documents",
    grep_documents: "Looking through matching documents",
    read_document: "Reading a document",
    remember: "Saving a memory",
  };
  const title = labels[tool] || `Running ${String(tool || "tool").replaceAll("_", " ")}`;
  return day ? `${title} for ${day}` : title;
}

function progressResultTitle(event) {
  const summary = event.result || {};
  if (summary.type === "error") return "Tool could not complete";
  if (summary.type === "list") return `${summary.count} result${summary.count === 1 ? "" : "s"} found`;
  return "Tool completed";
}

function agentProgressTimelineHtml(events) {
  const rows = (events || []).map((event) => {
    const type = event.type || "thinking";
    const title = type === "thinking"
      ? event.message || "Thinking"
      : type === "tool_call"
        ? progressToolTitle(event.tool, event.arguments)
        : type === "tool_result"
          ? progressResultTitle(event)
          : event.message || "Agent update";
    const icon = type === "thinking" ? "…" : type === "tool_call" ? "↗" : type === "tool_result" ? "✓" : "!";
    return `<div class="agent-progress-event agent-progress-${escapeHtml(type)}">
      <span class="agent-progress-icon" aria-hidden="true">${icon}</span>
      <span>${escapeHtml(title)}</span>
    </div>`;
  }).join("");
  return rows ? `<div class="agent-progress" aria-live="polite">${rows}</div>` : "";
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
      <article class="chat-message assistant${message.streaming ? " streaming" : ""}">
        <div class="chat-role">rag agent</div>
        ${agentProgressTimelineHtml(message.progress)}
        ${message.streaming ? "" : `<div class="chat-content">${escapeHtml(message.content)}</div>`}
        ${message.streaming ? "" : agentTraceTimelineHtml(message)}
        ${sourceFold}
      </article>
    `;
  }).join("");

  const hasVisibleConversation = agentConversation.length > 0;
  agentChat.innerHTML = messagesHtml;
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

async function readAgentProgress(response, onEvent) {
  if (!response.body) {
    throw new Error("This browser does not support streamed agent updates.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const consume = (chunk) => {
    buffer += chunk;
    const records = buffer.split(/\r?\n\r?\n/);
    buffer = records.pop() || "";
    records.forEach((record) => {
      const data = record
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice(6))
        .join("\n");
      if (data) onEvent(JSON.parse(data));
    });
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    consume(decoder.decode(value, { stream: true }));
  }
  consume(decoder.decode());
  if (buffer.trim()) {
    const data = buffer.split(/\r?\n/).find((line) => line.startsWith("data: "))?.slice(6);
    if (data) onEvent(JSON.parse(data));
  }
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

  agentConversation.push({
    role: "user",
    content: value,
    createdAt: new Date().toISOString(),
  });
  const progressMessage = {
    role: "assistant",
    content: "",
    progress: [{ type: "thinking", message: "Thinking" }],
    streaming: true,
    createdAt: new Date().toISOString(),
  };
  agentConversation.push(progressMessage);
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
    const response = await fetch("/agent/query/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: agentConversationId, question: value }),
    });

    if (!response.ok) {
      throw new Error(`Agent query failed with HTTP ${response.status}`);
    }

    await readAgentProgress(response, (event) => {
      if (event.type === "complete") {
        const data = event.result || {};
        if (data.conversation_id) {
          agentConversationId = data.conversation_id;
          localStorage.setItem(agentConversationIdKey, agentConversationId);
        }
        Object.assign(progressMessage, {
          content: data.answer || "No answer returned.",
          plan: data.plan || [],
          toolResults: data.tool_results || [],
          reasoning: data.reasoning || [],
          debug: data.debug || [],
          citations: data.citations || [],
          streaming: false,
        });
        status.textContent = `Agent completed ${Number((data.tool_results || []).length)} tool call(s).`;
      } else if (event.type === "error") {
        throw new Error(event.message || "Agent request failed.");
      } else {
        progressMessage.progress.push(event);
        status.textContent = event.type === "tool_call" ? progressToolTitle(event.tool, event.arguments) : "Agent is working...";
      }
      renderAgentConversation();
    });
    saveAgentConversation();
    renderAgentConversation();
  } catch (error) {
    Object.assign(progressMessage, {
      content: `Agent request failed: ${error.message}`,
      streaming: false,
    });
    saveAgentConversation();
    renderAgentConversation();
    status.textContent = "Agent query failed.";
  } finally {
    result.classList.add("visible");
    button.disabled = false;
    agentButton.disabled = false;
    debugButton.disabled = false;
    pipelineButton.disabled = false;
  }
}

async function toggleRecording() {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    recordButton.disabled = true;
    status.classList.remove("error");
    status.textContent = "Transcribing recording...";
    return;
  }

  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    status.textContent = "This browser does not support microphone recording.";
    return;
  }

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) recordedChunks.push(event.data);
    });
    mediaRecorder.addEventListener("stop", async () => {
      stream.getTracks().forEach((track) => track.stop());
      const audio = new Blob(recordedChunks, { type: mediaRecorder.mimeType || "audio/webm" });
      mediaRecorder = null;
      recordButton.classList.remove("recording");
      recordButton.textContent = "Record";
      try {
        const form = new FormData();
        form.append("audio", audio, "recording.webm");
        const response = await fetch("/agent/transcribe", { method: "POST", body: form });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || "Transcription failed.");
        }
        question.value = data.text;
        question.focus();
        status.classList.remove("error");
        status.textContent = "Transcript is ready to review.";
      } catch (error) {
        showRecordingError(error.message || "Transcription failed.");
      } finally {
        recordButton.disabled = false;
      }
    });
    mediaRecorder.start();
    recordButton.classList.add("recording");
    recordButton.textContent = "Stop recording";
    status.classList.remove("error");
    status.textContent = "Recording...";
  } catch (_error) {
    showRecordingError("Microphone access was not granted.");
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
recordButton.addEventListener("click", toggleRecording);

if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
  showRecordingError("Microphone recording is not supported by this browser.");
}
debugButton.addEventListener("click", () => runQuery(0));
pipelineButton.addEventListener("click", loadPipelineStatus);
clearChatButton.addEventListener("click", () => {
  agentConversation = [];
  localStorage.removeItem(agentHistoryKey);
  localStorage.removeItem(agentConversationIdKey);
  agentConversationId = null;
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
