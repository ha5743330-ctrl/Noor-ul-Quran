import base64
import io
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main

FREE = {"id": "free", "email": "f@x.y", "is_admin": False, "premium_access": False, "metadata": {}}
ADMIN = {"id": "a1", "email": "a@x.y", "is_admin": True, "premium_access": True, "metadata": {}}
TARGET = "00000000-0000-0000-0000-000000000002"
LIVE = {**FREE, "id": "u1", "token": "tok"}


class Resp:
    def __init__(self, data=None, status=200):
        self._data, self.status_code, self.ok = data or {}, status, status < 400

    def json(self):
        return self._data


@pytest.fixture
def client(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setenv("DISABLE_CLEANUP_THREAD", "1")
    for name, value in {
        "OUTPUT_DIR": out, "HISTORY_FILE": out / "history.json", "USERS_FILE": out / "users.json",
        "AUDIT_FILE": out / "audit.json", "CACHE_DIR": tmp_path / "cache",
        "SUPABASE_SERVICE_KEY": "", "SUPABASE_URL": "", "SUPABASE_KEY": "", "RAQM_AVAILABLE": True,
    }.items():
        monkeypatch.setattr(main, name, value)
    main._USERS_CACHE["at"] = -1e9
    for registry in (main.GENERATION_JOBS, main.DAILY_USAGE, main.ATTEMPTS, main.AUTH_CACHE):
        registry.clear()
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()
    for registry in (main.GENERATION_JOBS, main.ATTEMPTS, main.AUTH_CACHE):
        registry.clear()


def _as(user):
    main.app.dependency_overrides[main.get_current_user] = lambda: user
    main.app.dependency_overrides[main.get_user_allow_deactivated] = lambda: user


def _live(monkeypatch, user=LIVE):
    """Real auth dependency: only the Supabase round trip is stubbed."""
    monkeypatch.setattr(main, "_fetch_current_user", lambda token: dict(user))
    return {"Authorization": "Bearer tok"}


def _admin_stub(monkeypatch, admins=()):
    calls = []

    def fake(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return Resp({"email": "t@x.y", "user_metadata": {}})

    monkeypatch.setattr(main, "_admin_auth", fake)
    monkeypatch.setattr(main, "_admin_ids", lambda: set(admins))
    return calls


def test_block_is_enforced_on_a_live_token_and_cancels_jobs(client, monkeypatch):
    headers = _live(monkeypatch)
    assert client.get("/api/access", headers=headers).status_code == 200  # token now sits in AUTH_CACHE
    main.GENERATION_JOBS["j"] = {"owner_id": "u1", "status": "queued"}

    main._update_user_state("u1", blocked=True, reason="spam")
    for path in ("/api/access", "/api/history", "/api/me"):
        r = client.get(path, headers=headers)
        assert r.status_code == 403, path
        assert r.json()["error"] == "account_blocked" and r.json()["reason"] == "spam"
    main._cancel_user_jobs("u1")
    assert main.GENERATION_JOBS["j"]["status"] == "failed" and main.GENERATION_JOBS["j"]["cancelled"]

    main._update_user_state("u1", blocked=False, reason="")
    assert client.get("/api/access", headers=headers).status_code == 200


def test_admin_block_unblock_audit_and_ban_calls(client, monkeypatch):
    _as(ADMIN)
    calls = _admin_stub(monkeypatch, admins={"a1"})
    assert client.post(f"/api/admin/users/{TARGET}/block", json={"reason": "abuse"}).json() == {"status": "blocked"}
    assert main._users_state()[TARGET] == {"blocked": True, "reason": "abuse"}
    assert any(c[0] == "PUT" and c[2]["json"] == {"ban_duration": "876000h"} for c in calls)

    assert client.post(f"/api/admin/users/{TARGET}/unblock").status_code == 200
    assert TARGET not in main._users_state()
    assert any(c[2].get("json") == {"ban_duration": "none"} for c in calls)

    entries = client.get("/api/admin/audit").json()["entries"]
    assert [e["action"] for e in entries] == ["unblock", "block"]
    assert entries[1]["actor_id"] == "a1" and entries[1]["reason"] == "abuse"


def test_last_admin_and_self_cannot_be_blocked_or_delete_themselves(client, monkeypatch):
    _as(ADMIN)
    _admin_stub(monkeypatch, admins={"a1"})
    own = "00000000-0000-0000-0000-000000000001"
    monkeypatch.setitem(ADMIN, "id", own)
    monkeypatch.setattr(main, "_admin_ids", lambda: {own})
    assert client.post(f"/api/admin/users/{own}/block", json={}).status_code == 409

    # another admin exists but is already blocked -> target is the last *active* admin
    monkeypatch.setattr(main, "_admin_ids", lambda: {own, TARGET})
    main._update_user_state(TARGET, blocked=True)
    other_admin = {**ADMIN, "id": "00000000-0000-0000-0000-000000000009"}
    _as(other_admin)
    assert client.post(f"/api/admin/users/{own}/block", json={}).status_code == 409

    # last admin cannot delete their own account
    _as({**ADMIN, "id": own})
    monkeypatch.setattr(main, "_admin_ids", lambda: {own})
    monkeypatch.setattr(main, "_verify_password", lambda user, password: None)
    r = client.post("/api/me/delete", json={"password": "x", "confirm": "DELETE"})
    assert r.status_code == 409


def test_non_admin_gets_403_on_every_admin_route(client):
    _as(FREE)
    for method, path in (
        ("get", "/api/admin/users"),
        ("get", "/api/admin/audit"),
        ("post", f"/api/admin/users/{TARGET}/block"),
        ("post", f"/api/admin/users/{TARGET}/unblock"),
        ("post", f"/api/admin/users/{TARGET}/signout"),
        ("post", f"/api/admin/users/{TARGET}/purge"),
    ):
        assert getattr(client, method)(path).status_code == 403, path


def test_admin_audit_pages_newest_first(client):
    _as(ADMIN)
    main._write_state(main.AUDIT_FILE, main.AUDIT_OBJECT, [{"action": str(i)} for i in range(120)])
    first = client.get("/api/admin/audit").json()
    assert first["total"] == 120 and len(first["entries"]) == 50 and first["entries"][0]["action"] == "119"
    last = client.get("/api/admin/audit", params={"offset": 100}).json()
    assert [e["action"] for e in last["entries"]] == [str(i) for i in range(19, -1, -1)]


def test_admin_user_list_filters_searches_and_pages(client, monkeypatch):
    _as(ADMIN)
    users = [
        {"id": f"id{i:03d}", "email": f"user{i}@x.y", "created_at": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
         "user_metadata": {"display_name": f"Name {i}"}}
        for i in range(60)
    ]
    monkeypatch.setattr(main, "_all_auth_users", lambda: users)
    monkeypatch.setattr(main, "_premium_ids", lambda: {"id001"})
    monkeypatch.setattr(main, "_admin_ids", lambda: set())
    monkeypatch.setattr(main, "_signed_urls", lambda paths: {})
    main._update_user_state("id005", blocked=True, reason="r")

    body = client.get("/api/admin/users").json()
    assert body["total"] == 60 and len(body["users"]) == 50 and body["stats"] == {"total": 60, "premium": 1}
    assert len(client.get("/api/admin/users?page=2").json()["users"]) == 10
    blocked = client.get("/api/admin/users?status=blocked").json()["users"]
    assert [u["id"] for u in blocked] == ["id005"] and blocked[0]["reason"] == "r"
    assert [u["email"] for u in client.get("/api/admin/users?q=USER17@").json()["users"]] == ["user17@x.y"]
    assert client.get("/api/admin/users?status=nope").status_code == 400


def test_delete_account_purges_every_storage_key_and_the_auth_user(client, monkeypatch):
    _as(FREE)
    main._record_video_history("free", [{"filename": "a.mp4", "object_path": "videos/x_a.mp4"}], "full", "fast", None)
    main._record_image_history("free", "ayah-image-1.png", "1:1")
    main._record_video_history("other", [{"filename": "b.mp4", "object_path": "videos/y_b.mp4"}], "full", "fast", None)
    removed = []
    monkeypatch.setattr(main, "_delete_media_objects", lambda paths: removed.extend(paths))
    monkeypatch.setattr(main, "_verify_password", lambda user, password: None)
    calls = _admin_stub(monkeypatch)

    assert client.post("/api/me/delete", json={"password": "pw", "confirm": "nope"}).status_code == 400
    assert removed == []
    r = client.post("/api/me/delete", json={"password": "pw", "confirm": "DELETE"})
    assert r.status_code == 200 and r.json()["items_removed"] == 2
    assert sorted(removed) == ["avatars/free.webp", "ayah-images/ayah-image-1.png", "videos/x_a.mp4"]
    assert ("DELETE", "/users/free") in [(c[0], c[1]) for c in calls]
    users = main._read_video_history_unlocked()["users"]
    assert "free" not in users and "other" in users  # nobody else's history is touched


def test_delete_stops_when_storage_fails_and_keeps_the_login(client, monkeypatch):
    _as(FREE)
    main._record_video_history("free", [{"filename": "a.mp4", "object_path": "videos/x_a.mp4"}], "full", "fast", None)

    def boom(paths):
        raise main.HTTPException(status_code=502, detail="storage down")

    monkeypatch.setattr(main, "_delete_media_objects", boom)
    monkeypatch.setattr(main, "_verify_password", lambda user, password: None)
    calls = _admin_stub(monkeypatch)
    assert client.post("/api/me/delete", json={"password": "pw", "confirm": "DELETE"}).status_code == 502
    assert not any(c[0] == "DELETE" for c in calls)
    assert len(main._user_entries("free")) == 1


def _png(size=(40, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 120, 80)).save(buffer, "PNG")
    return buffer.getvalue()


def test_avatar_rejects_non_images_and_oversize_and_resizes_valid(client, monkeypatch):
    _as(FREE)
    stored = {}
    monkeypatch.setattr(main, "_put_media_bytes", lambda path, data, content_type: stored.update(path=path, data=data, type=content_type))
    monkeypatch.setattr(main, "_set_user_metadata", lambda user_id, **fields: stored.update(meta=fields))
    monkeypatch.setattr(main, "_signed_media_url", lambda path: f"https://signed/{path}")

    assert client.post("/api/me/avatar", content=b"just some text").status_code == 400
    assert client.post("/api/me/avatar", content=b"GIF89a" + b"\0" * 50).status_code == 400
    assert client.post("/api/me/avatar", content=b"\x89PNG\r\n\x1a\n" + b"garbage").status_code == 400  # magic ok, undecodable
    too_big = _png() + b"\0" * main.AVATAR_MAX_BYTES
    assert client.post("/api/me/avatar", content=too_big).status_code == 413
    assert stored == {}

    r = client.post("/api/me/avatar", content=_png())
    assert r.status_code == 200 and r.json()["avatar_url"] == "https://signed/avatars/free.webp"
    image = Image.open(io.BytesIO(stored["data"]))
    assert image.format == "WEBP" and image.size == (256, 256)
    assert stored["path"] == "avatars/free.webp" and stored["meta"] == {"avatar_path": "avatars/free.webp"}


def test_password_change_needs_the_current_password_and_is_rate_limited(client, monkeypatch):
    _as(FREE)
    calls = _admin_stub(monkeypatch)
    monkeypatch.setattr(main, "SUPABASE_KEY", "anon")
    monkeypatch.setattr(main, "_supabase_request", lambda method, path, token, **kw: Resp(status=200 if kw["json"]["password"] == "oldpass1" else 400))

    body = {"current_password": "wrong", "new_password": "newpass123"}
    r = client.post("/api/me/password", json=body)
    assert r.status_code == 403 and "incorrect" in r.json()["detail"]
    assert calls == []
    assert client.post("/api/me/password", json={"current_password": "oldpass1", "new_password": "short"}).status_code == 400
    assert calls == []
    assert client.post("/api/me/password", json={**body, "current_password": "oldpass1"}).status_code == 200
    assert calls[-1][2]["json"] == {"password": "newpass123"}

    codes = [client.post("/api/me/password", json=body).status_code for _ in range(4)]
    assert codes == [403, 403, 429, 429]  # attempts 4,5 reach Supabase, 6+ are refused


def test_deactivated_user_is_locked_out_until_reactivation(client, monkeypatch):
    headers = _live(monkeypatch)
    monkeypatch.setattr(main, "_set_user_metadata", lambda user_id, **fields: None)
    assert client.post("/api/me/deactivate", headers=headers).json() == {"status": "deactivated"}

    for path in ("/api/access", "/api/history", "/api/me/usage"):
        r = client.get(path, headers=headers)
        assert r.status_code == 403 and r.json()["error"] == "account_deactivated", path
    assert client.post("/api/generate", headers=headers, json={"mode": "pick", "verse": "1:1"}).status_code == 403
    assert client.get("/api/me", headers=headers).json()["status"] == "deactivated"  # profile stays reachable

    assert client.post("/api/me/reactivate", headers=headers).json() == {"status": "active"}
    assert client.get("/api/access", headers=headers).status_code == 200


def _jwt(claims):
    part = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part(claims)}.sig"


def test_sign_out_everywhere_rejects_logins_older_than_the_action(client, monkeypatch):
    monkeypatch.setattr(main, "_fetch_current_user", lambda token: dict(LIVE))
    now = int(time.time())
    old = {"Authorization": "Bearer " + _jwt({"iat": now, "amr": [{"method": "password", "timestamp": now - 100}]})}
    new = {"Authorization": "Bearer " + _jwt({"iat": now, "amr": [{"method": "password", "timestamp": now + 5}]})}
    assert client.get("/api/access", headers=old).status_code == 200
    main._update_user_state("u1", signed_out_at=now)
    assert client.get("/api/access", headers=old).status_code == 401
    assert client.get("/api/access", headers=new).status_code == 200  # fresh login is fine


def test_profile_name_is_validated_server_side(client, monkeypatch):
    _as(FREE)
    saved = {}
    monkeypatch.setattr(main, "_set_user_metadata", lambda user_id, **fields: saved.update(fields))
    assert client.put("/api/me", json={"display_name": "A"}).status_code == 400
    assert client.put("/api/me", json={"display_name": "x" * 41}).status_code == 400
    assert client.put("/api/me", json={"display_name": "   "}).status_code == 400
    r = client.put("/api/me", json={"display_name": "  Aisha <b>K</b>  "})
    assert r.status_code == 200 and saved == {"display_name": "Aisha <b>K</b>"}  # stored raw, rendered via textContent


def test_export_has_profile_and_history_without_secrets(client):
    _as(FREE)
    main._record_video_history("free", [{"filename": "a.mp4", "object_path": "videos/x_a.mp4"}], "full", "fast", None)
    body = client.get("/api/me/export").json()
    assert body["profile"]["email"] == "f@x.y" and "token" not in json.dumps(body)
    assert len(body["history"]) == 1 and "object_path" not in body["history"][0]


def test_access_reports_the_configured_free_cap(client, monkeypatch):
    _as(FREE)
    assert main.MAX_VIDEO_SECONDS == 35
    assert client.get("/api/access").json()["max_video_seconds"] == main.MAX_VIDEO_SECONDS
