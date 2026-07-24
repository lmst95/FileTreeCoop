// Kalenderseite: Todos/Übergaben mit Termin – als Monatsraster und als
// Liste der anstehenden Aufgaben in Datumsreihenfolge.

const TYPE_ICON_CAL = { note: "📝", todo: "☑", label: "🏷", handover: "➦" };
const WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];
const MONTHS = [
  "Januar", "Februar", "März", "April", "Mai", "Juni",
  "Juli", "August", "September", "Oktober", "November", "Dezember",
];

const cal = {
  view: "month",
  month: startOfMonth(new Date()),
  selectedDay: null, // "YYYY-MM-DD"
  source_id: null,
  openOnly: true, // „anstehend“ heißt offen – erledigte blendet man bewusst ein
  assignee: null,
};

const viewsEl = document.getElementById("cal-views");
const sourceSel = document.getElementById("cal-source");
const openOnlyBtn = document.getElementById("cal-open-only");
const toMeBtn = document.getElementById("cal-to-me");
const monthViewEl = document.getElementById("view-month");
const agendaViewEl = document.getElementById("view-agenda");
const gridEl = document.getElementById("cal-grid");
const titleEl = document.getElementById("cal-title");
const dayDetailEl = document.getElementById("cal-day-detail");
const agendaListEl = document.getElementById("agenda-list");
const agendaCountEl = document.getElementById("agenda-count");

// --- Datums-Helfer (alles lokale Zeit, ISO-Strings als Schlüssel) -----------

function startOfMonth(d) {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}
function addDays(d, n) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
}
function isoDay(d) {
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function todayIso() {
  return isoDay(new Date());
}
function formatDay(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d}. ${MONTHS[m - 1]} ${y}`;
}
// Montag der Woche, in der `d` liegt (Kalender startet montags).
function weekStart(d) {
  return addDays(d, -((d.getDay() + 6) % 7));
}
function daysBetween(isoA, isoB) {
  return Math.round((new Date(isoB) - new Date(isoA)) / 86400000);
}

// --- Laden ------------------------------------------------------------------

function baseParams() {
  const p = new URLSearchParams();
  p.set("has_due", "true");
  if (cal.source_id) p.set("source_id", cal.source_id);
  if (cal.openOnly) p.set("done", "false");
  if (cal.assignee) p.set("assignee", cal.assignee);
  return p;
}

async function loadRange(fromIso, toIso) {
  const p = baseParams();
  p.set("due_from", fromIso);
  p.set("due_to", toIso);
  p.set("order", "due");
  p.set("limit", "1000");
  return api(`/api/annotations?${p.toString()}`);
}

// --- Gemeinsame Item-Darstellung -------------------------------------------

function itemBody(a) {
  if (a.type === "handover") {
    const to = a.assignee_name ? `→ ${escapeHtml(a.assignee_name)}` : "→ ?";
    return `<strong>${to}</strong>${a.body ? " · " + escapeHtml(a.body) : ""}`;
  }
  if (a.type === "label") return `#${escapeHtml(a.label_value)}`;
  return escapeHtml(a.body) || "<span class='muted'>(leer)</span>";
}

// Überfällig = Termin liegt vor heute und die Aufgabe ist noch offen.
function isOverdue(a) {
  return !a.done && a.due_date < todayIso();
}

function itemCardHtml(a, { showDate = true } = {}) {
  const overdue = isOverdue(a);
  const dateChip = showDate
    ? `<span class="badge due${overdue ? " overdue" : ""}">📅 ${escapeHtml(formatDay(a.due_date))}</span>`
    : "";
  const doneBox = a.type === "todo"
    ? `<label class="ov-done"><input type="checkbox" data-toggle="${a.id}" ${a.done ? "checked" : ""}> erledigt</label>`
    : "";
  const missing = a.entry_status === "missing"
    ? `<span class="badge missing">verschwunden</span>` : "";

  return `<div class="card ov-item${overdue ? " overdue" : ""}" data-ann="${a.id}">
    <div class="ov-head">
      <span class="ann-type">${TYPE_ICON_CAL[a.type] || "•"}</span>
      <span class="ov-file">${EntryUI.iconFor({ name: a.entry_name, ext: "", is_dir: false })} ${escapeHtml(a.entry_name)}</span>
      ${dateChip}
      ${missing}
      <span class="badge source">${escapeHtml(a.source_label)}</span>
      <span class="row-spacer"></span>
      <a class="btn-link small" href="/browse?source=${a.source_id}&path=${encodeURIComponent(a.entry_path)}">im Baum →</a>
    </div>
    <div class="ov-path muted small">${escapeHtml(a.entry_path)}</div>
    <div class="ov-body ${a.done ? "done" : ""}">${itemBody(a)}</div>
    ${doneBox}
  </div>`;
}

// --- Monatsansicht ----------------------------------------------------------

