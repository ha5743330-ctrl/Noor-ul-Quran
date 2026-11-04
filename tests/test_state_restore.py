"""State restore: 404 starts empty; 502/timeout retry then SAFE MODE (no uploads) until a retry succeeds."""
import json

import pytest
import requests

import main


class Resp:
    def __init__(self, status, body=b""):
        self.status_code, self.body = status, body

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return json.loads(self.body)

    def iter_content(self, chunk_size):
        yield self.body


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Fake Supabase: `script` is a queue of GET outcomes; every POST (upload) is recorded."""
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "svc-key")
    monkeypatch.setattr(main, "SUPABASE_URL", "https://proj.supabase.co")
    monkeypatch.setattr(main, "STATE_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(main, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(main, "AUDIT_FILE", tmp_path / "audit.json")
    monkeypatch.setattr(main, "_USERS_CACHE", {"at": -1e9, "data": {}})
    monkeypatch.setenv("DISABLE_CLEANUP_THREAD", "1")
    state = {"script": [], "uploads": []}

    def fake_request(method, url, **kw):
        if method == "POST":
            state["uploads"].append(url)
            return Resp(200, b"{}")
        outcome = state["script"].pop(0) if state["script"] else Resp(404)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(main.requests, "request", fake_request)
    return state


def test_502_retries_then_safe_mode_blocks_writes_until_restore(storage, caplog):
    good = json.dumps({"u1": {"blocked": True}}).encode()
    storage["script"] = [Resp(502), Resp(502), Resp(502)]
    main._pull_account_state()  # users.json -> safe mode; audit.json -> 404 (empty)
    assert main.STATE_SAFE_MODE[main.USERS_OBJECT]["status"] == 502
    assert main.AUDIT_OBJECT not in main.STATE_SAFE_MODE
    assert "HTTP 502" in caplog.text and "svc-key" not in caplog.text and "supabase.co" not in caplog.text

    with pytest.raises(main.HTTPException) as err:
        main._update_user_state("u2", blocked=True)
    assert err.value.status_code == 503 and "changes disabled" in err.value.detail
    blank = main.AUDIT_FILE.parent / "blank.json"
    blank.write_text("{}")
    with pytest.raises(main.HTTPException):
        main._upload_media_file(blank, main.USERS_OBJECT, "application/json")  # any direct writer is refused too
    assert not any("state/users.json" in u for u in storage["uploads"])  # never overwritten
    assert not main.USERS_FILE.exists()

    storage["script"] = [Resp(200, good)]
    main._retry_safe_mode_state()  # background tick succeeds
    assert main.USERS_OBJECT not in main.STATE_SAFE_MODE
    assert json.loads(main.USERS_FILE.read_text())["u1"]["blocked"] is True
    main._update_user_state("u2", blocked=True)  # writable again
    assert any("state/users.json" in u for u in storage["uploads"])


def test_502_then_success_within_retries(storage):
    storage["script"] = [Resp(502), Resp(200, b'{"a": 1}')]
    assert main._restore_state(main.USERS_OBJECT, main.USERS_FILE) is True
    assert not main.STATE_SAFE_MODE and json.loads(main.USERS_FILE.read_text()) == {"a": 1}


@pytest.mark.parametrize("missing", [Resp(404), Resp(400, b'{"statusCode": "404", "error": "not_found"}')])
def test_404_starts_empty_and_stays_writable(storage, missing):
    storage["script"] = [missing]
    assert main._restore_state(main.USERS_OBJECT, main.USERS_FILE) is False
    assert not main.STATE_SAFE_MODE and main._users_state() == {}
    main._update_user_state("u1", blocked=True)
    assert storage["uploads"]


def test_timeout_enters_safe_mode_and_picks_save_is_refused(storage, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(main, "_PICKS_PULLED", False)
    storage["script"] = [requests.ReadTimeout("t")] * 3
    picks = main._load_picks()  # falls back to the bundled defaults, read-only
    assert picks and main.STATE_SAFE_MODE[main.PICKS_OBJECT]["status"] == "ReadTimeout"
    assert "HTTP ReadTimeout" in caplog.text
    with pytest.raises(main.HTTPException) as err:
        main._save_picks(picks)
    assert err.value.status_code == 502 and "nothing was changed" in err.value.detail
    assert not storage["uploads"] and not main._picks_file().exists()


def test_diagnostics_reports_safe_mode(storage):
    main.STATE_SAFE_MODE["state/users.json"] = {"local": main.USERS_FILE, "status": 502}
    main.app.dependency_overrides[main.require_admin] = lambda: {"id": "a", "is_admin": True}
    try:
        from fastapi.testclient import TestClient
        body = TestClient(main.app).get("/api/admin/diagnostics").json()
    finally:
        main.app.dependency_overrides.clear()
    assert body["state_safe_mode"] == {"state/users.json": 502}
