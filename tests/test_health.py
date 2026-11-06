import requests
from fastapi.testclient import TestClient

import main


def _get(monkeypatch, fake):
    monkeypatch.setattr(main.requests, "get", fake)
    monkeypatch.setattr(main, "SUPABASE_URL", "https://x.supabase.co")
    with TestClient(main.app) as c:
        return c.get("/api/health/db")


class _R:
    def __init__(self, ok):
        self.ok = ok


def test_health_ok(monkeypatch):
    seen = {}

    def fake(url, **kw):
        seen.update(url=url, **kw)
        return _R(True)

    r = _get(monkeypatch, fake)
    assert r.status_code == 200 and r.json() == {"ok": True, "db": True}
    assert "profiles" in seen["url"] and "limit=1" in seen["url"] and seen["timeout"] == 3


def test_health_db_failure(monkeypatch):
    r = _get(monkeypatch, lambda url, **kw: _R(False))
    assert r.status_code == 200 and r.json() == {"ok": True, "db": False}


def test_health_hard_timeout(monkeypatch):
    import time
    orig = main.anyio.fail_after
    monkeypatch.setattr(main.anyio, "fail_after", lambda s: orig(0.2))
    r = _get(monkeypatch, lambda url, **kw: time.sleep(1) or _R(True))
    assert r.json() == {"ok": True, "db": False}


def test_health_timeout(monkeypatch):
    def boom(url, **kw):
        raise requests.Timeout("slow")

    r = _get(monkeypatch, boom)
    assert r.status_code == 200 and r.json() == {"ok": True, "db": False}
    assert "slow" not in r.text
