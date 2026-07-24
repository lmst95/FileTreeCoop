# filetree_coop

Tracke systematisch, wo Dateien liegen — über den lokale Rechner — und finde
sie per beschreibendem Suchstring statt exaktem Namen. An jede Datei lassen
sich Notizen, Todos, Labels und Übergaben an Kollegen hängen. Neuerdings
lassen sich Notizen frei erstellen und mit KI verfeinern.

Der Clou: Der Browser selbst ist der Scanner. Über die File System Access API
wählst du einen Ordner, JavaScript läuft rekursiv durch den Baum und überträgt
nur Metadaten (Pfad, Name, Größe, Änderungsdatum) an den Server — kein
Dateiinhalt, keine Agent-Installation.

## Schnellstart

```bash
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py  # -> http://127.0.0.1:8000
```

Dann im Chrome oder Edge öffnen (die File System Access API gibt es nur in
Chromium-Browsern), registrieren, eine Quelle anlegen und einen Ordner scannen.

### Tests

```bash
pytest -q
```

## Wie es funktioniert

1. **Registrieren/Anmelden** — Konten trennen Notizen und Übergaben.
2. **Quelle anlegen** — ein benanntes Wurzelverzeichnis („Laptop – Projekte“,
   „Netzlaufwerk P:“ …) mit Art und Ortshinweis.
3. **Scannen** — „Ordner wählen & scannen“ öffnet den Ordner-Dialog; der Browser
   erfasst alle Dateien/Ordner darunter und schickt sie in Batches an den Server.
   Der Ordner-Handle wird in IndexedDB gemerkt → **„Erneut scannen“** ohne Dialog.
4. **Finden** — auf zwei Wegen:
   - **Suche** (`/search`): Freitext über Name, Pfad, Notizen und Labels (SQLite
     FTS5, mit Präfix-Matching, z. B. findet `rech` den Ordner `Rechnungen`).
   - **Baum** (`/browse`): durch die Ordner klicken; jede Ebene wird bei Bedarf
     nachgeladen. Über 📋 lässt sich der **Pfad kopieren** (siehe unten).
5. **Annotieren** — an jedem Treffer bzw. Baum-Eintrag Notiz/Todo/Label/Übergabe
   hinzufügen, Todos abhaken. Todos und Übergaben können ein **Fälligkeitsdatum**
   tragen (Datumsfeld im Editor, später jederzeit in der Detailliste änderbar;
   leeres Feld = kein Termin). Notizen überleben Re-Scans; verschwundene Dateien
   werden als *„verschwunden“* markiert, ohne die Notizen zu verlieren.
6. **Teilen & Übergeben** — per E-Mail an Kollegen freigeben (*nur lesen* oder
   *annotieren*), inklusive aller Notizen/Todos darin. Zwei Granularitäten:
   - **Ganze Quelle** — im Dashboard über „Teilen“.
   - **Nur ein Unterordner (Teilbaum)** — im **Baum** über das 🔗-Icon an jedem
     Ordner. Der Kollege sieht dann **genau diesen Ordner als Wurzel**, kann nur
     darin browsen/suchen/annotieren; alles darüber/daneben bleibt verborgen.

   Hat der Empfänger **noch kein Konto**, wird die Freigabe als **Einladung**
   gemerkt und greift automatisch, sobald er sich mit dieser E-Mail registriert.

   Eine **Übergabe** richtet sich an einen konkreten Empfänger aus dem Kreis der
   Mitglieder *dieses Pfads* – der Name wird am Eintrag angezeigt („→ Bob“).
   Übergaben haben einen **Workflow-Status** (*offen → angenommen → erledigt*):
   Unter **Übergaben** (`/handovers`) sieht jeder „An mich“ und „Von mir“,
   nimmt an, erledigt und öffnet wieder – der Empfänger darf das auch, wenn er
   sonst nur Leserechte hat. Neue Übergaben und Überfälliges erscheinen als
   **Badge in der Navigation**.
