// Basispfade pro Quelle und Gerät + „Pfad kopieren“.
//
// Hintergrund: Der Browser darf keinen Dateimanager öffnen, und die File System
// Access API gibt den absoluten Pfad eines gewählten Ordners nicht heraus – wir
// kennen nur Pfade *relativ* zur gescannten Wurzel. Wer den vollständigen Pfad
// in der Zwischenablage haben will, hinterlegt die Wurzel deshalb einmal pro
// Gerät selbst (localStorage, verlässt den Rechner nie).

(function () {
  const key = (sourceId) => `ftc:root:${sourceId}`;

  function getRoot(sourceId) {
    return localStorage.getItem(key(sourceId)) || "";
  }

  // Leerer Wert löscht den Eintrag; abschließende Trenner werden gekappt.
  function setRoot(sourceId, root) {
    const clean = (root || "").trim().replace(/[/\\]+$/, "");
    if (clean) localStorage.setItem(key(sourceId), clean);
    else localStorage.removeItem(key(sourceId));
    return clean;
  }

  // "C:\…" oder UNC "\\server\share" → Windows-Trenner verwenden.
  function isWindowsRoot(root) {
    return /^[a-zA-Z]:[\\/]/.test(root) || root.startsWith("\\\\");
  }

  // Ergibt { text, absolute }: ohne hinterlegte Wurzel bleibt es der relative Pfad.
  function fullPath(sourceId, relPath) {
    const root = getRoot(sourceId);
    if (!root) return { text: relPath, absolute: false };
    const win = isWindowsRoot(root);
    const rel = win ? relPath.replace(/\//g, "\\") : relPath;
    return { text: root + (win ? "\\" : "/") + rel, absolute: true };
  }

  async function copyText(text) {
    // navigator.clipboard gibt es nur im sicheren Kontext (https oder localhost).
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    if (!ok) throw new Error("Der Browser hat das Kopieren abgelehnt.");
  }

  async function copyPath(sourceId, relPath) {
    const res = fullPath(sourceId, relPath);
    await copyText(res.text);
    return res;
  }

  window.LocalPaths = { getRoot, setRoot, fullPath, copyPath };
})();
