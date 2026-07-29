"""Startskript für den Autostart – bewusst mit der Endung ``.pyw``.

Warum es das gibt: Der Autostart-Eintrag kennt kein Arbeitsverzeichnis. Ein
``pythonw -m ftc_client`` fände sein Paket deshalb nur, wenn man es über einen
``cmd``-Umweg mit gesetztem Verzeichnis startet – und dieses ``cmd`` blitzt bei
jeder Anmeldung als Konsolenfenster auf. Dieses Skript kennt seinen eigenen Ort
und braucht den Umweg nicht.

Es lässt sich auch direkt per Doppelklick starten: ``.pyw`` ist unter Windows
mit ``pythonw.exe`` verknüpft, also ohne Konsolenfenster.
"""

import sys
from pathlib import Path

# Das Verzeichnis dieser Datei ist das ``client/``-Verzeichnis; darunter liegt
# das Paket ``ftc_client``.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ftc_client.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
