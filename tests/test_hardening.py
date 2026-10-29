import asyncio
import io
import json
import os
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image

import fetch_assets
import main
import make


FREE_USER = {"id": "free-user", "is_admin": False, "premium_access": False}
ADMIN_USER = {"id": "admin-user", "is_admin": True, "premium_access": True}


def _quran_editions():
    qpc_hafs = {}
    urdu = []
    english = []
    for surah_number in range(1, 115):
        qpc_hafs[str(surah_number)] = {"1": "عَرَبِيّ"}
        urdu.append({
            "number": surah_number,
            "name": f"سورة {surah_number}",
            "englishName": f"Surah {surah_number}",
            "ayahs": [{"numberInSurah": 1, "text": "اردو"}],
        })
        english.append({
            "number": surah_number,
            "name": f"سورة {surah_number}",
            "englishName": f"Surah {surah_number}",
            "ayahs": [{"numberInSurah": 1, "text": "English"}],
        })
    return qpc_hafs, urdu, english


def test_quran_data_builder_aligns_all_three_editions():
    arabic, urdu, english = _quran_editions()

    result = fetch_assets.build_quran_data(arabic, urdu, english)

    assert len(result["surahs"]) == 114
    assert "Marmaduke William Pickthall" in result["english_translation"]
    assert "en.pickthall" in result["english_translation"]
    assert result["surahs"]["1"]["arabic"]["1"] == "عَرَبِيّ"
    assert result["surahs"]["1"]["ayahs"]["1"] == "اردو"
    assert result["surahs"]["1"]["english_ayahs"]["1"] == "English"


@pytest.mark.parametrize("edition_index", [0, 1, 2])
def test_quran_data_builder_rejects_missing_surahs(edition_index):
    editions = _quran_editions()
    if edition_index == 0:
        editions[edition_index].pop("1")
    else:
        editions[edition_index].pop()

    with pytest.raises(RuntimeError, match="114 surahs"):
        fetch_assets.build_quran_data(*editions)


def test_quran_data_builder_rejects_misaligned_or_empty_translation():
    editions = _quran_editions()
    editions[2][0]["ayahs"][0]["numberInSurah"] = 2
    with pytest.raises(RuntimeError, match="surah 1, ayah 1"):
        fetch_assets.build_quran_data(*editions)

    editions = _quran_editions()
    editions[2][0]["ayahs"][0]["text"] = " "
    with pytest.raises(RuntimeError, match="surah 1, ayah 1"):
        fetch_assets.build_quran_data(*editions)


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
    monkeypatch.setattr(main, "CLIP_CACHE_DIR", tmp_path / "cache" / "backgrounds")
    monkeypatch.setattr(main, "MUSIC_CACHE_DIR", tmp_path / "cache" / "music")
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
    assert client.post("/api/generate-image", json={}).status_code == 401
    main.app.dependency_overrides[main.get_current_user] = lambda: FREE_USER


def test_featured_ayah_selection_supports_specific_and_random_references(monkeypatch):
    monkeypatch.setattr(main.random, "choice", lambda values: values[-1])
    monkeypatch.setattr(main, "LAST_FEATURED_AYAH", None)

    assert main._select_featured_ayah("2:152") == "2:152"
    random_ayah = main._select_featured_ayah()
    assert random_ayah == main._featured_ayah_references()[-1]
    assert random_ayah != "2:152"
    assert main._select_featured_ayah() != random_ayah


def test_featured_ayah_list_is_valid_and_under_word_limit():
    featured = json.loads((main.BASE_DIR / "data" / "featured_ayahs.json").read_text(encoding="utf-8"))
    quran = json.loads((main.BASE_DIR / "data" / "quran.json").read_text(encoding="utf-8"))

    assert "Needs human review" in featured["status"]
    assert len(featured["ayahs"]) == 96
    assert len(set(featured["ayahs"])) == 96
    assert not {"18:24", "12:64", "16:126", "62:10"}.intersection(featured["ayahs"])
    for reference in featured["ayahs"]:
        surah, ayah = reference.split(":")
        english = quran["surahs"][surah]["english_ayahs"][ayah]
        assert len(english.split()) <= 40


@pytest.mark.parametrize("ayah", ["not-an-ayah", "1:8", "114:7"])
def test_ayah_image_api_rejects_unlisted_or_invalid_reference(client, monkeypatch, ayah):
    monkeypatch.setattr(main, "RAQM_AVAILABLE", True)

    response = client.post("/api/generate-image", json={"ayah": ayah})

    assert response.status_code == 400
    assert response.json()["detail"] == "Ayah must be in the curated featured list."


