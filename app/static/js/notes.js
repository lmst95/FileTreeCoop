// Notizen-Pinnwand: freie Notizen + an Dateien/Ordner geheftete Notizen.

const board = document.getElementById("notes-board");
const COLORS = ["yellow", "pink", "blue", "green", "purple", "orange"];

let currentNotes = [];
let overlay = null;
let activeColorFilter = null;

function colorClass(c) {
  return "note-" + (COLORS.includes(c) ? c : "yellow");
}

// Deterministische, leichte Schräglage je Notiz (kein Neu-Mischen beim Rerender).
function tiltFor(id) {
  return (((id * 37) % 7) - 3) * 0.9;
}

function fmtDate(iso) {
  if (!iso) return "";
  return new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleString("de-DE");
}

function colorPickerHtml(current) {
  const sel = COLORS.includes(current) ? current : "yellow";
  return `<div class="note-color-picker">${COLORS.map((c) =>
    `<button type="button" class="note-color-dot note-${c}${c === sel ? " selected" : ""}"
             data-color="${c}" title="${c}"></button>`
  ).join("")}</div>`;
}

// --- Pinnwand-Raster ---------------------------------------------------------

function noteCardHtml(n) {
  const attach = n.entry_name
    ? `<a class="sticky-note-attach" href="/browse?source=${n.source_id}&path=${encodeURIComponent(n.entry_path)}"
         title="${escapeHtml(n.entry_path)}" data-no-open>📎 ${escapeHtml(n.entry_name)}</a>`
    : `<span class="sticky-note-attach muted">frei</span>`;
  const author = !n.is_mine
    ? `<span class="muted small">${escapeHtml(n.author_name || "?")}</span>` : "";
  const shareBadge = n.is_mine && n.share_count
    ? `<span class="muted small" title="geteilt mit ${n.share_count} Kolleg(en)">👥 ${n.share_count}</span>`
    : "";
  const body = n.body ? escapeHtml(n.body) : `<span class="muted">(leer)</span>`;
  return `<div class="sticky-note ${colorClass(n.color)}" style="--tilt:${tiltFor(n.id).toFixed(1)}deg" data-note="${n.id}">
      <div class="sticky-note-body">${body}</div>
      <div class="sticky-note-meta">${attach}${author}${shareBadge}</div>
    </div>`;
}

async function loadNotes() {
  currentNotes = await api("/api/annotations/notes");
  renderFilterBar();
  renderBoard();
}

function renderFilterBar() {
  const bar = document.getElementById("notes-filter");
  if (!bar) return;
  const allBtn = `<button type="button" class="note-color-dot note-filter-all${activeColorFilter === null ? " selected" : ""}"
      data-filter-color="" title="Alle">Alle</button>`;
  const dots = COLORS.map((c) =>
    `<button type="button" class="note-color-dot note-${c}${activeColorFilter === c ? " selected" : ""}"
       data-filter-color="${c}" title="${c}"></button>`
  ).join("");
  bar.innerHTML = `<div class="note-color-picker">${allBtn}${dots}</div>`;
}

function renderBoard() {
  const notes = activeColorFilter
    ? currentNotes.filter((n) => (COLORS.includes(n.color) ? n.color : "yellow") === activeColorFilter)
    : currentNotes;
  board.innerHTML = notes.length
    ? notes.map(noteCardHtml).join("")
    : currentNotes.length
      ? `<p class="muted">Keine Notizen mit dieser Farbe.</p>`
      : `<p class="muted">Noch keine Notizen. Leg mit „+ Neue Notiz“ los oder hefte im Baum eine Notiz an eine Datei.</p>`;
}

document.getElementById("notes-filter").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-filter-color]");
  if (!btn) return;
  const color = btn.dataset.filterColor;
  activeColorFilter = color || null;
  renderFilterBar();
  renderBoard();
});

board.addEventListener("click", (e) => {
  if (e.target.closest("[data-no-open]")) return; // Datei-Link – eigenes Ziel
  const card = e.target.closest("[data-note]");
  if (!card) return;
  const note = currentNotes.find((n) => n.id === Number(card.dataset.note));
  if (note) openNoteModal(note);
});

// --- Modal-Grundgerüst --------------------------------------------------------