6b. **Diskutieren** — auf jede Annotation lässt sich per ↩ **antworten**
   (kleine Threads); jede Annotation zeigt **Autor und Datum**.
6c. **Aktivität** (`/activity`) — „Was ist passiert, seit ich weg war?“:
   Annotationen von Kollegen und Scans, zeitlich gemischt. Einträge mit neuen
   fremden Notizen tragen im Baum und in der Suche einen **blauen Punkt**, bis
   man die Quelle wieder besucht hat.
7. **Übersicht** (`/overview`) — alle Klassifizierungen quellenübergreifend an
   einem Ort: filterbar nach **Typ** (Notiz/Todo/Label/Übergabe), **Label**
   (Facetten-Chips mit Häufigkeit), **Quelle**, **Textsuche**, „**nur offene
   Todos**“ und „**an mich übergeben**“. Todos lassen sich hier direkt abhaken
   und ihr Termin direkt setzen.
8. **Kalender** (`/calendar`) — alles mit Termin in zwei Ansichten:
   - **🗓 Monat** — Monatsraster, jeder Tag zeigt seine Aufgaben; ein Klick auf
     einen Tag öffnet die vollen Einträge darunter. Heute ist markiert, Tage mit
     Überfälligem stechen rot heraus.
   - **📋 Anstehend** — nur Aufgaben *mit* Termin, streng nach Datum sortiert und
     gruppiert in *Überfällig · Heute · Morgen · Diese Woche · Diesen Monat ·
     Später*.

   Beide teilen die Filter **Quelle**, „**nur offene**“ (standardmäßig an) und
   „**an mich übergeben**“; abhaken geht direkt in beiden Ansichten.

**Mehrere Annotationen pro Datei** sind ausdrücklich möglich — beliebig viele
Notizen, Todos, Labels und Übergaben nebeneinander.

**⌘K / Strg+K** öffnet auf jeder Seite eine **Schnellsuch-Palette**; Enter
springt direkt in den Baum, der sich **bis zum Ziel aufklappt** (Deep-Links
`?path=…` funktionieren auch aus Übergaben und Aktivität heraus).

**Exporte:** 📆 **iCal** (`/api/export/calendar.ics`, alle offenen Termine für
Outlook/Apple/Google Calendar), ⬇ **CSV** aller Annotationen (Übersicht,
Excel-tauglich) und ⬇ **JSON** je Quelle (Dashboard, komplettes Backup samt
Annotationen).

**Bedienung (Explorer-Stil):** kompakte Zeilen; Annotationen erscheinen als kleine
**Chips** (`#label`, `📝 2`, `☑ 1/3`, `➦ Bob`, `📅 23.7.2026` für den nächsten
offenen Termin – rot, sobald er überfällig ist). Rechts in jeder Zeile erscheinen
beim Überfahren **Action-Icons** (📝 Notiz · ☑ Todo · 🏷 Label · ➦ Übergabe · ⋯
Details) – kein Typ-Dropdown mehr, ein Klick öffnet direkt den passenden
Inline-Editor. Dateien tragen **Typ-Icons** nach Endung (📕 PDF, 📊 Tabelle,
🖼️ Bild, 🎬 Video, 🐍 Python …), Ordner ein 📁.

## Pfad kopieren (📋)

Ein **Ordner im OS-Explorer/Finder zu öffnen ist aus dem Browser technisch nicht
möglich** (Sandbox) – und die File System Access API gibt den **absoluten Pfad**
einer gescannten Wurzel gar nicht heraus, wir kennen nur Pfade *relativ* dazu.
Statt eine Datei im Browser zu öffnen, legt der 📋-Button an jeder Zeile deshalb
ihren **Pfad in die Zwischenablage** – einfügen z. B. in Finder mit `⌘⇧G` oder im
Explorer in die Adresszeile.

