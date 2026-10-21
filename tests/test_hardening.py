import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main


FREE_USER = {"id": "free-user", "is_admin": False, "premium_access": False}
ADMIN_USER = {"id": "admin-user", "is_admin": True, "premium_access": True}


@pytest.fixture
def client(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    background_dir = tmp_path / "backgrounds"
    (background_dir / "music").mkdir(parents=True)
    output_dir.mkdir()
    monkeypatch.setattr(main, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(main, "HISTORY_FILE", output_dir / "history.json")
    monkeypatch.setattr(main, "BACKGROUND_DIR", background_dir)
    monkeypatch.setattr(main, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "")
    monkeypatch.setattr(main, "SUPABASE_URL", "")
    monkeypatch.setattr(main, "SUPABASE_KEY", "")
    main.app.dependency_overrides[main.get_current_user] = lambda: FREE_USER
    main.GENERATION_JOBS.clear()
    with TestClient(main.app) as test_client:
        yield test_client
    main.app.dependency_overrides.clear()
    main.GENERATION_JOBS.clear()


def _write_history(entries_by_user):
    main.HISTORY_FILE.write_text(
        json.dumps({"users": entries_by_user}),
        encoding="utf-8",
    )


def test_authentication_required_for_history_and_generation(client):
    main.app.dependency_overrides.clear()
    assert client.get("/api/history").status_code == 401
    assert client.post("/api/generate", json={"mode": "pick", "verse": "1:1"}).status_code == 401
    main.app.dependency_overrides[main.get_current_user] = lambda: FREE_USER


def test_free_user_is_gated_from_picks_random_and_long_videos(client):
    for payload in (
        {"mode": "picks"},
        {"mode": "random"},
        {"mode": "pick", "verse": "1:1", "max_duration": 61},
    ):
        response = client.post("/api/generate", json=payload)
        assert response.status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "pick", "verse": "1:1", "content_mode": "invalid"},
        {"mode": "pick", "verse": "1:1", "styles": {"arabic": {"color": "red"}}},
        {"mode": "pick", "verse": "1:1", "styles": {"arabic": {"font": "unknown"}}},
        {"mode": "pick", "verse": "1:1", "styles": {"other": {"color": "#ffffff"}}},
        {"mode": "pick", "verse": "1:1", "music": "../outside.mp3"},
        {"mode": "pick", "verse": "1:1", "music": "..\\outside.mp3"},
        {"mode": "pick", "verse": "1:1", "music": ""},
        {"mode": "pick", "verse": "1:1", "music": "not-listed.mp3"},
        {"mode": "pick", "verse": "1:1", "music": "bad\x00name.mp3"},
    ],
)
def test_invalid_generation_requests_are_rejected(client, payload):
    assert client.post("/api/generate", json=payload).status_code == 400


def test_dropdown_music_filename_is_the_only_filename_passed_to_renderer(client, monkeypatch):
    music = main.BACKGROUND_DIR / "music" / "permitted.mp3"
    music.write_bytes(b"audio")
    captured = {}

    class Executor:
        def submit(self, *args):
            captured["args"] = args

    monkeypatch.setattr(main, "GENERATION_EXECUTOR", Executor())
    assert client.get("/api/music-list").json() == {"music_files": ["permitted.mp3"]}
    response = client.post(
        "/api/generate",
        json={"mode": "pick", "verse": "1:1", "music": "permitted.mp3"},
    )
    assert response.status_code == 202
    command = captured["args"][2]
    music_index = command.index("--music")
    assert command[music_index + 1] == "permitted.mp3"


def test_music_symlink_cannot_escape_music_directory(client):
    outside = main.BACKGROUND_DIR.parent / "outside.mp3"
    outside.write_bytes(b"audio")
    link = main.BACKGROUND_DIR / "music" / "linked.mp3"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are not available on this platform.")
    assert main._available_music_filenames() == ["linked.mp3"]
    with pytest.raises(HTTPException) as exc:
        main._validate_music_filename("linked.mp3")
    assert exc.value.status_code == 400


def test_history_is_capped_at_five_and_prunes_local_and_storage_files(client, monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "configured")
    deleted_objects = []
    monkeypatch.setattr(main, "_delete_media_objects", lambda paths: deleted_objects.extend(paths))
    videos = []
    for index in range(6):
        filename = f"video{index}.mp4"
        (main.OUTPUT_DIR / filename).write_bytes(b"video")
        videos.append({"filename": filename, "object_path": f"videos/object-{index}.mp4"})

    main._record_video_history("free-user", videos, "full", "balanced", {})
    history = json.loads(main.HISTORY_FILE.read_text(encoding="utf-8"))
    entries = history["users"]["free-user"]
    assert len(entries) == 5
    assert (main.OUTPUT_DIR / "video0.mp4").exists() is False
    assert deleted_objects == ["videos/object-0.mp4"]
    assert all(entry["object_path"] for entry in entries)