function closeModal() {
  if (overlay) {
    overlay.remove();
    overlay = null;
  }
}

function openOverlay(innerHtml) {
  closeModal();
  overlay = document.createElement("div");
  overlay.className = "note-modal-overlay";
  overlay.innerHTML = `<div class="note-modal-box">${innerHtml}</div>`;
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) closeModal();
  });
  document.body.appendChild(overlay);
  return overlay.querySelector(".note-modal-box");
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && overlay) closeModal();
});

// --- Notiz ansehen/bearbeiten --------------------------------------------------

function aiPanelHtml(n) {
  // "übernehmen" nur, wenn der Nutzer die Notiz auch bearbeiten darf.
  const applyBtn = n.is_mine
    ? `<button type="button" class="tiny primary" data-ai-apply>Als Notiz übernehmen</button>`
    : "";
  return `
    <div class="note-ai">
      <div class="note-ai-controls">
        <label class="small">Prompt
          <select id="ai-prompt"><option value="">(lädt …)</option></select></label>
        <label class="small">LLM-Setting
          <select id="ai-setting"><option value="">(lädt …)</option></select></label>
        <button type="button" class="tiny primary" data-ai-run>Überarbeiten</button>
      </div>
      <p class="privacy-hint small">🔒 Der Notiztext wird zur Überarbeitung an den gewählten Anbieter übertragen.</p>
      <div id="ai-status" class="muted small"></div>
      <textarea id="ai-output" class="note-ai-output" placeholder="Noch kein Ergebnis. Wähle Prompt & Setting und klicke „Überarbeiten“."></textarea>
      <div class="note-modal-actions" id="ai-actions" hidden>
        <button type="button" class="tiny" data-ai-copy>Kopieren</button>
        ${applyBtn}
        <button type="button" class="tiny" data-ai-new>Als neue Notiz speichern</button>
      </div>
    </div>`;
}

function noteDetailHtml(n) {
  const attach = n.entry_name
    ? `<a href="/browse?source=${n.source_id}&path=${encodeURIComponent(n.entry_path)}"
         title="${escapeHtml(n.entry_path)}">📎 ${escapeHtml(n.entry_name)}</a>`
    : `<span class="muted">frei (kein Datei-Bezug)</span>`;
  const bodyBlock = n.is_mine
    ? `<textarea id="note-edit-body">${escapeHtml(n.body)}</textarea>`
    : `<div class="note-modal-body-text">${escapeHtml(n.body)}</div>`;
  const actions = n.is_mine
    ? `<button type="button" class="tiny primary" data-save-note>speichern</button>
       <button type="button" class="link-btn danger tiny" data-delete-note>löschen</button>`
    : "";
  const shareSection = n.is_mine && !n.entry_id
    ? `<div class="note-share-section">
        <h3 class="small">Geteilt mit</h3>
        <ul class="share-list small" id="note-share-list"><li class="muted small">lädt …</li></ul>
        <form id="note-share-form" class="inline-form">
          <input name="identifier" placeholder="E-Mail oder Username" required>
          <button type="submit" class="tiny">teilen</button>
        </form>
      </div>`
    : "";
  return `
    <div class="note-modal-head">
      <span class="badge">${escapeHtml(n.author_name || "?")}</span>
      ${attach}
      <span class="spacer"></span>
      <button type="button" class="link-btn tiny" data-close-modal title="schließen">✕</button>
    </div>
    <div class="tabs note-tabs">
      <button type="button" class="tab active" data-note-tab="original">Original</button>
      <button type="button" class="tab" data-note-tab="ai">KI-überarbeitet</button>
    </div>
    <div class="note-tab-panel active" data-note-panel="original">
      ${n.is_mine ? colorPickerHtml(n.color) : ""}
      ${bodyBlock}
      <div class="note-modal-actions">${actions}</div>
      ${shareSection}
      <p class="muted small">Erstellt ${fmtDate(n.created_at)} · zuletzt geändert ${fmtDate(n.updated_at)}</p>
    </div>
    <div class="note-tab-panel" data-note-panel="ai">
      ${aiPanelHtml(n)}
    </div>`;
}

