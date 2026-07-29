"""Das Symbol für den Infobereich der Taskleiste.

Bewusst im Code gezeichnet statt als mitgelieferte PNG-Datei: so gibt es keine
Binärdatei im Repository, das Symbol skaliert auf jede benötigte Größe, und die
Farbe kann den Zustand zeigen, ohne dass drei Bilder gepflegt werden müssen.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

# Zustandsfarben – dieselbe Sprache wie die Punkte auf der Geräte-Seite.
COLORS = {
    "ok": (48, 164, 108),        # verbunden, alles aktuell
    "busy": (0, 122, 255),       # scannt oder hasht gerade
    "paused": (199, 119, 0),     # pausiert
    "offline": (142, 142, 147),  # kein Kontakt zum Server
    "error": (255, 59, 48),      # Ordner mit Problemen
}


def make_icon(state: str = "offline", size: int = 64) -> Image.Image:
    """Ein Ordner-Symbol in der Farbe des Zustands."""
    color = COLORS.get(state, COLORS["offline"])
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    u = size / 64  # alles relativ zur Kantenlänge, damit jede Größe passt
    # Reiter des Ordners
    draw.rounded_rectangle(
        [(6 * u, 14 * u), (28 * u, 22 * u)], radius=3 * u, fill=color
    )
    # Korpus
    draw.rounded_rectangle(
        [(6 * u, 19 * u), (58 * u, 52 * u)], radius=5 * u, fill=color
    )
    # Heller Streifen als „Klappe“ – macht die Form auch bei 16 px erkennbar.
    draw.rounded_rectangle(
        [(10 * u, 27 * u), (54 * u, 31 * u)],
        radius=2 * u,
        fill=(255, 255, 255, 150),
    )
    return img
