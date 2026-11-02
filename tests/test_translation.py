"""Arabic recitation + translation option: en (Pickthall, default) or ur (Urdu text, no Urdu audio)."""
import json
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

import make

QURAN = {
    "surahs": {
        "1": {
            "n": 2,
            "name": "سورة الفاتحة",
            "arabic": {"1": "الْحَمْدُ لِلَّهِ", "2": "الرَّحْمَٰنِ"},
            "english_ayahs": {"1": "All praise is due to Allah.", "2": "The Beneficent."},
            "ayahs": {"1": "جالندھری ایک", "2": "جالندھری دو"},
        }
    }
}


@pytest.fixture
def plan_env(monkeypatch, tmp_path):
    items = [
        {"ayah": a, "ar_dur": 5, "u0": 0, "ur_dur": 0, "len": 5} for a in (1, 2)
    ]
    monkeypatch.setattr(
        make, "plan_duration",
        lambda *args: {"fit_items": [dict(i) for i in items], "fits_up_to": 2},
    )
    monkeypatch.setattr(make, "ayah_clip", lambda *args: {"segs": [], "len": 5})
    monkeypatch.setattr(make, "verified_arabic_text", lambda s, a: QURAN["surahs"]["1"]["arabic"][str(a)])
    monkeypatch.setattr(make, "URDU_USMANI_PATH", tmp_path / "urdu_usmani.json")
    return tmp_path / "urdu_usmani.json"


def test_urdu_text_uses_jalandhry_when_usmani_file_is_absent(plan_env):
    plan = make.plan_reel(QURAN, 1, 1, 2, 30, "arabic_urdu_text")
    assert [i["text"] for i in plan["items"]] == ["جالندھری ایک", "جالندھری دو"]
    assert plan["urdu_source"] == "jalandhry"
    assert plan["urdu_credit"] == "ترجمہ: فتح محمد جالندھری"
    assert all(i["arabic"] for i in plan["items"])


def test_urdu_text_uses_usmani_for_every_ayah_when_file_exists(plan_env):
    plan_env.write_text(json.dumps({"1": {"1": "عثمانی ایک", "2": "عثمانی دو"}}), encoding="utf-8")
    plan = make.plan_reel(QURAN, 1, 1, 2, 30, "arabic_urdu_text")
    assert [i["text"] for i in plan["items"]] == ["عثمانی ایک", "عثمانی دو"]
    assert plan["urdu_source"] == "usmani"
    assert plan["urdu_credit"] == "ترجمہ: مفتی محمد تقی عثمانی"


def test_usmani_file_missing_an_ayah_fails_instead_of_mixing_sources(plan_env):
    plan_env.write_text(json.dumps({"1": {"1": "عثمانی ایک"}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="1:2"):
        make.plan_reel(QURAN, 1, 1, 2, 30, "arabic_urdu_text")


def test_english_default_is_unchanged_even_if_usmani_file_exists(plan_env):
    plan_env.write_text(json.dumps({"1": {"1": "عثمانی ایک", "2": "عثمانی دو"}}), encoding="utf-8")
    plan = make.plan_reel(QURAN, 1, 1, 2, 30, "arabic_only")
    assert plan["items"][0]["english"] == "All praise is due to Allah."
    assert plan["items"][0]["text"] == ""


def test_urdu_audio_modes_keep_their_own_text_even_if_usmani_file_exists(plan_env):
    plan_env.write_text(json.dumps({"1": {"1": "عثمانی ایک", "2": "عثمانی دو"}}), encoding="utf-8")
    plan = make.plan_reel(QURAN, 1, 1, 2, 30, "full")
    assert plan["items"][0]["text"] == "جالندھری ایک"


def test_cli_translation_flag_requires_arabic_only(monkeypatch):
    monkeypatch.setattr(make, "ensure_required_tools", lambda: None)
    for flags in (["--translation", "ur"], ["--urdu-only", "--translation", "ur"]):
        monkeypatch.setattr(sys, "argv", ["make.py", "--pick", "1:1", *flags])
        with pytest.raises(SystemExit) as exc:
            make.main()
        assert exc.value.code == 2


def test_cli_translation_flag_rejects_unknown_language(monkeypatch):
    monkeypatch.setattr(make, "ensure_required_tools", lambda: None)
    monkeypatch.setattr(sys, "argv", ["make.py", "--pick", "1:1", "--arabic-only", "--translation", "fr"])
    with pytest.raises(SystemExit) as exc:
        make.main()
    assert exc.value.code == 2


def test_urdu_translation_screen_renders_arabic_over_urdu_with_credit(tmp_path):
    base = tmp_path / "base.png"
    Image.new("RGBA", (make.W, make.H), (0, 0, 0, 255)).save(base)
    out = tmp_path / "c0.png"
    make.chunk_bilingual_png(
        out, ["الْحَمْدُ لِلَّهِ"], ["جالندھری ایک"],
        make.load_font(make.QURAN_ARABIC_FONT, 64),
        make.load_font("nastaliq_regular", 40),
        base, {}, second_kind="ur", credit=make.URDU_CREDITS["jalandhry"],
    )
    assert Image.open(out).getbbox() is not None
    assert out.stat().st_size > Image.open(base).size[0]


# ---- API layer (needs fastapi, same fixture style as test_free_tier) ----
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


def test_default_translation_is_english_and_command_is_unchanged(client, monkeypatch):
    assert main.GenerateRequest(mode="pick").translation == "en"
    captured = _capture(monkeypatch)
    assert client.post("/api/generate", json={**PAYLOAD, "content_mode": "arabic_only"}).status_code == 202
    command = captured["args"][2]
    assert "--arabic-only" in command and "--translation" not in command
    assert captured["args"][8] == "arabic_only"


def test_urdu_translation_passes_flag_and_labels_history_as_urdu_text(client, monkeypatch):
    captured = _capture(monkeypatch)
    response = client.post("/api/generate", json={**PAYLOAD, "content_mode": "arabic_only", "translation": "ur"})
    assert response.status_code == 202
    command = captured["args"][2]
    assert command.count("--arabic-only") == 1
    assert command[command.index("--translation") + 1] == "ur"
    assert "--urdu-voice" not in command
    assert captured["args"][8] == "arabic_urdu_text"


@pytest.mark.parametrize(
    "extra",
    [
        {"translation": "fr"},
        {"translation": ""},
        {"translation": "ur", "content_mode": "full"},
        {"translation": "ur", "content_mode": "urdu_only"},
        {"translation": "ur", "content_mode": "arabic_only", "music": "x.mp3"},
    ],
)
def test_invalid_translation_requests_are_rejected(client, extra):
    assert client.post("/api/generate", json={**PAYLOAD, **extra}).status_code == 400
