import importlib
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ACCESS_KEY", "geheim")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import app.main as main

    importlib.reload(main)
    with TestClient(main.app) as c:
        yield c


def login(client):
    assert client.post("/api/login", json={"key": "geheim"}).status_code == 200


def add(client, d, loc="standort-1", red=0, yellow=0, other=0):
    r = client.post("/api/entries", json={
        "date": d.isoformat(), "location_id": loc, "red": red, "yellow": yellow, "other": other,
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_requires_key(client):
    assert client.get("/api/entries").status_code == 401
    assert client.get("/api/session").json() == {"authorized": False}
    assert client.post("/api/login", json={"key": "falsch"}).status_code == 401
    login(client)
    assert client.get("/api/session").json() == {"authorized": True}
    assert client.get("/api/entries").status_code == 200


def test_login_link_sets_cookie(client):
    r = client.get("/login?key=geheim", follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/api/entries").status_code == 200


def test_entry_validation(client):
    login(client)
    today = date.today()
    bad = {"date": today.isoformat(), "location_id": "gibtsnicht", "red": 1, "yellow": 0, "other": 0}
    assert client.post("/api/entries", json=bad).status_code == 422
    neg = {**bad, "location_id": "standort-1", "red": -1}
    assert client.post("/api/entries", json=neg).status_code == 422
    future = {**bad, "location_id": "standort-1", "date": (today + timedelta(days=5)).isoformat()}
    assert client.post("/api/entries", json=future).status_code == 422


def test_multiple_entries_and_delete(client):
    login(client)
    d = date.today()
    a = add(client, d, red=2)
    add(client, d, red=3)
    assert len(client.get("/api/entries").json()) == 2
    assert client.delete(f"/api/entries/{a['id']}").status_code == 204
    assert client.delete(f"/api/entries/{a['id']}").status_code == 404
    assert len(client.get("/api/entries").json()) == 1


def test_stats(client):
    login(client)
    monday = date(2026, 9, 14)
    add(client, monday, "standort-1", red=2, yellow=5, other=1)
    add(client, monday, "standort-1", red=1)
    add(client, monday + timedelta(days=2), "standort-2", yellow=4)
    add(client, monday + timedelta(days=7), "standort-1", red=3)

    s = client.get("/api/stats", params={
        "granularity": "week", "start": "2026-09-14", "end": "2026-09-25",
    }).json()
    assert s["labels"] == ["KW 38", "KW 39"]
    assert s["by_category"]["red"] == [3, 3]
    assert s["by_category"]["yellow"] == [9, 0]
    assert s["shifts"] == [3, 1]
    assert s["totals"] == {"red": 6, "yellow": 9, "other": 1, "total": 16, "shifts": 4}
    loc1 = next(l for l in s["location_totals"] if l["id"] == "standort-1")
    assert loc1["total"] == 12 and loc1["shifts"] == 3

    s = client.get("/api/stats", params={
        "granularity": "day", "start": "2026-09-14", "end": "2026-09-20", "location": "standort-2",
    }).json()
    assert len(s["labels"]) == 5  # Mo–Fr, Wochenende ohne Daten ausgeblendet
    assert s["by_category"]["yellow"] == [0, 0, 4, 0, 0]
    assert [l["id"] for l in s["location_totals"]] == ["standort-2"]

    s = client.get("/api/stats", params={"granularity": "month", "start": "2026-08-01", "end": "2026-09-30"}).json()
    assert s["labels"] == ["Aug 26", "Sep 26"]


def test_csv_export(client):
    login(client)
    add(client, date.today(), red=1, yellow=2)
    r = client.get("/api/export.csv")
    assert r.status_code == 200
    assert "Hauptstraße / Schulstraße;1;2;0;3" in r.text
