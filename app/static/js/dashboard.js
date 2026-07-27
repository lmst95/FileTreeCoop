// Quellen-Übersicht: anlegen, löschen, scannen/re-scannen, teilen.

const listEl = document.getElementById("sources-list");
const statusEl = document.getElementById("scan-status");
const KIND_LABELS = {
  local: "Lokal",
  network: "Netzlaufwerk",
  bwsync: "bwSync&Share",
  shared: "Geteilter Ordner",
};
let me = null; // aktueller Nutzer (für Besitz-Erkennung)
let lastSources = []; // zuletzt geladene Quellenliste (für Status-Meldungen)

if (!Scanner.supported()) {
  document.getElementById("browser-warn").hidden = false;
}

function setStatus(msg, kind = "info") {
  statusEl.hidden = false;
  statusEl.className = `scan-status ${kind}`;
  statusEl.textContent = msg;
}

// Wie setStatus, aber mit einem Abbrechen-Knopf daneben (langer Hash-Lauf).
// Bei Fortschrittsmeldungen wird nur der Text getauscht – würde hier jedes Mal
// neu gerendert, verlöre der Knopf bei jedem Tick den Fokus.
function setStatusCancellable(msg, onCancel) {
  statusEl.hidden = false;
  statusEl.className = "scan-status info";
  let label = statusEl.querySelector(".run-label");
  if (!label) {
    statusEl.innerHTML = `<span class="run-label"></span> <button class="link-btn" type="button">abbrechen</button>`;
    label = statusEl.querySelector(".run-label");
  }
  label.textContent = msg;
  statusEl.querySelector("button").onclick = onCancel;
}

function formatCount(n) {
  return Number(n).toLocaleString("de-DE");
}

// Kompakte Diff-Zeile eines Scans („+3 neu · 2 geändert · 1 verschwunden“).
function scanDiffText(sc) {
  if (!sc) return "";
  const skip = sc.skipped ? ` · ⚠ ${sc.skipped} übersprungen` : "";
  if (sc.initial) return `${sc.added} Einträge importiert${skip}`;
  const parts = [];
  if (sc.added) parts.push(`+${sc.added} neu`);
  if (sc.changed) parts.push(`${sc.changed} geändert`);
  if (sc.moved) parts.push(`${sc.moved} verschoben`);
  if (sc.missing) parts.push(`${sc.missing} verschwunden`);
  if (sc.reappeared) parts.push(`${sc.reappeared} wieder da`);
  return (parts.length ? parts.join(" · ") : "keine Änderungen") + skip;
}

async function loadSources() {
  const sources = await api("/api/sources");
  lastSources = sources;
  if (!sources.length) {
    listEl.innerHTML = `<p class="muted">Noch keine Quellen. Lege deine erste an und scanne einen Ordner.</p>`;
    return;
  }
  listEl.innerHTML = "";
  const canScan = Scanner.supported();
  for (const s of sources) {
    const isOwner = me && s.owner_user_id === me.id;
    const hasHandle = isOwner && canScan && (await Scanner.hasHandle(s.id));
    const scanned = s.last_scanned_at
      ? new Date(s.last_scanned_at).toLocaleString("de-DE")
      : "noch nie";
    const card = document.createElement("div");
    card.className = "card source-card";
    card.innerHTML = `
      <div class="source-head">
        <div>
          <strong>${escapeHtml(s.label)}</strong>
          <span class="badge">${escapeHtml(KIND_LABELS[s.kind] || s.kind)}</span>
          ${s.host_hint ? `<span class="muted"> · ${escapeHtml(s.host_hint)}</span>` : ""}
          ${isOwner ? "" : `<span class="badge shared-badge">geteilt mit mir</span>`}
        </div>
        ${isOwner ? `<button class="link-btn danger" data-del="${s.id}">löschen</button>` : ""}
      </div>
      <div class="muted small">Zuletzt gescannt: ${escapeHtml(scanned)}${
        s.last_scan ? ` · <span class="scan-diff">${escapeHtml(scanDiffText(s.last_scan))}</span>` : ""
      }</div>
      <div class="source-actions">
        ${isOwner && canScan ? `<button class="primary" data-scan="${s.id}">Ordner wählen &amp; scannen</button>` : ""}
        ${hasHandle ? `<button data-rescan="${s.id}">Erneut scannen</button>` : ""}
        ${isOwner ? `<button data-share="${s.id}">Teilen</button>` : ""}
        ${s.last_scan ? `<button data-scans="${s.id}">Scan-Historie</button>` : ""}
        ${s.last_scan && s.last_scan.skipped ? `<button class="btn-warn" data-skips="${s.last_scan.id}" data-src="${s.id}">⚠ ${s.last_scan.skipped} übersprungen</button>` : ""}
        ${isOwner && s.last_scan && s.last_scan.missing ? `<button data-cleanup="${s.id}">🧹 Aufräumen</button>` : ""}
        ${hasHandle ? `<button data-hash="${s.id}" title="SHA-256 je Datei im Browser berechnen – erkennt Duplikate und Umbenennungen">🔐 Inhalts-Hashes</button>` : ""}
        <a class="btn-link" href="/browse?source=${s.id}">im Baum öffnen →</a>
        <a class="btn-link" href="/storage?source=${s.id}">Speicher →</a>
        <a class="btn-link" href="/api/sources/${s.id}/export.json" download
           title="Quelle samt Annotationen als JSON sichern">⬇ Export</a>
      </div>
      <div class="muted small hash-line" data-hash-line="${s.id}"></div>
      ${rootPathFormHtml(s.id)}
      <div class="scans-panel" data-scans-panel="${s.id}" hidden></div>
      ${isOwner ? `<div class="share-panel" data-share-panel="${s.id}" hidden></div>` : ""}`;
    listEl.appendChild(card);
  }
  refreshHashLines();
}

