const form = document.getElementById("settings-form");
const status = document.getElementById("settings-status");
const saveButton = document.getElementById("save-settings");
const systemPromptInput = document.getElementById("system-prompt");
const systemPromptStatus = document.getElementById("system-prompt-status");
const resetSystemPromptButton = document.getElementById("reset-system-prompt");
const remoteMCPServerList = document.getElementById("remote-mcp-servers");
const remoteMCPFormStatus = document.getElementById("remote-mcp-form-status");
const piperVoiceSelect = document.getElementById("piper-voice");
const piperVoiceStatus = document.getElementById("piper-voice-status");
let systemPromptIsDefault = true;
let defaultSystemPrompt = "";

function updateSystemPromptStatus() {
  if (!systemPromptInput) return;
  systemPromptStatus.textContent = systemPromptIsDefault
    ? "Using the built-in default prompt."
    : "Using a custom prompt. Saving applies it to new chats.";
  resetSystemPromptButton.disabled = systemPromptIsDefault;
}

function renderRemoteMCPServers(servers = []) {
  remoteMCPServerList.replaceChildren();
  for (const server of servers) addRemoteMCPServer(server.name, server.url);
}

function renderPiperVoices(voices = []) {
  if (!piperVoiceSelect || !voices.length) return;
  const selected = piperVoiceSelect.value;
  piperVoiceSelect.replaceChildren(...voices.map((voice) => {
    const option = document.createElement("option");
    option.value = voice;
    option.textContent = voice;
    return option;
  }));
  piperVoiceSelect.value = voices.includes(selected) ? selected : voices[0];
  piperVoiceStatus.textContent = `${voices.length} Piper voice${voices.length === 1 ? "" : "s"} installed.`;
}

async function loadPiperVoices() {
  try {
    const response = await fetch("/api/piper/voices");
    if (!response.ok) return;
    const data = await response.json();
    renderPiperVoices(data.voices || []);
  } catch (_error) {
    // Keep the built-in choices when Piper is temporarily unavailable.
  }
}

function addRemoteMCPServer(name = "", url = "") {
  const row = document.createElement("div");
  row.className = "remote-mcp-server-row";
  const nameLabel = document.createElement("label");
  nameLabel.textContent = "Name";
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.name = "remote_mcp_server_name";
  nameInput.required = true;
  nameInput.maxLength = 40;
  nameInput.pattern = "[A-Za-z0-9][A-Za-z0-9 _-]*";
  nameInput.autocomplete = "off";
  nameInput.placeholder = "wikipedia";
  nameInput.value = name;
  nameLabel.append(nameInput);

  const urlLabel = document.createElement("label");
  urlLabel.textContent = "HTTPS MCP URL";
  const urlInput = document.createElement("input");
  urlInput.type = "url";
  urlInput.name = "remote_mcp_server_url";
  urlInput.required = true;
  urlInput.placeholder = "https://example.org/mcp";
  urlInput.value = url;
  urlLabel.append(urlInput);

  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "secondary";
  removeButton.textContent = "Remove";
  removeButton.addEventListener("click", () => row.remove());
  row.append(nameLabel, urlLabel, removeButton);
  remoteMCPServerList.append(row);
}

document.getElementById("add-remote-mcp-server").addEventListener("click", () => addRemoteMCPServer());
document.getElementById("add-wikimedia-mcp-server").addEventListener("click", () => {
  const existing = [...remoteMCPServerList.querySelectorAll('[name="remote_mcp_server_name"]')]
    .some((input) => input.value.trim().toLowerCase() === "wikimedia");
  if (!existing) {
    addRemoteMCPServer("wikimedia", "https://enterprise.wikimedia.com/mcp");
    remoteMCPFormStatus.textContent = "Wikimedia was added to this form. Click Save settings to connect it.";
  } else {
    remoteMCPFormStatus.textContent = "Wikimedia is already in this form. Click Save settings to connect it.";
  }
});

