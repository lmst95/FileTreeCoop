// Gemeinsame, kompakte Eintrags-UI (Explorer-Stil) für Suche und Baum.
// Statt eines Typ-Dropdowns gibt es kleine Action-Icons pro Zeile; Annotationen
// erscheinen als kompakte Chips, Details/Editor klappen bei Bedarf auf.
//
// Erwartete Node-Struktur (Suche: .hit, Baum: .entry):
//   <div data-entry="ID" data-source="SID">
//     <div class="entry-row"> … <span class="ann-chips"></span> … <span class="entry-actions"></span></div>
//     <div class="entry-detail" hidden> <ul class="ann-list"></ul> <div class="ann-editor"></div> </div>
//     [ <div class="entry-children"></div>  // nur Baum ]
//   </div>

const TYPE_LABEL = { note: "Notiz", todo: "Todo", label: "Label", handover: "Übergabe" };
const TYPE_ICON = { note: "📝", todo: "☑", label: "🏷", handover: "➦" };
const STATUS_LABEL = { open: "offen", accepted: "angenommen", done: "erledigt" };
const PERM_LABEL = { read: "nur lesen", annotate: "annotieren" };

// --- Freigaben-Zeilen (Teilen-Panels in Quellen & Baum) ---------------------

// Eine Zeile je Freigabe/Einladung; ``removeLabel`` erlaubt „entfernen“
// (Baum, Quellen) vs. „löschen“ (Profil-Übersicht) je nach Kontext.
function shareRowsHtml(sourceId, shares, removeLabel = "entfernen") {
  if (!shares.length) {
    return `<li class="muted small">Noch mit niemandem geteilt.</li>`;
  }
  return shares.map((sh) => {
    const scope = sh.path_prefix
      ? `<span class="badge scope-badge" title="Teilbaum">📁 ${escapeHtml(sh.path_prefix)}</span>`
      : `<span class="badge">ganze Quelle</span>`;
    if (sh.pending) {
      return `<li class="share-row">
        <span>${escapeHtml(sh.email)}</span>
        <span class="badge pending-badge">eingeladen – wartet auf Registrierung</span>
        ${scope}
        <span class="badge">${escapeHtml(PERM_LABEL[sh.permission] || sh.permission)}</span>
        <button class="link-btn danger tiny" data-uninvite data-src="${sourceId}"
                data-invite="${sh.invite_id}">zurückziehen</button>
      </li>`;
    }
    return `<li class="share-row">
      <span>${escapeHtml(sh.display_name)} <span class="muted small">@${escapeHtml(sh.username)}</span></span>
      ${scope}
      <span class="badge">${escapeHtml(PERM_LABEL[sh.permission] || sh.permission)}</span>
      <button class="link-btn danger tiny" data-unshare data-src="${sourceId}"
              data-user="${sh.user_id}" data-prefix="${escapeHtml(sh.path_prefix)}">${removeLabel}</button>
    </li>`;
  }).join("");
}

// --- Dateityp-Icons ---------------------------------------------------------

const ICON_BY_EXT = {
  pdf: "📕", doc: "📝", docx: "📝", odt: "📝", rtf: "📝", txt: "📄", md: "📄",
  xls: "📊", xlsx: "📊", csv: "📊", ods: "📊",
  ppt: "📽️", pptx: "📽️", odp: "📽️", key: "📽️",
  png: "🖼️", jpg: "🖼️", jpeg: "🖼️", gif: "🖼️", svg: "🖼️", webp: "🖼️",
  bmp: "🖼️", tif: "🖼️", tiff: "🖼️", heic: "🖼️", ico: "🖼️",
  mp3: "🎵", wav: "🎵", flac: "🎵", ogg: "🎵", m4a: "🎵", aac: "🎵",
  mp4: "🎬", mov: "🎬", avi: "🎬", mkv: "🎬", webm: "🎬", wmv: "🎬",
  zip: "🗜️", rar: "🗜️", "7z": "🗜️", tar: "🗜️", gz: "🗜️", bz2: "🗜️", xz: "🗜️",
  py: "🐍", js: "💻", ts: "💻", jsx: "💻", tsx: "💻", html: "🌐", htm: "🌐",
  css: "🎨", json: "🔧", yaml: "🔧", yml: "🔧", toml: "🔧", xml: "🔧",
  sql: "🗃️", sh: "⚙️", c: "💻", cpp: "💻", h: "💻", java: "💻", go: "💻",
  rs: "💻", rb: "💎", php: "💻", exe: "⚙️", app: "⚙️", dmg: "💽", iso: "💽",
};

