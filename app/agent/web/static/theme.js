const themeNames = {
  forest: "Forest green",
  ocean: "Ocean blue",
  ember: "Warm amber",
  light: "Light",
};
const themeStorageKey = "rag-agent-theme-v1";
const themeRoot = document.documentElement;

function applyTheme(theme) {
  const selectedTheme = Object.hasOwn(themeNames, theme) ? theme : "forest";
  themeRoot.dataset.theme = selectedTheme;
  return selectedTheme;
}

let savedTheme = "forest";
try {
  savedTheme = localStorage.getItem(themeStorageKey) || savedTheme;
} catch {
  // Storage may be unavailable in private browsing; the page still uses the selected theme.
}
savedTheme = applyTheme(savedTheme);

document.addEventListener("DOMContentLoaded", () => {
  const themePicker = document.getElementById("theme-picker");
  const themeStatus = document.getElementById("theme-status");
  if (!themePicker) return;

  themePicker.value = savedTheme;
  themePicker.addEventListener("change", () => {
    savedTheme = applyTheme(themePicker.value);
    try {
      localStorage.setItem(themeStorageKey, savedTheme);
    } catch {
      // The theme remains applied for this page even if it cannot be persisted.
    }
    if (themeStatus) themeStatus.textContent = `${themeNames[savedTheme]} theme selected.`;
  });
});

window.addEventListener("storage", (event) => {
  if (event.key !== themeStorageKey) return;
  savedTheme = applyTheme(event.newValue);
  const themePicker = document.getElementById("theme-picker");
  const themeStatus = document.getElementById("theme-status");
  if (themePicker) themePicker.value = savedTheme;
  if (themeStatus) themeStatus.textContent = `${themeNames[savedTheme]} theme selected.`;
});
