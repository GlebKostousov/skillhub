"use strict";

const reloadForm = document.querySelector("[data-reload-form]");
const reloadNotice = document.querySelector(
  "[data-reload-result], [data-reload-unavailable]",
);
const currentUrl = new URL(window.location.href);
const hasTransientQuery =
  currentUrl.searchParams.has("reload") ||
  currentUrl.searchParams.has("notice");
const themeToggle = document.querySelector("[data-theme-toggle]");
const skillHelp = document.querySelector("#add-skill-dialog");
const openSkillHelp = document.querySelector("[data-add-skill-open]");
const closeSkillHelp = document.querySelector("[data-add-skill-close]");

if (hasTransientQuery) {
  currentUrl.searchParams.delete("reload");
  currentUrl.searchParams.delete("notice");
  const remainingQuery = currentUrl.searchParams.toString();
  const canonicalUrl = `${currentUrl.pathname}${remainingQuery ? `?${remainingQuery}` : ""}${currentUrl.hash}`;
  history.replaceState(null, "", canonicalUrl);
}

if (reloadNotice instanceof HTMLElement) {
  reloadNotice.focus();
}

applyStoredTheme();

if (themeToggle instanceof HTMLInputElement) {
  themeToggle.addEventListener("change", () => {
    writeTheme(themeToggle.checked ? "dark" : "light");
    syncDocumentTheme(themeToggle.checked);
  });
}

if (skillHelp instanceof HTMLDialogElement) {
  if (openSkillHelp instanceof HTMLButtonElement) {
    openSkillHelp.addEventListener("click", () => {
      if (typeof skillHelp.showPopover === "function") {
        return;
      }
      skillHelp.showModal();
    });
  }
  if (closeSkillHelp instanceof HTMLButtonElement) {
    closeSkillHelp.addEventListener("click", () => {
      if (typeof skillHelp.hidePopover === "function") {
        return;
      }
      skillHelp.close();
    });
  }
}

function applyStoredTheme() {
  const dark = readTheme() === "dark";
  if (themeToggle instanceof HTMLInputElement) {
    themeToggle.checked = dark;
  }
  syncDocumentTheme(dark);
}

function syncDocumentTheme(dark) {
  document.documentElement.setAttribute("data-bs-theme", dark ? "dark" : "light");
}

function readTheme() {
  try {
    return localStorage.getItem("skillhub-theme") === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function writeTheme(theme) {
  try {
    localStorage.setItem("skillhub-theme", theme);
  } catch {
    return;
  }
}

if (reloadForm instanceof HTMLFormElement) {
  reloadForm.addEventListener("submit", () => {
    const button = reloadForm.querySelector("[data-reload-button]");
    const status = document.querySelector("#reload-live-status");

    if (button instanceof HTMLButtonElement) {
      button.disabled = true;
      button.textContent = "Перезагрузка…";
    }
    if (status instanceof HTMLElement) {
      status.textContent = "Каталог перезагружается.";
    }
  });
}
