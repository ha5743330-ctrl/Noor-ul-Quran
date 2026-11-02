"""Per-phrase text transitions: none (default), fade, fade_rise."""
import re
import sys

import pytest
from PIL import Image, ImageDraw

import make


def _layer(path, size=(400, 300)):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle((100, 120, 300, 160), fill=(255, 255, 255, 255))
    layer.save(path)


def _row_durations(rows):
    return [float(re.search(r"duration ([\d.]+)", row).group(1)) for row in rows]


def test_enforce_min_phrase_keeps_total_and_lifts_short_phrases():
    result = make.enforce_min_phrase([0.4, 5.0, 0.7, 4.0], 1.2)
    assert sum(result) == pytest.approx(10.1)
    assert all(value >= 1.2 - 1e-9 for value in result)


def test_enforce_min_phrase_leaves_long_enough_phrases_alone():
    assert make.enforce_min_phrase([2.0, 3.0], 1.2) == pytest.approx([2.0, 3.0])


def test_enforce_min_phrase_splits_evenly_when_total_is_too_short():
    result = make.enforce_min_phrase([0.2, 0.6, 0.4], 1.2)
    assert result == pytest.approx([0.4, 0.4, 0.4])


def test_chunk_spans_default_is_proportional_and_gapless():
    spans = make.chunk_spans([1, 3], 10.0, 8.0)
    assert [x for p in spans for x in p] == pytest.approx([10.0, 12.0, 12.0, 18.0])
    animated = make.chunk_spans([1, 9, 1, 9], 0.0, 12.0, animate=True)
    assert all(end - start >= 1.2 - 1e-9 for start, end in animated)
    assert animated[0][0] == 0.0 and animated[-1][1] == pytest.approx(12.0)


def test_fade_frames_ramp_alpha_up_then_down(tmp_path):
    base, layer = tmp_path / "base.png", tmp_path / "layer.png"
    Image.new("RGBA", (400, 300), (0, 0, 0, 0)).save(base)
    _layer(layer)
    steady = tmp_path / "steady.png"
    Image.open(layer).save(steady)
    rows, last = make.animation_frames(base, steady, layer, tmp_path, 0, 3.0, "fade")
    n_in = round(make.FADE_IN_SEC * make.FPS)
    n_out = round(make.FADE_OUT_SEC * make.FPS)
    assert len(rows) == n_in + 1 + n_out
    assert sum(_row_durations(rows)) == pytest.approx(3.0, abs=1e-3)
    files = re.findall(r"file '([^']+)'", "\n".join(rows))
    alphas = [Image.open(f).convert("RGBA").getchannel("A").getextrema()[1] for f in files]
    fade_in, fade_out = alphas[:n_in], alphas[n_in + 1:]
    assert fade_in == sorted(fade_in) and fade_in[0] < 255
    assert fade_out == sorted(fade_out, reverse=True) and fade_out[-1] < 255
    assert alphas[n_in] == 255
    assert str(last).endswith(".tga")


def test_fade_rise_drifts_up_during_fade_in_only(tmp_path):
    base, layer, steady = tmp_path / "base.png", tmp_path / "layer.png", tmp_path / "steady.png"
    Image.new("RGBA", (400, 300), (0, 0, 0, 0)).save(base)
    _layer(layer)
    Image.open(layer).save(steady)
    rows, _ = make.animation_frames(base, steady, layer, tmp_path, 1, 3.0, "fade_rise", scale=1.0)
    files = re.findall(r"file '([^']+)'", "\n".join(rows))
    n_in = round(make.FADE_IN_SEC * make.FPS)
    tops = [Image.open(f).convert("RGBA").getbbox()[1] for f in files]
    steady_top = tops[n_in]
    assert tops[0] > steady_top and tops[0] - steady_top <= make.RISE_PX
    assert tops[:n_in] == sorted(tops[:n_in], reverse=True)
    assert all(top == steady_top for top in tops[n_in + 1:])


def test_very_short_phrase_shrinks_fades_so_it_still_has_a_steady_part(tmp_path):
    base, layer, steady = tmp_path / "base.png", tmp_path / "layer.png", tmp_path / "steady.png"
    Image.new("RGBA", (400, 300), (0, 0, 0, 0)).save(base)
    _layer(layer)
    Image.open(layer).save(steady)
    rows, _ = make.animation_frames(base, steady, layer, tmp_path, 2, 0.5, "fade")
    durations = _row_durations(rows)
    assert min(durations) > 0 and sum(durations) == pytest.approx(0.5, abs=1e-3)


class _StopBuild(Exception):
    pass


