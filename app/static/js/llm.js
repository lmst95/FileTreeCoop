// KI-Einstellungen: Verbindungen, LLM-Settings und Prompts verwalten.

let META = { provider_types: [], features: [] };
let connections = [];
let settings = [];
let prompts = [];

// --- Tabs --------------------------------------------------------------------

document.querySelectorAll(".llm-page .tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    const key = btn.dataset.tab;
    document.querySelectorAll(".llm-page .tab").forEach((b) =>
      b.classList.toggle("active", b === btn));
    document.querySelectorAll(".llm-page .tab-panel").forEach((p) =>
      p.classList.toggle("active", p.dataset.panel === key));
  });
});

// --- Modal (eigenständig, unabhängig von notes.js) ---------------------------

let overlay = null;

function closeModal() {
  if (overlay) { overlay.remove(); overlay = null; }
}

function openModal(innerHtml) {
  closeModal();
  overlay = document.createElement("div");
  overlay.className = "note-modal-overlay";
  overlay.innerHTML = `<div class="note-modal-box llm-modal">${innerHtml}</div>`;
  overlay.addEventListener("click", (e) => { if (e.target === overlay) closeModal(); });
  document.body.appendChild(overlay);
  return overlay.querySelector(".note-modal-box");
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && overlay) closeModal();
});

function featureChecksHtml(selected) {
  const sel = new Set(selected || []);
  if (!META.features.length) return `<p class="muted small">Keine Features definiert.</p>`;
  return `<div class="llm-feature-checks">${META.features.map((f) =>
    `<label class="check"><input type="checkbox" data-feature value="${f.key}"
       ${sel.has(f.key) ? "checked" : ""}> ${escapeHtml(f.label)}</label>`).join("")}</div>`;
}

function collectFeatures(box) {
  return [...box.querySelectorAll("[data-feature]:checked")].map((c) => c.value);
}

// --- Verbindungen ------------------------------------------------------------

const connBox = document.getElementById("connections-list");

function providerLabel(value) {
  const pt = META.provider_types.find((p) => p.value === value);
  return pt ? pt.label : value;
}

function connCardHtml(c) {
  const key = c.has_key
    ? `<span class="muted small">Token ${escapeHtml(c.key_hint)}</span>`
    : `<span class="muted small">kein Token</span>`;
  const models = c.models && c.models.length
    ? `<span class="muted small">· ${c.models.length} Modelle im Cache</span>` : "";
  return `<div class="card llm-card" data-conn="${c.id}">
      <div class="row-between">
        <strong>${escapeHtml(c.label)}</strong>
        <span class="badge">${escapeHtml(providerLabel(c.provider_type))}</span>
      </div>
      <div class="muted small mono">${escapeHtml(c.base_url || "(keine URL)")}</div>
      <div class="llm-card-meta">${key} ${models}
        ${c.default_model ? `<span class="muted small">· Default: ${escapeHtml(c.default_model)}</span>` : ""}</div>
      <div class="llm-card-actions">
        <button class="tiny" data-edit-conn="${c.id}">bearbeiten</button>
        <button class="tiny" data-test-conn="${c.id}">testen</button>
        <button class="link-btn danger tiny" data-del-conn="${c.id}">löschen</button>
      </div>
    </div>`;
}

function renderConnections() {
  connBox.innerHTML = connections.length
    ? connections.map(connCardHtml).join("")
    : `<p class="muted">Noch keine Verbindung. Lege mit „+ Neue Verbindung“ eine an.</p>`;
}

async function loadConnections() {
  connections = await api("/api/llm/connections");
  renderConnections();
}

