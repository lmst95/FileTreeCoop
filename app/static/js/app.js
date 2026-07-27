// Geteilte Helfer für alle Seiten.

// Kleiner fetch-Wrapper: sendet/empfängt JSON, wirft bei Fehlern mit Detailtext.
async function api(path, { method = "GET", body, headers = {} } = {}) {
  const opts = { method, headers: { ...headers }, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (res.status === 204) return null;
  const text = await res.text();
  // Fehlerantworten sind nicht immer JSON (z. B. der Klartext-500 von Uvicorn),
  // deshalb tolerant parsen und im Zweifel den Rohtext als Meldung nutzen.
  let data = null;
  let parseFailed = false;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      parseFailed = true;
    }
  }
  if (!res.ok) {
    let detail = data && data.detail ? data.detail : null;
    if (!detail) detail = parseFailed ? text.slice(0, 300) : res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (parseFailed) throw new Error("Ungültige Antwort vom Server");
  return data;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Kurze Einblendung am unteren Rand (z. B. „Pfad kopiert“).
let _toastTimer = null;
function toast(msg, kind = "info", ms = 3200) {
  let el = document.getElementById("app-toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "app-toast";
    document.body.appendChild(el);
  }
  el.className = `app-toast ${kind}`;
  el.textContent = msg;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.remove(), ms);
}

// --- Navbar-Badges (Übergaben, Aktivität) -----------------------------------

function setNavBadge(linkId, count, title) {
  const link = document.getElementById(linkId);
  if (!link) return;
  let badge = link.querySelector(".nav-badge");
  if (!count) {
    if (badge) badge.remove();
    return;
  }
  if (!badge) {
    badge = document.createElement("span");
    badge.className = "nav-badge";
    link.appendChild(badge);
  }
  badge.textContent = count > 99 ? "99+" : String(count);
  if (title) badge.title = title;
}

async function refreshNavBadges() {
  try {
    const n = await api("/api/notifications");
    setNavBadge("nav-handovers", n.handovers_open, "neue Übergaben an dich");
    setNavBadge("nav-activity", n.activity_new, "neue Aktivität");
    setNavBadge("nav-calendar", n.overdue, "überfällige Aufgaben");
  } catch (_e) {
    /* Badges sind nice-to-have – Fehler still schlucken */
  }
}
window.refreshNavBadges = refreshNavBadges;

// Abmelde-Button (in base.html vorhanden, wenn eingeloggt).
document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("logout-btn");
  if (btn) {
    btn.addEventListener("click", async () => {
      await api("/api/auth/logout", { method: "POST" });
      window.location.href = "/login";
    });
  }
  if (document.querySelector(".topbar nav")) refreshNavBadges();
});

window.api = api;
window.escapeHtml = escapeHtml;
window.toast = toast;
