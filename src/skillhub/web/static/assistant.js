"use strict";

(function () {
const form = document.querySelector("[data-assistant-form]");
const statusNode = document.querySelector("[data-assistant-status]");
const badgeNode = document.querySelector("[data-assistant-badge]");
const resultNode = document.querySelector("[data-assistant-result]");
const submitButton = document.querySelector("[data-assistant-submit]");
const waitNode = document.querySelector("[data-assistant-wait]");
const waitTextNode = document.querySelector("[data-assistant-wait-text]");
const spinnerNode = document.querySelector("[data-assistant-spinner]");
const submitLabel = document.querySelector("[data-assistant-submit-label]");

if (form instanceof HTMLFormElement) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitAssistant(form);
  });
}

/**
 * Сначала выбирает режим, затем готовит ответ, не затирая ввод при ошибке.
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
  hideProtocolFollowup();
  setBusy(true);
  setBadgeText("Выбираю режим…");
  setWaitText("Смотрю, какой режим подойдёт.");
  setStatus("loading", "Смотрю, какой режим подойдёт.");
  if (resultNode instanceof HTMLElement) {
    resultNode.textContent = "";
  }
  try {
    const classified = await postAssistant(token.value, intent.value, material.value, "classify");
    const classifiedPayload = await classified.json();
    if (!classified.ok) {
      setStatus("error", readErrorMessage(classifiedPayload));
      return;
    }
    if (readString(classifiedPayload.outcome) !== "classified") {
      renderOutcome(classifiedPayload);
      return;
    }
    setBadge(classifiedPayload);
    setWaitText("Режим выбран. Пишу ответ.");
    setStatus("loading", "Режим выбран. Пишу ответ.");
    const response = await postAssistant(
      token.value,
      intent.value,
      material.value,
      "generate",
    );
    const payload = await response.json();
    if (!response.ok) {
      setStatus("error", readErrorMessage(payload));
      return;
    }
    if (readString(payload.selected_skill) === "meeting-protocol") {
      setBadge(payload);
      setWaitText("Готовлю вопросы по пробелам.");
      setStatus("loading", "Готовлю вопросы по пробелам.");
      if (resultNode instanceof HTMLElement) {
        resultNode.textContent = "";
      }
    } else {
      renderOutcome(payload);
    }
    await startProtocolFollowup(payload, material.value);
    if (readString(payload.selected_skill) === "meeting-protocol") {
      setStatus("success", "Ниже несколько вопросов — или сразу сохраните в Word.");
    }
  } catch {
    setStatus("error", "Не получилось отправить. Попробуйте ещё раз.");
  } finally {
    setBusy(false);
  }
}

/**
 * Отправляет одну стадию обращения к ассистенту.
 * @param {string} token
 * @param {string} intent
 * @param {string} material
 * @param {string} stage
 * @returns {Promise<Response>}
 */
function postAssistant(token, intent, material, stage) {
  return fetch("/api/assistant", {
    method: "POST",
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "X-CSRF-Token": token,
    },
    body: JSON.stringify({
      intent,
      material,
      stage,
    }),
  });
}

/**
 * После протокола открывает уточнения на этой же странице.
 * @param {object} payload
 * @param {string} material
 */
async function startProtocolFollowup(payload, material) {
  const panel = document.querySelector("#protocol-followup");
  if (
    readString(payload.selected_skill) !== "meeting-protocol" ||
    readString(payload.outcome) !== "success"
  ) {
    hideProtocolFollowup();
    return;
  }
  const text = readString(payload.text);
  if (panel instanceof HTMLElement) {
    panel.classList.remove("d-none");
  }
  showProtocolLoading(true);
  const markdown = document.querySelector("#protocol-markdown");
  if (markdown instanceof HTMLTextAreaElement) {
    markdown.value = text;
  }
  const transcript = document.querySelector("#protocol-transcript");
  if (transcript instanceof HTMLTextAreaElement) {
    transcript.value = material;
  }
  const protocol = await ensureProtocolApi();
  if (protocol && typeof protocol.startFromDraft === "function") {
    await protocol.startFromDraft(text, material);
    showProtocolLoading(false);
  } else {
    showProtocolLoading(false);
    setStatus("error", "Вопросы не загрузились. Обновите страницу.");
  }
  if (panel instanceof HTMLElement) {
    panel.scrollIntoView({ block: "nearest" });
  }
}

/**
 * Подгружает скрипт уточнений, если хук ещё не появился.
 * @returns {Promise<object|null>}
 */
async function ensureProtocolApi() {
  if (window.SkillHubProtocol && typeof window.SkillHubProtocol.startFromDraft === "function") {
    return window.SkillHubProtocol;
  }
  try {
    await new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "/static/protocol.js?v=revise-1";
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("protocol.js"));
      document.head.appendChild(script);
    });
  } catch {
    return null;
  }
  if (window.SkillHubProtocol && typeof window.SkillHubProtocol.startFromDraft === "function") {
    return window.SkillHubProtocol;
  }
  return null;
}

/**
 * Показывает ожидание списка пропусков в панели протокола.
 * @param {boolean} visible
 */
function showProtocolLoading(visible) {
  const loading = document.querySelector("#protocol-clarifications-loading");
  if (loading instanceof HTMLElement) {
    loading.classList.toggle("d-none", !visible);
  }
}

/**
 * Скрывает панель уточнений протокола.
 */
function hideProtocolFollowup() {
  const panel = document.querySelector("#protocol-followup");
  if (panel instanceof HTMLElement) {
    panel.classList.add("d-none");
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
  const caption = readString(payload.caption);
  setBadgeText(caption ? `Режим: ${caption}` : "Режим пока не выбран");
}

/**
 * Пишет текст бейджа режима.
 * @param {string} text
 */
function setBadgeText(text) {
  if (badgeNode instanceof HTMLElement) {
    badgeNode.textContent = text;
  }
}

/**
 * Пишет пояснение в баннер ожидания.
 * @param {string} text
 */
function setWaitText(text) {
  if (waitTextNode instanceof HTMLElement) {
    waitTextNode.textContent = text;
  }
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
    const fields = form.querySelectorAll("textarea, button");
    for (const field of fields) {
      if (field instanceof HTMLTextAreaElement || field instanceof HTMLButtonElement) {
        field.disabled = busy;
      }
    }
  }
  if (submitButton instanceof HTMLButtonElement) {
    submitButton.disabled = busy;
  }
  if (submitLabel instanceof HTMLElement) {
    submitLabel.textContent = busy ? "Работаю…" : "Отправить";
  }
  if (spinnerNode instanceof HTMLElement) {
    spinnerNode.classList.toggle("d-none", !busy);
  }
  if (waitNode instanceof HTMLElement) {
    waitNode.hidden = !busy;
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
  return message || "Не получилось отправить. Попробуйте ещё раз.";
}

/**
 * Возвращает строку или пустое значение для текстового узла.
 * @param {unknown} value
 * @returns {string}
 */
function readString(value) {
  return typeof value === "string" ? value : "";
}
})();
