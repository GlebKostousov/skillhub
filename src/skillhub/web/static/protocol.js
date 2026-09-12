"use strict";

const csrfInput = document.querySelector("#protocol-csrf");
const transcriptArea = document.querySelector("#protocol-transcript");
const markdownArea = document.querySelector("#protocol-markdown");
const draftButton = document.querySelector("#protocol-draft-button");
const downloadButton = document.querySelector("#protocol-download-button");
const statusNode = document.querySelector("#protocol-status");
const errorNode = document.querySelector("#protocol-error");
const errorMessageNode = document.querySelector("#protocol-error-message");
const errorLineNode = document.querySelector("#protocol-error-line");
const errorExpectedNode = document.querySelector("#protocol-error-expected");
const errorGotNode = document.querySelector("#protocol-error-got");
const CONNECTION_ERROR = "Запрос не выполнен. Проверьте соединение и повторите.";
const draftAvailable =
  draftButton instanceof HTMLButtonElement && !draftButton.disabled;

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
  if (typeof status === "string") {
    setText(statusNode, status);
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
}
