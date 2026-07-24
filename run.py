"""Startpunkt: startet den Entwicklungs-Server.

    python run.py            # http://127.0.0.1:8000
"""

from __future__ import annotations

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