async function loadNoteShares(noteId, box) {
  const list = box.querySelector("#note-share-list");
  if (!list) return;
  try {
    const shares = await api(`/api/annotations/${noteId}/shares`);
    list.innerHTML = shares.length
      ? shares.map((sh) => `<li class="share-row">
          <span>${escapeHtml(sh.display_name)} <span class="muted small">@${escapeHtml(sh.username)}</span></span>
          <button class="link-btn danger tiny" data-remove-note-share data-user="${sh.user_id}">entfernen</button>
        </li>`).join("")
      : `<li class="muted small">Noch mit niemandem geteilt.</li>`;
  } catch (err) {
    list.innerHTML = `<li class="error small">${escapeHtml(err.message)}</li>`;
  }
}

// --- KI-Überarbeitung (Tab „KI-überarbeitet") --------------------------------

// Feature-Optionen (Prompts + Settings für "notes") einmal je Seitenaufruf laden.
let notesLlmOptions = null;
async function getNotesLlmOptions() {
  if (notesLlmOptions === null) {
    notesLlmOptions = await api("/api/llm/features/notes");
  }
  return notesLlmOptions;
}

function initAiPanel(note, box) {
  const promptSel = box.querySelector("#ai-prompt");
  const settingSel = box.querySelector("#ai-setting");
  const runBtn = box.querySelector("[data-ai-run]");
  const statusEl = box.querySelector("#ai-status");
  const outputEl = box.querySelector("#ai-output");
  const actionsEl = box.querySelector("#ai-actions");
  if (!promptSel || !settingSel) return;

  function currentInput() {
    // Falls der Nutzer den Originaltext gerade bearbeitet, diesen verwenden.
    const edit = box.querySelector("#note-edit-body");
    return (edit ? edit.value : note.body) || "";
  }

  function showActions(show) {
    actionsEl.hidden = !show;
  }

  (async () => {
    let opts;
    try {
      opts = await getNotesLlmOptions();
    } catch (err) {
      statusEl.innerHTML = `<span class="error">${escapeHtml(err.message)}</span>`;
      return;
    }
    if (!opts.settings.length) {
      promptSel.innerHTML = settingSel.innerHTML = `<option value="">–</option>`;
      runBtn.disabled = true;
      statusEl.innerHTML =
        `Noch kein LLM-Setting für Notizen. Lege unter <a href="/llm">KI-Einstellungen</a> ` +
        `eine Verbindung + ein Setting an und ordne es dem Feature „Notizen“ zu.`;
      return;
    }
    promptSel.innerHTML =
      `<option value="">(ohne Prompt – nur Text)</option>` +
      opts.prompts.map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join("");
    settingSel.innerHTML = opts.settings
      .map((s) => `<option value="${s.id}">${escapeHtml(s.label)} · ${escapeHtml(s.model || "?")}</option>`)
      .join("");

    // Letztes Ergebnis für diese Notiz vorbefüllen (falls vorhanden).
    try {
      const runs = await api(`/api/llm/runs?target_kind=annotation&target_id=${note.id}&limit=1`);
      const last = runs.find((r) => r.status === "ok");
      if (last) {
        outputEl.value = last.output_text;
        showActions(true);
        statusEl.textContent = `Letztes Ergebnis von ${fmtDate(last.created_at)}` +
          (last.meta && last.meta.model ? ` · ${last.meta.model}` : "");
        if (last.setting_id) settingSel.value = String(last.setting_id);
        if (last.prompt_id) promptSel.value = String(last.prompt_id);
      }
    } catch (_e) {
      /* Verlauf ist optional */
    }
  })();

  runBtn.addEventListener("click", async () => {
    const input = currentInput().trim();
    if (!input) {
      toast("Die Notiz ist leer – nichts zu überarbeiten.", "error");
      return;
    }
    runBtn.disabled = true;
    statusEl.classList.remove("error");
    statusEl.textContent = "Überarbeite …";
    try {
      const res = await api("/api/llm/run", {
        method: "POST",
        body: {
          setting_id: Number(settingSel.value),
          prompt_id: promptSel.value ? Number(promptSel.value) : null,
          input_text: input,
          target_kind: "annotation",
          target_id: note.id,
        },
      });
      outputEl.value = res.output_text;
      showActions(true);
      statusEl.textContent = `Fertig${res.model ? " · " + res.model : ""}.`;
    } catch (err) {
      statusEl.innerHTML = `<span class="error">${escapeHtml(err.message)}</span>`;
    } finally {
      runBtn.disabled = false;
    }
  });

  actionsEl.addEventListener("click", async (e) => {
    const text = outputEl.value.trim();
    if (e.target.closest("[data-ai-copy]")) {
      try {
        await navigator.clipboard.writeText(outputEl.value);
        toast("In die Zwischenablage kopiert.", "success");
      } catch (_e) {
        toast("Kopieren nicht möglich.", "error");
      }
      return;
    }
    if (e.target.closest("[data-ai-apply]")) {
      if (!text) return;
      try {
        await api(`/api/annotations/${note.id}`, { method: "PATCH", body: { body: text } });
        closeModal();
        await loadNotes();
        toast("Überarbeitung übernommen.", "success");
      } catch (err) {
        toast(err.message, "error");
      }
      return;
    }
    if (e.target.closest("[data-ai-new]")) {
      if (!text) return;
      try {
        await api("/api/annotations", {
          method: "POST",
          body: { type: "note", body: text, color: note.color || "" },
        });
        closeModal();
        await loadNotes();
        toast("Als neue Notiz gespeichert.", "success");
      } catch (err) {
        toast(err.message, "error");
      }
    }
  });
}

