const settingsNavToggle = document.querySelector(".settings-nav-toggle");
const settingsSubnav = document.getElementById("settings-subnav");

function setSettingsNavOpen(open) {
  if (!settingsNavToggle || !settingsSubnav) return;
  settingsSubnav.hidden = !open;
  settingsNavToggle.setAttribute("aria-expanded", String(open));
  settingsNavToggle.setAttribute("aria-label", open ? "Collapse settings sections" : "Expand settings sections");
}

setSettingsNavOpen(window.location.pathname === "/settings");
settingsNavToggle?.addEventListener("click", () => setSettingsNavOpen(settingsSubnav.hidden));

if (!document.getElementById("conversation-sidebar")) {
  const menuToggle = document.getElementById("mobile-menu-toggle");
  const pageNav = document.getElementById("page-nav");

  function setMenuOpen(open) {
    pageNav?.classList.toggle("open", open);
    menuToggle?.setAttribute("aria-expanded", String(open));
    menuToggle?.setAttribute("aria-label", open ? "Close navigation menu" : "Open navigation menu");
  }

  menuToggle?.addEventListener("click", () => {
    setMenuOpen(menuToggle.getAttribute("aria-expanded") !== "true");
  });

  pageNav?.addEventListener("click", (event) => {
    if (event.target.closest("a, [data-close-mobile-menu]")) setMenuOpen(false);
  });

  document.addEventListener("click", (event) => {
    if (!pageNav?.contains(event.target) && !menuToggle?.contains(event.target)) setMenuOpen(false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setMenuOpen(false);
  });

  window.matchMedia("(min-width: 721px)").addEventListener("change", (event) => {
    if (event.matches) setMenuOpen(false);
  });
}

const pageSidebar = document.querySelector(".page-sidebar");
if (pageSidebar && !document.getElementById("agent-workspace")) {
  const menuToggle = document.getElementById("mobile-menu-toggle");
  const collapseToggle = document.getElementById("collapse-sidebar");
  const drawerClose = document.getElementById("toggle-conversations");
  const backdrop = document.getElementById("drawer-backdrop");
  const pageNav = document.getElementById("page-nav");
  const mobile = window.matchMedia("(max-width: 700px)");

  function setSidebarHidden(hidden) {
    if (mobile.matches) {
      document.body.classList.remove("sidebar-collapsed");
      pageSidebar.classList.toggle("open", !hidden);
      backdrop.hidden = hidden;
      menuToggle.setAttribute("aria-expanded", String(!hidden));
      menuToggle.setAttribute("aria-label", hidden ? "Open navigation menu" : "Close navigation menu");
    } else {
      document.body.classList.toggle("sidebar-collapsed", hidden);
      collapseToggle.setAttribute("aria-expanded", String(!hidden));
      collapseToggle.setAttribute("aria-label", hidden ? "Expand sidebar" : "Collapse sidebar");
      collapseToggle.title = hidden ? "Expand sidebar" : "Collapse sidebar";
      localStorage.setItem("conversation-sidebar-hidden", hidden ? "1" : "0");
    }
  }

  setSidebarHidden(mobile.matches || localStorage.getItem("conversation-sidebar-hidden") === "1");
  collapseToggle.addEventListener("click", () => setSidebarHidden(!document.body.classList.contains("sidebar-collapsed")));
  menuToggle.addEventListener("click", () => setSidebarHidden(pageSidebar.classList.contains("open")));
  drawerClose.addEventListener("click", () => setSidebarHidden(true));
  backdrop.addEventListener("click", () => setSidebarHidden(true));
  pageSidebar.addEventListener("click", (event) => {
    if (mobile.matches && event.target.closest("a")) setSidebarHidden(true);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && mobile.matches) setSidebarHidden(true);
  });
  mobile.addEventListener("change", () => setSidebarHidden(mobile.matches || localStorage.getItem("conversation-sidebar-hidden") === "1"));

  fetch("/agent/conversations")
    .then((response) => response.ok ? response.json() : [])
    .then((conversations) => {
      const list = document.getElementById("conversation-list");
      for (const conversation of conversations.slice(0, 12)) {
        const link = document.createElement("a");
        link.className = "conversation-item";
        link.href = `/?conversation_id=${encodeURIComponent(conversation.id)}`;
        link.textContent = conversation.title;
        list.append(link);
      }
    })
    .catch((error) => console.warn("Unable to load conversations", error));

  pageNav?.addEventListener("click", (event) => {
    if (event.target.closest("a")) setSidebarHidden(true);
  });
}