Damit dabei der **vollständige** Pfad herauskommt, trägt man unter **Quellen**
je Quelle einmal den **Basispfad auf diesem Gerät** ein (z. B.
`/Users/name/Documents`). Er liegt in `localStorage`, verlässt den Rechner nie
und wird pro Gerät gesetzt – sinnvoll auch bei geteilten Quellen, weil ein
Netzlaufwerk bei jedem woanders hängt. Ohne Basispfad wird der relative Pfad
kopiert. Windows-Wurzeln (`C:\…`, `\\server\share`) werden erkannt und mit
Backslashes zusammengesetzt.

Wer wirklich **einen Klick → Ordner geht auf** will, braucht ein optionales
lokales Helfer-Programm (siehe Roadmap: Python-CLI/Agent), das
`explorer`/`open`/`xdg-open` aufruft.

## Was beim erneuten Scannen passiert

Jeder Scan ist ein **eigener Lauf** mit Diff: Das Dashboard zeigt je Quelle
„+3 neu · 2 geändert · 1 verschoben · 4 verschwunden“, die aufklappbare
**Scan-Historie** listet jeden Lauf samt Einzeländerungen (wer, wann, was).
Scans von Kollegen erscheinen zudem im Aktivitäts-Feed.

| Fall | Verhalten | Annotationen |
|------|-----------|--------------|
| **Neue Datei/Ordner** | wird als neuer Eintrag angelegt | (noch keine) |
| **Unverändert** | `last_seen` aktualisiert, bleibt *vorhanden* | bleiben erhalten |
| **Geändert** (Größe/Datum) | Metadaten aktualisiert, Änderung protokolliert | bleiben erhalten |
| **Gelöscht / nicht mehr da** | wird als *verschwunden* markiert (nicht gelöscht) | **bleiben erhalten** |
| **Wieder aufgetaucht** | Status springt zurück auf *vorhanden* | die alten sind wieder da |
| **Verschoben/umbenannt** | **Umzug-Erkennung**: stimmen Name, Größe und Änderungsdatum eindeutig überein, wird der Eintrag umgezogen | **wandern mit** zum neuen Pfad |

Kurz: **Notizen/Todos gehen nie durch einen Scan verloren.** Einträge werden
nie automatisch gelöscht – nur als *verschwunden* markiert (in Suche/Übersicht
filterbar). Aufgeräumt wird bewusst per Hand: **🧹 Aufräumen** im Dashboard
entfernt verschwundene Einträge, lässt annotierte aber standardmäßig stehen.
Grenze der Umzug-Erkennung: Bei **mehrdeutigen** Kandidaten (mehrere identische
Dateien) wird nichts zugeordnet – dann gilt wie früher *verschwunden* + *neu*
(präziser würde erst ein Content-Hash, siehe Roadmap).

## Architektur

| Schicht      | Technik |
|--------------|---------|
| Web-Framework| FastAPI + Uvicorn |
| Datenbank    | SQLite über SQLAlchemy 2.0 |
| Migrationen  | **Alembic** (`migrations/`); `init_db()` migriert beim Start automatisch |
| Suche        | SQLite **FTS5**, per Trigger synchron gehaltener Index `entries_fts` |
| Auth         | E-Mail/Passwort (bcrypt), signiertes Session-Cookie |
| Frontend     | Jinja2-Templates + Vanilla JS (kein Build-Step) |
| Scanner      | Browser, File System Access API (`showDirectoryPicker`) |

