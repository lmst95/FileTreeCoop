// Suche – nutzt EntryUI für die Annotationen an jedem Treffer.

const resultsEl = document.getElementById("results");
const form = document.getElementById("search-form");
const input = document.getElementById("search-input");
const modeSel = document.getElementById("search-mode");
const statusSel = document.getElementById("search-status");
const fieldsSel = document.getElementById("search-fields");
const kindSel = document.getElementById("search-kind");
const modeHint = document.getElementById("mode-hint");

// Je Modus ein Satz Hilfe direkt unter der Leiste – die Syntax merkt sich
// niemand, und ein falsch verstandener Modus sieht aus wie „nichts gefunden“.
const MODE_HINTS = {
  smart: 'Volltext über Name, Pfad und Notizen. <code>-wort</code> schließt aus, ' +
    '<code>"zwei wörter"</code> sucht die Wortfolge, <code>OR</code> erlaubt Alternativen.',
  exact: 'Wörtliche Zeichenkette, genau so wie eingegeben – gut für <code>2026_01</code> ' +
    'oder <code>v1.2-final</code>, was die Volltextsuche in Wörter zerlegt.',
  glob: 'Platzhalter wie im Dateimanager: <code>*.pdf</code>, <code>Rechnung_20??</code>, ' +
    '<code>Projekte/**/alt</code>. Das Muster muss vollständig passen.',
  regex: 'Regulärer Ausdruck (Python-Syntax), Groß-/Kleinschreibung egal: ' +
    '<code>^IMG_\\d{4}\\.(jpg|png)$</code>.',
};

function showModeHint() {
  modeHint.innerHTML = MODE_HINTS[modeSel.value] || "";
}

function hitHtml(h) {
  const statusBadge = h.status === "missing"
    ? `<span class="badge missing">verschwunden</span>` : "";
  // Direkt aus dem Treffer eine Ignorierregel vorbereiten (Ordner -> Unterbaum,
  // Datei -> Name); abgeschickt wird sie erst im Formular.
  const ignoreBtn = `<button class="act" data-ignore title="dauerhaft ausblenden">🚫</button>`;
  return `<div class="card hit entry" data-entry="${h.entry_id}" data-source="${h.source_id}" data-path="${escapeHtml(h.path)}" data-name="${escapeHtml(h.name)}" data-dir="${h.is_dir ? "1" : ""}">
    <div class="entry-row">
      <span class="entry-name">${EntryUI.iconFor(h)} ${escapeHtml(h.name)}</span>
      ${statusBadge}
      <span class="badge source">${escapeHtml(h.source_label)}</span>
      ${EntryUI.annChipsHtml(h.annotations, h.has_new)}
      <span class="row-spacer"></span>
      ${EntryUI.actionsHtml(ignoreBtn)}
    </div>
    <div class="hit-path muted small">${escapeHtml(h.path)}</div>
    ${EntryUI.detailHtml(h.annotations, true)}
  </div>`;
}

async function doSearch() {
  const q = input.value.trim();
  const params = new URLSearchParams({ q });
  if (modeSel.value !== "smart") params.set("mode", modeSel.value);
  if (statusSel.value) params.set("status", statusSel.value);
  // Suchbereich (Name / Pfad / Notizen) und Typ (Datei / Ordner).
  if (fieldsSel.value) params.set("fields", fieldsSel.value);
  if (kindSel.value) params.set("is_dir", kindSel.value === "dirs" ? "true" : "false");
  if (!applyIgnores.checked) params.set("apply_ignores", "false");
  const hits = await api(`/api/search?${params.toString()}`);
  if (!hits.length) {
    resultsEl.innerHTML = `<p class="muted">Keine Treffer${q ? ` für „${escapeHtml(q)}“` : ""}.</p>`;
    return;
  }
  resultsEl.innerHTML = hits.map(hitHtml).join("");
}

