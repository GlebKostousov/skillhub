"use strict";

const form = document.querySelector("[data-settings-form]");
const statusNode = document.querySelector("[data-settings-status]");
const submitButton = document.querySelector("[data-settings-submit]");

if (form instanceof HTMLFormElement) {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitSettings(form);
  });
}

/**
 * Отправляет полный набор значений через CSRF и показывает исход текстом.
 * @param {HTMLFormElement} settingsForm
 */
async function submitSettings(settingsForm) {
  const token = settingsForm.elements.namedItem("csrf_token");
  if (!(token instanceof HTMLInputElement)) {
    return;
  }
  setBusy(true);
  setStatus("Настройки сохраняются.");
  try {
    const response = await fetch("/api/settings", {
      method: "POST",
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "X-CSRF-Token": token.value,
      },
      body: JSON.stringify({
        csrf_token: token.value,
        values: collectValues(settingsForm),
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      setStatus(readErrorMessage(payload));
      return;
    }
    setStatus("Настройки сохранены.");
  } catch {
    setStatus("Не удалось сохранить настройки.");
  } finally {
    setBusy(false);
  }
}

/**
 * Собирает объявленные поля формы в типизированный набор значений.
 * @param {HTMLFormElement} settingsForm
 * @returns {object}
 */
function collectValues(settingsForm) {
  const values = {};
  const fields = settingsForm.querySelectorAll("[data-settings-field]");
  for (const field of fields) {
    if (
      field instanceof HTMLInputElement ||
      field instanceof HTMLSelectElement ||
      field instanceof HTMLTextAreaElement
    ) {
      values[field.name] = readValue(field);
    }
  }
  return values;
}

/**
 * Разбирает значение поля по его машинному виду.
 * @param {HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement} field
 * @returns {unknown}
 */
function readValue(field) {
  const kind = field.getAttribute("data-kind");
  const raw = field.value;
  if (kind === "boolean") {
    return raw === "true";
  }
  if (kind === "integer") {
    if (raw === "") {
      return null;
    }
    return clampNumber(Number.parseInt(raw, 10), field);
  }
  if (kind === "number") {
    return clampNumber(Number.parseFloat(raw), field);
  }
  if (kind === "string_list") {
    return raw
      .split("\n")
      .map((item) => item.trim())
      .filter((item) => item.length > 0);
  }
  return raw;
}

/**
 * Ограничивает число границами поля, если они заданы.
 * @param {number} value
 * @param {HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement} field
 * @returns {number}
 */
function clampNumber(value, field) {
  const min = field.getAttribute("min");
  const max = field.getAttribute("max");
  let next = value;
  if (min !== null && next < Number(min)) {
    next = Number(min);
  }
  if (max !== null && next > Number(max)) {
    next = Number(max);
  }
  return next;
}

/**
 * Читает публичное сообщение отказа без разбора HTML.
 * @param {object} payload
 * @returns {string}
 */
function readErrorMessage(payload) {
  const error = payload && payload.error;
  const message = error && error.message;
  if (typeof message === "string" && message) {
    return message;
  }
  return "Некорректный запрос.";
}

/**
 * Пишет статус только в текстовый узел.
 * @param {string} message
 */
function setStatus(message) {
  if (statusNode instanceof HTMLElement) {
    statusNode.textContent = message;
  }
}

/**
 * Блокирует кнопку сохранения на время запроса.
 * @param {boolean} busy
 */
function setBusy(busy) {
  if (submitButton instanceof HTMLButtonElement) {
    submitButton.disabled = busy;
  }
}