function connectionFormHtml(c) {
  const isEdit = !!c;
  const options = META.provider_types.map((p) =>
    `<option value="${p.value}" ${c && c.provider_type === p.value ? "selected" : ""}>${escapeHtml(p.label)}</option>`
  ).join("");
  return `
    <div class="note-modal-head">
      <strong>${isEdit ? "Verbindung bearbeiten" : "Neue Verbindung"}</strong>
      <span class="spacer"></span>
      <button type="button" class="link-btn tiny" data-close-modal>✕</button>
    </div>
    <form class="llm-form" data-conn-form>
      <label>Name<input name="label" required maxlength="120" value="${c ? escapeHtml(c.label) : ""}"></label>
      <label>Anbieter-Typ<select name="provider_type">${options}</select></label>
      <label>Basis-URL <span class="muted small">(inkl. Versionspfad, z. B. https://api.openai.com/v1)</span>
        <input name="base_url" maxlength="500" placeholder="https://…" value="${c ? escapeHtml(c.base_url) : ""}"></label>
      <label>API-Token ${isEdit && c.has_key ? `<span class="muted small">(hinterlegt: ${escapeHtml(c.key_hint)} – leer lassen = unverändert)</span>` : ""}
        <input name="api_key" type="password" autocomplete="off" placeholder="${isEdit ? "unverändert lassen" : "sk-…"}"></label>
      <label>Standard-Modell <span class="muted small">(optional)</span>
        <input name="default_model" maxlength="200" value="${c ? escapeHtml(c.default_model) : ""}"></label>
      <div class="note-modal-actions">
        <button type="submit" class="primary tiny">${isEdit ? "speichern" : "anlegen"}</button>
        ${isEdit ? `<button type="button" class="tiny" data-refresh-models>Modelle abrufen</button>` : ""}
        <span class="llm-form-msg muted small"></span>
      </div>
    </form>`;
}

function openConnectionModal(c) {
  const box = openModal(connectionFormHtml(c));
  const form = box.querySelector("[data-conn-form]");
  const msg = box.querySelector(".llm-form-msg");

  box.querySelector("[data-close-modal]").addEventListener("click", closeModal);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const body = {
      label: f.get("label"),
      provider_type: f.get("provider_type"),
      base_url: f.get("base_url"),
      default_model: f.get("default_model"),
    };
    // Token nur mitschicken, wenn etwas eingegeben wurde (leer = unverändert).
    const key = f.get("api_key");
    if (key) body.api_key = key;
    else if (!c) body.api_key = null;
    try {
      if (c) await api(`/api/llm/connections/${c.id}`, { method: "PATCH", body });
      else await api("/api/llm/connections", { method: "POST", body });
      closeModal();
      await loadConnections();
      toast("Verbindung gespeichert.", "success");
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("error");
    }
  });

  const refreshBtn = box.querySelector("[data-refresh-models]");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      msg.classList.remove("error");
      msg.textContent = "rufe Modelle ab …";
      try {
        const res = await api(`/api/llm/connections/${c.id}/models`, { method: "POST" });
        msg.textContent = res.supported
          ? `${res.models.length} Modelle abgerufen.`
          : "Dieser Anbieter liefert keine Modell-Liste.";
        await loadConnections();
      } catch (err) {
        msg.textContent = err.message;
        msg.classList.add("error");
      }
    });
  }
}

connBox.addEventListener("click", async (e) => {
  const ed = e.target.closest("[data-edit-conn]");
  if (ed) return openConnectionModal(connections.find((c) => c.id === Number(ed.dataset.editConn)));
  const del = e.target.closest("[data-del-conn]");
  if (del) {
    if (!confirm("Verbindung wirklich löschen? Zugehörige Settings verlieren ihren Bezug.")) return;
    await api(`/api/llm/connections/${del.dataset.delConn}`, { method: "DELETE" });
    await Promise.all([loadConnections(), loadSettings()]);
    toast("Verbindung gelöscht.", "success");
    return;
  }
  const test = e.target.closest("[data-test-conn]");
  if (test) {
    test.disabled = true;
    const original = test.textContent;
    test.textContent = "teste …";
    try {
      const res = await api(`/api/llm/connections/${test.dataset.testConn}/test`, { method: "POST" });
      toast(res.detail, res.ok ? "success" : "error");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      test.disabled = false;
      test.textContent = original;
    }
  }
});

document.querySelector("[data-new-connection]").addEventListener("click", () => openConnectionModal(null));

// --- LLM-Settings ------------------------------------------------------------

const settingsBox = document.getElementById("settings-list");

function settingCardHtml(s) {
  const feats = s.features.length
    ? s.features.map((k) => `<span class="chip chip-label">${escapeHtml(featureLabel(k))}</span>`).join("")
    : `<span class="muted small">keinem Feature zugeordnet</span>`;
  return `<div class="card llm-card" data-setting="${s.id}">
      <div class="row-between">
        <strong>${escapeHtml(s.label)}</strong>
        <span class="muted small">${escapeHtml(s.connection_label)} · ${escapeHtml(s.model || "kein Modell")}</span>
      </div>
      <div class="ann-chips">${feats}</div>
      <div class="llm-card-actions">
        <button class="tiny" data-edit-setting="${s.id}">bearbeiten</button>
        <button class="link-btn danger tiny" data-del-setting="${s.id}">löschen</button>
      </div>
    </div>`;
}

