"""Tests für den Übergabe-Workflow (Status, Empfänger-Rechte, Badges)."""

from __future__ import annotations

import uuid


def _setup_shared_entry(client, second_client, permission="annotate"):
    """Quelle + Datei anlegen, an den zweiten Nutzer freigeben.

    Liefert (source_id, entry_id, second_user).
    """
    r = client.post("/api/sources", json={"label": "Team", "kind": "network"})
    sid = r.json()["id"]
    r = client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": "plan.xlsx", "name": "plan.xlsx"}],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    assert r.status_code == 200
    entry = client.get(f"/api/sources/{sid}/entries").json()[0]

    me2 = second_client.get("/api/auth/me").json()
    r = client.post(
        f"/api/sources/{sid}/shares",
        json={"identifier": me2["email"], "permission": permission},
    )
    assert r.status_code == 201, r.text
    return sid, entry["entry_id"], me2


def _handover(client, entry_id, assignee_id, body="bitte prüfen"):
    r = client.post("/api/annotations", json={
        "entry_id": entry_id, "type": "handover",
        "body": body, "assignee_user_id": assignee_id,
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_handover_status_workflow(client, second_client):
    _sid, eid, me2 = _setup_shared_entry(client, second_client)
    ann = _handover(client, eid, me2["id"])
    assert ann["status"] == "open"
    assert ann["author_name"] == "Testnutzer"
    assert ann["assignee_name"] == "Kollege"

    # Empfänger sieht die Übergabe unter „an mich“.
    mine = second_client.get("/api/annotations?type=handover&assignee=me").json()
    assert [a["id"] for a in mine] == [ann["id"]]

    # Annehmen -> angenommen, noch nicht erledigt.
    r = second_client.patch(f"/api/annotations/{ann['id']}", json={"status": "accepted"})
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    assert r.json()["done"] is False

    # Erledigen -> done synchronisiert.
    r = second_client.patch(f"/api/annotations/{ann['id']}", json={"status": "done"})
    assert r.json()["status"] == "done"
    assert r.json()["done"] is True

    # done=False öffnet wieder.
    r = second_client.patch(f"/api/annotations/{ann['id']}", json={"done": False})
    assert r.json()["status"] == "open"


def test_author_filter_lists_my_handovers(client, second_client):
    _sid, eid, me2 = _setup_shared_entry(client, second_client)
    ann = _handover(client, eid, me2["id"])
    von_mir = client.get("/api/annotations?type=handover&author=me").json()
    assert [a["id"] for a in von_mir] == [ann["id"]]


def test_readonly_assignee_may_update_status_but_not_body(client, second_client):
    _sid, eid, me2 = _setup_shared_entry(client, second_client, permission="read")
    ann = _handover(client, eid, me2["id"])

    # Status/Termin dürfen auch mit Nur-Lese-Freigabe geändert werden.
    r = second_client.patch(f"/api/annotations/{ann['id']}", json={"status": "accepted"})
    assert r.status_code == 200

    # Text ändern bleibt verboten.
    r = second_client.patch(f"/api/annotations/{ann['id']}", json={"body": "gekapert"})
    assert r.status_code == 403


def test_invalid_status_rejected(client, second_client):
    _sid, eid, me2 = _setup_shared_entry(client, second_client)
    ann = _handover(client, eid, me2["id"])
    r = client.patch(f"/api/annotations/{ann['id']}", json={"status": "vielleicht"})
    assert r.status_code == 422


def test_notification_counters(client, second_client):
    _sid, eid, me2 = _setup_shared_entry(client, second_client)
    ann = _handover(client, eid, me2["id"])

    n = second_client.get("/api/notifications").json()
    assert n["handovers_open"] == 1
    assert n["handovers_active"] == 1

    second_client.patch(f"/api/annotations/{ann['id']}", json={"status": "accepted"})
    n = second_client.get("/api/notifications").json()
    assert n["handovers_open"] == 0      # angenommen = nicht mehr „neu“
    assert n["handovers_active"] == 1    # aber noch unerledigt

    second_client.patch(f"/api/annotations/{ann['id']}", json={"status": "done"})
    n = second_client.get("/api/notifications").json()
    assert n["handovers_active"] == 0
