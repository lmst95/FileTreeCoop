// Aktivitäts-Feed: Annotationen von Kollegen + Scans, zeitlich gemischt.
// Ein Besuch der Seite markiert den Feed als gesehen (Badge-Reset).
// IIFE gegen Namenskollisionen mit den global geladenen Skripten (entry_ui.js).
(function () {

const feedEl = document.getElementById("act-feed");

const ACT_TYPE_LABEL = { note: "Notiz", todo: "Todo", label: "Label", handover: "Übergabe" };
const ACT_TYPE_ICON = { note: "📝", todo: "☑", label: "🏷", handover: "➦" };

function relTime(iso) {
  const then = new Date(iso + "Z").getTime();
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "gerade eben";
  if (mins < 60) return `vor ${mins} min`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `vor ${hours} h`;
  const days = Math.round(hours / 24);
  if (days === 1) return "gestern";
  if (days < 7) return `vor ${days} Tagen`;
  return new Date(iso + "Z").toLocaleDateString("de-DE");
}

function scanText(it) {
  if (it.initial) return `${it.added} Einträge importiert`;
  const parts = [];
  if (it.added) parts.push(`+${it.added} neu`);
  if (it.changed) parts.push(`${it.changed} geändert`);
  if (it.moved) parts.push(`${it.moved} verschoben`);
  if (it.missing) parts.push(`${it.missing} verschwunden`);
  if (it.reappeared) parts.push(`${it.reappeared} wieder da`);
  return parts.length ? parts.join(" · ") : "keine Änderungen";
}

function itemHtml(it, seenUntil) {
  const isNew = seenUntil && !it.is_own && it.when > seenUntil;
  const newDot = isNew || (!seenUntil && !it.is_own)
    ? `<span class="chip chip-new" title="neu seit deinem letzten Besuch">●</span>` : "";
  const when = `<span class="muted small act-when">${escapeHtml(relTime(it.when))}</span>`;
  if (it.kind === "scan") {
    const by = it.by_name ? `${escapeHtml(it.by_name)}` : "jemand";
    return `<div class="card act-item ${it.is_own ? "act-own" : ""}">
        ${newDot}🔄 <strong>${by}</strong> hat
        <a href="/browse?source=${it.source_id}">„${escapeHtml(it.source_label)}“</a>
        gescannt: ${escapeHtml(scanText(it))} ${when}
      </div>`;
  }
  const icon = ACT_TYPE_ICON[it.type] || "📝";
  const typeLabel = ACT_TYPE_LABEL[it.type] || it.type;
  const what = it.type === "label"
    ? `#${escapeHtml(it.label_value)}`
    : escapeHtml(it.body || "");
  const reply = it.is_reply ? " geantwortet:" : ` (${typeLabel}):`;
  const link = `/browse?source=${it.source_id}&path=${encodeURIComponent(it.entry_path)}`;
  return `<div class="card act-item ${it.is_own ? "act-own" : ""}">
      ${newDot}${icon} <strong>${escapeHtml(it.author_name)}</strong> an
      <a href="${link}">${escapeHtml(it.entry_name)}</a>
      <span class="muted small">(${escapeHtml(it.source_label)})</span>${reply}
      <span class="act-body">${what}</span> ${when}
    </div>`;
}

async function loadFeed() {
  const { items, seen_until } = await api("/api/activity");
  feedEl.innerHTML = items.length
    ? items.map((it) => itemHtml(it, seen_until)).join("")
    : `<p class="muted">Noch keine Aktivität. Sobald Kollegen annotieren oder scannen, erscheint es hier.</p>`;
  // Als gesehen markieren und das Badge zurücksetzen.
  await api("/api/activity/seen", { method: "POST" }).catch(() => {});
  if (window.refreshNavBadges) window.refreshNavBadges();
}

loadFeed().catch((e) => (feedEl.innerHTML = `<p class="error">${escapeHtml(e.message)}</p>`));

})();
