const form = document.querySelector("#search-form");
const resultsBody = document.querySelector("#results");
const status = document.querySelector("#status");
const warnings = document.querySelector("#warnings");
const submit = document.querySelector("#submit");
const download = document.querySelector("#download");
let latestRows = [];

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = form.query.value.trim();
  const limit = Number(form.limit.value || 8);
  setBusy(true, "Searching projects and likely lab pages...");
  warnings.hidden = true;
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query,
        limit,
        enrich: form.enrich.checked,
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
    resultsBody.innerHTML = `<tr class="empty"><td colspan="6">${message}</td></tr>`;
    return;
  }
  resultsBody.innerHTML = rows
    .map(
      (row) => `
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
      </tr>`
    )
    .join("");
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
