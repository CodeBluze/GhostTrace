const state = { cases: [], selected: null, status: "", query: "", index: -1 };
const $ = (selector) => document.querySelector(selector);
const esc = (value = "") => String(value).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));
const date = value => value ? new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
const statusClass = value => value.toLowerCase();

async function api(path) {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json();
}

async function init() {
  const [summary, cases] = await Promise.all([api("/api/summary"), api("/api/cases")]);
  $("#metricTotal").textContent = summary.total;
  $("#metricIncomplete").textContent = summary.counts.INCOMPLETE;
  $("#metricEvidence").textContent = summary.evidenceFiles;
  $("#metricObservations").textContent = summary.observations.toLocaleString();
  state.cases = cases;
  renderCases();
  bind();
  if (cases.length) selectCase(cases[0].caseId);
}

function bind() {
  $("#caseSearch").addEventListener("input", event => { state.query = event.target.value; renderCases(); });
  $("#statusFilters").addEventListener("click", event => {
    const button = event.target.closest("button"); if (!button) return;
    state.status = button.dataset.status;
    document.querySelectorAll("#statusFilters button").forEach(item => item.classList.toggle("active", item === button));
    renderCases();
  });
  $("#themeButton").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme === "dark";
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    localStorage.setItem("ghosttrace-theme", dark ? "light" : "dark");
  });
  const theme = localStorage.getItem("ghosttrace-theme"); if (theme) document.documentElement.dataset.theme = theme;
  document.addEventListener("keydown", event => {
    if (event.key === "/" && document.activeElement !== $("#caseSearch")) { event.preventDefault(); $("#caseSearch").focus(); }
    if (["j", "k"].includes(event.key) && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) {
      const visible = filteredCases(); if (!visible.length) return;
      event.preventDefault(); state.index = Math.max(0, Math.min(visible.length - 1, state.index + (event.key === "j" ? 1 : -1)));
      selectCase(visible[state.index].caseId);
    }
  });
}

function filteredCases() {
  const needle = state.query.toLowerCase().trim();
  return state.cases.filter(item => (!state.status || item.status === state.status) && (!needle || `${item.caseId} ${item.email}`.toLowerCase().includes(needle)));
}

function renderCases() {
  const cases = filteredCases(); $("#caseCount").textContent = cases.length;
  $("#caseList").innerHTML = cases.map((item, index) => `
    <button class="case-card ${item.caseId === state.selected ? "active" : ""}" data-id="${esc(item.caseId)}">
      <span class="case-line"><span class="case-id">${esc(item.caseId)}</span><span class="status-dot ${statusClass(item.status)}"></span></span>
      <span class="case-email">${esc(item.email)}</span>
      <span class="case-meta"><span>${item.stepCount} steps</span><span>${Math.round(item.confidence * 100)}% grounded</span></span>
    </button>`).join("") || `<p class="muted-placeholder">No cases match this view.</p>`;
  document.querySelectorAll(".case-card").forEach((button, index) => button.addEventListener("click", () => { state.index = index; selectCase(button.dataset.id); }));
}

async function selectCase(caseId) {
  state.selected = caseId; renderCases();
  const data = await api(`/api/cases/${caseId}`);
  renderCase(data);
  document.querySelector(`.case-card[data-id="${caseId}"]`)?.scrollIntoView({ block: "nearest" });
}

function renderCase(data) {
  const { case: item, decision, observations } = data;
  $("#caseEyebrow").textContent = `${item.caseId} · ${decision.userId || "unresolved subject"}`;
  $("#caseTitle").textContent = item.email;
  $("#caseSubtitle").textContent = `State reconstructed at ${date(item.auditAt)} from cutoff-eligible evidence.`;
  $("#verdictWrap").hidden = false; $("#verdict").textContent = decision.status;
  $("#verdict").className = `verdict ${statusClass(decision.status)}`;
  const details = [
    ["Audit cutoff", date(item.auditAt)], ["Reported state", item.reportedStatus],
    ["Resolved subject", decision.userId || "Unresolved"], ["Confidence", `${Math.round(decision.confidence * 100)}%`],
  ];
  $("#caseDetails").innerHTML = details.map(([key, value]) => `<div><dt>${esc(key)}</dt><dd title="${esc(value)}">${esc(value)}</dd></div>`).join("");
  $("#requestText").textContent = item.requestText;
  renderSteps(decision.steps); renderEvidence(observations); renderGraph(decision, observations);
  $("#warningsSection").hidden = !decision.warnings.length;
  $("#warningList").innerHTML = decision.warnings.map(value => `<div class="warning-card">${esc(value)}</div>`).join("");
}

