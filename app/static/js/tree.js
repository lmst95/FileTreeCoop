// Baumansicht einer Quelle mit Lazy-Loading pro Ordner.

const treeEl = document.getElementById("tree");
const sourceSelect = document.getElementById("source-select");

function nodeHtml(h) {
  const isDir = h.is_dir;
  const statusBadge = h.status === "missing"
    ? `<span class="badge missing">verschwunden</span>` : "";
  const toggle = isDir
    ? `<button class="tree-toggle" data-toggle-dir aria-label="aufklappen">▶</button>`
    : `<span class="tree-toggle leaf">·</span>`;
  // Nur Ordner haben einen Zusatz-Button („teilen“, 🔗); „Pfad kopieren“
  // steckt für alle Zeilen bereits in EntryUI.actionsHtml().
  const extraBtn = isDir
    ? `<button class="act act-share" data-share-dir title="Diesen Ordner teilen">🔗</button>`
    : "";
  return `<div class="entry ${isDir ? "is-dir" : "is-file"}"
       data-entry="${h.entry_id}" data-source="${h.source_id}"
       data-path="${escapeHtml(h.path)}" data-dir="${isDir ? 1 : 0}" data-loaded="0">
    <div class="entry-row">
      ${toggle}
      <span class="entry-name">${EntryUI.iconFor(h)} ${escapeHtml(h.name)}</span>
      ${statusBadge}
      ${EntryUI.annChipsHtml(h.annotations, h.has_new)}
      <span class="row-spacer"></span>
      ${EntryUI.actionsHtml(extraBtn, { inTree: true })}
    </div>
    ${EntryUI.detailHtml(h.annotations)}
    ${isDir ? `<div class="entry-children" hidden></div>` : ""}
  </div>`;
}

function currentSourceId() {
  return Number(sourceSelect.value);
}

async function loadChildren(sourceId, parentPath) {
  const params = new URLSearchParams();
  if (parentPath) params.set("parent", parentPath);
  return api(`/api/sources/${sourceId}/children?${params.toString()}`);
}

async function renderRoot() {
  const sid = currentSourceId();
  if (!sid) {
    treeEl.innerHTML = `<p class="muted">Keine Quelle ausgewählt.</p>`;
    return;
  }
  treeEl.innerHTML = `<p class="muted">Lädt …</p>`;
  const children = await loadChildren(sid, "");
  if (!children.length) {
    treeEl.innerHTML = `<p class="muted">Diese Quelle enthält keine Einträge – schon gescannt?</p>`;
    return;
  }
  // Teilbaum-Freigabe? Dann als Kontextzeile zeigen, *wo* man steht.
  const subtreeRoots = children.filter((c) => c.path.includes("/"));
  const context = subtreeRoots.length
    ? `<p class="tree-context muted small">🔗 Freigegebene${subtreeRoots.length > 1 ? " Teilbäume" : "r Teilbaum"}:
         ${subtreeRoots.map((r) => `<code>${escapeHtml(r.path)}</code>`).join(" · ")}</p>`
    : "";
  treeEl.innerHTML = context + children.map(nodeHtml).join("");
  // Besuch merken: die Ungelesen-Punkte dieser Quelle gelten ab jetzt als gesehen
  // (sie bleiben in der aktuellen Ansicht sichtbar, verschwinden beim nächsten Laden).
  api(`/api/sources/${sid}/seen`, { method: "POST" }).catch(() => {});
}

// --- Deep-Link: ?path=… bis zum Ziel aufklappen ------------------------------

function deepestVisibleNodeFor(target) {
  let best = null;
  treeEl.querySelectorAll(".entry").forEach((n) => {
    const p = n.dataset.path;
    if (p === target || target.startsWith(p + "/")) {
      if (!best || p.length > best.dataset.path.length) best = n;
    }
  });
  return best;
}

async function expandToPath(target) {
  // Klettert entlang des Pfads hinab; funktioniert auch, wenn die sichtbare
  // Wurzel ein freigegebener Teilbaum mitten im Pfad ist.
  for (let guard = 0; guard < 60; guard++) {
    const node = deepestVisibleNodeFor(target);
    if (!node) return;
    if (node.dataset.path === target) {
      // Ist das Ziel ein Ordner (z. B. Notiz an einem Ordner), gleich aufklappen.
      if (node.dataset.dir === "1") await expandDir(node);
      node.scrollIntoView({ block: "center" });
      node.querySelector(":scope > .entry-row").classList.add("flash");
      const detail = node.querySelector(":scope > .entry-detail");
      if (detail) detail.hidden = false;
      return;
    }
    if (node.dataset.dir !== "1") return;
    await expandDir(node);
  }
}

async function expandDir(node) {
  const childrenBox = node.querySelector(":scope > .entry-children");
  if (node.dataset.loaded === "0") {
    childrenBox.innerHTML = `<p class="muted small">Lädt …</p>`;
    const kids = await loadChildren(currentSourceId(), node.dataset.path);
    childrenBox.innerHTML = kids.length
      ? kids.map(nodeHtml).join("")
      : `<p class="muted small">– leer –</p>`;
    node.dataset.loaded = "1";
  }
  childrenBox.hidden = false;
  node.querySelector(":scope > .entry-row > .tree-toggle").textContent = "▼";
}

