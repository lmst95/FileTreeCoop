"""Test-Setup des Clients – bewusst unabhängig vom Server-Testlauf.

Der Client ist ein eigenes Programm mit eigenen Abhängigkeiten (pystray, Pillow,
watchdog). Wer nur den Server betreibt, hat die nicht installiert; deshalb wird
hier sauber übersprungen statt der Testlauf zu scheitern.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ``client/`` auf den Pfad – so läuft ``pytest`` sowohl aus ``client/`` heraus
# als auch aus dem Repo-Wurzelverzeichnis.
CLIENT_DIR = Path(__file__).resolve().parent.parent
if str(CLIENT_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENT_DIR))

pytest.importorskip("pystray", reason="Client-Abhängigkeiten nicht installiert")
pytest.importorskip("PIL", reason="Client-Abhängigkeiten nicht installiert")