def test_ayah_image_api_uses_raqm_guard(client, monkeypatch):
    monkeypatch.setattr(main, "RAQM_AVAILABLE", False)

    response = client.post("/api/generate-image", json={"ayah": "2:152"})

    assert response.status_code == 503
    assert "lacks RAQM text shaping" in response.json()["detail"]


def test_ayah_image_api_renders_and_returns_downloadable_image(client, monkeypatch):
    monkeypatch.setattr(main, "RAQM_AVAILABLE", True)
    images_dir = main.BACKGROUND_DIR / "images"
    images_dir.mkdir()
    Image.new("RGB", (50, 80), (20, 30, 40)).save(images_dir / "background.png")
    rendered = {}

    def render_image(arabic, english, label, background, destination):
        rendered["args"] = (arabic, english, label, background)
        destination.write_bytes(b"rendered-png")

    monkeypatch.setattr(make, "render_ayah_image", render_image)
    response = client.post("/api/generate-image", json={"ayah": "2:152"})

    assert response.status_code == 200
    result = response.json()
    assert result["reference"] == "2:152"
    assert result["image_data"] == "data:image/png;base64,cmVuZGVyZWQtcG5n"
    assert result["filename"].endswith(".png")
    assert rendered["args"][2] == "Surah Al-Baqara 2:152"
    assert rendered["args"][3].name == "background.png"


def test_startup_logs_raqm_unavailable(monkeypatch, caplog):
    monkeypatch.setattr(main, "RAQM_AVAILABLE", True)
    monkeypatch.setattr(main.features, "check", lambda feature: False)

    main.cleanup_output_on_startup()

    assert not main.RAQM_AVAILABLE
    assert main.RAQM_REQUIRED_ERROR in caplog.text


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
        {"mode": "pick", "verse": "114:1-9"},
        {"mode": "pick", "verse": "1:1", "music": "../outside.mp3"},
        {"mode": "pick", "verse": "1:1", "music": "..\\outside.mp3"},
        {"mode": "pick", "verse": "1:1", "music": ""},
        {"mode": "pick", "verse": "1:1", "music": "not-listed.mp3"},
        {"mode": "pick", "verse": "1:1", "music": "bad\x00name.mp3"},
        {"mode": "pick", "verse": "1:1", "reciter_id": "unknown"},
        {"mode": "pick", "verse": "1:1", "urdu_voice_id": "unknown"},
    ],
)
def test_invalid_generation_requests_are_rejected(client, payload):
    assert client.post("/api/generate", json=payload).status_code == 400


@pytest.mark.parametrize(
    ("content_mode", "expected_flag"),
    [
        ("full", None),
        ("urdu_only", "--urdu-only"),
        ("arabic_only", "--arabic-only"),
    ],
)
def test_generation_selects_renderer_flag_for_each_content_mode(
    client, monkeypatch, content_mode, expected_flag
):
    monkeypatch.setattr(main, "RAQM_AVAILABLE", True)
    captured = {}

    class Executor:
        def submit(self, *args):
            captured["args"] = args

    monkeypatch.setattr(main, "GENERATION_EXECUTOR", Executor())
    response = client.post(
        "/api/generate",
        json={
            "mode": "pick",
            "verse": "1:1",
            "content_mode": content_mode,
        },
    )

    assert response.status_code == 202
    command = captured["args"][2]
    assert command.count("--urdu-only") == int(expected_flag == "--urdu-only")
    assert command.count("--arabic-only") == int(expected_flag == "--arabic-only")
    assert command[command.index("--reciter") + 1] == "alafasy"
    if content_mode != "arabic_only":
        assert command[command.index("--urdu-voice") + 1] == "shamshad_ali_khan"


def test_arabic_only_ignores_invalid_urdu_voice(client, monkeypatch):
    monkeypatch.setattr(main, "RAQM_AVAILABLE", True)
    captured = {}

    class Executor:
        def submit(self, *args):
            captured["args"] = args

    monkeypatch.setattr(main, "GENERATION_EXECUTOR", Executor())
    response = client.post(
        "/api/generate",
        json={
            "mode": "pick",
            "verse": "1:1",
            "content_mode": "arabic_only",
            "urdu_voice_id": "not-a-voice",
        },
    )

    assert response.status_code == 202
    command = captured["args"][2]
    assert "--urdu-voice" not in command


