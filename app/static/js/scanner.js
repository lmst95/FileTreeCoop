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

// --- Inhalts-Hash (SHA-256) -------------------------------------------------
//
// Läuft als eigener Nachlauf nach dem Scan: der Server nennt die Dateien ohne
// gültigen Hash, hier wird gelesen und gerechnet, zurück geht nur der Hex-String
// – der Dateiinhalt verlässt den Rechner nie. Jederzeit abbrechbar; der
// nächste Lauf macht dort weiter, wo dieser aufgehört hat.

// Größere Dateien werden übersprungen: crypto.subtle kennt kein inkrementelles
// Hashen, die Datei muss komplett in den Speicher.
const MAX_HASH_BYTES = 256 * 1024 * 1024;
// Die Arbeitsliste wird in großen Blöcken geholt und dann komplett abgearbeitet
// – Ergebnisse gehen zwischendurch hoch (wie die Batches beim Scan), aber die
// Liste wird NICHT nach jedem Päckchen neu erfragt. Sonst entstünde ein
// Ping-Pong aus POST und GET, bei dem der Browser die halbe Zeit auf den Server
// wartet, statt zu rechnen.
const HASH_TODO_BATCH = 5000;
const HASH_POST_BATCH = 100;
// Fortschritt höchstens alle 400 ms melden; sonst rendert das UI je Datei neu.
const HASH_PROGRESS_MS = 400;

let hashCancelled = false;

async function sha256Hex(file) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

// Pfad relativ zur Wurzel in ein FileHandle auflösen; Ordner-Handles werden
// zwischengespeichert, weil die Arbeitsliste quer durch den Baum springt.
async function fileHandleForPath(rootHandle, path, dirCache) {
  const parts = path.split("/");
  const fileName = parts.pop();
  let dir = rootHandle;
  let prefix = "";
  for (const part of parts) {
    prefix = prefix ? `${prefix}/${part}` : part;
    let next = dirCache.get(prefix);
    if (!next) {
      next = await dir.getDirectoryHandle(part);
      dirCache.set(prefix, next);
    }
    dir = next;
  }
  return dir.getFileHandle(fileName);
}

async function hashPending(sourceId, onProgress) {
  const dirHandle = await idbGet(`source:${sourceId}`);
  if (!dirHandle) {
    throw new Error("Kein gemerkter Ordner – bitte die Quelle zuerst scannen.");
  }
  if (!(await ensurePermission(dirHandle))) {
    throw new Error("Zugriff auf den Ordner wurde abgelehnt.");
  }
  if (!(crypto && crypto.subtle)) {
    throw new Error(
      "SHA-256 steht nur in einem sicheren Kontext zur Verfügung (https oder localhost)."
    );
  }

  hashCancelled = false;
  const dirCache = new Map();
  const stats = { hashed: 0, skipped: 0, errors: 0, reconciled: 0, done: 0 };
  let pending = [];
  let lastProgress = 0;

  const report = (force = false) => {
    if (!onProgress) return;
    const now = Date.now();
    if (!force && now - lastProgress < HASH_PROGRESS_MS) return;
    lastProgress = now;
    onProgress(stats);
  };

  const flush = async () => {
    if (!pending.length) return;
    const res = await api(`/api/sources/${sourceId}/hashes`, {
      method: "POST",
      body: { items: pending },
    });
    stats.reconciled += (res && res.reconciled) || 0;
    pending = [];
  };

  // Was in diesem Lauf schon abgearbeitet wurde. Taucht eine Datei erneut in
  // der Arbeitsliste auf, hat die Übernahme sie nicht als erledigt gebucht –
  // dann wird abgebrochen statt endlos im Kreis zu laufen.
  const handled = new Set();

  while (!hashCancelled) {
    const todo = await api(
      `/api/sources/${sourceId}/hash-todo?limit=${HASH_TODO_BATCH}`
    );
    if (!todo.length) break;
    const fresh = todo.filter((item) => !handled.has(item.path));
    if (!fresh.length) {
      stats.stuck = todo.length;
      console.warn(
        `Hash-Lauf beendet: ${todo.length} Einträge bleiben offen (z. B. ${todo[0].path}).`
      );
      break;
    }

    for (const item of fresh) {
      if (hashCancelled) break;
      handled.add(item.path);
      const result = { path: item.path, size: item.size, mtime: item.mtime };
      try {
        const handle = await fileHandleForPath(dirHandle, item.path, dirCache);
        const file = await handle.getFile();
        // Größe/Datum, wie sie beim Hashen tatsächlich vorlagen. Der Server
        // bucht den Hash gegen den Index-Stand; diese Werte dokumentieren nur,
        // was gelesen wurde (und weichen ab, wenn der Index veraltet ist).
        result.size = file.size;
        result.mtime = file.lastModified / 1000;
        if (file.size > MAX_HASH_BYTES) {
          result.state = "skipped";
          stats.skipped += 1;
        } else {
          result.sha256 = await sha256Hex(file);
          result.state = "ok";
          stats.hashed += 1;
        }
      } catch (err) {
        result.state = "error";
        stats.errors += 1;
        console.warn(`Hash übersprungen (${item.path}):`, err);
      }
      pending.push(result);
      stats.done += 1;
      if (pending.length >= HASH_POST_BATCH) await flush();
      report();
    }
    await flush();
    report(true);
  }
  await flush();
  report(true);
  stats.cancelled = hashCancelled;
  return stats;
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

  // Inhalts-Hashes für alles nachrechnen, was noch keinen gültigen hat.
  hashPending,

  cancelHashing() {
    hashCancelled = true;
  },
};

window.Scanner = Scanner;
