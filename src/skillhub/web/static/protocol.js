"use strict";

const csrfInput = document.querySelector("#protocol-csrf");
const transcriptArea = document.querySelector("#protocol-transcript");
const markdownArea = document.querySelector("#protocol-markdown");
const draftButton = document.querySelector("#protocol-draft-button");
const downloadButton = document.querySelector("#protocol-download-button");
const clarificationsButton = document.querySelector("#protocol-clarifications-button");
const finalizeButton = document.querySelector("#protocol-finalize-button");
const clarificationsWrap = document.querySelector("#protocol-clarifications-wrap");
const clarificationsEmpty = document.querySelector("#protocol-clarifications-empty");
const clarificationsBody = document.querySelector("#protocol-clarifications-body");
const unconfirmedNode = document.querySelector("#protocol-unconfirmed");
const unconfirmedList = document.querySelector("#protocol-unconfirmed-list");
const statusNode = document.querySelector("#protocol-status");
const errorNode = document.querySelector("#protocol-error");
const errorMessageNode = document.querySelector("#protocol-error-message");
const errorLineNode = document.querySelector("#protocol-error-line");
const errorExpectedNode = document.querySelector("#protocol-error-expected");
const errorGotNode = document.querySelector("#protocol-error-got");
const CONNECTION_ERROR = "Запрос не выполнен. Проверьте соединение и повторите.";
const EMPTY_ANSWER_ERROR = "Ответ не должен быть пустым.";
const STATUS_LABELS = {
  pending: "Ожидает",
  answered: "Отвечено",
  skipped: "Пропущено",
};
const draftAvailable =
  draftButton instanceof HTMLButtonElement && !draftButton.disabled;
const rowMemory = new Map();
let lastItems = [];
let tableLoaded = false;
let baseText = "";

if (draftButton instanceof HTMLButtonElement) {
  draftButton.addEventListener("click", () => {
    void submitDraft();
  });
}

if (downloadButton instanceof HTMLButtonElement) {
  downloadButton.addEventListener("click", () => {
    void submitDownload();
  });
}

if (clarificationsButton instanceof HTMLButtonElement) {
  clarificationsButton.addEventListener("click", () => {
    void submitClarifications();
  });
}

if (finalizeButton instanceof HTMLButtonElement) {
  finalizeButton.addEventListener("click", () => {
    void submitFinalize();
  });
}

async function submitDraft() {
  if (!draftAvailable) {
    return;
  }
  clearMessages();
  setBusy(true, "Собирается черновик…");
  try {
    const response = await postJson("/protocol/draft", {
      csrf_token: csrfValue(),
      transcript: fieldValue(transcriptArea),
    });
    const payload = await readJson(response);
    if (!response.ok) {
      showError(payload);
      return;
    }
    writeMarkdown(payload);
    await refreshClarifications();
    setText(statusNode, "Черновик собран.");
  } catch {
    showConnectionError();
  } finally {
    setBusy(false);
  }
}

async function submitDownload() {
  clearMessages();
  setBusy(true, "Готовится Word…");
  try {
    const response = await postJson("/protocol/docx", {
      csrf_token: csrfValue(),
      text: fieldValue(markdownArea),
    });
    if (!response.ok) {
      showError(await readJson(response));
      return;
    }
    await saveDocx(response);
    setText(statusNode, "Файл protocol.docx сохранён.");
  } catch {
    showConnectionError();
  } finally {
    setBusy(false);
  }
}

function setBusy(busy, status) {
  if (draftButton instanceof HTMLButtonElement) {
    draftButton.disabled = busy || !draftAvailable;
  }
  if (downloadButton instanceof HTMLButtonElement) {
    downloadButton.disabled = busy;
  }
  if (clarificationsButton instanceof HTMLButtonElement) {
    clarificationsButton.disabled = busy;
  }
  if (finalizeButton instanceof HTMLButtonElement) {
    finalizeButton.disabled = busy;
  }
  applyRowBusy(busy);
  if (typeof status === "string") {
    setText(statusNode, status);
  }
}

async function submitFinalize() {
  clearMessages();
  setBusy(true, "Формируется итоговый протокол…");
  try {
    const response = await postJson("/protocol/finalize", {
      csrf_token: csrfValue(),
      text: tableLoaded ? baseText : fieldValue(markdownArea),
      answers: finalizeAnswers(),
      material: fieldValue(transcriptArea),
    });
    const payload = await readJson(response);
    if (!response.ok) {
      showError(payload);
      return;
    }
    writeMarkdown(payload);
    showUnconfirmed(asUnconfirmed(payload));
    setText(statusNode, "Итоговый протокол сформирован.");
  } catch {
    showConnectionError();
  } finally {
    setBusy(false);
  }
}