def test_history_ownership_and_delete(client, monkeypatch):
    video = main.OUTPUT_DIR / "owned.mp4"
    video.write_bytes(b"video")
    _write_history({"another-user": [{"id": "private-entry", "filename": "owned.mp4"}]})
    assert client.get("/api/history/private-entry/url").status_code == 404
    assert client.delete("/api/history/private-entry").status_code == 404

    video.write_bytes(b"video")
    _write_history({
        "free-user": [{
            "id": "my-entry",
            "filename": "owned.mp4",
            "object_path": "videos/owned-object.mp4",
        }]
    })
    deleted_objects = []
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "configured")
    monkeypatch.setattr(main, "_delete_media_objects", lambda paths: deleted_objects.extend(paths))
    response = client.delete("/api/history/my-entry")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert not video.exists()
    assert deleted_objects == ["videos/owned-object.mp4"]


def test_failed_renderer_marks_job_failed_and_logs_without_secrets(client, monkeypatch, caplog):
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "private-service-key")
    monkeypatch.setattr(main, "_prepare_ephemeral_media", lambda: None)
    monkeypatch.setattr(main, "_cleanup_orphaned_output_files", lambda: 0)
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="render failed private-service-key", stdout=""),
    )
    manifest = main.CACHE_DIR / "batch-failure.json"
    work_dir = main.CACHE_DIR / "tmp" / "failure"
    main.GENERATION_JOBS["job-failure"] = {
        "owner_id": "free-user",
        "status": "queued",
        "manifest_path": str(manifest),
        "videos": [],
    }

    main._run_generation_job(
        "job-failure",
        ["make.py"],
        "pick",
        manifest,
        work_dir,
        user_id="free-user",
    )
    assert main.GENERATION_JOBS["job-failure"]["status"] == "failed"
    assert "private-service-key" not in main.GENERATION_JOBS["job-failure"]["error"]
    assert "private-service-key" not in caplog.text
    assert "job-failure" in caplog.text
    assert "free-user" in caplog.text


def test_signed_urls_reject_tampered_expired_non_ascii_and_wrong_type(client):
    (main.OUTPUT_DIR / "signed.mp4").write_bytes(b"video")
    future = int(time.time()) + 60
    expired = int(time.time()) - 1
    valid_signature = main._video_signature("signed.mp4", future)
    assert client.get(
        "/api/video/signed.mp4",
        params={"exp": future, "sig": "0" * 64},
    ).status_code == 403
    assert client.get(
        "/api/video/signed.mp4",
        params={"exp": expired, "sig": main._video_signature("signed.mp4", expired)},
    ).status_code == 403
    assert client.get(
        "/api/video/signed.mp4",
        params={"exp": future, "sig": "é" * 32},
    ).status_code == 403
    with pytest.raises(HTTPException) as exc:
        main.serve_signed_video("signed.mp4", exp=str(future), sig=[])
    assert exc.value.status_code == 403
    assert len(valid_signature) == 64


def test_cleanup_removes_only_old_unreferenced_mp4_and_temporary_files(client):
    now = time.time()
    _write_history({"free-user": [{"id": "keep", "filename": "referenced.mp4"}]})
    filenames = ["referenced.mp4", "orphan.mp4", "orphan.tmp", "recent.mp4"]
    for filename in filenames:
        path = main.OUTPUT_DIR / filename
        path.write_bytes(b"data")
        timestamp = now - 25 * 60 * 60 if filename != "recent.mp4" else now
        os.utime(path, (timestamp, timestamp))

    assert main._cleanup_orphaned_output_files(now=now) == 2
    assert (main.OUTPUT_DIR / "referenced.mp4").exists()
    assert not (main.OUTPUT_DIR / "orphan.mp4").exists()
    assert not (main.OUTPUT_DIR / "orphan.tmp").exists()
    assert (main.OUTPUT_DIR / "recent.mp4").exists()


def test_admin_diagnostics_are_admin_only_and_never_return_secret_values(client, monkeypatch, tmp_path):
    assert client.get("/api/admin/diagnostics").status_code == 403
    main.app.dependency_overrides[main.get_current_user] = lambda: ADMIN_USER
    quran_dir = tmp_path / "data"
    quran_dir.mkdir()
    (quran_dir / "quran.json").write_text(
        json.dumps({"surahs": {"1": {"n": 7}, "2": {"n": 3}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    monkeypatch.setattr(main, "_available_music_filenames", lambda: [])
    monkeypatch.setattr(main, "SUPABASE_URL", "https://secret.example")
    monkeypatch.setattr(main, "SUPABASE_KEY", "anon-secret")
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "service-secret")
    monkeypatch.setenv("VIDEO_URL_SECRET", "video-secret")
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ffmpeg version test\n"),
    )
    response = client.get("/api/admin/diagnostics")
    assert response.status_code == 200
    diagnostics = response.json()
    assert diagnostics["ffmpeg_version"] == "ffmpeg version test"
    assert diagnostics["quran_json_present"] is True
    assert diagnostics["quran_ayat_count"] == 10
    assert diagnostics["supabase_url_set"] is True
    assert diagnostics["supabase_anon_key_set"] is True
    assert diagnostics["supabase_service_role_key_set"] is True
    assert diagnostics["video_url_secret_set"] is True
    assert all(secret not in response.text for secret in (
        "https://secret.example", "anon-secret", "service-secret", "video-secret"
    ))


def test_upload_background_rejects_malformed_content_length(client):
    class Request:
        query_params = {"filename": "clip.mp4"}
        headers = {"content-length": "not-a-number"}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.upload_background(Request(), user=ADMIN_USER))
    assert exc.value.status_code == 400
