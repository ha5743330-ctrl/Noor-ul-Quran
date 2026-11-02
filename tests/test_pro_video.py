"""Text layouts (one_line / two_lines / block), safe zones, and Pro-only features."""
import re

import pytest

import make

URDU = ("اللہ وہ ہے جس کے سوا کوئی معبود نہیں، وہ زندہ ہے سب کو قائم رکھنے والا ہے، نہ اسے اونگھ آتی ہے "
        "اور نہ نیند، اسی کا ہے جو کچھ آسمانوں میں ہے اور جو کچھ زمین میں ہے۔")
ENGLISH = ("Allah! There is no god save Him, the Alive, the Eternal. Neither slumber nor sleep overtakes Him. "
           "Unto Him belongeth whatsoever is in the heavens and whatsoever is in the earth.")
SECS = 40.0


class _Stop(Exception):
    pass


def _squash(text):
    return re.sub(r"\s+", "", text)


def _run_build(tmp_path, monkeypatch, mode, layout, video_style="classic", stop_at_playlist=True):
    arabic = make.verified_arabic_text(2, 255)
    item = {"ayah": 255, "ar_dur": SECS / 2, "u0": 0.0 if mode == "urdu_only" else SECS / 2, "len": SECS, "text": URDU, "arabic": arabic,
            "english": ENGLISH, "segs": []}
    plan = {"items": [item], "fit_seconds": SECS, "fits_up_to": 255, "urdu_credit": ""}
    monkeypatch.setattr(make, "plan_reel", lambda *a, **k: plan)
    monkeypatch.setattr(make, "INCLUDE_ARABIC_AUDIO", mode != "urdu_only")
    seen = {"ar": [], "second": [], "png": []}
    real_bi, real_png = make.chunk_bilingual_png, make.chunk_png

    def bi(path, ar, second, font_a, font_b, *a, **k):
        seen["ar"].append((ar, font_a)); seen["second"].append((second, font_b)); seen["png"].append(path)
        return real_bi(path, ar, second, font_a, font_b, *a, **k)

    def png(path, lines, font, *a, **k):
        seen["ar" if k.get("is_arabic") else "second"].append((lines, font)); seen["png"].append(path)
        return real_png(path, lines, font, *a, **k)

    monkeypatch.setattr(make, "chunk_bilingual_png", bi)
    monkeypatch.setattr(make, "chunk_png", png)
    cmds = []
    if stop_at_playlist:
        monkeypatch.setattr(make, "background_playlist", lambda *a, **k: (_ for _ in ()).throw(_Stop()))
    else:
        monkeypatch.setattr(make, "background_playlist", lambda *a, **k: [(tmp_path / "bg.mp4", 0, SECS)])
        monkeypatch.setattr(make, "encode_video", lambda cmd, out: cmds.append(cmd))
    Q = {"surahs": {"2": {"name": "سورة البقرة", "n": 286, "english": "Al-Baqarah"}}}
    args = (Q, 2, 255, 255, tmp_path / "x.mp4", [], None, tmp_path)
    kw = dict(content_mode=mode, max_duration=60, text_layout=layout, video_style=video_style)
    if stop_at_playlist:
        with pytest.raises(_Stop):
            make.build(*args, **kw)
    else:
        make.build(*args, **kw)
    rows = (tmp_path / "list.txt").read_text(encoding="utf-8").split("\n")
    return seen, rows, cmds


@pytest.mark.parametrize("layout", make.TEXT_LAYOUTS)
@pytest.mark.parametrize("mode", ["arabic_urdu_text", "arabic_only", "full", "urdu_only"])
def test_chunks_join_to_original_fit_width_and_min_duration(tmp_path, monkeypatch, mode, layout):
    seen, rows, _ = _run_build(tmp_path, monkeypatch, mode, layout)
    original_ar = _squash(make.verified_arabic_text(2, 255))
    if mode != "urdu_only":
        assert _squash("".join(l for lines, _ in seen["ar"] for l in lines)) == original_ar
    if mode == "arabic_urdu_text":
        want = URDU
    elif mode == "arabic_only":
        want = ENGLISH
    elif mode in ("full", "urdu_only"):
        want = URDU
    assert _squash("".join(l for lines, _ in seen["second"] for l in lines)) == _squash(want)
    max_lines = {"one_line": 1, "two_lines": 2}.get(layout, 10**6)
    for lines, font in seen["ar"] + seen["second"]:
        assert len(lines) <= max_lines
        for line in lines:
            lang = "en" if (mode == "arabic_only" and (lines, font) in seen["second"]) else "ar"
            direction = "ltr" if lang == "en" else "rtl"
            if (lines, font) in seen["second"] and lang != "en":
                lang = "ur"
            assert font.getlength(line, direction=direction, language=lang) <= make.TEXT_WIDTH + 1
    durations = [float(r.split()[1]) for r in rows if r.startswith("duration")]
    assert all(d >= make.MIN_PHRASE_SEC - 1e-3 for d in durations)
    assert sum(durations) == pytest.approx(SECS, abs=0.05)


