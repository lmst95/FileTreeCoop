"""filetree_coop Desktop-Client.

Ein Hintergrundprogramm, das konfigurierte Ordner überwacht und ihre Metadaten
mit einem filetree_coop-Server abgleicht. Es erscheint als Symbol im
Infobereich der Taskleiste; darüber öffnet sich das Einstellungsfenster.

Übertragen werden – wie beim Browser-Scanner – ausschließlich **Metadaten**
(Pfad, Name, Größe, Änderungsdatum) und auf Wunsch der SHA-256 des Inhalts.
Dateiinhalte verlassen den Rechner nie.
"""

__version__ = "1.0.0"
APP_NAME = "filetree_coop"
