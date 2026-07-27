// Speicherplatz-Ansicht: Kennzahlen, Ordner-Drilldown, Typen, Alter, Duplikate.
//
// Alle Balken zeigen dieselbe Größe (Bytes) und tragen deshalb genau einen
// Farbton – die Länge ist die Aussage, Farbe wäre hier nur Dekoration. Werte
// und Beschriftungen bleiben in den Text-Tokens der App.

const sourceSel = document.getElementById("storage-source");
const ageSel = document.getElementById("storage-age");
const tilesEl = document.getElementById("storage-tiles");
const crumbsEl = document.getElementById("folder-crumbs");
const folderEl = document.getElementById("folder-bars");
const typeEl = document.getElementById("type-bars");
const ageEl = document.getElementById("age-bars");
const largestEl = document.getElementById("largest-list");
const oldestEl = document.getElementById("oldest-list");
const dupEl = document.getElementById("dup-list");
const dupTotalEl = document.getElementById("dup-total");

let currentFolder = ""; // Pfad im Ordner-Drilldown

// --- Formatierung -----------------------------------------------------------

const UNITS = ["B", "KB", "MB", "GB", "TB", "PB"];

function formatBytes(n) {
  let v = Number(n) || 0;
  let i = 0;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i += 1;
  }
  const digits = v < 10 && i > 0 ? 1 : 0;
  return `${v.toLocaleString("de-DE", { maximumFractionDigits: digits })} ${UNITS[i]}`;
}

function formatCount(n) {
  return Number(n || 0).toLocaleString("de-DE");
}

function formatDate(epochSeconds) {
  if (!epochSeconds) return "";
  return new Date(epochSeconds * 1000).toLocaleDateString("de-DE");
}

function sourceParam(extra = {}) {
  const p = new URLSearchParams(extra);
  if (sourceSel.value) p.set("source_id", sourceSel.value);
  return p.toString();
}

// --- Balken -----------------------------------------------------------------

// Ein Balken je Zeile: Beschriftung links, Wert rechts, Balken darunter. Die
// Breite ist relativ zum größten Wert der Gruppe – so bleibt auch eine kleine
// Zeile noch sichtbar (Mindestbreite), ohne die Proportion zu verfälschen.
function barRow({ label, sub, value, max, meta, href, dataset = "" }) {
  const pct = max > 0 ? Math.max((value / max) * 100, 0.6) : 0;
  const inner = `
      <div class="bar-head">
        <span class="bar-label">${label}</span>
        <span class="bar-value">${escapeHtml(formatBytes(value))}</span>
      </div>
      <div class="bar-track"><div class="bar-fill" style="width:${pct.toFixed(2)}%"></div></div>
      ${sub || meta ? `<div class="bar-sub muted small">${sub || ""}${meta ? `<span class="bar-meta">${escapeHtml(meta)}</span>` : ""}</div>` : ""}`;
  const title = `${escapeHtml(formatBytes(value))}${meta ? ` · ${escapeHtml(meta)}` : ""}`;
  return href
    ? `<a class="bar-row" href="${href}" title="${title}" ${dataset}>${inner}</a>`
    : `<div class="bar-row" title="${title}" ${dataset}>${inner}</div>`;
}

function browseHref(sourceId, path) {
  return `/browse?source=${sourceId}&path=${encodeURIComponent(path)}`;
}

// --- Kennzahlen -------------------------------------------------------------

function tile(label, value, sub = "") {
  return `<div class="stat-tile">
      <div class="stat-value">${escapeHtml(value)}</div>
      <div class="stat-label">${escapeHtml(label)}</div>
      ${sub ? `<div class="stat-sub muted small">${escapeHtml(sub)}</div>` : ""}
    </div>`;
}

