const themeNames = {
  oak: "Oak",
  sage: "Sage",
  fog: "Fog blue",
};
const legacyThemes = { forest: "oak", ocean: "fog", ember: "sage", light: "oak" };
const themeStorageKey = "rag-agent-theme-v1";
const themeRoot = document.documentElement;

function applyTheme(theme) {
  const selectedTheme = Object.hasOwn(themeNames, theme) ? theme : (legacyThemes[theme] || "oak");
  themeRoot.dataset.theme = selectedTheme;
  return selectedTheme;
}

let savedTheme = "oak";
try {
  savedTheme = localStorage.getItem(themeStorageKey) || savedTheme;
} catch {
  // Storage may be unavailable in private browsing; the page still uses the selected theme.
}
savedTheme = applyTheme(savedTheme);
try {
  localStorage.setItem(themeStorageKey, savedTheme);
} catch {
  // Keep the selected profile for this page if storage is unavailable.
}

document.addEventListener("DOMContentLoaded", () => {
  const themePicker = document.getElementById("theme-picker");
  const themeStatus = document.getElementById("theme-status");
  const saveAppearance = document.getElementById("save-appearance");
  if (!themePicker) return;

  themePicker.value = savedTheme;
  if (themeStatus) themeStatus.textContent = `${themeNames[savedTheme]} profile selected.`;
  themePicker.addEventListener("change", () => {
    const preview = applyTheme(themePicker.value);
    if (themeStatus) themeStatus.textContent = preview === savedTheme
      ? `${themeNames[preview]} profile selected.`
      : `Previewing ${themeNames[preview]}. Save settings to keep it.`;
  });
  saveAppearance?.addEventListener("click", () => {
    savedTheme = applyTheme(themePicker.value);
    try {
      localStorage.setItem(themeStorageKey, savedTheme);
      if (themeStatus) themeStatus.textContent = `${themeNames[savedTheme]} profile saved.`;
    } catch {
      if (themeStatus) themeStatus.textContent = `${themeNames[savedTheme]} is active for this session, but could not be saved.`;
    }
  });
});

window.addEventListener("storage", (event) => {
  if (event.key !== themeStorageKey) return;
  savedTheme = applyTheme(event.newValue);
  const themePicker = document.getElementById("theme-picker");
  const themeStatus = document.getElementById("theme-status");
  if (themePicker) themePicker.value = savedTheme;
  if (themeStatus) themeStatus.textContent = `${themeNames[savedTheme]} profile selected.`;
});
