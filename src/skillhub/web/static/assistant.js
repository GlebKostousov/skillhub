"use strict";

const form = document.querySelector("[data-assistant-form]");
const statusNode = document.querySelector("[data-assistant-status]");
const badgeNode = document.querySelector("[data-assistant-badge]");
const resultNode = document.querySelector("[data-assistant-result]");
const submitButton = document.querySelector("[data-assistant-submit]");

if (form instanceof HTMLFormElement) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitAssistant(form);
  });
}

/**
 * Отправляет намерение и материал, не затирая ввод при ошибке.
 * @param {HTMLFormElement} assistantForm
 */
async function submitAssistant(assistantForm) {
  const intent = assistantForm.elements.namedItem("intent");
  const material = assistantForm.elements.namedItem("material");
  const token = assistantForm.elements.namedItem("csrf_token");
  if (
    !(intent instanceof HTMLTextAreaElement) ||
    !(material instanceof HTMLTextAreaElement) ||
    !(token instanceof HTMLInputElement)
  ) {
    return;
  }
  setBusy(true);
  setStatus("loading", "Запрос обрабатывается.");
  if (resultNode instanceof HTMLElement) {
    resultNode.textContent = "";
  }
  try {
    const response = await fetch("/api/assistant", {
      method: "POST",
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "X-CSRF-Token": token.value,
      },
      body: JSON.stringify({
        intent: intent.value,
        material: material.value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      setStatus("error", readErrorMessage(payload));
      return;
    }
    renderOutcome(payload);
  } catch {
    setStatus("error", "Не удалось выполнить запрос.");
  } finally {
    setBusy(false);
  }
}

/**
 * Показывает исход и бейдж выбранного режима только через textContent.
 * @param {object} payload
 */
function renderOutcome(payload) {
  const outcome = readString(payload.outcome) || "error";
  setBadge(payload);
  setStatus(outcome, readString(payload.message));
  if (resultNode instanceof HTMLElement) {
    resultNode.textContent = readString(payload.text);
  }
}

/**
 * Обновляет бейдж выбранного режима без HTML-разметки.
 * @param {object} payload
 */
function setBadge(payload) {
  if (!(badgeNode instanceof HTMLElement)) {
    return;
  }
  const caption = readString(payload.caption);
  badgeNode.textContent = caption
    ? `Выбран режим: ${caption}`
    : "Режим ещё не выбран";
}

/**
 * Пишет состояние окна в живой регион.
 * @param {string} state
 * @param {string} message
 */
function setStatus(state, message) {
  if (!(statusNode instanceof HTMLElement)) {
    return;
  }
  statusNode.dataset.assistantState = state;
  statusNode.textContent = message;
}

/**
 * Блокирует повторную отправку на время запроса.
 * @param {boolean} busy
 */
function setBusy(busy) {
  if (form instanceof HTMLFormElement) {
    form.setAttribute("aria-busy", busy ? "true" : "false");
  }
  if (submitButton instanceof HTMLButtonElement) {
    submitButton.disabled = busy;
  }
}

/**
 * Читает безопасное сообщение оболочки ошибки.
 * @param {object} payload
 * @returns {string}
 */
function readErrorMessage(payload) {
  const error = payload && payload.error;
  const message = error ? readString(error.message) : "";
  return message || "Не удалось выполнить запрос.";
}

/**
 * Возвращает строку или пустое значение для текстового узла.
 * @param {unknown} value
 * @returns {string}
 */
function readString(value) {
  return typeof value === "string" ? value : "";
}