async function loadSummary() {
  const sum = await api(`/api/storage/summary?${sourceParam()}`);
  tilesEl.innerHTML = [
    tile("belegt", formatBytes(sum.total_size)),
    tile("Dateien", formatCount(sum.files)),
    tile("Ordner", formatCount(sum.dirs)),
    sum.missing
      ? tile("verschwunden", formatCount(sum.missing), `zuletzt ${formatBytes(sum.missing_size)}`)
      : "",
  ].join("");

  // Quellen-Auswahl beim ersten Lauf füllen (mit Größe als Orientierung).
  if (sourceSel.options.length <= 1 && sum.sources.length) {
    for (const s of sum.sources) {
      const opt = document.createElement("option");
      opt.value = s.source_id;
      opt.textContent = `${s.label} (${formatBytes(s.size)})`;
      sourceSel.appendChild(opt);
    }
    // Deep-Link vom Dashboard: /storage?source=3
    const wanted = Number(new URLSearchParams(location.search).get("source"));
    if (wanted && sum.sources.some((s) => s.source_id === wanted)) {
      sourceSel.value = String(wanted);
      return loadSummary();
    }
  }
  return sum;
}

// --- Ordner-Drilldown -------------------------------------------------------

function renderCrumbs(sourceId) {
  const parts = currentFolder ? currentFolder.split("/") : [];
  const crumbs = [`<button class="link-btn" data-folder="">Wurzel</button>`];
  let acc = "";
  for (const part of parts) {
    acc = acc ? `${acc}/${part}` : part;
    crumbs.push(
      `<span class="crumb-sep">/</span><button class="link-btn" data-folder="${escapeHtml(acc)}">${escapeHtml(part)}</button>`
    );
  }
  const openTree = currentFolder
    ? ` <a class="btn-link" href="${browseHref(sourceId, currentFolder)}">im Baum öffnen →</a>`
    : "";
  crumbsEl.innerHTML = crumbs.join("") + openTree;
}

async function loadFolders() {
  const sourceId = sourceSel.value;
  if (!sourceId) {
    crumbsEl.innerHTML = "";
    folderEl.innerHTML = `<p class="muted small">Für den Ordner-Drilldown oben eine Quelle wählen.</p>`;
    return;
  }
  folderEl.innerHTML = `<p class="muted small">Lädt …</p>`;
  const level = await api(
    `/api/storage/folders?source_id=${sourceId}&parent=${encodeURIComponent(currentFolder)}`
  );
  renderCrumbs(sourceId);
  if (!level.children.length) {
    folderEl.innerHTML = `<p class="muted small">Keine Dateien in diesem Ordner.</p>`;
    return;
  }
  const max = level.children[0].size;
  folderEl.innerHTML = level.children
    .map((c) => {
      const share = level.total_size ? Math.round((c.size / level.total_size) * 100) : 0;
      const meta = c.is_dir
        ? `${formatCount(c.files)} Dateien · ${share}%`
        : `Datei · ${share}%`;
      const label = `${c.is_dir ? "📁" : EntryUI.iconFor({ name: c.name, is_dir: false })} ${escapeHtml(c.name)}`;
      return barRow({
        label,
        value: c.size,
        max,
        meta,
        href: c.is_dir ? null : browseHref(sourceId, c.path),
        dataset: c.is_dir ? `data-open="${escapeHtml(c.path)}"` : "",
      });
    })
    .join("");
}

// --- Typen, Alter, Listen ---------------------------------------------------

async function loadTypes() {
  const rows = await api(`/api/storage/types?${sourceParam({ limit: 12 })}`);
  if (!rows.length) {
    typeEl.innerHTML = `<p class="muted small">Keine Dateien erfasst.</p>`;
    return;
  }
  const max = rows[0].size;
  typeEl.innerHTML = rows
    .map((r) =>
      barRow({
        label: escapeHtml(r.ext === "(ohne)" ? "ohne Endung" : `.${r.ext}`),
        value: r.size,
        max,
        meta: `${formatCount(r.files)} Dateien`,
      })
    )
    .join("");
}