async function openNoteModal(note) {
  const box = openOverlay(noteDetailHtml(note));
  let selectedColor = COLORS.includes(note.color) ? note.color : "yellow";

  // Tab-Wechsel Original <-> KI-überarbeitet.
  box.querySelectorAll("[data-note-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.noteTab;
      box.querySelectorAll("[data-note-tab]").forEach((b) =>
        b.classList.toggle("active", b === btn));
      box.querySelectorAll("[data-note-panel]").forEach((p) =>
        p.classList.toggle("active", p.dataset.notePanel === key));
    });
  });

  initAiPanel(note, box);

  box.addEventListener("click", async (e) => {
    if (e.target.closest("[data-close-modal]")) {
      closeModal();
      return;
    }
    const dot = e.target.closest("[data-color]");
    if (dot) {
      selectedColor = dot.dataset.color;
      box.querySelectorAll(".note-color-dot").forEach((d) =>
        d.classList.toggle("selected", d.dataset.color === selectedColor));
      return;
    }
    if (e.target.closest("[data-save-note]")) {
      const body = box.querySelector("#note-edit-body").value.trim();
      if (!body) {
        toast("Notiz darf nicht leer sein.", "error");
        return;
      }
      try {
        await api(`/api/annotations/${note.id}`, {
          method: "PATCH",
          body: { body, color: selectedColor },
        });
        closeModal();
        await loadNotes();
        toast("Notiz gespeichert.", "success");
      } catch (err) {
        toast(err.message, "error");
      }
      return;
    }
    if (e.target.closest("[data-delete-note]")) {
      if (!confirm("Notiz wirklich löschen?")) return;
      try {
        await api(`/api/annotations/${note.id}`, { method: "DELETE" });
        closeModal();
        await loadNotes();
        toast("Notiz gelöscht.", "success");
      } catch (err) {
        toast(err.message, "error");
      }
      return;
    }
    const rm = e.target.closest("[data-remove-note-share]");
    if (rm) {
      await api(`/api/annotations/${note.id}/shares/${rm.dataset.user}`, { method: "DELETE" });
      await loadNoteShares(note.id, box);
      await loadNotes(); // Zähler auf der Karte aktualisieren
    }
  });

  const shareForm = box.querySelector("#note-share-form");
  if (shareForm) {
    shareForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const f = new FormData(shareForm);
      try {
        await api(`/api/annotations/${note.id}/shares`, {
          method: "POST",
          body: { identifier: f.get("identifier") },
        });
        shareForm.reset();
        await loadNoteShares(note.id, box);
        await loadNotes(); // Zähler auf der Karte aktualisieren
      } catch (err) {
        toast(err.message, "error");
      }
    });
  }

  if (note.is_mine && !note.entry_id) await loadNoteShares(note.id, box);
}

// --- Neue Notiz ----------------------------------------------------------------