async function renderMonth() {
  const first = weekStart(cal.month);
  const last = addDays(first, 41); // immer 6 Wochen -> stabile Rasterhöhe
  titleEl.textContent = `${MONTHS[cal.month.getMonth()]} ${cal.month.getFullYear()}`;

  const items = await loadRange(isoDay(first), isoDay(last));
  const byDay = new Map();
  items.forEach((a) => {
    if (!byDay.has(a.due_date)) byDay.set(a.due_date, []);
    byDay.get(a.due_date).push(a);
  });

  const today = todayIso();
  let html = WEEKDAYS.map((w) => `<div class="cal-wd">${w}</div>`).join("");
  for (let i = 0; i < 42; i++) {
    const d = addDays(first, i);
    const iso = isoDay(d);
    const dayItems = byDay.get(iso) || [];
    const cls = [
      "cal-day",
      d.getMonth() === cal.month.getMonth() ? "" : "other-month",
      iso === today ? "today" : "",
      iso === cal.selectedDay ? "selected" : "",
      dayItems.some(isOverdue) ? "has-overdue" : "",
    ].filter(Boolean).join(" ");

    const shown = dayItems.slice(0, 3).map((a) =>
      `<span class="cal-pill ${a.done ? "done" : ""}" title="${escapeHtml(a.entry_name)}">${TYPE_ICON_CAL[a.type] || "•"} ${escapeHtml(a.body || a.entry_name)}</span>`
    ).join("");
    const more = dayItems.length > 3
      ? `<span class="cal-more">+${dayItems.length - 3} weitere</span>` : "";

    html += `<div class="${cls}" data-day="${iso}">
        <span class="cal-daynum">${d.getDate()}</span>
        <div class="cal-pills">${shown}${more}</div>
      </div>`;
  }
  gridEl.innerHTML = html;
  // Blättert man weg, gehört das Tagesdetail nicht mehr zum sichtbaren Raster.
  const inGrid = cal.selectedDay >= isoDay(first) && cal.selectedDay <= isoDay(last);
  renderDayDetail(inGrid ? byDay.get(cal.selectedDay) || [] : null);
}

function renderDayDetail(items) {
  if (!cal.selectedDay || items === null) {
    dayDetailEl.innerHTML = "";
    return;
  }
  const head = `<h3 class="cal-detail-title">${escapeHtml(formatDay(cal.selectedDay))}</h3>`;
  dayDetailEl.innerHTML = head + (items.length
    ? `<div class="results">${items.map((a) => itemCardHtml(a, { showDate: false })).join("")}</div>`
    : `<p class="muted">Keine Aufgaben an diesem Tag.</p>`);
}

// --- Anstehend (Agenda) -----------------------------------------------------

// Gruppiert nach Nähe zum heutigen Tag; die Reihenfolge kommt vom Server.
function bucketOf(iso) {
  const diff = daysBetween(todayIso(), iso);
  if (diff < 0) return { key: "overdue", label: "⚠ Überfällig" };
  if (diff === 0) return { key: "today", label: "Heute" };
  if (diff === 1) return { key: "tomorrow", label: "Morgen" };
  if (diff <= 7) return { key: "week", label: "Diese Woche" };
  if (diff <= 31) return { key: "month", label: "Diesen Monat" };
  return { key: "later", label: "Später" };
}

async function renderAgenda() {
  const p = baseParams();
  p.set("order", "due");
  p.set("limit", "500");
  const items = await api(`/api/annotations?${p.toString()}`);

  agendaCountEl.textContent = items.length
    ? `${items.length} anstehende Einträge` : "";
  if (!items.length) {
    agendaListEl.innerHTML = `<p class="muted">Nichts mit Termin – setze an einem Todo ein Datum.</p>`;
    return;
  }

  let html = "";
  let current = null;
  items.forEach((a) => {
    const b = bucketOf(a.due_date);
    if (b.key !== current) {
      current = b.key;
      html += `<h3 class="agenda-group ${b.key}">${b.label}</h3>`;
    }
    html += itemCardHtml(a);
  });
  agendaListEl.innerHTML = html;
}

// --- Steuerung --------------------------------------------------------------

async function reload() {
  monthViewEl.hidden = cal.view !== "month";
  agendaViewEl.hidden = cal.view !== "agenda";
  if (cal.view === "month") await renderMonth();
  else await renderAgenda();
}

viewsEl.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-view]");
  if (!btn) return;
  cal.view = btn.dataset.view;
  [...viewsEl.querySelectorAll("[data-view]")].forEach((b) =>
    b.classList.toggle("active", b.dataset.view === cal.view)
  );
  reload();
});

document.getElementById("cal-prev").addEventListener("click", () => {
  cal.month = new Date(cal.month.getFullYear(), cal.month.getMonth() - 1, 1);
  renderMonth();
});
document.getElementById("cal-next").addEventListener("click", () => {
  cal.month = new Date(cal.month.getFullYear(), cal.month.getMonth() + 1, 1);
  renderMonth();
});
document.getElementById("cal-today").addEventListener("click", () => {
  cal.month = startOfMonth(new Date());
  cal.selectedDay = todayIso();
  renderMonth();
});

gridEl.addEventListener("click", (e) => {
  const cell = e.target.closest("[data-day]");
  if (!cell) return;
  const day = cell.dataset.day;
  cal.selectedDay = cal.selectedDay === day ? null : day; // Toggle
  renderMonth();
});

sourceSel.addEventListener("change", () => {
  cal.source_id = sourceSel.value || null;
  reload();
});
openOnlyBtn.addEventListener("click", () => {
  cal.openOnly = !cal.openOnly;
  openOnlyBtn.classList.toggle("active", cal.openOnly);
  reload();
});
toMeBtn.addEventListener("click", () => {
  cal.assignee = cal.assignee === "me" ? null : "me";
  toMeBtn.classList.toggle("active", cal.assignee === "me");
  reload();
});

// Abhaken funktioniert in beiden Ansichten.
document.addEventListener("change", async (e) => {
  const id = e.target.dataset.toggle;
  if (!id) return;
  await api(`/api/annotations/${id}`, { method: "PATCH", body: { done: e.target.checked } });
  reload();
});

// Start
(async () => {
  const sources = await api("/api/sources");
  sourceSel.innerHTML =
    `<option value="">alle Quellen</option>` +
    sources.map((s) => `<option value="${s.id}">${escapeHtml(s.label)}</option>`).join("");
  cal.selectedDay = todayIso();
  await reload();
})().catch((err) => (gridEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`));