function finalizeAnswers() {
  const answers = [];
  for (const decision of remainingDecisions()) {
    const item = {
      id: decision.item.id,
      action: decision.status === "skipped" ? "skip" : "answer",
    };
    if (decision.status === "answered") {
      item.value = decision.value;
    }
    answers.push(item);
  }
  return answers;
}

function asUnconfirmed(payload) {
  if (!payload || !Array.isArray(payload.unconfirmed)) {
    return [];
  }
  const items = [];
  for (const item of payload.unconfirmed) {
    if (item && typeof item.target === "string" && typeof item.reason === "string") {
      items.push(item);
    }
  }
  return items;
}

function showUnconfirmed(items) {
  if (!(unconfirmedList instanceof HTMLElement)) {
    return;
  }
  while (unconfirmedList.firstChild) {
    unconfirmedList.removeChild(unconfirmedList.firstChild);
  }
  for (const item of items) {
    const entry = document.createElement("li");
    entry.textContent = `${item.target}: ${item.reason}`;
    unconfirmedList.appendChild(entry);
  }
  toggleHidden(unconfirmedNode, items.length === 0);
}

async function submitClarifications() {
  clearMessages();
  setBusy(true, "Собираются уточнения…");
  try {
    const loaded = await refreshClarifications();
    if (loaded) {
      setText(
        statusNode,
        lastItems.length === 0 ? "Важных пропусков нет." : "Уточнения собраны.",
      );
    }
  } catch {
    showConnectionError();
  } finally {
    setBusy(false);
  }
}

async function refreshClarifications() {
  const response = await postJson("/protocol/clarifications", {
    csrf_token: csrfValue(),
    text: fieldValue(markdownArea),
  });
  const payload = await readJson(response);
  if (!response.ok) {
    showError(payload);
    return false;
  }
  rowMemory.clear();
  baseText = fieldValue(markdownArea);
  lastItems = asClarifications(payload);
  tableLoaded = true;
  renderClarifications(lastItems);
  return true;
}

async function submitAnswer(item, action, input) {
  const value = input instanceof HTMLInputElement ? input.value : "";
  if (action === "answer" && value.trim() === "") {
    showRowError(item.id, EMPTY_ANSWER_ERROR);
    return;
  }
  clearMessages();
  clearRowError(item.id);
  setBusy(true, action === "skip" ? "Строка пропускается…" : "Ответ отправляется…");
  try {
    const body = {
      csrf_token: csrfValue(),
      text: fieldValue(markdownArea),
      id: item.id,
      action,
    };
    if (action === "answer") {
      body.value = value;
    }
    const response = await postJson("/protocol/answer", body);
    const payload = await readJson(response);
    if (!response.ok) {
      showError(payload);
      return;
    }
    rememberDecision(item, action, value);
    writeMarkdown(payload);
    lastItems = mergeRows(asClarifications(payload));
    renderClarifications(lastItems);
    setText(statusNode, action === "skip" ? "Строка пропущена." : "Ответ принят.");
  } catch {
    showConnectionError();
  } finally {
    setBusy(false);
  }
}

function rememberDecision(item, action, value) {
  rowMemory.set(item.id, {
    item: {
      id: item.id,
      target: item.target,
      reason: item.reason,
      hint: item.hint,
    },
    status: action === "skip" ? "skipped" : "answered",
    value,
  });
}

async function cancelChoice(id) {
  const memory = rowMemory.get(id);
  if (!memory) {
    return;
  }
  rowMemory.delete(id);
  clearMessages();
  setBusy(true, "Выбор отменяется…");
  try {
    const remaining = remainingDecisions();
    if (remaining.length === 0) {
      writeMarkdown({ text: baseText });
      lastItems = pendingItems(lastItems);
      renderClarifications(lastItems);
      setText(statusNode, "Выбор отменён.");
      return;
    }
    const rebuilt = await replayRemaining(baseText, remaining);
    if (!rebuilt) {
      rowMemory.set(id, memory);
      return;
    }
    writeMarkdown(rebuilt);
    lastItems = mergeRows(asClarifications(rebuilt));
    renderClarifications(lastItems);
    setText(statusNode, "Выбор отменён.");
  } catch {
    rowMemory.set(id, memory);
    showConnectionError();
  } finally {
    setBusy(false);
  }
}

function pendingItems(items) {
  const rows = [];
  for (const item of items) {
    rows.push({
      id: item.id,
      target: item.target,
      reason: item.reason,
      hint: item.hint,
      status: "pending",
    });
  }
  return rows;
}