async function loadAges() {
  const rows = await api(`/api/storage/ages?${sourceParam()}`);
  const max = Math.max(...rows.map((r) => r.size), 0);
  ageEl.innerHTML = rows
    .map((r) =>
      barRow({
        label: escapeHtml(r.label),
        value: r.size,
        max,
        meta: `${formatCount(r.files)} Dateien`,
      })
    )
    .join("");
}

function entryRows(entries, emptyText) {
  if (!entries.length) return `<p class="muted small">${emptyText}</p>`;
  const max = entries[0].size;
  return entries
    .map((e) =>
      barRow({
        label: `${EntryUI.iconFor(e)} ${escapeHtml(e.name)}`,
        sub: `<span class="bar-path">${escapeHtml(e.source_label)} · ${escapeHtml(e.path)}</span>`,
        value: e.size,
        max,
        meta: e.mtime ? `geändert ${formatDate(e.mtime)}` : "",
        href: browseHref(e.source_id, e.path),
      })
    )
    .join("");
}

async function loadLargest() {
  const entries = await api(`/api/storage/largest?${sourceParam({ limit: 20 })}`);
  largestEl.innerHTML = entryRows(entries, "Keine Dateien erfasst.");
}

async function loadOldest() {
  const entries = await api(
    `/api/storage/oldest?${sourceParam({ limit: 20, days: ageSel.value })}`
  );
  oldestEl.innerHTML = entryRows(
    entries,
    "Nichts gefunden – alles wurde im gewählten Zeitraum angefasst."
  );
}

// --- Duplikate --------------------------------------------------------------

async function loadDuplicates() {
  dupEl.innerHTML = `<p class="muted small">Lädt …</p>`;
  const groups = await api(`/api/storage/duplicates?${sourceParam({ limit: 50 })}`);
  if (!groups.length) {
    dupTotalEl.textContent = "";
    dupEl.innerHTML = `<p class="muted small">Keine Duplikate gefunden.</p>`;
    return;
  }
  const wasted = groups.reduce((sum, g) => sum + g.wasted, 0);
  dupTotalEl.textContent = `${formatCount(groups.length)} Gruppen · ${formatBytes(wasted)} unnötig belegt`;
  dupEl.innerHTML = groups
    .map(
      (g) => `<div class="dup-group">
        <div class="dup-head">
          <strong>${escapeHtml(formatBytes(g.size))}</strong>
          <span class="badge">${g.count}× vorhanden</span>
          <span class="muted small">${escapeHtml(formatBytes(g.wasted))} davon unnötig</span>
          <code class="dup-hash muted small" title="SHA-256">${escapeHtml(g.content_hash.slice(0, 12))}…</code>
        </div>
        <ul class="dup-files small">${g.entries
          .map(
            (e) => `<li>
              <a href="${browseHref(e.source_id, e.path)}">${escapeHtml(e.path)}</a>
              <span class="badge source">${escapeHtml(e.source_label)}</span>
            </li>`
          )
          .join("")}</ul>
      </div>`
    )
    .join("");
}

// --- Laden & Interaktion ----------------------------------------------------

async function loadAll() {
  await loadSummary();
  await Promise.all([
    loadFolders(),
    loadTypes(),
    loadAges(),
    loadLargest(),
    loadOldest(),
    loadDuplicates(),
  ]);
}

folderEl.addEventListener("click", (e) => {
  const open = e.target.closest("[data-open]");
  if (!open) return;
  currentFolder = open.dataset.open;
  loadFolders().catch((err) => (folderEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`));
});

crumbsEl.addEventListener("click", (e) => {
  const crumb = e.target.closest("[data-folder]");
  if (!crumb) return;
  currentFolder = crumb.dataset.folder;
  loadFolders().catch((err) => (folderEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`));
});

sourceSel.addEventListener("change", () => {
  currentFolder = "";
  loadAll().catch((err) => toast(err.message, "error"));
});

ageSel.addEventListener("change", () => {
  loadOldest().catch((err) => toast(err.message, "error"));
});

loadAll().catch((err) => {
  tilesEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
});