function createModalHtml() {
  return `
    <div class="note-modal-head">
      <strong>Neue Notiz</strong>
      <span class="spacer"></span>
      <button type="button" class="link-btn tiny" data-close-modal title="schließen">✕</button>
    </div>
    ${colorPickerHtml("yellow")}
    <textarea id="new-note-body" placeholder="Was möchtest du dir notieren? …"></textarea>
    <div class="note-attach-picker">
      <label class="small">An Datei/Ordner anheften (optional)
        <input id="new-note-attach-q" type="text" placeholder="Datei oder Ordner suchen …" autocomplete="off">
      </label>
      <ul class="note-attach-results" id="new-note-attach-results" hidden></ul>
      <div id="new-note-attach-chosen"></div>
    </div>
    <div class="note-modal-actions">
      <button type="button" class="primary tiny" data-create-note>Notiz erstellen</button>
    </div>`;
}

function openCreateModal() {
  const box = openOverlay(createModalHtml());
  let selectedColor = "yellow";
  let chosenEntry = null;
  let lastHits = [];
  let debounceTimer = null;

  box.querySelector("#new-note-body").focus();

  const qInput = box.querySelector("#new-note-attach-q");
  const resultsEl = box.querySelector("#new-note-attach-results");
  const chosenEl = box.querySelector("#new-note-attach-chosen");

  function renderChosen() {
    resultsEl.hidden = true;
    resultsEl.innerHTML = "";
    if (chosenEntry) {
      qInput.value = "";
      qInput.hidden = true;
      chosenEl.innerHTML = `<div class="note-attach-chosen">📎 ${escapeHtml(chosenEntry.path)}
          <button type="button" class="link-btn tiny" data-clear-attach title="entfernen">✕</button></div>`;
    } else {
      qInput.hidden = false;
      chosenEl.innerHTML = "";
    }
  }

  qInput.addEventListener("input", () => {
    clearTimeout(debounceTimer);
    const val = qInput.value.trim();
    if (!val) {
      resultsEl.hidden = true;
      resultsEl.innerHTML = "";
      return;
    }
    debounceTimer = setTimeout(async () => {
      try {
        lastHits = await api(`/api/search?q=${encodeURIComponent(val)}&limit=10`);
        resultsEl.innerHTML = lastHits.length
          ? lastHits.map((h, i) => `<li data-attach-hit="${i}">${EntryUI.iconFor(h)} ${escapeHtml(h.name)}
              <span class="muted small">${escapeHtml(h.path)} · ${escapeHtml(h.source_label)}</span></li>`).join("")
          : `<li class="muted small">Keine Treffer.</li>`;
        resultsEl.hidden = false;
      } catch (_e) {
        /* Attach-Suche ist nice-to-have – Fehler still schlucken */
      }
    }, 200);
  });

  box.addEventListener("click", async (e) => {
    if (e.target.closest("[data-close-modal]")) {
      closeModal();
      return;
    }
    const dot = e.target.closest("[data-color]");
    if (dot) {
      selectedColor = dot.dataset.color;
      box.querySelectorAll(".note-color-dot").forEach((d) =>
        d.classList.toggle("selected", d.dataset.color === selectedColor));
      return;
    }
    if (e.target.closest("[data-clear-attach]")) {
      chosenEntry = null;
      renderChosen();
      return;
    }
    const hit = e.target.closest("[data-attach-hit]");
    if (hit) {
      chosenEntry = lastHits[Number(hit.dataset.attachHit)];
      renderChosen();
      return;
    }
    if (e.target.closest("[data-create-note]")) {
      const body = box.querySelector("#new-note-body").value.trim();
      if (!body) {
        toast("Notiz darf nicht leer sein.", "error");
        return;
      }
      try {
        await api("/api/annotations", {
          method: "POST",
          body: {
            type: "note",
            body,
            color: selectedColor,
            entry_id: chosenEntry ? chosenEntry.entry_id : null,
          },
        });
        closeModal();
        await loadNotes();
        toast("Notiz erstellt.", "success");
      } catch (err) {
        toast(err.message, "error");
      }
    }
  });
}

document.getElementById("new-note-btn").addEventListener("click", openCreateModal);

loadNotes().catch((err) => {
  board.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
});