async function replayRemaining(text, remaining) {
  let current = text;
  let payload = { text: current, clarifications: [] };
  for (const decision of remaining) {
    const body = {
      csrf_token: csrfValue(),
      text: current,
      id: decision.item.id,
      action: decision.status === "skipped" ? "skip" : "answer",
    };
    if (decision.status === "answered") {
      body.value = decision.value;
    }
    const response = await postJson("/protocol/answer", body);
    payload = await readJson(response);
    if (!response.ok) {
      showError(payload);
      return null;
    }
    current = textField(payload, "text");
  }
  return payload;
}

function remainingDecisions() {
  const decisions = [];
  const seen = new Set();
  for (const item of lastItems) {
    const memory = rowMemory.get(item.id);
    if (memory) {
      decisions.push(memory);
      seen.add(item.id);
    }
  }
  for (const [id, memory] of rowMemory) {
    if (!seen.has(id)) {
      decisions.push(memory);
    }
  }
  return decisions;
}

function mergeRows(serverItems) {
  const serverById = new Map();
  for (const item of serverItems) {
    serverById.set(item.id, item);
  }
  const ids = [];
  collectIds(ids, lastItems);
  collectIds(ids, serverItems);
  collectIds(ids, Array.from(rowMemory.keys()).map((id) => ({ id })));
  const rows = [];
  for (const id of ids) {
    const memory = rowMemory.get(id);
    if (memory) {
      rows.push({
        id: memory.item.id,
        target: memory.item.target,
        reason: memory.item.reason,
        hint: memory.item.hint,
        status: memory.status,
      });
      continue;
    }
    const server = serverById.get(id);
    if (server) {
      rows.push(server);
    }
  }
  return rows;
}

function collectIds(ids, items) {
  for (const item of items) {
    if (ids.indexOf(item.id) === -1) {
      ids.push(item.id);
    }
  }
}

function asClarifications(payload) {
  if (!payload || !Array.isArray(payload.clarifications)) {
    return [];
  }
  const items = [];
  for (const item of payload.clarifications) {
    if (item && typeof item.id === "string") {
      items.push(item);
    }
  }
  return items;
}

function renderClarifications(items) {
  if (!(clarificationsBody instanceof HTMLTableSectionElement)) {
    return;
  }
  while (clarificationsBody.firstChild) {
    clarificationsBody.removeChild(clarificationsBody.firstChild);
  }
  const hasItems = items.length > 0;
  toggleHidden(clarificationsWrap, !hasItems);
  toggleHidden(clarificationsEmpty, !tableLoaded || hasItems);
  for (const item of items) {
    clarificationsBody.appendChild(buildRow(item));
  }
}

function buildRow(item) {
  const row = document.createElement("tr");
  row.dataset.id = item.id;
  row.dataset.status = item.status;
  row.appendChild(textCell(item.reason));
  row.appendChild(answerCell(item));
  row.appendChild(actionsCell(item));
  row.appendChild(statusCell(item));
  return row;
}

function textCell(value) {
  const cell = document.createElement("td");
  cell.textContent = typeof value === "string" ? value : "";
  return cell;
}

function answerCell(item) {
  const cell = document.createElement("td");
  const input = document.createElement("input");
  input.type = "text";
  input.className = "form-control";
  input.setAttribute("aria-label", "Ответ");
  if (typeof item.hint === "string") {
    input.placeholder = item.hint;
  }
  const memory = rowMemory.get(item.id);
  if (memory && typeof memory.value === "string") {
    input.value = memory.value;
  }
  input.disabled = isLocked(item.status);
  const error = document.createElement("p");
  error.className = "text-danger small mb-0 mt-1 d-none";
  error.dataset.role = "row-error";
  cell.appendChild(input);
  cell.appendChild(error);
  return cell;
}

function actionsCell(item) {
  const cell = document.createElement("td");
  const locked = isLocked(item.status);
  const send = actionButton("Отправить", "send", "btn btn-sm btn-primary me-2");
  const skip = actionButton(
    "Пропустить",
    "skip",
    "btn btn-sm btn-outline-secondary me-2",
  );
  const cancel = actionButton(
    "Отменить выбор",
    "cancel",
    "btn btn-sm btn-outline-primary",
  );
  send.disabled = locked;
  skip.disabled = locked;
  cancel.hidden = !locked;
  send.addEventListener("click", () => {
    void submitAnswer(item, "answer", rowInput(item.id));
  });
  skip.addEventListener("click", () => {
    void submitAnswer(item, "skip", rowInput(item.id));
  });
  cancel.addEventListener("click", () => {
    void cancelChoice(item.id);
  });
  cell.appendChild(send);
  cell.appendChild(skip);
  cell.appendChild(cancel);
  return cell;
}

