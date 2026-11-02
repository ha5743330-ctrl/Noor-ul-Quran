"""Shared photo pool: admin upload/toggle/delete, pool selection, fallback, caps per background type."""
import io
import random
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main
import make

FREE = {"id": "free", "email": "f@x.y", "is_admin": False, "premium_access": False}
ADMIN = {"id": "admin", "email": "a@x.y", "is_admin": True, "premium_access": True}
PAYLOAD = {"mode": "pick", "verse": "1:1", "max_duration": 60, "quality": "fast"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setenv("DISABLE_CLEANUP_THREAD", "1")
    for name, value in {
        "OUTPUT_DIR": out, "HISTORY_FILE": out / "history.json", "CACHE_DIR": tmp_path / "cache",
        "PHOTO_CACHE_DIR": tmp_path / "photos", "SUPABASE_SERVICE_KEY": "", "SUPABASE_URL": "",
        "SUPABASE_KEY": "", "RAQM_AVAILABLE": True, "MAX_VIDEO_SECONDS": 35, "MAX_VIDEO_SECONDS_PHOTO": 60,
    }.items():
        monkeypatch.setattr(main, name, value)
    monkeypatch.setattr(main, "GENERATION_EXECUTOR", SimpleNamespace(submit=lambda *a, **k: None))
    main.GENERATION_JOBS.clear()
    main.DAILY_USAGE.clear()
    main.app.dependency_overrides[main.get_current_user] = lambda: ADMIN
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def _image_bytes(size=(1600, 900), fmt="PNG"):
    rng = random.Random(1)
    image = Image.merge("RGB", [Image.radial_gradient("L").resize(size)] * 3) if fmt == "PNG" else Image.new("RGB", size, (rng.randrange(256),) * 3)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def _upload(client, name="Sunset Photo.png", data=None):
    return client.post(f"/api/admin/photos?filename={name}", content=data or _image_bytes())


def test_upload_is_cover_cropped_and_small(client):
    response = _upload(client)
    assert response.status_code == 200, response.text
    name = response.json()["filename"]
    saved = main.PHOTO_CACHE_DIR / name
    assert saved.stat().st_size <= main.PHOTO_MAX_BYTES
    with Image.open(saved) as image:
        assert image.size == (1080, 1920) and image.format == "JPEG"
    listing = client.get("/api/admin/photos").json()
    assert [p["filename"] for p in listing] == [name] and listing[0]["enabled"]
    assert client.get(listing[0]["thumb_url"]).status_code == 200


def test_upload_validation(client):
    assert _upload(client, "x.png", b"not an image").status_code == 400
    assert _upload(client, "tiny.png", _image_bytes((100, 100))).status_code == 400
    assert client.post("/api/admin/photos?filename=x.png", content=b"").status_code == 400
    gif = io.BytesIO()
    Image.new("RGB", (800, 800)).save(gif, format="GIF")
    assert _upload(client, "a.gif", gif.getvalue()).status_code == 400
    assert not list(main.PHOTO_CACHE_DIR.glob("*.jpg"))


def test_photo_routes_are_admin_only(client):
    name = _upload(client).json()["filename"]
    main.app.dependency_overrides[main.get_current_user] = lambda: FREE
    assert client.get("/api/admin/photos").status_code == 403
    assert _upload(client, "b.png").status_code == 403
    assert client.patch(f"/api/admin/photos/{name}", json={"enabled": False}).status_code == 403
    assert client.delete(f"/api/admin/photos/{name}").status_code == 403
    assert client.get(f"/api/photo/{name}").status_code == 403  # unsigned


def test_pool_selection_toggle_delete_and_fallback(client):
    assert main._photo_pool() == [] and main._fallback_photos()  # empty pool -> bundled calm background
    first = _upload(client, "one.png").json()["filename"]
    second = _upload(client, "two.png").json()["filename"]
    assert {p.name for p in main._photo_pool()} == {first, second}
    assert client.patch(f"/api/admin/photos/{first}", json={"enabled": False}).status_code == 200
    assert [p.name for p in main._photo_pool()] == [second]
    assert client.get("/api/admin/photos").json()[0]["enabled"] is False
    assert client.delete(f"/api/admin/photos/{second}").status_code == 200
    assert main._photo_pool() == []  # first is disabled, second deleted


def test_ayah_image_background_comes_from_pool(client, monkeypatch):
    name = _upload(client, "pool.png").json()["filename"]
    chosen = {}
    import make as renderer

    def fake_render(arabic, english, reference, background_path, output_path):
        chosen["bg"] = background_path
        Image.new("RGB", (10, 10)).save(output_path, format="PNG")

    monkeypatch.setattr(renderer, "render_ayah_image", fake_render)
    response = client.post("/api/generate-image", json={})
    assert response.status_code in (200, 201), response.text
    assert chosen["bg"].name == name


def test_caps_per_background_type(client, monkeypatch):
    monkeypatch.setenv("RENDER_MEMORY_PROFILE", "low")
    access = client.get("/api/access").json()
    assert access["max_video_seconds"] == 35 and access["max_video_seconds_photo"] == 60
    assert client.post("/api/generate", json={**PAYLOAD, "background_type": "video"}).status_code == 400
    ok = client.post("/api/generate", json={**PAYLOAD, "background_type": "photos"})
    assert ok.status_code == 202, ok.text
    main.GENERATION_JOBS.clear()
    assert client.post("/api/generate", json={**PAYLOAD, "background_type": "nope"}).status_code == 400
    assert main._cap_for("photos") == 60 and main._cap_for("video") == 35 and main._cap_for(None) == 35
    assert main._estimate_render_mb(30, "high", "photos") > main._estimate_render_mb(30, "high", "video") - 100


def test_photo_command_carries_background_flags(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "GENERATION_EXECUTOR", SimpleNamespace(submit=lambda fn, job_id, cmd, *a, **k: seen.update(cmd=cmd)))
    assert client.post("/api/generate", json={**PAYLOAD, "max_duration": 30, "background_type": "photos"}).status_code == 202
    assert "--photo-list" in seen["cmd"] and seen["cmd"][seen["cmd"].index("--background") + 1] == "photos"
    seen.clear()
    main.GENERATION_JOBS.clear()
    assert client.post("/api/generate", json={**PAYLOAD, "max_duration": 30}).status_code == 202
    assert "--background" not in seen["cmd"]


def test_photo_playlist_segments_and_no_repeat():
    photos = [f"p{i}" for i in range(4)]
    playlist = make.photo_playlist(photos, 30.0, random.Random(2))
    assert len(playlist) == 4
    assert sum(seg for _, _, seg in playlist) == pytest.approx(30.0)
    assert all(a[0] != b[0] for a, b in zip(playlist, playlist[1:]))
    assert make.photo_playlist(photos, 5.0, random.Random(1))[0][2] == pytest.approx(5.0)
    with pytest.raises(RuntimeError):
        make.photo_playlist([], 30.0, random.Random(1))


def test_photo_background_list_sums_to_total(tmp_path, monkeypatch):
    monkeypatch.setattr(make, "W", 108)
    monkeypatch.setattr(make, "H", 192)
    photos = []
    for i in range(3):
        path = tmp_path / f"in{i}.jpg"
        Image.new("RGB", (300, 400), (i * 80, 40, 90)).save(path)
        photos.append(path)
    listing = make.photo_background_list(photos, 20.0, random.Random(1), tmp_path)
    durations = [float(line.split()[1]) for line in listing.read_text().splitlines() if line.startswith("duration")]
    assert sum(durations) == pytest.approx(20.0, abs=0.05) and min(durations) > 0
    with Image.open(tmp_path / "ph0.jpg") as frame:
        assert frame.size == (108, 192)
