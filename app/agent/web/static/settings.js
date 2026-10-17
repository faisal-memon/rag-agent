const form = document.getElementById("settings-form");
const status = document.getElementById("settings-status");
const saveButton = document.getElementById("save-settings");

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
  });
}

function setStatus(message) {
  status.textContent = message;
}

function fillForm(settings) {
  for (const [name, value] of Object.entries(settings)) {
    const input = form.elements.namedItem(name);
    if (input) input.value = value;
  }
}

async function loadSettings() {
  const response = await fetch("/api/settings");
  if (!response.ok) throw new Error("Could not load settings");
  fillForm(await response.json());
  setStatus("Ready");
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  saveButton.disabled = true;
  setStatus("Saving…");
  const data = Object.fromEntries(new FormData(form));
  data.query_limit = Number(data.query_limit);
  data.agent_max_steps = Number(data.agent_max_steps);
  data.home_latitude = data.home_latitude === "" ? null : Number(data.home_latitude);
  data.home_longitude = data.home_longitude === "" ? null : Number(data.home_longitude);

  try {
    const response = await fetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!response.ok) throw new Error((await response.json()).detail || "Could not save settings");
    fillForm(await response.json());
    setStatus("Saved. New chats will use these settings.");
  } catch (error) {
    setStatus(error.message);
  } finally {
    saveButton.disabled = false;
  }
});

loadSettings().catch((error) => setStatus(error.message));