function featureLabel(key) {
  const f = META.features.find((x) => x.key === key);
  return f ? f.label : key;
}

function renderSettings() {
  settingsBox.innerHTML = settings.length
    ? settings.map(settingCardHtml).join("")
    : `<p class="muted">Noch kein Setting.</p>`;
}

async function loadSettings() {
  settings = await api("/api/llm/settings");
  renderSettings();
}

function modelOptionsHtml(connId, current) {
  const conn = connections.find((c) => c.id === Number(connId));
  const models = conn && conn.models ? conn.models.slice() : [];
  // Aktuell gewähltes Modell mit aufnehmen, falls es (noch) nicht in der Liste ist.
  if (current && !models.includes(current)) models.unshift(current);
  const placeholder = `<option value="">${
    models.length ? "– aus Liste wählen –" : "– noch keine Modelle abgerufen –"
  }</option>`;
  const opts = models.map((m) =>
    `<option value="${escapeHtml(m)}"${m === current ? " selected" : ""}>${escapeHtml(m)}</option>`
  ).join("");
  return placeholder + opts;
}

function settingFormHtml(s) {
  const isEdit = !!s;
  if (!connections.length) {
    return `<div class="note-modal-head"><strong>Kein Setting möglich</strong>
      <span class="spacer"></span><button type="button" class="link-btn tiny" data-close-modal>✕</button></div>
      <p class="muted">Lege zuerst eine Verbindung an.</p>`;
  }
  const connOpts = connections.map((c) =>
    `<option value="${c.id}" ${s && s.connection_id === c.id ? "selected" : ""}>${escapeHtml(c.label)}</option>`
  ).join("");
  const params = (s && s.params) || {};
  const initialConn = s ? s.connection_id : connections[0].id;
  return `
    <div class="note-modal-head">
      <strong>${isEdit ? "Setting bearbeiten" : "Neues Setting"}</strong>
      <span class="spacer"></span>
      <button type="button" class="link-btn tiny" data-close-modal>✕</button>
    </div>
    <form class="llm-form" data-setting-form>
      <label>Name<input name="label" required maxlength="120" value="${s ? escapeHtml(s.label) : ""}"></label>
      <label>Verbindung<select name="connection_id" data-conn-select>${connOpts}</select></label>
      <label>Modell
        <div class="llm-model-picker">
          <select data-model-picker>${modelOptionsHtml(initialConn, s ? s.model : "")}</select>
          <button type="button" class="tiny" data-fetch-models>Modelle abrufen</button>
        </div>
        <input name="model" maxlength="200" autocomplete="off"
               placeholder="Modell-ID (z. B. gpt-4o-mini)" value="${s ? escapeHtml(s.model) : ""}">
        <span class="muted small" data-model-hint>Aus der Liste wählen oder Modell-ID manuell eintragen.</span>
      </label>
      <div class="llm-form-row">
        <label>Temperature<input name="temperature" type="number" step="0.1" min="0" max="2"
               value="${params.temperature ?? ""}"></label>
        <label>Max. Tokens<input name="max_tokens" type="number" min="1" step="1"
               value="${params.max_tokens ?? ""}"></label>
      </div>
      <label>System-Prompt <span class="muted small">(optional, jedem Lauf vorangestellt)</span>
        <textarea name="system_prompt" rows="2">${s ? escapeHtml(s.system_prompt) : ""}</textarea></label>
      <label class="check llm-web-search">
        <input type="checkbox" name="web_search" ${params.web_search ? "checked" : ""}>
        Web-Suche aktivieren <span class="muted small">(OpenAI)</span></label>
      <p class="muted small">Lässt das Modell live im Web recherchieren und hängt die Quellen ans
        Ergebnis an. Nur bei OpenAI und nur mit einem suchfähigen Modell
        (z. B. <code>gpt-4o-search-preview</code>, <code>gpt-4o-mini-search-preview</code>
        oder <code>gpt-5-search-api</code>).</p>
      <fieldset class="llm-fieldset"><legend>Verfügbar in Features</legend>${featureChecksHtml(s && s.features)}</fieldset>
      <div class="note-modal-actions">
        <button type="submit" class="primary tiny">${isEdit ? "speichern" : "anlegen"}</button>
        <span class="llm-form-msg muted small"></span>
      </div>
    </form>`;
}

