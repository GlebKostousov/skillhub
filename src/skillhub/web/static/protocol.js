"use strict";

(function () {
const csrfInput = document.querySelector("#protocol-csrf");
const transcriptArea = document.querySelector("#protocol-transcript");
const markdownArea = document.querySelector("#protocol-markdown");
const draftButton = document.querySelector("#protocol-draft-button");
const downloadButton = document.querySelector("#protocol-download-button");
const finalizeButton = document.querySelector("#protocol-finalize-button");
const reviseButton = document.querySelector("#protocol-revise-button");
const clarificationsList = document.querySelector("#protocol-clarifications-list");
const clarificationsEmpty = document.querySelector("#protocol-clarifications-empty");
const clarificationsLoading = document.querySelector("#protocol-clarifications-loading");
const unconfirmedNode = document.querySelector("#protocol-unconfirmed");
const unconfirmedList = document.querySelector("#protocol-unconfirmed-list");
const statusNode = document.querySelector("#protocol-status");
const errorNode = document.querySelector("#protocol-error");
const errorMessageNode = document.querySelector("#protocol-error-message");
const errorLineNode = document.querySelector("#protocol-error-line");
const errorExpectedNode = document.querySelector("#protocol-error-expected");
const errorGotNode = document.querySelector("#protocol-error-got");
const CONNECTION_ERROR = "Не получилось связаться. Проверьте сеть и попробуйте ещё раз.";
const EMPTY_ANSWER_ERROR = "Напишите ответ или пропустите вопрос.";
const STATUS_LABELS = {
  pending: "Ожидает",
  answered: "Принято",
  skipped: "Пропущено",
};
const draftAvailable =
  draftButton instanceof HTMLButtonElement && !draftButton.disabled;
const rowMemory = new Map();
let lastItems = [];
let tableLoaded = false;
let baseText = "";
let requestBusy = false;
let downloadBusy = false;

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

if (finalizeButton instanceof HTMLButtonElement) {
  finalizeButton.addEventListener("click", () => {
    void submitFinalize();
  });
}

if (reviseButton instanceof HTMLButtonElement) {
  reviseButton.addEventListener("click", () => {
    void submitRevise();
  });
}

window.SkillHubProtocol = {
  startFromDraft: startFromDraft,
};

function startFromDraft(text, transcript) {
  const panel = document.querySelector("#protocol-followup");
  if (panel instanceof HTMLElement) {
    panel.classList.remove("d-none");
  }
  toggleHidden(clarificationsLoading, false);
  toggleHidden(clarificationsList, true);
  toggleHidden(clarificationsEmpty, true);
  if (markdownArea instanceof HTMLTextAreaElement && typeof text === "string") {
    markdownArea.value = text;
  }
  if (
    transcriptArea instanceof HTMLTextAreaElement &&
    typeof transcript === "string"
  ) {
    transcriptArea.value = transcript;
  }
  return submitClarifications();
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
  if (downloadBusy) {
    return;
  }
  if (fieldValue(markdownArea) === "") {
    showError({ error: { message: "Сначала нужен готовый протокол." } });
    return;
  }
  clearMessages();
  downloadBusy = true;
  syncButtons();
  setText(statusNode, "Собираю файл Word…");
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
    setText(statusNode, "Файл Word сохранён.");
  } catch {
    showConnectionError();
  } finally {
    downloadBusy = false;
    syncButtons();
  }
}

function setBusy(busy, status) {
  requestBusy = busy;
  applyRowBusy(busy);
  syncButtons();
  const panel = document.querySelector("#protocol-followup");
  if (panel instanceof HTMLElement) {
    panel.toggleAttribute("data-busy", busy);
  }
  if (typeof status === "string") {
    setText(statusNode, status);
  }
}

function syncButtons() {
  if (draftButton instanceof HTMLButtonElement) {
    draftButton.disabled = requestBusy || !draftAvailable;
  }
  if (downloadButton instanceof HTMLButtonElement) {
    downloadButton.disabled = downloadBusy;
  }
  if (finalizeButton instanceof HTMLButtonElement) {
    finalizeButton.disabled = requestBusy || downloadBusy || !allResolved();
  }
  if (reviseButton instanceof HTMLButtonElement) {
    reviseButton.disabled = requestBusy || downloadBusy || !allResolved();
  }
}

function allResolved() {
  if (!tableLoaded || lastItems.length === 0) {
    return false;
  }
  for (const item of lastItems) {
    if (isLocked(item.status) && item.status === "skipped") {
      continue;
    }
    const input = rowInput(item.id);
    if (input instanceof HTMLInputElement && input.value.trim() !== "") {
      continue;
    }
    return false;
  }
  return true;
}

async function submitFinalize() {
  if (!allResolved()) {
    return;
  }
  clearMessages();
  setBusy(true, "Записываю ответы…");
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
    rowMemory.clear();
    lastItems = [];
    tableLoaded = true;
    renderClarifications(lastItems);
    setText(statusNode, "Готово, ответы в протоколе.");
  } catch {
    showConnectionError();
  } finally {
    setBusy(false);
  }
}

