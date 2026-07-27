// Suche – nutzt EntryUI für die Annotationen an jedem Treffer.

const resultsEl = document.getElementById("results");
const form = document.getElementById("search-form");
const input = document.getElementById("search-input");
const statusSel = document.getElementById("search-status");
const fieldsSel = document.getElementById("search-fields");
const kindSel = document.getElementById("search-kind");

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
  // Suchbereich (Name / Pfad / Notizen) und Typ (Datei / Ordner).
  if (fieldsSel.value) params.set("fields", fieldsSel.value);
  if (kindSel.value) params.set("is_dir", kindSel.value === "dirs" ? "true" : "false");
  const hits = await api(`/api/search?${params.toString()}`);
  if (!hits.length) {
    resultsEl.innerHTML = `<p class="muted">Keine Treffer${q ? ` für „${escapeHtml(q)}“` : ""}.</p>`;
    return;
  }
  resultsEl.innerHTML = hits.map(hitHtml).join("");
}

// Filter wirken sofort – ohne die Suche noch einmal abschicken zu müssen.
for (const sel of [statusSel, fieldsSel, kindSel]) {
  sel.addEventListener("change", () => {
    if (!input.value.trim() && !kindSel.value) return;
    assistResultEl.hidden = true;
    doSearch().catch((err) => (resultsEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`));
  });
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  assistResultEl.hidden = true;
  doSearch().catch((err) => (resultsEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`));
});

// --- Suchassistent ----------------------------------------------------------
//
// Das Modell übersetzt die Frage in dieselben Filter, die die Suche ohnehin
// kennt – das Ergebnis wird deshalb offen angezeigt („so habe ich das
// verstanden“), statt als Blackbox nur Treffer auszuwerfen.

const assistToggle = document.getElementById("assist-toggle");
const assistForm = document.getElementById("assist-form");
const assistQuestion = document.getElementById("assist-question");
const assistSetting = document.getElementById("assist-setting");
const assistPrompt = document.getElementById("assist-prompt");
const assistHint = document.getElementById("assist-hint");
const assistResultEl = document.getElementById("assist-result");

let assistLoaded = false;

async function loadAssistOptions() {
  if (assistLoaded) return;
  assistLoaded = true;
  try {
    const opts = await api("/api/llm/features/search");
    for (const s of opts.settings) {
      assistSetting.add(new Option(`${s.label} (${s.model || "ohne Modell"})`, s.id));
    }
    for (const p of opts.prompts) {
      assistPrompt.add(new Option(p.name, p.id));
    }
    if (!opts.settings.length) {
      assistHint.innerHTML =
        'Noch kein Modell für die Suche freigegeben – auf der Seite <a href="/llm">KI</a> ein LLM-Setting dem Feature „Suche“ zuordnen.';
      assistHint.hidden = false;
      assistForm.querySelector('button[type="submit"]').disabled = true;
    }
  } catch (err) {
    assistHint.textContent = err.message;
    assistHint.hidden = false;
  }
}

function filterChips(f) {
  const chips = [];
  if (f.query) chips.push(`Suchwörter: „${f.query}“`);
  if (f.source_label) chips.push(`Quelle: ${f.source_label}`);
  if (f.ext.length) chips.push(`Endung: ${f.ext.map((e) => `.${e}`).join(", ")}`);
  if (f.modified_after) chips.push(`geändert ab ${f.modified_after}`);
  if (f.modified_before) chips.push(`geändert bis ${f.modified_before}`);
  if (f.min_size) chips.push(`mindestens ${Math.round(f.min_size / 1048576)} MB`);
  if (f.max_size) chips.push(`höchstens ${Math.round(f.max_size / 1048576)} MB`);
  if (f.status) chips.push(f.status === "missing" ? "verschwunden" : "vorhanden");
  if (f.is_dir === true) chips.push("nur Ordner");
  if (f.is_dir === false) chips.push("nur Dateien");
  return chips.length
    ? chips.map((c) => `<span class="badge">${escapeHtml(c)}</span>`).join(" ")
    : `<span class="muted small">keine Einschränkung erkannt</span>`;
}

async function runAssist() {
  const question = assistQuestion.value.trim();
  if (!question || !assistSetting.value) return;
  assistResultEl.hidden = false;
  assistResultEl.innerHTML = `<p class="muted small">Der Assistent denkt nach …</p>`;
  resultsEl.innerHTML = "";
  const res = await api("/api/search/assist", {
    method: "POST",
    body: {
      question,
      setting_id: Number(assistSetting.value),
      prompt_id: assistPrompt.value ? Number(assistPrompt.value) : null,
    },
  });
  assistResultEl.innerHTML = `
    ${res.explanation ? `<p class="assist-explain">${escapeHtml(res.explanation)}</p>` : ""}
    <div class="assist-filters">${filterChips(res.filters)}</div>
    <p class="muted small">${res.hits.length} Treffer</p>`;
  resultsEl.innerHTML = res.hits.length
    ? res.hits.map(hitHtml).join("")
    : `<p class="muted">Keine Treffer zu dieser Frage.</p>`;
  // Die erkannten Suchwörter ins normale Feld übernehmen – von dort lässt sich
  // ohne Modell weitersuchen und verfeinern.
  if (res.filters.query) input.value = res.filters.query;
}

assistToggle.addEventListener("click", async () => {
  assistForm.hidden = !assistForm.hidden;
  if (!assistForm.hidden) {
    await loadAssistOptions();
    assistQuestion.focus();
  }
});

assistForm.addEventListener("submit", (e) => {
  e.preventDefault();
  runAssist().catch((err) => {
    assistResultEl.hidden = false;
    assistResultEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
  });
});

// Annotations-Interaktionen einmalig per Delegation verdrahten.
EntryUI.wireEntryActions(resultsEl);
