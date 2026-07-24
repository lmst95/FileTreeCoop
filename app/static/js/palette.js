// ⌘K / Ctrl+K – globale Schnellsuche von jeder Seite aus.
// Tippt gegen /api/search (FTS5) und springt per Enter/Klick in den Baum
// (Deep-Link ?path=… wird dort automatisch aufgeklappt).

(function () {
  let overlay = null;
  let input = null;
  let listEl = null;
  let hits = [];
  let selected = 0;
  let debounceTimer = null;

  function ensureOverlay() {
    if (overlay) return;
    overlay = document.createElement("div");
    overlay.id = "cmdk-overlay";
    overlay.hidden = true;
    overlay.innerHTML = `
      <div class="cmdk-box" role="dialog" aria-label="Schnellsuche">
        <input id="cmdk-input" placeholder="Datei oder Ordner suchen … (Esc schließt)"
               autocomplete="off" spellcheck="false">
        <ul id="cmdk-list"></ul>
        <div class="cmdk-hint muted small">↑↓ wählen · Enter öffnet im Baum · Esc schließen</div>
      </div>`;
    document.body.appendChild(overlay);
    input = overlay.querySelector("#cmdk-input");
    listEl = overlay.querySelector("#cmdk-list");

    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close();
    });
    input.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(runSearch, 180);
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); openHit(hits[selected]); }
      else if (e.key === "Escape") close();
    });
    listEl.addEventListener("click", (e) => {
      const li = e.target.closest("[data-idx]");
      if (li) openHit(hits[Number(li.dataset.idx)]);
    });
  }

  function open() {
    ensureOverlay();
    overlay.hidden = false;
    input.value = "";
    listEl.innerHTML = `<li class="muted small cmdk-empty">Tippen, um zu suchen …</li>`;
    hits = [];
    selected = 0;
    input.focus();
  }

  function close() {
    if (overlay) overlay.hidden = true;
  }

  function move(delta) {
    if (!hits.length) return;
    selected = (selected + delta + hits.length) % hits.length;
    render();
  }

  function openHit(h) {
    if (!h) return;
    location.href = `/browse?source=${h.source_id}&path=${encodeURIComponent(h.path)}`;
  }

  function render() {
    if (!hits.length) {
      listEl.innerHTML = `<li class="muted small cmdk-empty">Keine Treffer.</li>`;
      return;
    }
    listEl.innerHTML = hits
      .map((h, i) => `<li data-idx="${i}" class="${i === selected ? "sel" : ""}">
          <span>${EntryUI.iconFor(h)}</span>
          <span class="cmdk-name">${escapeHtml(h.name)}</span>
          ${h.status === "missing" ? `<span class="badge missing">verschwunden</span>` : ""}
          <span class="cmdk-path muted small">${escapeHtml(h.path)}</span>
          <span class="badge source">${escapeHtml(h.source_label)}</span>
        </li>`)
      .join("");
  }

  async function runSearch() {
    const q = input.value.trim();
    if (!q) {
      hits = [];
      listEl.innerHTML = `<li class="muted small cmdk-empty">Tippen, um zu suchen …</li>`;
      return;
    }
    try {
      hits = await api(`/api/search?q=${encodeURIComponent(q)}&limit=15`);
      selected = 0;
      render();
    } catch (_e) {
      listEl.innerHTML = `<li class="muted small cmdk-empty">Suche fehlgeschlagen.</li>`;
    }
  }

  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      if (overlay && !overlay.hidden) close();
      else open();
    }
  });
})();