function extOf(hit) {
  if (hit.ext) return String(hit.ext).toLowerCase();
  const i = hit.name.lastIndexOf(".");
  return i > 0 ? hit.name.slice(i + 1).toLowerCase() : "";
}
function iconFor(hit) {
  if (hit.is_dir) return "📁";
  return ICON_BY_EXT[extOf(hit)] || "📄";
}

// --- Kompakte Chips (Zusammenfassung in der Zeile) --------------------------

// Termin-Helfer -------------------------------------------------------------

function todayIso() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function formatDue(iso) {
  const [y, m, d] = iso.split("-");
  return `${Number(d)}.${Number(m)}.${y}`;
}
// Frühester offener Termin – der ist in der Zeile die interessante Zahl.
function nextDue(anns) {
  const open = anns.filter((a) => a.due_date && !a.done).map((a) => a.due_date);
  return open.length ? open.sort()[0] : null;
}

function annChipsHtml(annotations, hasNew = false) {
  const anns = annotations || [];
  let chips = "";
  if (hasNew) {
    chips += `<span class="chip chip-new" title="Neue Notizen von Kollegen seit deinem letzten Besuch">●</span>`;
  }
  const labels = anns.filter((a) => a.type === "label");
  const notes = anns.filter((a) => a.type === "note").length;
  const todos = anns.filter((a) => a.type === "todo");
  const todosDone = todos.filter((a) => a.done).length;
  const handovers = anns.filter((a) => a.type === "handover");

  labels.forEach((l) => (chips += `<span class="chip chip-label">#${escapeHtml(l.label_value)}</span>`));
  if (notes) chips += `<span class="chip chip-note" title="Notizen">📝 ${notes}</span>`;
  if (todos.length) {
    const open = todosDone < todos.length ? " chip-open" : "";
    chips += `<span class="chip chip-todo${open}" title="Todos">☑ ${todosDone}/${todos.length}</span>`;
  }
  handovers.forEach((h) => (chips += `<span class="chip chip-handover" title="Übergabe">➦ ${escapeHtml(h.assignee_name || "?")}</span>`));

  const due = nextDue(anns);
  if (due) {
    const overdue = due < todayIso() ? " chip-overdue" : "";
    chips += `<span class="chip chip-due${overdue}" title="nächster Termin">📅 ${formatDue(due)}</span>`;
  }
  return `<span class="ann-chips" data-toggle-detail>${chips}</span>`;
}

// --- Action-Icons (rechts in der Zeile) -------------------------------------

// ``inTree``: im Baum selbst wäre ein „im Baum öffnen“-Knopf sinnlos, daher
// nur außerhalb (Suche, künftige Eintrags-Ansichten) einblenden.
function actionsHtml(extra = "", { inTree = false } = {}) {
  const openTree = inTree
    ? ""
    : `<button class="act" data-open-tree title="im Baum öffnen">🌳</button>`;
  return `<span class="entry-actions">
      <button class="act" data-act="note" title="Notiz hinzufügen">📝</button>
      <button class="act" data-act="todo" title="Todo hinzufügen">☑</button>
      <button class="act" data-act="label" title="Label hinzufügen">🏷</button>
      <button class="act" data-act="handover" title="Übergeben">➦</button>
      <button class="act act-copy" data-copy-path title="Pfad kopieren">📋</button>
      ${openTree}
      ${extra}
      <button class="act act-more" data-toggle-detail title="Details ein-/ausblenden">⋯</button>
    </span>`;
}

