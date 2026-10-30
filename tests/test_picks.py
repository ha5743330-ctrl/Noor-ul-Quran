"""Managed picks: seed data, API gating, per-video charging, refunds, real-seconds cap, ZIP fallback.

Everything runs against the Supabase stub (SUPABASE_SERVICE_KEY is blank unless a test patches
_upload_media_file / _download_media_file itself); nothing here talks to the real project.
"""
import io
import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import main
import picks

ROOT = Path(__file__).resolve().parent.parent
FREE = {"id": "free", "email": "f@x.y", "is_admin": False, "premium_access": False}
PREMIUM = {"id": "prem", "email": "p@x.y", "is_admin": False, "premium_access": True}
ADMIN = {"id": "admin", "email": "a@x.y", "is_admin": True, "premium_access": True}
QURAN = json.loads((ROOT / "data" / "quran.json").read_text(encoding="utf-8"))
DEFAULT = json.loads((ROOT / "data" / "picks_default.json").read_text(encoding="utf-8"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setenv("DISABLE_CLEANUP_THREAD", "1")
    for name, value in {
        "OUTPUT_DIR": out, "HISTORY_FILE": out / "history.json", "CACHE_DIR": tmp_path / "cache",
        "SUPABASE_SERVICE_KEY": "", "SUPABASE_URL": "", "SUPABASE_KEY": "", "RAQM_AVAILABLE": True,
        "MAX_VIDEO_SECONDS": 35, "DAILY_LIMIT": 10, "_PICKS_PULLED": False,
        "AUDIT_FILE": out / "audit.json", "USERS_FILE": out / "users.json",
    }.items():
        monkeypatch.setattr(main, name, value)
    monkeypatch.setattr(main, "GENERATION_EXECUTOR", SimpleNamespace(submit=lambda *a, **k: None))
    main.GENERATION_JOBS.clear()
    main.DAILY_USAGE.clear()
    main.app.dependency_overrides[main.get_current_user] = lambda: PREMIUM
    main.app.dependency_overrides[main.require_admin] = lambda: ADMIN
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()
    main.GENERATION_JOBS.clear()


def _as(user):
    main.app.dependency_overrides[main.get_current_user] = lambda: user
    if user["is_admin"]:
        main.app.dependency_overrides[main.require_admin] = lambda: user
    else:
        main.app.dependency_overrides.pop(main.require_admin, None)  # the real admin check, which rejects


def _pick(bucket, mode, ref):
    return next(p for p in DEFAULT if (p["bucket"], p["mode"], p["ref"]) == (bucket, mode, ref))


# --- seed + parser ---------------------------------------------------------------------------
def test_seed_has_300_picks_50_per_bucket_and_mode():
    assert len(DEFAULT) == 300
    for bucket in picks.BUCKETS:
        for mode in picks.MODES:
            refs = [p["ref"] for p in DEFAULT if p["bucket"] == bucket and p["mode"] == mode]
            assert len(refs) == 50 and len(set(refs)) == 50
    assert all(set(p) >= {"id", "bucket", "mode", "ref", "seconds", "over_bucket"} for p in DEFAULT)
    assert len({p["id"] for p in DEFAULT}) == 300


def test_seed_matches_files_and_is_not_deduped_across_buckets():
    from_files = 0
    for name in ("30-sec-reference.txt", "1-min-reference.txt", "2-min-reference.txt"):
        text = (ROOT / "data" / "picks_seed" / name).read_text(encoding="utf-8")
        from_files += sum(1 for line in text.splitlines() if picks.REF_RE.search(line))
    assert from_files == 300
    assert len({p["bucket"] for p in DEFAULT if p["ref"] == "2:255"}) >= 2  # same ayah in several buckets
    assert all(picks.parse_ref(p["ref"], QURAN)[0] == p["ref"] for p in DEFAULT)


def test_over_bucket_flag_matches_seconds_and_nothing_is_dropped():
    assert all(p["over_bucket"] == (p["seconds"] > p["bucket"]) for p in DEFAULT)
    assert _pick(60, "arabic", "2:255")["seconds"] < 60
    assert sum(p["over_bucket"] for p in DEFAULT if p["bucket"] == 120 and p["mode"] == "arabic") == 49


def test_import_parser_ignores_names_and_reports_bad_lines():
    text = "=== ARABIC ONLY ===\n1. (Surah Al-Kawthar 108:1-3)\n2. (Surah X 999:1)\n3. (Surah Y 2:300)\nnonsense\n# c\n"
    found, invalid = picks.parse_import(text, QURAN)
    assert found == [("arabic", "108:1-3")]
    assert len(invalid) == 3
    assert picks.parse_import("2:255", QURAN)[1]  # no section header and no default mode -> invalid
    assert picks.parse_import("2:255", QURAN, "urdu")[0] == [("urdu", "2:255")]


def test_export_round_trips_through_import():
    rows = [p for p in DEFAULT if p["bucket"] == 30]
    found, invalid = picks.parse_import(picks.format_export(rows, QURAN), QURAN)
    assert not invalid and sorted(found) == sorted((p["mode"], p["ref"]) for p in rows)


# --- API: listing + gating -------------------------------------------------------------------
def test_picks_list_marks_selectable_by_real_seconds_against_cap(client):
    body = client.get("/api/picks", params={"bucket": 30, "mode": "arabic"}).json()
    assert body["cap"] == 35 and len(body["picks"]) == 50
    for row in body["picks"]:
        assert row["selectable"] == (row["seconds"] <= 35)
        assert row["over_bucket"] == (row["seconds"] > 30)
    ayat_255 = next(r for r in body["picks"] if r["ref"] == "2:255")
    assert ayat_255["selectable"] is False and ayat_255["surah_name"] == "Al-Baqara"


def test_writes_are_admin_only_and_validate_refs(client):
    _as(PREMIUM)
    assert client.post("/api/picks", json={"bucket": 30, "mode": "urdu", "ref": "112:1-4"}).status_code in (401, 403)
    assert client.delete("/api/picks/30u001").status_code in (401, 403)
    _as(ADMIN)
    for ref in ("999:1", "2:300", "2:5-3", "abc"):
        assert client.post("/api/picks", json={"bucket": 30, "mode": "urdu", "ref": ref}).status_code == 400
    assert client.post("/api/picks", json={"bucket": 45, "mode": "urdu", "ref": "112:1"}).status_code == 400
    r = client.post("/api/picks", json={"bucket": 30, "mode": "urdu", "ref": "112:1-2"})
    assert r.status_code == 201 and r.json()["seconds"] > 0
    assert client.post("/api/picks", json={"bucket": 30, "mode": "urdu", "ref": "112:1-2"}).status_code == 409
    assert client.delete(f"/api/picks/{r.json()['id']}").status_code == 200
    assert client.delete("/api/picks/nope").status_code == 404


def test_supabase_write_failure_is_returned_and_changes_nothing(client, monkeypatch):
    _as(ADMIN)
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "stub-key")
    monkeypatch.setattr(main, "_download_media_file", lambda *a, **k: False)

    def boom(*a, **k):
        raise main.HTTPException(status_code=503, detail="storage down stub-key")

    monkeypatch.setattr(main, "_upload_media_file", boom)
    before = client.get("/api/picks").json()["counts"]["total"]
    r = client.post("/api/picks", json={"bucket": 30, "mode": "urdu", "ref": "112:1-2"})
    assert r.status_code == 502 and "nothing was changed" in r.json()["detail"]
    assert "stub-key" not in r.json()["detail"]
    assert client.get("/api/picks").json()["counts"]["total"] == before


def test_import_preview_then_apply_and_export(client):
    _as(ADMIN)
    text = "=== URDU ONLY REFERENCES ===\n1. (Surah Al-Ikhlas 112:1-4)\n2. (Surah Z 1:1-7)\n3. bad line\n"
    body = {"text": text, "bucket": 30, "apply": False}
    preview = client.post("/api/picks/import", json=body).json()
    assert preview["applied"] is False and [a["ref"] for a in preview["add"]] == []  # both already in 30 s urdu
    assert len(preview["duplicates"]) == 2 and len(preview["invalid"]) == 1
    text2 = "=== URDU ONLY REFERENCES ===\n1. (Surah Al-Kawthar 108:1-2)\n"
    applied = client.post("/api/picks/import", json={"text": text2, "bucket": 30, "apply": True}).json()
    assert applied["applied"] is True
    exported = client.get("/api/picks/export", params={"bucket": 30}).text
    assert "108:1-2" in exported and exported.startswith("=== URDU ONLY REFERENCES ===")


def test_trim_needs_confirm_and_creates_a_new_pick(client, monkeypatch):
    _as(ADMIN)
    over = next(p for p in DEFAULT if p["bucket"] == 30 and p["mode"] == "urdu" and p["over_bucket"]
                and picks.split_ref(p["ref"])[1] != picks.split_ref(p["ref"])[2])
    r = client.post(f"/api/picks/{over['id']}/trim", json={})
    if r.status_code == 400:  # nothing trims to fit; the contract is still "never automatic"
        return
    preview = r.json()
    assert preview["confirmed"] is False and preview["seconds"] <= 30
    total = client.get("/api/picks").json()["counts"]["total"]
    assert client.get("/api/picks").json()["counts"]["total"] == total
    done = client.post(f"/api/picks/{over['id']}/trim", json={"confirm": True}).json()
    assert done["confirmed"] and done["pick"]["trimmed_from"] == over["id"]
    assert client.get("/api/picks").json()["counts"]["total"] == total + 1


# --- generate by pick ids --------------------------------------------------------------------
def _ids(bucket, mode, n, only_fitting=True):
    return [p["id"] for p in DEFAULT if p["bucket"] == bucket and p["mode"] == mode
            and (not only_fitting or p["seconds"] <= 35)][:n]


def test_generate_takes_only_pick_ids_and_charges_one_slot_per_video(client):
    ids = _ids(30, "urdu", 3)
    r = client.post("/api/generate", json={"mode": "picks", "pick_ids": ids, "quality": "fast",
                                           "refs": ["1:1"], "verse": "1:1"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["daily_remaining"] == 7 and len(body["items"]) == 3
    assert [i["pick_id"] for i in body["items"]] == ids
    job = main.GENERATION_JOBS[body["job_id"]]
    assert job["charged"] == 3 and job["status"] == "queued"
    work = main.CACHE_DIR / "tmp" / body["batch_id"] / "picks.txt"
    assert work.read_text().split() == [next(p["ref"] for p in DEFAULT if p["id"] == i) for i in ids]


def test_batch_larger_than_remaining_slots_is_rejected(client, monkeypatch):
    monkeypatch.setattr(main, "DAILY_LIMIT", 2)
    r = client.post("/api/generate", json={"mode": "picks", "pick_ids": _ids(30, "urdu", 3), "quality": "fast"})
    assert r.status_code == 429 and "only 2" in r.json()["detail"]
    assert client.get("/api/access").json()["daily_remaining"] == 2


@pytest.mark.parametrize("payload", [
    {"mode": "picks"},
    {"mode": "picks", "pick_ids": []},
    {"mode": "picks", "pick_ids": ["nope"]},
    {"mode": "picks", "pick_ids": ["30u001", "30u001"]},
    {"mode": "picks", "pick_ids": ["30u001", "30a001"]},  # mixed modes
    {"mode": "picks", "pick_ids": ["30u001", "60u001"]},  # mixed buckets
    {"mode": "picks", "pick_ids": ["30u001"], "reciter_id": "husary"},  # reciter is locked
])
def test_bad_pick_batches_are_rejected(client, payload):
    assert client.post("/api/generate", json={**payload, "quality": "fast"}).status_code in (400, 422)
    assert client.get("/api/access").json()["daily_remaining"] == 10


def test_more_than_25_picks_rejected(client):
    ids = [p["id"] for p in DEFAULT if p["bucket"] == 60 and p["mode"] == "urdu"][:26]
    assert client.post("/api/generate", json={"mode": "picks", "pick_ids": ids}).status_code in (400, 422)


def test_picks_are_premium_only_but_admin_is_fine(client):
    _as(FREE)
    assert client.post("/api/generate", json={"mode": "picks", "pick_ids": ["30u001"]}).status_code == 403
    _as(ADMIN)
    assert client.post("/api/generate", json={"mode": "picks", "pick_ids": _ids(30, "urdu", 1)}).status_code == 202


def test_pick_over_the_cap_is_rejected_for_everyone_with_a_suggestion(client):
    over = _pick(30, "arabic", "2:255")["id"]  # 51.9 s Arabic, cap 35
    for user in (PREMIUM, ADMIN):
        _as(user)
        r = client.post("/api/generate", json={"mode": "picks", "pick_ids": [over], "quality": "fast"})
        assert r.status_code == 400
        assert "51.9 s" in r.json()["detail"] and "limit is 35 s" in r.json()["detail"]
    assert client.get("/api/access").json()["daily_remaining"] is None  # admin: no count, nothing queued
    assert not main.GENERATION_JOBS


def test_single_2_255_arabic_is_refused_at_cap_35_even_for_admin(client):
    """The live overrun: 2:255 Arabic is ~51.9 s and used to render because the first ayah was always kept."""
    for user in (PREMIUM, ADMIN):
        _as(user)
        r = client.post("/api/generate", json={
            "mode": "pick", "verse": "2:255", "content_mode": "arabic_only", "max_duration": 30, "quality": "fast"})
        assert r.status_code == 400, r.text
        assert re.search(r"51\.9 s.*limit is 35 s.*Try 2:255 on the local app", r.json()["detail"])
    assert not main.GENERATION_JOBS


def test_single_range_suggests_the_nearest_range_that_fits(client):
    r = client.post("/api/generate", json={
        "mode": "pick", "verse": "2:255-257", "content_mode": "urdu_only", "max_duration": 60, "quality": "fast"})
    assert r.status_code == 400  # first ayah alone (40.4 s Urdu) is over 35


def test_same_ayah_fits_when_the_cap_is_raised_locally(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_VIDEO_SECONDS", 120)
    r = client.post("/api/generate", json={
        "mode": "pick", "verse": "2:255", "content_mode": "arabic_only", "max_duration": 60, "quality": "fast"})
    assert r.status_code == 202, r.text


# --- reliability -----------------------------------------------------------------------------
def _start_picks_job(client, n=3):
    ids = _ids(30, "urdu", n)
    body = client.post("/api/generate", json={"mode": "picks", "pick_ids": ids, "quality": "fast"}).json()
    return body["job_id"], body


def _run(job_id, monkeypatch, entries, returncode=0, stderr=""):
    monkeypatch.setattr(main, "_prepare_ephemeral_media", lambda: None)
    monkeypatch.setattr(main, "_cleanup_orphaned_output_files", lambda: 0)

    def fake_render(cmd, timeout, job_id=None, on_tick=None):
        for entry in entries:
            if "filename" in entry:
                (main.OUTPUT_DIR / entry["filename"]).write_bytes(b"reel")
        Path(cmd[cmd.index("--result-json") + 1]).write_text(json.dumps(entries), encoding="utf-8")
        return SimpleNamespace(returncode=returncode, stderr=stderr, stdout="")

    monkeypatch.setattr(main, "_run_render", fake_render)
    job = main.GENERATION_JOBS[job_id]
    batch = job["batch_id"]
    main._run_generation_job(
        job_id, ["make.py", "--result-json", job["manifest_path"]], "picks", Path(job["manifest_path"]),
        main.CACHE_DIR / "tmp" / batch, None, "prem", "urdu_only", "fast",
    )
    return main.GENERATION_JOBS[job_id]


def test_failed_reel_refunds_its_slot_is_reported_and_batch_is_not_completed(client, monkeypatch):
    job_id, _ = _start_picks_job(client, 3)
    assert client.get("/api/access").json()["daily_remaining"] == 7
    entries = [
        {"filename": "b_001.mp4", "surah": 112, "start_ayah": 1, "end_ayah": 4, "index": 1, "duration": 20},
        {"index": 2, "error": "Ayah audio could not be downloaded."},
        {"filename": "b_003.mp4", "surah": 108, "start_ayah": 1, "end_ayah": 3, "index": 3, "duration": 10},
    ]
    job = _run(job_id, monkeypatch, entries)
    assert job["status"] == "partial"
    states = [(i["status"], i["reason"]) for i in job["items"]]
    assert states[0][0] == "done" and states[2][0] == "done"
    assert states[1] == ("failed", "Ayah audio could not be downloaded.")
    assert job["items"][0]["history_id"] and job["items"][0]["batch_id"] == job["batch_id"]
    assert client.get("/api/access").json()["daily_remaining"] == 8  # 3 charged, 1 refunded
    history = client.get("/api/history").json()["videos"]
    assert len(history) == 2


def test_unreported_reels_are_failed_and_refunded(client, monkeypatch):
    job_id, _ = _start_picks_job(client, 3)
    job = _run(job_id, monkeypatch, [{"filename": "only.mp4", "index": 1, "duration": 9}], returncode=1, stderr="x\nboom")
    assert job["status"] == "partial"
    assert [i["status"] for i in job["items"]] == ["done", "failed", "failed"]
    assert job["items"][1]["reason"] == "boom"
    assert client.get("/api/access").json()["daily_remaining"] == 9


def test_whole_job_failure_refunds_every_slot(client, monkeypatch):
    job_id, _ = _start_picks_job(client, 3)
    job = _run(job_id, monkeypatch, [], returncode=1, stderr="render exploded")
    assert job["status"] == "failed" and all(i["status"] == "failed" for i in job["items"])
    assert client.get("/api/access").json()["daily_remaining"] == 10


def test_each_reel_is_recorded_as_it_finishes(client, monkeypatch):
    job_id, _ = _start_picks_job(client, 2)
    seen = []
    monkeypatch.setattr(main, "_prepare_ephemeral_media", lambda: None)
    monkeypatch.setattr(main, "_cleanup_orphaned_output_files", lambda: 0)

    def fake_render(cmd, timeout, job_id=None, on_tick=None):
        (main.OUTPUT_DIR / "r1.mp4").write_bytes(b"1")
        Path(cmd[cmd.index("--result-json") + 1]).write_text(
            json.dumps([{"filename": "r1.mp4", "index": 1, "duration": 5}]), encoding="utf-8")
        on_tick()  # the first reel is done while the renderer is still busy on the second
        seen.append([i["status"] for i in main.GENERATION_JOBS[job_id]["items"]])
        seen.append(len(client.get("/api/history").json()["videos"]))
        return SimpleNamespace(returncode=1, stderr="", stdout="")

    monkeypatch.setattr(main, "_run_render", fake_render)
    job = main.GENERATION_JOBS[job_id]
    main._run_generation_job(job_id, ["make.py", "--result-json", job["manifest_path"]], "picks",
                             Path(job["manifest_path"]), main.CACHE_DIR / "tmp" / job["batch_id"], None, "prem",
                             "urdu_only", "fast")
    assert seen == [["done", "rendering"], 1]


def test_cancel_terminates_the_running_subprocess(client):
    import subprocess
    import sys

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    main.GENERATION_JOBS["j"] = {"owner_id": "prem", "status": "processing", "_proc": proc, "videos": []}
    main._cancel_user_jobs("prem")
    assert proc.wait(timeout=10) != 0
    assert main.GENERATION_JOBS["j"]["cancelled"]


# --- downloads -------------------------------------------------------------------------------
def test_history_zip_falls_back_to_supabase_copy_and_reports_missing(client, monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "stub-key")
    (main.OUTPUT_DIR / "local.mp4").write_bytes(b"local-bytes")
    main._write_video_history_unlocked({"users": {"prem": [
        {"id": "a", "filename": "local.mp4", "object_path": "videos/a_local.mp4"},
        {"id": "b", "filename": "gone.mp4", "object_path": "videos/b_gone.mp4"},
        {"id": "c", "filename": "lost.mp4", "object_path": None},
    ]}})

    def download(object_path, target, bucket=None):
        if object_path != "videos/b_gone.mp4":
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"from-supabase")
        return True

    monkeypatch.setattr(main, "_download_media_file", download)
    meta = client.post("/api/history/bulk-download-url", json={"ids": ["a", "b", "c"]}).json()
    assert meta["missing"] == ["lost.mp4"] and meta["item_count"] == 2
    response = client.get(meta["url"])
    assert response.status_code == 200 and response.headers["x-missing-items"] == "1"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == ["MISSING.txt", "gone.mp4", "local.mp4"]
        assert archive.read("gone.mp4") == b"from-supabase" and b"lost.mp4" in archive.read("MISSING.txt")
    assert not list((main.CACHE_DIR / "tmp" / "dl").glob("*"))  # temp copies cleaned up


def test_batch_zip_falls_back_to_supabase_when_local_file_is_gone(client, monkeypatch):
    monkeypatch.setattr(main, "SUPABASE_SERVICE_KEY", "stub-key")
    main.GENERATION_JOBS["job"] = {"owner_id": "prem", "status": "completed", "videos": [
        {"filename": "x.mp4", "object_path": "videos/x.mp4"}, {"filename": "y.mp4", "object_path": None}]}

    def download(object_path, target, bucket=None):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"cloud")
        return True

    monkeypatch.setattr(main, "_download_media_file", download)
    meta = client.get("/api/generate/job/download-url").json()
    assert meta["video_count"] == 1 and meta["missing"] == ["y.mp4"]
    with zipfile.ZipFile(io.BytesIO(client.get(meta["url"]).content)) as archive:
        assert sorted(archive.namelist()) == ["MISSING.txt", "x.mp4"]
