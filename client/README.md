# filetree_coop – Desktop-Client

Ein kleines Hintergrundprogramm, das eingestellte Ordner überwacht und ihre
**Metadaten** mit einem filetree_coop-Server abgleicht. Es erscheint als Symbol
im Infobereich der Taskleiste (unten rechts); ein Klick darauf öffnet die
Einstellungen.

Was er dem Browser-Scanner voraushat:

- läuft **ohne offenen Tab**, auch wenn niemand am Rechner sitzt,
- meldet Änderungen **sofort** statt erst beim nächsten manuellen Scan,
- rechnet **Inhalts-Hashes** im Hintergrund, ohne Größengrenze
  (der Browser muss jede Datei komplett in den Speicher laden, hier wird
  strömend gehasht),
- kann aus der Weboberfläche heraus **den Ordner im Explorer öffnen** – das
  Einzige, was ein Browser prinzipbedingt nicht darf.

> Übertragen werden ausschließlich Metadaten (Pfad, Name, Größe, Änderungs­datum)
> und auf Wunsch der SHA-256 des Inhalts. **Dateiinhalte verlassen den Rechner
> nie** – genau wie beim Browser-Scanner.

## Installation

```bash
pip install -r client/requirements.txt
```

`tkinter` gehört zur Standardinstallation von Python; unter Linux ist dafür
gelegentlich `python3-tk` aus der Paketverwaltung nachzuinstallieren.

## Starten

```bash
python -m ftc_client
```

Aus dem Verzeichnis `client/` heraus (oder mit `client/` im `PYTHONPATH`). Beim
ersten Start öffnet sich das Einstellungsfenster von selbst.

| Argument | Wirkung |
|----------|---------|
| `--settings` | Einstellungsfenster beim Start öffnen |
| `--verbose` | ausführliches Protokoll |
| `--version` | Version ausgeben |

Unter Windows geht es auch per Doppelklick auf `run_client.pyw` – das ist mit
`pythonw.exe` verknüpft und startet ohne Konsolenfenster.

## Autostart – was genau eingetragen wird

Der Schalter im Reiter *Allgemein* schreibt unter Windows einen Wert nach
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Er sieht so aus:

```
C:\…\.venv\Scripts\pythonw.exe  C:\…\client\run_client.pyw
```

Zwei Dinge sind daran wichtig:

- **Der Interpreter wird eingefroren.** Eingetragen wird das `pythonw.exe`
  *neben* dem gerade laufenden `python.exe` – richtest du den Autostart aus
  einem venv heraus ein, startet er später also genau dieses venv samt seiner
  Pakete. Richtest du ihn aus dem System-Python ein, in dem die Abhängigkeiten
  fehlen, startet der Client nicht. Der Einstellungsdialog zeigt den fertigen
  Befehl deshalb offen an.
- **Die Pfade sind absolut.** Ziehen Projekt oder venv später um, bricht der
  Eintrag. Dann einfach den Schalter aus- und wieder einschalten.

Gestartet wird `run_client.pyw` statt `-m ftc_client`: Der Run-Key kennt kein
Arbeitsverzeichnis, `-m` fände sein Paket also nur über einen `cmd`-Umweg – und
*der* blitzt bei jeder Anmeldung kurz als schwarzes Konsolenfenster auf. Das
Skript kennt seinen eigenen Ort und kommt ohne aus.

Bei einer gepackten `.exe` (siehe unten) wird schlicht diese eingetragen; venv
und Python spielen dann keine Rolle mehr — die robusteste Variante, wenn der
Client dauerhaft laufen soll.

## Einrichten

1. **Server** – Adresse eintragen, mit den eigenen Konto-Daten *Verbinden*.
   Die Anmeldung passiert genau einmal: Benutzername und Passwort werden gegen
   einen **Gerätetoken** getauscht und danach verworfen. Auf der Platte liegt
   nur der Token, und der lässt sich in der Weboberfläche unter **Geräte**
   einzeln widerrufen, ohne das Konto-Passwort zu ändern.
2. **Ordner** – je Quelle den lokalen Ordner wählen. Es lassen sich auch neue
   Quellen direkt hier anlegen.
3. **Allgemein** – *Autostart* einschalten, damit der Client künftig nach der
   Benutzeranmeldung still mitstartet.

### Schalter je Ordner

| Schalter | Bedeutung |
|----------|-----------|
| **Sync aktiv** | Ordner überhaupt abgleichen. Aus = der Ordner wird ignoriert (der Index auf dem Server bleibt erhalten, nur eben stehen). |
| **Live-Überwachung** | Änderungen sofort melden (`watchdog`). Aus = nur der turnusmäßige Voll-Scan. |
| **Inhalts-Hashes** | SHA-256 je Datei nachrechnen – Grundlage für Duplikat- und Umbenennungs-Erkennung. Getrennt schaltbar, weil es jede Datei einmal komplett liest. |
| **Voll-Scan alle … Minuten** | Abgleich des ganzen Baums. Fängt auf, was passiert ist, während der Client aus war. |
| **Änderung melden nach … Sekunden Ruhe** | Karenzzeit, siehe unten. |

