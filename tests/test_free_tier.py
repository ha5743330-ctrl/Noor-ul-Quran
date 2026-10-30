import json
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main

FREE = {"id": "free", "email": "f@x.y", "is_admin": False, "premium_access": False}
ADMIN = {"id": "admin", "email": "a@x.y", "is_admin": True, "premium_access": True}
PAYLOAD = {"mode": "pick", "verse": "1:1", "max_duration": 30, "quality": "fast"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setenv("DISABLE_CLEANUP_THREAD", "1")
    for name, value in {
        "OUTPUT_DIR": out, "HISTORY_FILE": out / "history.json", "CACHE_DIR": tmp_path / "cache",
        "SUPABASE_SERVICE_KEY": "", "SUPABASE_URL": "", "SUPABASE_KEY": "", "RAQM_AVAILABLE": True,
    }.items():
        monkeypatch.setattr(main, name, value)
    monkeypatch.setattr(main, "GENERATION_EXECUTOR", SimpleNamespace(submit=lambda *a, **k: None))
    main.GENERATION_JOBS.clear()
    main.DAILY_USAGE.clear()
    main.RENDER_DURATIONS.clear()
    main.RENDER_RATES.clear()
    main.app.dependency_overrides[main.get_current_user] = lambda: FREE
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()
    main.GENERATION_JOBS.clear()


def _as(user):
    main.app.dependency_overrides[main.get_current_user] = lambda: user


def _finish_jobs():
    for job in main.GENERATION_JOBS.values():
        job["status"] = "completed"


def test_daily_limit_blocks_then_resets_and_admin_exempt(client, monkeypatch):
    monkeypatch.setattr(main, "DAILY_LIMIT", 2)
    for expected_left in (1, 0):
        r = client.post("/api/generate", json=PAYLOAD)
        assert r.status_code == 202 and r.json()["daily_remaining"] == expected_left
        _finish_jobs()
    r = client.post("/api/generate", json=PAYLOAD)
    assert r.status_code == 429 and "Daily limit" in r.json()["detail"]
    assert client.get("/api/access").json()["daily_remaining"] == 0

    monkeypatch.setattr(main, "_today", lambda: "next-day")
    assert client.get("/api/access").json()["daily_remaining"] == 2
    assert client.post("/api/generate", json=PAYLOAD).status_code == 202

    _as(ADMIN)
    for _ in range(3):
        assert client.post("/api/generate", json=PAYLOAD).status_code == 202
        _finish_jobs()
    assert client.get("/api/access").json()["daily_remaining"] is None


def test_duration_cap_default_quality_and_single_active_job(client):
    assert client.post("/api/generate", json={**PAYLOAD, "max_duration": 60}).status_code == 400
    assert main.GenerateRequest(mode="pick").quality == "fast"
    assert client.post("/api/generate", json=PAYLOAD).status_code == 202
    r = client.post("/api/generate", json=PAYLOAD)
    assert r.status_code == 429 and "already have" in r.json()["detail"]


def test_queue_position_and_wait_count_videos_and_seconds(client):
    main.RENDER_RATES.extend([2.0, 4.0])  # render seconds per video second, average 3
    main.GENERATION_JOBS["running"] = {"owner_id": "other", "status": "processing", "planned_seconds": 20}
    main.GENERATION_JOBS["waiting"] = {"owner_id": "other2", "status": "queued", "planned_seconds": 20}
    r = client.post("/api/generate", json=PAYLOAD)
    body = r.json()
    own = main.GENERATION_JOBS[body["job_id"]]["planned_seconds"]
    expected = round((40 + own) * 3)
    assert (body["queue_ahead"], body["estimated_wait_seconds"]) == (2, expected)

    status = client.get(f"/api/generate/{body['job_id']}").json()
    assert (status["queue_ahead"], status["estimated_wait_seconds"]) == (2, expected)
    main.GENERATION_JOBS["running"]["status"] = "completed"
    assert client.get(f"/api/generate/{body['job_id']}").json()["queue_ahead"] == 1


def test_cleanup_removes_only_old_local_media_and_history(client):
    old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    for name in ("old.mp4", "new.mp4", "keep.json"):
        (main.OUTPUT_DIR / name).write_bytes(b"x")
    main.HISTORY_FILE.write_text(json.dumps({"users": {"u": [
        {"id": "1", "filename": "old.mp4", "created_at": old},
        {"id": "2", "filename": "new.mp4", "created_at": new},
    ]}}), encoding="utf-8")

    counts = main.cleanup_old_media()

    assert counts == {"storage": 0, "local": 1, "history": 1}
    assert not (main.OUTPUT_DIR / "old.mp4").exists()
    assert (main.OUTPUT_DIR / "new.mp4").exists() and (main.OUTPUT_DIR / "keep.json").exists()
    kept = json.loads(main.HISTORY_FILE.read_text(encoding="utf-8"))["users"]["u"]
    assert [e["id"] for e in kept] == ["2"]


def test_cleanup_storage_is_scoped_to_media_bucket_prefixes(client, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    new = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "key")
    listing = {
        "videos": [{"id": "a", "name": "old.mp4", "created_at": old}, {"id": "b", "name": "new.mp4", "created_at": new}],
        "ayah-images": [{"id": "c", "name": "old.png", "created_at": old}],
    }
    monkeypatch.setattr(main, "_list_all_media_objects", lambda prefix: listing[prefix])
    calls = []
    monkeypatch.setattr(main, "_storage_request", lambda method, path, **kw: calls.append((method, path, kw)))

    assert main.cleanup_old_media()["storage"] == 2
    assert len(calls) == 1
    method, path, kw = calls[0]
    assert method == "DELETE" and path.endswith(main.MEDIA_BUCKET)
    assert kw["json"]["prefixes"] == ["ayah-images/old.png", "videos/old.mp4"]
    # other buckets / prefixes are refused by the delete allowlist
    calls.clear()
    main._delete_media_objects(["metadata/captions.csv", "../x", "videos/../y"])
    assert calls == []


def test_health_answers_while_render_lock_is_held(client):
    held = threading.Event()
    release = threading.Event()

    def hold():
        with main.GENERATION_LOCK:
            held.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(5)
    try:
        assert client.get("/api/health").json()["status"] == "online"
        assert client.head("/api/health").status_code == 200
    finally:
        release.set()
        t.join()


def test_recent_survives_restart_via_supabase_backup(client, monkeypatch):
    store = {}
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "key")
    monkeypatch.setattr(main, "_upload_media_file", lambda path, obj, ct, **k: store.__setitem__(obj, path.read_bytes()))

    def fake_download(obj, path, **k):
        path.write_bytes(store[obj])
        return True

    monkeypatch.setattr(main, "_download_media_file", fake_download)
    monkeypatch.setattr(main, "_signed_media_url", lambda obj: "https://sb/" + obj)
    main._record_video_history("free", [{"filename": "s112.mp4", "object_path": "videos/abc_s112.mp4"}], "full", "fast", {})
    assert "state/history.json" in store

    main.HISTORY_FILE.unlink()  # Render restart: disk wiped
    main._pull_history()
    video = client.get("/api/history").json()["videos"][0]
    assert video["filename"] == "s112.mp4" and video["has_file"]
    url = client.get(f"/api/history/{video['id']}/url").json()["url"]
    assert url == "https://sb/videos/abc_s112.mp4"


def test_config_js_exposes_contact_email_placeholder(client):
    assert 'window.NOOR_CONTACT_EMAIL = "contact@example.com"' in client.get("/config.js").text
