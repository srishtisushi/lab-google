const form = document.querySelector("#search-form");
const resultsBody = document.querySelector("#results");
const status = document.querySelector("#status");
const warnings = document.querySelector("#warnings");
const submit = document.querySelector("#submit");
const download = document.querySelector("#download");
const resumeInput = document.querySelector("#resume");
const resumeStatus = document.querySelector("#resume-status");
const draftDialog = document.querySelector("#draft-dialog");
const draftRecipient = document.querySelector("#draft-recipient");
const draftError = document.querySelector("#draft-error");
const draftApiKey = document.querySelector("#draft-api-key");
const draftTone = document.querySelector("#draft-tone");
const generateDraft = document.querySelector("#generate-draft");
const draftSubject = document.querySelector("#draft-subject");
const draftBody = document.querySelector("#draft-body");
const copyDraft = document.querySelector("#copy-draft");
let latestRows = [];
let resumeUpload = null;
let activeDraftRow = null;
let activeDraftButton = null;

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = form.query.value.trim();
  const limit = Number(form.limit.value || 8);
  setBusy(true, "Searching projects and email-bearing records...");
  warnings.hidden = true;
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        limit,
        enrich: false,
        prestigious: form.prestigious.checked,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Search failed.");
    }
    latestRows = payload.results || [];
    renderRows(latestRows, payload.count === 0);
    renderWarnings(payload.warnings || []);
    status.textContent = `${payload.count} result${payload.count === 1 ? "" : "s"} for "${payload.query}".`;
    download.disabled = latestRows.length === 0;
  } catch (error) {
    latestRows = [];
    renderRows([]);
    status.textContent = error.message;
    download.disabled = true;
  } finally {
    setBusy(false);
  }
});

download.addEventListener("click", () => {
  const headers = [
    "Person / PI",
    "Title",
    "Institution",
    "Undergraduate or medical student researchers",
    "Email",
    "Research synopsis",
    "Project title",
    "Source",
    "Lab page",
  ];
  const rows = latestRows.map((row) => [
    row.name,
    row.title,
    row.institution,
    row.student_researchers,
    row.email,
    row.synopsis,
    row.project_title,
    row.source_url,
    row.lab_page,
  ]);
  const csv = [headers, ...rows].map((row) => row.map(csvCell).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "lab-google-results.csv";
  link.click();
  URL.revokeObjectURL(link.href);
});

resumeInput.addEventListener("change", async () => {
  const file = resumeInput.files[0];
  if (!file) {
    return;
  }
  if (!file.name.toLowerCase().endsWith(".pdf") || file.type && file.type !== "application/pdf") {
    resumeUpload = null;
    resumeInput.value = "";
    resumeStatus.textContent = "Upload a PDF resume or CV.";
    updateDraftButtons();
    return;
  }
  if (file.size > 10_000_000) {
    resumeUpload = null;
    resumeInput.value = "";
    resumeStatus.textContent = "Keep the resume PDF under 10 MB.";
    updateDraftButtons();
    return;
  }
  resumeStatus.textContent = "Reading resume...";
  resumeUpload = {
    filename: file.name,
    file_data: await fileToBase64(file),
  };
  resumeStatus.textContent = `${file.name} is ready for drafts in this page session.`;
  updateDraftButtons();
});

resultsBody.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-draft-index]");
  if (!button) {
    return;
  }
  const row = latestRows[Number(button.dataset.draftIndex)];
  if (!row) {
    return;
  }
  if (!resumeUpload) {
    resumeStatus.textContent = "Upload one PDF resume or CV before drafting.";
    resumeInput.click();
    return;
  }
  openDraftDialog(row, button);
});

generateDraft.addEventListener("click", async () => {
  if (!activeDraftRow || !activeDraftButton) {
    return;
  }
  if (!draftApiKey.value.trim()) {
    setDraftError("Enter your OpenAI API key before drafting.");
    draftApiKey.focus();
    return;
  }
  await createDraft(activeDraftRow, activeDraftButton);
});

