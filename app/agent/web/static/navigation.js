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
