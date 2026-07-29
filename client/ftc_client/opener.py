"""Einen Ordner im Dateimanager des Systems öffnen.

Genau das, was der Browser nicht darf (Sandbox) und weshalb es diesen Befehl
gibt: In Baum und Suche steht an jeder Zeile ein 📂, der Server legt einen
Auftrag in die Queue, und hier wird er ausgeführt.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def reveal(path: Path, is_dir: bool = True) -> None:
    """Öffnet ``path`` – bei einer Datei deren Ordner, mit der Datei markiert.

    Wirft ``FileNotFoundError``, wenn der Pfad nicht (mehr) existiert: Der Index
    kann veraltet sein, und ein stiller Fehlschlag wäre für den Nutzer am
    Browser nicht von „passiert nichts“ zu unterscheiden.
    """
    if not path.exists():
        raise FileNotFoundError(f"Pfad existiert nicht: {path}")

    if sys.platform == "win32":
        if is_dir:
            # explorer liefert auch im Erfolgsfall Exitcode 1 – deshalb kein check.
            subprocess.run(["explorer", str(path)], check=False)
        else:
            subprocess.run(["explorer", f"/select,{path}"], check=False)
    elif sys.platform == "darwin":
        args = ["open", str(path)] if is_dir else ["open", "-R", str(path)]
        subprocess.run(args, check=False)
    else:
        # Linux: xdg-open kennt kein „markieren“, also den Ordner öffnen.
        target = path if is_dir else path.parent
        subprocess.run(["xdg-open", str(target)], check=False)
