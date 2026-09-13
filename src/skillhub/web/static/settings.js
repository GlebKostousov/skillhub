"use strict";

const form = document.querySelector("[data-settings-form]");
const statusNode = document.querySelector("[data-settings-status]");
const submitButton = document.querySelector("[data-settings-submit]");

if (form instanceof HTMLFormElement) {
  attachGuards(form);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitSettings(form);
  });
}

/**
 * Вешает ограничения ввода и проверяет форму при каждом изменении.
 * @param {HTMLFormElement} settingsForm
 */
function attachGuards(settingsForm) {
  const fields = settingsForm.querySelectorAll("[data-settings-field]");
  for (const field of fields) {
    if (
      !(
        field instanceof HTMLInputElement ||
        field instanceof HTMLSelectElement ||
        field instanceof HTMLTextAreaElement
      )
    ) {
      continue;
    }
    field.addEventListener("keydown", (event) => {
      rejectInvalidKey(event, field);
    });
    field.addEventListener("paste", (event) => {
      rejectInvalidPaste(event, field);
    });
    field.addEventListener("input", () => {
      constrainField(field, false);
      syncValidity(settingsForm);
    });
    field.addEventListener("blur", () => {
      constrainField(field, true);
      syncValidity(settingsForm);
    });
  }
  syncValidity(settingsForm);
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
  constrainAll(settingsForm);
  if (!syncValidity(settingsForm)) {
    setStatus("Исправьте значения вне допустимого диапазона.");
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
    syncValidity(settingsForm);
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
  if (!Number.isFinite(next)) {
    const fallback = Number(field.getAttribute("data-default"));
    return Number.isFinite(fallback) ? fallback : 0;
  }
  if (min !== null && next < Number(min)) {
    next = Number(min);
  }
  if (max !== null && next > Number(max)) {
    next = Number(max);
  }
  return next;
}

/**
 * Подрезает текущее значение поля до допустимого вида.
 * @param {HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement} field
 * @param {boolean} finalize
 */
function constrainField(field, finalize) {
  const kind = field.getAttribute("data-kind");
  if (kind === "string_list" && field instanceof HTMLTextAreaElement) {
    constrainStop(field);
    return;
  }
  if (kind !== "integer" && kind !== "number") {
    return;
  }
  const raw = field.value.trim();
  if (raw === "" || raw === "-" || raw === "." || raw === "-.") {
    if (finalize && field.required) {
      restoreDefault(field);
    }
    return;
  }
  const parsed =
    kind === "integer" ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
  if (!Number.isFinite(parsed)) {
    if (finalize) {
      restoreDefault(field);
    }
    return;
  }
  const next = clampNumber(parsed, field);
  if (next !== parsed || finalize) {
    field.value = String(next);
  }
}

/**
 * Подрезает стоп-фразы по числу строк и длине каждой строки.
 * @param {HTMLTextAreaElement} field
 */
function constrainStop(field) {
  const maxItems = Number(field.getAttribute("data-max-items") || "16");
  const maxLength = Number(field.getAttribute("data-max-length") || "256");
  const lines = field.value.split("\n");
  const kept = [];
  let filled = 0;
  for (const line of lines) {
    const clipped = line.slice(0, maxLength);
    if (clipped.trim() && filled >= maxItems) {
      continue;
    }
    if (clipped.trim()) {
      filled += 1;
    }
    kept.push(clipped);
  }
  const next = kept.join("\n");
  if (next !== field.value) {
    field.value = next;
  }
}

/**
 * Возвращает поле к значению по умолчанию.
 * @param {HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement} field
 */
function restoreDefault(field) {
  const fallback = field.getAttribute("data-default");
  field.value = fallback === null ? "" : fallback;
}

/**
 * Отклоняет клавиши, которые ломают числовой ввод.
 * @param {KeyboardEvent} event
 * @param {HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement} field
 */
function rejectInvalidKey(event, field) {
  const kind = field.getAttribute("data-kind");
  if (kind !== "integer" && kind !== "number") {
    return;
  }
  if (event.ctrlKey || event.metaKey || event.altKey) {
    return;
  }
  const key = event.key;
  if (key === "e" || key === "E" || key === "+" || key === " ") {
    event.preventDefault();
    return;
  }
  if (key === "." && kind === "integer") {
    event.preventDefault();
    return;
  }
  if (key === "-") {
    const min = Number(field.getAttribute("min"));
    if (!Number.isFinite(min) || min >= 0 || field.value.includes("-")) {
      event.preventDefault();
    }
  }
}

/**
 * Отклоняет вставку текста, который нельзя превратить в допустимое число.
 * @param {ClipboardEvent} event
 * @param {HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement} field
 */
function rejectInvalidPaste(event, field) {
  const kind = field.getAttribute("data-kind");
  if (kind !== "integer" && kind !== "number") {
    return;
  }
  const text = event.clipboardData ? event.clipboardData.getData("text") : "";
  const pattern = kind === "integer" ? /^-?\d+$/ : /^-?\d+(?:[.,]\d+)?$/;
  if (!pattern.test(text.trim().replace(",", "."))) {
    event.preventDefault();
  }
}

/**
 * Приводит все поля перед отправкой.
 * @param {HTMLFormElement} settingsForm
 */
function constrainAll(settingsForm) {
  const fields = settingsForm.querySelectorAll("[data-settings-field]");
  for (const field of fields) {
    if (
      field instanceof HTMLInputElement ||
      field instanceof HTMLSelectElement ||
      field instanceof HTMLTextAreaElement
    ) {
      constrainField(field, true);
    }
  }
}

/**
 * Обновляет ошибки полей и доступность кнопки сохранения.
 * @param {HTMLFormElement} settingsForm
 * @returns {boolean}
 */
function syncValidity(settingsForm) {
  let valid = true;
  const fields = settingsForm.querySelectorAll("[data-settings-field]");
  for (const field of fields) {
    if (
      !(
        field instanceof HTMLInputElement ||
        field instanceof HTMLSelectElement ||
        field instanceof HTMLTextAreaElement
      )
    ) {
      continue;
    }
    const message = fieldError(field);
    writeFieldError(field, message);
    if (message) {
      valid = false;
    }
  }
  if (submitButton instanceof HTMLButtonElement && !submitButton.dataset.busy) {
    submitButton.disabled = !valid;
  }
  return valid;
}

/**
 * Возвращает русское сообщение об ошибке поля или пустую строку.
 * @param {HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement} field
 * @returns {string}
 */
function fieldError(field) {
  const kind = field.getAttribute("data-kind");
  if (kind === "string_list") {
    return "";
  }
  if (kind !== "integer" && kind !== "number") {
    if (field.required && field.value === "") {
      return "Выберите одно из допустимых значений.";
    }
    return "";
  }
  const raw = field.value.trim();
  if (raw === "") {
    return field.required ? "Укажите число в допустимом диапазоне." : "";
  }
  const parsed =
    kind === "integer" ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
  if (!Number.isFinite(parsed)) {
    return "Введите число.";
  }
  const min = field.getAttribute("min");
  const max = field.getAttribute("max");
  if (min !== null && parsed < Number(min)) {
    return rangeMessage(min, max);
  }
  if (max !== null && parsed > Number(max)) {
    return rangeMessage(min, max);
  }
  return "";
}

/**
 * Собирает подпись допустимого диапазона.
 * @param {string | null} min
 * @param {string | null} max
 * @returns {string}
 */
function rangeMessage(min, max) {
  if (min !== null && max !== null) {
    return `Допустимо от ${min} до ${max}.`;
  }
  if (min !== null) {
    return `Допустимо от ${min}.`;
  }
  if (max !== null) {
    return `Допустимо до ${max}.`;
  }
  return "Значение вне допустимого диапазона.";
}

/**
 * Пишет ошибку поля только в текстовый узел.
 * @param {HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement} field
 * @param {string} message
 */
function writeFieldError(field, message) {
  const error = fieldErrorNode(field);
  field.setAttribute("aria-invalid", message ? "true" : "false");
  if (!(error instanceof HTMLElement)) {
    return;
  }
  error.textContent = message;
  error.hidden = !message;
}

/**
 * Находит узел ошибки рядом с полем.
 * @param {HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement} field
 * @returns {HTMLElement | null}
 */
function fieldErrorNode(field) {
  const row = field.closest(".settings-field-control");
  if (!(row instanceof HTMLElement)) {
    return null;
  }
  const error = row.querySelector("[data-settings-error]");
  return error instanceof HTMLElement ? error : null;
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
    if (busy) {
      submitButton.dataset.busy = "1";
      submitButton.disabled = true;
    } else {
      delete submitButton.dataset.busy;
    }
  }
}
