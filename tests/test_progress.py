import json

import progress
from tests.test_free_tier import PAYLOAD, client  # noqa: F401  (fixture)
import main


def test_percent_maps_stage_and_reel_shares():
    assert progress.percent("audio") == 0
    assert progress.percent("audio", 0.5) == 10
    assert progress.percent("frames", 0.5) == 40
    assert progress.percent("video") == 55 and progress.percent("upload") == 95
    assert progress.percent("frames", 0, reel=2, reels=2) == 62.5  # second reel starts at half + 25/2
    assert progress.percent("video", 5) == 95  # frac clamped


def test_eta_and_rate_stat(tmp_path):
    assert progress.eta(50, 30, 3.0) == 45
    assert progress.eta(50, 30, None) is None and progress.eta(100, 30, 3.0) == 0
    stat = tmp_path / "rate.json"
    assert progress.load_rate(stat) is None
    for r in [2.0] * 10 + [4.0] * 10:
        progress.record_rate(stat, r)
    assert json.loads(stat.read_text()) == [4.0] * 10 and progress.load_rate(stat) == 4.0


def test_write_read_roundtrip_and_bad_file(tmp_path):
    p = tmp_path / "p.json"
    assert progress.read(p) is None
    progress.write(p, "frames", 0.5)
    assert progress.read(p) == {"stage": "frames", "percent": 40}
    p.write_text("{nope")
    assert progress.read(p) is None


def test_status_endpoint_exposes_stage_percent_eta(client, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(main, "RATE_STAT", tmp_path / "rate.json")
    jid = client.post("/api/generate", json=PAYLOAD).json()["job_id"]
    job = main.GENERATION_JOBS[jid]
    assert "stage" not in client.get(f"/api/generate/{jid}").json()  # queued: nothing to report
    job["status"] = "processing"
    assert "stage" not in client.get(f"/api/generate/{jid}").json()  # no file yet
    progress.write(job["progress_path"], "frames", 0.5)
    body = client.get(f"/api/generate/{jid}").json()
    assert (body["stage"], body["percent"], body["eta_seconds"]) == ("frames", 40, None)  # no rate -> no ETA
    assert "progress_path" not in body
    progress.record_rate(main.RATE_STAT, 2.0)
    body = client.get(f"/api/generate/{jid}").json()
    assert body["eta_seconds"] == round(2.0 * job["planned_seconds"] * 0.6)
    job["stage"], job["percent"] = "upload", 95
    body = client.get(f"/api/generate/{jid}").json()
    assert (body["stage"], body["percent"]) == ("upload", 95)


def test_ffmpeg_progress_lines_map_to_video_stage():
    lines = ["frame=10", "out_time_us=2500000", "out_time_ms=2500000", "out_time=00:00:02.500000", "out_time_us=N/A",
             "out_time=garbage", "progress=continue", "", "out_time_us=-5"]
    got = [progress.out_seconds(x) for x in lines]
    assert got == [None, 2.5, 2.5, 2.5, None, None, None, None, 0.0]
    assert progress.percent("video", 2.5 / 10) == 65 and progress.percent("video", 1.0) == 95


def test_failed_videos_keeps_last_20_redacted_newest_first():
    import analytics
    analytics._failed.clear()
    analytics.render_done(0, 25, 0, [f"boom {i} at /app/work/x.png, reel 2/3" for i in range(25)])
    got = analytics.failed_videos()
    assert len(got) == 20 and got[0]["reason"] == "boom 24 at [path], reel 2/3" and set(got[0]) == {"at", "reason"}
    analytics._failed.clear()