def _build_rows(tmp_path, monkeypatch, animation, secs=40):
    item = {
        "ayah": 255, "ar_dur": secs / 2, "u0": 0.0, "len": secs,
        "text": "اللہ وہ ہے جس کے سوا کوئی معبود نہیں، وہ زندہ ہے سب کو قائم رکھنے والا ہے، نہ اسے اونگھ آتی ہے اور نہ نیند، اسی کا ہے جو کچھ آسمانوں میں ہے۔",
        "arabic": make.verified_arabic_text(2, 255), "english": "x", "segs": [],
    }
    plan = {"items": [item], "fit_seconds": secs, "fits_up_to": 255, "urdu_credit": ""}
    monkeypatch.setattr(make, "plan_reel", lambda *a, **k: plan)

    def stop(*args, **kwargs):
        raise _StopBuild()

    monkeypatch.setattr(make, "background_playlist", stop)
    Q = {"surahs": {"2": {"name": "سورة البقرة", "n": 286}}}
    with pytest.raises(_StopBuild):
        make.build(Q, 2, 255, 255, tmp_path / "x.mp4", [], None, tmp_path,
                   content_mode="arabic_urdu_text", max_duration=60, text_animation=animation)
    return (tmp_path / "list.txt").read_text(encoding="utf-8")


def test_default_animation_none_uses_one_png_per_phrase_as_before(tmp_path, monkeypatch):
    listing = _build_rows(tmp_path, monkeypatch, "none")
    assert ".tga" not in listing
    durations = _row_durations(listing.split("\n")[1::2])
    assert sum(durations) == pytest.approx(40.0, abs=0.05)


def test_fade_build_keeps_every_phrase_at_least_1_2_seconds(tmp_path, monkeypatch):
    listing = _build_rows(tmp_path, monkeypatch, "fade")
    per_phrase = {}
    lines = listing.split("\n")
    for file_line, duration_line in zip(lines[0::2], lines[1::2]):
        match = re.search(r"/f(\d+)_", file_line)
        if match:
            per_phrase[match.group(1)] = per_phrase.get(match.group(1), 0.0) + float(duration_line.split()[1])
    assert per_phrase and all(value >= 1.2 - 1e-3 for value in per_phrase.values())
    assert sum(per_phrase.values()) == pytest.approx(40.0, abs=0.05)


def test_build_rejects_unknown_animation(tmp_path):
    with pytest.raises(ValueError, match="fade_rise"):
        make.build({}, 1, 1, 1, tmp_path / "x.mp4", [], None, tmp_path, text_animation="spin")


def test_cli_rejects_unknown_animation(monkeypatch):
    monkeypatch.setattr(make, "ensure_required_tools", lambda: None)
    monkeypatch.setattr(sys, "argv", ["make.py", "--pick", "1:1", "--text-animation", "spin"])
    with pytest.raises(SystemExit) as exc:
        make.main()
    assert exc.value.code == 2


# ---- API layer (needs fastapi) ----
try:
    from fastapi.testclient import TestClient
    import main
except ImportError:  # pragma: no cover
    main = None

FREE = {"id": "free", "email": "f@x.y", "is_admin": False, "premium_access": False}
PAYLOAD = {"mode": "pick", "verse": "1:1", "max_duration": 30, "quality": "fast"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    if main is None:
        pytest.skip("fastapi is not installed")
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setenv("DISABLE_CLEANUP_THREAD", "1")
    for name, value in {
        "OUTPUT_DIR": out, "HISTORY_FILE": out / "history.json", "CACHE_DIR": tmp_path / "cache",
        "SUPABASE_SERVICE_KEY": "", "SUPABASE_URL": "", "SUPABASE_KEY": "", "RAQM_AVAILABLE": True,
    }.items():
        monkeypatch.setattr(main, name, value)
    main.GENERATION_JOBS.clear()
    main.DAILY_USAGE.clear()
    main.app.dependency_overrides[main.get_current_user] = lambda: FREE
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()
    main.GENERATION_JOBS.clear()


def _capture(monkeypatch):
    captured = {}

    class Executor:
        def submit(self, *args):
            captured["args"] = args

    monkeypatch.setattr(main, "GENERATION_EXECUTOR", Executor())
    return captured


def test_default_request_has_no_animation_flag(client, monkeypatch):
    assert main.GenerateRequest(mode="pick").text_animation == "none"
    captured = _capture(monkeypatch)
    assert client.post("/api/generate", json=PAYLOAD).status_code == 202
    assert "--text-animation" not in captured["args"][2]


@pytest.mark.parametrize("animation", ["fade", "fade_rise"])
def test_animation_is_passed_to_the_renderer(client, monkeypatch, animation):
    captured = _capture(monkeypatch)
    assert client.post("/api/generate", json={**PAYLOAD, "text_animation": animation}).status_code == 202
    command = captured["args"][2]
    assert command[command.index("--text-animation") + 1] == animation


@pytest.mark.parametrize("animation", ["spin", "", "FADE"])
def test_unknown_animation_is_rejected(client, animation):
    assert client.post("/api/generate", json={**PAYLOAD, "text_animation": animation}).status_code == 400