def test_reciters_endpoint_returns_choices_and_defaults(client):
    response = client.get("/api/reciters")

    assert response.status_code == 200
    assert response.json()["defaults"] == {
        "ar": "alafasy",
        "ur": "shamshad_ali_khan",
    }
    assert len([r for r in response.json()["reciters"] if r["language"] == "ar"]) == 5
    assert len([r for r in response.json()["reciters"] if r["language"] == "ur"]) == 1


def test_arabic_only_duration_estimate_uses_arabic_audio(client, monkeypatch):
    captured = {}

    def plan_duration(*args):
        captured["args"] = args
        return {
            "estimated_seconds": 7,
            "fits_up_to": 1,
            "tolerance_fits_up_to": 1,
            "total_count": 1,
            "range_seconds": 7,
        }

    monkeypatch.setattr(make, "plan_duration", plan_duration)
    response = client.get(
        "/api/estimate",
        params={
            "surah": "1",
            "start": "1",
            "end": "1",
            "mode": "arabic_only",
            "length_limit": "30",
        },
    )

    assert response.status_code == 200
    assert captured["args"][4] == "arabic_only"
    assert response.json()["estimated_seconds"] == 7


@pytest.mark.parametrize(
    ("content_mode", "expected_audio_kinds", "expected_seconds"),
    [
        ("full", ["urdu", "arabic"], 12.35),
        ("urdu_only", ["urdu"], 5),
        ("arabic_only", ["arabic"], 7),
    ],
)
def test_duration_planner_uses_audio_for_selected_content_mode(
    monkeypatch, content_mode, expected_audio_kinds, expected_seconds
):
    measured_kinds = []
    monkeypatch.setattr(make, "cached_ayah_duration", lambda *args: None)

    def measure_audio(surah, ayah, kind, reciter_id=None):
        measured_kinds.append(kind)
        return {"arabic": 7, "urdu": 5}[kind]

    monkeypatch.setattr(make, "measure_ayah_audio", measure_audio)
    quran = {"surahs": {"1": {"n": 1}}}
    plan = make.plan_duration(1, 1, 1, 30, content_mode, quran)

    assert measured_kinds == expected_audio_kinds
    assert plan["range_seconds"] == pytest.approx(expected_seconds)


def test_arabic_only_reel_uses_english_text_without_urdu_audio(monkeypatch):
    fetched_audio = []
    monkeypatch.setattr(make, "_clip_cache", {})

    def fetch_audio(kind, surah, ayah, reciter_id=None):
        fetched_audio.append(kind)
        return f"{kind}.mp3"

    monkeypatch.setattr(make, "fetch_ayah", fetch_audio)
    monkeypatch.setattr(make, "cached_ayah_duration", lambda *args: 7)
    clip = make.ayah_clip(1, 1, "arabic_only")

    assert fetched_audio == ["arabic"]
    assert clip["segs"] == [("arabic.mp3", 7, 0.0)]
    assert clip["len"] == 7

    monkeypatch.setattr(
        make,
        "plan_duration",
        lambda *args: {
            "fit_items": [{
                "ayah": 1, "ar_dur": 7, "u0": 0, "ur_dur": 0, "len": 7,
            }],
            "fits_up_to": 1,
        },
    )
    quran = {
        "surahs": {
            "1": {
                "n": 1,
                "name": "سورة الفاتحة",
                "arabic": {"1": "الْحَمْدُ لِلَّهِ"},
                "english_ayahs": {"1": "All praise is due to Allah."},
                "ayahs": {"1": "اردو ترجمہ"},
            }
        }
    }
    plan = make.plan_reel(quran, 1, 1, 1, 30, "arabic_only")

    assert plan["items"][0]["english"] == "All praise is due to Allah."
    assert plan["items"][0]["text"] == ""


def test_audio_cache_paths_and_durations_are_reciter_specific(monkeypatch, tmp_path):
    monkeypatch.setattr(make, "DURATION_CACHE_FILE", tmp_path / "durations.json")
    monkeypatch.setattr(make, "_duration_cache", None)
    alafasy_path = make.audio_cache_path("arabic", 112, 1, "alafasy")
    husary_path = make.audio_cache_path("arabic", 112, 1, "husary")
    assert alafasy_path != husary_path
    assert alafasy_path.parts[-3:] == ("ar", "alafasy", "112001.mp3")
    assert husary_path.parts[-3:] == ("ar", "husary", "112001.mp3")

    make.save_ayah_duration(112, 1, "arabic", 2.5, "alafasy")
    make.save_ayah_duration(112, 1, "arabic", 3.75, "husary")

    assert make.cached_ayah_duration(112, 1, "arabic", "alafasy") == 2.5
    assert make.cached_ayah_duration(112, 1, "arabic", "husary") == 3.75