function statusCell(item) {
  const cell = document.createElement("td");
  const label = STATUS_LABELS[item.status];
  cell.textContent = typeof label === "string" ? label : STATUS_LABELS.pending;
  return cell;
}

function actionButton(label, action, className) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.dataset.action = action;
  return button;
}

function rowInput(id) {
  const row = findRow(id);
  if (!(row instanceof HTMLElement)) {
    return null;
  }
  const input = row.querySelector("input");
  return input instanceof HTMLInputElement ? input : null;
}

function showRowError(id, message) {
  const error = rowError(id);
  if (!(error instanceof HTMLElement)) {
    return;
  }
  error.textContent = message;
  error.classList.remove("d-none");
}

function clearRowError(id) {
  const error = rowError(id);
  if (!(error instanceof HTMLElement)) {
    return;
  }
  error.textContent = "";
  error.classList.add("d-none");
}

function rowError(id) {
  const row = findRow(id);
  if (!(row instanceof HTMLElement)) {
    return null;
  }
  return row.querySelector('[data-role="row-error"]');
}

function findRow(id) {
  if (!(clarificationsBody instanceof HTMLElement)) {
    return null;
  }
  const rows = clarificationsBody.querySelectorAll("tr");
  for (const row of rows) {
    if (row instanceof HTMLElement && row.dataset.id === id) {
      return row;
    }
  }
  return null;
}

function applyRowBusy(busy) {
  if (!(clarificationsBody instanceof HTMLElement)) {
    return;
  }
  const rows = clarificationsBody.querySelectorAll("tr");
  for (const row of rows) {
    if (!(row instanceof HTMLElement)) {
      continue;
    }
    const locked = isLocked(row.dataset.status);
    const input = row.querySelector("input");
    if (input instanceof HTMLInputElement) {
      input.disabled = busy || locked;
    }
    const buttons = row.querySelectorAll("button");
    for (const button of buttons) {
      if (!(button instanceof HTMLButtonElement)) {
        continue;
      }
      if (button.dataset.action === "cancel") {
        button.disabled = busy;
      } else {
        button.disabled = busy || locked;
      }
    }
  }
}

function isLocked(status) {
  return status === "answered" || status === "skipped";
}

function toggleHidden(node, hidden) {
  if (node instanceof HTMLElement) {
    node.classList.toggle("d-none", hidden);
  }
}

async function postJson(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

async function saveDocx(response) {
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "protocol.docx";
  link.textContent = "protocol.docx";
  link.click();
  URL.revokeObjectURL(url);
}

function writeMarkdown(payload) {
  if (!(markdownArea instanceof HTMLTextAreaElement)) {
    return;
  }
  markdownArea.value = textField(payload, "text");
}

function showConnectionError() {
  showError({ error: { message: CONNECTION_ERROR } });
}

function showError(payload) {
  const details = formatError(payload && payload.error);
  setText(statusNode, "");
  setText(errorMessageNode, details.message);
  setDetail(errorLineNode, details.line);
  setDetail(errorExpectedNode, details.expected);
  setDetail(errorGotNode, details.got);
  if (errorNode instanceof HTMLElement) {
    errorNode.classList.remove("d-none");
  }
}

function formatError(error) {
  if (!error || typeof error.message !== "string") {
    return { message: "Запрос не выполнен." };
  }
  return {
    message: error.message,
    line: formatLine(error.line),
    expected: formatLabeled("Ожидалось", error.expected),
    got: formatLabeled("Получено", error.got),
  };
}

function formatLine(line) {
  if (typeof line !== "number") {
    return "";
  }
  return `Строка ${String(line)}.`;
}

function formatLabeled(label, value) {
  if (typeof value !== "string") {
    return "";
  }
  return `${label}: ${value}`;
}

function csrfValue() {
  if (csrfInput instanceof HTMLInputElement) {
    return csrfInput.value;
  }
  return "";
}

function fieldValue(node) {
  if (node instanceof HTMLTextAreaElement) {
    return node.value;
  }
  return "";
}

function textField(payload, name) {
  if (payload && typeof payload[name] === "string") {
    return payload[name];
  }
  return "";
}

function setText(node, value) {
  if (node instanceof HTMLElement) {
    node.textContent = value;
  }
}

function setDetail(node, value) {
  setText(node, value);
  if (node instanceof HTMLElement) {
    node.hidden = value === "";
  }
}

function clearMessages() {
  setText(statusNode, "");
  setText(errorMessageNode, "");
  setDetail(errorLineNode, "");
  setDetail(errorExpectedNode, "");
  setDetail(errorGotNode, "");
  if (errorNode instanceof HTMLElement) {
    errorNode.classList.add("d-none");
  }
  toggleHidden(unconfirmedNode, true);
}