function renderSteps(steps) {
  $("#stepCount").textContent = `${steps.length} ${steps.length === 1 ? "step" : "steps"}`;
  $("#steps").innerHTML = steps.length ? steps.map(step => `
    <article class="step"><span class="step-no">${String(step.step_no).padStart(2, "0")}</span>
      <span class="phase ${step.phase.toLowerCase()}">${esc(step.phase)}</span>
      <span class="step-main"><strong>${esc(step.operation.replaceAll("_", " "))}</strong><span>${esc(step.system_id)} / ${esc(step.resource_id)} → ${esc(step.state)}</span></span>
      <span class="evidence-chip">${esc(step.evidence_id || "future check")}</span>
    </article>`).join("") : `<p class="muted-placeholder">No actions are authorized for this request.</p>`;
}

function renderEvidence(observations) {
  $("#observationCount").textContent = observations.length; $("#evidenceList").innerHTML = observations.length ? observations.slice(-24).reverse().map(item => `
    <article class="evidence-row"><header><strong>${esc(item.resourceId)}</strong><span class="state ${statusClass(item.state)}">${esc(item.state)}</span></header>
      <p>${esc(item.systemId)} · ${esc(item.category)}</p><small>${esc(item.evidenceId)} · ${date(item.observedAt)}</small></article>`).join("") : `<p class="muted-placeholder">No cutoff-eligible observations.</p>`;
}

function renderGraph(decision, observations) {
  const latest = [...new Map(observations.map(item => [`${item.systemId}/${item.resourceId}`, item])).values()].slice(-11);
  if (!latest.length) { $("#graphStage").innerHTML = `<div class="empty-state"><span class="empty-orbit"></span><strong>No grounded resources</strong><p>Review the uncertainty ledger.</p></div>`; return; }
  const width = 820, height = 310, cx = width / 2, cy = height / 2, radius = 112;
  const points = latest.map((item, index) => ({ item, x: cx + Math.cos((index / latest.length) * Math.PI * 2 - Math.PI / 2) * (radius + (index % 2) * 34), y: cy + Math.sin((index / latest.length) * Math.PI * 2 - Math.PI / 2) * (radius + (index % 2) * 34) }));
  const safe = new Set(["ABSENT", "REDACTED", "ANONYMIZED", "DISABLED", "BLOCKED"]);
  $("#graphStage").innerHTML = `<svg class="graph-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Identity to evidence graph">
    ${points.map(p => `<line x1="${cx}" y1="${cy}" x2="${p.x}" y2="${p.y}" />`).join("")}
    <g class="node subject"><circle cx="${cx}" cy="${cy}" r="42"/><text x="${cx}" y="${cy - 2}">${esc(decision.userId || "subject")}</text><text class="sub" x="${cx}" y="${cy + 13}">${esc(decision.status)}</text></g>
    ${points.map(p => `<g class="node ${safe.has(p.item.state) ? "safe" : "risk"}"><circle cx="${p.x}" cy="${p.y}" r="30"/><text x="${p.x}" y="${p.y - 2}">${esc(p.item.resourceId)}</text><text class="sub" x="${p.x}" y="${p.y + 12}">${esc(p.item.state)}</text></g>`).join("")}
  </svg>`;
}

init().catch(error => { console.error(error); $("#caseList").innerHTML = `<p class="muted-placeholder">Unable to load GhostTrace.</p>`; });