// --- Annotationsliste (im Detailbereich) ------------------------------------

function fmtAnnDate(iso) {
  if (!iso) return "";
  return new Date(iso + (iso.endsWith("Z") ? "" : "Z")).toLocaleDateString("de-DE");
}

function annItemHtml(a, replies = [], isReply = false) {
  const toggle = a.type === "todo"
    ? `<input type="checkbox" data-toggle="${a.id}" ${a.done ? "checked" : ""} title="erledigt">`
    : "";
  let text;
  if (a.type === "label") {
    text = `#${escapeHtml(a.label_value)}`;
  } else if (a.type === "handover") {
    const to = a.assignee_name ? `→ ${escapeHtml(a.assignee_name)}` : "→ ?";
    const st = `<span class="badge status-${a.status}">${STATUS_LABEL[a.status] || a.status}</span>`;
    text = `<strong>${to}</strong>${a.body ? " · " + escapeHtml(a.body) : ""} ${st}`;
  } else {
    text = escapeHtml(a.body);
  }
  // Termin nur dort, wo er Sinn ergibt – und direkt in der Liste änderbar.
  const dated = a.type === "todo" || a.type === "handover";
  const overdue = a.due_date && !a.done && a.due_date < todayIso() ? " overdue" : "";
  const due = dated
    ? `<input type="date" class="ann-due${overdue}" data-due="${a.id}"
         value="${a.due_date || ""}" title="Fällig am">`
    : "";
  // Wer hat's geschrieben? Für Kooperation die halbe Miete.
  const meta = `<span class="ann-meta muted small">${escapeHtml(a.author_name || "?")} · ${fmtAnnDate(a.created_at)}</span>`;
  const replyBtn = isReply
    ? ""
    : `<button class="link-btn tiny" data-reply-to="${a.id}" title="antworten">↩</button>`;
  const replyList = replies.length
    ? `<ul class="ann-replies">${replies.map((r) => annItemHtml(r, [], true)).join("")}</ul>`
    : "";
  return `<li class="ann ann-${a.type}${isReply ? " ann-reply" : ""}">
      ${toggle}
      <span class="ann-type">${isReply ? "↳" : TYPE_LABEL[a.type] || a.type}</span>
      <span class="ann-body ${a.done ? "done" : ""}">${text}</span>
      ${meta}
      ${due}
      ${replyBtn}
      <button class="link-btn danger tiny" data-del-ann="${a.id}" title="löschen">×</button>
      ${replyList}
    </li>`;
}
function annListHtml(annotations) {
  const anns = annotations || [];
  if (!anns.length) return `<ul class="ann-list"><li class="muted small">noch keine Einträge</li></ul>`;
  const replies = new Map();
  anns.filter((a) => a.parent_annotation_id).forEach((a) => {
    const arr = replies.get(a.parent_annotation_id) || [];
    arr.push(a);
    replies.set(a.parent_annotation_id, arr);
  });
  const tops = anns.filter((a) => !a.parent_annotation_id);
  return `<ul class="ann-list">${tops.map((a) => annItemHtml(a, replies.get(a.id) || [])).join("")}</ul>`;
}

function detailHtml(annotations, open = false) {
  return `<div class="entry-detail" ${open ? "" : "hidden"}>
      ${annListHtml(annotations)}
      <div class="ann-editor"></div>
    </div>`;
}

// --- Mitglieder-Cache (für Übergabe-Empfänger) ------------------------------