function collapseDir(node) {
  node.querySelector(":scope > .entry-children").hidden = true;
  node.querySelector(":scope > .entry-row > .tree-toggle").textContent = "▶";
}

// Ordner auf-/zuklappen.
treeEl.addEventListener("click", async (e) => {
  // Ordner auf-/zuklappen.
  if (e.target.matches("[data-toggle-dir]")) {
    const node = e.target.closest(".entry");
    const box = node.querySelector(":scope > .entry-children");
    if (box.hidden) {
      try {
        await expandDir(node);
      } catch (err) {
        box.innerHTML = `<p class="error small">${escapeHtml(err.message)}</p>`;
        box.hidden = false;
      }
    } else {
      collapseDir(node);
    }
    return;
  }
  // Ordner teilen: Übersicht bestehender Freigaben + Formular einblenden.
  if (e.target.closest("[data-share-dir]")) {
    const node = e.target.closest(".entry");
    const detail = node.querySelector(":scope > .entry-detail");
    detail.hidden = false;
    await loadShareEditor(node);
    return;
  }
  // Freigabe dieses Ordners entfernen.
  const un = e.target.closest("[data-unshare]");
  if (un) {
    const node = un.closest(".entry");
    const { src, user, prefix } = un.dataset;
    await api(`/api/sources/${src}/shares/${user}?path_prefix=${encodeURIComponent(prefix)}`,
      { method: "DELETE" });
    EntryUI.invalidateMembers(Number(src));
    await loadShareEditor(node);
    return;
  }
  // Ausstehende Einladung für diesen Ordner zurückziehen.
  const uninv = e.target.closest("[data-uninvite]");
  if (uninv) {
    const node = uninv.closest(".entry");
    await api(`/api/sources/${uninv.dataset.src}/invites/${uninv.dataset.invite}`,
      { method: "DELETE" });
    await loadShareEditor(node);
  }
});

function shareEditorHtml(sourceId, path, shares) {
  const scoped = shares.filter((sh) => sh.path_prefix === path);
  return `
    <span class="ann-type editor-type">🔗 „${escapeHtml(path)}“ – Freigaben</span>
    <ul class="share-list small">${EntryUI.shareRowsHtml(sourceId, scoped)}</ul>
    <form class="share-editor-form">
      <input name="identifier" type="text" placeholder="E-Mail oder Username" required autocomplete="off">
      <select name="permission">
        <option value="annotate">annotieren</option>
        <option value="read">nur lesen</option>
      </select>
      <button type="submit" class="tiny primary" title="freigeben">✓ freigeben</button>
      <button type="button" class="link-btn tiny" data-editor-cancel title="schließen">✕</button>
    </form>
    <p class="share-editor-msg error small" hidden></p>`;
}

// Lädt die Freigaben der Quelle und rendert Liste + Formular für diesen Ordner neu.
async function loadShareEditor(node) {
  const box = node.querySelector(":scope > .entry-detail > .ann-editor");
  const sid = currentSourceId();
  box.innerHTML = `<p class="muted small">Lädt …</p>`;
  const shares = await api(`/api/sources/${sid}/shares`);
  box.innerHTML = shareEditorHtml(sid, node.dataset.path, shares);
}

// Absenden des Ordner-Teilen-Formulars.
treeEl.addEventListener("submit", async (e) => {
  if (!e.target.classList.contains("share-editor-form")) return;
  e.preventDefault();
  const form = e.target;
  const node = form.closest(".entry");
  const sid = currentSourceId();
  const f = new FormData(form);
  const msg = form.closest(".ann-editor").querySelector(".share-editor-msg");
  msg.hidden = true;
  try {
    await api(`/api/sources/${sid}/shares`, {
      method: "POST",
      body: { identifier: f.get("identifier"), permission: f.get("permission"), path_prefix: node.dataset.path },
    });
    EntryUI.invalidateMembers(sid);
    await loadShareEditor(node);
  } catch (err) {
    msg.textContent = err.message;
    msg.hidden = false;
  }
});

// Annotationen (Delegation greift auch für später eingefügte Knoten).
EntryUI.wireEntryActions(treeEl);

// Quellen-Auswahl befüllen und initiale Quelle wählen (?source=… oder erste).
async function init() {
  const sources = await api("/api/sources");
  if (!sources.length) {
    treeEl.innerHTML = `<p class="muted">Noch keine Quellen. Lege unter „Quellen“ eine an und scanne einen Ordner.</p>`;
    return;
  }
  sourceSelect.innerHTML = sources
    .map((s) => `<option value="${s.id}">${escapeHtml(s.label)}</option>`)
    .join("");
  const params = new URLSearchParams(location.search);
  const wanted = params.get("source");
  if (wanted && sources.some((s) => String(s.id) === wanted)) {
    sourceSelect.value = wanted;
  }
  sourceSelect.addEventListener("change", () => renderRoot().catch(showErr));
  await renderRoot();
  // Deep-Link (z. B. aus ⌘K-Palette, Übergaben oder Aktivität).
  const wantedPath = params.get("path");
  if (wantedPath) await expandToPath(wantedPath).catch(() => {});
}

function showErr(err) {
  treeEl.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
}

init().catch(showErr);
