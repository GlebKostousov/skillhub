"use strict";

const reloadForm = document.querySelector("[data-reload-form]");
const reloadNotice = document.querySelector(
  "[data-reload-result], [data-reload-unavailable]",
);
const currentUrl = new URL(window.location.href);
const hasTransientQuery =
  currentUrl.searchParams.has("reload") ||
  currentUrl.searchParams.has("notice");

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