function openSettingModal(s) {
  const box = openModal(settingFormHtml(s));
  box.querySelector("[data-close-modal]").addEventListener("click", closeModal);
  const form = box.querySelector("[data-setting-form]");
  if (!form) return;

  const connSelect = form.querySelector("[data-conn-select]");
  const modelInput = form.querySelector("[name=model]");
  const picker = form.querySelector("[data-model-picker]");
  const fetchBtn = form.querySelector("[data-fetch-models]");
  const hint = form.querySelector("[data-model-hint]");

  function repopulatePicker() {
    picker.innerHTML = modelOptionsHtml(connSelect.value, modelInput.value);
  }

  // Verbindung gewechselt -> Modell-Liste dieser Verbindung anzeigen.
  connSelect.addEventListener("change", repopulatePicker);

  // Auswahl aus dem Dropdown ins (maßgebliche) Textfeld übernehmen.
  picker.addEventListener("change", () => {
    if (picker.value) modelInput.value = picker.value;
  });

  // Modelle live vom Anbieter holen und Dropdown befüllen.
  fetchBtn.addEventListener("click", async () => {
    const connId = connSelect.value;
    hint.classList.remove("error");
    hint.textContent = "rufe Modelle ab …";
    fetchBtn.disabled = true;
    try {
      const res = await api(`/api/llm/connections/${connId}/models`, { method: "POST" });
      const conn = connections.find((c) => c.id === Number(connId));
      if (conn) conn.models = res.models; // lokalen Cache aktualisieren
      repopulatePicker();
      hint.textContent = res.supported
        ? `${res.models.length} Modelle geladen – im Dropdown wählbar.`
        : "Dieser Anbieter liefert keine Modell-Liste – Modell-ID bitte manuell eintragen.";
    } catch (err) {
      hint.textContent = err.message;
      hint.classList.add("error");
    } finally {
      fetchBtn.disabled = false;
    }
  });

  const msg = box.querySelector(".llm-form-msg");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const params = {};
    if (f.get("temperature") !== "") params.temperature = Number(f.get("temperature"));
    if (f.get("max_tokens") !== "") params.max_tokens = Number(f.get("max_tokens"));
    if (f.get("web_search")) params.web_search = true;
    const body = {
      label: f.get("label"),
      connection_id: Number(f.get("connection_id")),
      model: f.get("model"),
      system_prompt: f.get("system_prompt"),
      params,
      features: collectFeatures(form),
    };
    try {
      if (s) await api(`/api/llm/settings/${s.id}`, { method: "PATCH", body });
      else await api("/api/llm/settings", { method: "POST", body });
      closeModal();
      await loadSettings();
      toast("Setting gespeichert.", "success");
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("error");
    }
  });
}

settingsBox.addEventListener("click", async (e) => {
  const ed = e.target.closest("[data-edit-setting]");
  if (ed) return openSettingModal(settings.find((s) => s.id === Number(ed.dataset.editSetting)));
  const del = e.target.closest("[data-del-setting]");
  if (del) {
    if (!confirm("Setting wirklich löschen?")) return;
    await api(`/api/llm/settings/${del.dataset.delSetting}`, { method: "DELETE" });
    await loadSettings();
    toast("Setting gelöscht.", "success");
  }
});

document.querySelector("[data-new-setting]").addEventListener("click", () => openSettingModal(null));

const wsSettingBtn = document.querySelector("[data-web-search-setting]");
wsSettingBtn.addEventListener("click", async () => {
  wsSettingBtn.disabled = true;
  try {
    const before = settings.length;
    const s = await api("/api/llm/settings/web-search", { method: "POST" });
    await loadSettings();
    toast(settings.length > before
      ? `Setting „${s.label}“ mit Web-Suche angelegt (Modell ${s.model}).`
      : `Setting „${s.label}“ ist bereits vorhanden.`, "success");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    wsSettingBtn.disabled = false;
  }
});

// --- Prompts -----------------------------------------------------------------

const promptsBox = document.getElementById("prompts-list");

