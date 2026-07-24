// Übersichtsseite: alle Annotationen quellenübergreifend, mit Filtern.

const TYPE_META = {
  note: { icon: "📝", label: "Notizen" },
  todo: { icon: "☑", label: "Todos" },
  label: { icon: "🏷", label: "Labels" },
  handover: { icon: "➦", label: "Übergaben" },
};
const TYPE_LABEL_SINGULAR = { note: "Notiz", todo: "Todo", label: "Label", handover: "Übergabe" };

const state = { type: null, source_id: null, label: null, assignee: null, done: null, q: "" };

const typeChipsEl = document.getElementById("type-chips");
const labelChipsEl = document.getElementById("label-chips");
const sourceSel = document.getElementById("ov-source");
const qInput = document.getElementById("ov-q");
const openTodosBtn = document.getElementById("ov-open-todos");
const toMeBtn = document.getElementById("ov-to-me");
const resultsEl = document.getElementById("ov-results");
const countEl = document.getElementById("ov-count");

function renderTypeChips() {
  const types = [["", "Alle"], ...Object.entries(TYPE_META).map(([k, m]) => [k, `${m.icon} ${m.label}`])];
  typeChipsEl.innerHTML = types
    .map(([v, lbl]) => {
      const active = (state.type || "") === v ? " active" : "";
      return `<button class="chip-btn${active}" data-type="${v}">${escapeHtml(lbl)}</button>`;
    })
    .join("");
}

async function renderLabelChips() {
  const labels = await api("/api/annotations/labels");
  if (!labels.length) {
    labelChipsEl.innerHTML = "";
    return;
  }
  labelChipsEl.innerHTML =
    `<span class="filter-lead muted small">Labels:</span>` +
    labels
      .map((l) => {
        const active = state.label === l.value ? " active" : "";
        return `<button class="chip-btn chip-btn-label${active}" data-label="${escapeHtml(l.value)}">#${escapeHtml(l.value)} <span class="count">${l.count}</span></button>`;
      })
      .join("");
}

async function renderSources() {
  const sources = await api("/api/sources");
  sourceSel.innerHTML =
    `<option value="">alle Quellen</option>` +
    sources.map((s) => `<option value="${s.id}">${escapeHtml(s.label)}</option>`).join("");
}

function syncFlagButtons() {
  openTodosBtn.classList.toggle("active", state.type === "todo" && state.done === false);
  toMeBtn.classList.toggle("active", state.assignee === "me");
}

function resultHtml(a) {
  const m = TYPE_META[a.type] || { icon: "•", label: a.type };
  let body;
  if (a.type === "label") body = `#${escapeHtml(a.label_value)}`;
  else if (a.type === "handover") {
    const to = a.assignee_name ? `→ ${escapeHtml(a.assignee_name)}` : "→ ?";
    body = `<strong>${to}</strong>${a.body ? " · " + escapeHtml(a.body) : ""}`;
  } else body = escapeHtml(a.body) || "<span class='muted'>(leer)</span>";

  const dated = a.type === "todo" || a.type === "handover";
  const dueBox = dated
    ? `<label class="ov-due">📅 <input type="date" data-due="${a.id}" value="${a.due_date || ""}" title="Fällig am"></label>`
    : "";
  const doneBox = a.type === "todo"
    ? `<label class="ov-done"><input type="checkbox" data-toggle="${a.id}" ${a.done ? "checked" : ""}> erledigt</label>`
    : "";
  const missing = a.entry_status === "missing"
    ? `<span class="badge missing">verschwunden</span>` : "";

  return `<div class="card ov-item" data-ann="${a.id}">
    <div class="ov-head">
      <span class="ann-type">${m.icon} ${escapeHtml(TYPE_LABEL_SINGULAR[a.type] || a.type)}</span>
      <span class="ov-file">${EntryUI.iconFor({ name: a.entry_name, ext: "", is_dir: false })} ${escapeHtml(a.entry_name)}</span>
      ${missing}
      <span class="badge source">${escapeHtml(a.source_label)}</span>
      <span class="row-spacer"></span>
      <a class="btn-link small" href="/browse?source=${a.source_id}&path=${encodeURIComponent(a.entry_path)}">im Baum →</a>
      <button class="link-btn danger tiny" data-del-ann="${a.id}" title="löschen">×</button>
    </div>
    <div class="ov-path muted small">${escapeHtml(a.entry_path)}</div>
    <div class="ov-body ${a.done ? "done" : ""}">${body}</div>
    <div class="ov-meta">${doneBox}${dueBox}</div>
  </div>`;
}

async function reload() {
  const params = new URLSearchParams();
  if (state.type) params.set("type", state.type);
  if (state.source_id) params.set("source_id", state.source_id);
  if (state.label) params.set("label", state.label);
  if (state.assignee) params.set("assignee", state.assignee);
  if (state.done !== null) params.set("done", state.done);
  if (state.q) params.set("q", state.q);

  const items = await api(`/api/annotations?${params.toString()}`);
  countEl.textContent = items.length ? `${items.length} Einträge` : "";
  resultsEl.innerHTML = items.length
    ? items.map(resultHtml).join("")
    : `<p class="muted">Keine Einträge für diese Filter.</p>`;
  renderTypeChips();
  syncFlagButtons();
}

// --- Filter-Interaktionen ---------------------------------------------------

typeChipsEl.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-type]");
  if (!btn) return;
  state.type = btn.dataset.type || null;
  if (state.type !== "todo") state.done = null; // "offene Todos" nur bei Typ Todo
  reload();
});

labelChipsEl.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-label]");
  if (!btn) return;
  const val = btn.dataset.label;
  state.label = state.label === val ? null : val; // Toggle
  renderLabelChips();
  reload();
});

sourceSel.addEventListener("change", () => {
  state.source_id = sourceSel.value || null;
  reload();
});

let qTimer;
qInput.addEventListener("input", () => {
  clearTimeout(qTimer);
  qTimer = setTimeout(() => {
    state.q = qInput.value.trim();
    reload();
  }, 250);
});

openTodosBtn.addEventListener("click", () => {
  const on = !(state.type === "todo" && state.done === false);
  state.type = on ? "todo" : null;
  state.done = on ? false : null;
  renderLabelChips();
  reload();
});

toMeBtn.addEventListener("click", () => {
  state.assignee = state.assignee === "me" ? null : "me";
  reload();
});

document.getElementById("ov-reset").addEventListener("click", () => {
  Object.assign(state, { type: null, source_id: null, label: null, assignee: null, done: null, q: "" });
  qInput.value = "";
  sourceSel.value = "";
  renderLabelChips();
  reload();
});

// Ergebnis-Interaktionen: Todo abhaken / löschen.
resultsEl.addEventListener("change", async (e) => {
  const id = e.target.dataset.toggle;
  if (id) {
    await api(`/api/annotations/${id}`, { method: "PATCH", body: { done: e.target.checked } });
    reload();
    return;
  }
  const dueId = e.target.dataset.due;
  if (dueId) {
    await api(`/api/annotations/${dueId}`, {
      method: "PATCH",
      body: { due_date: e.target.value || null },
    });
    reload();
  }
});
resultsEl.addEventListener("click", async (e) => {
  const id = e.target.dataset.delAnn;
  if (id) {
    await api(`/api/annotations/${id}`, { method: "DELETE" });
    renderLabelChips();
    reload();
  }
});

// Start
(async () => {
  renderTypeChips();
  await Promise.all([renderLabelChips(), renderSources()]);
  await reload();
})().catch((err) => (resultsEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`));