// Empfänger hängen vom Pfad ab (Teilbaum-Freigaben), daher nach source+path cachen.
const _memberCache = new Map();
async function membersFor(sourceId, path = "") {
  const key = `${sourceId}:${path}`;
  if (!_memberCache.has(key)) {
    const qs = path ? `?path=${encodeURIComponent(path)}` : "";
    _memberCache.set(key, await api(`/api/sources/${sourceId}/members${qs}`));
  }
  return _memberCache.get(key);
}
function invalidateMembers(sourceId) {
  const p = `${sourceId}:`;
  for (const k of [..._memberCache.keys()]) if (k.startsWith(p)) _memberCache.delete(k);
}

// --- Label-Vorschläge (Autocomplete) ----------------------------------------

// Bestehende Labels als <datalist>, damit keine Duett-Labels wie
// "rechnung" / "Rechnungen" entstehen. Einmal pro Seite geladen.
let _labelsLoaded = false;
async function ensureLabelDatalist() {
  if (_labelsLoaded) return;
  _labelsLoaded = true;
  try {
    const labels = await api("/api/annotations/labels");
    const dl = document.createElement("datalist");
    dl.id = "label-suggestions";
    dl.innerHTML = labels
      .map((l) => `<option value="${escapeHtml(l.value)}"></option>`)
      .join("");
    document.body.appendChild(dl);
  } catch (_e) {
    _labelsLoaded = false; // beim nächsten Öffnen erneut versuchen
  }
}

// --- Inline-Editor ----------------------------------------------------------

function editorFormHtml(type, parentId = null) {
  const ph = parentId
    ? "Antwort …"
    : { note: "Notiz …", todo: "Todo …", label: "Label …", handover: "Notiz (optional) …" }[type];
  const assignee = type === "handover" ? `<select name="assignee"></select>` : "";
  const req = type === "handover" ? "" : "required";
  const due = type === "todo" || type === "handover"
    ? `<input type="date" name="due" title="Fällig am (optional)">` : "";
  const head = parentId ? "↩ Antwort" : `${TYPE_ICON[type]} ${TYPE_LABEL[type]}`;
  return `<form class="ann-editor-form" data-type="${type}"${parentId ? ` data-parent="${parentId}"` : ""}>
      <span class="ann-type editor-type">${head}</span>
      ${assignee}
      <input name="text" placeholder="${ph}" ${req} autocomplete="off"${type === "label" && !parentId ? ' list="label-suggestions"' : ""}>
      ${due}
      <button type="submit" class="tiny primary" title="speichern">✓</button>
      <button type="button" class="link-btn tiny" data-editor-cancel title="abbrechen">✕</button>
    </form>`;
}

async function openEditor(node, type, parentId = null) {
  const detail = node.querySelector(":scope > .entry-detail");
  detail.hidden = false;
  const box = detail.querySelector(":scope > .ann-editor");
  box.innerHTML = editorFormHtml(type, parentId);
  if (type === "label") await ensureLabelDatalist();
  if (type === "handover") {
    const sel = box.querySelector('select[name="assignee"]');
    const members = await membersFor(Number(node.dataset.source), node.dataset.path || "");
    sel.innerHTML = members
      .map((m) => `<option value="${m.id}">${escapeHtml(m.display_name)} (@${escapeHtml(m.username)})</option>`)
      .join("");
  }
  box.querySelector('input[name="text"]').focus();
}

async function refreshEntry(node) {
  const entryId = Number(node.dataset.entry);
  const anns = await api(`/api/annotations/by-entry/${entryId}`);
  const chips = node.querySelector(":scope > .entry-row .ann-chips");
  if (chips) chips.outerHTML = annChipsHtml(anns);
  const list = node.querySelector(":scope > .entry-detail > .ann-list");
  if (list) list.outerHTML = annListHtml(anns);
}

// --- Interaktionen (Event-Delegation, greift auch für neue Knoten) ----------