function promptCardHtml(p) {
  const feats = p.features.length
    ? p.features.map((k) => `<span class="chip chip-label">${escapeHtml(featureLabel(k))}</span>`).join("")
    : `<span class="muted small">keinem Feature zugeordnet</span>`;
  return `<div class="card llm-card" data-prompt="${p.id}">
      <div class="row-between">
        <strong>${escapeHtml(p.name)}</strong>
      </div>
      ${p.description ? `<div class="muted small">${escapeHtml(p.description)}</div>` : ""}
      <div class="llm-prompt-body mono small">${escapeHtml(p.body || "(leer)")}</div>
      <div class="ann-chips">${feats}</div>
      <div class="llm-card-actions">
        <button class="tiny" data-edit-prompt="${p.id}">bearbeiten</button>
        <button class="link-btn danger tiny" data-del-prompt="${p.id}">löschen</button>
      </div>
    </div>`;
}

function renderPrompts() {
  promptsBox.innerHTML = prompts.length
    ? prompts.map(promptCardHtml).join("")
    : `<p class="muted">Noch kein Prompt.</p>`;
}

async function loadPrompts() {
  prompts = await api("/api/llm/prompts");
  renderPrompts();
}

function promptFormHtml(p) {
  const isEdit = !!p;
  return `
    <div class="note-modal-head">
      <strong>${isEdit ? "Prompt bearbeiten" : "Neuer Prompt"}</strong>
      <span class="spacer"></span>
      <button type="button" class="link-btn tiny" data-close-modal>✕</button>
    </div>
    <form class="llm-form" data-prompt-form>
      <label>Name<input name="name" required maxlength="120" value="${p ? escapeHtml(p.name) : ""}"></label>
      <label>Beschreibung <span class="muted small">(optional)</span>
        <input name="description" maxlength="300" value="${p ? escapeHtml(p.description) : ""}"></label>
      <label>Prompt-Text <span class="muted small">Platzhalter {{input}} = eingesetzter Text</span>
        <textarea name="body" rows="6" placeholder="Überarbeite den folgenden Text sprachlich:\n\n{{input}}">${p ? escapeHtml(p.body) : ""}</textarea></label>
      <fieldset class="llm-fieldset"><legend>Verfügbar in Features</legend>${featureChecksHtml(p && p.features)}</fieldset>
      <div class="note-modal-actions">
        <button type="submit" class="primary tiny">${isEdit ? "speichern" : "anlegen"}</button>
        <span class="llm-form-msg muted small"></span>
      </div>
    </form>`;
}

function openPromptModal(p) {
  const box = openModal(promptFormHtml(p));
  box.querySelector("[data-close-modal]").addEventListener("click", closeModal);
  const form = box.querySelector("[data-prompt-form]");
  const msg = box.querySelector(".llm-form-msg");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(form);
    const body = {
      name: f.get("name"),
      description: f.get("description"),
      body: f.get("body"),
      features: collectFeatures(form),
    };
    try {
      if (p) await api(`/api/llm/prompts/${p.id}`, { method: "PATCH", body });
      else await api("/api/llm/prompts", { method: "POST", body });
      closeModal();
      await loadPrompts();
      toast("Prompt gespeichert.", "success");
    } catch (err) {
      msg.textContent = err.message;
      msg.classList.add("error");
    }
  });
}

promptsBox.addEventListener("click", async (e) => {
  const ed = e.target.closest("[data-edit-prompt]");
  if (ed) return openPromptModal(prompts.find((p) => p.id === Number(ed.dataset.editPrompt)));
  const del = e.target.closest("[data-del-prompt]");
  if (del) {
    if (!confirm("Prompt wirklich löschen?")) return;
    await api(`/api/llm/prompts/${del.dataset.delPrompt}`, { method: "DELETE" });
    await loadPrompts();
    toast("Prompt gelöscht.", "success");
  }
});

document.querySelector("[data-new-prompt]").addEventListener("click", () => openPromptModal(null));

const defaultsBtn = document.querySelector("[data-default-prompts]");
defaultsBtn.addEventListener("click", async () => {
  defaultsBtn.disabled = true;
  try {
    const before = prompts.length;
    const result = await api("/api/llm/prompts/defaults", { method: "POST" });
    await loadPrompts();
    const added = prompts.length - before;
    toast(added > 0
      ? `${added} Standard-Prompt(s) angelegt.`
      : `Standard-Prompts sind bereits vorhanden (${result.length}).`, "success");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    defaultsBtn.disabled = false;
  }
});

// --- Init --------------------------------------------------------------------

async function init() {
  META = await api("/api/llm/meta");
  await Promise.all([loadConnections(), loadSettings(), loadPrompts()]);
}

init().catch((err) => {
  connBox.innerHTML = `<p class="error">${escapeHtml(err.message)}</p>`;
});
