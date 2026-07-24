// Browser-seitiger Datei-Scanner via File System Access API.
// Läuft rekursiv durch einen vom Nutzer gewählten Ordner und pusht Metadaten
// (kein Dateiinhalt!) in Batches an den Server.

const BATCH_SIZE = 500;

// --- IndexedDB: Ordner-Handles pro Quelle speichern (für Re-Scans) ---------

function idb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("ftc_handles", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("handles");
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function idbSet(key, value) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("handles", "readwrite");
    tx.objectStore("handles").put(value, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

async function idbGet(key) {
  const db = await idb();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("handles", "readonly");
    const req = tx.objectStore("handles").get(key);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

// --- Berechtigung sicherstellen --------------------------------------------

async function ensurePermission(handle) {
  const opts = { mode: "read" };
  if ((await handle.queryPermission(opts)) === "granted") return true;
  return (await handle.requestPermission(opts)) === "granted";
}

// --- Verzeichnisbaum durchlaufen -------------------------------------------

// Eigener Name (nicht „extOf“): entry_ui.js deklariert global eine
// gleichnamige Funktion mit anderer Signatur – auf /browse und /dashboard
// werden beide Skripte geladen und würden sich sonst überschreiben.
function extFromName(name) {
  const i = name.lastIndexOf(".");
  return i > 0 ? name.slice(i + 1).toLowerCase() : "";
}

// Fehler, wenn die Wurzel selbst nicht gelesen werden kann (z. B. Netzlaufwerk
// nicht verbunden). Wird als fatal behandelt: der Scan bricht ab, BEVOR eine
// Abschluss-Batch gesendet wird – so bleibt der bestehende Index unangetastet
// (statt fälschlich alles als „verschwunden“ zu markieren).
function unreachableError(cause) {
  const err = new Error(
    "Netzlaufwerk oder Ordner nicht erreichbar – der Index wurde nicht verändert."
  );
  err.name = "SourceUnreachableError";
  err.cause = cause;
  return err;
}

// Auf Netzlaufwerken (SMB/DFS) können Einträge zwischen Auflisten und Zugriff
// verschwinden oder kurz unerreichbar sein -> die File System Access API wirft
// dann NotFoundError ("A requested file or directory could not be found ...").
// Fehler an einem Unterordner überspringen nur diesen (onSkip), Fehler an der
// WURZEL (prefix === "") sind fatal und brechen den ganzen Scan ab.
async function* walk(dirHandle, prefix = "", onSkip = null) {
  let iterator;
  try {
    iterator = dirHandle.entries();
  } catch (e) {
    if (prefix === "") throw unreachableError(e);
    if (onSkip) onSkip(prefix, e);
    return;
  }
  while (true) {
    let step;
    try {
      step = await iterator.next();
    } catch (e) {
      // Wurzel unlesbar -> fatal; Unterordner -> nur diesen überspringen.
      if (prefix === "") throw unreachableError(e);
      if (onSkip) onSkip(prefix, e);
      return;
    }
    if (step.done) break;
    const [name, handle] = step.value;
    const path = prefix ? `${prefix}/${name}` : name;
    if (handle.kind === "directory") {
      yield { path, name, is_dir: true, size: 0, mtime: 0, ext: "" };
      yield* walk(handle, path, onSkip);
    } else {
      let size = 0;
      let mtime = 0;
      try {
        const file = await handle.getFile();
        size = file.size;
        mtime = file.lastModified / 1000; // ms -> Sekunden
      } catch (_e) {
        /* Datei evtl. nicht lesbar – trotzdem als Eintrag erfassen */
      }
      yield { path, name, is_dir: false, size, mtime, ext: extFromName(name) };
    }
  }
}

// --- Ein Scan-Lauf ----------------------------------------------------------

async function scanHandle(sourceId, dirHandle, onProgress) {
  if (!(await ensurePermission(dirHandle))) {
    throw new Error("Zugriff auf den Ordner wurde abgelehnt.");
  }
  const scanId = crypto.randomUUID();
  let buffer = [];
  let total = 0;
  const skipped = [];

  async function flush(finalize) {
    const body = { entries: buffer, finalize, scan_id: scanId };
    if (finalize && skipped.length) {
      // Übersprungene Einträge zusammen mit der Abschluss-Batch übertragen
      // (dann liegt die vollständige Liste vor) und persistieren.
      body.skipped = skipped.map((s) => ({ path: s.path, reason: s.error }));
      // Unvollständiger Scan: NICHT als „verschwunden“ markieren, sonst würden
      // die Inhalte übersprungener (nur kurz unerreichbarer) Ordner fälschlich
      // als gelöscht gelten.
      body.mark_missing = false;
    }
    const res = await api(`/api/sources/${sourceId}/ingest`, {
      method: "POST",
      body,
    });
    buffer = [];
    return res;
  }

  const onSkip = (path, err) => {
    skipped.push({ path, error: String(err && err.name ? err.name : err) });
    console.warn(`Scan: Eintrag übersprungen (${path || "<root>"}):`, err);
  };

  for await (const entry of walk(dirHandle, "", onSkip)) {
    buffer.push(entry);
    total += 1;
    if (buffer.length >= BATCH_SIZE) {
      await flush(false);
      if (onProgress) onProgress(total);
    }
  }
  // Letzte Batch immer mit finalize=true senden (markiert Verschwundenes,
  // führt die Umzug-Erkennung aus). Volles IngestResult durchreichen.
  const result = await flush(true);
  if (onProgress) onProgress(total);
  return { total, skipped, ...(result || {}) };
}

// --- Öffentliche API --------------------------------------------------------

const Scanner = {
  supported() {
    return "showDirectoryPicker" in window;
  },

  // Neuen Ordner wählen, Handle für Re-Scans merken, dann scannen.
  async pickAndScan(sourceId, onProgress) {
    const dirHandle = await window.showDirectoryPicker({ mode: "read" });
    await idbSet(`source:${sourceId}`, dirHandle);
    return scanHandle(sourceId, dirHandle, onProgress);
  },

  // Erneut scannen mit gemerktem Handle (kein Dialog, falls Berechtigung noch gilt).
  async rescan(sourceId, onProgress) {
    const dirHandle = await idbGet(`source:${sourceId}`);
    if (!dirHandle) {
      throw new Error("Kein gemerkter Ordner – bitte neu scannen.");
    }
    return scanHandle(sourceId, dirHandle, onProgress);
  },

  async hasHandle(sourceId) {
    return Boolean(await idbGet(`source:${sourceId}`));
  },

};

window.Scanner = Scanner;