def test_bilingual_video_frame_attributes_pickthall_translation(monkeypatch, tmp_path):
    from PIL import ImageFont

    base = tmp_path / "base.png"
    Image.new("RGBA", (1080, 1920), (0, 0, 0, 0)).save(base)
    font_path = make.FONTS["amiri_regular"]
    font = ImageFont.truetype(str(font_path), 30)
    captured_text = []
    monkeypatch.setattr(make, "load_font", lambda *args: font)
    monkeypatch.setattr(
        make,
        "put",
        lambda draw, position, text, *args, **kwargs: captured_text.append(text),
    )

    make.chunk_bilingual_png(
        tmp_path / "attributed.png",
        ["Arabic ayah"],
        ["English translation"],
        font,
        font,
        base,
        {},
    )

    assert "Translation: Mohammed Marmaduke William Pickthall" in captured_text


def test_dropdown_music_filename_is_the_only_filename_passed_to_renderer(client, monkeypatch):
    monkeypatch.setattr(main, "RAQM_AVAILABLE", True)
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
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation is unavailable on this platform: {exc}")
    assert main._available_music_filenames() == ["linked.mp3"]
    with pytest.raises(HTTPException) as exc:
        main._validate_music_filename("linked.mp3")
    assert exc.value.status_code == 400


def test_history_keeps_all_generated_videos_without_pruning(client, monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "configured")
    deleted_objects = []
    monkeypatch.setattr(main, "_delete_media_objects", lambda paths: deleted_objects.extend(paths))
    videos = []
    for index in range(10):
        filename = f"video{index}.mp4"
        (main.OUTPUT_DIR / filename).write_bytes(b"video")
        videos.append({"filename": filename, "object_path": f"videos/object-{index}.mp4"})

    main._record_video_history("free-user", videos, "full", "balanced", {})
    history = json.loads(main.HISTORY_FILE.read_text(encoding="utf-8"))
    entries = history["users"]["free-user"]
    assert len(entries) == 10
    assert all((main.OUTPUT_DIR / f"video{index}.mp4").exists() for index in range(10))
    assert deleted_objects == []
    assert all(entry["object_path"] for entry in entries)
    response = client.get("/api/history")
    assert response.status_code == 200
    assert len(response.json()["videos"]) == 10
    assert all(video["has_file"] for video in response.json()["videos"])


def test_batch_download_returns_owner_scoped_zip_for_completed_reels(client):
    filenames = ["batch_001.mp4", "batch_002.mp4"]
    for index, filename in enumerate(filenames):
        (main.OUTPUT_DIR / filename).write_bytes(f"reel-{index + 1}".encode())
    main.GENERATION_JOBS["batch-job"] = {
        "owner_id": "free-user",
        "status": "completed",
        "videos": [{"filename": filename} for filename in filenames],
    }
    _write_history({
        "free-user": [
            {"id": f"entry-{index}", "filename": filename, "download_count": 0}
            for index, filename in enumerate(filenames)
        ]
    })

    main.app.dependency_overrides[main.get_current_user] = lambda: {"id": "another-user"}
    assert client.get("/api/generate/batch-job/download-url").status_code == 404
    main.app.dependency_overrides[main.get_current_user] = lambda: FREE_USER

    url_response = client.get("/api/generate/batch-job/download-url")
    assert url_response.status_code == 200
    assert url_response.json()["video_count"] == 2
    archive_response = client.get(url_response.json()["url"])
    assert archive_response.status_code == 200
    assert archive_response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
        assert archive.namelist() == filenames
        assert [archive.read(filename) for filename in filenames] == [b"reel-1", b"reel-2"]

    history = json.loads(main.HISTORY_FILE.read_text(encoding="utf-8"))
    assert all(entry["download_count"] == 1 for entry in history["users"]["free-user"])


def test_batch_download_url_waits_for_generation_completion(client):
    main.GENERATION_JOBS["batch-job"] = {
        "owner_id": "free-user",
        "status": "processing",
        "videos": [],
    }
    response = client.get("/api/generate/batch-job/download-url")
    assert response.status_code == 409


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
        main,
        "_run_render",
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


