"""Tests für die Baumansicht (children-Endpunkt: nur direkte Kinder je Ebene)."""

from __future__ import annotations

import uuid


def _setup(client):
    sid = client.post("/api/sources", json={"label": "Q", "kind": "local"}).json()["id"]
    entries = [
        {"path": "docs", "name": "docs", "is_dir": True},
        {"path": "docs/a.txt", "name": "a.txt"},
        {"path": "docs/sub", "name": "sub", "is_dir": True},
        {"path": "docs/sub/deep.txt", "name": "deep.txt"},
        {"path": "top.txt", "name": "top.txt"},
    ]
    client.post(
        f"/api/sources/{sid}/ingest",
        json={"entries": entries, "finalize": True, "scan_id": uuid.uuid4().hex},
    )
    return sid


def _children(client, sid, parent=""):
    r = client.get(f"/api/sources/{sid}/children", params={"parent": parent})
    assert r.status_code == 200, r.text
    return {h["path"]: h for h in r.json()}


def test_root_children_are_top_level_only(client):
    sid = _setup(client)
    root = _children(client, sid, "")
    assert set(root) == {"docs", "top.txt"}
    # Ordner zuerst.
    assert root["docs"]["is_dir"] is True


def test_children_of_folder(client):
    sid = _setup(client)
    lvl1 = _children(client, sid, "docs")
    assert set(lvl1) == {"docs/a.txt", "docs/sub"}
    # "deep.txt" liegt eine Ebene tiefer und darf hier NICHT auftauchen.
    assert "docs/sub/deep.txt" not in lvl1

    lvl2 = _children(client, sid, "docs/sub")
    assert set(lvl2) == {"docs/sub/deep.txt"}


def test_children_include_annotations(client):
    sid = _setup(client)
    top = _children(client, sid, "")["top.txt"]
    client.post("/api/annotations", json={
        "entry_id": top["entry_id"], "type": "label", "label_value": "wichtig"})
    again = _children(client, sid, "")["top.txt"]
    assert len(again["annotations"]) == 1
    assert again["annotations"][0]["label_value"] == "wichtig"


def test_children_respect_access(client, second_client):
    sid = _setup(client)
    r = second_client.get(f"/api/sources/{sid}/children")
    assert r.status_code == 403