// --- Inhalts-Hashes ---------------------------------------------------------

// Stand je Quelle nachladen („1.204 von 1.320 Dateien gehasht“). Nice-to-have:
// Fehler bleiben still, die Karte steht auch ohne diese Zeile.
async function refreshHashLine(sourceId) {
  const el = listEl.querySelector(`[data-hash-line="${sourceId}"]`);
  if (!el) return;
  try {
    const h = await api(`/api/sources/${sourceId}/hash-summary`);
    if (!h.files || (!h.hashed && !h.skipped && !h.errors)) {
      el.textContent = "";
      return;
    }
    const parts = [`🔐 ${formatCount(h.hashed)}/${formatCount(h.files)} Dateien gehasht`];
    if (h.pending) parts.push(`${formatCount(h.pending)} offen`);
    if (h.skipped) parts.push(`${formatCount(h.skipped)} zu groß`);
    if (h.errors) parts.push(`${formatCount(h.errors)} nicht lesbar`);
    el.innerHTML = escapeHtml(parts.join(" · "));
    if (h.duplicate_groups) {
      el.innerHTML += ` · <a href="/storage?source=${sourceId}#duplicates">${formatCount(
        h.duplicate_groups
      )} Duplikat-Gruppen</a>`;
    }
  } catch (_e) {
    el.textContent = "";
  }
}

function refreshHashLines() {
  for (const el of listEl.querySelectorAll("[data-hash-line]")) {
    refreshHashLine(Number(el.dataset.hashLine));
  }
}

async function runHashing(sourceId) {
  try {
    setStatusCancellable("Inhalts-Hashes werden berechnet …", () => {
      Scanner.cancelHashing();
      setStatus("Wird abgebrochen …", "info");
    });
    const stats = await Scanner.hashPending(sourceId, (s) => {
      const extra = [];
      if (s.skipped) extra.push(`${s.skipped} zu groß`);
      if (s.errors) extra.push(`${s.errors} nicht lesbar`);
      setStatusCancellable(
        `Hashe … ${formatCount(s.hashed)} Dateien${extra.length ? ` (${extra.join(", ")})` : ""}`,
        () => {
          Scanner.cancelHashing();
          setStatus("Wird abgebrochen …", "info");
        }
      );
    });
    const parts = [`${formatCount(stats.hashed)} Dateien gehasht`];
    if (stats.skipped) parts.push(`${stats.skipped} zu groß übersprungen`);
    if (stats.errors) parts.push(`${stats.errors} nicht lesbar`);
    if (stats.reconciled) {
      parts.push(`${stats.reconciled} umbenannte Datei(en) wiedererkannt – Notizen mitgenommen`);
    }
    let msg = (stats.cancelled ? "Abgebrochen: " : "Fertig: ") + parts.join(" · ") + ".";
    if (stats.stuck) {
      msg += ` ⚠ ${formatCount(stats.stuck)} Einträge blieben offen – Details in der Browser-Konsole.`;
    }
    setStatus(msg, stats.stuck ? "warn" : stats.cancelled ? "info" : "success");
    await refreshHashLine(sourceId);
  } catch (err) {
    setStatus("Fehler beim Hashen: " + err.message, "error");
  }
}

// --- Basispfad (pro Gerät) --------------------------------------------------

