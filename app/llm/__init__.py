"""LLM-Integration: generischer, provider-unabhängiger Kern.

Getrennt in
- ``crypto``     – Verschleierung gespeicherter API-Tokens (nur stdlib),
- ``providers``  – Adapter je Anbieter (chat + Modellliste),
- ``service``    – provider-unabhängige Fassade (``run_completion``, ``list_models``).

Kein Modul hier kennt konkrete Features (Notizen o. Ä.) – die Anbindung
geschieht ausschließlich über den generischen ``/api/llm/run``-Endpunkt.
"""
