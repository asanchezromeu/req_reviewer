import { parseRequirementFile, parseJsonRequirements } from "../src/parse-requirements.mjs";

const state = {
  requirements: [],
  review: null
};

const elements = {
  fileInput: document.querySelector("#fileInput"),
  jsonInput: document.querySelector("#jsonInput"),
  loadPasted: document.querySelector("#loadPasted"),
  loadSample: document.querySelector("#loadSample"),
  downloadCsvTemplate: document.querySelector("#downloadCsvTemplate"),
  downloadJsonTemplate: document.querySelector("#downloadJsonTemplate"),
  reviewButton: document.querySelector("#reviewButton"),
  exportButton: document.querySelector("#exportButton"),
  requirementsBody: document.querySelector("#requirementsBody"),
  status: document.querySelector("#status"),
  model: document.querySelector("#model"),
  projectContext: document.querySelector("#projectContext"),
  scoreList: document.querySelector("#scoreList"),
  conflictList: document.querySelector("#conflictList"),
  promptDialog: document.querySelector("#promptDialog"),
  promptText: document.querySelector("#promptText"),
  showPrompt: document.querySelector("#showPrompt"),
  closePrompt: document.querySelector("#closePrompt"),
  metricCount: document.querySelector("#metricCount"),
  metricAverage: document.querySelector("#metricAverage"),
  metricWeak: document.querySelector("#metricWeak"),
  metricConflicts: document.querySelector("#metricConflicts")
};

const sample = {
  requirements: [
    {
      id: "REQ-001",
      source: "SN-1",
      parents: ["SN-1"],
      text: "The camera system shall transmit image data continuously when vehicle speed is between 0 km/h and 200 km/h."
    },
    {
      id: "REQ-002",
      source: "SN-1",
      parents: ["SN-1"],
      text: "The camera system shall not transmit image data when vehicle speed is greater than 120 km/h."
    }
  ]
};

const templateRequirements = [
  {
    id: "REQ-001",
    domain: "Zone Controller",
    component: "Power Input",
    requirement_type: "System Requirement",
    asil: "B",
    source: "SN-001",
    parents: ["SN-001"],
    text: "The zone controller shall tolerate reverse battery connection of -14 V for 60 s without permanent damage."
  },
  {
    id: "REQ-002",
    domain: "Zone Controller",
    component: "Diagnostics",
    requirement_type: "System Requirement",
    asil: "B",
    source: "SN-002",
    parents: ["SN-002"],
    text: "The zone controller shall store diagnostic trouble code ZC_OC_01 within 1 s after detecting an overcurrent condition on any protected high-side output."
  }
];

const csvTemplate = [
  "id,domain,component,requirement_type,asil,source,parents,text",
  ...templateRequirements.map(requirement => [
    requirement.id,
    requirement.domain,
    requirement.component,
    requirement.requirement_type,
    requirement.asil,
    requirement.source,
    requirement.parents.join(";"),
    requirement.text
  ].map(csvCell).join(","))
].join("\n");

const jsonTemplate = JSON.stringify({ requirements: templateRequirements }, null, 2);

