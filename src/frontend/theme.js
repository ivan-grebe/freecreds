export function initThemeToggle() {
  const button = document.getElementById("theme-toggle");
  if (!button) return;
  const root = document.documentElement;
  const media = window.matchMedia
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null;

  function effectiveTheme() {
    const selected = root.getAttribute("data-theme");
    if (selected === "light" || selected === "dark") return selected;
    return media?.matches ? "dark" : "light";
  }

  function refreshLabel() {
    const theme = effectiveTheme();
    root.setAttribute("data-effective-theme", theme);
    const label = button.querySelector(".theme-label");
    if (label) label.textContent = theme === "dark" ? "Light mode" : "Dark mode";
    button.setAttribute(
      "aria-label",
      theme === "dark" ? "Switch to light mode" : "Switch to dark mode",
    );
  }

  button.addEventListener("click", () => {
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try {
      localStorage.setItem("theme", next);
    } catch {
      // Storage can be unavailable in privacy-restricted browsing contexts.
    }
    refreshLabel();
  });
  media?.addEventListener?.("change", () => {
    if (!root.hasAttribute("data-theme")) refreshLabel();
  });
  refreshLabel();
}
