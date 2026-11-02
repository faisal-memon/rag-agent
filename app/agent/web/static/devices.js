const deviceList = document.getElementById("device-list");
const devicesStatus = document.getElementById("devices-status");
const refreshButton = document.getElementById("refresh-devices");

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function makeMeta(label, value) {
  const item = document.createElement("div");
  item.className = "device-meta-item";
  const name = document.createElement("span");
  name.className = "device-meta-label";
  name.textContent = label;
  const content = document.createElement("strong");
  content.textContent = value;
  item.append(name, content);
  return item;
}

async function showHistory(device, container, button) {
  if (!container.hidden) {
    container.hidden = true;
    button.textContent = "View history";
    return;
  }
  button.disabled = true;
  button.textContent = "Loading…";
  try {
    const response = await fetch(`/api/devices/${encodeURIComponent(device.device_id)}/history`);
    if (!response.ok) throw new Error("Could not load device history");
    const messages = await response.json();
    container.replaceChildren();
    if (!messages.length) {
      const empty = document.createElement("p");
      empty.className = "status";
      empty.textContent = "No voice conversations saved yet.";
      container.append(empty);
    }
    for (const message of messages) {
      const article = document.createElement("article");
      article.className = `device-message ${message.role}`;
      const heading = document.createElement("div");
      heading.className = "device-message-meta";
      heading.textContent = `${message.role === "user" ? "Speaker" : "Assistant"} · ${formatDate(message.created_at)}`;
      const content = document.createElement("p");
      content.textContent = message.content;
      article.append(heading, content);
      container.append(article);
    }
    container.hidden = false;
    button.textContent = "Hide history";
  } catch (error) {
    devicesStatus.textContent = error.message;
    button.textContent = "View history";
  } finally {
    button.disabled = false;
  }
}

async function loadActivity(device, container) {
  try {
    const response = await fetch(`/api/devices/${encodeURIComponent(device.device_id)}/activity?limit=30`);
    if (!response.ok) throw new Error("Could not load recent activity");
    const events = await response.json();
    container.replaceChildren();
    if (!events.length) {
      const empty = document.createElement("p");
      empty.className = "status";
      empty.textContent = "No voice activity recorded yet.";
      container.append(empty);
      return;
    }
    for (const event of events) {
      const item = document.createElement("article");
      item.className = `device-activity-event ${event.status}`;
      const heading = document.createElement("strong");
      heading.textContent = event.stage;
      const status = document.createElement("span");
      status.className = "device-activity-status";
      status.textContent = event.status === "completed" ? "Done" : event.status === "failed" ? "Failed" : event.status === "started" ? "Started" : "Info";
      const meta = document.createElement("p");
      const duration = Number.isFinite(event.duration_ms) ? ` · ${event.duration_ms < 1000 ? `${event.duration_ms} ms` : `${(event.duration_ms / 1000).toFixed(1)} sec`}` : "";
      meta.textContent = `${formatDate(event.created_at)}${duration}${event.detail ? ` · ${event.detail}` : ""}`;
      item.append(heading, status, meta);
      container.append(item);
    }
  } catch (error) {
    const message = document.createElement("p");
    message.className = "status";
    message.textContent = error.message;
    container.replaceChildren(message);
  }
}

function renderDevice(device) {
  const card = document.createElement("article");
  card.className = "query-card device-card";

  const header = document.createElement("div");
  header.className = "device-card-header";
  const title = document.createElement("div");
  const name = document.createElement("h2");
  name.textContent = device.display_name || "ESPHome speaker";
  const id = document.createElement("p");
  id.className = "device-id";
  id.textContent = `Device ID · ${device.device_id}`;
  title.append(name, id);

  const badge = document.createElement("span");
  badge.className = `device-status-badge ${device.connected ? "connected" : "disconnected"}`;
  badge.textContent = device.connected ? "Connected" : "Not connected";
  header.append(title, badge);

  const details = document.createElement("div");
  details.className = "device-meta";
  details.append(
    makeMeta("Address", device.host ? `${device.host}:${device.port}` : "Not configured"),
    makeMeta("Last connected", formatDate(device.last_connected_at)),
    makeMeta("Last interaction", formatDate(device.last_interaction_at)),
  );

  const toggle = document.createElement("button");
  toggle.className = "secondary";
  toggle.type = "button";
  const hasIdentity = device.device_id && device.device_id !== "not-yet-seen";
  toggle.textContent = hasIdentity ? "View history" : "No history yet";
  toggle.disabled = !hasIdentity;
  const history = document.createElement("div");
  history.className = "device-history";
  history.hidden = true;
  if (hasIdentity) toggle.addEventListener("click", () => showHistory(device, history, toggle));
  const activityTitle = document.createElement("h3");
  activityTitle.className = "device-section-title";
  activityTitle.textContent = "Recent voice activity";
  const activity = document.createElement("div");
  activity.className = "device-activity";
  activity.textContent = hasIdentity ? "Loading activity…" : "Activity will appear after the speaker connects.";
  if (hasIdentity) loadActivity(device, activity);
  card.append(header, details, activityTitle, activity, toggle, history);
  return card;
}

async function loadDevices() {
  refreshButton.disabled = true;
  devicesStatus.textContent = "Refreshing…";
  try {
    const response = await fetch("/api/devices");
    if (!response.ok) throw new Error("Could not load devices");
    const devices = await response.json();
    deviceList.replaceChildren();
    if (!devices.length) {
      const empty = document.createElement("div");
      empty.className = "query-card device-empty";
      empty.textContent = "No voice devices are configured yet.";
      deviceList.append(empty);
      return;
    }
    for (const device of devices) deviceList.append(renderDevice(device));
    devicesStatus.textContent = "";
  } catch (error) {
    deviceList.replaceChildren();
    devicesStatus.textContent = error.message;
  } finally {
    refreshButton.disabled = false;
    if (devicesStatus.textContent === "Refreshing…") devicesStatus.textContent = "";
  }
}

refreshButton.addEventListener("click", loadDevices);
loadDevices();