```
app/
  main.py            FastAPI-App, Seiten, Startup (init_db)
  db.py              Engine, Session, Alembic-Anbindung, FTS5-Index + Sync-Trigger
  models.py          users, sources, source_shares, entries, annotations,
                     scans, entry_changes, source_visits, invites
  auth.py            Passwort-Hashing, Session, current_user
  access.py          zugängliche Quellen eines Nutzers (geteilt)
  search.py          FTS5-Query-Builder (Präfix, bm25-Ranking)
  serializers.py     Annotationen inkl. Autor- und Empfängername
  routers/           auth, sources (+ingest/scans/shares/invites/members/seen/missing),
                     entries, search, annotations, activity (+notifications), export
  templates/         login, dashboard, browse, search, overview, handovers, calendar, activity
  static/js/         app, entry_ui, palette, auth, scanner, dashboard, tree, search,
                     overview, handovers, calendar, activity
migrations/          Alembic (0001 Baseline, 0002 Kooperations-Ausbau)
tests/               ingest, scans, search, annotations, threads, handover_flow,
                     activity, invites, cleanup, export, … (pytest + TestClient)
```

## Datenmodell (Kurzform)

- **users** — Konten (+ `last_activity_seen_at` für das Aktivitäts-Badge).
- **sources** — registrierte Filesystem-Wurzeln (owner, label, kind, host_hint).
- **source_shares** — Freigabe an andere Nutzer (read | annotate); `path_prefix`
  leer = ganze Quelle, sonst nur dieser Teilbaum.
- **invites** — ausstehende Freigaben an E-Mails ohne Konto; werden bei der
  Registrierung automatisch zu `source_shares`.
- **entries** — Dateien/Ordner je Quelle; eindeutig per `(source_id, path)`;
  `status` = present | missing.
- **annotations** — note | todo | label | handover, an `entries` verankert;
  `due_date` (optional) speist den Kalender; `status` (open | accepted | done)
  ist der Übergabe-Workflow; `parent_annotation_id` macht Antworten möglich.
- **scans** — ein Scan-Lauf je Quelle mit Diff-Zählern (neu/geändert/
  verschwunden/verschoben/wieder da) und Startzeit (ersetzt das frühere
  In-Memory-Register, funktioniert auch mit mehreren Workern).
- **entry_changes** — Einzeländerungen je Scan (inkl. `old_path` bei Umzügen)
  = Änderungs-Historie jeder Datei.
- **source_visits** — letzter Besuch (Nutzer, Quelle) für die Ungelesen-Punkte.
- **llm_connections** — nutzerbezogene LLM-API-Anbindung (Typ, Basis-URL,
  verschlüsselter Token, Modell-Cache).
- **llm_settings** — auswählbares Inferenz-Profil (Verbindung + Modell +
  Parameter); erscheint in den Feature-Dropdowns.
- **llm_prompts** — wiederverwendbare Prompt-Vorlagen mit `{{input}}`-Platzhalter.
- **llm_feature_links** — macht ein Setting/Prompt in einem Feature (z. B.
  `notes`) verfügbar; die generische Zuordnung für alle künftigen Konsumenten.
- **ai_runs** — protokollierter LLM-Lauf samt Ergebnis, feature-unabhängig an
  `(target_kind, target_id)` gebunden (z. B. `("annotation", 42)`).

## KI-Integration (LLM)

Unter dem Reiter **KI** verwaltet jeder Nutzer seine eigenen LLM-Bausteine:

- **Verbindungen** — eine API-Anbindung (OpenAI, Anthropic, Ollama, jede
  OpenAI-kompatible URL). Der API-Token wird verschleiert gespeichert (nie im
  Klartext, nie an den Browser zurückgegeben) und lässt sich per „Testen“ /
  „Modelle abrufen“ prüfen.
- **LLM-Settings** — Verbindung + Modell + Parameter (temperature, max_tokens,
  System-Prompt) unter einem Namen; das ist die Auswahl in den Feature-Dropdowns.
  Optional **Web-Suche** (OpenAI): lässt das Modell live im Web recherchieren und
  hängt die gefundenen **Quellen** ans Ergebnis an. Setzt bei OpenAI ein
  suchfähiges Modell voraus (`gpt-4o-search-preview`, `gpt-4o-mini-search-preview`
  oder `gpt-5-search-api`) und schickt technisch `web_search_options` an
  `/chat/completions`. „**Web-Suche-Setting anlegen**“ erzeugt aus einer
  vorhandenen OpenAI-Verbindung mit einem Klick ein fertig konfiguriertes Setting
  (Suchmodell + Web-Suche an, dem Feature „Notizen“ zugeordnet).