// Filter wirken sofort – ohne die Suche noch einmal abschicken zu müssen.
for (const sel of [modeSel, statusSel, fieldsSel, kindSel]) {
  sel.addEventListener("change", () => {
    if (sel === modeSel) showModeHint();
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

// --- Ignorierregeln ---------------------------------------------------------
//
// Gespeicherte Ausschlüsse: ein Ordner (samt allem darunter) oder ein
// Dateiname-Muster taucht in keiner Suche mehr auf. Der Index bleibt vollständig
// – die Regel blendet nur aus und lässt sich jederzeit abschalten.

const ignoreToggle = document.getElementById("ignore-toggle");
const ignoreBox = document.getElementById("ignore-box");
const ignoreForm = document.getElementById("ignore-form");
const ignoreKind = document.getElementById("ignore-kind");
const ignorePattern = document.getElementById("ignore-pattern");
const ignoreSource = document.getElementById("ignore-source");
const ignoreList = document.getElementById("ignore-list");
const applyIgnores = document.getElementById("ignore-apply");

const KIND_LABEL = { path: "Ordner/Pfad", name: "Dateiname" };
let ignoreLoaded = false;

function ruleRowHtml(r) {
  const where = r.source_label ? escapeHtml(r.source_label) : "alle Quellen";
  return `<div class="ignore-rule${r.active ? "" : " off"}" data-rule="${r.id}">
    <label class="ignore-active" title="${r.active ? "aktiv" : "abgeschaltet"}">
      <input type="checkbox" data-rule-active ${r.active ? "checked" : ""}>
    </label>
    <span class="badge">${KIND_LABEL[r.kind] || r.kind}</span>
    <code class="ignore-pattern">${escapeHtml(r.pattern)}</code>
    <span class="muted small">${where}</span>
    <span class="row-spacer"></span>
    <button type="button" class="act" data-rule-delete title="Regel löschen">🗑</button>
  </div>`;
}

function renderRules(rules) {
  ignoreList.innerHTML = rules.length
    ? rules.map(ruleRowHtml).join("")
    : `<p class="muted small">Noch nichts ausgeblendet.</p>`;
}

async function loadIgnores() {
  if (!ignoreLoaded) {
    ignoreLoaded = true;
    // Quellen einmalig für die Auswahl „wo gilt die Regel“.
    try {
      for (const s of await api("/api/sources")) {
        ignoreSource.add(new Option(s.label, s.id));
      }
    } catch { /* ohne Quellenliste bleibt „alle Quellen“ – kein Grund zu scheitern */ }
  }
  renderRules(await api("/api/ignores"));
}

// Nach jeder Änderung an den Regeln die aktuelle Suche auffrischen, damit die
// Wirkung sofort sichtbar wird.
async function refreshAfterRules() {
  await loadIgnores();
  if (input.value.trim() || kindSel.value) await doSearch();
}

ignoreToggle.addEventListener("click", async () => {
  ignoreBox.hidden = !ignoreBox.hidden;
  if (!ignoreBox.hidden) {
    await loadIgnores();
    ignorePattern.focus();
  }
});

ignoreForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const pattern = ignorePattern.value.trim();
  if (!pattern) return;
  try {
    await api("/api/ignores", {
      method: "POST",
      body: {
        kind: ignoreKind.value,
        pattern,
        source_id: ignoreSource.value ? Number(ignoreSource.value) : null,
      },
    });
    ignorePattern.value = "";
    await refreshAfterRules();
    toast("Regel gespeichert – gilt ab sofort für jede Suche.", "success");
  } catch (err) {
    toast(err.message, "error");
  }
});

ignoreList.addEventListener("click", async (e) => {
  const row = e.target.closest("[data-rule]");
  if (!row || !e.target.closest("[data-rule-delete]")) return;
  try {
    await api(`/api/ignores/${row.dataset.rule}`, { method: "DELETE" });
    await refreshAfterRules();
  } catch (err) {
    toast(err.message, "error");
  }
});

ignoreList.addEventListener("change", async (e) => {
  const box = e.target.closest("[data-rule-active]");
  if (!box) return;
  const row = box.closest("[data-rule]");
  try {
    await api(`/api/ignores/${row.dataset.rule}`, {
      method: "PATCH",
      body: { active: box.checked },
    });
    await refreshAfterRules();
  } catch (err) {
    toast(err.message, "error");
  }
});

applyIgnores.addEventListener("change", () => {
  if (!input.value.trim() && !kindSel.value) return;
  doSearch().catch((err) => (resultsEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`));
});

// „🚫“ am Treffer füllt das Formular vor – abgeschickt wird bewusst von Hand,
// damit niemand versehentlich einen halben Baum ausblendet.
resultsEl.addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-ignore]");
  if (!btn) return;
  const node = btn.closest("[data-entry]");
  const isDir = node.dataset.dir === "1";
  ignoreBox.hidden = false;
  await loadIgnores();
  ignoreKind.value = isDir ? "path" : "name";
  ignorePattern.value = isDir ? node.dataset.path : node.dataset.name;
  ignoreSource.value = node.dataset.source;
  ignorePattern.focus();
  ignorePattern.select();
});

showModeHint();

// Annotations-Interaktionen einmalig per Delegation verdrahten.
EntryUI.wireEntryActions(resultsEl);