def test_cleanup_preserves_all_mp4_files_and_removes_old_temporary_files(client):
    now = time.time()
    _write_history({"free-user": [{"id": "keep", "filename": "referenced.mp4"}]})
    filenames = ["referenced.mp4", "orphan.mp4", "orphan.tmp", "recent.mp4", "recent.part"]
    for filename in filenames:
        path = main.OUTPUT_DIR / filename
        path.write_bytes(b"data")
        timestamp = now - 25 * 60 * 60 if filename in {"referenced.mp4", "orphan.mp4", "orphan.tmp"} else now
        os.utime(path, (timestamp, timestamp))

    assert main._cleanup_orphaned_output_files(now=now) == 1
    assert (main.OUTPUT_DIR / "referenced.mp4").exists()
    assert (main.OUTPUT_DIR / "orphan.mp4").exists()
    assert not (main.OUTPUT_DIR / "orphan.tmp").exists()
    assert (main.OUTPUT_DIR / "recent.mp4").exists()
    assert (main.OUTPUT_DIR / "recent.part").exists()


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


def test_ffmpeg_cpu_render_uses_single_codec_thread(monkeypatch, tmp_path):
    monkeypatch.delenv("RENDER_MEMORY_PROFILE", raising=False)
    captured = {}
    monkeypatch.setattr(make, "detect_hardware_encoder", lambda: "")
    monkeypatch.setattr(make, "run", lambda command, **kwargs: captured.update(command=command))

    make.encode_video(["ffmpeg", "-i", "input.mp4"], tmp_path / "output.mp4")

    command = captured["command"]
    assert command[command.index("-threads") + 1] == "1"
    assert command[command.index("-preset") + 1] == "medium"
    assert command[command.index("-crf") + 1] == "18"
    assert command[command.index("-tune") + 1] == "film"
    assert command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert command[command.index("-color_range") + 1] == "tv"
    assert command[-1] == str(tmp_path / "output.mp4")
    assert make.FFMPEG_FILTER_THREADS == (
        "-filter_threads", "1", "-filter_complex_threads", "1"
    )


