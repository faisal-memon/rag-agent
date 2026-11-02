const serverList = document.getElementById("mcp-server-list");
const statusLine = document.getElementById("tools-status");
const refreshButton = document.getElementById("refresh-tools");

function addToolCard(tool, container) {
  const item = document.createElement("article");
  item.className = "mcp-tool-item";
  const name = document.createElement("strong");
  name.textContent = tool.name || "Unnamed tool";
  item.append(name);
  if (tool.description) {
    const description = document.createElement("p");
    description.textContent = tool.description;
    item.append(description);
  }
  container.append(item);
}

function renderCatalog(data) {
  serverList.replaceChildren();
  for (const server of data.servers || []) {
    const card = document.createElement("article");
    card.className = "query-card mcp-server-card";
    const header = document.createElement("div");
    header.className = "mcp-server-heading";
    const title = document.createElement("h2");
    title.textContent = server.name;
    const badge = document.createElement("span");
    badge.className = `mcp-server-status ${server.reachable ? "reachable" : "unreachable"}`;
    const source = server.source === "remote" ? "Remote MCP" : "Private MCP";
    badge.textContent = server.reachable ? `${source} · tools discovered` : `${source} · unreachable`;
    header.append(title, badge);
    card.append(header);
    if (server.description) {
      const description = document.createElement("p");
      description.className = "subtitle";
      description.textContent = server.description;
      card.append(description);
    }
    if (server.error) {
      const error = document.createElement("p");
      error.className = "mcp-server-error";
      error.textContent = server.error;
      card.append(error);
    }
    const toolItems = document.createElement("div");
    toolItems.className = "mcp-tool-list";
    for (const tool of server.tools || []) addToolCard(tool, toolItems);
    if (!server.tools?.length && server.reachable) {
      const empty = document.createElement("p");
      empty.className = "status";
      empty.textContent = "Connected, but this server did not list any tools.";
      toolItems.append(empty);
    }
    card.append(toolItems);
    serverList.append(card);
  }
  if (!data.servers?.length) {
    const empty = document.createElement("article");
    empty.className = "query-card";
    empty.textContent = data.refresh_error || "No MCP servers are currently configured.";
    serverList.append(empty);
  }
}

async function loadCatalog() {
  refreshButton.disabled = true;
  statusLine.textContent = "Loading MCP catalog…";
  try {
    const response = await fetch("/api/tools");
    if (!response.ok) throw new Error("Could not load the MCP catalog.");
    const data = await response.json();
    renderCatalog(data);
    statusLine.textContent = data.refresh_error
      ? "Showing the last available catalog; the latest refresh failed."
      : `Showing ${data.servers.length} configured MCP server${data.servers.length === 1 ? "" : "s"}.`;
  } catch (error) {
    statusLine.textContent = error.message;
  } finally {
    refreshButton.disabled = false;
  }
}

refreshButton.addEventListener("click", loadCatalog);
loadCatalog();