// Der Browser kennt den absoluten Pfad der gescannten Wurzel nicht. Wer beim
// „Pfad kopieren“ den vollständigen Pfad möchte, trägt sie hier einmal ein.
// Auch für geteilte Quellen sinnvoll: ein Netzlaufwerk hängt bei jedem woanders.
function rootPathFormHtml(sourceId) {
  const root = LocalPaths.getRoot(sourceId);
  return `<form class="root-path-form" data-root-form="${sourceId}">
      <label class="small">Basispfad auf diesem Gerät
        <input name="root" placeholder="z. B. /Users/name/Documents oder P:\\Projekte"
               value="${escapeHtml(root)}" autocomplete="off" spellcheck="false">
      </label>
      <button type="submit" class="tiny">merken</button>
      <span class="muted small">nur auf diesem Gerät gespeichert – ergänzt „📋 Pfad kopieren“ zum vollständigen Pfad</span>
    </form>`;
}

// --- Teilen ----------------------------------------------------------------

function sharePanelHtml(sourceId, shares) {
  return `
    <p class="muted small">Ganze Quelle freigeben – einzelne Unterordner teilst du im <a href="/browse?source=${sourceId}">Baum</a> über 🔗.</p>
    <ul class="share-list">${EntryUI.shareRowsHtml(sourceId, shares)}</ul>
    <form class="share-form" data-share-form="${sourceId}">
      <input name="identifier" type="text" placeholder="E-Mail oder Username des Kollegen" required>
      <select name="permission">
        <option value="annotate">annotieren</option>
        <option value="read">nur lesen</option>
      </select>
      <button type="submit" class="tiny primary">ganze Quelle freigeben</button>
    </form>
    <p class="share-err error small" hidden></p>`;
}

async function openSharePanel(sourceId) {
  const panel = listEl.querySelector(`[data-share-panel="${sourceId}"]`);
  if (!panel) return;
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.innerHTML = `<p class="muted small">Lädt …</p>`;
  const shares = await api(`/api/sources/${sourceId}/shares`);
  panel.innerHTML = sharePanelHtml(sourceId, shares);
}

async function refreshSharePanel(sourceId) {
  const panel = listEl.querySelector(`[data-share-panel="${sourceId}"]`);
  const shares = await api(`/api/sources/${sourceId}/shares`);
  panel.innerHTML = sharePanelHtml(sourceId, shares);
}

// --- Scan-Historie ----------------------------------------------------------

const CHANGE_LABELS = {
  added: "＋ neu", modified: "✎ geändert", missing: "✖ verschwunden",
  moved: "→ verschoben", reappeared: "↩ wieder da",
};

function scanRowHtml(sid, sc) {
  const when = new Date(sc.started_at + "Z").toLocaleString("de-DE");
  const by = sc.started_by_name ? ` · ${escapeHtml(sc.started_by_name)}` : "";
  const hasDiff = !sc.initial &&
    (sc.added || sc.changed || sc.moved || sc.missing || sc.reappeared);
  const diffBtn = hasDiff
    ? `<button class="link-btn tiny" data-scan-diff="${sc.id}" data-src="${sid}">Diff anzeigen</button>`
    : "";
  const skipBtn = sc.skipped
    ? `<button class="link-btn tiny btn-warn" data-skips="${sc.id}" data-src="${sid}">⚠ ${sc.skipped} übersprungen</button>`
    : "";
  return `<li class="scan-row">
      <span class="muted small">${escapeHtml(when)}${by}</span>
      <span class="small">${escapeHtml(scanDiffText(sc))}</span>
      ${diffBtn}
      ${skipBtn}
      <ul class="scan-changes small" data-scan-changes="${sc.id}" hidden></ul>
    </li>`;
}

async function openScansPanel(sid) {
  const panel = listEl.querySelector(`[data-scans-panel="${sid}"]`);
  if (!panel.hidden) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.innerHTML = `<p class="muted small">Lädt …</p>`;
  const scans = await api(`/api/sources/${sid}/scans`);
  panel.innerHTML = scans.length
    ? `<ul class="scan-list">${scans.map((sc) => scanRowHtml(sid, sc)).join("")}</ul>`
    : `<p class="muted small">Noch keine Scans.</p>`;
}

