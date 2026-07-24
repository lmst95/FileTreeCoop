"""Tests für Freigaben (source_shares), Mitglieder und Übergaben (handover)."""

from __future__ import annotations

import uuid


def _me(c):
    return c.get("/api/auth/me").json()


def _source_with_entry(client):
    sid = client.post("/api/sources", json={"label": "Q", "kind": "local"}).json()["id"]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": [{"path": "f.txt", "name": "f.txt", "ext": "txt"}],
              "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    entry = client.get(f"/api/sources/{sid}/entries").json()[0]
    return sid, entry["entry_id"]


def _share(client, sid, email, permission="annotate"):
    return client.post(
        f"/api/sources/{sid}/shares", json={"email": email, "permission": permission}
    )


def test_share_grants_visibility_and_search(client, second_client):
    sid, _ = _source_with_entry(client)
    colleague = _me(second_client)

    # Vorher sieht der Kollege die Quelle nicht.
    assert second_client.get("/api/sources").json() == []

    r = _share(client, sid, colleague["email"], "annotate")
    assert r.status_code == 201, r.text

    # Jetzt taucht sie auf und ist durchsuchbar.
    visible = [s["id"] for s in second_client.get("/api/sources").json()]
    assert sid in visible
    hits = second_client.get("/api/search", params={"q": "f.txt"}).json()
    assert any(h["source_id"] == sid for h in hits)


def test_read_permission_blocks_annotating(client, second_client):
    sid, eid = _source_with_entry(client)
    colleague = _me(second_client)
    _share(client, sid, colleague["email"], "read")

    # Nur-Lese-Freigabe: Annotieren verboten.
    r = second_client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "geht nicht"})
    assert r.status_code == 403


def test_annotate_permission_allows_annotating(client, second_client):
    sid, eid = _source_with_entry(client)
    colleague = _me(second_client)
    _share(client, sid, colleague["email"], "annotate")
    r = second_client.post("/api/annotations", json={
        "entry_id": eid, "type": "note", "body": "geht"})
    assert r.status_code == 201


def test_members_lists_owner_and_shared(client, second_client):
    sid, _ = _source_with_entry(client)
    owner = _me(client)
    colleague = _me(second_client)
    _share(client, sid, colleague["email"], "annotate")

    members = client.get(f"/api/sources/{sid}/members").json()
    ids = {m["id"] for m in members}
    assert owner["id"] in ids and colleague["id"] in ids


def test_handover_sets_assignee_name(client, second_client):
    sid, eid = _source_with_entry(client)
    colleague = _me(second_client)
    _share(client, sid, colleague["email"], "annotate")

    r = client.post("/api/annotations", json={
        "entry_id": eid, "type": "handover", "body": "bitte übernehmen",
        "assignee_user_id": colleague["id"]})
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["assignee_user_id"] == colleague["id"]
    assert data["assignee_name"] == "Kollege"


def test_handover_requires_assignee(client):
    _, eid = _source_with_entry(client)
    r = client.post("/api/annotations", json={"entry_id": eid, "type": "handover"})
    assert r.status_code == 422


def test_handover_to_non_member_rejected(client, second_client):
    sid, eid = _source_with_entry(client)
    colleague = _me(second_client)  # NICHT freigegeben -> kein Mitglied
    r = client.post("/api/annotations", json={
        "entry_id": eid, "type": "handover", "assignee_user_id": colleague["id"]})
    assert r.status_code == 422


def test_unshare_removes_access(client, second_client):
    sid, _ = _source_with_entry(client)
    colleague = _me(second_client)
    _share(client, sid, colleague["email"], "annotate")
    assert sid in [s["id"] for s in second_client.get("/api/sources").json()]

    r = client.delete(f"/api/sources/{sid}/shares/{colleague['id']}")
    assert r.status_code == 204
    assert second_client.get("/api/sources").json() == []


def test_share_unknown_email_becomes_invite(client):
    # Unbekannte E-Mail ist kein Fehler mehr, sondern eine ausstehende
    # Einladung (siehe tests/test_invites.py für den vollen Ablauf).
    sid, _ = _source_with_entry(client)
    r = _share(client, sid, "niemand@example.com")
    assert r.status_code == 201
    assert r.json()["pending"] is True


def test_non_owner_cannot_manage_shares(client, second_client):
    sid, _ = _source_with_entry(client)
    # second_client ist nicht Besitzer.
    assert second_client.get(f"/api/sources/{sid}/shares").status_code == 403
    r = second_client.post(f"/api/sources/{sid}/shares",
                           json={"email": "x@example.com", "permission": "read"})
    assert r.status_code == 403
