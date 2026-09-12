"use strict";

const csrfInput = document.querySelector("#protocol-csrf");
const transcriptArea = document.querySelector("#protocol-transcript");
const markdownArea = document.querySelector("#protocol-markdown");
const draftButton = document.querySelector("#protocol-draft-button");
const downloadButton = document.querySelector("#protocol-download-button");
const statusNode = document.querySelector("#protocol-status");
const errorNode = document.querySelector("#protocol-error");

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
  clearMessages();
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
}

async function submitDownload() {
  clearMessages();
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

function showError(payload) {
  setText(errorNode, formatError(payload && payload.error));
}

function formatError(error) {
  if (!error || typeof error.message !== "string") {
    return "Запрос не выполнен.";
  }
  if (typeof error.line === "number") {
    return `${error.message} Строка ${String(error.line)}.`;
  }
  return error.message;
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

function clearMessages() {
  setText(statusNode, "");
  setText(errorNode, "");
}
