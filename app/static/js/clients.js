// Geräte-Übersicht: welcher Desktop-Client läuft wo, überwacht was, ist online?
//
// Der Client selbst ist Herr über seine Konfiguration (er kennt seine lokalen
// Pfade); hier wird gezeigt, was er meldet. Änderbar sind deshalb nur die Dinge,
// die den Server betreffen: Name, Pause und Entfernen.

const listEl = document.getElementById("clients-list");
const setupPanel = document.getElementById("setup-panel");

const KIND_LABELS = {
  local: "Lokal",
  network: "Netzlaufwerk",
  bwsync: "bwSync&Share",
  shared: "Geteilter Ordner",
};
const PLATFORM_LABELS = { win32: "Windows", darwin: "macOS", linux: "Linux" };

function fmtWhen(iso) {
  if (!iso) return "noch nie";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  const secs = Math.round((Date.now() - d.getTime()) / 1000);
  if (secs < 60) return "gerade eben";
  if (secs < 3600) return `vor ${Math.round(secs / 60)} min`;
  if (secs < 86400) return `vor ${Math.round(secs / 3600)} h`;
  return d.toLocaleString("de-DE");
}

function folderRowHtml(f) {
  const flags = [];
  if (!f.enabled) flags.push(`<span class="badge">pausiert</span>`);
  if (f.enabled && f.watch_enabled) {
    flags.push(`<span class="badge" title="Änderungen werden sofort gemeldet">👁 live</span>`);
  }
  if (f.hash_enabled) {
    flags.push(`<span class="badge" title="Inhalts-Hashes werden im Hintergrund berechnet">🔐 Hashes</span>`);
  }
  const err = f.last_error
    ? `<div class="client-error small">⚠ ${escapeHtml(f.last_error)}</div>`
    : "";
  return `<li class="client-folder${f.enabled ? "" : " off"}">
      <div class="client-folder-head">
        <a href="/browse?source=${f.source_id}">${escapeHtml(f.source_label)}</a>
        <span class="muted small">${escapeHtml(KIND_LABELS[f.source_kind] || f.source_kind)}</span>
        ${flags.join(" ")}
      </div>
      <div class="muted small mono">${escapeHtml(f.local_path || "(kein Pfad)")}</div>
      <div class="muted small">
        Voll-Scan alle ${f.scan_interval_minutes} min ·
        zuletzt: ${escapeHtml(fmtWhen(f.last_scan_at))}
      </div>
      ${err}
    </li>`;
}

function clientCardHtml(c) {
  const online = c.online && !c.paused;
  const dot = c.paused
    ? `<span class="client-dot paused" title="pausiert">●</span>`
    : `<span class="client-dot ${c.online ? "on" : "off"}"
             title="${c.online ? "verbunden" : "offline"}">●</span>`;
  const state = c.paused ? "pausiert" : c.online ? "verbunden" : "offline";
  const folders = c.folders.length
    ? `<ul class="client-folders">${c.folders.map(folderRowHtml).join("")}</ul>`
    : `<p class="muted small">Noch kein Ordner eingerichtet – im Client unter „Ordner“.</p>`;
  return `<div class="card client-card${online ? " is-online" : ""}" data-client="${c.id}">
      <div class="client-head">
        <div>
          ${dot}
          <strong>${escapeHtml(c.name)}</strong>
          <span class="badge">${escapeHtml(PLATFORM_LABELS[c.platform] || c.platform || "?")}</span>
          ${c.hostname ? `<span class="muted small"> · ${escapeHtml(c.hostname)}</span>` : ""}
          ${c.version ? `<span class="muted small"> · v${escapeHtml(c.version)}</span>` : ""}
        </div>
        <div class="client-actions">
          <button class="link-btn" data-rename="${c.id}">umbenennen</button>
          <button class="link-btn" data-pause="${c.id}">${c.paused ? "fortsetzen" : "pausieren"}</button>
          <button class="link-btn danger" data-del="${c.id}">entfernen</button>
        </div>
      </div>
      <div class="muted small">
        ${escapeHtml(state)} · zuletzt gesehen ${escapeHtml(fmtWhen(c.last_seen_at))}
        ${c.status_text ? ` · ${escapeHtml(c.status_text)}` : ""}
      </div>
      ${folders}
    </div>`;
}

async function load() {
  let clients;
  try {
    clients = await api("/api/clients");
  } catch (err) {
    listEl.innerHTML = `<p class="warn">Geräte konnten nicht geladen werden: ${escapeHtml(err.message)}</p>`;
    return;
  }
  if (!clients.length) {
    listEl.innerHTML = `<p class="muted">
      Noch kein Gerät eingerichtet. Mit „+ Gerät einrichten“ steht oben, wie es geht.
    </p>`;
    return;
  }
  listEl.innerHTML = clients.map(clientCardHtml).join("");
}

// --- Interaktion ------------------------------------------------------------

listEl.addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const { rename, pause, del } = btn.dataset;
  try {
    if (rename) {
      const card = btn.closest("[data-client]");
      const current = card.querySelector(".client-head strong").textContent;
      const name = prompt("Neuer Name für dieses Gerät:", current);
      if (name === null || !name.trim()) return;
      await api(`/api/clients/${rename}`, { method: "PATCH", body: { name: name.trim() } });
    } else if (pause) {
      // Der aktuelle Zustand steht in der Beschriftung – „pausieren“ heißt an.
      const wantPause = btn.textContent.trim() === "pausieren";
      await api(`/api/clients/${pause}`, { method: "PATCH", body: { paused: wantPause } });
      toast(wantPause ? "Gerät pausiert." : "Gerät fortgesetzt.", "success");
    } else if (del) {
      if (!confirm("Gerät entfernen? Sein Token gilt danach nicht mehr; der Index bleibt erhalten.")) {
        return;
      }
      await api(`/api/clients/${del}`, { method: "DELETE" });
      toast("Gerät entfernt.", "success");
    } else {
      return;
    }
    await load();
  } catch (err) {
    toast("Fehler: " + err.message, "error");
  }
});

document.getElementById("setup-btn").addEventListener("click", () => {
  setupPanel.hidden = !setupPanel.hidden;
  document.getElementById("setup-url").textContent = location.origin;
});
document.getElementById("setup-close").addEventListener("click", () => {
  setupPanel.hidden = true;
});

load();
// Der Online-Zustand ändert sich im Sekundentakt – regelmäßig nachladen, damit
// die Seite nicht „verbunden“ zeigt, während der Rechner längst aus ist.
setInterval(load, 15000);
