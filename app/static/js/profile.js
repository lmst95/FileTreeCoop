// Profilseite: Stammdaten bearbeiten, Passwort ändern.

const profileError = document.getElementById("profile-error");
const passwordError = document.getElementById("password-error");

function showFormError(el, msg) {
  el.textContent = msg;
  el.hidden = false;
}

document.getElementById("profile-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  profileError.hidden = true;
  const f = new FormData(e.target);
  try {
    const updated = await api("/api/auth/me", {
      method: "PATCH",
      body: {
        display_name: f.get("display_name"),
        username: f.get("username"),
        email: f.get("email"),
      },
    });
    document.querySelector(".who").textContent = updated.display_name;
    toast("Profil gespeichert.", "success");
  } catch (err) {
    showFormError(profileError, err.message);
  }
});

document.getElementById("password-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  passwordError.hidden = true;
  const f = new FormData(e.target);
  try {
    await api("/api/auth/me/password", {
      method: "POST",
      body: {
        current_password: f.get("current_password"),
        new_password: f.get("new_password"),
      },
    });
    e.target.reset();
    toast("Passwort geändert.", "success");
  } catch (err) {
    showFormError(passwordError, err.message);
  }
});

// --- Geteilte Inhalte --------------------------------------------------------

const mySharesBox = document.getElementById("my-shares-list");

function myShareRowHtml(sh) {
  const scope = sh.path_prefix
    ? `<span class="badge scope-badge" title="Teilbaum">📁 ${escapeHtml(sh.path_prefix)}</span>`
    : `<span class="badge">ganze Quelle</span>`;
  const who = sh.pending
    ? `${escapeHtml(sh.email)} <span class="badge pending-badge">eingeladen</span>`
    : `${escapeHtml(sh.display_name)} <span class="muted small">@${escapeHtml(sh.username)}</span>`;
  const identifier = sh.pending ? sh.email : sh.username;
  const removeBtn = sh.pending
    ? `<button class="link-btn danger tiny" data-uninvite data-src="${sh.source_id}"
              data-invite="${sh.invite_id}">zurückziehen</button>`
    : `<button class="link-btn danger tiny" data-unshare data-src="${sh.source_id}"
              data-user="${sh.user_id}" data-prefix="${escapeHtml(sh.path_prefix)}">löschen</button>`;
  const permSelect = sh.pending
    ? `<span class="badge">${escapeHtml(EntryUI.PERM_LABEL[sh.permission] || sh.permission)}</span>`
    : "";
  const updateSelect = sh.pending ? "" : `<select class="tiny" data-update-share
        data-src="${sh.source_id}" data-identifier="${escapeHtml(identifier)}"
        data-prefix="${escapeHtml(sh.path_prefix)}">
      <option value="annotate" ${sh.permission === "annotate" ? "selected" : ""}>annotieren</option>
      <option value="read" ${sh.permission === "read" ? "selected" : ""}>nur lesen</option>
    </select>`;
  return `<li class="share-row">
      <span><span class="badge source">${escapeHtml(sh.source_label)}</span> ${who}</span>
      ${scope}
      ${permSelect}
      ${updateSelect}
      ${removeBtn}
    </li>`;
}

async function loadMyShares() {
  const shares = await api("/api/auth/me/shares");
  mySharesBox.innerHTML = shares.length
    ? `<ul class="share-list">${shares.map(myShareRowHtml).join("")}</ul>`
    : `<p class="muted small">Du hast noch nichts geteilt.</p>`;
}

mySharesBox.addEventListener("click", async (e) => {
  const un = e.target.closest("[data-unshare]");
  if (un) {
    const { src, user, prefix } = un.dataset;
    await api(`/api/sources/${src}/shares/${user}?path_prefix=${encodeURIComponent(prefix)}`,
      { method: "DELETE" });
    toast("Freigabe gelöscht.", "success");
    await loadMyShares();
    return;
  }
  const uninv = e.target.closest("[data-uninvite]");
  if (uninv) {
    await api(`/api/sources/${uninv.dataset.src}/invites/${uninv.dataset.invite}`,
      { method: "DELETE" });
    toast("Einladung zurückgezogen.", "success");
    await loadMyShares();
  }
});

mySharesBox.addEventListener("change", async (e) => {
  const sel = e.target.closest("[data-update-share]");
  if (!sel) return;
  const { src, identifier, prefix } = sel.dataset;
  try {
    await api(`/api/sources/${src}/shares`, {
      method: "POST",
      body: { identifier, permission: sel.value, path_prefix: prefix },
    });
    toast("Berechtigung aktualisiert.", "success");
  } catch (err) {
    toast(err.message, "error");
    await loadMyShares();
  }
});

loadMyShares().catch((err) => {
  mySharesBox.innerHTML = `<p class="error small">${escapeHtml(err.message)}</p>`;
});
