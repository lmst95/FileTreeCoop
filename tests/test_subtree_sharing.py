"""Tests für Teilbaum-Freigaben: nur ein Unterordner wird geteilt."""

from __future__ import annotations

import uuid


def _me(c):
    return c.get("/api/auth/me").json()


def _tree_source(client):
    """Quelle mit verschachteltem Baum anlegen."""
    sid = client.post("/api/sources", json={"label": "Q", "kind": "local"}).json()["id"]
    entries = [
        {"path": "docs", "name": "docs", "is_dir": True},
        {"path": "docs/a.txt", "name": "a.txt"},
        {"path": "docs/sub", "name": "sub", "is_dir": True},
        {"path": "docs/sub/deep.txt", "name": "deep.txt"},
        {"path": "other", "name": "other", "is_dir": True},
        {"path": "other/geheim.txt", "name": "geheim.txt"},
    ]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": entries, "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    return sid


def _owner_entry_id(client, sid, path):
    for h in client.get(f"/api/sources/{sid}/entries").json():
        if h["path"] == path:
            return h["entry_id"]
    raise AssertionError(f"{path} nicht gefunden")


def _share_subtree(owner, sid, email, prefix, permission="annotate"):
    return owner.post(
        f"/api/sources/{sid}/shares",
        json={"email": email, "permission": permission, "path_prefix": prefix},
    )


def _children(c, sid, parent=""):
    r = c.get(f"/api/sources/{sid}/children", params={"parent": parent})
    return r


def test_share_nonexistent_folder_404(client, second_client):
    sid = _tree_source(client)
    r = _share_subtree(client, sid, _me(second_client)["email"], "docs/gibtsnicht")
    assert r.status_code == 404


def test_subtree_visible_as_root(client, second_client):
    sid = _tree_source(client)
    colleague = _me(second_client)
    assert _share_subtree(client, sid, colleague["email"], "docs/sub").status_code == 201

    # Quelle wird sichtbar.
    assert sid in [s["id"] for s in second_client.get("/api/sources").json()]
    # Wurzel des Kollegen = der freigegebene Ordner selbst.
    roots = {h["path"] for h in _children(second_client, sid, "").json()}
    assert roots == {"docs/sub"}
    # Dessen Kinder sind zugänglich.
    kids = {h["path"] for h in _children(second_client, sid, "docs/sub").json()}
    assert kids == {"docs/sub/deep.txt"}


def test_browsing_outside_subtree_forbidden(client, second_client):
    sid = _tree_source(client)
    colleague = _me(second_client)
    _share_subtree(client, sid, colleague["email"], "docs/sub")
    assert _children(second_client, sid, "docs").status_code == 403
    assert _children(second_client, sid, "other").status_code == 403


def test_annotate_only_within_subtree(client, second_client):
    sid = _tree_source(client)
    colleague = _me(second_client)
    _share_subtree(client, sid, colleague["email"], "docs/sub")

    deep_id = _owner_entry_id(client, sid, "docs/sub/deep.txt")
    a_id = _owner_entry_id(client, sid, "docs/a.txt")

    ok = second_client.post("/api/annotations", json={
        "entry_id": deep_id, "type": "note", "body": "im Teilbaum"})
    assert ok.status_code == 201
    forbidden = second_client.post("/api/annotations", json={
        "entry_id": a_id, "type": "note", "body": "verboten"})
    assert forbidden.status_code == 403


def test_readonly_subtree_blocks_annotating(client, second_client):
    sid = _tree_source(client)
    colleague = _me(second_client)
    _share_subtree(client, sid, colleague["email"], "docs/sub", permission="read")
    deep_id = _owner_entry_id(client, sid, "docs/sub/deep.txt")
    r = second_client.post("/api/annotations", json={
        "entry_id": deep_id, "type": "note", "body": "x"})
    assert r.status_code == 403


def test_search_and_overview_limited_to_subtree(client, second_client):
    sid = _tree_source(client)
    colleague = _me(second_client)
    _share_subtree(client, sid, colleague["email"], "docs/sub")

    # Suche: findet deep.txt, aber nichts außerhalb.
    assert len(second_client.get("/api/search", params={"q": "deep"}).json()) == 1
    assert second_client.get("/api/search", params={"q": "geheim"}).json() == []
    assert len(client.get("/api/search", params={"q": "geheim"}).json()) == 1

    # Übersicht: Owner-Notiz außerhalb bleibt unsichtbar.
    client.post("/api/annotations", json={
        "entry_id": _owner_entry_id(client, sid, "docs/a.txt"), "type": "note", "body": "nur owner"})
    deep_id = _owner_entry_id(client, sid, "docs/sub/deep.txt")
    second_client.post("/api/annotations", json={
        "entry_id": deep_id, "type": "note", "body": "kollege"})

    ov = second_client.get("/api/annotations").json()
    assert len(ov) == 1 and ov[0]["entry_name"] == "deep.txt"


def test_members_and_handover_are_path_scoped(client, second_client):
    sid = _tree_source(client)
    colleague = _me(second_client)
    _share_subtree(client, sid, colleague["email"], "docs/sub")

    # Mitglieder je Pfad.
    inside = {m["id"] for m in client.get(
        f"/api/sources/{sid}/members", params={"path": "docs/sub/deep.txt"}).json()}
    outside = {m["id"] for m in client.get(
        f"/api/sources/{sid}/members", params={"path": "docs/a.txt"}).json()}
    assert colleague["id"] in inside
    assert colleague["id"] not in outside

    # Übergabe innerhalb erlaubt, außerhalb (Empfänger kein Mitglied) abgelehnt.
    deep_id = _owner_entry_id(client, sid, "docs/sub/deep.txt")
    a_id = _owner_entry_id(client, sid, "docs/a.txt")
    assert client.post("/api/annotations", json={
        "entry_id": deep_id, "type": "handover", "assignee_user_id": colleague["id"]}).status_code == 201
    assert client.post("/api/annotations", json={
        "entry_id": a_id, "type": "handover", "assignee_user_id": colleague["id"]}).status_code == 422


def test_unshare_subtree(client, second_client):
    sid = _tree_source(client)
    colleague = _me(second_client)
    _share_subtree(client, sid, colleague["email"], "docs/sub")
    assert sid in [s["id"] for s in second_client.get("/api/sources").json()]

    r = client.delete(f"/api/sources/{sid}/shares/{colleague['id']}",
                      params={"path_prefix": "docs/sub"})
    assert r.status_code == 204
    assert second_client.get("/api/sources").json() == []


def test_multiple_subtrees_to_same_user(client, second_client):
    sid = _tree_source(client)
    colleague = _me(second_client)
    assert _share_subtree(client, sid, colleague["email"], "docs/sub").status_code == 201
    assert _share_subtree(client, sid, colleague["email"], "other").status_code == 201
    roots = {h["path"] for h in _children(second_client, sid, "").json()}
    assert roots == {"docs/sub", "other"}
