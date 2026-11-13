/* Shared appearance picker: skins are independent of the light/dark theme. */
(function () {
  "use strict";
  if (window.AtlasSkins) return;
  const KEY = "atlas-log-skin";
  const LABELS = { classic: "Classic", aurora: "Aurora", mineral: "Mineral" };
  const root = document.documentElement;
  const valid = (value) => Object.hasOwn(LABELS, value) ? value : "classic";
  let picker = null;
  let current = "classic";

  function apply(value) {
    current = valid(value);
    if (current === "classic") root.removeAttribute("data-skin");
    else root.setAttribute("data-skin", current);
    if (!picker) return;
    const summary = picker.querySelector("summary");
    summary.title = "Skin: " + LABELS[current];
    summary.setAttribute("aria-label", "Choose skin, current: " + LABELS[current]);
    picker.querySelectorAll("[data-skin-choice]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.skinChoice === current));
    });
  }

  function choose(value) {
    apply(value);
    try { localStorage.setItem(KEY, current); } catch (e) { /* storage unavailable */ }
  }

  window.AtlasSkins = { choose };
  try { apply(localStorage.getItem(KEY)); } catch (e) { apply(root.getAttribute("data-skin")); }
  window.addEventListener("storage", (event) => {
    if (event.key === KEY || event.key === null) apply(event.newValue);
  });

  function setup() {
    const theme = document.querySelector(".tool-btns #btn-theme, .tool-btns #shell-theme");
    if (!theme || document.querySelector(".skin-picker")) return;
    picker = document.createElement("details");
    picker.className = "skin-picker";
    picker.id = "skin-picker";
    const summary = document.createElement("summary");
    summary.className = "btn icon";
    summary.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.3" aria-hidden="true"><path d="M8 1.8a6.2 6.2 0 1 0 0 12.4h.7a1.4 1.4 0 0 0 1-2.4.8.8 0 0 1 .6-1.4h1.1A2.8 2.8 0 0 0 14.2 8 6.2 6.2 0 0 0 8 1.8Z"/><circle cx="4.5" cy="7" r=".6" fill="currentColor"/><circle cx="6.5" cy="4.8" r=".6" fill="currentColor"/><circle cx="9.5" cy="4.8" r=".6" fill="currentColor"/></svg>';
    const menu = document.createElement("div");
    menu.className = "skin-menu";
    menu.setAttribute("role", "group");
    menu.setAttribute("aria-label", "Appearance skins");
    Object.entries(LABELS).forEach(([value, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "skin-choice";
      button.dataset.skinChoice = value;
      const swatch = document.createElement("span");
      swatch.className = "skin-swatch";
      swatch.setAttribute("aria-hidden", "true");
      const text = document.createElement("span");
      text.textContent = label;
      button.append(swatch, text);
      button.addEventListener("click", () => choose(value));
      menu.append(button);
    });
    picker.append(summary, menu);
    theme.after(picker);
    apply(current);
    document.addEventListener("click", (event) => {
      if (!picker.contains(event.target)) picker.open = false;
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !picker.open) return;
      picker.open = false;
      summary.focus();
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", setup, { once: true });
  else setup();
})();
