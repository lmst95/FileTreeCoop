// Suche – nutzt EntryUI für die Annotationen an jedem Treffer.

const resultsEl = document.getElementById("results");
const form = document.getElementById("search-form");
const input = document.getElementById("search-input");
const statusSel = document.getElementById("search-status");

function hitHtml(h) {
  const statusBadge = h.status === "missing"
    ? `<span class="badge missing">verschwunden</span>` : "";
  return `<div class="card hit entry" data-entry="${h.entry_id}" data-source="${h.source_id}" data-path="${escapeHtml(h.path)}">
    <div class="entry-row">
      <span class="entry-name">${EntryUI.iconFor(h)} ${escapeHtml(h.name)}</span>
      ${statusBadge}
      <span class="badge source">${escapeHtml(h.source_label)}</span>
      ${EntryUI.annChipsHtml(h.annotations, h.has_new)}
      <span class="row-spacer"></span>
      ${EntryUI.actionsHtml()}
    </div>
    <div class="hit-path muted small">${escapeHtml(h.path)}</div>
    ${EntryUI.detailHtml(h.annotations, true)}
  </div>`;
}

async function doSearch() {
  const q = input.value.trim();
  const params = new URLSearchParams({ q });
  if (statusSel.value) params.set("status", statusSel.value);
  const hits = await api(`/api/search?${params.toString()}`);
  if (!hits.length) {
    resultsEl.innerHTML = `<p class="muted">Keine Treffer${q ? ` für „${escapeHtml(q)}“` : ""}.</p>`;
    return;
  }
  resultsEl.innerHTML = hits.map(hitHtml).join("");
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  doSearch().catch((err) => (resultsEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`));
});

// Annotations-Interaktionen einmalig per Delegation verdrahten.
EntryUI.wireEntryActions(resultsEl);