async function submitRevise() {
  if (!allResolved()) {
    return;
  }
  clearMessages();
  setBusy(true, "Собираю протокол заново…");
  try {
    const response = await postJson("/protocol/revise", {
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
    rowMemory.clear();
    lastItems = [];
    tableLoaded = true;
    renderClarifications(lastItems);
    setText(statusNode, "Готово. Протокол переписан с вашими ответами.");
  } catch {
    showConnectionError();
  } finally {
    setBusy(false);
  }
}

function finalizeAnswers() {
  const answers = [];
  for (const item of lastItems) {
    const memory = rowMemory.get(item.id);
    if (memory && memory.status === "skipped") {
      answers.push({ id: item.id, action: "skip" });
      continue;
    }
    const input = rowInput(item.id);
    const value = input instanceof HTMLInputElement ? input.value.trim() : "";
    answers.push({ id: item.id, action: "answer", value });
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
  toggleHidden(unconfirmedNode, items.length === 0);
}

async function submitClarifications() {
  clearMessages();
  setBusy(true, "Собираю вопросы…");
  try {
    const loaded = await refreshClarifications();
    if (loaded) {
      setText(
        statusNode,
        lastItems.length === 0
          ? "Похоже, всё уже на месте. Можно сразу сохранить в Word."
          : "Ответьте или пропустите вопрос, затем запишите ответы или соберите протокол заново.",
      );
    }
  } catch {
    showConnectionError();
  } finally {
    toggleHidden(clarificationsLoading, true);
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

function skipAnswer(item) {
  persistDrafts();
  clearMessages();
  clearRowError(item.id);
  rememberDecision(item, "skip", "");
  lastItems = mergeRows(lastItems);
  renderClarifications(lastItems);
  setText(statusNode, "Этот вопрос пропущен. Остальные можно заполнить и записать.");
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

function cancelChoice(id) {
  if (!rowMemory.has(id)) {
    return;
  }
  persistDrafts();
  rowMemory.delete(id);
  clearMessages();
  lastItems = mergeRows(lastItems);
  renderClarifications(lastItems);
  setText(statusNode, "Вопрос снова в списке.");
}

function persistDrafts() {
  for (const item of lastItems) {
    if (isLocked(item.status)) {
      continue;
    }
    const input = rowInput(item.id);
    if (!(input instanceof HTMLInputElement)) {
      continue;
    }
    rememberDraft(item, input.value);
  }
}

function rememberDraft(item, value) {
  if (value.trim() === "") {
    const memory = rowMemory.get(item.id);
    if (memory && memory.status === "pending") {
      rowMemory.delete(item.id);
    }
    return;
  }
  rowMemory.set(item.id, {
    item: {
      id: item.id,
      target: item.target,
      reason: item.reason,
      hint: item.hint,
    },
    status: "pending",
    value,
  });
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
  collectIds(ids, Array.from(rowMemory.keys()).map((key) => ({ id: key })));
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
      rows.push({
        id: server.id,
        target: server.target,
        reason: server.reason,
        hint: server.hint,
        status: "pending",
      });
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
  if (!(clarificationsList instanceof HTMLElement)) {
    return;
  }
  toggleHidden(clarificationsLoading, true);
  while (clarificationsList.firstChild) {
    clarificationsList.removeChild(clarificationsList.firstChild);
  }
  const hasItems = items.length > 0;
  toggleHidden(clarificationsList, !hasItems);
  toggleHidden(clarificationsEmpty, !tableLoaded || hasItems);
  for (const item of items) {
    clarificationsList.appendChild(buildGap(item));
  }
  syncButtons();
}

function buildGap(item) {
  const card = document.createElement("article");
  card.className = "protocol-gap";
  card.dataset.id = item.id;
  card.dataset.status = item.status;
  const title = document.createElement("h3");
  title.className = "h6 mb-1";
  title.textContent = gapTitle(item);
  const reason = document.createElement("p");
  reason.className = "protocol-gap-reason text-body-secondary mb-3";
  reason.textContent = typeof item.reason === "string" ? item.reason : "";
  card.appendChild(title);
  card.appendChild(reason);
  card.appendChild(answerBlock(item));
  card.appendChild(actionsBlock(item));
  return card;
}

function gapInputType(item) {
  const target = typeof item.target === "string" ? item.target : "";
  if (target === "date" || target.endsWith(":due")) {
    return "date";
  }
  return "text";
}

function gapTitle(item) {
  const target = typeof item.target === "string" ? item.target : "";
  if (target === "date") {
    return "Дата встречи";
  }
  if (target === "participants") {
    return "Участники";
  }
  if (target.endsWith(":assignee")) {
    return "Ответственный";
  }
  if (target.endsWith(":due")) {
    return "Срок";
  }
  if (target.startsWith("question:")) {
    return "Открытый вопрос";
  }
  return typeof item.reason === "string" ? item.reason : "Вопрос";
}

function answerBlock(item) {
  const wrap = document.createElement("div");
  const input = document.createElement("input");
  input.type = gapInputType(item);
  input.className = "form-control";
  input.setAttribute("aria-label", gapTitle(item));
  if (typeof item.hint === "string") {
    input.placeholder = item.hint;
  }
  const memory = rowMemory.get(item.id);
  if (memory && typeof memory.value === "string") {
    input.value = memory.value;
  }
  input.disabled = requestBusy || isLocked(item.status);
  input.addEventListener("input", () => {
    clearRowError(item.id);
    rememberDraft(item, input.value);
    syncButtons();
  });
  const error = document.createElement("p");
  error.className = "text-danger small mb-0 mt-1 d-none";
  error.dataset.role = "row-error";
  wrap.appendChild(input);
  wrap.appendChild(error);
  return wrap;
}

function actionsBlock(item) {
  const cell = document.createElement("div");
  cell.className = "protocol-gap-actions";
  const locked = isLocked(item.status);
  const skip = actionButton("Пропустить", "skip", "btn btn-outline-secondary");
  const cancel = actionButton(
    "Вернуть вопрос",
    "cancel",
    "btn btn-outline-primary",
  );
  skip.disabled = requestBusy || locked;
  skip.hidden = locked;
  cancel.hidden = !locked;
  cancel.disabled = requestBusy;
  skip.addEventListener("click", () => {
    skipAnswer(item);
  });
  cancel.addEventListener("click", () => {
    cancelChoice(item.id);
  });
  cell.appendChild(skip);
  cell.appendChild(cancel);
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
  if (!(clarificationsList instanceof HTMLElement)) {
    return null;
  }
  const rows = clarificationsList.querySelectorAll("[data-id]");
  for (const row of rows) {
    if (row instanceof HTMLElement && row.dataset.id === id) {
      return row;
    }
  }
  return null;
}

function applyRowBusy(busy) {
  if (!(clarificationsList instanceof HTMLElement)) {
    return;
  }
  const rows = clarificationsList.querySelectorAll("[data-id]");
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
  link.rel = "noopener";
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  window.setTimeout(() => {
    URL.revokeObjectURL(url);
    link.remove();
  }, 2000);
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
  toggleHidden(clarificationsLoading, true);
  setText(statusNode, "");
  setText(errorMessageNode, details.message);
  setDetail(errorLineNode, details.line);
  setDetail(errorExpectedNode, details.expected);
  setDetail(errorGotNode, details.got);
  if (errorNode instanceof HTMLElement) {
    errorNode.classList.remove("d-none");
    errorNode.scrollIntoView({ block: "nearest" });
  }
}

function formatError(error) {
  if (!error || typeof error.message !== "string") {
    return { message: "Не получилось выполнить запрос." };
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
  if (csrfInput instanceof HTMLInputElement && csrfInput.value !== "") {
    return csrfInput.value;
  }
  const assistant = document.querySelector("[data-assistant-form] [name='csrf_token']");
  if (assistant instanceof HTMLInputElement) {
    return assistant.value;
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
})();
