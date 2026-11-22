"""Other websites must not be able to trigger trades through the local API."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.security import add_origin_protection, parse_allowed_origins

DASHBOARD = "http://localhost:3000"
EVIL = "https://evil.example"


def _client() -> tuple[TestClient, list[str]]:
    calls: list[str] = []
    app = FastAPI()
    add_origin_protection(app, allowed=parse_allowed_origins(None))

    @app.post("/positions/{pid}/exit")
    def exit_position(pid: str):
        calls.append(pid)
        return {"ok": True}

    @app.get("/wallet/status")
    def status():
        return {"ok": True}

    return TestClient(app), calls


def test_foreign_origin_write_is_refused_and_not_executed():
    client, calls = _client()
    # A cross-site "simple" POST: no preflight, so CORS alone wouldn't stop it
    r = client.post(
        "/positions/p1/exit", headers={"Origin": EVIL, "Content-Type": "text/plain"}
    )
    assert r.status_code == 403
    assert calls == []


def test_null_origin_write_is_refused():
    client, calls = _client()
    r = client.post("/positions/p1/exit", headers={"Origin": "null"})
    assert r.status_code == 403
    assert calls == []


def test_dashboard_origin_write_is_allowed():
    client, calls = _client()
    r = client.post("/positions/p1/exit", headers={"Origin": DASHBOARD})
    assert r.status_code == 200
    assert calls == ["p1"]


def test_no_origin_local_script_is_allowed():
    client, calls = _client()
    assert client.post("/positions/p1/exit").status_code == 200
    assert calls == ["p1"]


def test_foreign_origin_reads_get_no_cors_header():
    client, _ = _client()
    r = client.get("/wallet/status", headers={"Origin": EVIL})
    assert "access-control-allow-origin" not in r.headers


def test_dashboard_preflight_ok():
    client, _ = _client()
    r = client.options(
        "/positions/p1/exit",
        headers={
            "Origin": DASHBOARD,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == DASHBOARD


def test_parse_allowed_origins():
    assert parse_allowed_origins(" http://a:1/ , http://b:2 ") == [
        "http://a:1",
        "http://b:2",
    ]
    assert parse_allowed_origins("") == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
