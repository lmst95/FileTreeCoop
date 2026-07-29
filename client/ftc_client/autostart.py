"""Autostart nach der Benutzeranmeldung – je System auf dem üblichen Weg.

- **Windows**: Wert unter ``HKCU\\…\\CurrentVersion\\Run``. Bewusst der
  Benutzer-Zweig (HKCU) und nicht HKLM: der Client läuft im Kontext genau dieses
  Nutzers, kennt nur dessen Ordner und braucht keine Administratorrechte.
- **macOS**: ``LaunchAgent``-plist in ``~/Library/LaunchAgents``.
- **Linux**: ``.desktop``-Datei in ``~/.config/autostart``.

Gestartet wird nach Möglichkeit ohne Konsolenfenster (``pythonw`` unter Windows);
läuft der Client als gepackte ``.exe``, wird schlicht diese eingetragen.
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from . import APP_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENTRY_NAME = "filetree_coop Client"


def launch_command() -> list[str]:
    """Wie dieser Client zu starten ist – als Argumentliste.

    Zwei Feinheiten, die beide dasselbe Ziel haben (still starten, ohne dass
    irgendwo ein Fenster aufblitzt):

    - ``sys.executable`` wird eingefroren. Wer den Client aus einem venv heraus
      einrichtet, bekommt genau dessen Interpreter eingetragen – sonst startete
      der Autostart ein Python ohne die nötigen Pakete.
    - Gestartet wird ``run_client.pyw`` statt ``-m ftc_client``. Der Autostart
      kennt kein Arbeitsverzeichnis; ``-m`` bräuchte deshalb einen
      ``cmd``-Umweg, und *der* blitzt bei jeder Anmeldung kurz als Konsole auf.
      Das Skript kennt seinen eigenen Ort und kommt ohne aus.
    """
    # Gepackt (PyInstaller): sys.frozen gesetzt, sys.executable IST der Client.
    if getattr(sys, "frozen", False):
        return [sys.executable]
    exe = Path(sys.executable)
    if sys.platform == "win32":
        # pythonw.exe startet ohne Konsolenfenster – für ein Tray-Programm das
        # einzig Sinnvolle.
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            exe = pythonw
    launcher = Path(__file__).resolve().parent.parent / "run_client.pyw"
    if launcher.exists():
        return [str(exe), str(launcher)]
    # Notnagel, falls jemand nur das Paket kopiert hat: dann muss das
    # Arbeitsverzeichnis stimmen (siehe _win_set).
    return [str(exe), "-m", "ftc_client"]


def needs_working_dir() -> bool:
    """True, wenn der Startbefehl auf das richtige Arbeitsverzeichnis angewiesen
    ist – also nur beim ``-m``-Notnagel."""
    return "-m" in launch_command()


def _quoted_command() -> str:
    parts = launch_command()
    if sys.platform == "win32":
        return " ".join(f'"{p}"' if " " in p else p for p in parts)
    return shlex.join(parts)


def _working_dir() -> str:
    """Wo der Client startet – nötig, damit ``-m ftc_client`` sich findet."""
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).parent)
    # Elternverzeichnis des Pakets = das ``client/``-Verzeichnis.
    return str(Path(__file__).resolve().parent.parent)


# --- Windows ----------------------------------------------------------------

def _win_set(enabled: bool) -> None:
    import winreg

    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ
    ) as key:
        if enabled:
            value = _quoted_command()
            if needs_working_dir():
                # Nur noch der Notnagel-Fall: ``-m`` braucht das richtige
                # Arbeitsverzeichnis, das der Run-Key nicht kennt – dafür der
                # cmd-Umweg (der leider kurz aufblitzt).
                value = f'cmd /c start "" /d "{_working_dir()}" {value}'
            winreg.SetValueEx(key, ENTRY_NAME, 0, winreg.REG_SZ, value)
        else:
            try:
                winreg.DeleteValue(key, ENTRY_NAME)
            except FileNotFoundError:
                pass


def _win_get() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, ENTRY_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


# --- macOS ------------------------------------------------------------------

def _mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"de.{APP_NAME}.client.plist"


def _mac_set(enabled: bool) -> None:
    path = _mac_plist_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    args = "".join(f"    <string>{p}</string>\n" for p in launch_command())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        f"  <key>Label</key><string>de.{APP_NAME}.client</string>\n"
        f"  <key>ProgramArguments</key><array>\n{args}  </array>\n"
        f"  <key>WorkingDirectory</key><string>{_working_dir()}</string>\n"
        "  <key>RunAtLoad</key><true/>\n"
        "</dict></plist>\n",
        encoding="utf-8",
    )


# --- Linux ------------------------------------------------------------------

def _linux_desktop_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "autostart" / f"{APP_NAME}-client.desktop"


def _linux_set(enabled: bool) -> None:
    path = _linux_desktop_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={ENTRY_NAME}\n"
        f"Exec={_quoted_command()}\n"
        f"Path={_working_dir()}\n"
        "X-GNOME-Autostart-enabled=true\n"
        "Terminal=false\n",
        encoding="utf-8",
    )


# --- Öffentliche API ---------------------------------------------------------

def is_enabled() -> bool:
    try:
        if sys.platform == "win32":
            return _win_get()
        if sys.platform == "darwin":
            return _mac_plist_path().exists()
        return _linux_desktop_path().exists()
    except OSError:
        return False


def set_enabled(enabled: bool) -> None:
    """Autostart ein-/ausschalten. Wirft bei Fehlern (das UI zeigt sie an)."""
    if sys.platform == "win32":
        _win_set(enabled)
    elif sys.platform == "darwin":
        _mac_set(enabled)
    else:
        _linux_set(enabled)
