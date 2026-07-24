"""Tests für Fälligkeitsdaten an Annotationen: setzen, filtern, sortieren."""

from __future__ import annotations

import uuid


def _source_with_entries(client, names):
    sid = client.post("/api/sources", json={"label": "Q", "kind": "local"}).json()["id"]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": n, "name": n} for n in names],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    entries = client.get(f"/api/sources/{sid}/entries").json()
    return sid, [e["entry_id"] for e in entries]


def _todo(client, entry_id, body, due=None):
    return client.post("/api/annotations", json={
        "entry_id": entry_id, "type": "todo", "body": body, "due_date": due,
    }).json()


def test_create_todo_with_due_date(client):
    _, (eid,) = _source_with_entries(client, ["a.txt"])
    ann = _todo(client, eid, "Rechnung prüfen", "2026-08-15")
    assert ann["due_date"] == "2026-08-15"
    assert client.get(f"/api/annotations/by-entry/{eid}").json()[0]["due_date"] == "2026-08-15"


def test_due_date_defaults_to_none(client):
    _, (eid,) = _source_with_entries(client, ["a.txt"])
    assert _todo(client, eid, "ohne Termin")["due_date"] is None


def test_patch_sets_and_clears_due_date(client):
    _, (eid,) = _source_with_entries(client, ["a.txt"])
    ann = _todo(client, eid, "Termin folgt")

    updated = client.patch(f"/api/annotations/{ann['id']}",
                           json={"due_date": "2026-09-01"}).json()
    assert updated["due_date"] == "2026-09-01"

    # Explizites null löscht den Termin …
    cleared = client.patch(f"/api/annotations/{ann['id']}",
                           json={"due_date": None}).json()
    assert cleared["due_date"] is None

    # … ein Patch ohne das Feld lässt ihn dagegen unangetastet.
    client.patch(f"/api/annotations/{ann['id']}", json={"due_date": "2026-09-01"})
    untouched = client.patch(f"/api/annotations/{ann['id']}",
                             json={"done": True}).json()
    assert untouched["due_date"] == "2026-09-01"


def test_filter_by_due_range(client):
    _, eids = _source_with_entries(client, ["a.txt", "b.txt", "c.txt"])
    _todo(client, eids[0], "früh", "2026-07-01")
    _todo(client, eids[1], "mittig", "2026-07-15")
    _todo(client, eids[2], "spät", "2026-08-20")

    items = client.get("/api/annotations",
                       params={"due_from": "2026-07-01", "due_to": "2026-07-31"}).json()
    assert {i["body"] for i in items} == {"früh", "mittig"}


def test_filter_has_due(client):
    _, eids = _source_with_entries(client, ["a.txt", "b.txt"])
    _todo(client, eids[0], "mit Termin", "2026-07-10")
    _todo(client, eids[1], "ohne Termin")

    with_due = client.get("/api/annotations", params={"has_due": True}).json()
    assert [i["body"] for i in with_due] == ["mit Termin"]

    without = client.get("/api/annotations", params={"has_due": False}).json()
    assert [i["body"] for i in without] == ["ohne Termin"]


def test_order_by_due_puts_undated_last(client):
    _, eids = _source_with_entries(client, ["a.txt", "b.txt", "c.txt"])
    _todo(client, eids[0], "spät", "2026-12-24")
    _todo(client, eids[1], "ohne")
    _todo(client, eids[2], "früh", "2026-07-02")

    items = client.get("/api/annotations", params={"order": "due"}).json()
    assert [i["body"] for i in items] == ["früh", "spät", "ohne"]


def test_calendar_page_reachable(client):
    assert client.get("/calendar").status_code == 200