function wireEntryActions(rootEl) {
  rootEl.addEventListener("click", async (e) => {
    const t = e.target;

    if (t.closest("[data-toggle-detail]")) {
      const node = t.closest("[data-entry]");
      const detail = node.querySelector(":scope > .entry-detail");
      detail.hidden = !detail.hidden;
      return;
    }
    if (t.closest("[data-open-tree]")) {
      const node = t.closest("[data-entry]");
      location.href = `/browse?source=${node.dataset.source}` +
        `&path=${encodeURIComponent(node.dataset.path || "")}`;
      return;
    }
    if (t.closest("[data-copy-path]")) {
      const node = t.closest("[data-entry]");
      try {
        const { text, absolute } = await LocalPaths.copyPath(
          Number(node.dataset.source), node.dataset.path || ""
        );
        toast(
          absolute
            ? `Pfad kopiert: ${text}`
            : `Relativer Pfad kopiert: ${text} · vollständigen Pfad gibt es, sobald unter „Quellen“ ein Basispfad hinterlegt ist.`,
          absolute ? "success" : "info"
        );
      } catch (err) {
        toast("Kopieren fehlgeschlagen: " + err.message, "error");
      }
      return;
    }
    const act = t.closest("[data-act]");
    if (act) {
      await openEditor(t.closest("[data-entry]"), act.dataset.act);
      return;
    }
    const replyTo = t.closest("[data-reply-to]");
    if (replyTo) {
      await openEditor(t.closest("[data-entry]"), "note", Number(replyTo.dataset.replyTo));
      return;
    }
    if (t.matches("[data-editor-cancel]")) {
      t.closest(".ann-editor").innerHTML = "";
      return;
    }
    const delId = t.dataset.delAnn;
    if (delId) {
      const node = t.closest("[data-entry]");
      await api(`/api/annotations/${delId}`, { method: "DELETE" });
      await refreshEntry(node);
    }
  });

  rootEl.addEventListener("change", async (e) => {
    const node = e.target.closest("[data-entry]");
    const toggleId = e.target.dataset.toggle;
    if (toggleId) {
      await api(`/api/annotations/${toggleId}`, {
        method: "PATCH",
        body: { done: e.target.checked },
      });
      await refreshEntry(node);
      return;
    }
    const dueId = e.target.dataset.due;
    if (dueId) {
      // Leeres Feld = Termin entfernen (der Server liest null als „löschen“).
      await api(`/api/annotations/${dueId}`, {
        method: "PATCH",
        body: { due_date: e.target.value || null },
      });
      await refreshEntry(node);
    }
  });

  rootEl.addEventListener("submit", async (e) => {
    if (!e.target.classList.contains("ann-editor-form")) return;
    e.preventDefault();
    const form = e.target;
    const node = form.closest("[data-entry]");
    const type = form.dataset.type;
    const text = (form.querySelector('input[name="text"]').value || "").trim();
    const dueInput = form.querySelector('input[name="due"]');
    const payload = {
      entry_id: Number(node.dataset.entry),
      type,
      body: type === "label" ? "" : text,
      label_value: type === "label" ? text : "",
      due_date: dueInput && dueInput.value ? dueInput.value : null,
    };
    if (form.dataset.parent) {
      payload.parent_annotation_id = Number(form.dataset.parent);
    }
    if (type === "handover") {
      const sel = form.querySelector('select[name="assignee"]');
      if (!sel.value) return alert("Bitte einen Empfänger wählen.");
      payload.assignee_user_id = Number(sel.value);
    } else if (!text) {
      return; // leeres Feld ignorieren
    }
    try {
      await api("/api/annotations", { method: "POST", body: payload });
      form.closest(".ann-editor").innerHTML = "";
      await refreshEntry(node);
    } catch (err) {
      alert("Konnte nicht speichern: " + err.message);
    }
  });
}

window.EntryUI = {
  iconFor, annChipsHtml, actionsHtml, annListHtml, detailHtml,
  wireEntryActions, invalidateMembers, TYPE_LABEL,
  shareRowsHtml, PERM_LABEL,
};