- **Prompts** — benannte Vorlagen; `{{input}}` markiert, wo der jeweilige Text
  (z. B. eine Notiz) eingesetzt wird. Jeder neue Nutzer bekommt vier
  **Standard-Prompts** als Starthilfe (dem Feature „Notizen“ zugeordnet):
  *Sprache & Grammatik* (sprachlich überarbeiten), *Komplexes erklären*
  (Erklärungen ergänzen), *Erweitern & beantworten* (Fragen/Themen vertiefen)
  und *Web-Recherche* (aktuelle Antworten, am besten mit einem Web-Suche-Setting).
  „Standard-Prompts anlegen“ auf der KI-Seite legt fehlende Vorlagen jederzeit
  (idempotent) wieder an.

Settings und Prompts werden pro **Feature** freigegeben. Aktuell konsumiert das
die **Notiz**-Ansicht: jede Notiz hat die Tabs *Original* und *KI-überarbeitet*,
in dem sich Prompt + Setting wählen und das Ergebnis übernehmen oder als neue
Notiz speichern lässt.

Technisch generisch aufgebaut (`app/llm/`): ein Provider-unabhängiger Service
mit Adaptern je Anbieter und **einem** Endpunkt `POST /api/llm/run`. Ein neues
Feature bindet die KI an, indem es einen `feature_key` in `app/llm/features.py`
registriert und `/api/llm/run` mit passendem `target_kind` aufruft — ohne
Änderung am LLM-Kern.

> ⚠️ **Datenschutz:** Beim Ausführen wird der jeweilige Inhalt (z. B. ein
> Notiztext) an den gewählten Anbieter übertragen. Nutze nur Endpunkte, denen du
> vertraust; für maximale Datenhoheit bietet sich ein self-hosted Ollama an.

## Konfiguration (Umgebungsvariablen)

| Variable | Default | Zweck |
|----------|---------|-------|
| `FTC_DB_PATH` | `./filetree_coop.db` | Pfad zur SQLite-Datei |
| `FTC_SECRET_KEY` | `dev-secret-change-me` | **In Produktion setzen!** Signiert Cookies |
| `FTC_SESSION_HTTPS_ONLY` | `false` | Cookie nur über HTTPS senden |
| `FTC_ENCRYPTION_KEY` | = `FTC_SECRET_KEY` | Schlüssel für die Verschleierung gespeicherter LLM-Tokens |
| `FTC_LLM_BLOCK_PRIVATE_HOSTS` | `false` | LLM-Basis-URLs auf private/lokale Adressen sperren (SSRF-Schutz); `false` lässt localhost/Ollama zu |
| `FTC_LLM_TIMEOUT_SECONDS` | `60` | Timeout ausgehender LLM-Requests |

## Bekannte Grenzen / Roadmap

- Nur der **Ordner-Scan** braucht einen Chromium-Browser (Chrome/Edge); alles
  andere (Suchen, Baum, Annotieren, Übergaben, Kalender) läuft überall.
- Termine sind reine **Tagesdaten** (keine Uhrzeiten, keine Wiederholungen);
  Erinnerungen übernimmt der Kalender des Vertrauens via **iCal-Export**.
- Die Umzug-Erkennung arbeitet mit (Name, Größe, mtime) und lässt mehrdeutige
  Fälle bewusst unangetastet.
- **Später:** wiederkehrende Todos, semantische Suche (Embeddings),
  Umzug-Erkennung per Content-Hash, optionale Inhalts-Indexierung, optionales
  Python-CLI für headless-Scans, E-Mail-Digest der Aktivität, Umstieg auf
  Postgres bei größerem Mehrbenutzerbetrieb.
