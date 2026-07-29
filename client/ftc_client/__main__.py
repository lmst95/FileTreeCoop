"""Einstiegspunkt: ``python -m ftc_client``.

Der Client startet still im Hintergrund und zeigt sich nur als Symbol im
Infobereich der Taskleiste. Beim allerersten Start (noch keine Konfiguration)
öffnet sich das Einstellungsfenster von selbst – sonst wüsste niemand, wohin.

Thread-Aufteilung (siehe auch ``tray.py``): tkinter läuft im Haupt-Thread, das
Taskleisten-Symbol und die Sync-Worker in eigenen Threads.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import tkinter as tk

from . import __version__
from .agent import Agent
from .config import Config, log_path
from .settings_ui import SettingsWindow
from .tray import Tray

log = logging.getLogger("ftc_client")


def setup_logging(verbose: bool = False) -> None:
    log_path().parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        # Rotierend, damit das Protokoll eines dauerlaufenden Programms nicht
        # unbemerkt die Platte füllt.
        logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
    ]
    # Ohne Konsole (pythonw / gepackt) ist stderr None – dann kein StreamHandler.
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )
    # watchdog ist bei DEBUG sehr gesprächig (ein Eintrag je Dateiereignis).
    logging.getLogger("watchdog").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ftc_client", description="filetree_coop Desktop-Client"
    )
    parser.add_argument(
        "--settings", action="store_true", help="Einstellungsfenster beim Start öffnen"
    )
    parser.add_argument("--verbose", action="store_true", help="ausführliches Protokoll")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    log.info("filetree_coop Desktop-Client %s startet", __version__)

    config = Config.load()
    agent = Agent(config)

    root = tk.Tk()
    root.withdraw()  # nur Träger der Tk-Schleife, nie selbst sichtbar
    root.title("filetree_coop")

    settings = SettingsWindow(root, agent)

    def open_settings() -> None:
        # Wird aus dem Tray-Thread aufgerufen -> in den Tk-Thread schicken.
        root.after(0, settings.show)

    def quit_app() -> None:
        log.info("Beenden angefordert")
        agent.stop()
        tray.stop()
        root.after(0, root.quit)

    tray = Tray(agent, open_settings, quit_app)
    # Zustandsänderungen des Agenten färben das Symbol und den Tooltip um.
    agent.on_state_changed = tray.refresh

    agent.start()
    tray.start()

    if args.settings or not config.is_connected() or config.show_settings_on_start:
        open_settings()

    try:
        root.mainloop()
    except KeyboardInterrupt:
        quit_app()
    finally:
        agent.stop()
        tray.stop()
    log.info("Beendet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