copyDraft.addEventListener("click", async () => {
  const draft = `Subject: ${draftSubject.value}\n\n${draftBody.value}`;
  await navigator.clipboard.writeText(draft);
  copyDraft.textContent = "Copied";
  window.setTimeout(() => {
    copyDraft.textContent = "Copy draft";
  }, 1200);
});

function setBusy(isBusy, message = "") {
  submit.disabled = isBusy;
  submit.querySelector("span:last-child").textContent = isBusy ? "Searching" : "Search";
  if (message) {
    status.textContent = message;
  }
}

function renderWarnings(messages) {
  if (!messages.length) {
    warnings.hidden = true;
    warnings.textContent = "";
    return;
  }
  warnings.hidden = false;
  warnings.textContent = messages.join(" ");
}

function renderRows(rows, emailMiss = false) {
  if (!rows.length) {
    const message = emailMiss
      ? "No rows with a public email were found for this search."
      : "No rows returned yet.";
    resultsBody.innerHTML = `<tr class="empty"><td colspan="7">${message}</td></tr>`;
    return;
  }
  resultsBody.innerHTML = rows
    .map(
      (row, index) => `
      <tr>
        <td>
          ${link(row.name, row.lab_page || row.source_url)}
          <span class="source">${escapeHtml(row.source_type)} source</span>
        </td>
        <td>${escapeHtml(row.title)}</td>
        <td>${escapeHtml(row.institution)}</td>
        <td>${escapeHtml(row.student_researchers)}</td>
        <td>${emailCell(row.email)}</td>
        <td>
          ${escapeHtml(row.synopsis)}
          <span class="source">${link(row.project_title || "Source record", row.source_url)}</span>
        </td>
        <td>
          <button class="draft-button" type="button" data-draft-index="${index}" ${resumeUpload ? "" : "disabled"}>
            Draft email
          </button>
        </td>
      </tr>`
    )
    .join("");
}

function openDraftDialog(row, button) {
  activeDraftRow = row;
  activeDraftButton = button;
  draftRecipient.textContent = `For ${row.name} at ${row.institution}`;
  draftApiKey.value = "";
  draftTone.value = "warm";
  draftSubject.value = "";
  draftBody.value = "";
  copyDraft.disabled = true;
  setDraftError("");
  draftDialog.showModal();
}

async function createDraft(row, button) {
  button.disabled = true;
  button.textContent = "Drafting";
  generateDraft.disabled = true;
  generateDraft.textContent = "Generating";
  draftSubject.value = "";
  draftBody.value = "";
  copyDraft.disabled = true;
  setDraftError("");
  try {
    const response = await fetch("/api/draft-email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resume: resumeUpload,
        researcher: row,
        interest: form.query.value.trim(),
        api_key: draftApiKey.value.trim(),
        tone: draftTone.value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Drafting failed.");
    }
    draftSubject.value = payload.subject;
    draftBody.value = payload.body;
    copyDraft.disabled = false;
  } catch (error) {
    setDraftError(error.message);
  } finally {
    draftApiKey.value = "";
    button.textContent = "Draft email";
    button.disabled = false;
    generateDraft.textContent = "Generate draft";
    generateDraft.disabled = false;
  }
}

function updateDraftButtons() {
  for (const button of resultsBody.querySelectorAll("[data-draft-index]")) {
    button.disabled = !resumeUpload;
  }
}

function setDraftError(message) {
  draftError.hidden = !message;
  draftError.textContent = message;
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(String(reader.result).split(",", 2)[1]));
    reader.addEventListener("error", () => reject(new Error("Could not read the resume PDF.")));
    reader.readAsDataURL(file);
  });
}

function link(label, url) {
  if (!url) {
    return escapeHtml(label);
  }
  return `<a href="${escapeAttribute(url)}" target="_blank" rel="noreferrer">${escapeHtml(label)}</a>`;
}

function emailCell(email) {
  if (!email || email === "Not found") {
    return "Not found";
  }
  return `<a href="mailto:${escapeAttribute(email)}">${escapeHtml(email)}</a>`;
}

function csvCell(value) {
  return `"${String(value || "").replaceAll('"', '""')}"`;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}