async function showScanDiff(sid, scanId) {
  const box = listEl.querySelector(`[data-scan-changes="${scanId}"]`);
  if (!box.hidden) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.innerHTML = `<li class="muted">Lädt …</li>`;
  const changes = await api(`/api/sources/${sid}/scans/${scanId}/changes`);
  box.innerHTML = changes.length
    ? changes.map((c) => {
        const label = CHANGE_LABELS[c.change] || c.change;
        const path = c.change === "moved"
          ? `${escapeHtml(c.old_path)} → ${escapeHtml(c.path)}`
          : escapeHtml(c.path);
        return `<li><span class="badge">${escapeHtml(label)}</span> ${path}</li>`;
      }).join("")
    : `<li class="muted">keine Einzeländerungen aufgezeichnet</li>`;
}

// --- Übersprungene Einträge (Log-/Fehler-Popup) ----------------------------

// Modales Popup mit den Pfaden, die ein Scan überspringen musste (nicht
// erreichbar, z. B. Netzwerk-Aussetzer). Die Liste wird persistent vom Server
// geladen, ist also auch nach einem Reload und für frühere Scans abrufbar.
async function showSkipLog(sid, scanId) {
  let overlay = document.getElementById("skiplog-overlay");
  if (overlay) overlay.remove();
  overlay = document.createElement("div");
  overlay.id = "skiplog-overlay";
  overlay.innerHTML = `
    <div class="skiplog-box" role="dialog" aria-label="Übersprungene Einträge">
      <div class="skiplog-head">
        <strong>⚠ Übersprungene Einträge</strong>
        <button class="link-btn" data-skiplog-close>schließen ✕</button>
      </div>
      <p class="muted small">
        Diese Pfade waren beim Scan nicht erreichbar und wurden übersprungen.
        Bei Netzlaufwerken meist ein kurzer Verbindungsabriss – ein erneuter
        Scan erfasst sie in der Regel wieder.
      </p>
      <div class="skiplog-list"><p class="muted small">Lädt …</p></div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay || e.target.closest("[data-skiplog-close]")) close();
  });
  document.addEventListener("keydown", function esc(e) {
    if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
  });

  const listBox = overlay.querySelector(".skiplog-list");
  try {
    const skips = await api(`/api/sources/${sid}/scans/${scanId}/skips`);
    listBox.innerHTML = skips.length
      ? `<div class="skiplog-count small muted">${skips.length} Einträge</div>
         <ul class="skiplog-items">${skips
           .map((s) => `<li>
               <code>${escapeHtml(s.path || "(Wurzel)")}</code>
               ${s.reason ? `<span class="badge danger">${escapeHtml(s.reason)}</span>` : ""}
             </li>`)
           .join("")}</ul>`
      : `<p class="muted small">Keine übersprungenen Einträge aufgezeichnet.</p>`;
  } catch (err) {
    listBox.innerHTML = `<p class="error small">Log konnte nicht geladen werden: ${escapeHtml(err.message)}</p>`;
  }
}

// --- Verschwundene aufräumen ------------------------------------------------

// Löscht verschwundene Einträge endgültig – Einträge mit Notizen bleiben
// grundsätzlich stehen (der Server schützt sie zusätzlich).
async function cleanupMissing(sid) {
  const { count, annotated } = await api(`/api/sources/${sid}/missing/summary`);
  if (!count) {
    toast("Nichts aufzuräumen – keine verschwundenen Einträge.", "info");
    return;
  }
  const keep = annotated
    ? ` ${annotated} davon tragen Notizen und bleiben erhalten.`
    : "";
  if (!confirm(`${count} verschwundene Einträge endgültig entfernen?${keep}`)) return;
  const { deleted } = await api(
    `/api/sources/${sid}/missing/cleanup`, { method: "POST" }
  );
  toast(`${deleted} Einträge entfernt.`, "success");
  await loadSources();
}

// --- Scannen ---------------------------------------------------------------

async function runScan(fn, sourceId) {
  if (!Scanner.supported()) {
    setStatus("Ordner-Scan wird von diesem Browser nicht unterstützt.", "error");
    return;
  }
  try {
    setStatus("Scan läuft … Ordner wird durchsucht.", "info");
    const res = await fn(sourceId, (n) =>
      setStatus(`Scan läuft … ${n} Einträge übertragen.`, "info")
    );
    const { total, skipped } = res;
    await loadSources();
    // Der komplette Diff steht am Scan selbst (die Zähler laufen über alle Batches).
    const src = lastSources.find((s) => s.id === sourceId);
    let msg = `Fertig: ${total} Einträge erfasst.`;
    if (src && src.last_scan) msg += ` ${scanDiffText(src.last_scan)}.`;
    const nSkipped = (skipped && skipped.length) || (src && src.last_scan && src.last_scan.skipped) || 0;
    if (nSkipped) {
      let w = `${msg} ⚠ ${nSkipped} Einträge nicht erreichbar – übersprungen.`;
      // Bei unvollständigem Scan wurde nichts als „verschwunden“ markiert.
      if (res.missing_check_skipped) {
        w += " „Verschwunden“ wurde diesmal nicht markiert.";
      }
      setStatus(w, "warn");
      toast(`${nSkipped} Einträge übersprungen. Log wird angezeigt.`, "warn");
      if (src && src.last_scan) await showSkipLog(sourceId, src.last_scan.id);
    } else {
      setStatus(msg, "success");
    }
  } catch (err) {
    if (err.name === "AbortError") setStatus("Abgebrochen.", "info");
    // Wurzel nicht erreichbar (z. B. Netzlaufwerk getrennt): der Index blieb
    // unverändert – klare Meldung statt „Fehler …“.
    else if (err.name === "SourceUnreachableError") setStatus("⚠ " + err.message, "error");
    else setStatus("Fehler: " + err.message, "error");
  }
}

// --- Klick-Handling (Delegation) -------------------------------------------

listEl.addEventListener("click", async (e) => {
  const un = e.target.closest("[data-unshare]");
  if (un) {
    const { src, user, prefix } = un.dataset;
    await api(`/api/sources/${src}/shares/${user}?path_prefix=${encodeURIComponent(prefix)}`,
      { method: "DELETE" });
    await refreshSharePanel(Number(src));
    return;
  }
  const uninv = e.target.closest("[data-uninvite]");
  if (uninv) {
    await api(`/api/sources/${uninv.dataset.src}/invites/${uninv.dataset.invite}`,
      { method: "DELETE" });
    await refreshSharePanel(Number(uninv.dataset.src));
    return;
  }
  const diffBtn = e.target.closest("[data-scan-diff]");
  if (diffBtn) {
    await showScanDiff(Number(diffBtn.dataset.src), Number(diffBtn.dataset.scanDiff));
    return;
  }
  const skipsBtn = e.target.closest("[data-skips]");
  if (skipsBtn) {
    await showSkipLog(Number(skipsBtn.dataset.src), Number(skipsBtn.dataset.skips));
    return;
  }
  const { scan, rescan, del, share, scans, cleanup, hash } = e.target.dataset;
  if (scan) await runScan(Scanner.pickAndScan.bind(Scanner), Number(scan));
  else if (rescan) await runScan(Scanner.rescan.bind(Scanner), Number(rescan));
  else if (hash) await runHashing(Number(hash));
  else if (scans) await openScansPanel(Number(scans));
  else if (share) await openSharePanel(Number(share));
  else if (cleanup) await cleanupMissing(Number(cleanup));
  else if (del) {
    if (confirm("Quelle samt Index wirklich löschen? Notizen gehen verloren.")) {
      await api(`/api/sources/${del}`, { method: "DELETE" });
      await loadSources();
    }
  }
});

listEl.addEventListener("submit", async (e) => {
  if (e.target.matches("[data-root-form]")) {
    e.preventDefault();
    const sid = Number(e.target.dataset.rootForm);
    const input = e.target.querySelector('input[name="root"]');
    const saved = LocalPaths.setRoot(sid, input.value);
    input.value = saved;
    setStatus(saved ? `Basispfad gemerkt: ${saved}` : "Basispfad entfernt.", "success");
    return;
  }
  if (!e.target.matches("[data-share-form]")) return;
  e.preventDefault();
  const sid = Number(e.target.dataset.shareForm);
  const f = new FormData(e.target);
  const errEl = e.target.parentElement.querySelector(".share-err");
  errEl.hidden = true;
  try {
    await api(`/api/sources/${sid}/shares`, {
      method: "POST",
      body: { identifier: f.get("identifier"), permission: f.get("permission") },
    });
    await refreshSharePanel(sid);
  } catch (err) {
    errEl.textContent = err.message;
    errEl.hidden = false;
  }
});

// Formular zum Anlegen einer Quelle.
const form = document.getElementById("new-source-form");
document.getElementById("new-source-btn").addEventListener("click", () => {
  form.hidden = !form.hidden;
});
document.getElementById("cancel-source").addEventListener("click", () => {
  form.hidden = true;
});
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(form);
  await api("/api/sources", {
    method: "POST",
    body: { label: f.get("label"), kind: f.get("kind"), host_hint: f.get("host_hint") },
  });
  form.reset();
  form.hidden = true;
  await loadSources();
});

// Start: aktuellen Nutzer holen, dann Quellen rendern.
(async () => {
  me = await api("/api/auth/me");
  await loadSources();
})();