def test_layout_changes_screen_count(tmp_path, monkeypatch):
    one, _, _ = _run_build(tmp_path, monkeypatch, "arabic_urdu_text", "one_line")
    block, _, _ = _run_build(tmp_path, monkeypatch, "arabic_urdu_text", "block")
    assert len(block["png"]) == 1 < len(one["png"])


def test_safe_zones(tmp_path, monkeypatch):
    calls = []
    real_put = make.put
    monkeypatch.setattr(make, "put", lambda d, xy, text, font, *a, **k: (
        calls.append((xy, text, font)), real_put(d, xy, text, font, *a, **k))[1])
    make.base_overlay(tmp_path / "b.png", "سورۃ البقرہ", "آیت ۲۵۵", True)
    for (x, y), text, font in calls:
        top, bottom = font.getbbox(text, anchor="ma")[1] + y, font.getbbox(text, anchor="ma")[3] + y
        if text == make.CHANNEL:
            assert bottom <= make.SAFE_BOTTOM
        else:
            assert top >= make.SAFE_TOP
    seen, _, _ = _run_build(tmp_path, monkeypatch, "arabic_urdu_text", "two_lines")
    from PIL import Image, ImageChops
    base = Image.open(tmp_path / "base.png").convert("RGBA")
    for path in seen["png"]:
        left, top, right, bottom = ImageChops.difference(Image.open(path).convert("RGBA"), base).getbbox()
        assert left >= make.CARD_MARGIN - 10 and right <= make.CARD_X1 + 10  # bead ring margin
        assert bottom <= make.SAFE_BOTTOM


def test_pro_features_off_on_render_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAND_HANDLE", "@noor")
    monkeypatch.setenv("RENDER_MEMORY_PROFILE", "low")
    _, _, cmds = _run_build(tmp_path, monkeypatch, "arabic_urdu_text", "one_line", "pro", stop_at_playlist=False)
    fc = cmds[0][cmds[0].index("-filter_complex") + 1]
    assert "colorbalance" not in fc and "title" not in fc and "-loop" not in cmds[0]
    assert "scale=w=" in fc and "eval=frame" in fc and "afade=t=in:d=1" in fc  # always-on features stay


def test_pro_features_on_locally(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAND_HANDLE", "@noor")
    monkeypatch.delenv("RENDER_MEMORY_PROFILE", raising=False)
    _, _, cmds = _run_build(tmp_path, monkeypatch, "arabic_urdu_text", "one_line", "pro", stop_at_playlist=False)
    fc = cmds[0][cmds[0].index("-filter_complex") + 1]
    assert "colorbalance" in fc and cmds[0].count("-loop") == 2 and (tmp_path / "watermark.png").exists()
    monkeypatch.setenv("BRAND_HANDLE", "")
    _, _, cmds = _run_build(tmp_path, monkeypatch, "arabic_urdu_text", "one_line", "pro", stop_at_playlist=False)
    assert cmds[0].count("-loop") == 1  # title only, watermark off when BRAND_HANDLE is empty


def test_fade_rise_falls_back_to_fade_on_low_profile(monkeypatch):
    monkeypatch.setenv("RENDER_MEMORY_PROFILE", "low")
    assert make.effective_animation("fade_rise") == "fade"
    assert make.effective_animation("none") == "none"
    monkeypatch.delenv("RENDER_MEMORY_PROFILE")
    assert make.effective_animation("fade_rise") == "fade_rise"