for (const tab of document.querySelectorAll(".settings-tab")) {
  tab.addEventListener("click", () => {
    for (const candidate of document.querySelectorAll(".settings-tab")) {
      const active = candidate === tab;
      candidate.classList.toggle("active", active);
      candidate.setAttribute("aria-selected", String(active));
    }
    for (const panel of document.querySelectorAll(".settings-panel")) {
      panel.hidden = panel.id !== tab.dataset.panel;
    }
    document.getElementById(tab.dataset.panel)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function setStatus(message) {
  status.textContent = message;
}

async function responseErrorMessage(response, fallback) {
  try {
    const payload = await response.json();
    const detail = payload?.detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => {
        if (typeof item === "string") return item;
        const location = Array.isArray(item?.loc) ? item.loc.join(".") : "Settings";
        return `${location}: ${item?.msg || "Invalid value"}`;
      }).join("; ");
    }
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object") return detail.message || JSON.stringify(detail);
  } catch (error) {
    console.warn("Unable to read settings error", error);
  }
  return fallback;
}

function fillForm(settings) {
  for (const [name, value] of Object.entries(settings)) {
    const input = form.elements.namedItem(name);
    if (input) {
      if (input.type === "checkbox") input.checked = Boolean(value);
      else input.value = value;
    }
  }
  defaultSystemPrompt = settings.default_system_prompt || "";
  systemPromptIsDefault = Boolean(settings.system_prompt_is_default);
  updateSystemPromptStatus();
  renderRemoteMCPServers(settings.remote_mcp_servers || []);
}

systemPromptInput?.addEventListener("input", () => {
  systemPromptIsDefault = false;
  updateSystemPromptStatus();
});

resetSystemPromptButton?.addEventListener("click", () => {
  systemPromptInput.value = defaultSystemPrompt;
  systemPromptIsDefault = true;
  updateSystemPromptStatus();
  setStatus("Default system prompt restored. Click Save settings to apply it.");
});

async function loadSettings() {
  const response = await fetch("/api/settings");
  if (!response.ok) throw new Error("Could not load settings");
  fillForm(await response.json());
  await loadPiperVoices();
  setStatus("Ready");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  saveButton.disabled = true;
  setStatus("Saving…");
  const data = Object.fromEntries(new FormData(form));
  data.system_prompt_is_default = systemPromptIsDefault;
  data.query_limit = Number(data.query_limit);
  data.agent_max_steps = Number(data.agent_max_steps);
  data.voice_start_grace_ms = Number(data.voice_start_grace_ms);
  data.voice_end_silence_ms = Number(data.voice_end_silence_ms);
  data.voice_vad_window_frames = Number(data.voice_vad_window_frames);
  data.voice_vad_window_speech_frames = Number(data.voice_vad_window_speech_frames);
  data.voice_vad_activity_history = Number(data.voice_vad_activity_history);
  data.voice_vad_active_windows_required = Number(data.voice_vad_active_windows_required);
  data.voice_capture_limit_seconds = Number(data.voice_capture_limit_seconds);
  data.voice_follow_up_timeout_seconds = Number(data.voice_follow_up_timeout_seconds);
  data.voice_follow_up_start_timeout_seconds = Number(data.voice_follow_up_start_timeout_seconds);
  data.home_latitude = data.home_latitude === "" ? null : Number(data.home_latitude);
  data.home_longitude = data.home_longitude === "" ? null : Number(data.home_longitude);
  data.memory_enabled = form.elements.memory_enabled.checked;
  data.memory_require_approval = form.elements.memory_require_approval.checked;
  const remoteNames = [...form.querySelectorAll('[name="remote_mcp_server_name"]')];
  const remoteUrls = [...form.querySelectorAll('[name="remote_mcp_server_url"]')];
  data.remote_mcp_servers = remoteNames.map((input, index) => ({
    name: input.value.trim(),
    url: remoteUrls[index].value.trim(),
  }));

  try {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!response.ok) throw new Error(await responseErrorMessage(response, "Could not save settings"));
    fillForm(await response.json());
    setStatus("Saved. New chats will use these settings.");
  } catch (error) {
    setStatus(error.message);
  } finally {
    saveButton.disabled = false;
  }
});

loadSettings().catch((error) => setStatus(error.message));