### Die Karenzzeit (wichtig)

Eine erkannte Änderung wird **nicht sofort** gemeldet, sondern erst, wenn der
betreffende Pfad die eingestellte Zeit lang Ruhe gegeben hat (Voreinstellung:
10 Sekunden). Jeder Pfad hat seinen eigenen Zähler, der bei jedem weiteren
Ereignis von vorn beginnt.

Ohne diese Karenz landete lauter Unsinn im Index:

- Dateien, die noch **geschrieben** werden (Kopieren, Export, Download), kämen
  mit halber Größe an und müssten sofort wieder korrigiert werden.
- **Temporärdateien** vieler Programme (`.~lock…`, `Dokument.tmp`, Office- und
  Editor-Zwischenstände) entstehen und verschwinden im Sekundenbereich; sie
  würden angelegt und im nächsten Atemzug als „verschwunden“ markiert.
- Ein **Umbenennen** erzeugt zwei Ereignisse (alter Pfad weg, neuer da). Erst
  nach der Karenz liegen beide vor und ergeben zusammen ein sauberes Bild.

Als zweiter Riegel wird eine Datei, deren Änderungszeit beim Abholen jünger als
zwei Sekunden ist, noch einmal zurückgelegt – für Programme, die in großen
Blöcken schreiben, ohne dass für jeden ein Ereignis eintrifft.

Ein Ordner mit dauernder Schreibaktivität (Build-Verzeichnis, Datenbankdatei)
blockiert dabei nichts: die Karenz gilt pro Pfad, alle anderen Änderungen gehen
weiter ihren Weg.

## Ordner öffnen aus dem Browser

In Baum und Suche steht an jeder Zeile ein 📂, sobald ein Client die Quelle
betreut und online ist. Ein Klick legt einen Auftrag in eine Queue, die der
Client beim nächsten Heartbeat (alle 5 Sekunden) abholt.

Bewusst **Polling statt Push**: so muss der Rechner von außen nicht erreichbar
sein, es funktioniert hinter NAT, Proxy und Firewall, und es braucht keine
offene Verbindung. Übertragen wird nur der Pfad *relativ* zur Quelle – wo die
Wurzel auf dem Rechner liegt, weiß allein der Client, und er weigert sich, etwas
außerhalb davon zu öffnen. Nicht abgeholte Aufträge verfallen nach fünf Minuten,
damit ein Rechner nach einer Nacht im Ruhezustand nicht zwanzig Fenster aufreißt.

## Wo liegt was?

| | Windows | macOS | Linux |
|---|---|---|---|
| Konfiguration | `%APPDATA%\filetree_coop\client.json` | `~/Library/Application Support/filetree_coop/` | `~/.config/filetree_coop/` |
| Protokoll | `client.log` daneben (rotierend, 4 × 1 MB) | ebenso | ebenso |
| Autostart | `HKCU\…\CurrentVersion\Run` | `~/Library/LaunchAgents` | `~/.config/autostart` |
| Startskript | `client/run_client.pyw` | ebenso | ebenso |

Die Konfigurationsdatei enthält den Gerätetoken und wird unter Unix auf `0600`
gesetzt; unter Windows erbt sie die Rechte des Benutzerprofils.

## Als eigenständige `.exe` verpacken

```bash
pip install pyinstaller
cd client
pyinstaller --noconsole --onefile --name filetree-coop-client ^
            --hidden-import pystray._win32 ftc_client/__main__.py
```

Die fertige Datei liegt in `dist/`. Der Autostart erkennt eine gepackte Version
(`sys.frozen`) und trägt dann direkt die `.exe` ein – ohne Abhängigkeit von
Python-Installation oder venv.

## Tests

```bash
cd client && pytest
```

Eigener Testlauf, unabhängig vom Server (`pytest -q` im Wurzelverzeichnis meint
weiterhin nur die Server-Tests). Ohne installierte Client-Abhängigkeiten werden
sie übersprungen statt zu scheitern.

## Bekannte Grenzen

- **macOS**: `pystray` verlangt dort den Haupt-Thread, den hier `tkinter`
  belegt. Der Client läuft, das Menü im Infobereich kann sich aber
  eigenwillig verhalten; unter Windows und Linux ist das unproblematisch.
- Ein **neu aufgetauchter Ordner** (Kopieren, Entpacken) stößt sicherheitshalber
  einen Voll-Scan an, weil `watchdog` bei Massenereignissen nicht garantiert
  jedes Kind meldet.
- Auf **Netzlaufwerken** ist die Live-Überwachung je nach Protokoll unzuverlässig
  – dort trägt der turnusmäßige Voll-Scan die Hauptlast. Ist die Wurzel nicht
  erreichbar, bricht der Scan ab, *ohne* den Index anzufassen (statt alles als
  „verschwunden“ zu markieren).