def test_ffmpeg_low_memory_profile_keeps_low_memory_encoder_settings(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("RENDER_MEMORY_PROFILE", "low")
    captured = {}
    monkeypatch.setattr(make, "detect_hardware_encoder", lambda: "")
    monkeypatch.setattr(
        make, "run", lambda command, **kwargs: captured.update(command=command)
    )

    make.encode_video(["ffmpeg", "-i", "input.mp4"], tmp_path / "output.mp4")

    command = captured["command"]
    assert command[command.index("-preset") + 1] == "ultrafast"
    assert command[command.index("-crf") + 1] == "24"
    assert "-tune" not in command


def test_video_header_uses_source_bismillah_and_amiri_for_title_and_label(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(make, "ROOT", tmp_path)
    monkeypatch.setattr(make, "W", 1080)
    monkeypatch.setattr(make, "H", 1920)
    fonts = []
    texts = []
    monkeypatch.setattr(
        make, "load_font", lambda name, *args, **kwargs: fonts.append(name) or name
    )
    monkeypatch.setattr(
        make, "put", lambda draw, position, text, font, *args, **kwargs: texts.append((text, font))
    )

    make.base_overlay(tmp_path / "with-bismillah.png", "title", "label", True)

    assert (make.QPC_BISMILLAH_TEXT, make.QURAN_ARABIC_FONT) in texts
    assert fonts[:3] == [make.QURAN_ARABIC_FONT, "amiri_bold", "amiri_bold"]

    fonts.clear()
    texts.clear()
    make.base_overlay(tmp_path / "without-bismillah.png", "title", "label", False)
    assert make.QPC_BISMILLAH_TEXT not in [text for text, _font in texts]


@pytest.mark.parametrize("content_mode", ["full", "urdu_only", "arabic_only"])
def test_generation_refuses_script_modes_without_raqm(client, monkeypatch, content_mode):
    monkeypatch.setattr(main, "RAQM_AVAILABLE", False)
    response = client.post(
        "/api/generate",
        json={
            "mode": "pick",
            "verse": "1:1",
            "content_mode": content_mode,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == main.RAQM_REQUIRED_ERROR


def test_arabic_only_generation_rejects_music_in_api(client):
    response = client.post(
        "/api/generate",
        json={
            "mode": "pick",
            "verse": "1:1",
            "content_mode": "arabic_only",
            "music": "nasheed.mp3",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Background music is not available in Arabic-only mode."


@pytest.mark.parametrize(
    ("quality", "expected"),
    [
        ("balanced", (480, 854, 20)),
        ("high", (540, 960, 20)),
    ],
)
def test_render_memory_profile_uses_smaller_output_on_render(monkeypatch, quality, expected):
    monkeypatch.setenv("RENDER_MEMORY_PROFILE", "low")
    make.set_video_quality(quality)
    assert (make.W, make.H, make.FPS) == expected


def test_render_quality_defaults_are_unchanged_without_memory_profile(monkeypatch):
    monkeypatch.delenv("RENDER_MEMORY_PROFILE", raising=False)
    make.set_video_quality("balanced")
    assert (make.W, make.H, make.FPS) == (720, 1280, 24)
    make.set_video_quality("high")
    assert (make.W, make.H, make.FPS) == (1080, 1920, 30)


class _Upload:
    def __init__(self, name, body, length=None):
        self.query_params = {"filename": name}
        self.headers = {"content-length": str(len(body) if length is None else length)}
        self._body = body

    async def stream(self):
        yield self._body


MP4 = b"\x00\x00\x00ftypmp42" + b"\x00" * 8


def test_music_upload_goes_to_supabase_music_folder_and_cache(client, monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "configured")
    uploads = []
    monkeypatch.setattr(
        main, "_upload_media_file",
        lambda path, object_path, content_type, bucket=main.MEDIA_BUCKET: uploads.append((path.read_bytes(), object_path, content_type, bucket)),
    )
    response = asyncio.run(main.upload_background_music(_Upload("nasheed.mp3", b"audio"), user=ADMIN_USER))
    assert response == {"filename": "nasheed.mp3", "size": 5}
    assert uploads == [(b"audio", "music/nasheed.mp3", "audio/mpeg", main.MEDIA_BUCKET)]
    assert (main.MUSIC_CACHE_DIR / "nasheed.mp3").read_bytes() == b"audio"


def test_background_upload_goes_to_backgrounds_bucket_and_cache(client, monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "configured")
    uploads = []
    monkeypatch.setattr(
        main, "_upload_media_file",
        lambda path, object_path, content_type, bucket=main.MEDIA_BUCKET: uploads.append((object_path, bucket)),
    )
    asyncio.run(main.upload_background(_Upload("clip.mp4", MP4), user=ADMIN_USER))
    assert uploads == [("clip.mp4", main.BACKGROUNDS_BUCKET)]
    assert (main.CLIP_CACHE_DIR / "clip.mp4").read_bytes() == MP4


@pytest.mark.parametrize("name,body,status", [
    ("clip.mov", MP4, 400),
    ("clip.mp3", MP4, 400),
    ("../clip.mp4", MP4, 400),
    ("clip.mp4", b"not a video at all", 400),
    ("clip.mp4", b"", 400),
])
def test_background_upload_rejects_bad_type_and_content(client, name, body, status):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.upload_background(_Upload(name, body), user=ADMIN_USER))
    assert exc.value.status_code == status
    assert not list(main.BACKGROUND_DIR.glob("*.upload")) and not list(main.BACKGROUND_DIR.glob("*.mp4"))


def test_uploads_reject_over_50mb_by_header_and_by_stream(client):
    for upload in (main.upload_background, main.upload_background_music):
        name = "a.mp4" if upload is main.upload_background else "a.mp3"
        with pytest.raises(HTTPException) as exc:
            asyncio.run(upload(_Upload(name, MP4, length=main.MAX_BACKGROUND_BYTES + 1), user=ADMIN_USER))
        assert exc.value.status_code == 413

    class Big(_Upload):
        async def stream(self):
            yield b"\x00" * (main.MAX_BACKGROUND_BYTES + 1)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.upload_background(Big("a.mp4", b"", length=1), user=ADMIN_USER))
    assert exc.value.status_code == 413


def test_media_admin_routes_are_admin_only(client):
    for method, path in (
        ("get", "/api/admin/backgrounds"),
        ("post", "/api/admin/backgrounds?filename=a.mp4"),
        ("delete", "/api/admin/backgrounds/a.mp4"),
        ("get", "/api/admin/music"),
        ("post", "/api/admin/music?filename=a.mp3"),
        ("delete", "/api/admin/music/a.mp3"),
    ):
        assert getattr(client, method)(path).status_code == 403, path


def test_admin_delete_removes_bucket_object_and_cached_copy(client, monkeypatch):
    main.app.dependency_overrides[main.get_current_user] = lambda: ADMIN_USER
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "configured")
    removed = []
    monkeypatch.setattr(main, "_remove_bucket_object", lambda bucket, path: removed.append((bucket, path)))
    for directory, name in ((main.CLIP_CACHE_DIR, "clip.mp4"), (main.MUSIC_CACHE_DIR, "song.mp3")):
        directory.mkdir(parents=True)
        (directory / name).write_bytes(b"x")
    assert client.delete("/api/admin/backgrounds/clip.mp4").status_code == 200
    assert client.delete("/api/admin/music/song.mp3").status_code == 200
    assert removed == [(main.BACKGROUNDS_BUCKET, "clip.mp4"), (main.MEDIA_BUCKET, "music/song.mp3")]
    assert not (main.CLIP_CACHE_DIR / "clip.mp4").exists() and not (main.MUSIC_CACHE_DIR / "song.mp3").exists()
    with pytest.raises(HTTPException):
        main.delete_background_music("../x.mp3", user=ADMIN_USER)
    assert len(removed) == 2


def test_empty_local_dirs_fall_back_cleanly(client, monkeypatch):
    main.app.dependency_overrides[main.get_current_user] = lambda: ADMIN_USER
    assert client.get("/api/admin/backgrounds").json() == []
    assert client.get("/api/music-list").json() == {"music_files": []}
    main._sync_render_media("x.mp3")  # no service key: no-op

    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "configured")
    monkeypatch.setattr(main, "_list_media_objects", lambda prefix, bucket=main.MEDIA_BUCKET: [])
    assert client.get("/api/music-list").json() == {"music_files": []}


def test_sync_render_media_streams_into_cache_and_skips_unchanged(client, monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "configured")

    def listing(prefix, bucket=main.MEDIA_BUCKET):
        if bucket == main.BACKGROUNDS_BUCKET:
            return [{"name": "clip.mp4", "metadata": {"size": 5}}, {"name": "notes.txt"}]
        return [{"name": "song.mp3", "metadata": {"size": 5}}]

    downloads = []

    def download(object_path, local_path, bucket=main.MEDIA_BUCKET):
        downloads.append((bucket, object_path))
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(b"12345")
        return True

    monkeypatch.setattr(main, "_list_media_objects", listing)
    monkeypatch.setattr(main, "_download_media_file", download)
    main._sync_render_media("song.mp3")
    assert downloads == [(main.BACKGROUNDS_BUCKET, "clip.mp4"), (main.MEDIA_BUCKET, "music/song.mp3")]
    main._sync_render_media("song.mp3")
    assert len(downloads) == 2  # cached, same size
    (main.CLIP_CACHE_DIR / "clip.mp4").write_bytes(b"1")  # changed size -> re-download
    main._sync_render_media(None)
    assert len(downloads) == 3


def test_render_timeout_kills_process_and_frees_queue(client, monkeypatch):
    monkeypatch.setattr(main, "_prepare_ephemeral_media", lambda: None)
    monkeypatch.setattr(main, "_cleanup_orphaned_output_files", lambda: 0)
    manifest = main.CACHE_DIR / "batch-timeout.json"
    main.GENERATION_JOBS["job-timeout"] = {
        "owner_id": "free-user", "status": "queued", "manifest_path": str(manifest), "videos": [],
    }
    started = time.monotonic()
    main._run_generation_job(
        "job-timeout",
        [main.sys.executable, "-c", "import time; time.sleep(30)"],
        "pick",
        manifest,
        main.CACHE_DIR / "tmp" / "timeout",
        user_id="free-user",
        timeout=1,
    )
    assert time.monotonic() - started < 15
    assert main.GENERATION_JOBS["job-timeout"]["status"] == "failed"
    assert "was stopped" in main.GENERATION_JOBS["job-timeout"]["error"]
    assert not main.GENERATION_LOCK.locked()


def test_ayah_range_is_capped_for_estimate_and_generate(client):
    too_many = f"2:1-{main.MAX_AYAHS_PER_REQUEST + 1}"
    response = client.post("/api/generate", json={"mode": "pick", "verse": too_many})
    assert response.status_code == 400
    response = client.get("/api/estimate", params={
        "surah": 2, "start": 1, "end": main.MAX_AYAHS_PER_REQUEST + 1, "mode": "full",
    })
    assert response.status_code == 400


def test_estimate_requires_exact_reciter_ids(client):
    params = {"surah": 1, "start": 1, "end": 1, "mode": "full", "length_limit": 60}
    assert client.get("/api/estimate", params={**params, "reciter_id": "Alafasy"}).status_code == 400
    assert client.get("/api/estimate", params={**params, "urdu_voice_id": "alafasy"}).status_code == 400
    assert client.get("/api/estimate", params={**params, "reciter_id": "shamshad_ali_khan"}).status_code == 400


def test_quran_text_font_is_rejected(client):
    response = client.post("/api/generate", json={
        "mode": "pick", "verse": "1:1", "styles": {"arabic": {"font": "amiri_bold"}},
    })
    assert response.status_code == 400


def test_render_arabic_always_comes_from_verified_qpc(monkeypatch):
    monkeypatch.setattr(make, "ayah_clip", lambda *args, **kwargs: {"dur": 1.0})
    monkeypatch.setattr(make, "plan_duration", lambda *args, **kwargs: {"fit_items": [{"ayah": 2}]})
    quran = {"surahs": {"1": {"ayahs": {"2": "urdu"}, "arabic": {"2": "hand typed"}}}}
    plan = make.plan_reel(quran, 1, 2, 2, 30, "full")
    assert plan["items"][0]["arabic"] == make.QPC_QURAN["surahs"]["1"]["2"]


def test_verse_studio_routes_are_gone(client):
    main.app.dependency_overrides[main.get_current_user] = lambda: ADMIN_USER
    for method, path in (
        ("get", "/api/admin/verses/1/1"),
        ("put", "/api/admin/verses/1/1"),
        ("post", "/api/admin/verses/1/1/audio/arabic"),
    ):
        assert getattr(client, method)(path).status_code in (404, 405), path  # 405 = static mount at "/" rejecting non-GET
    assert not hasattr(make, "admin_content")


def test_x264_preset_follows_quality(monkeypatch, tmp_path):
    monkeypatch.delenv("RENDER_MEMORY_PROFILE", raising=False)
    captured = {}
    monkeypatch.setattr(make, "run", lambda command, **kwargs: captured.update(command=command))
    for quality, preset in (("fast", "veryfast"), ("balanced", "fast"), ("high", "medium")):
        make.set_video_quality(quality)
        make.encode_video(["ffmpeg"], tmp_path / "out.mp4")
        command = captured["command"]
        assert command[command.index("-preset") + 1] == preset
        assert command[command.index("-crf") + 1] == "18"
    make.set_video_quality("high")


def _history_files(count):
    entries = []
    for index in range(count):
        name = f"item{index}.mp4" if index % 2 else f"ayah-image-{index}.png"
        (main.OUTPUT_DIR / name).write_bytes(f"data-{index}".encode() * 1000)
        entries.append({"id": f"id{index}", "filename": name, "type": "image" if name.endswith(".png") else "video"})
    _write_history({"free-user": entries, "another-user": [{"id": "theirs", "filename": "theirs.mp4"}]})
    return entries


def test_history_lists_images_with_thumbnails(client):
    _history_files(2)
    videos = client.get("/api/history").json()["videos"]
    image = next(item for item in videos if item["type"] == "image")
    assert image["thumb_url"].startswith("/api/video/ayah-image-0.png?")
    assert client.get(image["thumb_url"]).headers["content-type"] == "image/png"


def test_bulk_history_zip_streams_owned_items_only(client):
    _history_files(7)
    ids = [f"id{index}" for index in range(7)]
    assert client.post("/api/history/bulk-download-url", json={"ids": ids + ["theirs"]}).status_code == 404
    assert client.post("/api/history/bulk-download-url", json={"ids": ["../x"]}).status_code == 400
    assert client.post("/api/history/bulk-download-url", json={"ids": [f"i{n}" for n in range(101)]}).status_code == 422
    url = client.post("/api/history/bulk-download-url", json={"ids": ids}).json()["url"]
    response = client.get(url)
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None
    assert len(archive.namelist()) == 7
    assert all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist())
    assert archive.read("item1.mp4") == b"data-1" * 1000
    assert client.get(url.replace("id0", "theirs")).status_code == 403


def test_bulk_history_delete_checks_ownership(client):
    _history_files(3)
    assert client.post("/api/history/bulk-delete", json={"ids": ["id0", "theirs"]}).status_code == 404
    assert (main.OUTPUT_DIR / "ayah-image-0.png").exists()
    response = client.post("/api/history/bulk-delete", json={"ids": ["id0", "id1"]})
    assert response.json() == {"deleted": 2}
    assert not (main.OUTPUT_DIR / "ayah-image-0.png").exists()
    remaining = json.loads(main.HISTORY_FILE.read_text(encoding="utf-8"))
    assert [entry["id"] for entry in remaining["users"]["free-user"]] == ["id2"]
    assert remaining["users"]["another-user"]
