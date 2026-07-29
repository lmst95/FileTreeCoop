"""Einstellungsfenster (tkinter) – erreichbar über das Taskleisten-Symbol.

Drei Reiter:

- **Server** – Adresse, Anmeldung, Gerätename. Die Anmeldung passiert genau
  einmal: Konto-Daten gehen gegen einen Gerätetoken raus, das Passwort wird
  nicht gespeichert.
- **Ordner** – je Quelle der lokale Ordner und die Schalter dazu. Sync und
  Inhalts-Hashes sind getrennt schaltbar, weil sie ganz unterschiedlich teuer
  sind: den Index aktuell halten kostet fast nichts, Hashen liest jede Datei.
- **Allgemein** – Autostart und Protokoll.

Netzaufrufe laufen in Hintergrund-Threads; Ergebnisse werden über ``after``
zurück in den Tk-Thread gereicht, weil tkinter nur von dort bedient werden darf.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__, autostart
from .agent import Agent
from .api import Api, ApiError
from .config import FolderConfig, log_path

log = logging.getLogger(__name__)


class SettingsWindow:
    """Ein Fenster; erneutes Öffnen holt das bestehende nach vorn."""

    def __init__(self, root: tk.Tk, agent: Agent):
        self.root = root
        self.agent = agent
        self.win: tk.Toplevel | None = None
        self.sources: list[dict] = []

    # --- Öffnen / Schließen -------------------------------------------------

    def show(self) -> None:
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return
        self._build()

    def _close(self) -> None:
        if self.win is not None:
            self.win.destroy()
            self.win = None

    # --- Aufbau -------------------------------------------------------------

    def _build(self) -> None:
        win = tk.Toplevel(self.root)
        self.win = win
        win.title("filetree_coop – Einstellungen")
        win.geometry("720x560")
        win.minsize(620, 480)
        win.protocol("WM_DELETE_WINDOW", self._close)

        notebook = ttk.Notebook(win)
        notebook.pack(fill="both", expand=True, padx=10, pady=10)
        self._build_server_tab(notebook)
        self._build_folders_tab(notebook)
        self._build_general_tab(notebook)

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        self.status_var = tk.StringVar(value=self.agent.status_text())
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        ttk.Button(bar, text="Schließen", command=self._close).pack(side="right")
        self._tick_status()

        # Ist schon alles eingerichtet, ist der Ordner-Reiter der interessante.
        if self.agent.config.is_connected():
            notebook.select(1)
            self._refresh_sources_async()

    def _tick_status(self) -> None:
        if self.win is None or not self.win.winfo_exists():
            return
        self.status_var.set(self.agent.status_text())
        self.win.after(1000, self._tick_status)

    # --- Reiter „Server“ ----------------------------------------------------

    def _build_server_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=14)
        notebook.add(frame, text="Server")
        cfg = self.agent.config

        ttk.Label(
            frame,
            text="Adresse des filetree_coop-Servers, z. B. https://filetree.example.org",
            wraplength=620,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.url_var = tk.StringVar(value=cfg.server_url)
        self.ident_var = tk.StringVar(value="")
        self.pass_var = tk.StringVar(value="")
        self.name_var = tk.StringVar(value=cfg.client_name)

        rows = [
            ("Serveradresse", self.url_var, False),
            ("E-Mail oder Username", self.ident_var, False),
            ("Passwort", self.pass_var, True),
            ("Name dieses Geräts", self.name_var, False),
        ]
        for i, (label, var, secret) in enumerate(rows, start=1):
            ttk.Label(frame, text=label).grid(row=i, column=0, sticky="w", pady=4)
            entry = ttk.Entry(frame, textvariable=var, width=46, show="•" if secret else "")
            entry.grid(row=i, column=1, sticky="we", pady=4, padx=(8, 0))
        frame.columnconfigure(1, weight=1)

        self.connect_btn = ttk.Button(frame, text="Verbinden", command=self._connect)
        self.connect_btn.grid(row=5, column=1, sticky="w", pady=(10, 4), padx=(8, 0))

        self.conn_status = tk.StringVar()
        ttk.Label(frame, textvariable=self.conn_status, wraplength=620).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(6, 0)
        )
        self._update_conn_status()

        ttk.Label(
            frame,
            text=(
                "Das Passwort wird nur einmal für die Anmeldung verwendet und "
                "nicht gespeichert – auf diesem Rechner liegt danach nur ein "
                "Gerätetoken, der sich in der Weboberfläche unter „Geräte“ "
                "jederzeit einzeln widerrufen lässt."
            ),
            wraplength=620,
            foreground="#6e6e73",
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(14, 0))

    def _update_conn_status(self) -> None:
        cfg = self.agent.config
        if cfg.is_connected():
            who = f" als {cfg.user_display}" if cfg.user_display else ""
            self.conn_status.set(f"✓ Verbunden mit {cfg.server_url}{who}.")
        else:
            self.conn_status.set("Noch nicht verbunden.")

    def _connect(self) -> None:
        url = self.url_var.get().strip().rstrip("/")
        ident = self.ident_var.get().strip()
        password = self.pass_var.get()
        name = self.name_var.get().strip() or "Desktop-Client"
        if not url or not ident or not password:
            messagebox.showwarning(
                "Angaben fehlen",
                "Serveradresse, Anmeldename und Passwort werden gebraucht.",
                parent=self.win,
            )
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
            self.url_var.set(url)

        self.connect_btn.state(["disabled"])
        self.conn_status.set("Verbinde …")

        def work() -> None:
            api = Api(url)
            try:
                data = api.register(ident, password, name)
            except ApiError as e:
                self._ui(lambda: self._connect_failed(str(e)))
                return
            self._ui(lambda: self._connect_ok(url, name, data))

        threading.Thread(target=work, daemon=True).start()

    def _connect_failed(self, message: str) -> None:
        self.connect_btn.state(["!disabled"])
        self.conn_status.set(f"✗ {message}")

    def _connect_ok(self, url: str, name: str, data: dict) -> None:
        cfg = self.agent.config
        cfg.server_url = url
        cfg.token = data["token"]
        cfg.client_id = data["client_id"]
        cfg.client_name = name
        cfg.user_display = (data.get("user") or {}).get("display_name", "")
        cfg.show_settings_on_start = False
        self.agent.save_config()
        self.agent.reload()
        # Passwort sofort aus dem Formular entfernen – es wird nicht mehr gebraucht.
        self.pass_var.set("")
        self.connect_btn.state(["!disabled"])
        self._update_conn_status()
        self._refresh_sources_async()

    # --- Reiter „Ordner“ ----------------------------------------------------

    def _build_folders_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=14)
        notebook.add(frame, text="Ordner")

        head = ttk.Frame(frame)
        head.pack(fill="x")
        ttk.Label(
            head,
            text="Welche Quelle liegt auf diesem Rechner wo? Sync und Hashes je Quelle einzeln schaltbar.",
            wraplength=560,
        ).pack(side="left")
        ttk.Button(head, text="Quellen laden", command=self._refresh_sources_async).pack(
            side="right"
        )

        # Scrollbarer Bereich – es können beliebig viele Quellen sein.
        canvas = tk.Canvas(frame, highlightthickness=0)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        self.folders_frame = ttk.Frame(canvas)
        self.folders_frame.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        window = canvas.create_window((0, 0), window=self.folders_frame, anchor="nw")
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(window, width=e.width)
        )
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True, pady=(10, 0))
        scroll.pack(side="right", fill="y", pady=(10, 0))

        self._render_folders()

    def _refresh_sources_async(self) -> None:
        if not self.agent.config.is_connected():
            return

        def work() -> None:
            try:
                sources = self.agent.api.list_sources()
            except ApiError as e:
                self._ui(lambda: self._sources_failed(str(e)))
                return
            self._ui(lambda: self._sources_loaded(sources))

        threading.Thread(target=work, daemon=True).start()

    def _sources_failed(self, message: str) -> None:
        messagebox.showerror("Quellen laden", message, parent=self.win)

    def _sources_loaded(self, sources: list[dict]) -> None:
        # Nur eigene Quellen: geteilte gehören einem anderen Rechner.
        self.sources = sources
        # Bezeichnungen in der lokalen Konfiguration nachziehen.
        by_id = {s["id"]: s for s in sources}
        for folder in self.agent.config.folders:
            src = by_id.get(folder.source_id)
            if src:
                folder.source_label = src["label"]
        self.agent.save_config()
        self._render_folders()

    def _render_folders(self) -> None:
        for child in self.folders_frame.winfo_children():
            child.destroy()

        cfg = self.agent.config
        if not cfg.is_connected():
            ttk.Label(
                self.folders_frame,
                text="Zuerst im Reiter „Server“ verbinden.",
            ).pack(anchor="w", pady=8)
            return

        configured = {f.source_id for f in cfg.folders}
        if not self.sources and not cfg.folders:
            ttk.Label(
                self.folders_frame,
                text="Keine Quellen gefunden. Lege im Browser eine an – oder unten eine neue.",
                wraplength=560,
            ).pack(anchor="w", pady=8)

        for folder in cfg.folders:
            self._render_folder_row(folder)

        # Quellen ohne Ordner auf diesem Gerät: mit einem Klick einrichten.
        missing = [s for s in self.sources if s["id"] not in configured]
        if missing:
            ttk.Separator(self.folders_frame).pack(fill="x", pady=8)
            ttk.Label(
                self.folders_frame,
                text="Noch nicht auf diesem Gerät eingerichtet:",
            ).pack(anchor="w")
            for src in missing:
                row = ttk.Frame(self.folders_frame)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=f"{src['label']} ({src['kind']})").pack(side="left")
                ttk.Button(
                    row,
                    text="Ordner wählen …",
                    command=lambda s=src: self._add_folder(s),
                ).pack(side="right")

        ttk.Separator(self.folders_frame).pack(fill="x", pady=8)
        ttk.Button(
            self.folders_frame,
            text="+ Neue Quelle anlegen und Ordner wählen …",
            command=self._create_source,
        ).pack(anchor="w")

    def _render_folder_row(self, folder: FolderConfig) -> None:
        box = ttk.LabelFrame(
            self.folders_frame,
            text=folder.source_label or f"Quelle {folder.source_id}",
            padding=8,
        )
        box.pack(fill="x", pady=5)

        path_row = ttk.Frame(box)
        path_row.pack(fill="x")
        path_var = tk.StringVar(value=folder.local_path)
        entry = ttk.Entry(path_row, textvariable=path_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.state(["readonly"])
        ttk.Button(
            path_row,
            text="ändern …",
            command=lambda: self._choose_path(folder, path_var),
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            path_row, text="entfernen", command=lambda: self._remove_folder(folder)
        ).pack(side="left", padx=(6, 0))

        opts = ttk.Frame(box)
        opts.pack(fill="x", pady=(6, 0))

        enabled = tk.BooleanVar(value=folder.enabled)
        watch = tk.BooleanVar(value=folder.watch_enabled)
        hashes = tk.BooleanVar(value=folder.hash_enabled)
        interval = tk.StringVar(value=str(folder.scan_interval_minutes))
        settle = tk.StringVar(value=str(folder.settle_seconds))

        def apply() -> None:
            folder.enabled = enabled.get()
            folder.watch_enabled = watch.get()
            folder.hash_enabled = hashes.get()
            folder.scan_interval_minutes = _as_int(interval.get(), 60, 1, 10080)
            folder.settle_seconds = _as_int(settle.get(), 10, 0, 3600)
            interval.set(str(folder.scan_interval_minutes))
            settle.set(str(folder.settle_seconds))
            self._apply_changes()

        ttk.Checkbutton(
            opts, text="Sync aktiv", variable=enabled, command=apply
        ).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Checkbutton(
            opts, text="Live-Überwachung", variable=watch, command=apply
        ).grid(row=0, column=1, sticky="w", padx=(0, 14))
        ttk.Checkbutton(
            opts, text="Inhalts-Hashes (SHA-256)", variable=hashes, command=apply
        ).grid(row=0, column=2, sticky="w")

        timing = ttk.Frame(box)
        timing.pack(fill="x", pady=(6, 0))
        ttk.Label(timing, text="Voll-Scan alle").pack(side="left")
        sp1 = ttk.Spinbox(timing, from_=1, to=10080, width=6, textvariable=interval, command=apply)
        sp1.pack(side="left", padx=4)
        sp1.bind("<FocusOut>", lambda _e: apply())
        ttk.Label(timing, text="Minuten · Änderung melden nach").pack(side="left")
        sp2 = ttk.Spinbox(timing, from_=0, to=3600, width=5, textvariable=settle, command=apply)
        sp2.pack(side="left", padx=4)
        sp2.bind("<FocusOut>", lambda _e: apply())
        ttk.Label(timing, text="Sekunden Ruhe").pack(side="left")

        ttk.Label(
            box,
            text=(
                "Die Ruhezeit verhindert, dass gerade erst geschriebene oder nur "
                "kurz existierende Dateien im Index landen."
            ),
            wraplength=560,
            foreground="#6e6e73",
        ).pack(anchor="w", pady=(4, 0))

        if folder.last_error:
            ttk.Label(box, text=f"⚠ {folder.last_error}", foreground="#c77700",
                      wraplength=560).pack(anchor="w", pady=(4, 0))

    def _choose_path(self, folder: FolderConfig, path_var: tk.StringVar) -> None:
        chosen = filedialog.askdirectory(
            parent=self.win,
            title=f"Ordner für „{folder.source_label}“",
            initialdir=folder.local_path or str(Path.home()),
        )
        if not chosen:
            return
        folder.local_path = str(Path(chosen))
        folder.last_error = ""
        path_var.set(folder.local_path)
        self._apply_changes()

    def _add_folder(self, source: dict) -> None:
        chosen = filedialog.askdirectory(
            parent=self.win,
            title=f"Ordner für „{source['label']}“",
            initialdir=str(Path.home()),
        )
        if not chosen:
            return
        self.agent.config.folders.append(
            FolderConfig(
                source_id=source["id"],
                source_label=source["label"],
                local_path=str(Path(chosen)),
            )
        )
        self._apply_changes()
        self._render_folders()

    def _remove_folder(self, folder: FolderConfig) -> None:
        if not messagebox.askyesno(
            "Ordner entfernen",
            f"„{folder.source_label}“ auf diesem Gerät nicht mehr synchronisieren?\n\n"
            "Der Index auf dem Server bleibt erhalten.",
            parent=self.win,
        ):
            return
        self.agent.config.folders = [
            f for f in self.agent.config.folders if f.source_id != folder.source_id
        ]
        self._apply_changes()
        self._render_folders()

    def _create_source(self) -> None:
        chosen = filedialog.askdirectory(
            parent=self.win, title="Ordner wählen", initialdir=str(Path.home())
        )
        if not chosen:
            return
        path = Path(chosen)
        label = _ask_text(self.win, "Neue Quelle", "Bezeichnung der Quelle:", path.name)
        if not label:
            return

        def work() -> None:
            try:
                src = self.agent.api.create_source(
                    label, "local", self.agent.config.client_name
                )
            except ApiError as e:
                self._ui(lambda: messagebox.showerror("Quelle anlegen", str(e), parent=self.win))
                return
            self._ui(lambda: self._source_created(src, path))

        threading.Thread(target=work, daemon=True).start()

    def _source_created(self, source: dict, path: Path) -> None:
        self.agent.config.folders.append(
            FolderConfig(
                source_id=source["id"],
                source_label=source["label"],
                local_path=str(path),
            )
        )
        self._apply_changes()
        self._refresh_sources_async()
        self._render_folders()

    # --- Reiter „Allgemein“ -------------------------------------------------

    def _build_general_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook, padding=14)
        notebook.add(frame, text="Allgemein")

        self.autostart_var = tk.BooleanVar(value=autostart.is_enabled())
        ttk.Checkbutton(
            frame,
            text="Nach der Anmeldung automatisch starten",
            variable=self.autostart_var,
            command=self._toggle_autostart,
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "Der Client startet dann still im Hintergrund, sobald du dich an "
                "diesem Rechner anmeldest, und erscheint als Symbol unten rechts "
                "in der Taskleiste."
            ),
            wraplength=620,
            foreground="#6e6e73",
        ).pack(anchor="w", pady=(2, 6))

        # Der eingetragene Befehl steht hier offen – er friert den *aktuellen*
        # Interpreter ein (also das venv, aus dem heraus eingerichtet wird), und
        # absolute Pfade brechen, wenn Projekt oder venv später umziehen. Beides
        # sieht man nur, wenn man es sieht.
        cmd_box = tk.Text(frame, height=2, wrap="word", relief="flat", borderwidth=0)
        cmd_box.insert("1.0", " ".join(autostart.launch_command()))
        cmd_box.configure(state="disabled", background=frame.winfo_toplevel().cget("background"))
        cmd_box.pack(fill="x", pady=(0, 2))
        ttk.Label(
            frame,
            text=(
                "Genau dieser Befehl wird eingetragen. Ziehen Projekt oder "
                "virtuelle Umgebung später um, hier einmal aus- und wieder "
                "einschalten."
            ),
            wraplength=620,
            foreground="#6e6e73",
        ).pack(anchor="w", pady=(0, 14))

        pause_var = tk.BooleanVar(value=self.agent.paused)
        ttk.Checkbutton(
            frame,
            text="Sync pausieren",
            variable=pause_var,
            command=lambda: self.agent.set_paused(pause_var.get()),
        ).pack(anchor="w", pady=(0, 14))

        row = ttk.Frame(frame)
        row.pack(anchor="w", pady=(0, 8))
        ttk.Button(row, text="Protokoll öffnen", command=self._open_log).pack(side="left")
        ttk.Button(
            row, text="Weboberfläche öffnen", command=self._open_web
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            row, text="Abmelden …", command=self._disconnect
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            frame,
            text=f"filetree_coop Desktop-Client, Version {__version__}",
            foreground="#6e6e73",
        ).pack(anchor="w", pady=(20, 0))

    def _toggle_autostart(self) -> None:
        want = self.autostart_var.get()
        try:
            autostart.set_enabled(want)
        except Exception as e:
            self.autostart_var.set(not want)
            messagebox.showerror("Autostart", f"Hat nicht geklappt: {e}", parent=self.win)
            return
        self.agent.config.autostart = want
        self.agent.save_config()

    def _open_log(self) -> None:
        path = log_path()
        if not path.exists():
            messagebox.showinfo("Protokoll", "Noch kein Protokoll vorhanden.", parent=self.win)
            return
        webbrowser.open(path.as_uri())

    def _open_web(self) -> None:
        if self.agent.config.server_url:
            webbrowser.open(self.agent.config.server_url)

    def _disconnect(self) -> None:
        if not messagebox.askyesno(
            "Abmelden",
            "Verbindung zum Server trennen? Der Gerätetoken wird verworfen und "
            "das Gerät auf dem Server entfernt.",
            parent=self.win,
        ):
            return
        try:
            self.agent.api.unregister()
        except ApiError as e:
            log.info("Abmelden am Server fehlgeschlagen (lokal trotzdem): %s", e)
        cfg = self.agent.config
        cfg.token = ""
        cfg.client_id = None
        cfg.user_display = ""
        self.agent.save_config()
        self.agent.reload()
        self._update_conn_status()
        self._render_folders()

    # --- Hilfen -------------------------------------------------------------

    def _apply_changes(self) -> None:
        """Konfiguration sichern und die Worker neu aufsetzen."""
        self.agent.save_config()
        self.agent.reload()

    def _ui(self, fn) -> None:
        """Etwas im Tk-Thread ausführen (aus einem Worker heraus aufgerufen)."""
        try:
            self.root.after(0, fn)
        except RuntimeError:
            pass  # Fenster bereits zu


def _as_int(value: str, fallback: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(str(value).strip())))
    except (TypeError, ValueError):
        return fallback


def _ask_text(parent, title: str, prompt: str, initial: str = "") -> str | None:
    """Kleiner Eingabedialog (tkinter.simpledialog zieht unter Windows
    gelegentlich ein zweites Fenster hoch – deshalb selbst gebaut)."""
    dialog = tk.Toplevel(parent)
    dialog.title(title)
    dialog.transient(parent)
    dialog.resizable(False, False)
    ttk.Label(dialog, text=prompt).pack(padx=14, pady=(14, 4), anchor="w")
    var = tk.StringVar(value=initial)
    entry = ttk.Entry(dialog, textvariable=var, width=40)
    entry.pack(padx=14, fill="x")
    entry.focus_set()
    entry.select_range(0, "end")

    result: dict[str, str | None] = {"value": None}

    def ok() -> None:
        result["value"] = var.get().strip() or None
        dialog.destroy()

    bar = ttk.Frame(dialog)
    bar.pack(padx=14, pady=12, anchor="e")
    ttk.Button(bar, text="Abbrechen", command=dialog.destroy).pack(side="right")
    ttk.Button(bar, text="OK", command=ok).pack(side="right", padx=(0, 6))
    dialog.bind("<Return>", lambda _e: ok())
    dialog.bind("<Escape>", lambda _e: dialog.destroy())
    dialog.grab_set()
    parent.wait_window(dialog)
    return result["value"]
