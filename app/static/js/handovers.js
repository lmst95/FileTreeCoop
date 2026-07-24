// Übergaben-Seite: „An mich“ / „Von mir“, mit Workflow-Status
// (offen -> angenommen -> erledigt) und direkten Aktions-Buttons.
// IIFE: entry_ui.js deklariert global u. a. STATUS_LABEL – ohne Kapselung
// kollidieren die Deklarationen und das ganze Skript fiele aus.
(function () {

const resultsEl = document.getElementById("ho-results");
const countEl = document.getElementById("ho-count");

const STATUS_LABEL = { open: "offen", accepted: "angenommen", done: "erledigt" };

const state = { tab: "me", openOnly: true };

function todayIsoHo() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function rowHtml(a) {
  const overdue = a.due_date && !a.done && a.due_date < todayIsoHo();
  const who = state.tab === "me"
    ? `von <strong>${escapeHtml(a.author_name || "?")}</strong>`
    : `an <strong>${escapeHtml(a.assignee_name || "?")}</strong>`;
  const due = a.due_date
    ? `<span class="chip chip-due${overdue ? " chip-overdue" : ""}">📅 ${escapeHtml(a.due_date)}</span>`
    : "";
  // Aktionen je nach Status und Rolle.
  let actions = "";
  if (state.tab === "me") {
    if (a.status === "open") {
      actions = `<button class="tiny primary" data-status="accepted" data-id="${a.id}">Annehmen</button>
                 <button class="tiny" data-status="done" data-id="${a.id}">Erledigt</button>`;
    } else if (a.status === "accepted") {
      actions = `<button class="tiny primary" data-status="done" data-id="${a.id}">Erledigt</button>`;
    } else {
      actions = `<button class="tiny" data-status="open" data-id="${a.id}">Wieder öffnen</button>`;
    }
  } else if (a.status === "done") {
    actions = `<button class="tiny" data-status="open" data-id="${a.id}">Wieder öffnen</button>`;
  }
  return `<div class="card ho-row ${a.done ? "ho-done" : ""}">
      <div class="ho-main">
        <span class="badge status-${a.status}">${STATUS_LABEL[a.status] || a.status}</span>
        <span>➦ ${who}</span>
        ${a.body ? `<span class="ho-body">· ${escapeHtml(a.body)}</span>` : ""}
        ${due}
      </div>
      <div class="ho-file muted small">
        ${escapeHtml(a.entry_name)}
        <span class="muted">(${escapeHtml(a.source_label)} · ${escapeHtml(a.entry_path)})</span>
        <a href="/browse?source=${a.source_id}&path=${encodeURIComponent(a.entry_path)}">im Baum →</a>
      </div>
      <div class="ho-actions">${actions}</div>
    </div>`;
}

async function load() {
  const params = new URLSearchParams({ type: "handover", order: "due" });
  if (state.tab === "me") params.set("assignee", "me");
  else params.set("author", "me");
  if (state.openOnly) params.set("done", "false");
  const items = await api(`/api/annotations?${params.toString()}`);
  countEl.textContent = items.length
    ? `${items.length} Übergabe${items.length === 1 ? "" : "n"}`
    : "";
  resultsEl.innerHTML = items.length
    ? items.map(rowHtml).join("")
    : `<p class="muted">Keine Übergaben ${state.tab === "me" ? "an dich" : "von dir"}${state.openOnly ? " offen" : ""}. 🎉</p>`;
}

document.querySelectorAll("[data-tab]").forEach((btn) => {
  btn.addEventListener("click", () => {
    state.tab = btn.dataset.tab;
    document.querySelectorAll("[data-tab]").forEach((b) =>
      b.classList.toggle("active", b === btn));
    load().catch((e) => toast(e.message, "error"));
  });
});

document.getElementById("ho-open-only").addEventListener("click", (e) => {
  state.openOnly = !state.openOnly;
  e.target.classList.toggle("active", state.openOnly);
  load().catch((err) => toast(err.message, "error"));
});

resultsEl.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-status]");
  if (!btn) return;
  try {
    await api(`/api/annotations/${btn.dataset.id}`, {
      method: "PATCH",
      body: { status: btn.dataset.status },
    });
    await load();
    if (window.refreshNavBadges) window.refreshNavBadges();
  } catch (err) {
    toast(err.message, "error");
  }
});

load().catch((e) => (resultsEl.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`));

})();
