import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import main
import make
from test_hardening import client, FREE_USER  # noqa: F401  (client fixture)


class Resp:
    def __init__(self, status, size=2000):
        self.status_code, self.content = status, b"x" * size


@pytest.fixture
def audio(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "audio_cache_path", lambda kind, s, a, r: tmp_path / f"{kind}{s}_{a}.mp3")
    monkeypatch.setattr(make, "BACKOFF_BASE", 0)
    monkeypatch.setattr(make, "dur", lambda p: 1.0)
    monkeypatch.setattr(make, "save_ayah_duration", lambda *a, **k: None)
    monkeypatch.setattr(make, "cached_ayah_duration", lambda *a, **k: None)


def test_fetch_retries_503_then_succeeds(audio, monkeypatch):
    codes = iter([503, 503, 200])
    monkeypatch.setattr(make.requests, "get", lambda *a, **k: Resp(next(codes)))
    assert make.fetch_ayah("arabic", 93, 3).exists()


def test_fetch_error_names_ayah_and_status(audio, monkeypatch):
    monkeypatch.setattr(make.requests, "get", lambda *a, **k: Resp(503))
    with pytest.raises(RuntimeError) as exc:
        make.fetch_ayah("urdu", 93, 3)
    assert "93:3" in str(exc.value) and "503" in str(exc.value)
    assert "http" not in str(exc.value).lower().replace("http 503", "")


def test_fetch_offline_fails_cleanly(audio, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("no network")
    monkeypatch.setattr(make.requests, "get", boom)
    with pytest.raises(RuntimeError) as exc:
        make.fetch_ayah("arabic", 93, 1)
    assert "93:1" in str(exc.value) and "ConnectionError" in str(exc.value)


def test_job_error_uses_manifest_reason(client, monkeypatch):
    monkeypatch.setattr(main, "_prepare_ephemeral_media", lambda: None)
    monkeypatch.setattr(main, "_cleanup_orphaned_output_files", lambda: 0)
    manifest = main.CACHE_DIR / "m.json"

    def fake_render(*a, **k):
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps([{"index": 1, "error": "EveryAyah download failed for 93:3 (urdu): HTTP 503"}]))
        return SimpleNamespace(returncode=0, stderr="", stdout="")
    monkeypatch.setattr(main, "_run_render", fake_render)
    main.GENERATION_JOBS["j-err"] = {"owner_id": "free-user", "status": "queued", "manifest_path": str(manifest), "videos": []}
    main._run_generation_job("j-err", ["make.py"], "pick", manifest, main.CACHE_DIR / "w", user_id="free-user")
    job = main.GENERATION_JOBS["j-err"]
    assert job["status"] == "failed" and "93:3" in job["error"] and "503" in job["error"]


def test_make_exits_1_when_every_reel_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "RAQM_AVAILABLE", True)
    monkeypatch.setattr(make, "build", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom 93:1")))
    monkeypatch.setattr(make, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(make.sys, "argv", ["make.py", "--pick", "93:1-2", "--result-json", str(tmp_path / "r.json")])
    with pytest.raises(SystemExit) as exc:
        make.main()
    assert exc.value.code == 1
    assert "boom" in (tmp_path / "r.json").read_text()


def test_range_400_names_surah_and_count(client):
    for r in (
        client.get("/api/estimate", params={"surah": 108, "start": 1, "end": 11, "mode": "full"}),
        client.post("/api/generate", json={"mode": "pick", "verse": "108:1-11"}),
    ):
        assert r.status_code == 400
        assert r.json()["detail"] == "Surah 108 has only 3 ayat (you asked 1-11)."


def test_estimate_photo_cap_applies_with_background(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_VIDEO_SECONDS", 35)
    monkeypatch.setattr(main, "MAX_VIDEO_SECONDS_PHOTO", 60)
    p = {"surah": 93, "start": 1, "end": 2, "mode": "full", "length_limit": 60}
    assert client.get("/api/estimate", params=p).status_code == 400
    assert client.get("/api/estimate", params={**p, "background": "photos"}).status_code != 400


def test_blocked_clips_skipped():
    assert make.is_blocked_clip(Path("12419860_1080_1920_48fps.mp4"))
    assert make.is_blocked_clip(Path("x/16349470_1080_1920_30fps.mp4"))
    assert not make.is_blocked_clip(Path("15873690_1080_1920_60fps.mp4"))


def test_audio_chain_resamples_to_48k():
    assert "loudnorm=I=-18:TP=-2:LRA=7,aresample=48000" in Path(make.__file__).read_text(encoding="utf-8")