elements.fileInput.addEventListener("change", async event => {
  const file = event.target.files?.[0];
  if (!file) {
    return;
  }

  try {
    const text = await file.text();
    setRequirements(parseRequirementFile(file.name, text), `Loaded ${file.name}.`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

elements.loadPasted.addEventListener("click", () => {
  try {
    setRequirements(parseJsonRequirements(elements.jsonInput.value), "Loaded pasted JSON.");
  } catch (error) {
    setStatus(error.message, true);
  }
});

elements.loadSample.addEventListener("click", () => {
  elements.jsonInput.value = JSON.stringify(sample, null, 2);
  setRequirements(parseJsonRequirements(elements.jsonInput.value), "Loaded sample contradiction.");
});

elements.downloadCsvTemplate.addEventListener("click", () => {
  downloadText("requirements_template.csv", csvTemplate, "text/csv");
});

elements.downloadJsonTemplate.addEventListener("click", () => {
  downloadText("requirements_template.json", jsonTemplate, "application/json");
});

elements.reviewButton.addEventListener("click", async () => {
  elements.reviewButton.disabled = true;
  setStatus("Reviewing with Ollama...");

  try {
    const response = await fetch("/api/review", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        requirements: state.requirements,
        projectContext: elements.projectContext.value,
        model: elements.model.value
      })
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Review failed.");
    }

    state.review = payload;
    renderReview();
    setStatus("Review complete.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    elements.reviewButton.disabled = state.requirements.length === 0;
  }
});

elements.exportButton.addEventListener("click", () => {
  if (!state.review) {
    return;
  }
  const blob = new Blob([JSON.stringify(state.review, null, 2)], { type: "application/json" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "requirement-review.json";
  link.click();
  URL.revokeObjectURL(link.href);
});

elements.showPrompt.addEventListener("click", async () => {
  const response = await fetch("/api/prompt");
  const payload = await response.json();
  elements.promptText.textContent = payload.prompt || "";
  elements.promptDialog.showModal();
});

elements.closePrompt.addEventListener("click", () => {
  elements.promptDialog.close();
});

function setRequirements(requirements, message) {
  state.requirements = requirements;
  state.review = null;
  elements.reviewButton.disabled = requirements.length === 0;
  elements.exportButton.disabled = true;
  renderRequirements();
  resetReview();
  setStatus(`${message} ${requirements.length} requirement(s) ready.`);
}

function renderRequirements() {
  elements.requirementsBody.replaceChildren(
    ...state.requirements.map(requirement => {
      const row = document.createElement("tr");
      row.append(
        cell(requirement.id),
        cell(requirement.text),
        cell([requirement.source, requirement.parents?.join?.(", ")].filter(Boolean).join(" / "))
      );
      return row;
    })
  );
  elements.metricCount.textContent = String(state.requirements.length);
}

function renderReview() {
  const review = state.review;
  elements.exportButton.disabled = false;
  elements.metricCount.textContent = String(review.summary.requirementCount);
  elements.metricAverage.textContent = `${review.summary.averageScore}%`;
  elements.metricWeak.textContent = String(review.summary.belowThresholdCount);
  elements.metricConflicts.textContent = String(review.summary.conflictCount);

  elements.scoreList.className = "score-list";
  elements.scoreList.replaceChildren(
    ...review.requirements.map(row => {
      const card = document.createElement("article");
      card.className = `score-card ${row.score < 85 ? "weak" : ""}`;
      card.innerHTML = `
        <div class="card-line"><strong>${escapeHtml(row.id)}</strong><strong>${row.score}%</strong></div>
        ${recommendationDropdown(row)}
        <div class="tags">${row.flags.map(flag => `<span class="tag">${escapeHtml(flag)}</span>`).join("")}</div>
      `;
      return card;
    })
  );

  if (review.conflicts.length) {
    elements.conflictList.className = "conflict-list";
    elements.conflictList.replaceChildren(...review.conflicts.map(conflictCard));
  } else {
    elements.conflictList.className = "conflict-list empty";
    elements.conflictList.textContent = "No conflicts reported.";
  }
}

function conflictCard(conflict) {
  const card = document.createElement("article");
  card.className = `conflict-card ${conflict.severity}`;
  card.innerHTML = `
    <div class="card-line">
      <strong>${escapeHtml(conflict.type)}</strong>
      <span>${escapeHtml(conflict.requirementIds.join(", "))}</span>
    </div>
    <p>${escapeHtml(conflict.evidence)}</p>
    <p><strong>Mitigation:</strong> ${escapeHtml(conflict.mitigation)}</p>
  `;
  return card;
}

function recommendationDropdown(row) {
  if (row.score >= 85 || !row.improvement) {
    return "";
  }

  return `
    <details class="recommendation">
      <summary>Recommendations</summary>
      <p>${escapeHtml(row.improvement)}</p>
    </details>
  `;
}

function resetReview() {
  elements.metricAverage.textContent = "-";
  elements.metricWeak.textContent = "0";
  elements.metricConflicts.textContent = "0";
  elements.scoreList.className = "score-list empty";
  elements.scoreList.textContent = "Run a review to see scores.";
  elements.conflictList.className = "conflict-list empty";
  elements.conflictList.textContent = "No review results yet.";
}

function setStatus(message, isError = false) {
  elements.status.textContent = message;
  elements.status.style.color = isError ? "var(--danger)" : "var(--muted)";
}

function cell(value) {
  const td = document.createElement("td");
  td.textContent = value || "";
  return td;
}

function downloadText(filename, text, mimeType) {
  const blob = new Blob([text], { type: `${mimeType};charset=utf-8` });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}

function csvCell(value) {
  const text = String(value ?? "");
  return `"${text.replaceAll('"', '""')}"`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
